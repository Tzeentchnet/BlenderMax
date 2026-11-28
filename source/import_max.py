# SPDX-FileCopyrightText: 2023-2026 Sebastian Schrand
#                         2017-2022 Jens M. Plonka
#                         2005-2018 Philippe Lagadec
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Import is based on using information from `olefile` IO source-code
# and the FreeCAD Autodesk 3DS Max importer ImportMAX.
#
# `olefile` (formerly OleFileIO_PL) is copyright Philippe Lagadec.
# (https://www.decalage.info)
#
# ImportMAX is copyright Jens M. Plonka.
# (https://www.github.com/jmplonka/Importer3D)

import io
import os
import re
import sys
import bpy
import math
import zlib
import array
import struct
import traceback
import mathutils
import numpy as np
from pathlib import Path, PureWindowsPath
from bpy_extras.node_shader_utils import PrincipledBSDFWrapper

from .max_animation import (
    TICKS_PER_SECOND,
    ActionBuilder,
    extract_transform_tracks,
    max_quaternion,
    max_scale_matrix,
    sample_transform,
)
from .max_materials import TextureResolver, clear_texture_cache
from .max_uvwmap import (
    PB_MAPTYPE,
    SUPPORTED_MAP_TYPES,
    UVWMapSettings,
    map_corners,
    object_to_gizmo,
)


###################
# DATA STRUCTURES #
###################

MAGIC = b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1'

MIN_FILE_SIZE = 1536
UNKNOWN_SIZE = 0x7FFFFFFF
MAXFILE_SIZE = 0x7FFFFFFFFFFFFFFF
MAXREGSECT = 0xFFFFFFFA  # (-6) maximum SECT
DIFSECT = 0xFFFFFFFC  # (-4) denotes a DIFAT sector in a FAT
FATSECT = 0xFFFFFFFD  # (-3) denotes a FAT sector in a FAT
ENDOFCHAIN = 0xFFFFFFFE  # (-2) end of a virtual stream chain
FREESECT = 0xFFFFFFFF  # (-1) unallocated sector
MAX_STREAM = 2  # element is a stream object
ROOT_STORE = 5  # element is a root storage

TYP_REFS = {0x9, 0x1040, 0x2034, 0x2035}
TYP_SHORT = {0x103, 0x105, 0x99C, 0x2500, 0x3005}
TYP_LINK = {0x1020, 0x1030, 0x1050, 0x1080, 0x3002, 0x4003}
TYP_NAME = {0x340, 0x456, 0x962, 0x10A0, 0x1010, 0x1230, 0x4001}
TYP_ARRAY = {0x102, 0x96A, 0x96B, 0x96C, 0x9ED, 0x2501, 0x2503, 0x2504, 0x2505, 0x2511}
UNPACK_BOX_DATA = struct.Struct('<HIHHBff').unpack_from  # Index, int, 2short, byte, 2float
INVALID_NAME = re.compile('^[0-9].*')

FLOAT_POINT = 0x71F11549498702E7  # Float Wire
MATRIX_POS = 0xFFEE238A118F7E02  # Position XYZ
MATRIX_ROT = 0x3A90416731381913  # Rotation Wire
MATRIX_SCL = 0xFEEE238B118F7C01  # Scale XYZ
BIPED_OBJ = 0x0000000000009125  # Biped Object
BIPED_ANIM = 0x78C6B2A6B147369  # Biped SubAnim
EDIT_MESH = 0x00000000E44F10B3  # Editable Mesh
EDIT_POLY = 0x192F60981BF8338D  # Editable Poly
POLY_MESH = 0x000000005D21369A  # PolyMeshObject
CAMERA = 0x0000000000001002  # Camera
OMNI_LIGHT = 0x0000000000001011  # Omni
DIRECT_LIGHT = 0x0000000000001013  # Free Direct
CAMERA_TARGET = 0x0000000000001020  # Camera Target
LAYER_MTL = 0x8425554E65486584  # CoronaLayeredMtl
COPHY_MTL = 0x871517206912AB89  # CoronaPhysicalMtl
SURF_MTL = 0x62F74B4C7E73161F  # Standard Surface
SKIN_MTL = 0x44CFDE95bEC41C73  # CoronaSkinMtl
HAIR_MTL = 0xCA41F54622C05DD6  # CoronaHairMtl
SKIN_MODIFIER = 0x000156660095C6A3
PHYSIQUE_MODIFIER = 0x0000000000000100
EDIT_NORMALS_MODIFIER = 0x35CA1CDE4AA52AE3
UVW_MAP_MODIFIER = 0x00000000000F72B1
EDIT_MESH_MODIFIER = 0x0000000000000050
# Edit Mesh mesh-delta chunks this decoder does not understand (vertex and
# face creation, map-channel edits). Their presence disables evaluation.
EDIT_MESH_UNSUPPORTED = {0x2770, 0x2771, 0x2772, 0x2775, 0x2776, 0x2781}
UNWRAP_MODIFIERS = {
    0x72BA4E1F02DF2E3A: 'Unwrap UVW',
    0x72BA4F2902DF2E3A: 'UnwrapUVW+',
}
RAYT_MTL = 0x329B106E27190FF4  # RaytraceMtl
PHYS_MTL = 0xDEADC0013D6B1CEC  # PhysicalMtl
CORO_MTL = 0x448931DD70BE6506  # CoronaMtl
ARCH_MTL = 0x4A16365470B05735  # ArchMtl
VRAY_MTL = 0x7034695C37BF3F2F  # VRayMtl
VTWO_MTL = 0x11731B4B6066686A  # VRay2SidedMtl
SHEL_MTL = 0x0000000046EE536A  # Shellac
BOOLEAN = 0x1BBAC5F7BC209AF  # Boolean
DUMMY = 0x0000000000876234  # Dummy
PLANE = 0x77566F65081F1DFC  # Plane
CONE = 0x00000000A86C23DD  # Cone

SKIPPABLE = {
    0x0000000000001040: 'Line',
    0x0000000000001065: 'Rectangle',
    0x0000000000001097: 'Ellipse',
    0x0000000000001999: 'Circle',
    0x0000000000002013: 'Point',
    0x05622B0D69011E82: 'Compass',
    0x12A822FB76A11646: 'CV Surface',
    0x1EB3430074F93B07: 'Particle View',
    0x2ECCA84028BF6E8D: 'Bone',
    0x3BDB0E0C628140F6: 'VRayPlane',
    0x4E9B599047DB14EF: 'Slider',
    0x522E47057BF61478: 'Sky',
    0x5FD602DF3C5575A1: 'VRayLight',
}

CONFIG = []
CLS_DATA = []
DLL_DIR_LIST = []
CLS_DIR3_LIST = []
VID_PST_QUE = []
SCENE_LIST = []

object_list = []
face_sources = {}  # Blender mesh pointer -> (MAX face count, kept MAX face indices)
parent_dict = {}
matrix_dict = {}
target_dict = {}


def get_valid_name(name):
    if (INVALID_NAME.match(name)):
        return "_%s" % (name.encode('utf8'))
    return "%s" % (name.encode('utf8'))


def i8(data):
    return data if data.__class__ is int else data[0]


def i16(data, offset=0):
    return struct.unpack("<H", data[offset:offset + 2])[0]


def i32(data, offset=0):
    return struct.unpack("<I", data[offset:offset + 4])[0]


def get_byte(data, offset=0):
    size = offset + 1
    value = struct.unpack('<B', data[offset:size])[0]
    return value, size


def get_short(data, offset=0):
    size = offset + 2
    value = struct.unpack('<H', data[offset:size])[0]
    return value, size


def get_long(data, offset=0):
    size = offset + 4
    value = struct.unpack('<I', data[offset:size])[0]
    return value, size


def get_float(data, offset=0):
    size = offset + 4
    value = struct.unpack('<f', data[offset:size])[0]
    return value, size


def get_bytes(data, offset=0, count=1):
    size = offset + count
    values = struct.unpack('<' + 'B' * count, data[offset:size])
    return values, size


def get_shorts(data, offset=0, count=1):
    size = offset + count * 2
    values = struct.unpack('<' + 'H' * count, data[offset:size])
    return values, size


def get_longs(data, offset=0, count=1):
    size = offset + count * 4
    values = struct.unpack('<' + 'I' * count, data[offset:size])
    return values, size


def get_floats(data, offset=0, count=1):
    size = offset + count * 4
    values = struct.unpack('<' + 'f' * count, data[offset:size])
    return values, size


def _clsid(clsid):
    """Converts a CLSID to a readable string."""
    assert len(clsid) == 16
    if not clsid.strip(b"\0"):
        return ""
    return (("%08X-%04X-%04X-%02X%02X-" + "%02X" * 6) %
            ((i32(clsid, 0), i16(clsid, 4), i16(clsid, 6)) +
            tuple(map(i8, clsid[8:16]))))


###############
# DATA IMPORT #
###############

def is_maxfile(filename):
    """Test if file is a MAX OLE2 container."""
    if hasattr(filename, 'read'):
        header = filename.read(len(MAGIC))
        filename.seek(0)
    elif isinstance(filename, bytes) and len(filename) >= MIN_FILE_SIZE:
        header = filename[:len(MAGIC)]
    else:
        with open(filename, 'rb') as fp:
            header = fp.read(len(MAGIC))
    if header == MAGIC:
        return True
    else:
        return False


class MaxStream(io.BytesIO):
    """Returns an instance of the BytesIO class as read-only file object."""
    def __init__(self, fp, sect, size, offset, sectorsize, fat, filesize):
        if size == UNKNOWN_SIZE:
            size = len(fat) * sectorsize
        nb_sectors = (size + (sectorsize - 1)) // sectorsize

        data = []
        for i in range(nb_sectors):
            try:
                fp.seek(offset + sectorsize * sect)
            except:
                break
            sector_data = fp.read(sectorsize)
            data.append(sector_data)
            try:
                sect = fat[sect] & FREESECT
            except IndexError:
                break
        data = b"".join(data)
        if len(data) >= size:
            data = data[:size]
            self.size = size
        else:
            self.size = len(data)
        io.BytesIO.__init__(self, data)


class MaxFileDirEntry:
    """Directory Entry for a stream or storage."""
    STRUCT_DIRENTRY = '<64sHBBIII16sIQQIII'
    DIRENTRY_SIZE = 128
    assert struct.calcsize(STRUCT_DIRENTRY) == DIRENTRY_SIZE

    def __init__(self, entry, sid, maxfile):
        self.sid = sid
        self.maxfile = maxfile
        self.kids = []
        self.kids_dict = {}
        self.used = False
        (
            self.name_raw,
            self.namelength,
            self.entry_type,
            self.color,
            self.sid_left,
            self.sid_right,
            self.sid_child,
            clsid,
            self.dwUserFlags,
            self.createTime,
            self.modifyTime,
            self.isectStart,
            self.sizeLow,
            self.sizeHigh
        ) = struct.unpack(MaxFileDirEntry.STRUCT_DIRENTRY, entry)

        if self.namelength > 64:
            self.namelength = 64
        self.name_utf16 = self.name_raw[:(self.namelength - 2)]
        self.name = maxfile._decode_utf16_str(self.name_utf16)
        # print('DirEntry SID=%d: %s' % (self.sid, repr(self.name)))
        if maxfile.sectorsize == 512:
            self.size = self.sizeLow
        else:
            self.size = self.sizeLow + (int(self.sizeHigh) << 32)
        self.clsid = _clsid(clsid)
        self.is_minifat = False
        if self.entry_type in (ROOT_STORE, MAX_STREAM) and self.size > 0:
            if self.size < maxfile.minisectorcutoff \
                    and self.entry_type == MAX_STREAM:  # only streams can be in MiniFAT
                self.is_minifat = True
            else:
                self.is_minifat = False
            maxfile._check_duplicate_stream(self.isectStart, self.is_minifat)
        self.sect_chain = None

    def build_sect_chain(self, maxfile):
        if self.sect_chain:
            return
        if self.entry_type not in (ROOT_STORE, MAX_STREAM) or self.size == 0:
            return
        self.sect_chain = list()
        if self.is_minifat and not maxfile.minifat:
            maxfile.loadminifat()
        next_sect = self.isectStart
        while next_sect != ENDOFCHAIN:
            self.sect_chain.append(next_sect)
            if self.is_minifat:
                next_sect = maxfile.minifat[next_sect]
            else:
                next_sect = maxfile.fat[next_sect]

    def build_storage_tree(self):
        if self.sid_child != FREESECT:
            self.append_kids(self.sid_child)
            self.kids.sort()

    def append_kids(self, child_sid):
        if child_sid == FREESECT:
            return
        elif child_sid >= len(self.maxfile.direntries):
            # Truncated directory stream: the sibling tree points past the
            # last stored entry. Keep the entries that are readable.
            print("\tOLEWarning: directory entry %d out of range (%d entries)"
                  % (child_sid, len(self.maxfile.direntries)))
            return
        else:
            child = self.maxfile._load_direntry(child_sid)
            if child.used:
                return
            child.used = True
            self.append_kids(child.sid_left)
            name_lower = child.name.lower()
            self.kids.append(child)
            self.kids_dict[name_lower] = child
            self.append_kids(child.sid_right)
            child.build_storage_tree()

    def __eq__(self, other):
        return self.name == other.name

    def __lt__(self, other):
        return self.name < other.name

    def __ne__(self, other):
        return not self.__eq__(other)

    def __le__(self, other):
        return self.__eq__(other) or self.__lt__(other)


