"""From the disc image and the 2.0 patch to a game that runs, in one go.

    .venv/bin/python -I tools/install/install.py --iso TPWORLD.ISO --patch themeparkworld20patch.zip

Every step of the README's build, into one folder (by default the Mac's
Application Support, where the app will look):

    game/             the disc's files patched to 2.0, the game unwrapped from
                      SafeDisc; its saves and settings are written here
    build/analysis/   the function catalog and the rest of the lift's inputs
    build/gen/        the lifted C
    build/host/       the compiled host: build/host/tpw
    install.json      what each part was made from

Run again, it redoes only what is out of date: after a fix to the host only
the compile (seconds, ninja), after a lifter fix the lift and the compile,
and only after a change to the disc's steps (DATA_VERSION) the whole of it,
which needs the disc image and the patch again. The game's saves are kept.

Progress goes to stdout as plain lines; `@step i n title` and
`@progress fraction` lines are for a front end to show.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'tools', 'disc'))

import iso  # noqa: E402

ISO_MD5 = '0a792a18af8b4d5fdbe4957e4eeaaf0a'
PATCH_MD5 = '30b872ae27c4a053ef9ca07c720ca461'
# The game unwrapped from the patched disc, as tools/safedisc/unwrap.py
# writes it: the same bytes on every Mac, so a catalog matches it.
GAME_MD5 = '3c4084844aec199c321d466b8032f4a0'
# Bumped when the disc's steps (extract, patch, unwrap) change what they make.
DATA_VERSION = 1
DEFAULT_DEST = os.path.expanduser('~/Library/Application Support/Theme Park World')
# What a player's run writes into the game folder, carried over a new one.
KEEP = ('save', os.path.join('data', 'Resolution.sam'))
SPACE_NEEDED = 3 << 30       # the disc twice over, the lift and the build, at their peak


class Failed(Exception):
    pass


def say(*a):
    print(*a, flush=True)


def md5(path, progress=None):
    h, size, done = hashlib.md5(), os.path.getsize(path), 0
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 << 20), b''):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done / size)
    return h.hexdigest()


def tree_hash(*paths):
    """A hash of source files: what a step's output depends on."""
    h = hashlib.sha1()
    for top in paths:
        files = [top] if os.path.isfile(top) else sorted(
            os.path.join(d, n) for d, _, ns in os.walk(top) for n in ns
            if n.endswith('.py') and '__pycache__' not in d)
        for p in files:
            h.update(os.path.relpath(p, os.path.dirname(top)).encode())
            with open(p, 'rb') as f:
                h.update(f.read().replace(b'\r\n', b'\n'))
    return h.hexdigest()[:16]


def find_pcrecomp(given):
    for p in (given, os.environ.get('PCRECOMP'), os.path.join(REPO, 'pcrecomp'),
              os.path.join(os.path.dirname(REPO), 'pcrecomp')):
        if p and os.path.isdir(os.path.join(p, 'runtime', 'win32hle')):
            return os.path.abspath(p)
    raise Failed('no pcrecomp found: give --pcrecomp, or clone it beside this repository (README)')


