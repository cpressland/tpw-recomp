# Bring-up log

Each wall the native macOS build hit, in order, and what moved it. Run with
`build-macos/tpw --run --headless --watchdog 30`; the knobs used to find each
one are at the end.

1. **`??0Dialup@@QAE@XZ` unimplemented**: the first game code after the CRT
   constructs the Won online objects (`wea*.dll`, eight libraries, 101
   `__thiscall` methods). `tools/gen/won_stubs.py` writes
   `src/host/won_stubs.c`: every method answers "not connected", and pops
   exactly what the real export pops, read from its `ret N` in the disc's
   DLL (following a tail `jmp`). Methods new in 2.0 (`weachatr`,
   `weauploadr` were updated by the patch) are sized from their MSVC names;
   the name parser agrees with `ret N` on all 200 exports where both exist.
2. **`IsDBCSLeadByte` and 30 more**: the imports win32hle does not answer,
   listed at once by comparing the import table with the registered shims.
   `src/host/shims.c` answers them as a Windows 98 machine with no MIDI, IME
   or Uniscribe; `tools/gen/qmixer_stubs.py` stubs QSound's QMixer (27 calls,
   `ret N` from `QMixer.dll`) so the game runs silent; DirectInput reports
   unavailable.
3. **WinMain returns at once**: `Data\Language\American` is missing. The
   language comes from the registry; the no-CD build's `tp.reg` says `0x0409`
   (American), this European disc carries `English`: `0x0809`.
4. **WinMain returns after the fonts**: resource `0x419` is not in
   `residx.dat`. The 2.0 executable needs the 2.0 data: the patch adds four
   fonts (`DATE*.bf4`) and updates the index. `tools/rtpatch/apply.py` runs
   Pocket Soft's own `patchw32.dll` (RTPatch 5.00, from the patch's
   InstallShield cabinet) in the SafeDisc emulator's Win32, as Windows 98,
   applying `PATCH.RTP`, `EuroAmer\PATCH.RTP` and `2057\PATCH.RTP` as the
   installer's script does. 44 files change. The patched, still-encrypted
   `TP.ICD` has the no-CD executable's entry point, section layout and
   resources, so the no-CD file is the 2.0 game.
5. **"Theme Park World requires an MMX-enabled processor"**: pcrecomp's
   `CPUID` asks the host CPU, and only under MSVC; on an arm64 Mac it changes
   nothing. `run_lift.py` routes the five lifted `cpuid`s to
   `src/host/cpu.c`, which answers as a Pentium II: MMX, no SSE or 3DNow!.
6. **"DirectX6 not found"**: the game asks DirectDraw for `IDirectDraw4`
   (`{9C59509A-39BD-11D1-8C4A-00C04FD930C5}`); win32hle stopped at
   `IDirectDraw2`. pcrecomp branch `tpw-dx6` adds `IDirectDraw4` (its four
   new methods, a `DDSURFACEDESC2` to the mode callback) and
   `IDirectDrawSurface4` (five new methods; one vtable serves surfaces 1 to
   4, a DDSURFACEDESC2 having every older field where it was), plus hooks for
   a host to serve more interfaces on DirectDraw and its surfaces (Direct3D,
   later).
7. **The renderer**: TPW has two, Direct3D (`[0x8BD524]` = 1) and Martin
   Griffiths' MMX software renderer (4), chosen by `save\config.tcf`. With no
   config it asks for `IDirect3D3` and does not survive a refusal. (Until 12,
   the host copied the 2.0 patch's `safemode.tcf`, which picks the software
   renderer, into place on a first run.)
8. **A sound object never made**: with no QMixer session the game goes on to
   use one. `qmixer_stubs.py` now opens a session (a handle from
   `QSWaveMixInitEx`); every other call returns 0, so the game is silent.
9. **Uniscribe**: menu text is laid out and drawn with `ScriptStringAnalyse`
   and `ScriptStringOut` into a DIB section. `src/host/shims.c` answers the
   five calls on win32hle's GDI: advances from `GetTextExtentPoint32A`,
   drawing with `TextOutA`.
