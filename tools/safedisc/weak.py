"""SafeDisc 1's weak sectors, modelled from the loader's own oracle.

The disc carries a band of filler sectors after 00000001.TMP. A few of them
were mastered "weak": a real drive needs retries to read them, so they read
measurably slower. The loader times reads of ordinary filler sectors, then
of a weak one, and wants the weak read to take more than twice as long.

Which sectors are weak is not stored anywhere on the disc image: the loader
decides it with a small hash over the LBA, in one function. We find that
function by its hash constant, watch its answers, and make the virtual
drive slow for the LBAs it calls weak.
"""
import struct

from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX

# lea eax, [eax + edx*4 + 0x6a7f]: the tail of the weak-sector hash
SIGNATURE = bytes.fromhex('8d84907f6a0000')
WEAK_READ_MS = 400       # a weak sector costs the drive retries
NORMAL_READ_MS = 8


def find_oracle(image, base):
    i = image.find(SIGNATURE)
    if i < 0:
        return None
    # Walk back to the function's `push ebp; mov ebp, esp`.
    j = image.rfind(b'\x55\x8b\xec', max(0, i - 0x400), i)
    return base + j if j >= 0 else None


def install(emu, mod):
    image = emu.read(mod['base'], mod['size'])
    oracle = find_oracle(image, mod['base'])
    if oracle is None:
        print('  weak: oracle not found; reads will not be timed')
        return None
    sites = []
    for i in range(len(image) - 5):
        if image[i] == 0xE8:
            if mod['base'] + i + 5 + struct.unpack_from('<i', image, i + 1)[0] == oracle:
                sites.append(mod['base'] + i + 5)
    print(f'  weak: oracle at {oracle:#x}, {len(sites)} call sites')
    state = {'pending': [], 'weak': set(), 'asked': 0}

    def on_entry(uc, addr, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        state['pending'].append(struct.unpack('<I', uc.mem_read(esp + 4, 4))[0])

    def on_return(uc, addr, size, _):
        if not state['pending']:
            return
        lba = state['pending'].pop()
        state['asked'] += 1
        if uc.reg_read(UC_X86_REG_EAX) & 0xFFFF == 1:
            state['weak'].add(lba)
    emu.uc.hook_add(UC_HOOK_CODE, on_entry, begin=oracle, end=oracle)
    for s in sites:
        emu.uc.hook_add(UC_HOOK_CODE, on_return, begin=s, end=s)
    return state


def read_cost(state, lba, count):
    if state and any(l in state['weak'] for l in range(lba, lba + count)):
        return WEAK_READ_MS
    return NORMAL_READ_MS * count
