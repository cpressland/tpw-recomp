"""secdrv.sys, the Macrovision security driver, answered in user mode.

The loader talks to it with a single IOCTL (0xEF002407) whose buffers are
  in:  major, minor, patch, command, verification[0x100], extra_size, extra[0x40]
  out: major, minor, patch, verification[0x100], extra_size, extra[0x80]
The replies follow the latest driver (4.03.086), which answers each command
with a constant; the protocol is documented by RibShark's SafeDiscShim
(https://github.com/RibShark/SafeDiscShim).
"""
import struct

IOCTL_MAIN = 0xEF002407
KUSER_TICK = 0x7FFE0320

REPLIES = {
    0x3C: 0x400,          # GetDebugRegisterInfo
    0x3D: 0x2C8,          # GetIdtInfo
    0x3E: 0x5278D11B,     # SetupVerification
}


def verification(tick):
    v = [0] * 0x100
    v[0] = tick
    cur = 0xF367AC7F
    for i in (3, 2, 1):
        cur = (0x361962E9 - 0x0D5ACB1B * cur) & 0xFFFFFFFF
        v[i] = cur
        v[0] ^= cur
    return v


def ioctl(emu, inb, out_size):
    major, minor, patch, cmd = struct.unpack_from('<4I', inb)
    extra = struct.unpack_from('<64I', inb, 16 + 0x400 + 4)
    print(f'  secdrv: v{major}.{minor:02}.{patch:03} command {cmd:#x} from {backtrace(emu)}')
    if cmd in REPLIES:
        value = REPLIES[cmd]
    elif cmd == 0x3F and out_size == 0xC18 and extra[0] <= 0x60:
        value = 0
    elif cmd == 0x40 and out_size == 0xC18 and extra[0] and extra[1]:
        value = 0x56791283 if extra[1] <= 0x80 else 0x587C1284
    elif cmd == 0x41 and out_size == 0xC18 and extra[0] & 0xFF:
        value = 0
    elif cmd == 0x43 and extra[0] == 0x98A64100 and extra[1] <= 7 and extra[1] != 4:
        value = 0
    else:
        print(f'  secdrv: refusing command {cmd:#x}')
        return b'', 0
    tick = emu.r32(KUSER_TICK)
    out = struct.pack('<3I', 4, 3, 86) + struct.pack('<256I', *verification(tick))
    out += struct.pack('<I', 4) + struct.pack('<I', value) + b'\0' * (0x80 * 4 - 4)
    return out.ljust(out_size, b'\0'), 1


def backtrace(emu, depth=8):
    from unicorn.x86_const import UC_X86_REG_EBP
    ebp = emu.reg(UC_X86_REG_EBP)
    out = []
    for _ in range(depth):
        if not emu.is_mapped(ebp, 8):
            break
        out.append(f'{emu.r32(ebp + 4):x}')
        ebp = emu.r32(ebp)
    return ' < '.join(out)
