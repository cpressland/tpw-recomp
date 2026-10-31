#!/usr/bin/env python3
"""Theme Park World lift driver: work/tp/functions.json -> src/recomp/gen/.

Drives pcrecomp's shared `tools/lift/generate.py` + `lift32`, the same shape
as redalert2-recomp's run_lift.py:

* Closure-limited lifting (`generate.closure`): lift the call-graph closure
  from the entry point and give every other catalogued function a stub that
  aborts naming itself. Bring-up uses --all; the closure is for bisecting.
* Extents by walking the branches (`generate.true_extent`).

tp.exe has two code sections, `.text` and Bullfrog's hand-written `cseg`,
adjacent in the image; both are lifted, from the mapped image, as one range.

    python3 run_lift.py --all
    python3 run_lift.py --roots 0x0067EE10 --max 5000
"""
import argparse
import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# PCRECOMP picks another toolkit checkout, the same knob CMakeLists.txt has:
# the lifter and the runtime header must come from the same tree.
_TOOLS = os.path.join(os.environ.get('PCRECOMP', os.path.join(_HERE, '..', 'pcrecomp')), 'tools')
sys.path.insert(0, os.path.join(_TOOLS, 'lift'))
sys.path.insert(0, os.path.join(_TOOLS, 'pe'))

import pefile                                              # noqa: E402
from capstone import Cs, CS_ARCH_X86, CS_MODE_32          # noqa: E402
from generate import (EXTENT_REACH, closure, find_splits, true_extent,  # noqa: E402
                      linear_disassemble_function, lift_function_linear, write_chunk)
from lift32 import Lifter                                  # noqa: E402
from pe_analyze import analyze_pe, build_iat_map           # noqa: E402

EXE = os.path.join(_HERE, 'game', 'TP.EXE')
WORK = os.path.join(_HERE, 'work', 'tp')
CATALOG = os.path.join(WORK, 'functions.json')
SEEDS = os.path.join(WORK, 'rtti_seeds.json')
OUT = os.path.join(_HERE, 'src', 'recomp', 'gen')
STATS = os.path.join(WORK, 'lift_stats.json')

# Sim Theme Park World 2.0, SafeDisc removed (docs/RECON.md), by PE timestamp:
# unwrapped from the disc by tools/safedisc/unwrap.py (TP.ICD's own stamp), or
# the 2000-era no-CD release, whose stamp was rewritten. Their code differs
# only in which import slot some calls name, and the no-CD's CD-check patch.
EXE_STAMPS = {0x38DB8658: 'unwrapped from the disc', 0x63732172: 'no-CD release'}
TITLE = 'Theme Park World'

# Entries the catalog does not find. Grown from runs (docs/bringup.md).
RUN_SEEDS = []

# Functions whose body is the host's instead of the lift: the host defines
# tpw_hook_<name>(void) and it runs like an import shim.
HOOKS = {}

# Remaster patches: C emitted after one instruction (va -> C).
PATCHES = {
    # Any screen size, widescreen too: every path of the resolution switch
    # meets here, having set the width (0x7A1968) and height (0x7A1930), and
    # this instruction loads the width. The host may replace both
    # (tpw_size_override, --resolution WxH).
    0x0044A156: '{ extern void tpw_size_override(uint32_t *, uint32_t *); '
                'uint32_t w_ = MEM32(0x7A1968), h_ = MEM32(0x7A1930); tpw_size_override(&w_, &h_); '
                'MEM32(0x7A1968) = w_; MEM32(0x7A1930) = h_; eax = w_; }',
    # The HUD at widescreen (src/host/widescreen.c): the widget constructor,
    # after its push ebp; the projection, for models drawn in screen space.
    0x0065BCAD: '{ extern void tpw_widget(uint32_t, uint32_t); tpw_widget(ecx, esp); }',
    0x00579690: '{ extern void tpw_hud_models(uint32_t, uint32_t, uint32_t); '
                'tpw_hud_models(MEM32(esp + 8), MEM32(esp + 0xC), MEM32(esp + 0x10)); }',
    # Splash and loading pictures: once the back buffer is locked, the host
    # draws the picture at 4:3 with black sides, and the game only unlocks.
    0x00587191: '{ extern int tpw_splash(uint32_t, uint32_t); '
                'if (tpw_splash(esi, esp + 0x20)) goto L_005872BA; }',
    # Movies: their width, and their x when one is given, at 4:3.
    0x0051B0F7: '{ extern uint32_t tpw_movie_w(void); MEM32(0x802460) = tpw_movie_w(); }',
    0x0051B150: '{ extern uint32_t tpw_movie_x(uint32_t); MEM32(0x802458) = tpw_movie_x(ecx); }',
    # Hit circles, of a squeezed widget: made by the layout builder, and the
    # x distance in their test, after it is stored.
    0x00660045: '{ extern void tpw_hit_circle(uint32_t, uint32_t); tpw_hit_circle(eax, MEM32(ebp - 0xA0)); }',
    0x0066C0DA: '{ extern int32_t tpw_circle_dx(uint32_t, int32_t); '
                'MEM32(ebp - 8) = (uint32_t)tpw_circle_dx(MEM32(ebp - 0x10), (int32_t)eax); }',
}

