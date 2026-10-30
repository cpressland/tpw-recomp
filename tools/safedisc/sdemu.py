"""Run the SafeDisc 1.x loader (tp.exe) headlessly under Unicorn.

The loader authenticates the disc, decrypts TP.ICD and writes it into a
suspended child process. Here every Win32 call the loader makes is answered
in Python: files come from the extracted disc, the "CD drive" is backed by
the ISO, and the child process is a sparse address space we can dump.

    .venv/bin/python -I tools/safedisc/sdemu.py --cd work/cd --iso TPWORLD.ISO
"""
import argparse
import os
import struct
import sys
import time

import pefile
from unicorn import (Uc, UcError, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE,
                     UC_HOOK_INTR, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_WRITE, UC_PROT_ALL)
from unicorn.x86_const import *  # noqa: F401,F403

sys.path.insert(0, os.path.dirname(__file__))
import winapi  # noqa: E402
import weak  # noqa: E402

PAGE = 0x1000
THUNKS = 0x7E000000          # one byte per fake API entry point
SPECIAL = 0x7E0FF000          # internal return trampolines (SEH, callbacks)
HEAP, HEAP_END = 0x20000000, 0x40000000
STACK, STACK_SIZE = 0x00100000, 0x00200000
TEB, PEB = 0x7FFDE000, 0x7FFDF000
GDT = 0x7FFD0000
KUSER = 0x7FFE0000


def align(v, a=PAGE):
    return (v + a - 1) & ~(a - 1)