class ImportMaxFile:
    """Representing an interface for importing .max files."""
    def __init__(self, filename=None):
        self._filesize = None
        self.byte_order = None
        self.directory_fp = None
        self.direntries = None
        self.dll_version = None
        self.fat = None
        self.first_difat_sector = None
        self.first_dir_sector = None
        self.first_mini_fat_sector = None
        self.fp = None
        self.header_clsid = None
        self.header_signature = None
        self.mini_sector_shift = None
        self.mini_sector_size = None
        self.mini_stream_cutoff_size = None
        self.minifat = None
        self.minifatsect = None
        self.minisectorcutoff = None
        self.minisectorsize = None
        self.ministream = None
        self.minor_version = None
        self.nb_sect = None
        self.num_difat_sectors = None
        self.num_dir_sectors = None
        self.num_fat_sectors = None
        self.num_mini_fat_sectors = None
        self.reserved1 = None
        self.reserved2 = None
        self.root = None
        self.sector_shift = None
        self.sector_size = None
        self.transaction_signature_number = None
        if filename:
            self.open(filename)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _decode_utf16_str(self, utf16_str, errors='replace'):
        unicode_str = utf16_str.decode('UTF-16LE', errors)
        return unicode_str

    def open(self, filename):
        if hasattr(filename, 'read'):
            self.fp = filename
        elif isinstance(filename, bytes) and len(filename) >= MIN_FILE_SIZE:
            self.fp = io.BytesIO(filename)
        else:
            self.fp = open(filename, 'rb')
        filesize = 0
        self.fp.seek(0, os.SEEK_END)
        try:
            filesize = self.fp.tell()
        finally:
            self.fp.seek(0)
        self._filesize = filesize
        self._used_streams_fat = []
        self._used_streams_minifat = []
        header = self.fp.read(512)
        fmt_header = '<8s16sHHHHHHLLLLLLLLLL'
        header_size = struct.calcsize(fmt_header)
        header1 = header[:header_size]
        (
            self.header_signature,
            self.header_clsid,
            self.minor_version,
            self.dll_version,
            self.byte_order,
            self.sector_shift,
            self.mini_sector_shift,
            self.reserved1,
            self.reserved2,
            self.num_dir_sectors,
            self.num_fat_sectors,
            self.first_dir_sector,
            self.transaction_signature_number,
            self.mini_stream_cutoff_size,
            self.first_mini_fat_sector,
            self.num_mini_fat_sectors,
            self.first_difat_sector,
            self.num_difat_sectors
        ) = struct.unpack(fmt_header, header1)

        self.sector_size = 2**self.sector_shift
        self.mini_sector_size = 2**self.mini_sector_shift
        if self.mini_stream_cutoff_size != 0x1000:
            self.mini_stream_cutoff_size = 0x1000
        self.nb_sect = ((filesize + self.sector_size - 1) // self.sector_size) - 1

        # file clsid
        self.header_clsid = _clsid(header[8:24])
        self.sectorsize = self.sector_size  # i16(header, 30)
        self.minisectorsize = self.mini_sector_size   # i16(header, 32)
        self.minisectorcutoff = self.mini_stream_cutoff_size  # i32(header, 56)
        self._check_duplicate_stream(self.first_dir_sector)
        if self.num_mini_fat_sectors:
            self._check_duplicate_stream(self.first_mini_fat_sector)
        if self.num_difat_sectors:
            self._check_duplicate_stream(self.first_difat_sector)

        # Load file allocation tables
        self.loadfat(header)
        self.loaddirectory(self.first_dir_sector)
        self.minifatsect = self.first_mini_fat_sector

    def close(self):
        self.fp.close()

    def _check_duplicate_stream(self, first_sect, minifat=False):
        if minifat:
            used_streams = self._used_streams_minifat
        else:
            if first_sect in (DIFSECT, FATSECT, ENDOFCHAIN, FREESECT):
                return
            used_streams = self._used_streams_fat
        if first_sect in used_streams:
            pass
        else:
            used_streams.append(first_sect)

    def sector_array(self, sect):
        ary = array.array('I', sect)
        if sys.byteorder == 'big':
            ary.byteswap()
        return ary

    def loadfat_sect(self, sect):
        if isinstance(sect, array.array):
            fat1 = sect
        else:
            fat1 = self.sector_array(sect)
        isect = None
        for isect in fat1:
            isect = isect & FREESECT
            if isect == ENDOFCHAIN or isect == FREESECT:
                break
            sector = self.getsect(isect)
            nextfat = self.sector_array(sector)
            self.fat = self.fat + nextfat
        return isect

    def loadfat(self, header):
        sect = header[76:512]
        self.fat = array.array('I')
        self.loadfat_sect(sect)
        if self.num_difat_sectors != 0:
            nb_difat_sectors = (self.sectorsize // 4) - 1
            nb_difat = (self.num_fat_sectors - 109 + nb_difat_sectors - 1) // nb_difat_sectors
            isect_difat = self.first_difat_sector
            for i in range(nb_difat):
                sector_difat = self.getsect(isect_difat)
                difat = self.sector_array(sector_difat)
                self.loadfat_sect(difat[:nb_difat_sectors])
                isect_difat = difat[nb_difat_sectors]
        if len(self.fat) > self.nb_sect:
            self.fat = self.fat[:self.nb_sect]

    def loadminifat(self):
        stream_size = self.num_mini_fat_sectors * self.sector_size
        nb_minisectors = (self.root.size + self.mini_sector_size - 1) // self.mini_sector_size
        used_size = nb_minisectors * 4
        sect = self._open(self.minifatsect, stream_size, force_FAT=True).read()
        self.minifat = self.sector_array(sect)
        self.minifat = self.minifat[:nb_minisectors]

    def getsect(self, sect):
        try:
            self.fp.seek(self.sectorsize * (sect + 1))
        except:
            print('IndexError: Sector index out of range')
        sector = self.fp.read(self.sectorsize)
        return sector

    def loaddirectory(self, sect):
        self.directory_fp = self._open(sect, force_FAT=True)
        max_entries = self.directory_fp.size // 128
        self.direntries = [None] * max_entries
        root_entry = self._load_direntry(0)
        self.root = self.direntries[0]
        self.root.build_storage_tree()

    def _load_direntry(self, sid):
        if self.direntries[sid] is not None:
            return self.direntries[sid]
        self.directory_fp.seek(sid * 128)
        entry = self.directory_fp.read(128)
        self.direntries[sid] = MaxFileDirEntry(entry, sid, self)
        return self.direntries[sid]

    def _open(self, start, size=UNKNOWN_SIZE, force_FAT=False):
        if size < self.minisectorcutoff and not force_FAT:
            if not self.ministream:
                self.loadminifat()
                size_ministream = self.root.size
                self.ministream = self._open(self.root.isectStart,
                                             size_ministream, force_FAT=True)
            return MaxStream(fp=self.ministream, sect=start, size=size,
                             offset=0, sectorsize=self.minisectorsize,
                             fat=self.minifat, filesize=self.ministream.size)
        else:
            return MaxStream(fp=self.fp, sect=start, size=size,
                             offset=self.sectorsize, sectorsize=self.sectorsize,
                             fat=self.fat, filesize=self._filesize)

    def _find(self, filename):
        if isinstance(filename, str):
            filename = filename.split('/')
        node = self.root
        for name in filename:
            node = next(
                (kid for kid in node.kids if kid.name.casefold() == name.casefold()),
                None,
            )
            if node is None:
                raise FileNotFoundError(
                    "MAX stream not found: %s" % "/".join(filename)
                )
        return node.sid

    def openstream(self, filename):
        sid = self._find(filename)
        entry = self.direntries[sid]
        return self._open(entry.isectStart, entry.size)


###################
# DATA PROCESSING #
###################

class MaxChunk(object):
    """Representing a chunk of a .max file."""
    __slots__ = "superid", "types", "level", "number", "size", "data"

    def __init__(self, superid, types, level, number, size, data=None):
        self.superid = superid
        self.types = types
        self.data = data
        self.number = 0
        self.level = 0
        self.size = 0
        
    def __str__(self):
        return "%s[%4x]%04X:%s" % ("" * self.level, self.number, self.types, self.data)


class ByteArrayChunk(MaxChunk):
    """A byte array of a .max chunk."""
    def __init__(self, superid, types, level, number, size, data):
        MaxChunk.__init__(self, superid, types, level, number, size, data)
        self.superid = superid
        self.children = []

    def get_first(self, types):
        return None

    def set(self, data, fmt, start, end):
        try:
            self.data = struct.unpack(fmt, data[start:end])
        except Exception as exc:
            self.data = data
            # print('\tStructError: %s' % exc)

    def set_string(self, data):
        try:
            self.data = data.decode('UTF-16LE')
        except:
            self.data = data.decode('UTF-8', 'replace')
        finally:
            self.data = data.decode('UTF-16LE', 'ignore')

    def set_meta_data(self, data):
        metadict = {}
        try:
            mdatazip = list(zip(*[iter(data.split(b'\x00\x00\x00'))]*2))
            metadata = list(filter(lambda tpl: b'' not in tpl, mdatazip))
            for mdata in metadata:
                header = mdata[0]
                imgkey = header[-1]
                mtitle = b''.join(mdata[1].split(b'\x00')).decode('UTF-8', 'ignore')
                if (len(header) > ROOT_STORE):
                    size = len(header[:-ROOT_STORE])
                    head = struct.unpack('<' + 'IH' * int(size / 6), header[:size])
                    meta = get_longs(header, size, len(header[size:]) // 4)[0]
                    print("  metadata: %s '%s'...%s" % (hex(head[-1]), imgkey, mtitle))
                    metakey = meta[0]
                    metadict[metakey] = [(head[0], head[1])]
                elif metadict and metakey:
                    metadict[metakey].insert(-imgkey, (imgkey, mtitle))
                    print("  imgpath: %s -> '%s: %s'" % (hex(metakey), imgkey, mtitle))
        except:
            self.data = data
            # print('\tStructError: %s' % exc)
        finally:
            self.data = metadict

    def set_data(self, data):
        if (self.types in TYP_NAME):
            self.set_string(data)
        elif (self.types in TYP_LINK):
            self.set(data, '<I', 0, len(data))
        elif (self.types in TYP_REFS):
            self.set(data, '<' + 'I' * int(len(data) / 4), 0, len(data))
        elif (self.types in TYP_SHORT):
            self.set(data, '<' + 'H' * int(len(data) / 2), 0, len(data))
        elif (self.types in TYP_ARRAY):
            self.set(data, '<' + 'f' * int(len(data) / 4), 0, len(data))
        elif (self.types == 0x2510):
            self.set(data, '<' + 'f' * int(len(data) / 4 - 1) + 'I', 0, len(data))
        else:
            self.data = data


class ClassIDChunk(ByteArrayChunk):
    """The class ID subchunk of a .max chunk."""
    def __init__(self, superid, types, level, number, size, data):
        MaxChunk.__init__(self, superid, types, level, number, size, data)
        self.superid = 0x5
        self.dll = None

    def set_data(self, data):
        if (self.types == 0x2042):
            self.set_string(data)  # ClsName
        elif (self.types == 0x2060):
            self.set(data, '<IQI', 0, 16)  # DllIndex, ID, SuperID
        else:
            self.data = ":".join("%02x" % (c) for c in data)


class DirectoryChunk(ByteArrayChunk):
    """The directory chunk of a .max file."""
    def __init__(self, superid, types, level, number, size, data):
        MaxChunk.__init__(self, superid, types, level, number, size, data)
        self.superid = 0x4

    def set_data(self, data):
        if (self.types in (0x2037, 0x2039)):
            self.set_string(data)


class ContainerChunk(MaxChunk):
    """A container chunk in a .max file wich includes byte arrays."""
    def __init__(self, superid, types, level, number, size, data, primReader=ByteArrayChunk):
        MaxChunk.__init__(self, superid, types, level, number, size, data)
        self.primReader = primReader
        self.superid = superid

    def __str__(self):
        return "%s[%4x]%04X" % ("" * self.level, self.number, self.types)

    def get_first(self, types):
        for child in self.children:
            if (child.types == types):
                return child
        return None

    def set_data(self, data):
        reader = ChunkReader()
        self.children = reader.get_chunks(self.superid, data, self.level + 1, ContainerChunk, self.primReader)


class SceneChunk(ContainerChunk):
    """The scene chunk of a .max file wich includes the relevant data for blender."""
    def __init__(self, superid, types, data, number, level, size, primReader=ByteArrayChunk):
        MaxChunk.__init__(self, superid, types, data, number, level, size)
        self.primReader = primReader
        self.superid = 0x2

    def __str__(self):
        return "%s[%4x]%s" % ("" * self.level, self.number, get_cls_name(self))

    def set_data(self, data):
        # print('Scene', "%s %s" % (hex(self.types), self))
        reader = ChunkReader()
        self.children = reader.get_chunks(self.superid, data, self.level + 1, SceneChunk, ByteArrayChunk)


class ChunkReader(object):
    """The chunk reader class for decoding the byte arrays."""
    def __init__(self, name=None):
        self.name = name

    def get_chunks(self, superid, data, level, conReader, primReader):
        chunks = []
        offset = 0
        if (len(data) > ROOT_STORE and level == 0):
            root, step = get_short(data, 0)
            long, step = get_long(data, step)
            print("  reading '%s'..." % self.name, len(data))
            if (root == 0x8B1F):
                long, step = get_long(data, step)
                if (long in (0xB000000, 0xA040000, 0x8000001E)):
                    data = zlib.decompress(data, zlib.MAX_WBITS | 32)
            elif (superid in (0xA, 0xB)):
                chunk = primReader(superid, root, level, 1, len(data), data)
                chunk.set_meta_data(data)
                return [chunk]
        while offset < len(data):
            offset, chunk = self.get_next_chunk(superid, data, offset, level,
                                                len(chunks), conReader, primReader)
            chunks.append(chunk)
        return chunks

    def get_next_chunk(self, superid, data, offset, level, number, conReader, primReader):
        header = 6
        typ, siz = struct.unpack("<Hi", data[offset:offset + header])
        chunksize = siz & UNKNOWN_SIZE
        if (siz == 0):
            siz, = struct.unpack("<q", data[offset + header:offset + header + 8])
            header += 8
            chunksize = siz & MAXFILE_SIZE
        if (siz < 0):
            chunk = conReader(superid, typ, level, number, chunksize, data, primReader)
        else:
            chunk = primReader(superid, typ, level, number, chunksize, data)
        chunkdata = data[offset + header:offset + chunksize]
        chunk.set_data(chunkdata)
        return offset + chunksize, chunk


class Mesh3d(object):
    """Class representing a editable poly mesh object."""
    def __init__(self):
        self.verts = []
        self.faces = []
        self.polys = []
        self.cords = []
        self.uvids = []
        self.maps = []
        self.mats = []
        self.smoothing = []
        self.points = None
        self.tris = None
        self.face_source = None

    def __str__(self):
        coordsize = [len(crds) // 3 for crds in self.cords]
        return "[%d-%s][%d][%d-%s]" % (len(self.verts) // 3,
                                       '/'.join("%d" % c for c in coordsize),
                                       len(self.polys) // 2, len(self.faces),
                                       '/'.join("%d" % len(u) for u in self.uvids))

    def set(self, indices):
        for point in indices:
            ply = point.points
            key = point.group
            self.mats.append(key)
            self.faces.append(ply)
            self.smoothing.append(point.flag3)


class Point3d(object):
    """Class representing a three dimensional vector plus pointflag."""
    def __init__(self):
        self.points = None
        self.flags = 0
        self.group = 0
        self.flag1 = 0
        self.flag2 = 0
        self.flag3 = 0
        self.fbits = []

    def __str__(self):
        return "[%s]:'%X'%x,%x,%x[%s]" % ('/'.join("%d" % p for p in self.points),
                                         self.group, self.flag1, self.flag2, self.flag3,
                                         ','.join("%x" % f for f in self.fbits))


class Material(object):
    """Representing a material chunk of a scene chunk."""
    def __init__(self):
        self.data = {}

    def set(self, name, value):
        self.data[name] = value

    def get(self, name, default=None):
        value = None
        if (name in self.data):
            value = self.data[name]
        if (value is None):
            return default
        return value


def get_node(index):
    if isinstance(index, tuple):
        index = index[0]
    global SCENE_LIST
    if (index < len(SCENE_LIST[0].children)):
        return SCENE_LIST[0].children[index]
    return None


def get_node_parent(node):
    parent = None
    if (node):
        chunk = node.get_first(0x0960)
        if (chunk and len(chunk.data) > 3):
            idx, offset = get_long(chunk.data, 0)
            parent = get_node(idx)
    return parent


def get_node_name(node):
    if (node):
        name = node.get_first(0x0962)
        if (name):
            return name.data
    return node.__class__.__name__


def get_class(chunk):
    global CLS_DIR3_LIST
    if chunk and (chunk.types < len(CLS_DIR3_LIST)):
        return CLS_DIR3_LIST[chunk.types]
    return None


def get_dll(chunk):
    global DLL_DIR_LIST
    dll = chunk.get_first(0x2060)
    if (dll):
        idx = dll.data[0]
        if (idx < len(DLL_DIR_LIST)):
            return DLL_DIR_LIST[idx]
    return None


def get_metadata(index):
    global META_DATA
    pathdata = META_DATA[0].data if META_DATA else None
    if pathdata:
        pathname = pathdata.get(index)
        if pathname is None:
            for key, uid in pathdata.items():
                if index in uid[2]:
                    return uid[0][1]
        else:
            return pathname[0][1]
    return pathdata


def get_guid(chunk):
    clid = get_class(chunk)
    if (clid):
        guid = clid.get_first(0x2060)
        if guid is not None:
            return guid.data[1]
    return chunk.types if chunk else 0


def get_super_id(chunk):
    clid = get_class(chunk)
    if (clid):
        suid = clid.get_first(0x2060)
        if suid is not None:
            return suid.data[2]
    return 0x0


def get_cls_name(chunk):
    clid = get_class(chunk)
    if (clid and clid.get_first(0x2042)):
        cls_name = clid.get_first(0x2042).data
        try:
            return "'%s'" % (cls_name)
        except:
            return "'%r'" % (cls_name)
    return u"%04X" % (chunk.types)


def get_references(chunk):
    references = []
    if chunk is None:
        return references
    refs = chunk.get_first(0x2034)
    if (refs):
        references = [get_node(idx) for idx in refs.data]
    return references


def get_reference(chunk):
    references = {}
    refs = chunk.get_first(0x2035)
    if (refs):
        offset = 1
        while offset < len(refs.data):
            key = refs.data[offset]
            offset += 1
            idx = refs.data[offset]
            offset += 1
            references[key] = get_node(idx)
    return references


def read_chunks(maxfile, name, conReader=ContainerChunk, primReader=ByteArrayChunk, superId=None):
    with maxfile.openstream(name) as file:
        scene = file.read()
        reader = ChunkReader(name)
        return reader.get_chunks(superId, scene, 0, conReader, primReader)


def read_class_data(maxfile, filename):
    global CLS_DATA
    CLS_DATA = read_chunks(maxfile, 'ClassData', superId=6)


def read_class_directory(maxfile, filename):
    global CLS_DIR3_LIST
    for stream_name in ('ClassDirectory3', 'ClassDirectory2', 'ClassDirectory'):
        try:
            CLS_DIR3_LIST = read_chunks(
                maxfile,
                stream_name,
                ContainerChunk,
                ClassIDChunk,
            )
            break
        except FileNotFoundError:
            continue
    else:
        raise FileNotFoundError(
            "MAX class directory stream not found in %s" % filename
        )
    for clsdir in CLS_DIR3_LIST:
        clsdir.dll = get_dll(clsdir)


def read_config(maxfile, filename):
    global CONFIG
    CONFIG = read_chunks(maxfile, 'Config', superId=7)


def get_scene_timing():
    timing = {
        "fps": None,
        "start_tick": None,
        "end_tick": None,
        "current_tick": None,
    }
    root = next((chunk for chunk in CONFIG if chunk.types == 0x20B0), None)
    if root is None:
        return timing

    for name, chunk_type in (
        ("fps", 0x0010),
        ("start_tick", 0x0050),
        ("end_tick", 0x0060),
        ("current_tick", 0x0070),
    ):
        chunk = root.get_first(chunk_type)
        if chunk is not None and isinstance(chunk.data, bytes) and len(chunk.data) >= 4:
            timing[name] = struct.unpack_from("<i", chunk.data)[0]

    if timing["fps"] is not None and not 1 <= timing["fps"] <= 1000:
        timing["fps"] = None
    if (
        timing["start_tick"] is not None
        and timing["end_tick"] is not None
        and timing["start_tick"] > timing["end_tick"]
    ):
        timing["start_tick"] = None
        timing["end_tick"] = None
    return timing


def read_directory(maxfile, filename):
    global DLL_DIR_LIST
    DLL_DIR_LIST = read_chunks(maxfile, 'DllDirectory', ContainerChunk, DirectoryChunk)


def read_video_postqueue(maxfile, filename):
    global VID_PST_QUE
    VID_PST_QUE = read_chunks(maxfile, 'VideoPostQueue', superId=8)


def calc_point(data):
    count, offset = get_long(data, 0)
    dtype = np.dtype({
        "names": ("index", "point"),
        "formats": ("<u4", ("<f4", 3)),
        "offsets": (0, 4),
        "itemsize": 16,
    })
    available = (len(data) - offset) // dtype.itemsize
    records = np.frombuffer(data, dtype=dtype, count=min(count, available), offset=offset)
    return records["point"].reshape(-1)


def calc_point_float(data):
    count, offset = get_long(data, 0)
    available = (len(data) - offset) // 12
    return np.frombuffer(
        data,
        dtype="<f4",
        count=min(count, available) * 3,
        offset=offset,
    )


def calc_point_3d(chunk):
    data = chunk.data
    count, offset = get_long(data, 0)
    pointlist = []
    while (offset < len(data)):
        pt = Point3d()
        long, offset = get_long(data, offset)
        pt.points, offset = get_longs(data, offset, long)
        pt.flags, offset = get_short(data, offset)
        if ((pt.flags & 0x01) != 0):
            (pt.flag1, pt.flag2), offset = get_shorts(data, offset, 2)
        if ((pt.flags & 0x08) != 0):
            pt.group, offset = get_short(data, offset)
        if ((pt.flags & 0x10) != 0):
            pt.flag3, offset = get_long(data, offset)
        if ((pt.flags & 0x20) != 0):
            pt.fbits, offset = get_longs(data, offset, 2 * (long - 3))
        if (len(pt.points) > 0):
            pointlist.append(pt)
    return pointlist


def get_point(floatval, default=0.0):
    uid = get_guid(floatval)
    if (uid == 0x2007):  # Bezier-Float
        flv = floatval.get_first(0x7127)
        if (flv):
            try:
                return flv.get_first(0x2501).data[0]
            except:
                print("SyntaxError: %s - assuming 0.0!\n" % (floatval))
        return default
    if (uid == FLOAT_POINT):  # Float Wire
        flv = get_references(floatval)[0]
        return get_point(flv)
    else:
        return default


def get_point_3d(chunk, default=0.0):
    floats = []
    if (chunk):
        refs = get_references(chunk)
        for fl in refs:
            flt = get_point(fl, default)
            if (fl is not None):
                floats.append(flt)
    return floats


def get_point_array(values):
    if len(values) < 4:
        return np.empty(0, dtype=np.float32)
    count, offset = get_long(values, 0)
    available = (len(values) - offset) // 12
    return np.frombuffer(
        values,
        dtype="<f4",
        count=min(count, available) * 3,
        offset=offset,
    )


def get_mesh_polys(data):
    count, offset = get_long(data, 0)
    dtype = np.dtype({
        "names": ("vertices", "smoothing", "flags", "material"),
        "formats": (("<u4", 3), "<u4", "<u2", "<u2"),
        "offsets": (0, 12, 16, 18),
        "itemsize": 20,
    })
    available = (len(data) - offset) // dtype.itemsize
    records = np.frombuffer(data, dtype=dtype, count=min(count, available), offset=offset)
    return (
        records["vertices"],
        records["material"].astype(np.int32),
        records["smoothing"].astype(np.uint32),
    )


def get_face_chunks(chunk):
    faceflags = get_long(chunk.data)
    facechunk = chunk.get_first(0x0110)
    matlchunk = chunk.get_first(0x0130)
    size, step = get_long(facechunk.data)
    face, step = get_longs(facechunk.data, step, size)
    matid = get_short(matlchunk.data)[0] if matlchunk else 0
    return face, matid


def get_poly_data(chunk):
    offset = 0
    polylist = []
    data = chunk.data
    while (offset < len(data)):
        count, offset = get_long(data, offset)
        points, offset = get_longs(data, offset, count)
        polylist.append(points)
    return polylist


def get_poly_loops(chunk):
    looplist = []
    data = chunk.data
    counts, offset = get_long(data)
    while (offset < len(data)):
        count, offset = get_long(data, offset)
        point, offset = get_longs(data, offset, 2)
        (lp, sp), offset = get_longs(data, offset, 2)
        flags, offset = get_long(data, offset)
        loops = (*point, lp) if sp == FREESECT else point + (lp, sp)
        looplist.append(loops)
    return looplist


def get_uvw_coords(chunk, face_count=None):
    data = np.frombuffer(chunk.data, dtype="<u4")
    # Most UV-face chunks start with a face count, but legacy Editable Mesh
    # 0x0918 chunks hold exactly three indices per face with no header.
    start = 0 if (face_count is not None and len(data) == face_count * 3) else 1
    return data[start:start + ((len(data) - start) // 3) * 3].reshape(-1, 3)


def get_tri_data(chunk):
    data = chunk.data
    head, offset = get_long(data)
    count = max(0, min((head - offset) // 4, (len(data) - offset) // 4))
    indices = np.frombuffer(data, dtype="<u4", count=count, offset=offset)
    return indices[:(len(indices) // 3) * 3].reshape(-1, 3)


def get_property(properties, idx):
    for child in properties.children:
        if len(child.data) > 5 and not isinstance(child.data, tuple):
            if (get_short(child.data, 0)[0] == idx):
                return child
    return None


def get_bitmap(chunk):
    if (chunk is not None):
        pathstring = matlib = None
        parameters = get_references(chunk)
        if (len(parameters) >= 2) and parameters[1]:
            custom = parameters[1].get_first(0x3)
            if (custom is not None):
                pathchunk = custom.get_first(0x1230)
                pathlink = custom.get_first(0x1260)
                if (pathchunk and pathchunk.data):
                    pathstring = pathchunk.data
                elif (pathlink and pathlink.children):
                    matlib = pathlink.children[0]
            else:
                references = get_references(parameters[1])
                for reference in references:
                    if reference is not None:
                        for ref in reference.children:
                            imglink = ref.get_first(0x1260)
                            if imglink and imglink.children:
                                matlib = imglink.children[0]
                if matlib is None:
                    for block in parameters[1].children:
                        if (block.children and get_guid(block) == 0x3333):
                            matlib = block.children[0]
        if (matlib and matlib.data):
            idsize = len(matlib.data[:-4])
            metaidx = get_longs(matlib.data, idsize, len(matlib.data[idsize:]) // 4)[0]
            pathstring = get_metadata(metaidx[0])
        return pathstring
    return None


def get_texmap(chunk):
    texpath = None
    parameters = get_references(chunk)
    attributes = get_references(parameters[0])
    properties = get_references(attributes[1])
    for prop in properties:
        propname = next(
            (pc.data.decode("UTF-16", "ignore") for pc in prop.children if pc.types == 1), None)
        if propname == "filename":
            view = next(
                (ck.children[3] for ck in prop.children if ck.types == 3), None)
            if view:
                uid = get_long(view.children[0].data, 0)[0] 
                texpath = get_metadata(uid)
    return texpath


def get_material_name(reference):
    mtlname = ""
    name_chunk = reference.get_first(0x100)
    if name_chunk:
        split_data = name_chunk.data.split(b'@')
        if len(split_data) >= 2:
            splitname = b''.join(split_data[1].split(b'\x00'))
            mtlname = splitname.decode("UTF-8", "ignore")
    return mtlname


def get_rgb(colors, idx):
    prop = get_property(colors, idx)
    if (prop is not None):
        if not isinstance(prop.data, bytes) or len(prop.data) < 16:
            print("\tMaterialWarning: RGB property 0x%X has no inline color" % idx)
            return None
        siz = len(prop.data) - 16
        rgb, offset = get_floats(prop.data, siz, 3)
        return (rgb[0], rgb[1], rgb[2])
    return None


def get_color(colors, idx):
    prop = get_property(colors, idx)
    if (prop is not None):
        if not isinstance(prop.data, bytes) or len(prop.data) < 12:
            print("\tMaterialWarning: color property 0x%X has no inline color" % idx)
            return None
        siz = len(prop.data) - 12
        col, offset = get_floats(prop.data, siz, 3)
        return (col[0], col[1], col[2])
    return None


def get_value(value, idx):
    prop = get_property(value, idx)
    if (prop is not None):
        if not isinstance(prop.data, bytes) or len(prop.data) < 4:
            print("\tMaterialWarning: value property 0x%X has no inline value" % idx)
            return None
        siz = len(prop.data) - 4
        val, offset = get_float(prop.data, siz)
        return val
    return None


def get_parameter(values, fmt):
    if (fmt == 0x1):
        if not isinstance(values.data, bytes) or len(values.data) < 12:
            return None
        siz = len(values.data) - 12
        para, offset = get_floats(values.data, siz, 3)
    else:
        if not isinstance(values.data, bytes) or len(values.data) < 4:
            return None
        siz = len(values.data) - 4
        para, offset = get_float(values.data, siz)
    return para


def get_standard_material(refs):
    material = None
    try:
        if (len(refs) > 2):
            texmap = refs[1]
            colors = refs[2]
            material = Material()
            parameter = get_references(colors)[0]
            bitmap = get_bitmap(get_reference(texmap).get(3))
            glossmap = get_bitmap(get_reference(texmap).get(7))
            transmap = get_bitmap(get_reference(texmap).get(13))
            normalmap = get_bitmap(get_reference(texmap).get(17))
            material.set('ambient', get_color(parameter, 0x00))
            material.set('diffuse', get_color(parameter, 0x01))
            material.set('specular', get_color(parameter, 0x02))
            material.set('shinines', get_value(parameter, 0x0B))
            material.set('emissive', get_color(parameter, 0x08))
            parablock = refs[4]  # ParameterBlock2
            material.set('glossines', get_value(parablock, 0x02))
            material.set('metallic', get_value(parablock, 0x05))
            material.set('refraction', get_value(parablock, 0x06))
            material.set('opacity', get_value(parablock, 0x01))
            if (bitmap is not None):
                material.set('bitmap', Path(bitmap).name)
            if (glossmap is not None):
                material.set('glossmap', Path(glossmap).name)
            if (transmap is not None):
                material.set('transmap', Path(transmap).name)
            if (normalmap is not None):
                material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'StandardMtl' Error:", exc)
    return material


def get_standard_surface(arnold):
    material = None
    try:
        if (len(arnold) >= 2):
            shader = arnold[1]
            material = Material()
            shadertree = get_references(shader)
            for shade in shadertree:
                socket_name = next(
                    (sk.data.decode("UTF-16", "ignore") for sk in shade.children if sk.types == 1), None)
                if socket_name == "base_color":
                    socket_color = next(
                        (get_parameter(sk, 1) for sk in shade.children if sk.types == 3), (0.0, 0.0, 0.0))
                    material.set('diffuse', socket_color)
                elif socket_name == "specular_color":
                    socket_color = next(
                        (get_parameter(sk, 1) for sk in shade.children if sk.types == 3), (0.0, 0.0, 0.0))
                    material.set("specular", socket_color)
                elif socket_name == "emission_color":
                    socket_color = next(
                        (get_parameter(sk, 1) for sk in shade.children if sk.types == 3), (0.0, 0.0, 0.0))
                    material.set("emissive", socket_color)
                elif socket_name == "diffuse_roughness":
                    socket_value = next(
                        (get_parameter(sk, 2) for sk in shade.children if sk.types == 3), 0.0)
                    material.set("shinines", socket_value)
                elif socket_name == "specular":
                    socket_color = next(
                        (get_parameter(sk, 2) for sk in shade.children if sk.types == 3), 0.0)
                    material.set("glossines", socket_color)
                elif socket_name == "metalness":
                    socket_color = next(
                        (get_parameter(sk, 2) for sk in shade.children if sk.types == 3), 0.0)
                    material.set("metallic", socket_color)
                elif socket_name == "opacity":
                    socket_color = next(
                        (get_parameter(sk, 2) for sk in shade.children if sk.types == 3), 0.0)
                    material.set("opacity", socket_color)
                elif socket_name == "specular_IOR":
                    socket_color = next(
                        (get_parameter(sk, 2) for sk in shade.children if sk.types == 3), 1.45)
                    material.set("refraction", socket_color)
                elif socket_name == "base_color.shader":
                    mtldata = get_references(shade)
                    if mtldata:
                        bitmap = get_texmap(mtldata[0])
                        if bitmap:
                            material.set('bitmap', Path(bitmap).name)
                elif socket_name == "specular_color.shader":
                    mtldata = get_references(shade)
                    if mtldata:
                        specmap = get_texmap(mtldata[0])
                        if specmap:
                            material.set('glossmap', Path(specmap).name)
                elif socket_name == "diffuse_roughness.shader":
                    mtldata = get_references(shade)
                    if mtldata:
                        shinmap = get_texmap(mtldata[0])
                        if shinmap:
                            material.set('shinmap', Path(shinmap).name)
                elif socket_name == "opacity.shader":
                    mtldata = get_references(shade)
                    if mtldata:
                        opacmap = get_texmap(mtldata[0])
                        if opacmap:
                            material.set('transmap', Path(opacmap).name)
                elif socket_name == "normal.shader":
                    mtldata = get_references(shade)
                    if mtldata:
                        normalmap = get_texmap(mtldata[0])
                        if normalmap:
                            material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'StandardSurface' Error:", exc)
    return material


def get_physical_material(phys):
    material = None
    try:
        colors = phys[0]
        material = Material()
        textures = get_reference(colors)
        bitmap = get_bitmap(textures.get(1))
        glossmap = get_bitmap(textures.get(2))
        shinmap = get_bitmap(textures.get(4))
        normalmap = get_bitmap(textures.get(21))
        transmap = get_bitmap(textures.get(24))
        material.set('diffuse', get_rgb(colors, 0x02))
        material.set('metallic', get_value(colors, 0x05))
        material.set('shinines', 1.0 - get_value(colors, 0x03))
        material.set('glossines', 1.0 - get_value(colors, 0x04))
        material.set('opacity', 1.0 - get_value(colors, 0x06))
        material.set('specular', get_rgb(colors, 0x0A))
        material.set('emissive', get_rgb(colors, 0x1F))
        if (bitmap is not None):
            material.set('bitmap', Path(bitmap).name)
        if (glossmap is not None):
            material.set('glossmap', Path(glossmap).name)
        if (shinmap is not None):
            material.set('shinmap', Path(shinmap).name)
        if (transmap is not None):
            material.set('transmap', Path(transmap).name)
        if (normalmap is not None):
            material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'PhysicalMtl' Error:", exc)
    return material


def get_raytrace_material(rays):
    material = None
    try:
        if (len(rays) >= 2):
            colors = rays[0]
            texmap = rays[2]
            values = rays[5]
            material = Material()
            parameter = get_references(texmap)[0]
            attributes = get_references(parameter)[0]
            properties = get_references(attributes)
            for color in colors.children:
                if (color.types == 0x2) and color.get_first(0x3).data[0] == 0:
                    material.set('ambient', color.get_first(0x102).data)
                if (color.types == 0x2) and color.get_first(0x3).data[0] == 3:
                    material.set('diffuse', color.get_first(0x102).data)
                if (color.types == 0x2) and color.get_first(0x3).data[0] == 7:
                    material.set('emissive', color.get_first(0x102).data)
                if (color.types == 0x2) and color.get_first(0x3).data[0] == 14:
                    material.set('specular', color.get_first(0x102).data)
            material.set('shinines', get_value(values, 0x01))
            material.set('metallic', get_value(values, 0x02))
            material.set('glossines', get_value(values, 0x03))
            material.set('opacity', 1.0 - get_value(values, 0x04))
    except Exception as exc:
        print("\t'RaytraceMtl' Error:", exc)
    return material


def get_vray_material(vray):
    material = Material()
    try:
        parameter = vray.get(1)
        bitmap = get_bitmap(vray.get(7))
        shinmap = get_bitmap(vray.get(8))
        glossmap = get_bitmap(vray.get(11))
        transmap = get_bitmap(vray.get(19))
        normal = get_references(vray.get(10))
        material.set('diffuse', get_color(parameter, 0x01))
        material.set('specular', get_color(parameter, 0x02))
        material.set('shinines', get_value(parameter, 0x03))
        material.set('emissive', get_color(parameter, 0x33))
        material.set('glossines', get_value(parameter, 0x06))
        material.set('metallic', get_value(parameter, 0x19))
        material.set('refraction', get_value(parameter, 0x09))
        material.set('opacity', 1.0 - get_value(parameter, 0x1D))
        if (bitmap is not None):
            material.set('bitmap', Path(bitmap).name)
        if (shinmap is not None):
            material.set('shinmap', Path(shinmap).name)
        if (glossmap is not None):
            material.set('glossmap', Path(glossmap).name)
        if (transmap is not None):
            material.set('transmap', Path(transmap).name)
        if (normal and len(normal) > 0):
            material.set('strength', get_value(parameter, 0x06))
            refs = get_references(normal[0])
            if refs:
                normalmap = get_bitmap(refs[0])
                if (normalmap is not None):
                    material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'VrayMtl' Error:", exc)
    return material


def get_corona_material(mtl):
    material = Material()
    try:
        corona = mtl[0].children
        parameter = get_reference(mtl[0])
        bitmap = get_bitmap(parameter.get(0))
        shinmap = get_bitmap(parameter.get(1))
        glossmap = get_bitmap(parameter.get(2))
        transmap = get_bitmap(parameter.get(5))
        normal = get_references(parameter.get(6))
        material.set('diffuse', get_parameter(corona[0x03], 1))
        material.set('specular', get_parameter(corona[0x05], 1))
        material.set('shinines', get_parameter(corona[0x0D], 2))
        material.set('emissive', get_parameter(corona[0x08], 1))
        material.set('glossines', get_parameter(corona[0x0A], 2))
        material.set('metallic', get_parameter(corona[0x0E], 2))
        material.set('refraction', get_parameter(corona[0x3F], 2))
        material.set('opacity', 1.0 - get_parameter(corona[0x0B], 2))
        if (bitmap is not None):
            material.set('bitmap', Path(bitmap).name)
        if (shinmap is not None):
            material.set('shinmap', Path(shinmap).name)
        if (glossmap is not None):
            material.set('glossmap', Path(glossmap).name)
        if (transmap is not None):
            material.set('transmap', Path(transmap).name)
        if (normal and len(normal) > 0):
            values = normal[0].children
            refs = get_references(normal[0])
            if refs:
                normalmap = get_bitmap(refs[0])
                material.set('strength', get_parameter(values[0x03], 2))
                if (normalmap is not None):
                    material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'CoronaMtl' Error:", exc)
    return material


def get_skinhair_material(mtl):
    material = Material()
    try:
        corona = mtl[0].children
        parameter = get_reference(mtl[0])
        bitmap = get_bitmap(parameter.get(0))
        shinmap = get_bitmap(parameter.get(1))
        glossmap = get_bitmap(parameter.get(5))
        transmap = get_bitmap(parameter.get(2))
        normal = get_references(parameter.get(6))
        material.set('diffuse', get_parameter(corona[0x03], 1))
        material.set('specular', get_parameter(corona[0x0A], 1))
        material.set('shinines', get_parameter(corona[0x0C], 2))
        material.set('glossines', get_parameter(corona[0x08], 2))
        material.set('refraction', get_parameter(corona[0x0B], 2))
        material.set('opacity', 1.0 - get_parameter(corona[0x15], 2))
        if (bitmap is not None):
            material.set('bitmap', Path(bitmap).name)
        if (shinmap is not None):
            material.set('shinmap', Path(shinmap).name)
        if (glossmap is not None):
            material.set('glossmap', Path(glossmap).name)
        if (transmap is not None):
            material.set('transmap', Path(transmap).name)
        if (normal and len(normal) > 0):
            values = normal[0].children
            refs = get_references(normal[0])
            if refs:
                normalmap = get_bitmap(refs[0])
                material.set('strength', get_parameter(values[0x03], 2))
                if (normalmap is not None):
                    material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'CoronaSkinMtl' Error:", exc)
    return material


def get_corophysical_material(mtl):
    material = Material()
    try:
        corona = mtl[0].children
        parameter = get_reference(mtl[0])
        ambient = get_references(parameter.get(0))
        color = get_references(parameter.get(2))
        bitmap = get_bitmap(ambient[0])
        if bitmap is None:
            bitmap = get_bitmap(parameter.get(0))
        shinmap = get_bitmap(parameter.get(18))
        specmap = get_bitmap(get_reference(color[0]).get(0)) if color else None
        normal = get_references(parameter.get(20))
        material.set('diffuse', get_parameter(corona[0x52], 1))
        material.set('specular', get_parameter(corona[0x09], 1))
        material.set('shinines', 1.0 - get_parameter(corona[0x0F], 2))
        material.set('glossines', get_parameter(corona[0x3B], 2))
        material.set('metallic', get_parameter(corona[0x56], 2))
        material.set('refraction', get_parameter(corona[0x1B], 2))
        material.set('opacity', get_parameter(corona[0x36], 2))
        if (bitmap is not None):
            material.set('bitmap', Path(bitmap).name)
        if (shinmap is not None):
            material.set('shinmap', Path(shinmap).name)
        if (specmap is not None):
            material.set('glossmap', Path(specmap).name)
        if (normal and len(normal) > 0):
            values = normal[0].children
            material.set('strength', get_parameter(values[0x03], 2))
            refs = get_references(normal[0])
            if refs:
                normalmap = get_bitmap(refs[0])
                if (normalmap is not None):
                    material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'CoronaPhysicalMtl' Error:", exc)
    return material


def get_legacy_material(mtl):
    material = None
    try:
        if (len(mtl) > 1):
            colors = mtl[0]
            texmap = mtl[1]
            material = Material()
            color = colors.children
            parameter = get_reference(texmap)
            bitmap = parameter.get(3)
            material.set('ambient', color[2].get_first(0x102).data)
            material.set('diffuse', color[3].get_first(0x102).data)
            material.set('specular', color[4].get_first(0x102).data)
            material.set('glossines', get_float(color[5].get_first(0x100).data)[0])
            material.set('shinines', 1.0 - get_float(color[6].get_first(0x100).data)[0])
            material.set('opacity', 1.0 - get_float(color[7].get_first(0x100).data)[0])
            if (bitmap is not None):
                texchunk = bitmap.get_first(0x5010)
                if texchunk:
                    namechunk = texchunk.get_first(0x1230)
                    if namechunk:
                        material.set('bitmap', Path(namechunk.data).name)
    except:
        pass
    return material


LEGACY_PARAMBLOCK = 0x0008  # IParamBlock (pre-ParamBlock2)
LEGACY_BITMAP = 0x0240  # Bitmap texture
LEGACY_TEXMAPS = 0x1200  # StdMtl Texmaps container

# StdMtl Texmaps slots (keyed reference 2 * slot + 1 is the map)
TEXMAP_DIFFUSE = 1
TEXMAP_OPACITY = 6


def get_paramblock_values(pblock):
    """Return {index: value} from a legacy IParamBlock's inline values.

    Each parameter is a 0x0002 container holding its index (0x0003) and one
    inline value: float 0x0100, int 0x0101, or Point3/colour 0x0102.
    Parameters driven by a controller carry no inline value and are omitted.
    """
    values = {}
    if pblock is None:
        return values
    for entry in pblock.children:
        if entry.types != 0x0002 or not getattr(entry, "children", None):
            continue
        index_chunk = entry.get_first(0x0003)
        if index_chunk is None or len(index_chunk.data) < 4:
            continue
        index = struct.unpack_from("<i", index_chunk.data)[0]
        for child in entry.children:
            data = child.data
            if child.types == 0x0100 and isinstance(data, bytes) and len(data) >= 4:
                values[index] = struct.unpack_from("<f", data)[0]
            elif child.types == 0x0101 and isinstance(data, bytes) and len(data) >= 4:
                values[index] = struct.unpack_from("<i", data)[0]
            elif child.types == 0x0102 and isinstance(data, tuple) and len(data) >= 3:
                values[index] = tuple(data[:3])
    return values


def get_legacy_bitmap_path(bitmap):
    """Return the stored file path of a Bitmap texmap, legacy or ParamBlock2."""
    if bitmap is None:
        return None
    if get_guid(bitmap) == LEGACY_BITMAP:
        info = bitmap.get_first(0x5010)
        name = info.get_first(0x1230) if info is not None else None
        if name is not None and isinstance(name.data, str) and name.data:
            return name.data
    try:
        return get_bitmap(bitmap)
    except Exception:
        return None


def get_legacy_texmap(texmaps, slot):
    """Return the enabled map in a legacy Texmaps slot, or None."""
    if texmaps is None or get_guid(texmaps) != LEGACY_TEXMAPS:
        return None
    mask = texmaps.get_first(0x5003)
    if mask is not None and len(mask.data) >= 4:
        if not (struct.unpack_from("<I", mask.data)[0] >> slot) & 1:
            return None
    return get_reference(texmaps).get(2 * slot + 1)


def get_legacy_standard_material(refs):
    """Decode a 3ds Max 2-4 Standard material: IParamBlock + Texmaps.

    ParamBlock indices: 0 ambient, 1 diffuse, 2 specular, 3 shininess,
    4 shininess strength, 5 self-illumination, 6 opacity, 10 IOR.
    """
    values = get_paramblock_values(refs[0])
    texmaps = refs[1] if len(refs) > 1 else None
    material = Material()
    diffuse = values.get(1)
    material.set('ambient', values.get(0))
    material.set('diffuse', diffuse)
    material.set('specular', values.get(2))
    material.set('shinines', values.get(3))
    strength = values.get(4)
    if strength is not None:
        material.set('glossines', min(max(strength, 0.0), 1.0))
    opacity = values.get(6)
    if opacity is not None:
        material.set('opacity', min(max(opacity, 0.0), 1.0))
    self_illum = values.get(5)
    if self_illum and diffuse is not None and isinstance(self_illum, float):
        material.set('emissive', tuple(c * self_illum for c in diffuse))
    ior = values.get(10)
    if ior is not None and ior > 0.0:
        material.set('refraction', ior)
    bitmap = get_legacy_bitmap_path(get_legacy_texmap(texmaps, TEXMAP_DIFFUSE))
    if bitmap:
        material.set('bitmap', bitmap)
    transmap = get_legacy_bitmap_path(get_legacy_texmap(texmaps, TEXMAP_OPACITY))
    if transmap:
        material.set('transmap', transmap)
    return material


def get_arch_material(ad):
    material = Material()
    try:
        material.set('diffuse', get_rgb(ad, 0x1A))
        material.set('specular', get_color(ad, 0x05))
        material.set('shinines', get_value(ad, 0x0B))
    except:
        pass
    return material


def get_multi_material_ids(parameter_block, count):
    if parameter_block is None:
        return list(range(count))

    for chunk in parameter_block.children:
        if chunk.types == 0x000A:
            header_size = 11
        elif chunk.types == 0x000E:
            header_size = 15
        else:
            continue
        if not isinstance(chunk.data, bytes) or len(chunk.data) < header_size + 4:
            continue

        _, parameter_type = struct.unpack_from("<HH", chunk.data)
        if parameter_type != 0x0801:
            continue
        stored_count = struct.unpack_from("<I", chunk.data, header_size)[0]
        if stored_count != count:
            continue

        offset = header_size + 4
        material_ids = []
        for _ in range(stored_count):
            if offset + 5 > len(chunk.data):
                material_ids = []
                break
            material_id = struct.unpack_from("<i", chunk.data, offset + 1)[0]
            if material_id <= 0:
                material_ids = []
                break
            material_ids.append(material_id - 1)
            offset += 5
        if material_ids:
            return material_ids

    return list(range(count))


class MaterialSlotMap(dict):
    """Material-ID to slot mapping; ``cycle`` is set for dense ID tables."""

    cycle = None


def remap_material_indices(indices, slot_map):
    indices = np.asarray(indices, dtype=np.int32)
    if not len(indices) or not slot_map:
        return indices

    fallback = slot_map.get(0, min(slot_map.values()))
    remapped = np.full(indices.shape, fallback, dtype=np.int32)
    missing = []
    cycle = getattr(slot_map, "cycle", None)
    for material_id in np.unique(indices):
        slot = slot_map.get(int(material_id))
        if slot is None and cycle:
            # Dense Multi/Sub materials repeat: face ID n uses sub-material
            # n modulo the sub-material count.
            slot = slot_map.get(int(material_id) % cycle)
        if slot is None:
            missing.append(int(material_id))
        else:
            remapped[indices == material_id] = slot
    if missing:
        print(
            "\tMaterialWarning: no Multi/Sub material for face IDs %s"
            % ", ".join(str(material_id + 1) for material_id in missing)
        )
    return remapped


def adjust_multi_material(resolver, obj, mat):
    references = get_references(mat)
    parameter_block = references[0] if references and get_guid(references[0]) == 0x82 else None
    count_chunk = mat.get_first(0x4002)
    if (
        count_chunk is None
        or not isinstance(count_chunk.data, bytes)
        or len(count_chunk.data) < 4
    ):
        count = len(references) - (1 if parameter_block is not None else 0)
    else:
        count = struct.unpack_from("<I", count_chunk.data)[0]

    submaterials = references[1:] if parameter_block is not None else references
    material_ids = get_multi_material_ids(parameter_block, count)
    slot_map = MaterialSlotMap()
    if count and material_ids == list(range(count)):
        slot_map.cycle = count
    for index in range(count):
        material_id = material_ids[index]
        submaterial = submaterials[index] if index < len(submaterials) else None
        before = len(obj.data.materials)
        nested_map = (
            adjust_material(resolver, obj, submaterial)
            if submaterial is not None
            else None
        )
        if len(obj.data.materials) == before:
            placeholder_name = "BlenderMax Missing Material %d" % (material_id + 1)
            placeholder = bpy.data.materials.get(placeholder_name)
            if placeholder is None:
                placeholder = bpy.data.materials.new(placeholder_name)
            obj.data.materials.append(placeholder)
        slot = (
            nested_map.get(material_id, before)
            if isinstance(nested_map, dict)
            else before
        )
        slot_map.setdefault(material_id, slot)
    return slot_map


def adjust_material(resolver, obj, mat):
    material = mtl_id = mtl_name = None
    if (mat is not None):
        uid = get_guid(mat)
        if (uid == 0x0002):  # Standard
            mtl_id = mat.get_first(0x4000)
            refs = get_references(mat)
            if refs and get_guid(refs[0]) == LEGACY_PARAMBLOCK:
                material = get_legacy_standard_material(refs)
            else:
                material = get_standard_material(refs)
        elif (uid == 0x0006):  # Legacy
            mtl_id = mat.get_first(0x4000)
            refs = get_references(mat)
            material = get_legacy_material(refs)
        elif (uid == VRAY_MTL):  # VRayMtl
            mtl_id = mat.get_first(0x5431)
            refs = get_reference(mat)
            if refs:
                material = get_vray_material(refs)
        elif (uid == CORO_MTL):  # CoronaMtl
            mtl_id = mat.get_first(0x0FA0)
            refs = get_references(mat)
            material = get_corona_material(refs)
        elif (uid == PHYS_MTL):  # PhysicalMtl
            id_chunk = mat.get_first(0x100)
            mtl_name = id_chunk.get_first(0x4001).data
            refs = get_references(mat)
            material = get_physical_material(refs)
        elif (uid == RAYT_MTL):  # RaytraceMtl
            mtl_name = get_material_name(mat)
            refs = get_references(mat)
            material = get_raytrace_material(refs)
        elif (uid == SURF_MTL):  # StandardSurface
            mtl_name = get_material_name(mat)
            refs = get_references(mat)
            shaders = get_references(refs[0])
            material = get_standard_surface(shaders)
        elif (uid in {HAIR_MTL, SKIN_MTL}):  # CoronaSkinMtl
            mtl_id = mat.get_first(0x0FA0)
            refs = get_references(mat)
            material = get_skinhair_material(refs)
        elif (uid == COPHY_MTL):  # CoronaPhysicalMtl
            refs = get_references(mat)
            mtl_id = mat.get_first(0x0FA0)
            material = get_corophysical_material(refs)
        elif (uid == LAYER_MTL):  # CoronaLayeredMtl
            refs = get_references(mat)
            layers = get_reference(refs[0])
            mtl_id = mat.get_first(0x0FA0)
            for layer in layers.values():
                material = adjust_material(resolver, obj, layer)
        elif (uid in {SHEL_MTL, VTWO_MTL}):  # VRay2SidedMtl
            sides = get_references(mat)
            for side in sides:
                material = adjust_material(resolver, obj, side)
        elif (uid == ARCH_MTL):  # Arch
            mtl_name = get_material_name(mat)
            refs = get_references(mat)
            material = get_arch_material(refs[0])
        elif (uid == 0x0200):  # Multi/Sub-Object
            return adjust_multi_material(resolver, obj, mat)
        if (obj is not None) and isinstance(material, Material):
            if mtl_name is None:
                matname = mtl_id.children[0].data if mtl_id else get_cls_name(mat)
            else:
                matname = mtl_name
            # Make material name unique by appending bitmap name if available
            # This prevents multiple different materials with the same name from being treated as one
            texname = material.get('bitmap', None)
            if texname is not None:
                matname = f"{matname}_{PureWindowsPath(texname).stem}"
            objMaterial = bpy.data.materials.get(matname)
            if objMaterial is None:
                objMaterial = bpy.data.materials.new(matname)
            obj.data.materials.append(objMaterial)
            shader = PrincipledBSDFWrapper(objMaterial, is_readonly=False)
            shader.base_color = objMaterial.diffuse_color[:3] = material.get('diffuse', (0.8, 0.8, 0.8))
            shader.specular_tint = objMaterial.specular_color[:3] = material.get('specular', (1, 1, 1))
            shader.specular = objMaterial.specular_intensity = material.get('glossines', 0.5)
            shader.roughness = objMaterial.roughness = 1.0 - material.get('shinines', 0.6)
            shader.alpha = objMaterial.diffuse_color[3] = material.get('opacity', 1.0)
            shader.metallic = objMaterial.metallic = material.get('metallic', 0.0)
            shader.emission_color = material.get('emissive', (0.0, 0.0, 0.0))
            shader.ior = material.get('refraction', 1.45)
            texname = material.get('bitmap', None)
            shinmap = material.get('shinmap', None)
            glossmap = material.get('glossmap', None)
            transmap = material.get('transmap', None)
            normalmap = material.get('normalmap', None)
            if (texname is not None):
                image = resolver.load(str(texname))
                if (image is not None):
                    if image.depth in {32, 16}:
                        image.alpha_mode = 'CHANNEL_PACKED'
                    shader.base_color_texture.image = image
            if (shinmap is not None):
                image = resolver.load(str(shinmap), data=True)
                if (image is not None):
                    shader.roughness_texture.image = image
            if (glossmap is not None):
                image = resolver.load(str(glossmap), data=True)
                if (image is not None):
                    shader.specular_tint_texture.image = image
            if (normalmap is not None):
                shader.normalmap_strength = material.get('strength', 0.8)
                image = resolver.load(str(normalmap), data=True)
                if (image is not None):
                    shader.normalmap_texture.image = image
            if (transmap is not None):
                if (transmap == texname and shader.node_principled_bsdf.inputs[0].is_linked):
                    imgwrap = shader.base_color_texture.node_image
                    shader.material.node_tree.links.new(
                        imgwrap.outputs["Alpha"],
                        shader.node_principled_bsdf.inputs["Alpha"],
                    )
                else:
                    image = resolver.load(str(transmap), data=True)
                    if (image is not None):
                        shader.alpha_texture.image = image
            if (transmap or shader.node_principled_bsdf.inputs["Alpha"].default_value < 1.0):
                shader.material.surface_render_method = 'DITHERED'


def get_bezier_floats(pos):
    refs = get_references(pos)
    floats = get_point_3d(pos)
    if any(rf.get_first(0x2501) for rf in refs):
        floats.clear()
        for ref in refs:
            floats.append(ref.get_first(0x2501).data[0])
    return floats


def get_position(pos):
    position = mathutils.Vector()
    if (pos):
        uid = get_guid(pos)
        if (uid == MATRIX_POS):  # Position XYZ
            position = mathutils.Vector(get_bezier_floats(pos))
        elif (uid == 0x2008):  # Bezier Position
            position = mathutils.Vector(pos.get_first(0x2503).data)
        elif (uid == 0x442312):  # TCB Position
            position = mathutils.Vector(pos.get_first(0x2503).data)
        elif (uid == 0x4B4B1002):  # Position List
            refs = get_references(pos)
            if (len(refs) >= 3):
                return get_position(refs[0])
    return position


def get_rotation(pos):
    rotation = mathutils.Quaternion()
    if (pos):
        uid = get_guid(pos)
        if (uid == 0x2012):  # Euler XYZ
            rot = get_bezier_floats(pos)
            rotation = mathutils.Euler((rot[0], rot[1], rot[2])).to_quaternion()
        elif (uid == 0x442313):  # TCB Rotation
            rot = pos.get_first(0x2504).data
            rotation = max_quaternion(rot)
        elif (uid == MATRIX_ROT):  # Rotation Wire
            return get_rotation(get_references(pos)[0])
        elif (uid == 0x4B4B1003):  # Rotation List
            refs = get_references(pos)
            if (len(refs) > 3):
                return get_rotation(refs[0])
    return rotation


def get_scale(pos):
    scale = mathutils.Vector((1.0, 1.0, 1.0))
    if (pos):
        uid = get_guid(pos)
        if (uid == MATRIX_SCL):  # ScaleXYZ
            pos = get_bezier_floats(pos)
        elif (uid == 0x2010):  # Bezier Scale
            scl = pos.get_first(0x2501)
            if (scl is None):
                scl = pos.get_first(0x2505)
            pos = scl.data
        elif (uid == 0x442315):  # TCB Zoom
            scl = pos.get_first(0x2501)
            if (scl is None):
                scl = pos.get_first(0x2505)
            pos = scl.data
        elif (uid == 0x4B4B1004):  # Scale List
            refs = get_references(pos)
            if (len(refs) >= 3):
                return get_scale(refs[0])
        else:
            pos = (1.0, 1.0, 1.0)
        scale = mathutils.Vector(pos[:3])
    return scale


def get_scale_matrix(pos):
    values = (1.0, 1.0, 1.0)
    if pos:
        uid = get_guid(pos)
        if uid == MATRIX_SCL:
            values = get_bezier_floats(pos)
        elif uid in {0x2010, 0x442315, 0x2004}:
            chunk = pos.get_first(0x2505) or pos.get_first(0x2501)
            if chunk is not None:
                values = chunk.data
        elif uid == 0x4B4B1004:
            refs = get_references(pos)
            if refs:
                return get_scale_matrix(refs[0])
    return max_scale_matrix(values)


def create_matrix(prc):
    uid = get_guid(prc)
    mtx = mathutils.Matrix.Identity(4)
    if (uid == 0x2005):  # Position/Rotation/Scale
        pos = get_position(get_references(prc)[0])
        rot = get_rotation(get_references(prc)[1])
        scl = get_scale_matrix(get_references(prc)[2])
        mtx = (
            mathutils.Matrix.Translation(pos)
            @ rot.to_matrix().to_4x4()
            @ scl
        )
    elif (uid == 0x9154):  # BipSlave Control
        biped = get_references(prc)[-1]
        if biped and (get_guid(biped) == BIPED_ANIM):
            ref = get_references(biped)
            scl = get_scale_matrix(get_references(ref[1])[0])
            rot = get_rotation(get_references(ref[2])[0])
            pos = get_position(get_references(ref[3])[0])
            mtx = (
                mathutils.Matrix.Translation(pos)
                @ rot.to_matrix().to_4x4()
                @ scl
            )
    elif (uid == 0x2006):  # Look At
        refs = get_references(prc)
        pos = get_position(refs[1]) if len(refs) > 1 else mathutils.Vector()
        scl = get_scale_matrix(refs[3]) if len(refs) > 3 else mathutils.Matrix.Identity(4)
        mtx = mathutils.Matrix.Translation(pos) @ scl
    return mtx


def get_matrix_mesh_material(node):
    refs = get_reference(node)
    if (refs):
        prs = refs.get(0, None)
        msh = refs.get(1, None)
        mat = refs.get(3, None)
        lyr = refs.get(6, None)
    else:
        refs = get_references(node)
        prs = refs[0] if len(refs) else None
        msh = refs[1] if len(refs) > 1 else None
        mat = refs[3] if len(refs) > 3 else None
        lyr = refs[6] if len(refs) > 6 else None
    return prs, msh, mat, lyr


def adjust_matrix(obj, node):
    mtx = create_matrix(node)
    obj.matrix_world = mtx @ obj.matrix_world.copy()
    return mtx


def sanitize_faces(mesh):
    """Drop faces Blender cannot represent and keep per-face data aligned.

    Faces with out-of-range or repeated vertex indices are removed together
    with their material ID, smoothing mask, and UV faces. Coincident faces,
    such as the opposite-facing copies used for double-sided walls, are kept:
    ``Mesh.validate()`` would delete them and leave the per-face arrays longer
    than the polygon list.
    """
    faces = mesh.faces
    face_count = len(faces)
    mesh.face_source = (face_count, np.arange(face_count))
    if not face_count:
        return 0
    vertex_count = np.asarray(mesh.verts).size // 3
    if isinstance(faces, np.ndarray) and faces.ndim == 2:
        keep = (
            (faces < vertex_count).all(axis=1)
            & (faces[:, 0] != faces[:, 1])
            & (faces[:, 1] != faces[:, 2])
            & (faces[:, 0] != faces[:, 2])
        )
    else:
        keep = np.fromiter(
            (
                len(face) >= 3
                and len(set(face)) == len(face)
                and all(0 <= index < vertex_count for index in face)
                for face in faces
            ),
            dtype=np.bool_,
            count=face_count,
        )
    removed = face_count - int(keep.sum())
    if not removed:
        return 0

    def filtered(values):
        if len(values) != face_count:
            return values
        if isinstance(values, np.ndarray):
            return values[keep]
        return [value for value, kept in zip(values, keep) if kept]

    mesh.face_source = (face_count, np.flatnonzero(keep))
    mesh.faces = filtered(faces)
    mesh.mats = filtered(mesh.mats)
    mesh.smoothing = filtered(mesh.smoothing)
    mesh.uvids = [filtered(uvids) for uvids in mesh.uvids]
    return removed


def draw_shape(name, mesh, faces):
    vertices = np.asarray(mesh.verts, dtype=np.float32).reshape(-1, 3)
    counts = np.fromiter((len(face) for face in faces), dtype=np.int32, count=len(faces))
    looplines = int(counts.sum())
    loopstart = np.empty(len(faces), dtype=np.int32)
    if len(loopstart):
        loopstart[0] = 0
        np.cumsum(counts[:-1], out=loopstart[1:])
    indices = (
        np.concatenate([np.asarray(face, dtype=np.int32) for face in faces])
        if looplines
        else np.empty(0, dtype=np.int32)
    )
    shape = bpy.data.meshes.new(name)
    shape.vertices.add(len(vertices))
    shape.loops.add(looplines)
    shape.polygons.add(len(faces))
    if len(vertices):
        # A mesh without vertices has no position attribute in Blender 5.
        shape.attributes["position"].data.foreach_set("vector", vertices.ravel())
    shape.polygons.foreach_set("loop_start", loopstart)
    shape.polygons.foreach_set("loop_total", counts)
    shape.loops.foreach_set("vertex_index", indices)
    return shape


def apply_smoothing_groups(shape, smoothing_groups):
    groups = np.asarray(smoothing_groups, dtype=np.uint32).reshape(-1)
    if not len(groups):
        return shape
    if len(groups) != len(shape.polygons):
        print(
            "\tSmoothingWarning: %d smoothing groups for %d polygons in '%s'; "
            "smoothing skipped" % (len(groups), len(shape.polygons), shape.name)
        )
        return shape

    attribute = shape.attributes.new(
        name="max_smoothing_group",
        type='INT',
        domain='FACE',
    )
    attribute.data.foreach_set("value", groups.view(np.int32))
    shape.polygons.foreach_set("use_smooth", groups != 0)
    shape.update(calc_edges=True)

    edge_groups = {}
    for polygon, group in zip(shape.polygons, groups):
        vertices = tuple(polygon.vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge = (min(first, second), max(first, second))
            edge_groups.setdefault(edge, []).append(int(group))

    sharp_edges = set()
    for edge, adjacent in edge_groups.items():
        for first_index, first_group in enumerate(adjacent):
            if any(
                first_group == 0
                or second_group == 0
                or not (first_group & second_group)
                for second_group in adjacent[first_index + 1:]
            ):
                sharp_edges.add(edge)
                break

    edge_sharp = np.fromiter(
        (
            (min(edge.vertices), max(edge.vertices)) in sharp_edges
            for edge in shape.edges
        ),
        dtype=np.bool_,
        count=len(shape.edges),
    )
    shape.edges.foreach_set("use_edge_sharp", edge_sharp)
    return shape


def draw_map(shape, uvmap, uvcoords, uvwids):
    uvlayer = shape.uv_layers.new(do_init=True)
    if (uvlayer):
        if (uvmap > 0):
            uvlayer.name = 'UVMap_%d' % uvmap
        uvcord = np.asarray(uvcoords, dtype=np.float32).reshape(-1, 3)[:, :2]
        try:
            uvindices = np.concatenate(
                [np.asarray(face, dtype=np.int32) for face in uvwids]
            )
            uvloops = uvcord[uvindices].reshape(-1)
            uvlayer.data.foreach_set("uv", uvloops)
        except Exception as exc:
            print('\tArrayLengthMismatchError: %s' % exc)
    return shape


def create_shape(context, settings, node, mesh, mat):
    meshname = ""
    filename, obtypes, search, resolver = settings
    name = node.get_first(0x0962)
    if name is not None:
        meshname = name.data
    removed = sanitize_faces(mesh)
    if removed:
        print("\tMeshWarning: removed %d degenerate faces from '%s'" % (removed, meshname))
    meshobject = draw_shape(meshname, mesh, mesh.faces)
    face_sources[meshobject.as_pointer()] = mesh.face_source
    if ('UV' in obtypes and mesh.maps):
        for idx, uvm in enumerate(mesh.maps[:min(len(mesh.cords), len(mesh.uvids))]):
            meshobject = draw_map(meshobject, idx, mesh.cords[idx], mesh.uvids[idx])
    meshobject.update(calc_edges=True)
    apply_smoothing_groups(meshobject, mesh.smoothing)
    meshobject.update()
    obj = bpy.data.objects.new(meshname, meshobject)
    context.view_layer.active_layer_collection.collection.objects.link(obj)
    if ('MATERIAL' in obtypes):
        material_slot_map = adjust_material(resolver, obj, mat)
        if (len(mesh.mats) > 0):
            try:
                material_indices = (
                    remap_material_indices(mesh.mats, material_slot_map)
                    if isinstance(material_slot_map, dict)
                    else mesh.mats
                )
                obj.data.polygons.foreach_set("material_index", material_indices)
            except Exception as exc:
                print('\tArrayLengthMismatchError: %s' % exc)
    object_list.append(obj)
    return object_list


def create_dummy_object(context, node, uid):
    dummy = bpy.data.objects.new(get_node_name(node), None)
    dummy.empty_display_type = 'SINGLE_ARROW' if uid == BIPED_OBJ else 'PLAIN_AXES'
    context.view_layer.active_layer_collection.collection.objects.link(dummy)
    return dummy


def create_camera_object(context, node):
    name = get_node_name(node)
    camera = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, camera)
    context.view_layer.active_layer_collection.collection.objects.link(obj)
    return obj


def create_light_object(context, node, light_type):
    name = get_node_name(node)
    light = bpy.data.lights.new(name, light_type)
    obj = bpy.data.objects.new(name, light)
    context.view_layer.active_layer_collection.collection.objects.link(obj)
    return obj


def create_editable_poly(context, settings, node, msh, mat):
    polychunk = msh.get_first(0x08FE)
    created = []
    if (polychunk):
        mesh = Mesh3d()
        for child in polychunk.children:
            if isinstance(child.data, tuple):
                created = create_shape(context, settings, node, mesh, mat)
            elif (child.types == 0x0100):
                mesh.verts = calc_point(child.data)
            elif (child.types == 0x0108):
                mesh.polys = get_poly_loops(child)
            elif (child.types == 0x010A):
                mesh.tris = get_tri_data(child)
            elif (child.types == 0x011A):
                mesh.points = calc_point_3d(child)
            elif (child.types == 0x0118):
                face, matid = get_face_chunks(child) 
                mesh.faces.append(face)
                mesh.mats.append(matid)
            elif (child.types == 0x0124):
                mesh.maps.append(get_long(child.data, 0)[0])
            elif (child.types == 0x0128):
                mesh.cords.append(calc_point_float(child.data))
            elif (child.types == 0x012B):
                mesh.uvids.append(get_poly_data(child))
            elif (child.types == 0x0310):
                mesh.polys = get_poly_data(child)
        if (mesh.points is not None):
            mesh.set(mesh.points)
            created += create_shape(context, settings, node, mesh, mat)
        elif (mesh.faces is not None):
            created += create_shape(context, settings, node, mesh, mat)
        elif (mesh.tris is not None) and 'UV' not in settings[1]:
            created += create_shape(context, settings, node, mesh, mat)
    return created


def create_editable_mesh(context, settings, node, msh, mat):
    meshchunk = msh.get_first(0x08FE)
    created = []
    if (meshchunk):
        editmesh = Mesh3d()
        # Legacy meshes store UVs in 0x0916/0x0918, newer ones in 0x2394/0x2396.
        # Detect by presence: the class ID alone does not tell them apart.
        legacy_uv = (
            get_guid(msh) == 0x019
            or meshchunk.get_first(0x0916) is not None
        )
        cnt = 0x0916 if legacy_uv else 0x2394
        uvm = 0x0918 if legacy_uv else 0x2396
        vertex_chunk = meshchunk.get_first(0x0914)
        faceid_chunk = meshchunk.get_first(0x0912)
        if (vertex_chunk and faceid_chunk):
            editmesh.verts = get_point_array(vertex_chunk.data)
            editmesh.faces, editmesh.mats, editmesh.smoothing = get_mesh_polys(
                faceid_chunk.data
            )
            for chunk in meshchunk.children:
                if (chunk.types in {0x0924, 0x0959}):
                    editmesh.maps.append(get_long(chunk.data, 0)[0])
                elif (chunk.types == cnt):
                    editmesh.cords.append(get_point_array(chunk.data))
                elif (chunk.types == uvm):
                    editmesh.uvids.append(
                        get_uvw_coords(chunk, len(editmesh.faces))
                    )
            if editmesh.cords and not editmesh.maps:
                editmesh.maps.append(0)
            created += create_shape(context, settings, node, editmesh, mat)
    return created


def get_context_matrix(mod_app):
    """Column-vector ModContext matrix (object to modifier-context space)."""
    matrix = np.eye(4)
    chunk = mod_app.get_first(0x2510) if mod_app is not None else None
    if chunk is not None and isinstance(chunk.data, tuple) and len(chunk.data) >= 12:
        rows = np.asarray(chunk.data[:12], dtype=np.float64).reshape(4, 3)
        matrix[:3, :3] = rows[:3].T
        matrix[:3, 3] = rows[3]
    return matrix


def get_uvw_mapping(modifier, mod_app):
    """Decode a legacy UVW Map modifier into (settings, object-to-gizmo matrix)."""
    refs = get_references(modifier)
    if len(refs) < 2 or refs[1] is None or get_guid(refs[1]) != LEGACY_PARAMBLOCK:
        return None
    values = get_paramblock_values(refs[1])
    if PB_MAPTYPE not in values:
        return None
    mapping = UVWMapSettings.from_paramblock(values)
    if mapping.map_type not in SUPPORTED_MAP_TYPES or mapping.channel != 0:
        return None
    gizmo = np.array(create_matrix(refs[0])) if refs[0] is not None else np.eye(4)
    # 3ds Max 3+ UVW Maps pre-rotate the gizmo icon (A_PLUGIN1): 180 degrees
    # about Z for planar and box, 90 degrees for cylindrical.
    transform = object_to_gizmo(
        mapping,
        gizmo,
        get_context_matrix(mod_app),
        rotate_icon=True,
    )
    return mapping, transform


def get_unwrap_uvs(modifier):
    """Decode Unwrap UVW local data: (texture vertices (N, 3), faces (F, 3)).

    Both the 3ds Max 3 Unwrap UVW and UnwrapUVW+ classes store a full copy of
    the channel: 0x0100 vertex count, 0x0110 Point3 texture vertices,
    0x0230 face count, and 0x0240 three uint32 vertex indices per face.
    """
    chunks = [modifier.get_first(t) for t in (0x0100, 0x0110, 0x0230, 0x0240)]
    if any(c is None or not isinstance(c.data, bytes) for c in chunks):
        return None
    count_chunk, vertex_chunk, face_count_chunk, face_chunk = chunks
    if len(count_chunk.data) < 4 or len(face_count_chunk.data) < 4:
        return None
    count = struct.unpack_from("<I", count_chunk.data)[0]
    face_count = struct.unpack_from("<I", face_count_chunk.data)[0]
    if len(vertex_chunk.data) != count * 12 or len(face_chunk.data) != face_count * 12:
        print("\tUnwrapWarning: inconsistent Unwrap UVW payload sizes")
        return None
    vertices = np.frombuffer(vertex_chunk.data, dtype="<f4").reshape(-1, 3)
    faces = np.frombuffer(face_chunk.data, dtype="<u4").reshape(-1, 3)
    if len(faces) and faces.max() >= count:
        print("\tUnwrapWarning: Unwrap UVW face index out of range")
        return None
    return vertices, faces


def apply_unwrap(obj, unwrap):
    """Replace map channel 1 with Unwrap UVW coordinates.

    Unwrap faces pair with the incoming mesh's faces by index, so this only
    applies when the face count matches the base mesh and every polygon is a
    triangle. Returns True when applied.
    """
    mesh = obj.data
    source = face_sources.get(mesh.as_pointer())
    vertices, faces = unwrap
    if obj.type != 'MESH' or source is None or source[0] != len(faces):
        return False
    kept = source[1]
    loop_total = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", loop_total)
    if len(kept) != len(mesh.polygons) or (loop_total != 3).any():
        return False
    corners = faces[kept].reshape(-1)
    layer = mesh.uv_layers[0] if len(mesh.uv_layers) else mesh.uv_layers.new(name="UVMap")
    layer.data.foreach_set("uv", vertices[corners, :2].astype(np.float32).ravel())
    return True


def _u32_array(chunk):
    if chunk is None or not isinstance(chunk.data, bytes) or len(chunk.data) % 4:
        return None
    return np.frombuffer(chunk.data, dtype="<u4")


def _u32(chunk):
    values = _u32_array(chunk)
    return int(values[0]) if values is not None and len(values) else None


def delete_sequence(count, deletions):
    """Indices of range(count) surviving deletion at each position in turn.

    Edit Mesh records deletions as positions in the list as it shrinks, so
    deleting [3, 3] from five items removes the original items 3 and 4.
    A Fenwick tree finds each position in O(log n), keeping large meshes
    linear in memory and fast. Returns None for an out-of-range position.
    """
    deletions = np.asarray(deletions, dtype=np.int64).ravel()
    if len(deletions) > count:
        return None
    if not len(deletions):
        return np.arange(count, dtype=np.int64)
    tree = np.ones(count + 1, dtype=np.int64)
    tree[0] = 0
    for index in range(1, count + 1):
        parent = index + (index & -index)
        if parent <= count:
            tree[parent] += tree[index]
    removed = np.zeros(count, dtype=np.bool_)
    remaining = count
    top = 1 << count.bit_length()
    for position in deletions.tolist():
        if position < 0 or position >= remaining:
            return None
        node, rank, step = 0, position + 1, top
        while step:
            candidate = node + step
            if candidate <= count and tree[candidate] < rank:
                node = candidate
                rank -= tree[candidate]
            step >>= 1
        removed[node] = True
        index = node + 1
        while index <= count:
            tree[index] -= 1
            index += index & -index
        remaining -= 1
    return np.flatnonzero(~removed)


def get_edit_mesh(mod_app, vertex_count, face_count):
    """Decode 3ds Max 3 Edit Mesh local data (ModApp 0x2512), or None.

    ``vertex_count`` and ``face_count`` describe the incoming mesh. The data
    is only decoded when its stored input counts match them, so corrupt or
    stale counts never size an allocation. Returns per-vertex offsets,
    surviving input vertices and faces, output faces in output vertex
    indices, and output smoothing groups.
    """
    local = mod_app.get_first(0x2512) if mod_app is not None else None
    delta = local.get_first(0x2765) if local is not None else None
    if delta is None:
        return None
    if any(child.types in EDIT_MESH_UNSUPPORTED for child in delta.children):
        return None
    if (
        _u32(delta.get_first(0x2820)) != vertex_count
        or _u32(delta.get_first(0x2810)) != face_count
    ):
        return None
    moves = local.get_first(0x2760)
    offsets = None
    if moves is not None:
        # One Point3 per input vertex. Shorter arrays occur but are not
        # understood (padding them with zero offsets was wrong for most
        # objects checked against exported geometry), so they are rejected.
        if not isinstance(moves.data, bytes) or len(moves.data) != vertex_count * 12:
            return None
        offsets = np.frombuffer(moves.data, dtype="<f4").reshape(-1, 3)
    vertex_deletes = _u32_array(delta.get_first(0x2790))
    face_deletes = _u32_array(delta.get_first(0x2800))
    alive_vertices = delete_sequence(
        vertex_count, [] if vertex_deletes is None else vertex_deletes.tolist()
    )
    alive_faces = delete_sequence(
        face_count, [] if face_deletes is None else face_deletes.tolist()
    )
    if alive_vertices is None or alive_faces is None:
        return None
    output_vertices = _u32(local.get_first(0x3000))
    if output_vertices is not None and output_vertices != len(alive_vertices):
        return None
    faces = _u32_array(delta.get_first(0x2780))
    if faces is not None:
        if len(faces) != len(alive_faces) * 3:
            return None
        faces = faces.reshape(-1, 3).astype(np.int64)
        if len(faces) and faces.max() >= len(alive_vertices):
            return None
    smoothing = _u32_array(delta.get_first(0x2840))
    if smoothing is not None and len(smoothing) != len(alive_faces):
        smoothing = None
    return {
        "vertex_count": vertex_count,
        "face_count": face_count,
        "offsets": offsets,
        "alive_vertices": alive_vertices,
        "alive_faces": alive_faces,
        "faces": faces,
        "smoothing": smoothing,
    }


def apply_edit_mesh(obj, edit):
    """Rebuild obj's mesh as the Edit Mesh output. Returns True when applied.

    Output face i comes from surviving input face i: its material, UVs, and
    (unless the Edit Mesh stores new ones) smoothing groups are carried over.
    """
    mesh = obj.data
    source = face_sources.get(mesh.as_pointer()) if obj.type == 'MESH' else None
    if (
        source is None
        or source[0] != edit["face_count"]
        or len(mesh.vertices) != edit["vertex_count"]
    ):
        return False
    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", positions)
    positions = positions.reshape(-1, 3)
    if edit["offsets"] is not None:
        positions = positions + edit["offsets"]
    alive_vertices = edit["alive_vertices"]
    remap = -np.ones(len(positions), dtype=np.int64)
    remap[alive_vertices] = np.arange(len(alive_vertices))

    loop_start = np.empty(len(mesh.polygons), dtype=np.int32)
    loop_total = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_start)
    mesh.polygons.foreach_get("loop_total", loop_total)
    loop_vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertices)
    material_index = np.zeros(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", material_index)
    groups = mesh.attributes.get("max_smoothing_group")
    old_smoothing = np.zeros(len(mesh.polygons), dtype=np.int32)
    if groups is not None and groups.domain == 'FACE':
        groups.data.foreach_get("value", old_smoothing)
    polygon_of = {int(face): index for index, face in enumerate(source[1])}

    faces, face_poly, corner_loops, smoothing, kept = [], [], [], [], []
    for out_index, in_face in enumerate(edit["alive_faces"]):
        poly = polygon_of.get(int(in_face))
        if poly is None:
            continue
        start, total = int(loop_start[poly]), int(loop_total[poly])
        loops = range(start, start + total)
        if edit["faces"] is not None:
            face = edit["faces"][out_index]
        else:
            face = remap[loop_vertices[start:start + total]]
            if (face < 0).any():
                return False
        if len(set(face.tolist())) != len(face):
            continue
        corners = []
        for vertex in face:
            match = next((loop for loop in loops if remap[loop_vertices[loop]] == vertex), None)
            corners.append(start if match is None else match)
        faces.append(face)
        kept.append(out_index)
        face_poly.append(poly)
        corner_loops.extend(corners)
        smoothing.append(
            int(edit["smoothing"][out_index]) if edit["smoothing"] is not None else int(old_smoothing[poly])
        )

    shape = Mesh3d()
    shape.verts = positions[alive_vertices].astype(np.float32).ravel()
    new = draw_shape(mesh.name, shape, faces)
    corner_loops = np.asarray(corner_loops, dtype=np.int64)
    for layer in mesh.uv_layers:
        uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        layer.data.foreach_get("uv", uv)
        new_layer = new.uv_layers.new(name=layer.name)
        new_layer.data.foreach_set("uv", uv.reshape(-1, 2)[corner_loops].ravel())
    for material in mesh.materials:
        new.materials.append(material)
    new.update(calc_edges=True)
    if len(face_poly):
        new.polygons.foreach_set("material_index", material_index[np.asarray(face_poly)])
    apply_smoothing_groups(new, np.asarray(smoothing, dtype=np.uint32))
    new.update()
    face_sources[new.as_pointer()] = (len(edit["alive_faces"]), np.asarray(kept, dtype=np.int64))
    obj.data = new
    face_sources.pop(mesh.as_pointer(), None)
    bpy.data.meshes.remove(mesh)
    return True


def evaluate_stack(obj, stack, mod_apps, uv_enabled):
    """Evaluate supported modifiers bottom-up; return the stack indices used.

    Edit Mesh and UVW Map are applied only while the mesh still equals the
    stack's state at that point; after any modifier that is not evaluated,
    geometry-dependent modifiers stop. Unwrap UVW replaces the whole channel
    by face index, so it still applies whenever its faces match.
    """
    used = set()
    exact = len(mod_apps) == len(stack)
    for index in range(len(stack) - 1, -1, -1):
        modifier = stack[index]
        uid = get_guid(modifier) if modifier is not None else None
        mod_app = mod_apps[index] if len(mod_apps) == len(stack) else None
        if uid == EDIT_MESH_MODIFIER and exact and obj.type == 'MESH':
            source = face_sources.get(obj.data.as_pointer())
            edit = (
                get_edit_mesh(mod_app, len(obj.data.vertices), source[0])
                if source is not None
                else None
            )
            if edit is not None and apply_edit_mesh(obj, edit):
                used.add(index)
                continue
        elif uid == UVW_MAP_MODIFIER and exact:
            if not uv_enabled:
                continue
            mapping = get_uvw_mapping(modifier, mod_app)
            if mapping is not None:
                apply_uvw_mappings(obj, [mapping])
                used.add(index)
                continue
        elif uid in UNWRAP_MODIFIERS:
            if not uv_enabled:
                continue
            unwrap = get_unwrap_uvs(modifier)
            if unwrap is not None and apply_unwrap(obj, unwrap):
                used.add(index)
            continue
        exact = False
    return used


def apply_uvw_mappings(obj, mappings):
    """Write UVW Map results, bottom of the stack first, to map channel 1."""
    mesh = obj.data
    if obj.type != 'MESH' or not len(mesh.polygons):
        return
    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", positions)
    loop_vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertices)
    loop_start = np.empty(len(mesh.polygons), dtype=np.int32)
    loop_total = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_start)
    mesh.polygons.foreach_get("loop_total", loop_total)
    for mapping, transform in mappings:
        uvw = map_corners(mapping, transform, positions, loop_vertices, loop_start, loop_total)
        layer = mesh.uv_layers[0] if len(mesh.uv_layers) else mesh.uv_layers.new(name="UVMap")
        layer.data.foreach_set("uv", uvw[:, :2].astype(np.float32).ravel())


def create_shell(context, settings, node, shell, mat, mtx):
    refs = get_references(shell)
    created = []
    if refs:
        stack = refs[:-1]
        mod_apps = [child for child in shell.children if child.types == 0x2500]
        msh = refs[-1]
        created, uid = create_mesh(context, settings, node, msh, mat, mtx)
        # Modifiers are stored top of the stack first; evaluate_stack walks
        # them bottom-up and reports which ones it could reproduce.
        applied = None
        for obj in created:
            used = evaluate_stack(obj, stack, mod_apps, 'UV' in settings[1])
            applied = used if applied is None else applied & used
        applied = applied or set()
        modifiers = [
            ref for index, ref in enumerate(stack)
            if ref is not None and index not in applied
        ]
        if modifiers:
            labels = []
            for modifier in modifiers:
                uid = get_guid(modifier)
                if uid == SKIN_MODIFIER:
                    labels.append("Skin weights")
                elif (
                    uid == PHYSIQUE_MODIFIER
                    and get_super_id(modifier) == 0x810
                ):
                    labels.append("Physique weights")
                elif uid == EDIT_NORMALS_MODIFIER:
                    labels.append("Edit Normals")
                else:
                    labels.append(get_cls_name(modifier))
            print(
                "\tModifierWarning: importing base geometry without applying %s"
                % ", ".join(labels)
            )
    return created


def create_plane(context, settings, node, plane, mat, mtx):
    created = []
    name = node.get_first(0x0962)
    filename, obtypes, search, resolver = settings
    parablock = get_references(plane)[0]
    if len(parablock.children[1].data) > 11:
        values = parablock.children
        length = get_float(values[1].data, len(values[1].data)-4)[0]
        width = get_float(values[2].data, len(values[2].data)-4)[0]
    else:
        try:
            length = get_float(parablock.children[2].get_first(0x100).data)[0]
            width = get_float(parablock.children[3].get_first(0x100).data)[0]    
        except:
            length = UNPACK_BOX_DATA(parablock.children[1].data)[6]
            width = UNPACK_BOX_DATA(parablock.children[2].data)[6]
    bpy.ops.mesh.primitive_plane_add(size=1.0, scale=(width, length, 0.0))
    obj = context.selected_objects[0]
    if name is not None:
        obj.name = name.data
    if ('MATERIAL' in obtypes):
        adjust_material(resolver, obj, mat)
    adjust_matrix(obj, mtx)
    created.append(obj)
    return created


def create_box(context, settings, node, box, mat, mtx):
    created = []
    name = node.get_first(0x0962)
    filename, obtypes, search, resolver = settings
    parablock = get_references(box)
    if parablock:
        values = parablock[0].children
        if len(parablock[0].children[1].data) > 11:
            length = get_float(values[1].data, len(values[1].data)-4)[0]
            width = get_float(values[2].data, len(values[2].data)-4)[0]
            depth = get_float(values[3].data, len(values[3].data)-4)[0]
        else:
            try:
                length = get_float(values[2].get_first(0x100).data)[0]
                width = get_float(values[3].get_first(0x100).data)[0]
                depth = get_float(values[4].get_first(0x100).data)[0]
            except:
                length = UNPACK_BOX_DATA(values[1].data)[6]
                width  = UNPACK_BOX_DATA(values[2].data)[6]
                depth = UNPACK_BOX_DATA(values[3].data)[6]
        height = -depth if (depth < 0) else depth
        bpy.ops.mesh.primitive_cube_add(size=1.0, scale=(width, length, height))
        obj = context.selected_objects[0]
        if name is not None:
            obj.name = name.data
        if ('MATERIAL' in obtypes):
            adjust_material(resolver, obj, mat)
    else:
        obj = bpy.data.objects.new(name.data, None)
        context.view_layer.active_layer_collection.collection.objects.link(obj)
        obj.empty_display_type = 'CUBE'
    adjust_matrix(obj, mtx)
    created.append(obj)
    return created


def create_sphere(context, settings, node, sphere, mat, mtx):
    created = []
    name = node.get_first(0x0962)
    filename, obtypes, search, resolver = settings
    parablock = get_references(sphere)[0]
    if len(parablock.children[1].data) > 11:
        values = parablock.children[1]
        rd = get_float(values.data, len(values.data)-4)[0]
    else:
        value = parablock.children[2].get_first(0x100)
        if value is None:
           value = parablock.children[3].get_first(0x100) 
        try:
            rd = get_float(value.data)[0]
        except:
            rd = UNPACK_BOX_DATA(parablock.children[1].data)[6]
    bpy.ops.mesh.primitive_uv_sphere_add(radius=rd)
    obj = context.selected_objects[0]
    if name is not None:
        obj.name = name.data
    if ('MATERIAL' in obtypes):
        adjust_material(resolver, obj, mat)
    adjust_matrix(obj, mtx)
    created.append(obj)
    return created


def create_torus(context, settings, node, torus, mat, mtx):
    created = []
    name = node.get_first(0x0962)
    filename, obtypes, search, resolver = settings
    parablock = get_references(torus)[0]
    if len(parablock.children[1].data) > 11:
        values = parablock.children
        rd1 = get_float(values[1].data, len(values[1].data)-4)[0]
        rd2 = get_float(values[2].data, len(values[2].data)-4)[0]
    else:
        try:
            rd1 = get_float(parablock.children[2].get_first(0x100).data)[0]
            rd2 = get_float(parablock.children[3].get_first(0x100).data)[0]
        except:
            rd1 = UNPACK_BOX_DATA(parablock.children[1].data)[6]
            rd2 = UNPACK_BOX_DATA(parablock.children[2].data)[6]
    bpy.ops.mesh.primitive_torus_add(major_radius=rd1, minor_radius=rd2)
    obj = context.selected_objects[0]
    if name is not None:
        obj.name = name.data
    if ('MATERIAL' in obtypes):
        adjust_material(resolver, obj, mat)
    adjust_matrix(obj, mtx)
    created.append(obj)
    return created


def create_cylinder(context, settings, node, cylinder, mat, mtx):
    created = []
    name = node.get_first(0x0962)
    filename, obtypes, search, resolver = settings
    parablock = get_references(cylinder)
    if parablock:
        values = parablock[0].children
        if len(values[1].data) > 11:
            rd = get_float(values[1].data, len(values[1].data)-4)[0]
            hg = get_float(values[2].data, len(values[2].data)-4)[0]
        else:
            try:
                rd = get_float(values[2].get_first(0x100).data)[0]
                hg = get_float(values[3].get_first(0x100).data)[0]
            except:
                rd = UNPACK_BOX_DATA(values[1].data)[6]
                hg = UNPACK_BOX_DATA(values[2].data)[6]
        rad = -rd if (rd < 0) else rd
        height = -hg if (hg < 0) else hg
        bpy.ops.mesh.primitive_cylinder_add(radius=rad, depth=height)
        obj = context.selected_objects[0]
        if name is not None:
            obj.name = name.data
        if ('MATERIAL' in obtypes):
            adjust_material(resolver, obj, mat)
    else:
        obj = bpy.data.objects.new(name.data, None)
        context.view_layer.active_layer_collection.collection.objects.link(obj)
    adjust_matrix(obj, mtx)
    created.append(obj)
    return created


def create_cone(context, settings, node, cone, mat, mtx):
    created = []
    name = node.get_first(0x0962)
    filename, obtypes, search, resolver = settings
    parablock = get_references(cone)[0]
    if len(parablock.children[1].data) > 11:
        values = parablock.children
        rd1 = get_float(values[1].data, len(values[1].data)-4)[0]
        rd2 = get_float(values[2].data, len(values[2].data)-4)[0]
        hgt = get_float(values[3].data, len(values[3].data)-4)[0]
    else:
        try:
            rd1 = get_float(parablock.children[2].get_first(0x100).data)[0]
            rd2 = get_float(parablock.children[3].get_first(0x100).data)[0]
            hgt = get_float(parablock.children[4].get_first(0x100).data)[0] 
        except:
            rd1 = UNPACK_BOX_DATA(parablock.children[1].data)[6]
            rd2 = UNPACK_BOX_DATA(parablock.children[2].data)[6]
            hgt = UNPACK_BOX_DATA(parablock.children[3].data)[6]
    height = -hgt if (hgt < 0) else hgt
    bpy.ops.mesh.primitive_cone_add(radius1=rd1, radius2=rd2, depth=height)
    obj = context.selected_objects[0]
    if name is not None:
        obj.name = name.data
    if ('MATERIAL' in obtypes):
        adjust_material(resolver, obj, mat)
    adjust_matrix(obj, mtx)
    created.append(obj)
    return created


def create_skipable(context, node, skip):
    name = node.get_first(0x0962)
    if name is not None:
        name = name.data
        print("\tskipping %s '%s'... " % (skip, name))
    return []


def create_mesh(context, settings, node, msh, mat, mtx):
    created = []
    uid = get_guid(msh)
    if msh is not None:
        object_list.clear()
        if (uid in {POLY_MESH, EDIT_POLY}):
            created = create_editable_poly(context, settings, node, msh, mat)
        elif msh.get_first(0x08FE) and (uid in {0x10, 0x11, 0x12, 0x16, 0x19, EDIT_MESH}):
            created = create_editable_mesh(context, settings, node, msh, mat)
        elif (uid in {0x10, 0x14, 0x19} and 'PRIMITIVE' in settings[1]):
            created = create_box(context, settings, node, msh, mat, mtx)
        elif (uid == 0x011 and 'PRIMITIVE' in settings[1]):
            created = create_sphere(context, settings, node, msh, mat, mtx)
        elif (uid == 0x012 and 'PRIMITIVE' in settings[1]):
            created = create_cylinder(context, settings, node, msh, mat, mtx)
        elif (uid == 0x020 and 'PRIMITIVE' in settings[1]):
            created = create_torus(context, settings, node, msh, mat, mtx)
        elif (uid == CONE and 'PRIMITIVE' in settings[1]):
            created = create_cone(context, settings, node, msh, mat, mtx)
        elif (uid == PLANE and 'PRIMITIVE' in settings[1]):
            created = create_plane(context, settings, node, msh, mat, mtx)
        elif (uid in {0x2032, 0x2033}):
            created = create_shell(context, settings, node, msh, mat, mtx)
        elif (uid == DUMMY and 'EMPTY' in settings[1]):
            created = [create_dummy_object(context, node, uid)]
        elif (uid == BIPED_OBJ and 'ARMATURE' in settings[1]):
            created = [create_dummy_object(context, node, uid)]
        elif (uid == CAMERA and 'CAMERA' in settings[1]):
            created = [create_camera_object(context, node)]
        elif (uid == CAMERA_TARGET and 'CAMERA' in settings[1]):
            created = [create_dummy_object(context, node, uid)]
        elif (uid == OMNI_LIGHT and 'LIGHT' in settings[1]):
            created = [create_light_object(context, node, 'POINT')]
        elif (uid == DIRECT_LIGHT and 'LIGHT' in settings[1]):
            created = [create_light_object(context, node, 'SUN')]
        elif (uid == BOOLEAN):
            boolrefs = get_references(get_references(msh)[0])
            for ref in boolrefs:
                created, uid = create_mesh(context, settings, node, ref, mat, mtx)  
        else:
            skip = SKIPPABLE.get(uid)
            if (skip is not None):
                created = create_skipable(context, node, skip)
    return created, uid


def create_object(context, settings, node, transform):
    parent = get_node_parent(node)
    nodename = get_node_name(node)
    parentname = get_node_name(parent)
    prs, msh, mat, lyr = get_matrix_mesh_material(node)
    created, uid = create_mesh(context, settings, node, msh, mat, prs)
    created = [idx for ob, idx in enumerate(created) if idx not in created[:ob]]
    for obj in created:
        if obj.name != nodename:
            parent_dict[obj.name] = parentname
        if (transform and obj.type == 'MESH'):
            nodeloca = node.get_first(0x96A)
            noderota = node.get_first(0x96B)
            nodesize = node.get_first(0x96C)
            quats = noderota.data if noderota else (0.0, 0.0, 0.0, 1.0)
            pivot = mathutils.Vector(nodeloca.data if nodeloca else (0.0, 0.0, 0.0))
            angle = max_quaternion(quats)
            scale = max_scale_matrix(
                nodesize.data if nodesize else (1.0, 1.0, 1.0)
            )
            p_mtx = (
                mathutils.Matrix.Translation(pivot)
                @ angle.to_matrix().to_4x4()
                @ scale
            )
            obj.data.transform(p_mtx)
    matrix_dict[nodename] = create_matrix(prs)
    parent_dict[nodename] = parentname
    if get_guid(prs) == 0x2006:
        refs = get_references(prs)
        if refs and refs[0] is not None:
            target_dict[nodename] = get_node_name(refs[0])
    return nodename, created


def import_animations(context, settings, imported_nodes, animation_options):
    if not animation_options["enabled"]:
        return None

    action_builder = ActionBuilder("%s|MAX" % Path(settings[0]).stem)
    unsupported = set()
    for node, nodename, objects in imported_nodes:
        prs = get_matrix_mesh_material(node)[0]
        uid = get_guid(prs)
        if uid in {0x9154, 0x9156, 0x3011}:
            unsupported.add(get_cls_name(prs))
            continue
        try:
            tracks = extract_transform_tracks(prs, get_guid, get_references)
        except ValueError as exc:
            print("\tAnimationError: %s '%s'" % (exc, nodename))
            continue
        if not tracks.animated:
            continue

        source_anchor = create_matrix(prs)
        for obj in objects:
            samples = sample_transform(
                tracks,
                obj.matrix_basis.copy(),
                source_anchor,
                animation_options["fps"],
                animation_options["frame_offset"],
                animation_options["sampling"],
            )
            if samples is not None:
                action_builder.add_object(obj, samples)

    action = action_builder.finish()
    scene = context.scene
    fps = animation_options["fps"]
    scene.render.fps = round(fps)
    scene.render.fps_base = scene.render.fps / fps
    if action is not None:
        action["source_file"] = settings[0]
        action["source_fps"] = fps
        print(
            "\tAnimation: %d objects, frames %g-%g"
            % (
                action_builder.animated_objects,
                action_builder.frame_start,
                action_builder.frame_end,
            )
        )

    ticks_per_frame = TICKS_PER_SECOND / fps
    start_tick = animation_options.get("start_tick")
    end_tick = animation_options.get("end_tick")
    if start_tick is not None and end_tick is not None:
        scene.frame_start = math.floor(
            start_tick / ticks_per_frame + animation_options["frame_offset"]
        )
        scene.frame_end = math.ceil(
            end_tick / ticks_per_frame + animation_options["frame_offset"]
        )
    elif action is not None:
        scene.frame_start = math.floor(action_builder.frame_start)
        scene.frame_end = math.ceil(action_builder.frame_end)
    if action is not None or (start_tick is not None and end_tick is not None):
        scene.frame_set(scene.frame_start)
    if unsupported:
        print(
            "\tAnimationWarning: unsupported controllers: %s"
            % ", ".join(sorted(unsupported))
        )
    return action


def make_scene(context, settings, mscale, transform, parent, animation_options):
    imported = []
    imported_nodes = []
    for chunk in parent.children:
        if (
            isinstance(chunk, SceneChunk)
            and get_guid(chunk) == 0x1
            and get_super_id(chunk) == 0x1
        ) or get_node_parent(chunk):
            try:
                nodename, created = create_object(context, settings, chunk, transform)
                imported.append((nodename, created))
                imported_nodes.append((chunk, nodename, created))
            except Exception as exc:
                print("\tImportError: %s %s" % (exc, chunk), get_node_name(chunk))

    # Apply matrix and assign parents to objects
    objects = dict(imported)
    for idx, objs in objects.items():
        pt_name = parent_dict.get(idx)
        parents = objects.get(pt_name)
        obj_mtx = matrix_dict.get(idx, mathutils.Matrix())
        prt_mtx = matrix_dict.get(pt_name, mathutils.Matrix())
        for obj in objs:
            if parents:
                try:
                    obj.parent = parents[0]
                except TypeError as te:
                    print("\tTypeError: %s '%s'" % (te, pt_name))
            if obj_mtx:
                if obj.parent and obj.parent.empty_display_type != 'SINGLE_ARROW':
                    trans_mtx = obj.parent.matrix_world @ obj_mtx
                else:
                    trans_mtx = prt_mtx @ obj_mtx
                if transform:
                    obj.matrix_world = trans_mtx
                obj.matrix_world = mscale @ obj.matrix_world

    for source_name, target_name in target_dict.items():
        sources = objects.get(source_name, ())
        targets = objects.get(target_name, ())
        if not targets:
            continue
        for obj in sources:
            if obj.type not in {'CAMERA', 'LIGHT'}:
                continue
            constraint = obj.constraints.new('TRACK_TO')
            constraint.name = "MAX Look At"
            constraint.target = targets[0]
            constraint.track_axis = 'TRACK_NEGATIVE_Z'
            constraint.up_axis = 'UP_Y'
    import_animations(context, settings, imported_nodes, animation_options)


def read_scene(context, maxfile, settings, mscale, transform, animation_options):
    global SCENE_LIST, META_DATA
    metasid = min(max(entry.sid for entry in maxfile.direntries if entry is not None), 11)
    SCENE_LIST = read_chunks(maxfile, 'Scene', SceneChunk)
    META_DATA = read_chunks(maxfile, maxfile.direntries[metasid].name, superId=metasid) if metasid >= 0xA else []
    make_scene(context, settings, mscale, transform, SCENE_LIST[0], animation_options)
    # For debug: Print directory
    # print('Directory', maxfile.direntries[0].kids_dict.keys())


def read(context, filename, mscale, obtypes, search, transform, texture_directory, animation_options,
         prefer_source_textures=False):
    if not is_maxfile(filename):
        raise ValueError("File is not an Autodesk 3ds Max OLE scene: %s" % filename)

    resolver = TextureResolver(
        filename,
        texture_directory,
        search,
        prefer_source_directory=prefer_source_textures,
    )
    settings = filename, obtypes, search, resolver
    with ImportMaxFile(filename) as maxfile:
        read_class_data(maxfile, filename)
        read_config(maxfile, filename)
        file_animation_options = animation_options.copy()
        timing = get_scene_timing()
        if file_animation_options["use_scene_fps"] and timing["fps"] is not None:
            file_animation_options["fps"] = float(timing["fps"])
        file_animation_options["start_tick"] = timing["start_tick"]
        file_animation_options["end_tick"] = timing["end_tick"]
        read_directory(maxfile, filename)
        read_class_directory(maxfile, filename)
        read_video_postqueue(maxfile, filename)
        read_scene(
            context,
            maxfile,
            settings,
            mscale,
            transform,
            file_animation_options,
        )


def load(operator, context, files=None, directory="", filepath="", scale_objects=1.0, use_collection=False,
         use_image_search=True, texture_directory="", object_filter=None, use_apply_matrix=True,
         use_animation=True, use_scene_fps=True, animation_fps=30.0,
         animation_sampling='BAKE', frame_offset=0.0, global_matrix=None,
         prefer_source_textures=False):

    parent_dict.clear()
    matrix_dict.clear()
    target_dict.clear()
    face_sources.clear()

    if context.window:
        context.window.cursor_set('WAIT')
    mscale = mathutils.Matrix.Scale(scale_objects, 4)
    if global_matrix is not None:
        mscale = global_matrix @ mscale

    if not object_filter:
        object_filter = {
            'MATERIAL',
            'UV',
            'PRIMITIVE',
            'EMPTY',
            'ARMATURE',
            'CAMERA',
            'LIGHT',
        }

    default_layer = context.view_layer.active_layer_collection.collection
    animation_options = {
        "enabled": use_animation,
        "use_scene_fps": use_scene_fps,
        "fps": animation_fps,
        "sampling": animation_sampling,
        "frame_offset": frame_offset,
    }

    if not files:
        files = [Path(filepath)]
        directory = Path(filepath).parent

    try:
        for fl in files:
            if use_collection:
                collection = bpy.data.collections.new(Path(fl.name).stem)
                context.scene.collection.children.link(collection)
                context.view_layer.active_layer_collection = (
                    context.view_layer.layer_collection.children[collection.name]
                )
            read(
                context,
                os.path.join(directory, fl.name),
                mscale,
                obtypes=object_filter,
                search=use_image_search,
                transform=use_apply_matrix,
                texture_directory=texture_directory,
                animation_options=animation_options,
                prefer_source_textures=prefer_source_textures,
            )
        return {'FINISHED'}
    except Exception as exc:
        traceback.print_exc()
        operator.report({'ERROR'}, "MAX import failed: %s" % exc)
        return {'CANCELLED'}
    finally:
        parent_dict.clear()
        matrix_dict.clear()
        target_dict.clear()
        face_sources.clear()
        clear_texture_cache()
        active = context.view_layer.layer_collection.children.get(default_layer.name)
        if active is not None:
            context.view_layer.active_layer_collection = active
        if context.window:
            context.window.cursor_set('DEFAULT')
