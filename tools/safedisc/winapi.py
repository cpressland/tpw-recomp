"""The Win32 the SafeDisc loader sees: just enough of Windows 98/NT4, in Python.

Each handler is registered with its stdcall argument count (-1 for cdecl)
and returns EAX. Handlers that redirect control flow (guest callbacks, SEH)
set emu.pending and arrange the return themselves.
"""
import os
import struct
import time

from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                               UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
                               UC_X86_REG_EIP, UC_X86_REG_EFLAGS)

import child as childmod
import secdrv
import cdrom

APIS = {}


class Stop(Exception):
    pass


def api(argc, *names, dll=None):
    def deco(fn):
        for n in names or (fn.__name__,):
            APIS[n] = (argc, fn)
        return fn
    return deco


def lookup(dll, name):
    return APIS.get(name)


CTX_REGS = [(0x9C, UC_X86_REG_EDI), (0xA0, UC_X86_REG_ESI), (0xA4, UC_X86_REG_EBX),
            (0xA8, UC_X86_REG_EDX), (0xAC, UC_X86_REG_ECX), (0xB0, UC_X86_REG_EAX),
            (0xB4, UC_X86_REG_EBP), (0xB8, UC_X86_REG_EIP), (0xC0, UC_X86_REG_EFLAGS),
            (0xC4, UC_X86_REG_ESP)]
CONTEXT_SIZE = 0x2CC

SYSTEM_DLLS = {'kernel32.dll', 'user32.dll', 'advapi32.dll', 'version.dll', 'gdi32.dll', 'ntdll.dll',
               'shell32.dll', 'comctl32.dll', 'winspool.drv', 'ole32.dll', 'oleaut32.dll', 'winmm.dll',
               'wnaspi32.dll', 'msvcrt.dll', 'imagehlp.dll', 'psapi.dll', 'kernel32', 'ddraw.dll',
               'dsound.dll', 'dinput.dll', 'wsock32.dll', 'usp10.dll', 'setupapi.dll'}


class Handle:
    def __init__(self, kind, **kw):
        self.kind = kind
        self.__dict__.update(kw)


class WinState:
    def __init__(self, emu):
        self.emu = emu
        self.handles = {}
        self.next_handle = 0x100
        self.last_error = 0
        self.resume_pending = False
        self.conts = {}              # special index -> continuation
        self.next_special = 16
        self.tls = {}
        self.next_tls = 0
        self.env = {'PATH': 'C:\\WINDOWS;C:\\WINDOWS\\SYSTEM', 'windir': 'C:\\WINDOWS'}
        self.cmdline = 'D:\\tp.exe'
        self.out_dir = 'work/dump'
        self.children = []
        self.main_module = None
        self.weak = None
        self.deferred = []           # run between emu_start calls (hooks can't add hooks)
        self.version = 'nt4'         # what GetVersionEx reports
        self.c_root = os.path.join(os.path.dirname(emu.cd), 'cdrive')
        os.makedirs(self.c_root, exist_ok=True)
        self.epoch = 946728000.0         # 2000-01-01 12:00 UTC: a plausible 2000-era clock
        self.ms = 0                      # virtual milliseconds since boot
        self.log = []
        # Static strings the CRT keeps pointers to.
        self.cmdline_a = self._static(self.cmdline.encode() + b'\0')
        self.cmdline_w = self._static(self.cmdline.encode('utf-16le') + b'\0\0')
        env = b''.join(f'{k}={v}'.encode() + b'\0' for k, v in self.env.items()) + b'\0'
        self.env_a = self._static(env)
        self.env_w = self._static(env.decode().encode('utf-16le'))
        self.peb_setup()
        emu.w32(secdrv.KUSER_TICK, 0x0004C8E1)

    def _static(self, b):
        a = self.emu.alloc(len(b) + 16)
        self.emu.write(a, b)
        return a

    def peb_setup(self):
        e = self.emu
        from sdemu import PEB, KUSER
        e.w32(PEB + 0x08, 0x400000)       # ImageBaseAddress
        e.w32(PEB + 0x18, self._static(b'\0' * 0x100))  # ProcessHeap
        e.w32(PEB + 0x0C, 0)              # Ldr: SafeDisc may walk it; keep empty
        e.w32(PEB + 0xA4, 4)              # OSMajorVersion
        e.w32(PEB + 0xA8, 0)
        e.w32(KUSER + 0x26C, 4)

    def new_handle(self, h):
        n = self.next_handle
        self.next_handle += 4
        self.handles[n] = h
        return n

    # ------------------------------------------------------------ paths
    def host_path(self, p):
        p = p.replace('/', '\\')
        low = p.lower()
        if low.startswith('\\\\.\\'):
            return None
        if len(p) >= 2 and p[1] == ':':
            drive, rest = p[0].lower(), p[2:].lstrip('\\')
            root = self.emu.cd if drive == 'd' else self.c_root
        else:
            root, rest = self.emu.cd, p.lstrip('\\')
        cur = root
        for part in [x for x in rest.split('\\') if x and x != '.']:
            if part == '..':
                cur = os.path.dirname(cur)
                continue
            try:
                match = next((n for n in os.listdir(cur) if n.lower() == part.lower()), None)
            except (FileNotFoundError, NotADirectoryError):
                match = None
            cur = os.path.join(cur, match or part)
        return cur

    # ------------------------------------------------------------ guest callbacks
    def guest_call(self, addr, args, cont):
        """Call a guest function from inside an API; cont(eax) runs after it returns."""
        e = self.emu
        idx = self.next_special
        self.next_special += 1
        if self.next_special >= 0xF00:
            self.next_special = 16
        self.conts[idx] = cont
        esp = e.reg(UC_X86_REG_ESP)
        for a in reversed(args):
            esp -= 4
            e.w32(esp, a)
        esp -= 4
        e.w32(esp, e.special(idx))
        e.setreg(UC_X86_REG_ESP, esp)
        e.setreg(UC_X86_REG_EIP, addr)
        e.pending = True

    def api_return(self, esp_at_call, argc, eax):
        """Finish an API whose handler deferred via guest_call."""
        e = self.emu
        ret = e.r32(esp_at_call)
        e.setreg(UC_X86_REG_EAX, eax)
        e.setreg(UC_X86_REG_ESP, esp_at_call + 4 + 4 * argc)
        e.setreg(UC_X86_REG_EIP, ret)

    def special(self, idx):
        e = self.emu
        if idx == 0:          # top-level emu.call() return: the run loop stops here
            e.uc.emu_stop()
            return
        if idx == 1:
            self.seh_handler_returned()
            return
        cont = self.conts.pop(idx, None)
        if cont is None:
            e.stop_reason = f'stray return to special {idx}'
            e.uc.emu_stop()
            return
        e.pending = None
        cont(e.reg(UC_X86_REG_EAX))

    # ------------------------------------------------------------ SEH
    def save_context(self, addr, eip):
        e = self.emu
        e.write(addr, b'\0' * CONTEXT_SIZE)
        e.w32(addr, 0x1003F)
        for off, r in CTX_REGS:
            e.w32(addr + off, e.reg(r))
        e.w32(addr + 0xB8, eip)
        e.w32(addr + 0x8C, 0)
        e.w32(addr + 0x90, 0x3B)
        e.w32(addr + 0x94, 0x23)
        e.w32(addr + 0x98, 0x23)
        e.w32(addr + 0xBC, 0x1B)
        e.w32(addr + 0xC8, 0x23)

    def load_context(self, addr):
        e = self.emu
        for off, r in CTX_REGS:
            e.setreg(r, e.r32(addr + off))

    def dispatch_exception(self, code, address, info=(), ctx_eip=None):
        e = self.emu
        self.log.append(('exception', hex(code), hex(address)))
        if e.verbose:
            print(f'  !! exception {code:08X} at 0x{address:08X} info={[hex(i) for i in info]}')
        esp = (e.reg(UC_X86_REG_ESP) - 0x400) & ~0xF
        ctx = esp - CONTEXT_SIZE
        rec = ctx - 0x50
        self.save_context(ctx, address if ctx_eip is None else ctx_eip)
        if code == 0x80000003:
            # Windows reports int3 at the 0xCC; EIP in the context is past it
            # on NT only after the handler adjusts. Keep it at the 0xCC.
            pass
        e.write(rec, struct.pack('<5I', code, 0, 0, address, len(info)) +
                b''.join(struct.pack('<I', i) for i in info) + b'\0' * (60 - 4 * len(info)))
        state = dict(rec=rec, ctx=ctx, frame=e.r32(0x7FFDE000), base_esp=rec - 0x40)
        self.seh_stack = getattr(self, 'seh_stack', [])
        self.seh_stack.append(state)
        self._call_next_handler(state)

    def _call_next_handler(self, st):
        e = self.emu
        frame = st['frame']
        if frame in (0xFFFFFFFF, 0) or not e.is_mapped(frame, 8):
            code = e.r32(st['rec'])
            addr = e.r32(st['rec'] + 12)
            e.stop_reason = f'unhandled exception {code:08X} at 0x{addr:08X}'
            e.uc.emu_stop()
            return
        handler = e.r32(frame + 4)
        esp = st['base_esp']
        for v in (st['rec'], frame, st['ctx'], 0):
            pass
        e.write(esp, struct.pack('<5I', e.special(1), st['rec'], frame, st['ctx'], 0))
        e.setreg(UC_X86_REG_ESP, esp)
        e.setreg(UC_X86_REG_EIP, handler)
        e.pending = True

    def seh_handler_returned(self):
        e = self.emu
        st = self.seh_stack[-1]
        rv = e.reg(UC_X86_REG_EAX)
        if rv == 0:                      # ExceptionContinueExecution
            self.seh_stack.pop()
            self.load_context(st['ctx'])
            self.resume_pending = True
            e.uc.emu_stop()
            return
        st['frame'] = e.r32(st['frame'])
        self._call_next_handler(st)

    def set_cmdline(self, cmd):
        self.cmdline = cmd
        self.cmdline_a = self._static(cmd.encode('latin-1') + b'\0')
        self.cmdline_w = self._static(cmd.encode('utf-16le') + b'\0\0')

    def finish(self):
        for c in self.children:
            c.dump(self.out_dir)


