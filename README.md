# Theme Park World — Static Recompilation

A static recompilation of Bullfrog's **Theme Park World** (1999), version
2.0, from its shipping Win32 binary to native C, targeting **macOS** first
and Linux after. Built on the [pcrecomp](https://github.com/sp00nznet/pcrecomp)
toolchain and its `runtime/win32hle`, with ideas borrowed from
[redalert2-recomp](https://github.com/sp00nznet/redalert2-recomp).

**Status: bring-up.** The whole executable lifts to C with no errors and
builds into a native Mac program that plays the intro and runs the front end
and its menus, drawn through DirectX 6's Direct3D at up to 2048x1536, with
the mouse and keyboard. No sound yet, and loading into a park stalls waiting
for the welcome speech. See [docs/bringup.md](docs/bringup.md) and
[docs/RECON.md](docs/RECON.md).

It needs pcrecomp's `main` (DirectX 6) and, until it is merged, the
`win32hle-dinput` branch (DirectInput).

## What you need

Nothing from the game is in this repository, and nothing generated from it.
You supply:

1. **The retail disc image, `TPWORLD.ISO`**, free from the Internet Archive:
   <https://archive.org/details/tpworld_202307>
   (MD5 `0a792a18af8b4d5fdbe4957e4eeaaf0a`).
2. **The 2.0 executable without SafeDisc**, `tp.exe`
   (MD5 `e4c2a8979d1a5d7e705976c1d4d511be`). The disc's executable is
   SafeDisc 1.35-encrypted, and its loader cannot run on a modern system;
   `tools/safedisc` emulates it far enough to pass the disc check, but not
   yet to the decrypted game (docs/RECON.md).

3. **The 2.0 patch**, `themeparkworld20patch.zip`, free from
   [ModDB](https://www.moddb.com/downloads/theme-park-world-20-patch).
   The 2.0 executable needs the 2.0 data (new fonts, an updated resource
   index, rides and levels); `tools/rtpatch/apply.py` applies it to the
   disc's files with the patch's own RTPatch applier, run headlessly, so
   there is no Windows installer to run. Its data cannot be shipped here.

## Building (macOS)

Prerequisites: Xcode's command line tools, Python 3.10+, and Homebrew's
`cmake ninja pkgconf sdl2 sdl2_ttf uv`. The pcrecomp toolkit is cloned
**beside** this repository:

```
some-folder/
  pcrecomp/      <- git clone https://github.com/sp00nznet/pcrecomp
  tpw-recomp/    <- this repository
```

1. Python packages:
   ```
   uv venv .venv && uv pip install --python .venv/bin/python pefile capstone unicorn
   ```
2. The game folder: the disc's files, patched to 2.0, with the 2.0 `tp.exe`
   over the SafeDisc loader (needs `p7zip`):
   ```
   mkdir -p work && 7z x -owork/cd TPWORLD.ISO
   .venv/bin/python -I tools/rtpatch/apply.py themeparkworld20patch.zip work/cd work/v20
   cp -Rc work/v20 game && cp /path/to/tp.exe game/TP.EXE && rm game/TP.ICD
   ```
   `apply.py` should end with `done`, all three patches returning 0.
3. Headers, imports and C++ classes (seconds):
   ```
   mkdir -p work/tp
   .venv/bin/python ../pcrecomp/tools/pe/pe_analyze.py game/tp.exe --json work/tp/pe_analysis.json
   .venv/bin/python ../pcrecomp/tools/cpp/rtti.py game/tp.exe -o work/tp/rtti.json --seeds work/tp/rtti_seeds.json
   ```
4. The function catalog (a few minutes): `Functions: 33994`, 94.8% coverage.
   ```
   .venv/bin/python ../pcrecomp/tools/disasm/disasm32.py game/tp.exe -o work/tp/functions.json --seed-functions work/tp/rtti_seeds.json
   ```
5. Code reached only through data tables, then the lift (under a minute):
   `lifted 34344   not-lifted stubs 0   errors 0`.
   ```
   .venv/bin/python -I tools/gen/data_code_ptrs.py game/TP.EXE work/tp/functions.json work/tp/run_seeds.json
   .venv/bin/python run_lift.py --all
   ```
6. Build and run:
   ```
   ./build-macos.sh
   build-macos/tpw --run --resolution 1024x768
   ```
   `--resolution` takes 512x384 up to 2048x1536 (4:3, the 2.0 patch's own
   choices), or `options` to leave it to the game.

## License

MIT for this repository's own code. Theme Park World is © Electronic Arts;
none of its files, and nothing generated from them, is in this repository.
