"""From the disc image and the 2.0 patch to a game that runs, in one go.

    .venv/bin/python -I tools/install/install.py --iso TPWORLD.ISO --patch themeparkworld20patch.zip

Every step of the README's build, into one folder (by default the Mac's
Application Support, where Theme Park World.app looks; on Linux
$XDG_DATA_HOME/theme-park-world):

    game/             the disc's files patched to 2.0, the game unwrapped from
                      SafeDisc; its saves and settings are written here
    build/src/        the sources it builds from, copied from this repository
                      and pcrecomp (SOURCES), so the build's paths never move
    build/analysis/   the function catalog and the rest of the lift's inputs
    build/gen/        the lifted C
    build/host/       the compiled game: libtpw.dylib (.so), which the app loads
                      (app/Launcher), and tpw, the same as a program
    install.json      what each part was made from

Run again, it redoes only what is out of date: after a fix to the host only
the compile (ninja: what changed), after a lifter fix the lift and the
compile, and only after a change to the disc's steps (DATA_VERSION) the
whole of it, which needs the disc image and the patch again. The game's
saves are kept. --status says what a run would redo, as JSON.

Theme Park World.app runs this with the Python it carries (app/toolchain.sh),
whose packages include CMake and Ninja, and --sdl-frameworks for its SDL.
Without those, CMake, Ninja and SDL come from the PATH and pkg-config.

Progress goes to stdout as plain lines; `@step i n title`, `@progress
fraction` and `@error text` lines are for a front end to show.
"""
import argparse
import filecmp
import hashlib
import json
import os
import shutil
import subprocess
import ssl
import sys
import time
import urllib.error
import urllib.request

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
if sys.platform == 'darwin':
    DEFAULT_DEST = os.path.expanduser('~/Library/Application Support/Theme Park World')
    LIBRARY = 'libtpw.dylib'
else:
    DEFAULT_DEST = os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'),
                                'theme-park-world')
    LIBRARY = 'libtpw.so'
# What a player's run writes into the game folder, carried over a new one.
KEEP = ('save', os.path.join('Data', 'Resolution.sam'))   # as the disc spells them: Linux minds the case
SPACE_NEEDED = 3 << 30       # the disc twice over, the lift and the build, at their peak

# --download: the two files, from where this project keeps copies of them
# (TPW_DOWNLOAD_BASE: elsewhere). Checked against the same MD5s as ever.
DOWNLOAD_BASE = os.environ.get('TPW_DOWNLOAD_BASE', 'https://files.cpressland.io/public/tpw-recomp')
DOWNLOADS = (('disc image', 'theme_park_world.iso', ISO_MD5, 'iso'),
             ('2.0 patch', 'theme_park_world_2_0_patch.zip', PATCH_MD5, 'patch'))

# What the build needs of each tree: what the app carries, and what is staged.
SOURCES = {'tpw': ('CMakeLists.txt', 'run_lift.py', 'src/host', 'tools'),
           'pcrecomp': ('runtime', 'tools', 'LICENSE')}
SDL_FRAMEWORKS = ('SDL2.framework', 'SDL2_ttf.framework')


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


def _files(top):
    """Every file under a folder (or the file itself), less Python's caches."""
    if os.path.isfile(top):
        return [top]
    return sorted(os.path.join(d, n) for d, ds, ns in os.walk(top) for n in ns
                  if '__pycache__' not in d.split(os.sep) and not n.endswith('.pyc') and n != '.DS_Store')


def tree_hash(*paths):
    """A hash of source files: what a step's output depends on."""
    h = hashlib.sha1()
    for top in paths:
        for p in _files(top):
            h.update(os.path.relpath(p, os.path.dirname(top)).encode())
            with open(p, 'rb') as f:
                h.update(f.read().replace(b'\r\n', b'\n'))
    return h.hexdigest()[:16]