# ======================================================================= kernel32
def S(emu, n):
    return emu.cstr(n)


@api(0)
def GetVersion(e, a):
    return 0x05650004 if e.os.version == 'nt4' else 0xC0000A04


@api(1)
def GetVersionExA(e, a):
    p = a(0)
    if e.os.version == 'nt4':
        e.write(p + 4, struct.pack('<4I', 4, 0, 1381, 2))
        e.put_cstr(p + 20, 'Service Pack 6')
    else:
        e.write(p + 4, struct.pack('<4I', 4, 10, 0x040A07CE, 1))
        e.put_cstr(p + 20, ' A ')
    return 1


@api(0)
def GetCommandLineA(e, a):
    return e.os.cmdline_a


@api(0)
def GetCommandLineW(e, a):
    return e.os.cmdline_w


@api(1)
def GetStartupInfoA(e, a):
    e.write(a(0), struct.pack('<I', 68) + b'\0' * 64)


@api(0)
def GetEnvironmentStrings(e, a):
    return e.os.env_a


APIS['GetEnvironmentStringsA'] = APIS['GetEnvironmentStrings']


@api(0)
def GetEnvironmentStringsW(e, a):
    return e.os.env_w


@api(1, 'FreeEnvironmentStringsA', 'FreeEnvironmentStringsW')
def FreeEnvironmentStrings(e, a):
    return 1


@api(3)
def GetEnvironmentVariableA(e, a):
    v = e.os.env.get(S(e, a(0)))
    if v is None:
        e.os.last_error = 203
        return 0
    if a(2) <= len(v):
        return len(v) + 1
    return e.put_cstr(a(1), v)


@api(2)
def SetEnvironmentVariableA(e, a):
    k, v = S(e, a(0)), S(e, a(1))
    if v is None:
        e.os.env.pop(k, None)
    else:
        e.os.env[k] = v
    return 1


@api(1)
def GetStdHandle(e, a):
    return {0xFFFFFFF6: 1, 0xFFFFFFF5: 2, 0xFFFFFFF4: 3}.get(a(0), 0xFFFFFFFF)


@api(1)
def GetFileType(e, a):
    h = e.os.handles.get(a(0))
    return 1 if h else 2


@api(1)
def SetHandleCount(e, a):
    return a(0)


@api(0)
def GetACP(e, a):
    return 1252


@api(0)
def GetOEMCP(e, a):
    return 437


@api(2)
def GetCPInfo(e, a):
    e.write(a(1), struct.pack('<I', 1) + b'?\0' + b'\0' * 12)
    return 1


@api(0)
def GetSystemDefaultLCID(e, a):
    return 0x0809


@api(0, 'GetUserDefaultLCID', 'GetThreadLocale')
def GetUserDefaultLCID(e, a):
    return 0x0809


@api(4)
def GetLocaleInfoA(e, a):
    vals = {0x1004: '1252', 0x0B: '850', 0x1001: 'English', 0x59: 'en', 0x5A: 'GB', 0x3: 'ENG',
            0x7: 'GBR', 0x5: '44', 0x1: '0809'}
    v = vals.get(a(1) & 0xFFFF, '')
    if not a(3):
        return len(v) + 1
    return e.put_cstr(a(2), v, a(3)) + 1


