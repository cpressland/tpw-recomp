"""Copy the files out of a Microsoft cabinet, or one inside an installer.

The 2.0 patch's installer (an InstallShield self-extractor) is a small
program with a cabinet after it: stored or MSZIP folders, one cabinet, no
spanning. MSZIP is deflate in blocks of up to 32 KB, each one starting
`CK` and using the blocks before it as its dictionary.

    python3 -I tools/disc/cab.py TPPatchTwoEUROAMER20000324a.exe work/payload
"""
import os
import struct
import sys
import zlib

STORED, MSZIP = 0, 1
RESERVE_PRESENT = 4


def find(blob):
    """Where the cabinet starts: its signature, with a size that fits."""
    at = 0
    while True:
        at = blob.find(b'MSCF\0\0\0\0', at)
        if at < 0:
            raise SystemExit('no cabinet found')
        size = struct.unpack_from('<I', blob, at + 8)[0]
        if at + size <= len(blob) and blob[at + 24:at + 26] == b'\x03\x01':   # version 1.3
            return at
        at += 1


def _cstr(b, o):
    end = b.index(b'\0', o)
    return b[o:end], end + 1


def _folder_data(cab, start, blocks, kind, reserve):
    if kind & 0xF not in (STORED, MSZIP):
        raise SystemExit(f'cabinet folder compression {kind:#x} is not stored or MSZIP')
    out, o = bytearray(), start
    for _ in range(blocks):
        _csum, size, _plain = struct.unpack_from('<IHH', cab, o)
        o += 8 + reserve
        data = cab[o:o + size]
        o += size
        if kind & 0xF == STORED:
            out += data
            continue
        if data[:2] != b'CK':
            raise SystemExit('an MSZIP block without its CK')
        d = zlib.decompressobj(-15, zdict=bytes(out[-32768:])) if out else zlib.decompressobj(-15)
        out += d.decompress(data[2:]) + d.flush()
    return bytes(out)


def files(blob):
    """Yield (name, data) for each file, names with '/'."""
    cab = blob[find(blob):]
    (_sig, _r1, _size, _r2, coff_files, _r3, _minor, _major,
     n_folders, n_files, flags, _set, _index) = struct.unpack_from('<4sIIIIIBBHHHHH', cab, 0)
    o = 36
    hdr_res = fold_res = data_res = 0
    if flags & RESERVE_PRESENT:
        hdr_res, fold_res, data_res = struct.unpack_from('<HBB', cab, o)
        o += 4 + hdr_res
    if flags & 3:
        raise SystemExit('a cabinet spanning several files')
    folders = []
    for _ in range(n_folders):
        start, blocks, kind = struct.unpack_from('<IHH', cab, o)
        folders.append((start, blocks, kind))
        o += 8 + fold_res
    unpacked = {}
    o = coff_files
    for _ in range(n_files):
        size, offset, folder, _date, _time, attrs = struct.unpack_from('<IIHHHH', cab, o)
        name, o = _cstr(cab, o + 16)
        name = name.decode('utf-8' if attrs & 0x80 else 'latin-1').replace('\\', '/').lstrip('/')
        if folder >= 0xFFFD:
            raise SystemExit(f'{name}: continued from another cabinet')
        if folder not in unpacked:
            unpacked[folder] = _folder_data(cab, *folders[folder], data_res)
        yield name, unpacked[folder][offset:offset + size]


def extract(path, out):
    with open(path, 'rb') as f:
        blob = f.read()
    n = 0
    for name, data in files(blob):
        dest = os.path.join(out, *name.split('/'))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as o:
            o.write(data)
        n += 1
    return n


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(f'{extract(sys.argv[1], sys.argv[2])} files')