class Install:
    def __init__(self, a):
        self.a = a
        self.dest = os.path.abspath(a.dest)
        self.pcrecomp = find_pcrecomp(a.pcrecomp)
        self.game = os.path.join(self.dest, 'game')
        self.build = os.path.join(self.dest, 'build')
        self.analysis = os.path.join(self.build, 'analysis')
        self.gen = os.path.join(self.build, 'gen')
        self.host = os.path.join(self.build, 'host')
        self.tmp = os.path.join(self.dest, 'tmp')
        self.state_path = os.path.join(self.dest, 'install.json')
        try:
            with open(self.state_path) as f:
                self.state = json.load(f)
        except (OSError, ValueError):
            self.state = {}
        self.env = dict(os.environ, PCRECOMP=self.pcrecomp)

    # ---- bookkeeping ---------------------------------------------------

    def save_state(self):
        with open(self.state_path + '.new', 'w') as f:
            json.dump(self.state, f, indent=1)
        os.replace(self.state_path + '.new', self.state_path)

    def run(self, *cmd, cwd=None):
        """A step's command; its output is shown as it comes."""
        say('$ ' + ' '.join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        p = subprocess.run(cmd, cwd=cwd or REPO, env=self.env)
        if p.returncode:
            raise Failed(f'{os.path.basename(cmd[0])} {cmd[1] if len(cmd) > 1 else ""} failed ({p.returncode})')

    def py(self, script, *args):
        self.run(sys.executable, '-I', script, *args)

    # ---- the keys: what each part was made from -------------------------

    def keys(self):
        tools = os.path.join(self.pcrecomp, 'tools')
        data = f'data-{DATA_VERSION}'
        analysis = tree_hash(os.path.join(tools, 'pe'), os.path.join(tools, 'cpp'),
                             os.path.join(tools, 'disasm'), os.path.join(REPO, 'tools', 'gen'))
        lift = tree_hash(os.path.join(tools, 'lift'), os.path.join(REPO, 'run_lift.py'))
        return {'data': data,
                'analysis': f'{data}/{analysis}',
                'lift': f'{data}/{analysis}/{lift}'}

    # ---- the steps -------------------------------------------------------

    def check(self):
        """The toolchain, the space, and the inputs when the disc's steps must run."""
        missing = [m for m in ('pefile', 'capstone', 'unicorn')
                   if subprocess.run([sys.executable, '-I', '-c', f'import {m}'],
                                     capture_output=True).returncode]
        if missing:
            raise Failed(f'this Python lacks {", ".join(missing)} (README: Python packages)')
        if subprocess.run(['xcrun', '--find', 'clang'], capture_output=True).returncode:
            raise Failed("no C compiler: install Xcode's command line tools (xcode-select --install)")
        for tool in ('cmake', 'ninja', 'pkg-config'):
            if not shutil.which(tool):
                raise Failed(f'no {tool} on the PATH (README: prerequisites)')
        if subprocess.run(['pkg-config', '--exists', 'sdl2', 'SDL2_ttf']).returncode:
            raise Failed('pkg-config cannot find sdl2 and SDL2_ttf')
        os.makedirs(self.dest, exist_ok=True)
        free = shutil.disk_usage(self.dest).free
        if free < SPACE_NEEDED:
            raise Failed(f'{free >> 20} MB free at {self.dest}; it needs about {SPACE_NEEDED >> 30} GB')

    def need_inputs(self):
        for what, path, want in (('disc image', self.a.iso, ISO_MD5), ('2.0 patch', self.a.patch, PATCH_MD5)):
            if not path:
                raise Failed(f'the game must be set up from the start: give --iso and --patch')
            if not os.path.isfile(path):
                raise Failed(f'no {what} at {path}')
            say(f'checking the {what}...')
            got = md5(path, lambda f: say(f'@progress {f:.3f}'))
            if got != want:
                raise Failed(f'{path} is not the {what} this needs (MD5 {got}, wanted {want}); see README')

    def disc(self):
        """Extract, patch to 2.0, unwrap: a new game folder, saves carried over."""
        self.need_inputs()
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp)
        cd, v20, exe = (os.path.join(self.tmp, n) for n in ('cd', 'v20', 'TP.EXE'))
        say('extracting the disc...')
        n = iso.extract(self.a.iso, cd, lambda d, t: say(f'@progress {d / t:.3f}'))
        say(f'{n} files')
        self.py(os.path.join(REPO, 'tools', 'rtpatch', 'apply.py'), self.a.patch, cd, v20)
        shutil.rmtree(cd)
        self.py(os.path.join(REPO, 'tools', 'safedisc', 'unwrap.py'), '--cd', v20, '--iso', self.a.iso,
                '-o', exe, '--work', os.path.join(self.tmp, 'unwrap'))
        got = md5(exe)
        if got != GAME_MD5:
            raise Failed(f'the unwrapped game is not the expected one (MD5 {got}, wanted {GAME_MD5})')
        os.replace(exe, os.path.join(v20, 'TP.EXE'))
        os.remove(os.path.join(v20, 'TP.ICD'))
        for rel in KEEP:
            old = os.path.join(self.game, rel)
            if os.path.exists(old):
                new = os.path.join(v20, rel)
                shutil.rmtree(new, ignore_errors=True) if os.path.isdir(new) else None
                os.makedirs(os.path.dirname(new), exist_ok=True)
                os.replace(old, new)
        if os.path.exists(self.game):
            os.replace(self.game, os.path.join(self.tmp, 'old-game'))
        os.replace(v20, self.game)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def analyse(self):
        """The headers and imports, C++ classes, the function catalog, code in data."""
        tools, exe, out = os.path.join(self.pcrecomp, 'tools'), os.path.join(self.game, 'TP.EXE'), self.analysis
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        self.py(os.path.join(tools, 'pe', 'pe_analyze.py'), exe, '--json', os.path.join(out, 'pe_analysis.json'))
        self.py(os.path.join(tools, 'cpp', 'rtti.py'), exe, '-o', os.path.join(out, 'rtti.json'),
                '--seeds', os.path.join(out, 'rtti_seeds.json'))
        say('cataloguing the functions (a few minutes)...')
        self.py(os.path.join(tools, 'disasm', 'disasm32.py'), exe, '-o', os.path.join(out, 'functions.json'),
                '--seed-functions', os.path.join(out, 'rtti_seeds.json'))
        self.py(os.path.join(REPO, 'tools', 'gen', 'data_code_ptrs.py'), exe,
                os.path.join(out, 'functions.json'), os.path.join(out, 'run_seeds.json'))

    def lift(self):
        shutil.rmtree(self.gen, ignore_errors=True)
        os.makedirs(self.gen)
        self.py(os.path.join(REPO, 'run_lift.py'), '--all', '--exe', os.path.join(self.game, 'TP.EXE'),
                '--work', self.analysis, '--out', self.gen)

    def compile(self):
        if not os.path.exists(os.path.join(self.host, 'build.ninja')):
            self.run('cmake', '-S', REPO, '-B', self.host, '-G', 'Ninja',
                     f'-DPCRECOMP={self.pcrecomp}', f'-DGEN_DIR={self.gen}')
        say('compiling (a few minutes the first time)...')
        self.run('cmake', '--build', self.host)

    # ---- the order -------------------------------------------------------

    def go(self):
        keys = self.keys()
        if self.a.force:
            self.state = {}
        if not os.path.isdir(self.game):
            self.state.pop('data', None)
        steps = [('Checking the tools', self.check, None),
                 ('Unpacking, patching and unwrapping the game', self.disc, 'data'),
                 ('Analysing the game', self.analyse, 'analysis'),
                 ('Translating the game to C', self.lift, 'lift'),
                 ('Compiling', self.compile, None)]
        # A part is redone when its key changed, and so is everything after it.
        stale = False
        todo = []
        for title, fn, key in steps:
            stale = stale or (key is not None and self.state.get(key) != keys[key])
            if key is None or stale:
                todo.append((title, fn, key))
        for i, (title, fn, key) in enumerate(todo, 1):
            say(f'@step {i} {len(todo)} {title}')
            say(f'==> [{i}/{len(todo)}] {title}')
            t = time.time()
            if key:
                self.state.pop(key, None)     # half-made until it finishes
                self.save_state()
            fn()
            if key:
                self.state[key] = keys[key]
                self.save_state()
            say(f'    {title.lower()}: {time.time() - t:.0f} s')
        tpw = os.path.join(self.host, 'tpw')
        say('done. Play with:')
        say(f'  "{tpw}" --game "{self.game}" --run --resolution native')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--iso', help='TPWORLD.ISO (needed the first time)')
    ap.add_argument('--patch', help='themeparkworld20patch.zip (needed the first time)')
    ap.add_argument('--dest', default=DEFAULT_DEST, help='where it goes (default: %(default)s)')
    ap.add_argument('--pcrecomp', help='the pcrecomp checkout (default: ./pcrecomp, then ../pcrecomp)')
    ap.add_argument('--force', action='store_true', help='redo every step')
    a = ap.parse_args()
    try:
        Install(a).go()
    except Failed as e:
        say(f'@error {e}')
        say(f'error: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
