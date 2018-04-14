import os
import mmap
import struct
import shutil
import argparse
import collections

"""
The AVIATION file system uses the following structure:

typedef struct ENTRY_ATTR {
  unsigned int      reserved : 15;
  unsigned int      is_folder : 1;
  unsigned int      reserved2 : 16;
};

typedef struct ENTRY {
  unsigned int      offset_into_str_list_for_name;
  unsigned int      pointer;
  unsigned int      length;
  ENTRY_ATTR        attr;
  unsigned int      reserved;
};

typedef struct DIRECTORY {
  unsigned int      num_entries;
  ENTRY             entries[num_entries];
  unsigned int      strings_len;
  char              strings[strings_len];
  char              padding;
};

The file system starts with a DIRECTORY.
"""

def memory_map(filename, access=mmap.ACCESS_READ):
    size = os.path.getsize(filename)
    fd = os.open(filename, os.O_RDONLY)
    return mmap.mmap(fd, size, access=access)

StructDef = collections.namedtuple('StructDef', 'name type')
BitFieldDef = collections.namedtuple('BitFieldDef', 'name from_bit to_bit')


class StructBuilder(object):
    """Helper class to read a buffer as a struct, and convert its fields to an object's attributes."""

    def __init__(self, base_offset):
        """Initiate a StructBuilder, which will start reading from base_offset"""
        self.offset = base_offset

    def build_struct(self, object, definition):
        """Build a struct based on the definition supplied, apply fields to object"""
        for member in definition:
            size = struct.calcsize(member.type)
            val, = struct.unpack_from(member.type, Aviation.bin, self.offset)
            setattr(object, member.name, val)
            self.offset += size

    def build_bit_field(self, object, definition, full_value):
        """Build a bit field based on the definition and 32bit value supplied, apply fields to object"""
        for member in definition:
            val = (full_value & ( ( 2 << member.to_bit ) - 1 ) ) >> member.from_bit
            setattr(object, member.name, val)


class Entry(object):
    """Represents an Entry (points to a file or directory) in the AVIATION file system"""

    DEF_ENTRY_HEADER = [
            StructDef("offset_into_str_list",     "I"),
            StructDef("pointer",                  "I"),
            StructDef("size",                     "I"),
            StructDef("attributes",               "I"),
            StructDef("reserved1",                "I")
        ]
    DEF_ATTR = [
            BitFieldDef("reserved2",    0, 14),
            BitFieldDef("is_dir",       15, 15),
            BitFieldDef("reserved3",    16, 31)
        ]

    def __init__(self, parent, depth):
        self.parent = parent
        self.depth = depth
        self.parent.sb.build_struct(self, self.DEF_ENTRY_HEADER)
        self.parent.sb.build_bit_field(self, self.DEF_ATTR, self.attributes)

    def __repr__(self):
        return "Entry('{}')".format(self.name)

    def __str__(self):
        return self.depth * " " + "{} ({})\n".format(self.name, self.size)

    def __getitem__(self, arg):
        return getattr(self, arg)

class File(object):
    """Represents a file in the AVIATION file system"""

    def __init__(self, self_entry, depth, parent_path):
        self.base_offset = self_entry["pointer"]
        self.size = self_entry["size"]
        self.name = self_entry["name"]
        self.depth = depth
        self.path = parent_path + os.path.sep + self.name

    def __repr__(self):
        return "File('{}')".format(self.name)

    def __str__(self):
        return "{} ({})".format(self.name, self.size)

    def walk(self):
        print self.depth * " " + "-" + str(self)
        if Aviation.Config['write_to_disk']:
            with open(self.path, "wb") as file:
                file.write(Aviation.bin[self.base_offset:self.base_offset + self.size])


