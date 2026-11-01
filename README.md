# Theme Park World — Static Recompilation

A static recompilation of Bullfrog's **Theme Park World** (1999), version
2.0, from its shipping Win32 binary to native C, targeting **macOS** first
and Linux after. Built on the [pcrecomp](https://github.com/sp00nznet/pcrecomp)
toolchain and its `runtime/win32hle`, with ideas borrowed from
[redalert2-recomp](https://github.com/sp00nznet/redalert2-recomp).

**Status: playable, early.** The whole executable lifts to C with no errors
and builds into a native Mac program: the intro, the menus, and a park that
loads and runs, with visitors, money and the advisor, music, speech and
sound effects, drawn through DirectX 6's Direct3D at up to 2048x1536 and
played with the mouse and keyboard. See [docs/bringup.md](docs/bringup.md)
and [docs/RECON.md](docs/RECON.md).

It needs pcrecomp's `main` (DirectX 6) plus four win32hle changes that are
still awaiting review upstream; see [pcrecomp: pending merges](#pcrecomp-pending-merges).

## What you need

Nothing from the game is in this repository, and nothing generated from it.
You supply two free downloads:

1. **The retail disc image, `TPWORLD.ISO`**, from the Internet Archive:
   <https://archive.org/details/tpworld_202307>
   (MD5 `0a792a18af8b4d5fdbe4957e4eeaaf0a`).
2. **The 2.0 patch**, `themeparkworld20patch.zip`, from
   [ModDB](https://www.moddb.com/downloads/theme-park-world-20-patch).
   `tools/rtpatch/apply.py` applies it to the disc's files with the patch's
   own RTPatch applier, run headlessly, so there is no Windows installer to
   run. Its data cannot be shipped here.

The game's executable is protected by SafeDisc 1.41, whose loader cannot
run on a modern system. `tools/safedisc/unwrap.py` runs it headlessly under
an emulator against the disc image, lets it decrypt the game, and writes the
game back out as a plain executable (docs/RECON.md). No cracked executable
is needed. The 2000-era no-CD `tp.exe`
(MD5 `e4c2a8979d1a5d7e705976c1d4d511be`) still works in its place.

## pcrecomp: pending merges

Some of what this game needs from win32hle is in open pull requests on
[sp00nznet/pcrecomp](https://github.com/sp00nznet/pcrecomp). Until they are
merged, pcrecomp's `main` alone won't build this host, and without them the
game wouldn't get past a park's loading screen or respond to the mouse.

| PR | Branch | What it adds | Needed? |
|----|--------|--------------|---------|
| [#159](https://github.com/sp00nznet/pcrecomp/pull/159) | `win32hle-dinput` | DirectInput 3/5: the system mouse and keyboard | **Yes.** Without it the game's cursor never moves, so no menu can be used. |
| [#160](https://github.com/sp00nznet/pcrecomp/pull/160) | `win32hle-gdi-lfwidth` | A font's `lfWidth` squeezes or stretches its text | **Yes.** Without it the ride-sign fitting loop never ends and a park never loads. |
| [#161](https://github.com/sp00nznet/pcrecomp/pull/161) | `win32hle-mouse-confine` | The mouse held inside the picture while the window has focus (Ctrl+Cmd lets go) | **Yes.** The host calls its `hle_screen_confine`; without it the build fails, and the pointer escapes the window. |
| [#163](https://github.com/sp00nznet/pcrecomp/pull/163) | `win32hle-metal` | Direct3D on the GPU through Metal | **Yes.** The host builds `d3d_metal.m` and calls `hle_d3d_metal_enable`; without it the build fails. |
| [#164](https://github.com/sp00nznet/pcrecomp/pull/164) | `win32hle-dsound-cursor` | DirectSound's play cursor moves smoothly between mixes | No, but recommended. Without it the intro movies lose a third of a second of sound, now and then. |
| [#158](https://github.com/sp00nznet/pcrecomp/pull/158) | `win32hle-dx6-followups` | DirectDraw 4 review follow-ups (resized textures, attached z-buffers, uniqueness values) | No. Correctness fixes from #154's review; TPW runs without them. |

To build against them, merge the needed branches into a local branch of
your pcrecomp clone. They merge cleanly into `main`. `win32hle-metal` and
`win32hle-dinput` each add a row at the same place in
`runtime/win32hle/README.md`, so merge `win32hle-metal` on its own and keep
both rows there:

```
cd ../pcrecomp
git remote add cpressland https://github.com/cpressland/pcrecomp
git fetch origin && git fetch cpressland
git switch -c tpw-local origin/main
git merge --no-edit cpressland/win32hle-dinput cpressland/win32hle-gdi-lfwidth cpressland/win32hle-mouse-confine cpressland/win32hle-dsound-cursor
git merge --no-edit cpressland/win32hle-metal    # keep both README rows, then:
git add runtime/win32hle/README.md && git commit --no-edit
```

When a PR is merged, drop its branch from that list (and from this table);
once all are, build against plain `main`.

## Building (macOS)

Prerequisites: Xcode's command line tools, Python 3.10+, and Homebrew's
`cmake ninja pkgconf sdl2 sdl2_ttf uv`. The pcrecomp toolkit is cloned
**beside** this repository:

```
some-folder/
  pcrecomp/      <- git clone https://github.com/sp00nznet/pcrecomp
  tpw-recomp/    <- this repository
```

with the pending branches merged in, as above.

1. Python packages:
   ```
   uv venv .venv && uv pip install --python .venv/bin/python pefile capstone unicorn
   ```
2. The game folder: the disc's files, patched to 2.0, with the game
   unwrapped from SafeDisc over its loader (needs `p7zip`):
   ```
   mkdir -p work && 7z x -owork/cd TPWORLD.ISO
   .venv/bin/python -I tools/rtpatch/apply.py themeparkworld20patch.zip work/cd work/v20
   .venv/bin/python -I tools/safedisc/unwrap.py --cd work/v20 --iso TPWORLD.ISO -o work/unwrap/TP.EXE
   cp -Rc work/v20 game && cp work/unwrap/TP.EXE game/TP.EXE && rm game/TP.ICD
   ```
   `apply.py` should end with `done`, all three patches returning 0.
   `unwrap.py` takes about a minute and should report `125` KERNEL32 and
   `38` USER32 imports behind SafeDisc stubs, `585 call sites` and name key
   `0x7c8d8b73`.
3. Headers, imports and C++ classes (seconds):
   ```
   mkdir -p work/tp
   .venv/bin/python ../pcrecomp/tools/pe/pe_analyze.py game/tp.exe --json work/tp/pe_analysis.json
   .venv/bin/python ../pcrecomp/tools/cpp/rtti.py game/tp.exe -o work/tp/rtti.json --seeds work/tp/rtti_seeds.json
   ```
4. The function catalog (a few minutes): `Functions: 34444`, 94.8% coverage.
   ```
   .venv/bin/python ../pcrecomp/tools/disasm/disasm32.py game/tp.exe -o work/tp/functions.json --seed-functions work/tp/rtti_seeds.json
   ```
5. Code reached only through data tables, then the lift (under a minute):
   `lifted 34790   not-lifted stubs 0   errors 0`.
   ```
   .venv/bin/python -I tools/gen/data_code_ptrs.py game/TP.EXE work/tp/functions.json work/tp/run_seeds.json
   .venv/bin/python run_lift.py --all
   ```
6. Build and run:
   ```
   ./build-macos.sh
   build-macos/tpw --run --resolution 1024x768
   ```
   `--resolution` takes any `WxH` from 640x480 up, widescreen included
   (`1920x1080`, `3440x1440`), `native` for the desktop's size, or `options`
   to leave it to the game's Options screen. It is remembered for later runs.
   The game's own sizes (512x384 to 2048x1536, 4:3) go through its own
   setting; any other size is set where the game sets its screen size (a lift
   patch). Widescreen shows more of the park left and right, and the HUD
   keeps its 4:3 shape, held to the screen's corners and centre
   (`TPW_STRETCH_HUD=1` stretches it as the game would). Loading screens and
   movies keep 4:3 too, with black at the sides.
   Direct3D draws on the GPU through Metal (pcrecomp's `d3d_metal.m`);
   `--renderer software` draws on the CPU instead, as before, which is slow
   at large sizes.

   The game opens full screen, in its own Space on macOS (`--windowed` for
   a window; F11 or Alt+Enter toggles). The game draws its own cursor, so the
   mouse is held inside the picture while the game has focus: Ctrl+Cmd lets
   it go, a click takes it back, and switching apps lets it go too
   (`--free-mouse` turns this off). F12 cycles the scaling.

## License

MIT for this repository's own code. Theme Park World is © Electronic Arts;
none of its files, and nothing generated from them, is in this repository.