@api(4)
def GetLocaleInfoW(e, a):
    return 0


@api(5)
def GetStringTypeA(e, a):
    return _string_type(e, e.read(a(2), a(3) if a(3) != 0xFFFFFFFF else len(S(e, a(2))) + 1), a(4), a(1))


@api(4)
def GetStringTypeW(e, a):
    n = a(2)
    if n == 0xFFFFFFFF:
        n = len(e.wstr(a(1))) + 1
    s = e.read(a(1), 2 * n).decode('utf-16le', 'replace')
    return _string_type(e, s.encode('latin-1', 'replace'), a(3), a(0))


def _string_type(e, data, out, info):
    if info != 1:
        e.write(out, b'\0' * 2 * len(data))
        return 1
    res = []
    for b in data:
        c = chr(b)
        t = 0
        if c.isupper():
            t |= 1
        if c.islower():
            t |= 2
        if c.isdigit():
            t |= 4
        if c in ' \t\r\n\v\f':
            t |= 8
        if c in '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~':
            t |= 0x10
        if b < 32 or b == 127:
            t |= 0x20
        if c == ' ':
            t |= 0x40
        if c in '0123456789abcdefABCDEF':
            t |= 0x80
        if c.isalpha():
            t |= 0x100
        res.append(t)
    e.write(out, b''.join(struct.pack('<H', t) for t in res))
    return 1


@api(6)
def LCMapStringA(e, a):
    n = a(3)
    src = e.read(a(2), n if n != 0xFFFFFFFF else len(S(e, a(2))) + 1)
    flags = a(1)
    if flags & 0x200:
        src = src.upper()
    elif flags & 0x100:
        src = src.lower()
    if not a(5):
        return len(src)
    e.write(a(4), src[:a(5)])
    return min(len(src), a(5))


@api(6)
def LCMapStringW(e, a):
    n = a(3)
    if n == 0xFFFFFFFF:
        n = len(e.wstr(a(2))) + 1
    src = e.read(a(2), 2 * n).decode('utf-16le', 'replace')
    flags = a(1)
    if flags & 0x200:
        src = src.upper()
    elif flags & 0x100:
        src = src.lower()
    if not a(5):
        return len(src)
    e.write(a(4), src[:a(5)].encode('utf-16le'))
    return min(len(src), a(5))


@api(6)
def MultiByteToWideChar(e, a):
    n = a(3)
    src = e.read(a(2), n) if n != 0xFFFFFFFF else (S(e, a(2)) + '\0').encode('latin-1')
    w = src.decode('latin-1')
    if not a(5):
        return len(w)
    e.write(a(4), w[:a(5)].encode('utf-16le'))
    return min(len(w), a(5))


@api(8)
def WideCharToMultiByte(e, a):
    n = a(3)
    if n == 0xFFFFFFFF:
        n = len(e.wstr(a(2))) + 1
    w = e.read(a(2), 2 * n).decode('utf-16le', 'replace')
    b = w.encode('latin-1', 'replace')
    if not a(5):
        return len(b)
    e.write(a(4), b[:a(5)])
    return min(len(b), a(5))


@api(6)
def CompareStringA(e, a):
    def get(p, n):
        return e.read(p, n) if n != 0xFFFFFFFF else S(e, p).encode('latin-1')
    s1, s2 = get(a(2), a(3)), get(a(4), a(5))
    if a(1) & 1:
        s1, s2 = s1.lower(), s2.lower()
    return 1 if s1 < s2 else 3 if s1 > s2 else 2


@api(6)
def CompareStringW(e, a):
    return 2


@api(3)
def HeapCreate(e, a):
    return 0x00500000


@api(1)
def HeapDestroy(e, a):
    return 1


def _alloc(e, n):
    p = e.alloc(n + 16, 16)
    e.w32(p, n)
    e.write(p + 16, b'\0' * n)
    return p + 16


@api(3)
def HeapAlloc(e, a):
    return _alloc(e, a(2))


@api(4)
def HeapReAlloc(e, a):
    old = a(2)
    n = a(3)
    p = _alloc(e, n)
    if old:
        on = e.r32(old - 16)
        e.write(p, e.read(old, min(on, n)))
    return p


@api(3)
def HeapFree(e, a):
    return 1


@api(3)
def HeapSize(e, a):
    return e.r32(a(2) - 16)


@api(0)
def GetProcessHeap(e, a):
    return 0x00500000


@api(2, 'LocalAlloc', 'GlobalAlloc')
def LocalAlloc(e, a):
    return _alloc(e, a(1))


@api(1, 'LocalFree', 'GlobalFree')
def LocalFree(e, a):
    return 0


@api(1, 'GlobalLock', 'LocalLock')
def GlobalLock(e, a):
    return a(0)


@api(1, 'GlobalUnlock', 'LocalUnlock')
def GlobalUnlock(e, a):
    return 1


@api(4)
def VirtualAlloc(e, a):
    addr, size = a(0), a(1)
    if addr:
        if not e.is_mapped(addr, size):
            e.mmap(addr, size)
        return addr & ~0xFFF
    p = e.alloc(size + 0x1000, 0x1000)
    e.write(p, b'\0' * size)
    return p


@api(3)
def VirtualFree(e, a):
    return 1


@api(4)
def VirtualProtect(e, a):
    if a(3):
        e.w32(a(3), 0x40)
    return 1


@api(3)
def VirtualQuery(e, a):
    addr = a(0)
    base = addr & ~0xFFF
    mapped = e.is_mapped(addr)
    e.write(a(1), struct.pack('<7I', base, base, 0x40, 0x1000, 0x1000 if mapped else 0x10000,
                              0x40 if mapped else 1, 0x1000000 if mapped else 0))
    return 28


@api(0)
def GetLastError(e, a):
    return e.os.last_error


@api(1)
def SetLastError(e, a):
    e.os.last_error = a(0)


@api(1, 'SetErrorMode')
def SetErrorMode(e, a):
    return 0


@api(0)
def TlsAlloc(e, a):
    i = e.os.next_tls
    e.os.next_tls += 1
    return i


@api(1)
def TlsFree(e, a):
    return 1


@api(2)
def TlsSetValue(e, a):
    e.os.tls[a(0)] = a(1)
    return 1


@api(1)
def TlsGetValue(e, a):
    e.os.last_error = 0
    return e.os.tls.get(a(0), 0)


@api(1, 'InitializeCriticalSection', 'DeleteCriticalSection', 'EnterCriticalSection',
     'LeaveCriticalSection')