class Directory(object):
    """Represents a Directory in the AVIATION file system"""

    # A set of pointers to invalid entries found in the AVIATION file system.
    # Possibly a bug in the file system creation.
    invalid_entries = set([0x254574ac])

    # Since many double-entries exist in the file system, this dictionary will
    # map pointers to directories to locate doubles.
    dir_map = {}

    def __init__(self, self_entry, depth, parent_path):
        base_offset = self_entry["pointer"]
        self.name = self_entry["name"]
        self.depth = depth
        self.path = parent_path + os.path.sep + self.name

        if base_offset in Directory.dir_map:
            if not Aviation.Config['avoid_double_entries']:
                print "{} same as {} (0x{:X})".format(self.name, Directory.dir_map[base_offset], base_offset)
                self.copy_attr(Directory.dir_map[base_offset])
                return
            else:
                assert(False) #If we are avoiding double entries, we should never find an entry already visited
        else:
            Directory.dir_map[base_offset] = self
        
        self.sb = StructBuilder(base_offset)
        self.sb.build_struct(self, [StructDef("num_entries", "I")])
        entries = [Entry(self, self.depth + 1) for i in xrange(self.num_entries)]
        self.sb.build_struct(self, [StructDef("name_list_length", "I")])
        self.sb.build_struct(self, [StructDef("name_list", "{}s".format(self.name_list_length))])

        self.directories = []
        self.files = []

        #Iterate all entries and create new files/directories
        for entry in entries:
            entry.name = self.name_list[entry.offset_into_str_list:self.name_list.index('\0', entry.offset_into_str_list)]
            if self.__should_visit_entry(entry):
                if entry.is_dir:
                    directory = Directory(entry, self.depth + 1, self.path)
                    self.directories.append(directory)
                else:
                    file = File(entry, self.depth + 1, self.path)
                    self.files.append(file)

    def __should_visit_entry(self, entry):
        if entry.pointer in Directory.invalid_entries:
            print "Skipping {} (0x{:x}) - Invalid".format(entry.name, entry.pointer)
            return False
        if Aviation.Config['avoid_double_entries']:
            if entry.pointer in Directory.dir_map:
                print "Skipping {}{} (0x{:x})\n same as {}".format(self.path + os.path.sep, 
                                                                    entry.name, 
                                                                    entry.pointer, 
                                                                    Directory.dir_map[entry.pointer].path)
                return False
        if Aviation.Config['depth_limit'] != 0 and entry.depth > Aviation.Config['depth_limit']:
            return False
        return True

    def __repr__(self):
        return "Directory('{}')".format(self.name)

    def __str__(self):
        return "{} ({})".format(self.name, self.path)

    def walk(self):
        print self.depth * " " + "+" + str(self)
        if Aviation.Config['write_to_disk']:
            os.makedirs(self.path)
        for dir in self.directories:
            dir.walk()
        for file in self.files:
            file.walk()

    def copy_attr(self, other):
        self.directories = other.directories
        self.files = other.files

class AviationException(Exception):
    pass

class Aviation(object):

    Config = {
        # The AVIATION FS includes many double pointers which essentially point to the same location.
        # Skip them during parsing to avoid the overhead.
        'avoid_double_entries': True,

        # Write the files to the disk or just print them?
        'write_to_disk': False,

        # Limit depth for debug? 0 is unlimited.
        'depth_limit': 0
    }

    def __init__(self, path_to_bin_file, working_dir):
        if not os.path.exists(path_to_bin_file):
            raise AviationException("File does not exist: {}".format(path_to_bin_file))

        if not os.path.exists(working_dir) or not os.path.isdir(working_dir):
            raise AviationException("Illegal dir path: {}".format(working_dir))

        try:
            Aviation.bin = memory_map(path_to_bin_file)
            root_dir = "root"
            if Aviation.Config['write_to_disk']:
                os.chdir(working_dir)
                if os.path.exists(root_dir):
                    shutil.rmtree(root_dir)

            self.root = Directory({"pointer": 0, "name": root_dir}, 0, working_dir)

        except struct.error:
            raise AviationException("Unexpected Error Reading AVIATION File Format")
        except e:
            raise AviationException("Unexpected Error: {}".format(e))

    @classmethod
    def SetConfig(cls, **kwargs):
        for key, value in kwargs.iteritems():
            if key in cls.Config:
                if type(cls.Config[key]) == type(value):
                    cls.Config[key] = value
                else:
                    raise AviationException("Invalid configuration: '{}'='{}', expecting {}".format(key, value, type(cls.Config[key])))
            else:
                raise AviationException("Unknown configuration: '{}'".format(key))

    def Walk(self):
        self.root.walk()


if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description='Parse AVIATION File System')
    parser.add_argument('-i', '--input', help='File system to parse (path to the AVIATION file)', required=True)
    parser.add_argument('-w', '--working_dir', help='Folder to output extracted files to')
    parser.add_argument('-e', '--extract_files', help='Extract files to working directory instead of just printing paths', action='store_true')
    args = parser.parse_args()

    if args.working_dir == None:
        args.working_dir = os.getcwd()

    try:
        a = Aviation(args.input, args.working_dir)
        if args.extract_files:
            a.SetConfig(write_to_disk = True)
        a.Walk()
    except AviationException as e:
        print e