10. **The renderer's span fillers**: the triangle setup (`0x006F0A50`, cseg)
    ends in `jmp [0xFBE044]`, a span filler picked from a table of code
    pointers; nothing calls them, so the catalog missed them, the jump went
    nowhere, the six registers the filler pops were never popped, and the next
    triangle's vertices were read from a framebuffer address.
    `tools/gen/data_code_ptrs.py` seeds every code pointer in cseg's tables
    (and function-like ones elsewhere): 319 entries, `work/tp/run_seeds.json`.
11. **Nothing drawn after the logo**: the game renders only while `GetFocus()`
    is its window. win32hle made the first visible top-level window active
    but gave it no focus; on Windows it has both (fixed on `tpw-dx6`).

12. **Direct3D**: pcrecomp `tpw-dx6` adds `d3d.c`, DirectX 6's Direct3D:
    `IDirect3D3` offering one HAL device (hardware rasterisation, 565/1555/
    4444/8888 textures, a 16-bit z-buffer), `IDirect3DDevice3`, viewports,
    materials, textures (a surface's `IDirect3DTexture2` face, `Load` copying
    every mip level) and vertex buffers. What the game draws with is
    `DrawIndexedPrimitive` of pre-transformed vertices, with texture-stage
    states. DirectDraw learned what Direct3D needs: z-buffers attached beside
    the flip chain, each surface keeping the pixel format it was made with,
    mipmap chains, and a change counter.
13. **Drawing it**: `d3d_raster.c`, a software rasteriser: perspective-correct
    attributes, the top-left fill rule, a 16-bit z-buffer, nearest and
    bilinear texturing with wrap and clamp, colour key, stage 0's colour and
    alpha operations, alpha test, vertex fog, specular, and every blend
    factor. First version: 6 frames a second at 1024x768. Each texture is now
    cached as ARGB with a key mask, remade only when its change counter moves
    (3.5x), and triangles are queued and drawn at the end of a scene, or
    before DirectDraw touches any surface, by a thread per core, each owning
    a band of the screen and drawing every triangle in order (3x again): about
    60 frames a second at 1024x768 and 30 at 1600x1200 on an M-series Mac.
    `HLE_D3DFPS=1` prints the rate, `HLE_D3DTHREADS=N` sets the threads.
14. **Higher resolutions**: the 2.0 patch's own override, `data\Resolution.sam`
    (512x384 to 2048x1536); `tpw --resolution 1024x768` writes it, and
    `--resolution options` removes it.

15. **The mouse**: the game reads its mouse and keyboard through DirectInput 5
    (buffered, 32 events, relative axes), and with DirectInput refused its
    cursor never moved; keys reached it through window messages. pcrecomp
    branch `win32hle-dinput` adds `dinput.c`: the system mouse and keyboard,
    read from the same state the window messages come from (user32's cursor
    and key bytes), so both paths agree. The game does not poll: it waits on
    the event `SetEventNotification` gave, so input arriving (a hook in
    `hle_input_mouse`/`hle_input_key`) records what changed as buffered
    events there and then and sets each acquired device's event.
    `HLE_DITRACE=1` prints the set-up and every buffered read.
16. **Scripted input** for headless checks, `src/host/script.c`: `--move`,
    `--click`, `--rclick` and `--key vk` at given seconds, through the same
    win32hle input calls the SDL window uses. Verified: Escape skips the
    first movie, the game's cursor follows the mouse, clicking "Create New
    Player" opens the name dialog, typing and Backspace edit the name, and
    the tick creates the player (`save\users\1<name>`). The name field
    keeps its "Type your name" placeholder when clicked: the game's own
    behaviour, and with its 16-character limit only two letters fit after it.
17. **Screenshots tearing**: `--shot` copied the primary from its own thread
    while the game flipped; it now copies holding the machine.

