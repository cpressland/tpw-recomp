"""Unwrap SafeDisc 1.41 from Theme Park World 2.0: write TP.ICD out decrypted.

The retail loader (TP.EXE) is run headlessly under the emulator against the
disc image. It passes its disc check and starts its child, in which
dplayerx.dll decrypts the game (TP.ICD) in place and jumps to its entry
point. We stop there, before any game code runs, and write that image back
out as a plain executable.

Every section is taken as the child holds it, decrypted. What is left is
SafeDisc's hold on the KERNEL32 and USER32 imports:

- Their address tables hold stubs, not APIs. A stub calls into dplayerx,
  which picks the API from the call's return address: a call site names
  some slot of the right DLL, not the right slot. Run from an address that
  is no call site, a stub falls back to its own slot, which gives each
  slot's API; run from each call site, it gives the API that site wants,
  and the site is pointed at that API's slot.
- Their name tables are encrypted (one XOR key), their names too, and the
  first entry of each is zeroed and only handed over by the loader at run
  time (the MDJ mapping). The key is the one that puts every entry where
  the hint/name entries tile the import area, holes the size of the names;
  the names are written back into those holes in plain text.
- Every address table is unbound again (the same as its name table).

    .venv/bin/python -I tools/safedisc/unwrap.py --cd work/v20 --iso TPWORLD.ISO -o work/unwrap/TP.EXE
"""
import argparse
import os
import struct
import sys
from collections import defaultdict

import pefile

sys.path.insert(0, os.path.dirname(__file__))
import sdemu  # noqa: E402
from unicorn.x86_const import UC_X86_REG_ESP  # noqa: E402

THUNKS_END = sdemu.THUNKS + 0x100000


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def run_until_game(cd, iso, work, verbose=False):
    """Run the loader and its children; return the child stopped at the ICD's entry."""
    emu = sdemu.Emu(cd, iso, verbose)
    emu.shared['child_image'] = 'TP.ICD'
    emu.shared['stop_at_entry'] = True
    emu.os.out_dir = work
    mod = emu.load_pe(os.path.join(cd, 'TP.EXE'), main=True)
    emu.os.main_module = mod
    queue = [(emu, mod['base'] + mod['pe'].OPTIONAL_HEADER.AddressOfEntryPoint)]
    while queue:
        proc, start = queue.pop(0)
        print(f'=== running {proc.name} (pid {proc.pid:#x}) from {start:#x}', flush=True)
        try:
            proc.run(start)
        except SystemExit as ex:
            print(f'=== {proc.name} stopped: {ex}')
        if proc.stop_reason and proc.stop_reason.startswith('entry '):
            return proc
        for c in proc.os.children:
            if c.resumed and not c.started:
                c.started = True
                queue.append((c.emu, c.prepare()))
    raise SystemExit('the game was never reached')


def resolve_stub(emu, stub, site):
    """Run one SafeDisc import stub as if called from `site`; return its API name."""
    esp = emu.shared.setdefault('scratch_esp', emu.reg(UC_X86_REG_ESP) - 0x4000)
    emu.w32(esp, site)
    emu.setreg(UC_X86_REG_ESP, esp)
    emu.trap, emu.trapped = site, None
    emu.stop_reason = None
    try:
        emu.run(stub)
    except SystemExit as ex:
        raise SystemExit(f'stub {stub:#x} from {site:#x}: {ex}')
    finally:
        emu.trap = None
    if emu.trapped is None:
        raise SystemExit(f'stub {stub:#x} from {site:#x} did not reach an API')
    return emu.trapped[1]


def hidden_entries(emu):
    """The encrypted name-table entries the loader hands its child (MDJ mapping)."""
    for name, (owner, m) in emu.shared['named'].items():
        if name.startswith('MDJ'):
            data = owner.read(m.buf, m.size)
            count = u32(data, 0x24)
            return [u32(data, 0x28 + 8 * i) for i in range(count)]
    return []


def entry_size(name):
    return (2 + len(name) + 1 + 1) & ~1         # hint, name, NUL, word-aligned


