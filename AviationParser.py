import os
import mmap
import struct
import shutil
import argparse
import collections

def memory_map(filename, access=mmap.ACCESS_READ):
    size = os.path.getsize(filename)
    fd = os.open(filename, os.O_RDONLY)
    return mmap.mmap(fd, size, access=access)

StructDef = collections.namedtuple('StructDef', 'name type')
BitFieldDef = collections.namedtuple('BitFieldDef', 'name from_bit to_bit')

class Config:
    # The AVIATION FS includes many double pointers which essentially point to the same location.
    # Skip them during parsing to avoid the overhead.
    avoid_double_entries = True

    # Write the files to the disk or just print them?
    write_to_disk = False

    # Limit depth for debug? 0 is unlimited.
    depth_limit = 0

class StructBuilder(object):
    def __init__(self, base_offset):
        self.offset = base_offset

    def build_struct(self, object, definition):
        for member in definition:
            size = struct.calcsize(member.type)
            val, = struct.unpack_from(member.type, Aviation.bin, self.offset)
            setattr(object, member.name, val)
            self.offset += size

    def build_bit_field(self, object, definition, full_value):
        for member in definition:
            val = (full_value & ( ( 2 << member.to_bit ) - 1 ) ) >> member.from_bit
            setattr(object, member.name, val)
        



class File(object):
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
        if Config.write_to_disk:
            with open(self.path, "wb") as file:
                file.write(Aviation.bin[self.base_offset:self.base_offset + self.size])

class Entry(object):
    __slots__ = ['offset_into_str_list', 'pointer', 'size', 
                 'attributes', 'reserved1', 'reserved2', 'is_dir', 'reserved3',
                 'parent', 'name', 'depth']

    DEF_ENTRY_HEADER = [
            StructDef("offset_into_str_list",     "I"),
            StructDef("pointer",                  "I"),
            StructDef("size",                     "I"),
            StructDef("attributes",               "I"),
            StructDef("reserved1",                "I")
        ]
    DEF_ATTR = [
            BitFieldDef("reserved2", 0, 14),
            BitFieldDef("is_dir", 15, 15),
            BitFieldDef("reserved3", 16, 31)
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

class Directory(object):

    invalid_entries = set([0x254574ac])
    dir_map = {}

    def __init__(self, self_entry, depth, parent_path):
        base_offset = self_entry["pointer"]
        self.name = self_entry["name"]
        self.depth = depth
        self.path = parent_path + os.path.sep + self.name

        if base_offset in Directory.dir_map:
            if not Config.avoid_double_entries:
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
        if Config.avoid_double_entries:
            if entry.pointer in Directory.dir_map:
                print "Skipping {}{} (0x{:x})\n same as {}".format(self.path + os.path.sep, 
                                                                    entry.name, 
                                                                    entry.pointer, 
                                                                    Directory.dir_map[entry.pointer].path)
                return False
        if Config.depth_limit != 0 and entry.depth > Config.depth_limit:
            return False
        return True

    def __repr__(self):
        return "Directory('{}')".format(self.name)

    def __str__(self):
        return "{} ({})".format(self.name, self.path)

    def walk(self):
        print self.depth * " " + "+" + str(self)
        if Config.write_to_disk:
            os.makedirs(self.path)
        for dir in self.directories:
            dir.walk()
        for file in self.files:
            file.walk()

    def copy_attr(self, other):
        self.directories = other.directories
        self.files = other.files


class Aviation(object):
    def __init__(self, path_to_bin_file, working_dir):
        Aviation.bin = memory_map(path_to_bin_file)
        root_dir = "root"
        if Config.write_to_disk:
            os.chdir(working_dir)
            if os.path.exists(root_dir):
                shutil.rmtree(root_dir)
        root = Directory({"pointer": 0, "name": root_dir}, 0, working_dir)
        root.walk()


if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description='Parse AVIATION File System')
    parser.add_argument('-i', '--input', help='File system to parse (path to the AVIATION file)', required=True)
    parser.add_argument('-w', '--working_dir', help='Folder to output extracted files')
    args = parser.parse_args()

    if args.working_dir == None:
        args.working_dir = os.getcwd()

    a = Aviation(args.input, args.working_dir)