def CriticalSection(e, a):
    return 0


@api(1)
def InterlockedIncrement(e, a):
    v = (e.r32(a(0)) + 1) & 0xFFFFFFFF
    e.w32(a(0), v)
    return v


@api(1)
def InterlockedDecrement(e, a):
    v = (e.r32(a(0)) - 1) & 0xFFFFFFFF
    e.w32(a(0), v)
    return v


@api(2)
def InterlockedExchange(e, a):
    v = e.r32(a(0))
    e.w32(a(0), a(1))
    return v


@api(0)
def GetCurrentThreadId(e, a):
    return e.pid + 4


@api(0)
def GetCurrentProcessId(e, a):
    return e.pid


@api(0)
def GetCurrentProcess(e, a):
    return 0xFFFFFFFF


@api(0)
def GetCurrentThread(e, a):
    return 0xFFFFFFFE


@api(1)
def ExitProcess(e, a):
    raise Stop(f'ExitProcess({a(0)})')


@api(2)
def TerminateProcess(e, a):
    if a(0) == 0xFFFFFFFF:
        raise Stop(f'TerminateProcess(self, {a(1)})')
    h = e.os.handles.get(a(0))
    e.os.log.append(('TerminateProcess', a(0)))
    return 1


def now_ms(e):
    e.os.ms += 1                         # every clock read costs a little time
    return e.os.ms


@api(0)
def GetTickCount(e, a):
    return 0x100000 + now_ms(e)


@api(1)
def QueryPerformanceCounter(e, a):
    e.write(a(0), struct.pack('<Q', now_ms(e) * 1193))
    return 1


@api(1)
def QueryPerformanceFrequency(e, a):
    e.write(a(0), struct.pack('<Q', 1193182))
    return 1


@api(1)
def Sleep(e, a):
    e.os.ms += a(0)


def _systemtime(e, p):
    ms = now_ms(e)
    t = time.gmtime(e.os.epoch + ms / 1000)
    e.write(p, struct.pack('<8H', t.tm_year, t.tm_mon, (t.tm_wday + 1) % 7, t.tm_mday,
                           t.tm_hour, t.tm_min, t.tm_sec, ms % 1000))


@api(1, 'GetSystemTime', 'GetLocalTime')
def GetSystemTime(e, a):
    _systemtime(e, a(0))


@api(1)
def GetSystemTimeAsFileTime(e, a):
    ft = int((e.os.epoch + now_ms(e) / 1000 + 11644473600) * 1e7)
    e.write(a(0), struct.pack('<Q', ft))


@api(1)
def GetTimeZoneInformation(e, a):
    e.write(a(0), b'\0' * 172)
    return 0


@api(2)
def FileTimeToLocalFileTime(e, a):
    e.write(a(1), e.read(a(0), 8))
    return 1


@api(2)
def FileTimeToSystemTime(e, a):
    ft = struct.unpack('<Q', e.read(a(0), 8))[0]
    t = time.gmtime(ft / 1e7 - 11644473600)
    e.write(a(1), struct.pack('<8H', t.tm_year, t.tm_mon, (t.tm_wday + 1) % 7, t.tm_mday,
                              t.tm_hour, t.tm_min, t.tm_sec, 0))
    return 1


# ---------------------------------------------------------------- modules
@api(1)
def GetModuleHandleA(e, a):
    n = S(e, a(0))
    if n is None:
        return e.os.main_module['base']
    m = e.module_by_name(n)
    if m:
        return m['base']
    nl = n.lower()
    if not nl.endswith('.dll') and '.' not in nl:
        nl += '.dll'
    if nl in SYSTEM_DLLS:
        return fake_module(e, nl)
    e.os.last_error = 126
    return 0


def fake_module(e, name):
    name = name.lower()
    mods = e.shared['fake_mods']
    if name not in mods:
        mods[name] = 0x77000000 + 0x100000 * len(mods)
    base = mods[name]
    if not e.is_mapped(base):
        # A minimal MZ/PE header so code that peeks at it sees something sane.
        hdr = bytearray(0x1000)
        hdr[0:2] = b'MZ'
        struct.pack_into('<I', hdr, 0x3C, 0x80)
        hdr[0x80:0x84] = b'PE\0\0'
        struct.pack_into('<HH', hdr, 0x84, 0x14C, 0)
        e.mmap(base, 0x1000, bytes(hdr))
    return base


def fake_module_name(e, h):
    for n, b in e.shared['fake_mods'].items():
        if b == h:
            return n
    return None


@api(3)
def GetModuleFileNameA(e, a):
    h = a(0)
    if h == 0 or (e.os.main_module and h == e.os.main_module['base']):
        p = 'D:\\' + os.path.basename(e.os.main_module['path'])
    elif h in e.modules:
        p = 'D:\\' + os.path.basename(e.modules[h]['path'])
    elif fake_module_name(e, h):
        p = 'C:\\WINDOWS\\SYSTEM\\' + fake_module_name(e, h).upper()
    else:
        return 0
    return e.put_cstr(a(1), p, a(2))


@api(1, 'LoadLibraryA')
def LoadLibraryA(e, a):
    return _load_library(e, a, S(e, a(0)), 1)


@api(3, 'LoadLibraryExA')
def LoadLibraryExA(e, a):
    return _load_library(e, a, S(e, a(0)), 3, a(2))


def _load_library(e, a, name, argc, flags=0):
    base = os.path.basename(name.replace('\\', '/')).lower()
    if '.' not in base:
        base += '.dll'
    m = e.module_by_name(base)
    if m:
        return m['base']
    if base in SYSTEM_DLLS:
        return fake_module(e, base)
    path = e.os.host_path(name)
    if not path or not os.path.exists(path):
        path = e.os.host_path('D:\\' + base)
    if not path or not os.path.exists(path):
        e.os.last_error = 126
        print(f'  LoadLibrary: {name} not found')
        return 0
    print(f'  LoadLibrary: {name} -> {path}')
    m = e.load_pe(path)
    ep = m['pe'].OPTIONAL_HEADER.AddressOfEntryPoint
    if flags & 1 or not ep:      # DONT_RESOLVE_DLL_REFERENCES
        return m['base']
    esp = e.reg(UC_X86_REG_ESP)
    e.os.guest_call(m['base'] + ep, [m['base'], 1, 0],
                    lambda rv: e.os.api_return(esp, argc, m['base'] if rv else 0))
    return None


@api(1)
def FreeLibrary(e, a):
    return 1