18. **QMixer**: `src/host/qmixer.c`, QSound's mixer answered in the host
    (the generated failure stubs are gone). Every sound TPW plays is an
    input stream (`OpenWaveEx` type 0x11, read from the disc's `QMixer.dll`:
    a `WAVEFORMATEX`, a block size, a callback and its user data); the
    callback, `(buffer, bytes, user)` -> more to come, waits up to 500 ms
    for the game's own decoder thread. So each channel has a puller thread
    (one for good: every thread that calls the game gets a guest stack, and
    a thread per sound ran win32hle out of them) filling a ring from the
    callback, and a mixer thread mixes the rings into win32hle's audio
    output, 44.1 kHz stereo, about 100 ms ahead: volume, frequency, pause
    and stop, and simple 3D (distance between the mapping's minimum and
    maximum, left and right from the listener's orientation). The welcome
    speech now plays, and the loading screen goes on to the player's
    islands. `TPW_QMTRACE=1` traces it, `TPW_QMWAV=FILE` records the mix.
19. **A sign that never fits**: loading a park, the game fits each ride's
    sign by measuring its text, scaling the characters' advances, and
    measuring again through Uniscribe's `piDx`, until it is small enough.
    The Uniscribe answers ignored `piDx`, and win32hle's GDI ignored
    `lfWidth`, with which the game asks for the sign's font (width 1:
    squeezed): the text kept its natural 645 pixels and the loop never
    ended. `ScriptStringAnalyse` now takes the given advances (and
    `ScriptStringOut` places each character by them), and pcrecomp branch
    `win32hle-gdi-lfwidth` makes `lfWidth` scale text across.
    `TPW_SSATRACE=1` prints each measurement.

Now: the game plays. A park loads (Lost Kingdom), visitors arrive, money
and the date move, the advisor talks, with music, speech and effects, through
Direct3D at up to 2048x1536 and with the mouse and keyboard. Not yet heard
by a person: whether the 3D placement's left and right are the right way
round, and how the mix sounds against the original.

## The knobs

| | |
|---|---|
| `HLE_FILETRACE=1` | every `CreateFileA` and `FindFirstFileA`, with the host path |
| `HLE_REGTRACE=1` | registry opens and queries, found or missing |
| `HLE_DDTRACE=1` | DirectDraw calls, and each interface asked for |
| `TPW_PROBETRACE=1` | every `GetFileAttributesA`: how the game finds its data |
| `TPW_BT_FILE=text` | a backtrace when a file whose name contains `text` is opened |
| `--trace` | one line per import called |
| `TPW_VMTRACE=1` | `VirtualAlloc`/`VirtualFree`/`MapViewOfFile`/`UnmapViewOfFile` with their values |
| `--shot FILE.bmp --shot-after S [--shot-every S]` | the screen, as BMPs |
| `HLE_D3DTRACE=1` | Direct3D's device, viewports and the interfaces asked for |
| `HLE_D3DFPS=1` | scenes a second and triangles a scene, every five seconds |
| `HLE_DITRACE=1` | DirectInput's devices, and every buffered read |
| `TPW_QMTRACE=1` | QMixer: set-up, every wave opened and played |
| `TPW_QMWAV=FILE` | the mixed audio, recorded to a WAV as it plays |
| `TPW_SSATRACE=1` | every Uniscribe measurement, with the size it gives |
| `HLE_MSGTRACE=1` | every window message posted |
| `--move`, `--click`, `--rclick` `x,y@s`, `--key vk@s` | scripted input (`src/host/script.c`) |
| `game/enginedebug.txt` | the game's own log |

The traced build (`BUILD_DIR=build-macos-trace CMAKE_ARGS=-DTPW_TRACE=ON
./build-macos.sh`) adds, from `src/host/trace.c`:

| | |
|---|---|
| `TPW_FIRSTHIT=FILE` | each lifted function the first time it runs, in order |
| `TPW_RING_AT=VA` | the last 1024 functions entered, when VA is first entered |
| `TPW_ARGTRACE=VA` | `ecx` and four stack arguments (as strings where they are) per entry to VA |

The lifted code pushes 0 for a return address (`RECOMP_RETADDR=0u`) and the
game is built without frame pointers, so there is no guest backtrace to walk;
the entry ring is the call history.