class Emu:
    def __init__(self, cd, iso, verbose=False, shared=None, pid=0x1000, name='tp'):
        # State every emulated process sees the same: API thunk addresses (so a
        # pointer handed to a child still means the same function), system
        # module bases, named kernel objects.
        self.shared = shared if shared is not None else dict(
            thunks={}, thunk_by_name={}, fake_mods={}, named={}, next_pid=pid + 0x100,
            first_pid=pid)
        self.pid = pid
        self.max_api = int(os.environ.get('SDEMU_MAX_API', '0'))
        self.name = name
        self.cd = os.path.abspath(cd)
        self.iso = iso
        self.verbose = verbose
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.thunks = self.shared['thunks']                # addr -> (dll, name)
        self.thunk_by_name = self.shared['thunk_by_name']
        self.modules = {}           # hmodule -> dict(name, path, pe, base, size)
        self.mapped = []            # (start, end)
        self.heap = HEAP
        self.trace = []
        self.api_count = 0
        self.hist = {}              # (api, return address) -> calls
        self.trap = None            # return address whose API call is caught, not run
        self.trapped = None
        self.progress = int(os.environ.get('SDEMU_PROGRESS', '0'))
        self.t0 = time.time()
        self.sampled = False
        self.faults = {}            # eip -> access violations raised there
        self.ms_per_call = int(os.environ.get('SDEMU_MS_PER_CALL', '1'))
        for x in filter(None, os.environ.get('SDEMU_POKE', '').split(',')):
            at, _, rest = x.partition('@')   # experiments: dword=value when EIP reaches at
            addr, val = (int(v, 16) for v in rest.split('='))
            self.uc.hook_add(UC_HOOK_CODE, lambda uc, a_, s_, d: (
                print(f'  !! {self.name} poke [{d[0]:08x}]={d[1]:08x}', flush=True),
                uc.mem_write(d[0], struct.pack('<I', d[1]))), (addr, val),
                begin=int(at, 16), end=int(at, 16))
        for x in filter(None, os.environ.get('SDEMU_REGS', '').split(',')):
            a = int(x, 16)       # print registers and the frame's arguments here
            self.uc.hook_add(UC_HOOK_CODE, self._regs_hook, begin=a, end=a)
        for x in filter(None, os.environ.get('SDEMU_WATCH', '').split(',')):
            a = int(x, 16)       # report every write that touches this dword
            self.uc.hook_add(UC_HOOK_MEM_WRITE, self._watch_hook, begin=a - 3, end=a + 3)
        sample = float(os.environ.get('SDEMU_SAMPLE', '0'))
        if sample:
            import threading

            def sampler():
                while True:
                    time.sleep(sample)
                    if getattr(self, '_run_stack', None):
                        self.sampled = True
                        self.uc.emu_stop()
            threading.Thread(target=sampler, daemon=True).start()
        self.hits = {}              # SDEMU_COUNT addresses -> times executed
        for x in filter(None, os.environ.get('SDEMU_COUNT', '').split(',')):
            a = int(x, 16)
            self.hits[a] = 0
            self.uc.hook_add(UC_HOOK_CODE, self._count_hook, begin=a, end=a)
        self.stop_reason = None
        self.pending = None         # set by an API that redirects EIP
        self.seh_state = None
        self.map_fixed()
        self.os = winapi.WinState(self)
        if 'trace' in self.shared:
            lo, hi, out = self.shared['trace']
            self.start_trace(lo, hi, out.replace('.txt', f'_{name}.txt'))

    # ------------------------------------------------------------ memory
    def mmap(self, addr, size, data=b''):
        addr &= ~(PAGE - 1)
        size = align(size)
        for s, e in self.mapped:
            if addr < e and addr + size > s:
                # Map only the holes.
                cur = addr
                for s2, e2 in sorted(self.mapped):
                    if e2 <= cur or s2 >= addr + size:
                        continue
                    if s2 > cur:
                        self.uc.mem_map(cur, s2 - cur, UC_PROT_ALL)
                        self.mapped.append((cur, s2))
                    cur = max(cur, e2)
                if cur < addr + size:
                    self.uc.mem_map(cur, addr + size - cur, UC_PROT_ALL)
                    self.mapped.append((cur, addr + size))
                break
        else:
            self.uc.mem_map(addr, size, UC_PROT_ALL)
            self.mapped.append((addr, addr + size))
        if data:
            self.uc.mem_write(addr, data)

    def is_mapped(self, addr, size=1):
        return any(s <= addr and addr + size <= e for s, e in self.mapped)

    def alloc(self, size, align_to=16):
        a = (self.heap + align_to - 1) & ~(align_to - 1)
        end = a + size
        if end > HEAP_END:
            raise MemoryError('heap exhausted')
        self.heap = end
        return a

    def r8(self, a):
        return self.uc.mem_read(a, 1)[0]

    def r16(self, a):
        return struct.unpack('<H', self.uc.mem_read(a, 2))[0]

    def r32(self, a):
        return struct.unpack('<I', self.uc.mem_read(a, 4))[0]

    def w32(self, a, v):
        self.uc.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))

    def w16(self, a, v):
        self.uc.mem_write(a, struct.pack('<H', v & 0xFFFF))

    def read(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def write(self, a, b):
        self.uc.mem_write(a, bytes(b))

    def cstr(self, a, limit=4096):
        if not a:
            return None
        out = bytearray()
        while len(out) < limit:
            b = self.r8(a + len(out))
            if not b:
                break
            out.append(b)
        return out.decode('latin-1')

    def wstr(self, a, limit=4096):
        if not a:
            return None
        out = []
        while len(out) < limit:
            c = self.r16(a + 2 * len(out))
            if not c:
                break
            out.append(chr(c))
        return ''.join(out)

    def put_cstr(self, a, s, cap=None):
        b = s.encode('latin-1')
        if cap is not None:
            b = b[:max(cap - 1, 0)]
        self.write(a, b + b'\0')
        return len(b)

    def reg(self, r):
        return self.uc.reg_read(r)

    def setreg(self, r, v):
        self.uc.reg_write(r, v & 0xFFFFFFFF)

    # ------------------------------------------------------------ setup
    def map_fixed(self):
        uc = self.uc
        self.mmap(THUNKS, 0x100000)
        self.mmap(STACK, STACK_SIZE)
        self.mmap(TEB, 2 * PAGE)
        self.mmap(GDT, PAGE)
        self.mmap(KUSER, PAGE)
        self.mmap(HEAP, 0x1000000)       # grows on demand, see alloc hook
        self.heap_mapped = HEAP + 0x1000000
        # All thunks are `ret` so an emu that misses the hook still returns.
        uc.mem_write(THUNKS, b'\xC3' * 0x100000)
        stack_top = STACK + STACK_SIZE
        teb = struct.pack('<7I', 0xFFFFFFFF, stack_top, STACK, 0, 0x1E00, 0, TEB)
        teb += b'\0' * (0x20 - len(teb))
        teb += struct.pack('<4I', self.pid, self.pid + 4, 0, PEB)  # pid/tid at 0x20/0x24, PEB at 0x30
        uc.mem_write(TEB, teb)
        self.w32(TEB + 0x30, PEB)
        self.w32(TEB + 0x18, TEB)
        uc.mem_write(GDT + 0x08, self._gdt_entry(TEB, PAGE - 1))
        uc.mem_write(GDT + 0x10, self._gdt_entry(0, 0xFFFFF, 0xC0))
        uc.mem_write(GDT + 0x18, self._gdt_entry(0, 0xFFFFF, 0xC0, code=True))
        uc.reg_write(UC_X86_REG_GDTR, (0, GDT, 0x20 - 1, 0))
        uc.reg_write(UC_X86_REG_FS, 0x08)
        uc.reg_write(UC_X86_REG_SS, 0x10)
        uc.reg_write(UC_X86_REG_DS, 0x10)
        uc.reg_write(UC_X86_REG_ES, 0x10)
        uc.reg_write(UC_X86_REG_ESP, stack_top - 0x100)
        uc.hook_add(UC_HOOK_CODE, self._thunk_hook, begin=THUNKS, end=THUNKS + 0xFFFFF)
        uc.hook_add(UC_HOOK_INTR, self._intr_hook)
        uc.hook_add(UC_HOOK_MEM_INVALID, self._mem_invalid)

    @staticmethod
    def _gdt_entry(base, limit, flags=0x40, code=False):
        access = 0x9B if code else 0x93
        return struct.pack('<HHBBBB', limit & 0xFFFF, base & 0xFFFF, (base >> 16) & 0xFF,
                           access, flags | ((limit >> 16) & 0xF), (base >> 24) & 0xFF)

    # ------------------------------------------------------------ thunks
    def thunk(self, dll, name):
        key = (dll.lower(), name)
        if key in self.thunk_by_name:
            return self.thunk_by_name[key]
        a = THUNKS + len(self.thunks)
        self.thunks[a] = key
        self.thunk_by_name[key] = a
        return a

    def special(self, idx):
        return SPECIAL + idx

    def _regs_hook(self, uc, addr, size, _):
        r = [self.reg(x) for x in (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                                   UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP)]
        ebp = r[6]
        args = [self.r32(ebp + 8 + 4 * i) if self.is_mapped(ebp + 8 + 4 * i, 4) else 0 for i in range(6)]
        stack = [self.r32(r[7] + 4 * i) if self.is_mapped(r[7] + 4 * i, 4) else 0 for i in range(6)]
        print(f'  ## {self.name} {addr:08x} after {self.api_count}: '
              + ' '.join(f'{v:08x}' for v in r) + ' | args ' + ' '.join(f'{v:08x}' for v in args)
              + ' | stack ' + ' '.join(f'{v:08x}' for v in stack), flush=True)

    def _watch_hook(self, uc, access, addr, size, value, _):
        print(f'  @@ {self.name} write [{addr:08x}]={value & ((1 << 8 * size) - 1):0{2 * size}x} '
              f'from {self.reg(UC_X86_REG_EIP):08x} after {self.api_count} calls', flush=True)

    def _count_hook(self, uc, addr, size, _):
        self.hits[addr] += 1

    def _thunk_hook(self, uc, addr, size, _):
        if addr >= SPECIAL:
            self.os.special(addr - SPECIAL)
            return
        dll, name = self.thunks[addr]
        esp = self.reg(UC_X86_REG_ESP)
        ret = self.r32(esp)
        if self.trap is not None and ret == self.trap:
            self.trapped = (dll, name)     # an API reached from the trapped call site
            uc.emu_stop()
            return
        args = lambda n: self.r32(esp + 4 + 4 * n)  # noqa: E731
        self.api_count += 1
        self.hist[(name, ret)] = self.hist.get((name, ret), 0) + 1
        if self.progress and self.api_count % self.progress == 0:
            print(f'  .. {self.name} {self.api_count} calls, {time.time() - self.t0:.0f}s, '
                  f'{name} @0x{ret:08X}', flush=True)
        self.os.ms += self.ms_per_call   # time passes as the program works
        if self.max_api and self.api_count > self.max_api:
            self.stop_reason = f'API budget of {self.max_api} calls spent'
            uc.emu_stop()
            return
        h = winapi.lookup(dll, name)
        if h is None:
            self.stop_reason = f'unhandled {dll}!{name} from 0x{ret:08X}'
            uc.emu_stop()
            return
        argc, fn = h
        self.pending = None
        try:
            rv = fn(self, args)
        except winapi.Stop as e:
            self.stop_reason = str(e)
            uc.emu_stop()
            return
        if self.verbose and name not in winapi.QUIET:
            nargs = argc if argc >= 0 else 4
            print(f'  [{self.api_count:6}] {dll}!{name}({", ".join(hex(args(i)) for i in range(nargs))})'
                  f' -> {rv if isinstance(rv, str) else hex((rv or 0) & 0xFFFFFFFF)}  @0x{ret:08X}')
        if self.pending is not None:      # the API took over control flow
            return
        if rv is None:
            rv = 0
        self.setreg(UC_X86_REG_EAX, rv)
        self.setreg(UC_X86_REG_ESP, esp + 4 + 4 * max(argc, 0))
        self.setreg(UC_X86_REG_EIP, ret)

    # ------------------------------------------------------------ PE loading
    def load_pe(self, path, name=None, main=False):
        pe = pefile.PE(path)
        oh = pe.OPTIONAL_HEADER
        size = align(oh.SizeOfImage)
        base = oh.ImageBase
        if not main and any(base < e and base + size > s for s, e in self.mapped):
            base = self._find_free(size, 0x10000000)
        if base != oh.ImageBase:
            pe.relocate_image(base)
        img = pe.get_memory_mapped_image(ImageBase=base)
        self.mmap(base, size, img[:size])
        mod = dict(name=(name or os.path.basename(path)).lower(), path=path, pe=pe, base=base, size=size,
                   exports={})
        self.modules[base] = mod
        if hasattr(pe, 'DIRECTORY_ENTRY_EXPORT'):
            for s in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                addr = base + s.address
                if s.name:
                    mod['exports'][s.name.decode()] = addr
                mod['exports'][s.ordinal] = addr
        self._bind_imports(pe, base)
        return mod

    def _find_free(self, size, start):
        a = start
        while any(a < e and a + size > s for s, e in self.mapped):
            a += 0x10000
        return a

    def _bind_imports(self, pe, base):
        for imp in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
            dll = imp.dll.decode().lower()
            for i in imp.imports:
                nm = i.name.decode() if i.name else i.ordinal
                self.w32(i.address - pe.OPTIONAL_HEADER.ImageBase + base if False else
                         base + (i.address - pe.OPTIONAL_HEADER.ImageBase), self.resolve(dll, nm))

    def resolve(self, dll, name):
        dll = dll.lower()
        if not dll.endswith('.dll') and '.' not in dll:
            dll += '.dll'
        mod = self.module_by_name(dll)
        if mod is not None and name in mod['exports']:
            return mod['exports'][name]
        return self.thunk(dll, name)

    def module_by_name(self, dll):
        dll = os.path.basename(dll.replace('\\', '/')).lower()
        if '.' not in dll:
            dll += '.dll'
        for m in self.modules.values():
            if m['name'] == dll:
                return m
        return None

    def call_dllmain(self, mod, reason=1):
        ep = mod['pe'].OPTIONAL_HEADER.AddressOfEntryPoint
        if not ep:
            return 1
        return self.call(mod['base'] + ep, [mod['base'], reason, 0])

    # ------------------------------------------------------------ guest calls
    def call(self, addr, args):
        """Run a stdcall/cdecl guest function to completion (nested)."""
        saved = {r: self.reg(r) for r in (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
                                          UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI,
                                          UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EIP,
                                          UC_X86_REG_EFLAGS)}
        esp = saved[UC_X86_REG_ESP] - 0x40
        for a in reversed(args):
            esp -= 4
            self.w32(esp, a)
        esp -= 4
        ret_tramp = self.special(0)
        self.w32(esp, ret_tramp)
        self.setreg(UC_X86_REG_ESP, esp)
        self.run(addr, until=ret_tramp)
        rv = self.reg(UC_X86_REG_EAX)
        for r, v in saved.items():
            self.uc.reg_write(r, v)
        return rv

    # ------------------------------------------------------------ faults
    def _intr_hook(self, uc, intno, _):
        # Report what Windows NT would: EIP here is past the instruction.
        eip = self.reg(UC_X86_REG_EIP)
        prev2 = self.read(eip - 2, 2) if self.is_mapped(eip - 2, 2) else b''
        if intno == 3 and prev2[1:] == b'\xCC':
            self.os.dispatch_exception(0x80000003, eip - 1, (0,), ctx_eip=eip - 1)
        elif prev2[:1] == b'\xCD':
            n = prev2[1]
            if n == 3:      # the two-byte form: NT still backs EIP up by one
                self.os.dispatch_exception(0x80000003, eip - 1, (0,), ctx_eip=eip - 1)
            elif n == 0x2D:
                self.os.dispatch_exception(0x80000003, eip - 1, (0,), ctx_eip=eip - 1)
            else:           # a ring-0 gate from ring 3: #GP, an access violation
                self.os.dispatch_exception(0xC0000005, eip - 2, (0, 0xFFFFFFFF), ctx_eip=eip - 2)
        elif intno == 1:    # the trap flag
            self.os.dispatch_exception(0x80000004, eip, ())
        elif intno == 0:
            self.os.dispatch_exception(0xC0000094, eip, ())
        elif intno == 6:
            self.os.dispatch_exception(0xC000001D, eip, ())
        elif intno == 13:
            self.os.dispatch_exception(0xC0000096, eip, ())
        else:
            self.stop_reason = f'interrupt {intno} at 0x{eip:08X}'
            uc.emu_stop()

    def _mem_invalid(self, uc, access, addr, size, value, _):
        # Grow the heap lazily; anything else becomes an access violation.
        if HEAP <= addr < HEAP_END:
            lo = self.heap_mapped
            hi = align(max(addr + size, self.heap) + 0x100000)
            uc.mem_map(lo, hi - lo, UC_PROT_ALL)
            self.mapped.append((lo, hi))
            self.heap_mapped = hi
            return True
        self.fault = (access, addr, size)
        return False

    def raise_exception(self, code, address, after=None, info=()):
        self.os.dispatch_exception(code, address, info)

    # ------------------------------------------------------------ tracing
    def start_trace(self, lo, hi, out):
        from capstone import Cs, CS_ARCH_X86, CS_MODE_32
        md = Cs(CS_ARCH_X86, CS_MODE_32)
        f = open(out, 'w')
        regs = (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP)

        left = [int(os.environ.get('SDEMU_TRACE_MAX', '0')) or -1]

        def hook(uc, addr, size, _):
            if left[0] == 0:
                return
            left[0] -= 1
            code = bytes(uc.mem_read(addr, size))
            ins = next(md.disasm(code, addr), None)
            text = f'{ins.mnemonic} {ins.op_str}' if ins else code.hex()
            r = ' '.join(f'{uc.reg_read(x):08x}' for x in regs)
            f.write(f'{self.api_count:7} {addr:08x} {text:40} {r}\n')
        self.uc.hook_add(UC_HOOK_CODE, hook, begin=lo, end=hi)
        self.trace_file = f

    # ------------------------------------------------------------ run loop
    def run(self, start, until=0):
        eip = start
        depth_marker = object()
        self._run_stack = getattr(self, '_run_stack', [])
        self._run_stack.append(depth_marker)
        self.done_until = None
        try:
            while True:
                self.fault = None
                try:
                    self.uc.emu_start(eip, until)
                except UcError as e:
                    eip_now = self.reg(UC_X86_REG_EIP)
                    if self.fault:
                        access, addr, _ = self.fault
                        n = self.faults[eip_now] = self.faults.get(eip_now, 0) + 1
                        if n > 1000:
                            raise SystemExit(f'exception storm: {n} faults at 0x{eip_now:08X} '
                                             f'touching 0x{addr:08X}')
                        self.os.dispatch_exception(0xC0000005, eip_now,
                                                   (1 if access in (20, 21, 22) else 0, addr))
                        eip = self.reg(UC_X86_REG_EIP)
                        continue
                    raise SystemExit(f'emulation error at 0x{eip_now:08X}: {e}')
                if self.stop_reason:
                    raise SystemExit(self.stop_reason)
                eip = self.reg(UC_X86_REG_EIP)
                if self.sampled:
                    self.sampled = False
                    print(f'  ~~ {self.name} sample eip={eip:08x} esp={self.reg(UC_X86_REG_ESP):08x} '
                          f'after {self.api_count} calls, {time.time() - self.t0:.0f}s', flush=True)
                    continue
                if until and eip == until:
                    return
                if self.os.deferred:
                    while self.os.deferred:
                        self.os.deferred.pop(0)()
                    continue
                if self.os.resume_pending:
                    self.os.resume_pending = False
                    continue
                if not until:
                    return
        finally:
            self._run_stack.pop()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--cd', required=True, help='directory holding the extracted disc')
    ap.add_argument('--iso', required=True, help='the disc image, for raw sector reads')
    ap.add_argument('--exe', default='tp.exe')
    ap.add_argument('--out', default='work/dump')
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--trace', help='LO-HI: log every instruction executed in this range')
    ap.add_argument('--trace-out', default='work/trace.txt')
    ap.add_argument('--child-image', help="map this file as the loader's suspended child (TP.ICD)")
    a = ap.parse_args()
    emu = Emu(a.cd, a.iso, a.verbose)
    if a.child_image:
        emu.shared['child_image'] = a.child_image
    if a.trace:
        lo, hi = (int(x, 16) for x in a.trace.split('-'))
        emu.shared['trace'] = (lo, hi, a.trace_out)
        emu.start_trace(lo, hi, a.trace_out)
    emu.os.out_dir = a.out
    mod = emu.load_pe(os.path.join(a.cd, a.exe), main=True)
    emu.os.main_module = mod
    entry = mod['base'] + mod['pe'].OPTIONAL_HEADER.AddressOfEntryPoint
    queue = [(emu, entry)]
    while queue:
        proc, start = queue.pop(0)
        print(f'=== running {proc.name} (pid {proc.pid:#x}) from {start:#x}')
        t = time.time()
        try:
            proc.run(start)
        except SystemExit as ex:
            print(f'=== {proc.name} stopped: {ex}')
        finally:
            print(f'{proc.api_count} API calls, {time.time() - t:.1f}s; stop: {proc.stop_reason}')
            for spec in filter(None, os.environ.get('SDEMU_DUMP', '').split(',')):
                lo_, n_ = (int(x, 16) for x in spec.split(':'))
                if proc.is_mapped(lo_, n_):
                    with open(os.path.join(a.out, f'mem_{proc.name}_{lo_:08x}.bin'), 'wb') as f:
                        f.write(proc.read(lo_, n_))
            for a_, c in proc.hits.items():
                print(f'  hit {a_:08X} x{c}')
            if os.environ.get('SDEMU_HIST'):
                for (n, r), c in sorted(proc.hist.items(), key=lambda kv: -kv[1])[:25]:
                    print(f'  {c:8}  {n} @0x{r:08X}')
            proc.os.finish()
            os.makedirs(a.out, exist_ok=True)
            for m in proc.modules.values():
                with open(os.path.join(a.out, f"mem_{proc.name}_{m['name']}_{m['base']:08x}.bin"), 'wb') as f:
                    f.write(proc.read(m['base'], m['size']))
        for c in proc.os.children:
            if c.resumed and not c.started:
                c.started = True
                queue.append((c.emu, c.prepare()))


if __name__ == '__main__':
    main()