@api(2)
def GetProcAddress(e, a):
    h, p = a(0), a(1)
    name = p if p < 0x10000 else S(e, p)
    if h in e.modules:
        addr = e.modules[h]['exports'].get(name)
        if addr is None:
            e.os.last_error = 127
            return 0
        return addr
    dll = fake_module_name(e, h)
    if dll is None:
        if h == e.os.main_module['base']:
            return 0
        dll = f'hmod_{h:08x}'
    if e.verbose:
        print(f'  GetProcAddress {dll}!{name}')
    return e.thunk(dll, name)


@api(1)
def DisableThreadLibraryCalls(e, a):
    return 1


# ---------------------------------------------------------------- files
@api(7)
def CreateFileA(e, a):
    name = S(e, a(0))
    access, disp = a(1), a(4)
    low = name.lower()
    if low.startswith('\\\\.\\'):
        dev = low[4:]
        e.os.log.append(('device', name))
        print(f'  CreateFile device {name}')
        if dev in ('d:', 'cdrom0') or dev.startswith('secdrv') or dev.startswith('scsi'):
            return e.os.new_handle(Handle('device', name=dev))
        e.os.last_error = 2
        return 0xFFFFFFFF
    path = e.os.host_path(name)
    exists = os.path.exists(path)
    if disp in (3, 5) and not exists:        # OPEN_EXISTING, TRUNCATE_EXISTING
        e.os.last_error = 2
        if e.verbose:
            print(f'  CreateFile {name}: not found')
        return 0xFFFFFFFF
    if os.path.isdir(path):
        return e.os.new_handle(Handle('dir', path=path))
    writable = bool(access & 0x40000000)
    if writable and path.startswith(e.cd):
        # Never write into the disc: redirect to the C: sandbox.
        e.os.last_error = 5
        return 0xFFFFFFFF
    if disp == 2 or (disp == 4 and not exists) or (disp == 1 and not exists):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').close()
    elif disp == 1 and exists:
        e.os.last_error = 80
        return 0xFFFFFFFF
    f = open(path, 'r+b' if writable else 'rb')
    print(f'  CreateFile {name} ({"rw" if writable else "r"})')
    return e.os.new_handle(Handle('file', path=path, f=f, name=name))


@api(5)
def ReadFile(e, a):
    h = e.os.handles.get(a(0))
    if not h or h.kind != 'file':
        e.os.last_error = 6
        return 0
    data = h.f.read(a(2))
    e.write(a(1), data)
    if a(3):
        e.w32(a(3), len(data))
    return 1


@api(5)
def WriteFile(e, a):
    if a(0) in (1, 2, 3):
        s = e.read(a(1), a(2))
        print('  [stdout]', s.decode('latin-1').rstrip())
        if a(3):
            e.w32(a(3), a(2))
        return 1
    h = e.os.handles.get(a(0))
    if not h or h.kind != 'file':
        e.os.last_error = 6
        return 0
    h.f.write(e.read(a(1), a(2)))
    if a(3):
        e.w32(a(3), a(2))
    return 1


@api(4)
def SetFilePointer(e, a):
    h = e.os.handles.get(a(0))
    if not h or h.kind != 'file':
        return 0xFFFFFFFF
    dist = a(1)
    if dist & 0x80000000:
        dist -= 1 << 32
    if a(2):
        hi = e.r32(a(2))
        dist = (hi << 32 | (dist & 0xFFFFFFFF))
        if hi & 0x80000000:
            dist -= 1 << 64
    h.f.seek(dist, a(3))
    pos = h.f.tell()
    if a(2):
        e.w32(a(2), pos >> 32)
    return pos & 0xFFFFFFFF


@api(2)
def GetFileSize(e, a):
    h = e.os.handles.get(a(0))
    size = os.path.getsize(h.path)
    if a(1):
        e.w32(a(1), 0)
    return size


@api(4)
def GetFileTime(e, a):
    h = e.os.handles.get(a(0))
    st = os.stat(h.path)
    ft = int((st.st_mtime + 11644473600) * 1e7)
    for p in (a(1), a(2), a(3)):
        if p:
            e.write(p, struct.pack('<Q', ft))
    return 1


@api(1)
def FlushFileBuffers(e, a):
    return 1


@api(1)
def SetEndOfFile(e, a):
    h = e.os.handles.get(a(0))
    h.f.truncate()
    return 1


@api(1)
def CloseHandle(e, a):
    h = e.os.handles.pop(a(0), None)
    if h is not None and getattr(h, 'f', None):
        h.f.close()
    return 1


@api(1)
def GetFileAttributesA(e, a):
    name = S(e, a(0))
    p = e.os.host_path(name)
    if not p or not os.path.exists(p):
        e.os.last_error = 2
        if e.verbose:
            print(f'  GetFileAttributes {name}: missing')
        return 0xFFFFFFFF
    return 0x10 if os.path.isdir(p) else (0x01 if p.startswith(e.cd) else 0x20)


@api(2)
def SetFileAttributesA(e, a):
    return 1


@api(1)
def DeleteFileA(e, a):
    p = e.os.host_path(S(e, a(0)))
    if p and p.startswith(e.os.c_root) and os.path.exists(p):
        os.remove(p)
        return 1
    return 0


@api(3)
def CopyFileA(e, a):
    src, dst = e.os.host_path(S(e, a(0))), e.os.host_path(S(e, a(1)))
    print(f'  CopyFile {S(e, a(0))} -> {S(e, a(1))}')
    if not src or not os.path.exists(src) or dst.startswith(e.cd):
        return 0
    if a(2) and os.path.exists(dst):
        return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, 'rb') as f, open(dst, 'wb') as g:
        g.write(f.read())
    return 1


@api(2)
def GetLogicalDriveStringsA(e, a):
    s = b'C:\\\0D:\\\0\0'
    if a(0) < len(s):
        return len(s)
    e.write(a(1), s)
    return len(s) - 1


@api(0)
def GetLogicalDrives(e, a):
    return 0b1100


@api(1)
def GetDriveTypeA(e, a):
    n = (S(e, a(0)) or 'C')[0].upper()
    return {'C': 3, 'D': 5}.get(n, 1)


@api(8)
def GetVolumeInformationA(e, a):
    root = (S(e, a(0)) or 'C:\\')[0].upper()
    label = 'TPWORLD' if root == 'D' else 'WIN98'
    if a(1):
        e.put_cstr(a(1), label, a(2))
    if a(3):
        e.w32(a(3), 0x2A0F1B6C if root == 'D' else 0x1234ABCD)
    if a(4):
        e.w32(a(4), 255)
    if a(5):
        e.w32(a(5), 0)
    if a(6):
        e.put_cstr(a(6), 'CDFS' if root == 'D' else 'FAT32', a(7))
    return 1


@api(2)
def GetSystemDirectoryA(e, a):
    return e.put_cstr(a(0), 'C:\\WINDOWS\\SYSTEM', a(1))