# The GUI layout builder (sub_0065FD58) has just read a widget's text
# rectangle, hit shape or list columns into its locals: the x fields, as
# offsets below ebp; the widget is at [ebp-0xA0]. Widgets themselves go
# through the constructor.
LAYOUT_X = {
    0x0065FECA: (0x28, 0x24),   # text rectangle
    0x0065FF78: (0x28, 0x24),   # hit rectangle
    0x0065FFF8: (0x28,),        # hit circle's centre
    0x0066009A: (0x28, 0x24),   # hit rectangle
    0x00660165: (0x28,),        # hit polygon's point
    0x00660562: (0x28, 0x24),   # area, of a type 4 widget
    0x006605BB: (0x28, 0x24),   # area, of a type 7 widget
    0x0066063F: (0x28, 0x24),   # list column: left, right
}
for _va, _offs in LAYOUT_X.items():
    PATCHES[_va] = ('{ extern void tpw_layout_x(uint32_t, uint32_t); %s }'
                    % ' '.join('tpw_layout_x(MEM32(ebp - 0xA0), ebp - 0x%X);' % o for o in _offs))

# Widths in pixels made virtual by the GUI's width, for widgets the squeeze
# narrows again: HUD text placed at an offset from its element ([esi+0xDE])
# times the width, three copies of one routine; and the pause menu's lines
# (Esc in a park), each as wide as its text measures (sub_00492F60). The
# register just loaded with the width gets the 4:3 width instead. Not the
# mouse's conversions (0x486C75, 0x48A68E): those are places on the screen.
REL_W = {
    0x0048F8CC: 'ecx', 0x0048F957: 'edx', 0x0048F9CA: 'ecx',
    0x00491427: 'ecx', 0x004914AE: 'edx', 0x0049151E: 'ecx',
    0x00491B4C: 'ecx', 0x00491BD7: 'edx', 0x00491C4A: 'ecx',
    0x00492FD8: 'ecx',
}
for _va, _r in REL_W.items():
    PATCHES[_va] = '{ extern uint32_t tpw_rel_w(uint32_t); %s = tpw_rel_w(%s); }' % (_r, _r)

# Picking (sub_0045BF90): the ray under the mouse is made from the view's
# corners, built for a 4:3 view (atan2(0.75, 1) at 0x45C09A), and the mouse
# as a fraction across them, x / width. Wider than 4:3 the picked spot was
# pulled towards the middle, to (4/3) / (width / height) of the way out (the
# grass beside a ride picked the ride, at 21:9). The fraction is widened
# about the middle by as much, after its fdivr (tpw_pick_scale).
PATCHES[0x0045C1CA] = '{ extern double tpw_pick_scale(void); _st[0] = 0.5 + (_st[0] - 0.5) * tpw_pick_scale(); }'

# The GUI's mouse (sub_0065889C sets it, sub_006588EF moves it), in its
# virtual 2048x1536: past 2048 pixels wide (or 1536 high) the last pixel
# comes out 2047 (1535), outside the full-screen layers, which end there.
# The park's view is one, so the right edge did not scroll (nor would the
# original's at 2048x1536). After each one's mov ebp, esp: its x and y kept
# inside.
for _va in (0x0065889D, 0x006588F0):
    PATCHES[_va] = ('{ int16_t x_ = (int16_t)MEM32(ebp + 8), y_ = (int16_t)MEM32(ebp + 0xC); '
                    'if (x_ > 2046) MEM32(ebp + 8) = 2046; if (y_ > 1534) MEM32(ebp + 0xC) = 1534; }')

