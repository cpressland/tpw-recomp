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

It needs pcrecomp's `main` plus win32hle changes still awaiting review
upstream (Metal, Vulkan, and input a controller needs); see
[pcrecomp: pending merges](#pcrecomp-pending-merges).

## Playing on a Mac

Theme Park World.app needs a Mac with Apple silicon and macOS 26 or later.
Nothing of the game is in the app: the first time it runs, it downloads the
disc image and the 2.0 patch (630 MB, from `files.cpressland.io`) and builds
the game on your Mac from them, which takes about ten minutes.

Either paste this into Terminal:

```
curl -fsSL https://github.com/cpressland/tpw-recomp/releases/latest/download/install.sh | sh
```

or download `Theme-Park-World.dmg` from the
[latest release](https://github.com/cpressland/tpw-recomp/releases/latest)
and drag the app to Applications. The app isn't notarized by Apple, so the
first time you open a downloaded copy macOS refuses it: open it once, then
in **System Settings → Privacy & Security** choose **Open Anyway**. The
Terminal install skips that step.

The first time it opens, the app offers to install Apple's Command Line Tools
if this Mac doesn't have them (the compiler; Xcode isn't needed), then
**Download and Build**. **Use Files I Already Have** builds from your own
copies of the two files instead (below). After that it opens on **Play**,
and checks for updates (Sparkle) as it opens, asking before it installs one.
When an update needs the game rebuilt, it does that on its next launch, in a
few minutes, without downloading the disc again. Your parks and saves are
kept in `~/Library/Application Support/Theme Park World`.

## Playing on Linux and Steam Deck

Theme Park World is a Flatpak for any Linux on x86-64 or arm64: a Steam
Deck, Fedora, CachyOS, Ubuntu and the rest. As on a Mac, nothing of the game
is in it: the first run downloads the disc image and the 2.0 patch and
builds the game, which takes 10 to 20 minutes (a Steam Deck is at the slow
end).

```
flatpak install --user https://flatpak.cpressland.io/io.cpressland.ThemeParkWorld.flatpakref
```

or open that link in a browser, which hands it to your software centre. It
adds the `cpressland` remote ([cpressland/flatpak](https://github.com/cpressland/flatpak)),
and fetches the freedesktop SDK from Flathub (the compiler the build needs).
Updates come with your other Flatpaks.

The window offers **Download and Build**, then **Play**. A mouse, the
keyboard, the touch screen or a controller (D-pad and A) all work it. On a
Steam Deck, install it in Desktop Mode (Konsole, or Discover with the link),
then **Add a Non-Steam Game** in Steam to play it from Game Mode. The game,
its saves and the logs are in `~/.var/app/io.cpressland.ThemeParkWorld/data/theme-park-world`.

### Or as an AppImage

Each release also has `Theme-Park-World-VERSION-x86_64.AppImage` and
`-aarch64.AppImage`: one file, made executable and run, with no software
centre or Flathub needed. Where the Flatpak takes its compiler from the
freedesktop SDK, the AppImage carries its own (zig's clang), so the first run
downloads and builds the game the same way, on a Steam Deck too. The game,
its saves and the logs are in `~/.local/share/theme-park-world`, which also
gets a link, `appimage`, to the running AppImage: the game's build finds its
tools and SDL through it. `--install` runs the build without a window
(`--install --iso TPWORLD.ISO --patch themeparkworld20patch.zip` for your own
two files, `--install --status` to see what's out of date). Built by
`app/linux/build-appimage.sh` on Ubuntu 22.04 (`.github/workflows/appimage.yml`).

The game holds the mouse inside its picture while it has focus, as on a
Mac: Ctrl+Super lets it go and a click takes it back. Direct3D draws on the
GPU through Vulkan (1.3, as Mesa's drivers on a Steam Deck and any recent
desktop have), or in software where there is none.

## Playing with a controller

An Xbox controller, a Steam Deck's own controls or a PlayStation pad (any
SDL knows) plays the game as a mouse and keys would, on a Mac or Linux:

| Control | Does |
|---|---|
| Left stick | the cursor (at an edge, the park scrolls); pressed in: slow, for small things |
| A | click; held, a drag (paths, sliders) |
| B | right-click: cancel, back |
| X, held | click on to build more of the same (Ctrl) |
| Right stick | scroll the park |
| LB / RB | rotate the park |
| LT / RT | zoom out / in |
| D-pad left / right | rotate the item being placed |
| D-pad up / down | zoom a step |
| Start | the pause menu; out of a dialog (Escape) |

`--no-pad` leaves controllers alone (for Steam Input's own mouse, say). The
mouse's wheel zooms too.

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
merged, pcrecomp's `main` alone won't build this host.

| PR | Branch | What it adds | Needed? |
|----|--------|--------------|---------|
| [#163](https://github.com/sp00nznet/pcrecomp/pull/163) | `win32hle-metal` | Direct3D on the GPU through Metal (macOS), and the `d3d_gpu.h` seam | **Yes.** The host calls `hle_d3d_metal_enable`. |
| [#187](https://github.com/sp00nznet/pcrecomp/pull/187) | `win32hle-vulkan` | Direct3D on the GPU through Vulkan (Linux); builds on #163 | **Yes.** The host calls `hle_d3d_vulkan_enable` (a stub where Vulkan's headers are missing). |
| [#184](https://github.com/sp00nznet/pcrecomp/pull/184) | `win32hle-dinput-relative` | An exclusive DirectInput mouse gets movement, not the pointer | **Yes.** Without it the game's cursor sticks where the pointer met an edge; the controller moves it with `hle_input_motion`. |
| [#185](https://github.com/sp00nznet/pcrecomp/pull/185) | `win32hle-mouse-wheel` | The mouse wheel, as `WM_MOUSEWHEEL` and DirectInput's z axis; builds on #184 | **Yes.** The wheel and the triggers zoom with it (`hle_input_wheel`). |
| [#186](https://github.com/sp00nznet/pcrecomp/pull/186) | `win32hle-poll-hook` | A poll hook at each pump, for a host's controller; builds on #184 | **Yes.** `src/host/pad.c` reads the controller there. |
| [#180](https://github.com/sp00nznet/pcrecomp/pull/180) | `win32hle-present-pacing` | Every flip shown, paced to the display; `Sleep(0)` polling sleeps | No, but recommended: without it frames judder, and the pointer thread takes a core. |
| [#183](https://github.com/sp00nznet/pcrecomp/pull/183) | `win32hle-handover` | A quieter hand-over between threads, on a condition variable | No: 5% of the main thread back. |
| [#181](https://github.com/sp00nznet/pcrecomp/pull/181) | `win32hle-dinput-followups` | #159's review follow-ups, and `dinput_selftest.c` | No: correctness for other games. |
| [#182](https://github.com/sp00nznet/pcrecomp/pull/182) | `win32hle-createfont-width` | `CreateFontA`'s width, as `lfWidth` (#160's follow-up) | No: the game uses `CreateFontIndirectA`. |

DirectX 6 (#154), DirectInput (#159), font widths (#160), the held mouse
(#161), DirectSound's play cursor (#164) and #154's follow-ups (#158) are in
`main` now.

The fork's `tpw` branch is upstream `main` with all of these, and the
commit `app/pcrecomp.lock` names is on it. To build against it:

```
cd ../pcrecomp
git remote add cpressland https://github.com/cpressland/pcrecomp
git fetch cpressland
git switch -c tpw cpressland/tpw
```

When a PR is merged, it drops off this table and out of `tpw`; once all
are, build against plain `main`.

## Building (macOS)

Prerequisites: Xcode's command line tools, Python 3.10+, and Homebrew's
`cmake ninja pkgconf sdl2 sdl2_ttf uv`. The pcrecomp toolkit is cloned
**beside** this repository:

```
some-folder/
  pcrecomp/      <- git clone https://github.com/sp00nznet/pcrecomp
  tpw-recomp/    <- this repository
```

on the fork's `tpw` branch, as above.

### In one command

With the Python packages installed (`uv venv .venv && uv pip install
--python .venv/bin/python pefile capstone unicorn`), `tools/install/install.py`
does every step below, from the disc image to a compiled game:

```
.venv/bin/python -I tools/install/install.py --iso TPWORLD.ISO --patch themeparkworld20patch.zip
```

`--download` in place of `--iso` and `--patch` downloads both, as the app
does: resumed if a download stops, and deleted once the game folder is made.
It checks both files' MD5s first, and puts everything in
`~/Library/Application Support/Theme Park World` (`--dest` for elsewhere):
the game folder, where its saves go, and the analysis, the lifted C and the
build, under `build/`. It takes about ten minutes, and prints how to play at
the end. Run again, it redoes only what is out of date: after a host fix only
the compile, after a lifter fix the lift too, and the disc and patch are
needed again only if the disc's steps change. Saves are kept.

### Theme Park World.app

`app/build-app.sh` makes `build-app/Theme Park World.app` (about 125 MB):
the launcher (`app/Launcher`, Swift), the tools to build the game
(`app/toolchain.sh`: a standalone Python with pefile, capstone, unicorn,
CMake and Ninja, and SDL2 and SDL2_ttf as frameworks, all pinned by
checksum), this repository's and pcrecomp's sources
(`install.py --export-sources`), Sparkle, and a stand-in icon
(`app/icon.swift`). It needs only Xcode's command line tools to build.

The app runs `install.py --status` to see what's out of date, runs
`install.py` with what it carries when anything is, showing its progress,
then loads `build/host/libtpw.dylib` and enters it at `tpw_main`. The game
runs in a child process, so a crash ends in an alert offering the log.

- `~/Library/Logs/Theme Park World/`: `tpw.log` (the game; the run before,
  `tpw.1.log`) and `install.log` (the last build).
- `--setup` opens the setup window even when the game is built.
- Started from a terminal, the game's output stays there, and arguments pass
  to it: `".../Theme Park World.app/Contents/MacOS/Theme Park World" --windowed`.
- `TPW_HOME` points the app at another game folder. `TPW_ISO` and
  `TPW_PATCH` give it your own two files and start the build.
  `TPW_DOWNLOAD_BASE` downloads them from elsewhere.
- For tests: `TPW_BUILD_NOW` presses Download and Build, and `TPW_PLAY_NOW`
  presses Play, once the game is built.
- `TPW_SNAPSHOT=DIR` draws the setup window's states into DIR as PNGs.

### Releases

Pushing a tag `v1.2.3` runs `.github/workflows/release.yml`, which runs
`app/release.sh` and publishes the release:

- `Theme-Park-World-1.2.3.dmg`;
- the zip that Sparkle and `install.sh` fetch;
- `install.sh`;
- `appcast.xml`, Sparkle's feed.

The app is signed ad hoc, not notarized. Sparkle checks each update with its
own Ed25519 signature: the workflow signs with the `SPARKLE_PRIVATE_KEY`
secret, and the app holds the public half, `app/sparkle-public-key.txt`.
pcrecomp is the commit `app/pcrecomp.lock` names: upstream `main` with the
pending PRs, on the fork's `tpw` branch.

The same tag runs `.github/workflows/flatpak.yml`: `app/linux/build-flatpak.sh`
builds the Flatpak on x86-64 and arm64 runners (from the tagged commit's
checked-in files and pcrecomp at the lock) and attaches them to the
`flatpak` release. [cpressland/flatpak](https://github.com/cpressland/flatpak)
signs and publishes them at `flatpak.cpressland.io`, which holds the latest
build only; the `FLATPAK_HUB_TOKEN` secret lets this workflow start that at
once (otherwise it runs each day). `app/linux/launcher.c` is the
Linux launcher (SDL, as the game is); `TPW_LAUNCHER_SHOT=FILE.bmp` with
`TPW_LAUNCHER_STATE=setup|working|ready|failed|crashed` draws one screen.

### Step by step

1. Python packages:
   ```
   uv venv .venv && uv pip install --python .venv/bin/python pefile capstone unicorn
   ```
2. The game folder: the disc's files, patched to 2.0, with the game
   unwrapped from SafeDisc over its loader:
   ```
   python3 -I tools/disc/iso.py TPWORLD.ISO work/cd
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
   Direct3D draws on the GPU through Metal on a Mac (pcrecomp's
   `d3d_metal.m`) and Vulkan on Linux (`d3d_vulkan.c`, which needs Vulkan's
   headers to build and falls back to software without a Vulkan 1.3 GPU);
   `--renderer software` draws on the CPU instead, as before, which is slow
   at large sizes.

   The game opens full screen, in its own Space on macOS (`--windowed` for
   a window; F11 or Alt+Enter toggles). The game draws its own cursor, so the
   mouse is held inside the picture while the game has focus: Ctrl+Cmd lets
   it go, a click takes it back, and switching apps lets it go too
   (`--free-mouse` turns this off). F12 cycles the scaling.

   The game draws as many frames as it can, as it did on Windows, where its
   monitor's refresh held it back. Here each frame is held for whole
   refreshes, never fewer than keep it near 60 a second (2 on a 120 Hz
   display), and the time between is idle: `HLE_MAX_FPS=0` lifts the cap.
   On an M1 Pro a park takes about 40% of one core, down from 120%.

## License

MIT for this repository's own code. Theme Park World is © Electronic Arts;
none of its files, and nothing generated from them, is in this repository.