def name_key(encrypted, names, starts, lo, hi):
    """The XOR key for the encrypted name-table entries, and the holes it gives.

    With the right key every entry lands in [lo, hi), and together with the
    plain hint/name entries and DLL names (`starts`) they tile the import
    area: the holes it leaves are exactly the sizes of `names`.
    """
    want = sorted(entry_size(n) for n in names)
    found = []
    for r in range(lo, hi, 2):
        k = encrypted[0] ^ r
        at = [e ^ k for e in encrypted]
        if not all(lo <= x < hi for x in at) or len(set(at)) != len(at):
            continue
        edges = sorted(set(at) | starts)
        nxt = {x: y for x, y in zip(edges, edges[1:])}
        holes = sorted(nxt.get(x, x + 4096) - x for x in at)
        # the last entry of the area has no next start: let it be as big as needed
        if holes[:-1] == sorted(want)[:-1] or holes == want:
            found.append((k, at, nxt))
    if len(found) != 1:
        raise SystemExit(f'name-table key: {len(found)} candidates')
    return found[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--cd', required=True, help='the 2.0-patched disc files (TP.EXE, TP.ICD, ...)')
    ap.add_argument('--iso', required=True, help='the disc image, for raw sector reads')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--work', default='work/unwrap', help="where the emulated processes' logs go")
    ap.add_argument('--compare', help='an executable to compare the result with, section by section')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()

    os.makedirs(a.work, exist_ok=True)
    emu = run_until_game(a.cd, a.iso, a.work, a.verbose)
    icd = pefile.PE(os.path.join(a.cd, 'TP.ICD'))
    base = icd.OPTIONAL_HEADER.ImageBase
    entry = base + icd.OPTIONAL_HEADER.AddressOfEntryPoint
    mem = bytearray(emu.read(base, icd.OPTIONAL_HEADER.SizeOfImage))
    filed = icd.get_memory_mapped_image()

    # The import descriptors as the file has them; dplayerx zeroes some in memory.
    imp = icd.OPTIONAL_HEADER.DATA_DIRECTORY[1].VirtualAddress
    dlls = []           # (descriptor rva, name-table rva, dll name rva, address-table rva, hidden)
    d = imp
    while u32(filed, d + 16):
        oft, _, _, name_rva, ft = struct.unpack_from('<5I', filed, d)
        dlls.append((d, oft, name_rva, ft, u32(mem, d) == 0))
        d += 20

    # Each hidden slot's API: its stub run from an address that is no call site.
    slot_api = {}       # address-table rva -> (dll index, API name)
    for i, (_, oft, name_rva, ft, hidden) in enumerate(dlls):
        if not hidden:
            continue
        j = 0
        while u32(mem, ft + 4 * j):
            v = u32(mem, ft + 4 * j)
            name = emu.thunks[v][1] if sdemu.THUNKS <= v < THUNKS_END else resolve_stub(emu, v, entry)
            slot_api[ft + 4 * j] = (i, name)
            j += 1
        dll = filed[name_rva:filed.index(b'\0', name_rva)].decode()
        print(f'{dll}: {j} imports behind SafeDisc stubs', flush=True)

    # Each call site through a hidden slot: point it at its own API's slot.
    by_api = {api: slot for slot, api in slot_api.items()}
    stubs = {slot: u32(mem, slot) for slot in slot_api}
    moved = 0
    for s in icd.sections:
        if not s.Characteristics & 0x20000000:          # executable
            continue
        lo, hi = s.VirtualAddress, s.VirtualAddress + s.Misc_VirtualSize
        i = mem.find(b'\xff', lo, hi)
        while i >= 0:
            if mem[i + 1] in (0x15, 0x25):              # call / jmp dword ptr [slot]
                slot = u32(mem, i + 2) - base
                if slot in slot_api:
                    want = by_api[(slot_api[slot][0], resolve_stub(emu, stubs[slot], base + i + 6))]
                    if want != slot:
                        struct.pack_into('<I', mem, i + 2, base + want)
                        moved += 1
            i = mem.find(b'\xff', i + 1, hi)
    print(f'{moved} call sites pointed at their own slot', flush=True)

    # The hidden name tables: decrypt the entries, write the names into their holes.
    rdata = next(s for s in icd.sections if s.VirtualAddress <= imp < s.VirtualAddress + s.Misc_VirtualSize)
    extra = hidden_entries(emu)
    encrypted, names, rows = [], [], []
    starts = set()
    for i, (_, oft, name_rva, ft, hidden) in enumerate(dlls):
        starts.add(name_rva)
        j = 0
        while u32(mem, ft + 4 * j) or u32(filed, oft + 4 * j):
            e = u32(filed, oft + 4 * j)
            if hidden:
                e = e or extra.pop(0)
                encrypted.append(e)
                names.append(slot_api[ft + 4 * j][1])
                rows.append((oft + 4 * j, ft + 4 * j, slot_api[ft + 4 * j][1]))
            elif not e & 0x80000000:
                starts.add(e)
            j += 1
    key, holes, nxt = name_key(encrypted, names, starts, rdata.VirtualAddress,
                               rdata.VirtualAddress + rdata.Misc_VirtualSize)
    print(f'name-table key {key:#010x}')
    free = defaultdict(list)                         # hole size -> holes
    for x in sorted(holes):
        free[nxt.get(x, x + 4096) - x].append(x)
    largest = max(holes)
    for int_slot, iat_slot, name in rows:
        size = entry_size(name)
        rva = free[size].pop(0) if free[size] else largest
        hint_name = b'\0\0' + name.encode() + b'\0'
        mem[rva:rva + size] = hint_name.ljust(size, b'\0')
        struct.pack_into('<I', mem, int_slot, rva)

    # Every descriptor as on disk: name table back, address table unbound.
    for desc, oft, name_rva, ft, _ in dlls:
        struct.pack_into('<5I', mem, desc, oft, 0, 0, name_rva, ft)
        j = 0
        while u32(mem, oft + 4 * j):
            struct.pack_into('<I', mem, ft + 4 * j, u32(mem, oft + 4 * j))
            j += 1

    # Back to the file layout: the ICD's own headers, every section from memory.
    out = bytearray(icd.__data__)
    for s in icd.sections:
        out[s.PointerToRawData:s.PointerToRawData + s.SizeOfRawData] = \
            mem[s.VirtualAddress:s.VirtualAddress + s.SizeOfRawData]
    with open(a.out, 'wb') as f:
        f.write(out)
    print(f'wrote {a.out} ({len(out)} bytes)')

    if a.compare:
        ref = pefile.PE(a.compare).get_memory_mapped_image()
        for s in icd.sections:
            lo, n = s.VirtualAddress, s.Misc_VirtualSize
            diff = sum(1 for i in range(lo, lo + n) if mem[i] != ref[i])
            print(f'  {s.Name.rstrip(b"\0").decode():9} {diff:7} bytes differ of {n:#x}')
        same = sum(1 for _, iat_slot, name in rows
                   if ref[u32(ref, iat_slot) + 2:].split(b'\0')[0] == name.encode())
        print(f'  hidden imports named as in the reference: {same}/{len(rows)}')


if __name__ == '__main__':
    main()