# pcrecomp's CPUID asks the host CPU, and only under MSVC; on an arm64 Mac it
# leaves the registers as they were and the game finds no MMX ("Theme Park
# World requires an MMX-enabled processor"). Every cpuid goes to the host's
# tpw_cpuid (src/host/cpu.c), which answers as a Pentium II.
CPUID_LIFTED = 'CPUID(eax, ebx, ecx, edx);'
CPUID_HOST = '{ extern void tpw_cpuid(void); RECOMP_REGS_OUT(); tpw_cpuid(); RECOMP_REGS_IN(); }'


def apply_patches(body, patches):
    """Add each patch's C after the line that lifts its instruction."""
    for va, c in patches.items():
        tag = '/* 0x%08X:' % va
        k = body.find(tag)
        if k != -1:
            start = body.rfind('\n', 0, k) + 1
            e = body.index('\n', k)
            if c.startswith('@replace:'):
                body = body[:start] + '    ' + c[len('@replace:'):] + '\n' + body[e + 1:]
            else:
                body = body[:e + 1] + '    ' + c + '\n' + body[e + 1:]
    return body


def exe_stamp(path):
    """The PE header's TimeDateStamp: which build of the game an exe is."""
    with open(path, 'rb') as f:
        head = f.read(4096)
    nt = int.from_bytes(head[0x3C:0x40], 'little')
    return int.from_bytes(head[nt + 8:nt + 12], 'little')


def lifter_stamp():
    """What a lift depends on besides the game: the lifter and this file."""
    h = hashlib.sha1()
    for p in (os.path.join(_TOOLS, 'lift', 'lift32.py'), os.path.join(_TOOLS, 'lift', 'generate.py'),
              os.path.abspath(__file__)):
        h.update(open(p, 'rb').read().replace(b'\r\n', b'\n'))
    return h.hexdigest()[:16]


