"""Find code reached only through data: entries the catalog misses.

Bullfrog's hand-written renderer (cseg) picks its span fillers from tables of
code pointers and jumps through them (`jmp [0xFBE044]`); nothing calls those
fillers, so no disassembler pass names them, and an unresolved jump returns
without the filler's epilogue, leaving the caller's registers wrong.

Seeds written: every code pointer in cseg's own tables, and from the other
data sections only pointers that look like a function start (16-byte aligned
behind ret/int3/nop padding), since a dword there is often just a number.

    .venv/bin/python -I tools/gen/data_code_ptrs.py game/TP.EXE work/tp/functions.json work/tp/run_seeds.json
"""
import json
import struct
import sys

import pefile


def main(exe, catalog, out):
    known = {f['address'] for f in json.load(open(catalog))['functions']}
    pe = pefile.PE(exe)
    img = pe.get_memory_mapped_image()
    base = pe.OPTIONAL_HEADER.ImageBase
    code = [(base + s.VirtualAddress, base + s.VirtualAddress + s.Misc_VirtualSize)
            for s in pe.sections if s.Characteristics & 0x20000000]
    lo, hi = min(a for a, _ in code), max(b for _, b in code)
    seeds = {}
    for s in pe.sections:
        name = s.Name.rstrip(b'\0').decode()
        if name == '.text':
            continue                       # jump tables inside functions: the lifter's own
        start = s.VirtualAddress
        for off in range(start, start + s.Misc_VirtualSize - 3, 4):
            v = struct.unpack_from('<I', img, off)[0]
            if not lo <= v < hi or v in known:
                continue
            if name != 'cseg':
                pad = img[v - base - 1]
                if v % 16 or pad not in (0xC3, 0xCC, 0x90):
                    continue
            seeds.setdefault(v, base + off)
    json.dump([{'address': a, 'from': f} for a, f in sorted(seeds.items())], open(out, 'w'), indent=0)
    print(f'{len(seeds)} code pointers in data -> {out}')


if __name__ == '__main__':
    main(*sys.argv[1:4])