def sync(src, rels, dst):
    """Make dst/<rel> match src/<rel>: a file is written only when its bytes
    differ, so its time says when it last changed, whatever the source's says
    (an update's files can be older than the last build). Returns the count."""
    changed = 0
    for rel in rels:
        s, d = os.path.join(src, rel), os.path.join(dst, rel)
        want = {os.path.relpath(p, s) if os.path.isdir(s) else '' for p in _files(s)} if os.path.exists(s) else set()
        if os.path.isdir(d):
            for p in _files(d):
                if os.path.relpath(p, d) not in want:
                    os.remove(p)
                    changed += 1
        for r in sorted(want):
            sp, dp = os.path.join(s, r) if r else s, os.path.join(d, r) if r else d
            if os.path.isfile(dp) and filecmp.cmp(sp, dp, shallow=False):
                continue
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copyfile(sp, dp)
            shutil.copymode(sp, dp)
            changed += 1
    return changed


def find_pcrecomp(given):
    for p in (given, os.environ.get('PCRECOMP'), os.path.join(REPO, 'pcrecomp'),
              os.path.join(os.path.dirname(REPO), 'pcrecomp')):
        if p and os.path.isdir(os.path.join(p, 'runtime', 'win32hle')):
            return os.path.abspath(p)
    raise Failed('no pcrecomp found: give --pcrecomp, or clone it beside this repository (README)')


def find_tools():
    """CMake and Ninja: this Python's own packages first (the app's), then the PATH."""
    try:
        import cmake
        cm = os.path.join(cmake.CMAKE_BIN_DIR, 'cmake')
    except ImportError:
        cm = shutil.which('cmake')
    nj = os.path.join(os.path.dirname(sys.executable), 'ninja')
    if not os.access(nj, os.X_OK):
        nj = shutil.which('ninja')
    return cm, nj


