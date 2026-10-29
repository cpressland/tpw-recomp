"""List the files of an ISO 9660 image with their extents (LBA, length)."""
import struct
import sys


def walk(f, lba, size, path=''):
    f.seek(lba * 2048)
    data = f.read(size)
    i = 0
    while i < len(data):
        n = data[i]
        if n == 0:
            i = (i // 2048 + 1) * 2048
            continue
        ext, ln = struct.unpack_from('<I', data, i + 2)[0], struct.unpack_from('<I', data, i + 10)[0]
        flags, nl = data[i + 25], data[i + 32]
        name = data[i + 33:i + 33 + nl]
        if name not in (b'\0', b'\1'):
            nm = name.decode('latin-1').split(';')[0]
            if flags & 2:
                yield from walk(f, ext, ln, path + nm + '/')
            else:
                yield ext, ln, path + nm
        i += n


def files(iso):
    with open(iso, 'rb') as f:
        f.seek(16 * 2048 + 156)
        root = f.read(34)
        lba, size = struct.unpack_from('<I', root, 2)[0], struct.unpack_from('<I', root, 10)[0]
        return sorted(walk(f, lba, size))


if __name__ == '__main__':
    for ext, ln, p in files(sys.argv[1]):
        print(f'{ext:8} {ln:10} {p}')