@api(2)
def GetWindowsDirectoryA(e, a):
    return e.put_cstr(a(0), 'C:\\WINDOWS', a(1))


@api(2)
def GetTempPathA(e, a):
    return e.put_cstr(a(1), 'C:\\WINDOWS\\TEMP\\', a(0))


@api(2)
def GetCurrentDirectoryA(e, a):
    return e.put_cstr(a(1), 'D:\\', a(0))


@api(1)
def SetCurrentDirectoryA(e, a):
    return 1


@api(4)
def GetFullPathNameA(e, a):
    n = S(e, a(0))
    if len(n) < 2 or n[1] != ':':
        n = 'D:\\' + n.lstrip('\\')
    ln = e.put_cstr(a(2), n, a(1))
    if a(3):
        e.w32(a(3), a(2) + n.rfind('\\') + 1)
    return ln


def _find_fill(e, p, path):
    st = os.stat(path)
    ft = int((st.st_mtime + 11644473600) * 1e7)
    attr = 0x10 if os.path.isdir(path) else 0x01
    name = os.path.basename(path)
    e.write(p, struct.pack('<I', attr) + struct.pack('<QQQ', ft, ft, ft) +
            struct.pack('<IIII', 0, st.st_size if attr != 0x10 else 0, 0, 0))
    e.put_cstr(p + 44, name, 260)
    e.put_cstr(p + 44 + 260, name.upper()[:12] if len(name) <= 12 else '', 14)


@api(2)
def FindFirstFileA(e, a):
    import fnmatch
    pat = S(e, a(0))
    d, mask = pat.rsplit('\\', 1) if '\\' in pat else ('D:', pat)
    hd = e.os.host_path(d + '\\')
    try:
        names = sorted(os.listdir(hd))
    except OSError:
        names = []
    hits = [os.path.join(hd, n) for n in names if fnmatch.fnmatch(n.lower(), mask.lower())]
    if e.verbose:
        print(f'  FindFirstFile {pat}: {len(hits)}')
    if not hits:
        e.os.last_error = 2
        return 0xFFFFFFFF
    _find_fill(e, a(1), hits[0])
    return e.os.new_handle(Handle('find', hits=hits[1:]))


@api(2)
def FindNextFileA(e, a):
    h = e.os.handles.get(a(0))
    if not h or not h.hits:
        e.os.last_error = 18
        return 0
    _find_fill(e, a(1), h.hits.pop(0))
    return 1


@api(1)
def FindClose(e, a):
    e.os.handles.pop(a(0), None)
    return 1


@api(3, 'GetProfileIntA')
def GetProfileIntA(e, a):
    return a(2)


@api(5, 'GetProfileStringA')
def GetProfileStringA(e, a):
    d = S(e, a(2)) or ''
    return e.put_cstr(a(3), d, a(4))


@api(6)
def GetPrivateProfileStringA(e, a):
    d = S(e, a(2)) or ''
    return e.put_cstr(a(3), d, a(4))


@api(4)
def GetPrivateProfileIntA(e, a):
    return a(2)


@api(4)
def WritePrivateProfileStringA(e, a):
    return 1


# ---------------------------------------------------------------- mappings
@api(6)
def CreateFileMappingA(e, a):
    name = S(e, a(5))
    size = a(4)
    h = e.os.handles.get(a(0))
    for x in e.os.handles.values():
        if x.kind == 'mapping' and name and x.name == name:
            e.os.last_error = 183
            return e.os.new_handle(x)
    path = None
    if h and h.kind == 'file':
        cur = h.f.tell()
        h.f.seek(0)
        data = h.f.read()
        h.f.seek(cur)
        size = size or len(data)
        # PAGE_READWRITE / PAGE_WRITECOPY: written back to the file on unmap.
        if a(2) & 0x04:
            path = h.path
    else:
        data = b''
    if e.verbose:
        print(f'  CreateFileMapping name={name} size={size:#x}')
    buf = e.alloc(align_up(size or 0x1000), 0x1000)
    e.write(buf, data[:size] + b'\0' * (size - len(data[:size])))
    e.os.last_error = 0
    return e.os.new_handle(Handle('mapping', name=name, buf=buf, size=size, path=path))


def align_up(n):
    return (n + 0xFFF) & ~0xFFF


@api(3)
def OpenFileMappingA(e, a):
    name = S(e, a(2))
    for x in e.os.handles.values():
        if x.kind == 'mapping' and x.name == name:
            return e.os.new_handle(x)
    print(f'  OpenFileMapping {name}: none')
    e.os.last_error = 2
    return 0


@api(5)
def MapViewOfFile(e, a):
    h = e.os.handles.get(a(0))
    if not h or h.kind != 'mapping':
        return 0
    return h.buf + a(3)


@api(1)
def UnmapViewOfFile(e, a):
    # A writable view of a file: its bytes go back to the file.
    for x in e.os.handles.values():
        if x.kind == 'mapping' and getattr(x, 'path', None) and x.buf <= a(0) < x.buf + max(x.size, 1):
            with open(x.path, 'r+b') as f:
                f.write(e.read(x.buf, x.size))
            break
    return 1


# ---------------------------------------------------------------- sync
@api(4)
def CreateEventA(e, a):
    return e.os.new_handle(Handle('event', name=S(e, a(3)), state=a(2)))


@api(3)
def CreateMutexA(e, a):
    return e.os.new_handle(Handle('mutex', name=S(e, a(2))))


@api(3)
def OpenMutexA(e, a):
    e.os.last_error = 2
    return 0


@api(1, 'SetEvent', 'ResetEvent', 'PulseEvent', 'ReleaseMutex')
def SetEvent(e, a):
    return 1


@api(2)
def WaitForSingleObject(e, a):
    h = e.os.handles.get(a(0))
    print(f'  WaitForSingleObject {a(0):#x} ({h.kind if h else None}) {a(1)}')
    if h and h.kind == 'process':
        return e.os.wait_child(h)
    return 0


@api(2)
def GetExitCodeProcess(e, a):
    h = e.os.handles.get(a(0))
    e.w32(a(1), getattr(h, 'exit_code', 259) if h else 0)
    return 1


@api(2)
def GetExitCodeThread(e, a):
    e.w32(a(1), 0)
    return 1


@api(2)
def WaitForInputIdle(e, a):
    return 0


@api(5)
def MsgWaitForMultipleObjects(e, a):
    return 0


@api(1)
def IsDebuggerPresent(e, a):
    return 0


@api(0)
def IsDebuggerPresent0(e, a):
    return 0


APIS['IsDebuggerPresent'] = (0, IsDebuggerPresent0)


