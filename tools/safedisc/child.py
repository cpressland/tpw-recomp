"""The other side of the loader: the child process it decrypts TP.ICD into,
and the devices (the CD drive, secdrv) it talks to.

The child never runs. It is a sparse copy of the ICD image as Windows would
map it; the loader's WriteProcessMemory calls land in it, and its final state
is what we dump.
"""
import os
import struct

import pefile
from unicorn.x86_const import UC_X86_REG_ESP

PAGE = 0x1000


class Child:
    """A process the loader created: a full emulated process of its own,
    started once the parent resumes its thread and finishes running."""

    def __init__(self, parent, path, cmdline):
        import sdemu
        self.path = path
        self.cmdline = cmdline
        shared = parent.shared
        pid = shared['next_pid']
        shared['next_pid'] += 0x100
        name = os.path.splitext(os.path.basename(path))[0].lower()
        self.emu = sdemu.Emu(parent.cd, parent.iso, parent.verbose, shared=shared, pid=pid,
                             name=f'{name}_{pid:x}')
        self.emu.os.out_dir = parent.os.out_dir
        self.emu.os.set_cmdline(cmdline or path)
        self.mod = self.emu.load_pe(path, main=True)
        self.emu.os.main_module = self.mod
        self.pe = self.mod['pe']
        self.base, self.size = self.mod['base'], self.mod['size']
        self.original = self.emu.read(self.base, self.size)
        self.entry = self.base + self.pe.OPTIONAL_HEADER.AddressOfEntryPoint
        # A suspended thread's context: EAX = entry, EBX = PEB, EIP at kernel32's
        # BaseProcessStart, which calls EAX.
        self.start_thunk = self.emu.thunk('kernel32.dll', 'BaseProcessStart')
        self.ctx = {'eax': self.entry, 'ebx': 0x7FFDF000, 'eip': self.start_thunk,
                    'esp': 0x0012FFC4, 'eflags': 0x202}
        self.writes = []
        self.resumed = False
        self.started = False
        self.exit_code = 259

    def read(self, addr, n):
        if not self.emu.is_mapped(addr, n):
            return None
        return self.emu.read(addr, n)

    def write(self, addr, data):
        if not self.emu.is_mapped(addr, len(data)):
            return False
        self.emu.write(addr, data)
        self.writes.append((addr, len(data)))
        return True

    def alloc(self, addr, size):
        if addr:
            if not self.emu.is_mapped(addr, size):
                self.emu.mmap(addr, size)
            return addr
        return self.emu.alloc(size, PAGE)

    def prepare(self):
        """Load the thread context into the CPU; return where to start."""
        from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
                                       UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI,
                                       UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EFLAGS)
        regs = dict(eax=UC_X86_REG_EAX, ebx=UC_X86_REG_EBX, ecx=UC_X86_REG_ECX, edx=UC_X86_REG_EDX,
                    esi=UC_X86_REG_ESI, edi=UC_X86_REG_EDI, ebp=UC_X86_REG_EBP, esp=UC_X86_REG_ESP,
                    eflags=UC_X86_REG_EFLAGS)
        for k, r in regs.items():
            if k in self.ctx:
                self.emu.setreg(r, self.ctx[k])
        return self.ctx['eip']

    def dump(self, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        name = self.emu.name
        image = self.emu.read(self.base, self.size)
        changed = sum(1 for i in range(0, self.size, PAGE)
                      if image[i:i + PAGE] != self.original[i:i + PAGE])
        with open(os.path.join(out_dir, f'{name}.log'), 'w') as f:
            f.write(f'{self.path} base {self.base:#x} size {self.size:#x} entry {self.entry:#x}\n')
            f.write(f'context {self.ctx}\n')
            f.write(f'{len(self.writes)} writes, {changed} pages changed\n')
            for a, n in self.writes:
                f.write(f'  {a:#010x} {n:#x}\n')
        print(f'child {name}: {len(self.writes)} writes, {changed} pages changed')


CTX_OFF = {'edi': 0x9C, 'esi': 0xA0, 'ebx': 0xA4, 'edx': 0xA8, 'ecx': 0xAC, 'eax': 0xB0,
           'ebp': 0xB4, 'eip': 0xB8, 'eflags': 0xC0, 'esp': 0xC4}


def register(api, APIS, Handle):

    def child_of(e, h):
        x = e.os.handles.get(h)
        if x is not None and x.kind in ('process', 'thread'):
            return x.child
        return None

    @api(10, 'CreateProcessA')
    def CreateProcessA(e, a):
        app, cmd = e.cstr(a(0)), e.cstr(a(1))
        flags = a(5)
        print(f'  CreateProcess app={app!r} cmd={cmd!r} flags={flags:#x}')
        target = app or cmd.split('"')[1] if cmd and cmd.startswith('"') else (app or cmd.split()[0])
        path = e.os.host_path(target)
        if not path or not os.path.exists(path):
            e.os.last_error = 2
            return 0
        c = Child(e, path, cmd)
        e.os.children.append(c)
        hp = e.os.new_handle(Handle('process', child=c))
        ht = e.os.new_handle(Handle('thread', child=c))
        c.debugged = bool(flags & 3)
        e.write(a(9), struct.pack('<4I', hp, ht, 0x2000, 0x2004))
        return 1

    @api(5, 'ReadProcessMemory')
    def ReadProcessMemory(e, a):
        h, addr, buf, n = a(0), a(1), a(2), a(3)
        if h == 0xFFFFFFFF:
            data = e.read(addr, n)
        else:
            c = child_of(e, h)
            data = c.read(addr, n) if c else None
        if e.verbose:
            print(f'  ReadProcessMemory {addr:#x} {n:#x} -> {"ok" if data else "FAIL"}')
        if data is None:
            e.os.last_error = 299
            return 0
        e.write(buf, data)
        if a(4):
            e.w32(a(4), n)
        return 1

    @api(5, 'WriteProcessMemory')
    def WriteProcessMemory(e, a):
        h, addr, buf, n = a(0), a(1), a(2), a(3)
        data = e.read(buf, n)
        if h == 0xFFFFFFFF:
            e.write(addr, data)
            ok = True
        else:
            c = child_of(e, h)
            ok = c.write(addr, data) if c else False
        print(f'  WriteProcessMemory {addr:#x} {n:#x} -> {ok}')
        if a(4):
            e.w32(a(4), n if ok else 0)
        return int(ok)

    @api(5, 'VirtualProtectEx')
    def VirtualProtectEx(e, a):
        if a(4):
            e.w32(a(4), 0x40)
        return 1

    @api(5, 'VirtualAllocEx')
    def VirtualAllocEx(e, a):
        c = child_of(e, a(0))
        return c.alloc(a(1), a(2)) if c else 0

    @api(4, 'VirtualFreeEx')
    def VirtualFreeEx(e, a):
        return 1

    @api(4, 'VirtualQueryEx')
    def VirtualQueryEx(e, a):
        c = child_of(e, a(0))
        addr = a(1) & ~0xFFF
        inside = c and c.emu.is_mapped(addr)
        e.write(a(2), struct.pack('<7I', addr, c.base if inside else 0, 0x80, PAGE,
                                  0x1000 if inside else 0x10000, 0x80 if inside else 1,
                                  0x1000000 if inside else 0))
        return 28

    @api(1, 'FlushInstructionCache')
    def FlushInstructionCache(e, a):
        return 1

    @api(3, 'FlushInstructionCache')
    def FlushInstructionCache3(e, a):
        return 1

    @api(2, 'GetThreadContext')
    def GetThreadContext(e, a):
        c = child_of(e, a(0))
        p = a(1)
        if c is None:
            # Our own thread: no debug registers set.
            for off in range(4, 0x1C, 4):
                e.w32(p + off, 0)
            return 1
        for k, off in CTX_OFF.items():
            e.w32(p + off, c.ctx.get(k, 0))
        for off in range(4, 0x1C, 4):
            e.w32(p + off, 0)
        return 1

    @api(2, 'SetThreadContext')
    def SetThreadContext(e, a):
        c = child_of(e, a(0))
        if c is None:
            return 1
        for k, off in CTX_OFF.items():
            c.ctx[k] = e.r32(a(1) + off)
        print(f'  SetThreadContext child eip={c.ctx["eip"]:#x} eax={c.ctx["eax"]:#x}')
        return 1

    @api(1, 'ResumeThread')
    def ResumeThread(e, a):
        c = child_of(e, a(0))
        if c is None:
            return 0
        c.resumed = True
        print(f'  ResumeThread child: entry eax={c.ctx["eax"]:#x} eip={c.ctx["eip"]:#x}')
        return 1

    @api(4, 'OpenProcess')
    def OpenProcess(e, a):
        print(f'  OpenProcess pid={a(2):#x}')
        if a(2) == 0x1000:
            return 0xFFFFFFFF
        for c in e.os.children:
            return e.os.new_handle(Handle('process', child=c))
        return 0

    @api(3, 'DuplicateHandle')
    def DuplicateHandle(e, a):
        return 1

    # -------------------------------------------------------- devices
    @api(8, 'DeviceIoControl')
    def DeviceIoControl(e, a):
        h = e.os.handles.get(a(0))
        code = a(1)
        inb = e.read(a(2), a(3)) if a(2) and a(3) else b''
        name = getattr(h, 'name', '?')
        print(f'  DeviceIoControl {name} code={code:#010x} in={a(3):#x} out={a(5):#x} '
              f'in[:32]={inb[:32].hex()}')
        handler = getattr(e.os, 'ioctl', None)
        if handler:
            r = handler(name, code, inb, a(4), a(5))
            if r is not None:
                out, ok = r
                if out:
                    e.write(a(4), out[:a(5)])
                if a(6):
                    e.w32(a(6), len(out))
                return ok
        e.os.last_error = 1
        return 0