def write_target_header(out, stamp):
    """recomp_target.h: what the host needs to know about the binary it runs."""
    with open(os.path.join(out, 'recomp_target.h'), 'w', newline='\n') as f:
        f.write('/* Generated by run_lift.py: the binary this build runs. */\n#pragma once\n')
        f.write('#define TPW_EXE_NAME "tp.exe"\n')
        f.write('#define TPW_TITLE "%s"\n' % TITLE)
        f.write('#define TPW_EXE_STAMP 0x%08Xu   /* its PE timestamp: the build lifted */\n' % stamp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', default=EXE)
    ap.add_argument('--catalog', default=CATALOG)
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--roots', default='', help='comma-separated extra root VAs')
    ap.add_argument('--max', type=int, default=3000, help='closure size cap')
    ap.add_argument('--all', action='store_true', help='lift every function')
    ap.add_argument('--split', type=int, default=400, help='functions per .c file')
    ap.add_argument('--seeds', default=os.path.join(WORK, 'run_seeds.json'),
                    help='seed_from_log.py JSON of unresolved targets from runs')
    ap.add_argument('--check', action='store_true',
                    help='exit 0 if the lift is current (this lifter, this driver), 1 if not')
    args = ap.parse_args()
    if args.check:
        try:
            sys.exit(0 if open(os.path.join(args.out, 'lift_stamp.txt')).read().strip()
                     == lifter_stamp() else 1)
        except OSError:
            sys.exit(1)
    if not os.path.exists(args.catalog):
        sys.exit('no catalog at %s -- run disasm32.py first (docs/building.md)' % args.catalog)
    stamp = exe_stamp(args.exe)
    if stamp not in EXE_STAMPS:
        sys.exit('%s is not a build this project supports: PE timestamp 0x%08X, wanted one of %s '
                 '(docs/RECON.md)' % (args.exe, stamp, ', '.join('0x%08X' % s for s in EXE_STAMPS)))
    print('[*] %s (%s)' % (args.exe, EXE_STAMPS[stamp]))

    info = analyze_pe(args.exe)
    iat = build_iat_map(info)
    cs, ce = info.code_start, info.code_end
    print('[*] base=0x%08X code=0x%08X-0x%08X IAT=%d' % (info.image_base, cs, ce, len(iat)))
    # .text and cseg as the loader maps them: one buffer from cs to ce.
    image = pefile.PE(args.exe).get_memory_mapped_image()
    code = image[cs - info.image_base:ce - info.image_base]

    cat = json.load(open(args.catalog))
    byaddr = {f['address']: f for f in cat['functions'] if cs <= f['address'] < ce}
    print('[*] catalog: %d functions in the code range' % len(byaddr))

    # Vtable slots and run seeds, bounded by the next known entry.
    want = {a for a in RUN_SEEDS if cs <= a < ce and a not in byaddr}
    for path in (SEEDS, args.seeds):
        if os.path.exists(path):
            want |= {e['address'] for e in json.load(open(path))
                     if cs <= e['address'] < ce and e['address'] not in byaddr}
    known = sorted(set(byaddr) | want)
    nxt = {a: (known[i + 1] if i + 1 < len(known) else ce) for i, a in enumerate(known)}
    for a in want:
        byaddr[a] = {'address': a, 'end': min(nxt[a], a + 0x100), 'calls_to': [],
                     'entry_kind': 'start'}
    print('[*] seeds not in the catalog: %d injected' % len(want))

    entry = info.image_base + info.entry_point_rva
    roots = [entry] + [int(x, 0) for x in args.roots.split(',') if x.strip()]
    chosen = set(byaddr) if args.all else set(closure(byaddr, roots, args.max))
    print('[*] lifting %d of %d functions (%s)'
          % (len(chosen), len(byaddr), 'all' if args.all else 'closure, cap %d' % args.max))

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    lifter = Lifter(iat_map=iat, lifted=set(byaddr), precise_carry=True, precise_sbb=True)
    os.makedirs(args.out, exist_ok=True)
    for fn in os.listdir(args.out):                 # a smaller lift must not leave stale chunks
        if fn.startswith('recomp_') and fn.endswith('.c'):
            os.remove(os.path.join(args.out, fn))
    entries, chunk, idx, errors, dirty = [], [], 0, 0, 0
    ordered = sorted(byaddr)
    starts = {a for a in ordered if byaddr[a].get('entry_kind') != 'alias'}
    splits = find_splits(md, code, cs, ce, starts)
    starts -= splits
    print('[*] split entries: %d' % len(splits))
    t0 = time.time()

    def flush(force=False):
        nonlocal chunk, idx
        if chunk and (force or len(chunk) >= args.split):
            write_chunk(args.out, idx, chunk)
            idx += 1
            chunk = []

    def lift_one(addr, name, end, reached):
        nonlocal errors
        if addr in HOOKS:
            hook = HOOKS[addr]
            chunk.append(('extern void tpw_hook_%s(void);\nvoid %s(void) { tpw_hook_%s(); }\n'
                          % (hook, name, hook), addr, name))
            entries.append((addr, name))
            return
        try:
            lo = min(reached) if reached else addr
            insns, leaders = (linear_disassemble_function(md, code, cs, lo, end, reached=reached)
                              if end > addr else ([], None))
            if leaders is not None:
                leaders.add(addr)
            body = (lift_function_linear(lifter, name, insns, leaders, addr) if insns
                    else 'void %s(void) { }\n' % name)
            body = apply_patches(body, {va: c for va, c in PATCHES.items() if reached and va in reached})
            body = body.replace(CPUID_LIFTED, CPUID_HOST)
        except Exception as e:                      # noqa: BLE001 -- counted, not hidden
            body = '/* ERROR %s: %s */\nvoid %s(void) { }\n' % (name, e, name)
            errors += 1
        chunk.append((body, addr, name))
        entries.append((addr, name))
        if len(chunk) >= args.split:
            flush()
            print('[*]   %d/%d (%d err)' % (len(entries), len(chosen), errors), flush=True)

    # Branch and call targets the catalog missed become entries, round by
    # round, until none are new (redalert2-recomp's run_lift.py has why).
    todo, added = sorted(chosen), 0
    while todo:
        outside = set()
        for addr in todo:
            reached, behind, called = set(), set(), set()
            end, clean = true_extent(md, code, cs, addr, min(addr + EXTENT_REACH, ce), starts,
                                     reached=reached, behind=behind, called=called)
            outside |= {t for t in behind if t not in reached} | called
            dirty += not clean
            lift_one(addr, 'sub_%08X' % addr, end, reached)
        todo = sorted(t for t in outside if cs <= t < ce and t not in byaddr)
        for t in todo:
            byaddr[t] = {'address': t, 'end': ce, 'calls_to': [], 'entry_kind': 'start'}
            chosen.add(t)
        added += len(todo)
    print('[*] branch and call targets outside the catalog, added as entries: %d' % added)

    stubs = [a for a in ordered if a not in chosen]
    for a in stubs:
        name = 'sub_%08X' % a
        chunk.append(('void %s(void) { RECOMP_NOT_LIFTED(0x%08Xu); }\n' % (name, a), a, name))
        entries.append((a, name))
        flush()
    flush(force=True)

    with open(os.path.join(args.out, 'recomp_funcs.h'), 'w', newline='\n') as f:
        f.write('/* tp.exe - AUTO-GENERATED by run_lift.py */\n#pragma once\n'
                '#include <stdint.h>\n\n'
                'void recomp_not_lifted(uint32_t va);\n'
                '#define RECOMP_NOT_LIFTED(va) recomp_not_lifted(va)\n\n')
        for a, n in entries:
            f.write('void %s(void);\n' % n)
    with open(os.path.join(args.out, 'recomp_dispatch.c'), 'w', newline='\n') as f:
        f.write('/* tp.exe - AUTO-GENERATED by run_lift.py */\n'
                '#include "recomp_types.h"\n#include "recomp_funcs.h"\n\n'
                'const recomp_dispatch_entry_t recomp_dispatch_table[] = {\n')
        for a, n in sorted(entries):
            f.write('    { 0x%08Xu, %s },\n' % (a, n))
        f.write('};\nconst uint32_t recomp_dispatch_count = %d;\n'
                'const uint32_t tpw_entry_va = 0x%08Xu;\n' % (len(entries), entry))

    try:
        import name_lift
    except ImportError:
        print('[*] names: no tools/lift/name_lift.py in this pcrecomp; functions keep their address names')
    else:
        rtti_path = os.path.join(WORK, 'rtti.json')
        rtti = json.load(open(rtti_path)) if os.path.exists(rtti_path) else {}
        known_names = {va: 'hook_%s' % hook for va, hook in HOOKS.items()}
        named = name_lift.name_lift(args.out, name_lift.Image(args.exe), rtti, known_names)
        print('[*] names: %d of %d functions named from the binary\'s own evidence'
              % (sum(1 for f in named.values() if f.name), len(named)))

    lines = sum(sum(1 for _ in open(os.path.join(args.out, fn), encoding='utf-8', errors='replace'))
                for fn in os.listdir(args.out))
    stats = {'lifted': len(chosen), 'stubs': len(stubs), 'errors': errors,
             'no_terminator': dirty, 'files': idx, 'lines': lines}
    json.dump(stats, open(STATS, 'w'), indent=1)
    write_target_header(args.out, stamp)
    # The game's own DLLs, stubbed (src/host/main.c registers them): generated
    # from the game folder like the lift, so they live beside it, not in git.
    game = os.path.dirname(os.path.abspath(args.exe))
    gen = os.path.join(_HERE, 'tools', 'gen')
    for tool, out in (('won_stubs.py', 'won_stubs.c'),):
        import subprocess
        subprocess.run([sys.executable, '-I', os.path.join(gen, tool), game, os.path.join(args.out, out)],
                       check=True)
    open(os.path.join(args.out, 'lift_stamp.txt'), 'w').write(lifter_stamp() + '\n')
    print('=' * 60)
    print('  lifted %d   not-lifted stubs %d   errors %d   no terminator %d'
          % (len(chosen), len(stubs), errors, dirty))
    print('  %s lines of C in %d files, %.1fs' % (format(lines, ','), idx, time.time() - t0))
    print('=' * 60)


if __name__ == '__main__':
    main()