class Install:
    def __init__(self, a):
        self.a = a
        self.dest = os.path.abspath(a.dest)
        self.pcrecomp = find_pcrecomp(a.pcrecomp)
        self.game = os.path.join(self.dest, 'game')
        self.build = os.path.join(self.dest, 'build')
        self.src = os.path.join(self.build, 'src')
        self.tpw_src = os.path.join(self.src, 'tpw')
        self.pc_src = os.path.join(self.src, 'pcrecomp')
        self.sdl = os.path.join(self.src, 'Frameworks') if a.sdl_frameworks else None
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
        self.env = dict(os.environ, PCRECOMP=self.pc_src)

    # ---- bookkeeping ---------------------------------------------------

    def save_state(self):
        with open(self.state_path + '.new', 'w') as f:
            json.dump(self.state, f, indent=1)
        os.replace(self.state_path + '.new', self.state_path)

    def run(self, *cmd):
        """A step's command; its output is shown as it comes."""
        say('$ ' + ' '.join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        p = subprocess.run(cmd, cwd=self.tpw_src, env=self.env)
        if p.returncode:
            raise Failed(f'{os.path.basename(cmd[0])} {cmd[1] if len(cmd) > 1 else ""} failed ({p.returncode})')

    def py(self, script, *args):
        self.run(sys.executable, '-I', script, *args)

    # ---- the keys: what each part was made from -------------------------

    def keys(self):
        """From the sources given (the repository's, or the app's), not the staged copy."""
        tools = os.path.join(self.pcrecomp, 'tools')
        data = f'data-{DATA_VERSION}'
        analysis = tree_hash(os.path.join(tools, 'pe'), os.path.join(tools, 'cpp'),
                             os.path.join(tools, 'disasm'), os.path.join(REPO, 'tools', 'gen'))
        lift = tree_hash(os.path.join(tools, 'lift'), os.path.join(REPO, 'run_lift.py'))
        host = tree_hash(os.path.join(REPO, 'CMakeLists.txt'), os.path.join(REPO, 'src', 'host'),
                         os.path.join(self.pcrecomp, 'runtime'),
                         *(os.path.join(self.a.sdl_frameworks, f) for f in SDL_FRAMEWORKS if self.a.sdl_frameworks))
        return {'data': data,
                'analysis': f'{data}/{analysis}',
                'lift': f'{data}/{analysis}/{lift}',
                'host': f'{data}/{analysis}/{lift}/{host}'}

    def stale(self, keys):
        """The parts out of date, in order: one stale part makes all after it so."""
        if not os.path.isdir(self.game):
            self.state.pop('data', None)
        if not os.path.exists(os.path.join(self.host, LIBRARY)):
            self.state.pop('host', None)
        out = []
        for k in ('data', 'analysis', 'lift', 'host'):
            if out or self.state.get(k) != keys[k]:
                out.append(k)
        return out

    # ---- the steps -------------------------------------------------------

    def check(self):
        """The toolchain and the space; the inputs are checked when the disc's steps run."""
        missing = [m for m in ('pefile', 'capstone', 'unicorn')
                   if subprocess.run([sys.executable, '-I', '-c', f'import {m}'],
                                     capture_output=True).returncode]
        if missing:
            raise Failed(f'this Python lacks {", ".join(missing)} (README: Python packages)')
        if sys.platform == 'darwin':
            if subprocess.run(['xcrun', '--find', 'clang'], capture_output=True).returncode:
                raise Failed("no C compiler: install Xcode's command line tools (xcode-select --install)")
        elif not (os.environ.get('CC') or shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')):
            raise Failed('no C compiler: install clang (or gcc), e.g. the build-essential package')
        self.cmake, self.ninja = find_tools()
        if not self.cmake or not self.ninja:
            raise Failed('no cmake or ninja (README: prerequisites)')
        if self.a.sdl_frameworks:
            for f in SDL_FRAMEWORKS:
                if not os.path.isdir(os.path.join(self.a.sdl_frameworks, f)):
                    raise Failed(f'no {f} in {self.a.sdl_frameworks}')
        elif subprocess.run(['pkg-config', '--exists', 'sdl2', 'SDL2_ttf']).returncode:
            raise Failed('pkg-config cannot find sdl2 and SDL2_ttf (README: prerequisites)')
        os.makedirs(self.dest, exist_ok=True)
        free = shutil.disk_usage(self.dest).free
        if free < SPACE_NEEDED and not os.path.isdir(self.host):
            raise Failed(f'{free >> 20} MB free at {self.dest}; it needs about {SPACE_NEEDED >> 30} GB')

    def stage(self):
        """The sources, and SDL, copied to where the build always finds them."""
        n = sync(REPO, SOURCES['tpw'], self.tpw_src) + sync(self.pcrecomp, SOURCES['pcrecomp'], self.pc_src)
        # Frameworks are copied whole, links and all, when they differ at all.
        for f in SDL_FRAMEWORKS if self.sdl else ():
            src, dst = os.path.join(self.a.sdl_frameworks, f), os.path.join(self.sdl, f)
            if not os.path.isdir(dst) or tree_hash(src) != tree_hash(dst):
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst, symlinks=True)
                n += 1
        say(f'{n} source files changed')

    def download(self):
        """The disc image and the patch, into downloads/: resumed where a last
        try stopped, checked, and kept only until the game folder is made."""
        folder = os.path.join(self.dest, 'downloads')
        os.makedirs(folder, exist_ok=True)
        try:
            ctx = ssl.create_default_context()
            urllib.request.urlopen(DOWNLOAD_BASE + '/' + DOWNLOADS[1][1], context=ctx, timeout=30).close()
        except ssl.SSLError:
            ctx = ssl.create_default_context(cafile='/etc/ssl/cert.pem')    # macOS's own roots
        except (urllib.error.URLError, OSError) as e:
            raise Failed(f'cannot reach {DOWNLOAD_BASE}: {getattr(e, "reason", e)}')
        sizes = {}
        for what, name, want, _ in DOWNLOADS:
            with urllib.request.urlopen(urllib.request.Request(f'{DOWNLOAD_BASE}/{name}', method='HEAD'),
                                        context=ctx, timeout=30) as r:
                sizes[name] = int(r.headers['Content-Length'])
        total, before = sum(sizes.values()), 0
        for what, name, want, arg in DOWNLOADS:
            path = os.path.join(folder, name)
            have = os.path.getsize(path) if os.path.exists(path) else 0
            if have > sizes[name]:
                os.remove(path)
                have = 0
            if have < sizes[name]:
                say(f'downloading the {what} ({sizes[name] >> 20} MB)...')
                req = urllib.request.Request(f'{DOWNLOAD_BASE}/{name}')
                if have:
                    req.add_header('Range', f'bytes={have}-')
                    say(f'  resuming at {have >> 20} MB')
                try:
                    with urllib.request.urlopen(req, context=ctx, timeout=60) as r, \
                            open(path, 'ab' if have and r.status == 206 else 'wb') as f:
                        if r.status != 206:
                            have = 0
                        last = 0.0
                        for chunk in iter(lambda: r.read(1 << 20), b''):
                            f.write(chunk)
                            have += len(chunk)
                            if time.time() - last > 0.25:
                                last = time.time()
                                say(f'@progress {(before + have) / total:.3f}')
                except (urllib.error.URLError, OSError) as e:
                    raise Failed(f'the download of the {what} stopped ({getattr(e, "reason", e)}); '
                                 'trying again carries on from there')
            say(f'checking the {what}...')
            got = md5(path)
            if got != want:
                os.remove(path)
                raise Failed(f'the downloaded {what} is not the expected file (MD5 {got[:8]}…, '
                             f'wanted {want[:8]}…); trying again downloads it afresh')
            setattr(self.a, arg, path)
            before += sizes[name]
        say('@progress 1')

    def need_inputs(self):
        for what, path, want in (('disc image', self.a.iso, ISO_MD5), ('2.0 patch', self.a.patch, PATCH_MD5)):
            if not path:
                raise Failed('the game must be set up from the disc: give --iso and --patch, or --download')
            if not os.path.isfile(path):
                raise Failed(f'no {what} at {path}')
            say(f'checking the {what}...')
            got = md5(path, lambda f: say(f'@progress {f:.3f}'))
            if got != want:
                raise Failed(f'{os.path.basename(path)} is not the {what} this needs: check it is the '
                             f'right file, downloaded whole (MD5 {got[:8]}…, wanted {want[:8]}…)')

    def disc(self):
        """Extract, patch to 2.0, unwrap: a new game folder, saves carried over."""
        self.need_inputs()
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp)
        cd, v20, exe = (os.path.join(self.tmp, n) for n in ('cd', 'v20', 'TP.EXE'))
        say('extracting the disc...')
        n = iso.extract(self.a.iso, cd, lambda d, t: say(f'@progress {d / t:.3f}'))
        say(f'{n} files')
        say('@progress -1')
        self.py(os.path.join(self.tpw_src, 'tools', 'rtpatch', 'apply.py'), self.a.patch, cd, v20)
        shutil.rmtree(cd)
        self.py(os.path.join(self.tpw_src, 'tools', 'safedisc', 'unwrap.py'), '--cd', v20, '--iso', self.a.iso,
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
                if os.path.isdir(new):
                    shutil.rmtree(new)
                os.makedirs(os.path.dirname(new), exist_ok=True)
                os.replace(old, new)
        if os.path.exists(self.game):
            os.replace(self.game, os.path.join(self.tmp, 'old-game'))
        os.replace(v20, self.game)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(os.path.join(self.dest, 'downloads'), ignore_errors=True)

    def analyse(self):
        """The headers and imports, C++ classes, the function catalog, code in data."""
        tools, exe, out = os.path.join(self.pc_src, 'tools'), os.path.join(self.game, 'TP.EXE'), self.analysis
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        self.py(os.path.join(tools, 'pe', 'pe_analyze.py'), exe, '--json', os.path.join(out, 'pe_analysis.json'))
        self.py(os.path.join(tools, 'cpp', 'rtti.py'), exe, '-o', os.path.join(out, 'rtti.json'),
                '--seeds', os.path.join(out, 'rtti_seeds.json'))
        say('cataloguing the functions (a few minutes)...')
        self.py(os.path.join(tools, 'disasm', 'disasm32.py'), exe, '-o', os.path.join(out, 'functions.json'),
                '--seed-functions', os.path.join(out, 'rtti_seeds.json'))
        self.py(os.path.join(self.tpw_src, 'tools', 'gen', 'data_code_ptrs.py'), exe,
                os.path.join(out, 'functions.json'), os.path.join(out, 'run_seeds.json'))

    def lift(self):
        shutil.rmtree(self.gen, ignore_errors=True)
        os.makedirs(self.gen)
        self.py(os.path.join(self.tpw_src, 'run_lift.py'), '--all', '--exe', os.path.join(self.game, 'TP.EXE'),
                '--work', self.analysis, '--out', self.gen)

    def compile(self):
        # A build folder made another way (other sources, other SDL) starts again.
        config = [self.tpw_src, self.pc_src, self.sdl or 'pkg-config', self.cmake]
        if self.state.get('host_config') != config:
            shutil.rmtree(self.host, ignore_errors=True)
        if not os.path.exists(os.path.join(self.host, 'build.ninja')):
            self.run(self.cmake, '-S', self.tpw_src, '-B', self.host, '-G', 'Ninja',
                     f'-DCMAKE_MAKE_PROGRAM={self.ninja}', f'-DPCRECOMP={self.pc_src}', f'-DGEN_DIR={self.gen}',
                     f'-DTPW_SDL_FRAMEWORKS={self.sdl or ""}')
            self.state['host_config'] = config
            self.save_state()
        say('compiling (a few minutes the first time)...')
        self.run(self.cmake, '--build', self.host)

    # ---- the order -------------------------------------------------------

    def status(self):
        keys = self.keys()
        stale = self.stale(keys)
        say(json.dumps({'ready': not stale, 'stale': stale, 'needs_disc': 'data' in stale}))

    def go(self):
        keys = self.keys()
        if self.a.force:
            self.state = {}
        stale = self.stale(keys)
        # Downloading only when the disc's steps run and no files were given.
        fetch = 'data' in stale and self.a.download and not (self.a.iso and self.a.patch)
        steps = [('Checking the tools', self.check, None),
                 ('Copying the sources', self.stage, None),
                 *([('Downloading the game', self.download, None)] if fetch else []),
                 ('Unpacking, patching and unwrapping the game', self.disc, 'data'),
                 ('Analysing the game', self.analyse, 'analysis'),
                 ('Translating the game to C', self.lift, 'lift'),
                 ('Compiling', self.compile, 'host')]
        todo = [s for s in steps if s[2] is None or s[2] in stale]
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
        say('@done')
        tpw = os.path.join(self.host, 'tpw')
        say('done. Play with Theme Park World.app (app/build-app.sh), or from a terminal:' if sys.platform == 'darwin'
            else 'done. Play from a terminal:')
        say(f'  "{tpw}" --game "{self.game}" --run --resolution native')


def export(out, pcrecomp):
    """The sources the app carries: SOURCES of this repository and pcrecomp."""
    n = sync(REPO, SOURCES['tpw'], os.path.join(out, 'tpw')) + \
        sync(find_pcrecomp(pcrecomp), SOURCES['pcrecomp'], os.path.join(out, 'pcrecomp'))
    say(f'{n} files to {out}')


def main():
    if os.environ.get('TPW_PGROUP'):
        os.setpgrp()       # the app stops the whole build (compilers too) through the group
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--iso', help='TPWORLD.ISO (needed the first time)')
    ap.add_argument('--patch', help='themeparkworld20patch.zip (needed the first time)')
    ap.add_argument('--download', action='store_true',
                    help='with no --iso and --patch, download both (TPW_DOWNLOAD_BASE)')
    ap.add_argument('--dest', default=DEFAULT_DEST, help='where it goes (default: %(default)s)')
    ap.add_argument('--pcrecomp', help='the pcrecomp checkout (default: ./pcrecomp, then ../pcrecomp)')
    ap.add_argument('--sdl-frameworks', help='a folder with SDL2.framework and SDL2_ttf.framework '
                    '(default: SDL from pkg-config)')
    ap.add_argument('--force', action='store_true', help='redo every step')
    ap.add_argument('--status', action='store_true', help='print what a run would redo, as JSON')
    ap.add_argument('--export-sources', metavar='DIR', help='copy the sources the build needs to DIR, and stop')
    a = ap.parse_args()
    if a.sdl_frameworks:
        a.sdl_frameworks = os.path.abspath(a.sdl_frameworks)
    try:
        if a.export_sources:
            export(a.export_sources, a.pcrecomp)
        elif a.status:
            Install(a).status()
        else:
            Install(a).go()
    except Failed as e:
        say(f'@error {e}')
        say(f'error: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