@api(1)
def OutputDebugStringA(e, a):
    print('  [debug]', S(e, a(0)))


@api(1)
def UnhandledExceptionFilter(e, a):
    raise Stop('UnhandledExceptionFilter')


@api(1)
def SetUnhandledExceptionFilter(e, a):
    return 0


@api(4)
def RtlUnwind(e, a):
    # Pop SEH frames up to the target; MSVC's handlers don't need the unwind calls
    # for the loader's purposes.
    target = a(0)
    if target:
        e.w32(0x7FFDE000, target)
    return 0


@api(4)
def RaiseException(e, a):
    esp = e.reg(UC_X86_REG_ESP)
    ret = e.r32(esp)
    e.setreg(UC_X86_REG_ESP, esp + 4 + 16)
    e.setreg(UC_X86_REG_EIP, ret)
    info = [e.r32(a(3) + 4 * i) for i in range(a(2))] if a(3) else []
    e.pending = True
    e.os.dispatch_exception(a(0), ret, info)


@api(1)
def IsBadCodePtr(e, a):
    return 0 if e.is_mapped(a(0)) else 1


@api(2, 'IsBadReadPtr', 'IsBadWritePtr')
def IsBadReadPtr(e, a):
    return 0 if e.is_mapped(a(0), max(a(1), 1)) else 1


@api(3)
def GetThreadContext(e, a):
    return e.os.thread_context(a(0), a(1), get=True)


@api(2)
def SetThreadContext(e, a):
    return e.os.thread_context(a(0), a(1), get=False)


@api(1)
def ResumeThread(e, a):
    return e.os.resume_thread(a(0))


@api(1)
def SuspendThread(e, a):
    return 0


@api(2)
def GetThreadPriority(e, a):
    return 0


@api(2)
def SetThreadPriority(e, a):
    return 1


# DeviceIoControl, CreateProcess and the process-memory calls live in child.py.
childmod.register(api, APIS, Handle)


# ======================================================================= user32
@api(-1)
def wsprintfA(e, a):
    out = _sprintf(e, S(e, a(1)), lambda i: a(2 + i))
    return e.put_cstr(a(0), out)


@api(3)
def wvsprintfA(e, a):
    va = a(2)
    out = _sprintf(e, S(e, a(1)), lambda i: e.r32(va + 4 * i))
    return e.put_cstr(a(0), out)


def _sprintf(e, fmt, arg):
    import re
    i = 0
    out = []

    def rep(m):
        nonlocal i
        spec = m.group(0)
        conv = spec[-1]
        if conv == '%':
            return '%'
        v = arg(i)
        i += 1
        flags = spec[1:-1].replace('l', '').replace('h', '')
        if conv == 's':
            return ('%' + flags + 's') % (S(e, v) or '(null)')
        if conv in 'dic':
            if conv == 'c':
                return chr(v & 0xFF)
            if v & 0x80000000:
                v -= 1 << 32
            return ('%' + flags + 'd') % v
        if conv in 'uxXo':
            return ('%' + flags + conv) % v
        return spec
    return re.sub(r'%[-+ #0]*\d*(?:\.\d+)?[lh]?[sdiucxXo%]', rep, fmt)


@api(4)
def MessageBoxA(e, a):
    text, cap = S(e, a(1)), S(e, a(2))
    print(f'  MessageBox [{cap}]: {text}')
    e.os.log.append(('MessageBox', cap, text))
    raise Stop(f'MessageBox [{cap}]: {text}')


@api(0)
def GetDesktopWindow(e, a):
    return 0x10010


@api(1)
def GetKeyboardType(e, a):
    return {0: 4, 1: 0, 2: 12}.get(a(0), 0)


@api(1)
def RegisterClassA(e, a):
    e.os.wndproc = e.r32(a(0) + 4)
    return 0xC001


@api(12)
def CreateWindowExA(e, a):
    return 0x20020


@api(2)
def ShowWindow(e, a):
    return 1


@api(1, 'UpdateWindow', 'DestroyWindow')
def UpdateWindow(e, a):
    return 1


@api(5)
def PeekMessageA(e, a):
    return 0


@api(4)
def GetMessageA(e, a):
    return 0


@api(1, 'TranslateMessage', 'DispatchMessageA')
def TranslateMessage(e, a):
    return 0


@api(4)
def DefWindowProcA(e, a):
    return 0


@api(1)
def GetSystemMetrics(e, a):
    return {0: 1024, 1: 768}.get(a(0), 0)


# ======================================================================= advapi32
@api(3, 'RegOpenKeyA')
def RegOpenKeyA(e, a):
    return _reg_open(e, a(0), S(e, a(1)), a(2))


@api(5, 'RegOpenKeyExA')
def RegOpenKeyExA(e, a):
    return _reg_open(e, a(0), S(e, a(1)), a(4))


@api(9, 'RegCreateKeyExA')
def RegCreateKeyExA(e, a):
    return _reg_open(e, a(0), S(e, a(1)), a(7), create=True)


REGISTRY = {}


def _reg_open(e, root, sub, out, create=False):
    roots = {0x80000000: 'HKCR', 0x80000001: 'HKCU', 0x80000002: 'HKLM', 0x80000003: 'HKU'}
    base = roots.get(root) or getattr(e.os.handles.get(root), 'path', '?')
    path = base + '\\' + (sub or '')
    print(f'  RegOpenKey {path}')
    known = path.lower() in {k.lower() for k in REGISTRY} or create
    if not known:
        return 2
    e.w32(out, e.os.new_handle(Handle('reg', path=path)))
    return 0


@api(6)
def RegQueryValueExA(e, a):
    h = e.os.handles.get(a(0))
    print(f'  RegQueryValue {getattr(h, "path", "?")}\\{S(e, a(1))}')
    return 2


@api(1)
def RegCloseKey(e, a):
    return 0


@api(8)
def RegEnumKeyExA(e, a):
    return 259


@api(6)
def RegSetValueExA(e, a):
    return 0


# ======================================================================= version
@api(2)
def GetFileVersionInfoSizeA(e, a):
    return _version_blob(e, S(e, a(0)), None, 0)


@api(4)
def GetFileVersionInfoA(e, a):
    return _version_blob(e, S(e, a(0)), a(3), a(2))


def _version_blob(e, name, out, size):
    path = e.os.host_path(name)
    print(f'  GetFileVersionInfo {name}')
    try:
        import pefile
        pe = pefile.PE(path)
        for r in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if r.id == 16:
                d = r.directory.entries[0].directory.entries[0].data.struct
                blob = pe.get_data(d.OffsetToData, d.Size)
                break
        else:
            return 0
    except Exception:
        return 0
    if out is None:
        return len(blob)
    e.write(out, blob[:size])
    return 1


@api(4)
def VerQueryValueA(e, a):
    blk, sub = a(0), S(e, a(1))
    if sub == '\\':
        data = e.read(blk, 0x200)
        i = data.find(struct.pack('<I', 0xFEEF04BD))
        if i < 0:
            return 0
        e.w32(a(2), blk + i)
        e.w32(a(3), 52)
        return 1
    print(f'  VerQueryValue {sub}')
    return 0


# ======================================================================= ntdll
@api(5)
def NtQueryInformationProcess(e, a):
    cls, buf, n = a(1), a(2), a(3)
    print(f'  NtQueryInformationProcess class={cls}')
    if cls == 0:       # ProcessBasicInformation
        from sdemu import PEB
        e.write(buf, struct.pack('<6I', 0, PEB, 1, 8, 0x1000, 0x0F00))
        written = 24
    else:              # ProcessDebugPort (7) and anything else: zeros
        e.write(buf, b'\0' * n)
        written = n
    if a(4):
        e.w32(a(4), written)
    return 0


QUIET = {'EnterCriticalSection', 'LeaveCriticalSection', 'HeapAlloc', 'HeapFree', 'TlsGetValue',
         'TlsSetValue', 'InterlockedIncrement', 'InterlockedDecrement', 'GetLastError', 'SetLastError'}


def _ioctl(self, name, code, inb, out_ptr, out_size):
    if code == secdrv.IOCTL_MAIN:
        return secdrv.ioctl(self.emu, inb, out_size)
    if name in ('d:', 'cdrom0'):
        if not hasattr(self, 'cd'):
            self.cd = cdrom.CdDrive(self.emu.iso)
            # The loader has decrypted itself by now: find the weak-sector oracle.
            import weak

            def install():
                self.weak = weak.install(self.emu, self.main_module)
            self.deferred.append(install)
            self.emu.uc.emu_stop()
        return self.cd.ioctl(self.emu, code, inb, out_size)
    return None


WinState.ioctl = _ioctl


@api(0)
def BaseProcessStart(e, a):
    # The first thing a new process's thread runs: call the entry point in EAX,
    # then exit with its result.
    entry = e.reg(UC_X86_REG_EAX)
    print(f'  BaseProcessStart -> {entry:#x}')
    esp = e.reg(UC_X86_REG_ESP)
    e.w32(esp, e.thunk('kernel32.dll', 'ExitThread'))
    e.setreg(UC_X86_REG_EIP, entry)
    e.pending = True


@api(1)
def ExitThread(e, a):
    raise Stop(f'ExitThread({a(0)})')


# ======================================================================= for the RTPatch applier
@api(0)
def AreFileApisANSI(e, a):
    return 1


@api(0)
def SetFileApisToANSI(e, a):
    return 0


@api(5)
def GetDiskFreeSpaceA(e, a):
    for p, v in ((a(1), 64), (a(2), 512), (a(3), 0x100000), (a(4), 0x200000)):
        if p:
            e.w32(p, v)
    return 1


@api(3)
def GetShortPathNameA(e, a):
    return e.put_cstr(a(1), S(e, a(0)), a(2))


@api(3)
def MoveFileExA(e, a):
    src, dst = e.os.host_path(S(e, a(0))), S(e, a(1))
    print(f'  MoveFileEx {S(e, a(0))} -> {dst} flags={a(2):#x}')
    if not src or not os.path.exists(src):
        e.os.last_error = 2
        return 0
    if dst is None:
        os.remove(src)
        return 1
    hd = e.os.host_path(dst)
    if os.path.exists(hd) and a(2) & 1:      # MOVEFILE_REPLACE_EXISTING
        os.remove(hd)
    os.replace(src, hd)
    return 1


@api(2)
def MoveFileA(e, a):
    src, dst = e.os.host_path(S(e, a(0))), e.os.host_path(S(e, a(1)))
    if not src or not os.path.exists(src) or os.path.exists(dst):
        e.os.last_error = 183 if dst and os.path.exists(dst) else 2
        return 0
    os.replace(src, dst)
    return 1


@api(2)
def LocalFileTimeToFileTime(e, a):
    e.write(a(1), e.read(a(0), 8))
    return 1


@api(3)
def DosDateTimeToFileTime(e, a):
    d, t = a(0) & 0xFFFF, a(1) & 0xFFFF
    import calendar
    secs = calendar.timegm(((d >> 9) + 1980, (d >> 5) & 15, d & 31, t >> 11, (t >> 5) & 63, (t & 31) * 2, 0, 0, 0))
    e.write(a(2), struct.pack('<Q', int((secs + 11644473600) * 1e7)))
    return 1


@api(4)
def SetFileTime(e, a):
    return 1


@api(3)
def WriteProfileStringA(e, a):
    return 1


@api(2)
def OemToCharA(e, a):
    if a(0) != a(1):
        e.put_cstr(a(1), S(e, a(0)))
    return 1


@api(1)
def RemoveDirectoryA(e, a):
    p = e.os.host_path(S(e, a(0)))
    try:
        os.rmdir(p)
        return 1
    except OSError:
        return 0


@api(2)
def CreateDirectoryA(e, a):
    p = e.os.host_path(S(e, a(0)))
    if os.path.isdir(p):
        e.os.last_error = 183
        return 0
    os.makedirs(p)
    return 1


@api(0)
def SetFileApisToOEM(e, a):
    return 0


@api(3)
def FileTimeToDosDateTime(e, a):
    ft = struct.unpack('<Q', e.read(a(0), 8))[0]
    t = time.gmtime(ft / 1e7 - 11644473600)
    e.w16(a(1), ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday)
    e.w16(a(2), (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2))
    return 1


@api(1)
def GetSystemInfo(e, a):
    e.write(a(0), struct.pack('<HHIIIIIIIHH', 0, 0, 0x1000, 0x10000, 0x7FFEFFFF, 1, 1, 586, 0x10000, 6, 0))


@api(2)
def SetStdHandle(e, a):
    return 1


# DDE (the applier's "tell Program Manager" path): unavailable.
@api(4)
def DdeInitializeA(e, a):
    return 0x4009                    # DMLERR_SYS_ERROR


for _n, _c in (('DdeUninitialize', 1), ('DdeDisconnect', 1), ('DdeFreeStringHandle', 2),
               ('DdeCreateStringHandleA', 3), ('DdeConnect', 4), ('DdeCreateDataHandle', 7),
               ('DdeClientTransaction', 8)):
    APIS[_n] = (_c, lambda e, a: 0)

for _n, _c in (('RegDeleteValueA', 2), ('RegDeleteKeyA', 2)):
    APIS[_n] = (_c, lambda e, a: 2)
