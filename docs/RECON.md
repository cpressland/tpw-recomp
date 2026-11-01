# Reconnaissance

## The disc (`TPWORLD.ISO`, 1999-10-26, multilingual EU release BFY08901509IB)

- Plain 2048-byte-sector ISO 9660 image, 2,783 files. No raw (2352) sectors,
  no subchannel data.
- `tp.exe` (285 KB) is **not the game**: it is the SafeDisc loader.
- `TP.ICD` (3.69 MB) is the real game executable, encrypted.
- SafeDisc support files: `clcd16.dll`, `clcd32.dll`, `clokspl.exe`,
  `dplayerx.dll`, `drvmgt.dll`, `secdrv.sys`, `00000001.TMP`.
- Game data under `Data/`, per-language speech and fonts under `English/`,
  `French/`, `German/`, `Danish/`, `Swedish/`, InstallShield 5 setup in
  `<Language>/Setup/`.
- Middleware visible on the disc: QMixer (`QMixer.dll`), DirectX 6.1 redist,
  `usp10.dll`, Won/"wea*" online DLLs, MPEG (`LBMPEG_D` section, `TheSims.mpg`).

## Protection: SafeDisc 1.35

`tp.exe` carries the signature `BoG_ *90.0&!!  Yy>` followed by version
`1.35.0`. In SafeDisc 1.x the loader authenticates the disc (via `secdrv.sys`
and SCSI reads), derives the key, decrypts `TP.ICD` and runs it.

`TP.ICD` sections (image base `0x400000`, entry RVA `0x273750`):

| Section  | RVA      | Virtual size | Entropy | Notes |
|----------|----------|--------------|---------|-------|
| .text    | 0x001000 | 0x2dc835     | 7.99    | encrypted |
| CSEG     | 0x2de000 | 0x0156b2     | 7.99    | encrypted |
| .rdata   | 0x2f4000 | 0x0293e1     | 4.86    | plain |
| .data    | 0x31e000 | 0x855958     | 7.34    | encrypted (raw 0x5d600) |
| DSEG, UVA_DATA, IDCT_DAT, GRPOLY_D | | | ~3.5 | plain |
| TQIA_DAT, LBMPEG_D | | | ~7.6 | likely encrypted or tables |
| .rsrc    | 0xb80000 | 0x0041de     | 4.77    | plain |

~3 MB of x86 code once decrypted: comparable to Red Alert 2.

This was the gating problem: nothing can be lifted until `.text` is
decrypted. It is now solved from the ISO alone, by running the loader
under emulation (below).

## The 2.0 patch (`themeparkworld20patch.zip`)

- `TPPatchTwoEUROAMER20000324a.exe`: InstallShield self-extractor holding an
  MSZip cabinet.
- The payload is **RTPatch** (Pocket Soft) `.RTP` files: a top-level
  `PATCH.RTP` (4.0 MB, 2000-05-11), `EuroAmer/PATCH.RTP`, and per-language
  ones keyed by LCID/language id.
- `PATCH.RTP` updates `TP.EXE`, `TP.ICD`, every SafeDisc file
  (`clcd16/32.dll`, `clokspl.exe`, `dplayerx.dll`, `drvmgt.dll`), `QMixer.dll`,
  `weachatr.dll`, `weauploadr.dll`, `data\_Resolution.sam`, `data\high.sam`,
  `data\med.sam` and cursors under `data\ui\cursors`.
- So the patched `TP.ICD` is **still SafeDisc-encrypted**, now SafeDisc
  1.41 (`1.41.000` in the patched loader), and applying the patch needs an
  RTPatch applier (proprietary; `tools/rtpatch` runs its own DLL under
  emulation).

## The executable we lift: Sim Theme Park World 2.0, unwrapped

`tools/safedisc/unwrap.py` writes `TP.ICD` out decrypted (below). It keeps
the ICD's own PE timestamp, `0x38DB8658`, which `run_lift.py` and the host
check. It is a plain MSVC image: entry `0x0067EE10` is `_WinMainCRTStartup`.

The 2000-era no-CD release of the same build (MD5
`e4c2a8979d1a5d7e705976c1d4d511be`, timestamp rewritten to `0x63732172`) is
also accepted. Against it, the unwrapped image differs in one byte of code
(the crack's own patch, `jne` to `jmp` at `0x005AA5AD`, past a CD check our
host passes anyway), the order of the import tables, and junk in two
sections' padding.

| Section  | RVA      | Virtual size |
|----------|----------|--------------|
| .text    | 0x001000 | 0x2e5a7a     |
| CSEG     | 0x2e7000 | 0x0156b2     |
| .rdata   | 0x2fd000 | 0x029a99     |
| .data    | 0x327000 | 0x88dcd8     |
| DSEG, UVA_DATA, IDCT_DAT, GRPOLY_D, TQIA_DAT, LBMPEG_D, .rsrc | | |

`.text` and `CSEG` (Bullfrog's hand-written code) are adjacent, one code
range `0x00401000-0x006FC800`, lifted as one.

Imports: KERNEL32 (125), USER32 (38), GDI32, ADVAPI32, WSOCK32, DDRAW
(`DirectDrawCreate`, `DirectDrawEnumerateA`: Direct3D is reached through
DirectDraw's `QueryInterface`), WINMM, DINPUT, DSOUND, IMM32, USP10, ole32,
`QMIXER.dll` (27: QSound's 3D mixer, on the disc as a DLL) and the eight
`wea*.dll` online-service libraries (Won: auth, chat, city, mail, news, RAS,
upload, vote).

The pipeline's numbers:

| Step | Result |
|------|--------|
| `unwrap.py` | 163 hidden imports, 585 call sites, about a minute |
| `pe_analyze.py` | 21 import DLLs |
| `rtti.py` | 52 classes, 167 virtual methods (the engine is mostly C) |
| `disasm32.py` | 34,444 functions, 94.8% byte coverage |
| `run_lift.py --all` | 34,790 lifted, 0 errors, 225 without a terminator, 4.44M lines of C |

## SafeDisc 1.41, emulated (tools/safedisc)

`sdemu.py` runs the retail loader headlessly under Unicorn, against the disc
image, answering every Win32 call in Python.

- **Anti-debug**: `IsDebuggerPresent`, `NtQueryInformationProcess(7)`,
  `\\.\SICE`/`\\.\NTICE`, and `int 1`/`int 3` whose exceptions must look
  exactly as Windows NT reports them (an `int 1` from ring 3 is an access
  violation at the instruction).
- **secdrv.sys**: one IOCTL, `0xEF002407`, answered in user mode with the
  latest driver's constants (from RibShark's SafeDiscShim). They are the
  right ones: `dplayerx` decrypts its own code with the 0x3C answer
  (`0x400`), and gives up on any other 0x3D answer than `0x2C8`.
- **The disc**: SCSI pass-through READ(10) on `\\.\D:`, plus TOC and address
  queries. A band of filler sectors, LBA 510 to 10510, follows
  `00000001.TMP` and belongs to no file.
- **Weak sectors**: the loader times reads of ordinary filler sectors, then
  of one it calls weak, and wants the weak read to take more than twice the
  average. Which sectors are weak is a hash over the LBA, in one function of
  the loader: `((lba ^ seed) * K + 0x6A7F) >> 8` modulo a count from the
  disc's descriptor. The emulator finds that function by its constant and
  makes those reads slow on a virtual clock. The check passes.
- **The child**: the loader starts itself again, suspended, writes a
  0xB5-byte stub onto the child's stack (`0x12FF07`, loading `dplayerx.dll`
  and carrying the parent's 128-bit key) and resumes it. It also creates two
  named mappings for the child, by the child's process id:
  `ELBANG63719_<pid>` (0x28 bytes, a constant from the loader's overlay:
  `TP.ICD`'s IAT `0x2FD000`/`0x610` and import area `0x323F60`/`0x2B2C`) and
  `MDJ240167_<pid>` (two encrypted name-table entries).
- **The keys** are all derived, none read from the disc: the parent's key
  from a seed in the loader XORed with constants and a hash of its own
  decrypted code; the child's from that, hashes of `dplayerx`'s own code
  (kept in a state block inside its `.text`), and hashes of the code
  sections of the image at `0x400000`, plus that image's entry RVA.
- **The image at `0x400000` must be `TP.ICD`**. Every offset the parent
  passes and every key the child derives is the ICD's: with the loader's own
  image there, the child's import key comes out wrong and it faults. With
  `TP.ICD` mapped as the suspended child's image, `dplayerx` decrypts it in
  place (887 pages), rebuilds its imports and jumps to its entry, and the
  result matches the no-CD release byte for byte (above). How Windows itself
  ends up with the ICD there, given `CreateProcessA("D:\TP.EXE")`, is not
  yet understood; the emulator maps it (`child_image`) and the result
  verifies.
- **Imports**: KERNEL32 and USER32 stay hidden after decryption. Their
  address tables hold stubs (`push magic; pushfd; pushad; push esp; push
  index; push dll; call resolver`), and the resolver picks the API from the
  call's return address, so a call site names some slot of the right DLL
  but not its own: 585 sites do. From an address that is no call site, a
  stub falls back to its own slot. Their name tables are XOR-encrypted
  (`0x7C8D8B73`), the names too, and entry 0 of each is zeroed and comes
  from `MDJ` (`GetDriveTypeA`, `GetKeyboardLayout`). `unwrap.py` resolves
  each slot and each call site by running the stubs, finds the key as the
  one whose entries leave holes the size of the names, and writes the names
  back in plain text.

Fixed in the emulator along the way: `CreateProcess` reported a hard-coded
process id (so the child looked for `…_00001200` mappings the parent had
named `…_00002000`), and named mappings were private to each emulated
process. Diagnostics: `SDEMU_HIST`, `SDEMU_PROGRESS`, `SDEMU_SAMPLE`,
`SDEMU_COUNT`, `SDEMU_WATCH`, `SDEMU_REGS`, `SDEMU_DUMP`, `SDEMU_SHOWMAP`,
`SDEMU_MS_PER_CALL`, `SDEMU_POKE` (`at@addr=value`), `SDEMU_SECDRV_CHILD`
(`cmd=value` for children), `SDEMU_TRACE_MAX`; 1000 faults at one address
stop the run.

## Sound: the banks and QMixer

- **Banks** (`*.sdt`, `Data\global\Speech\speechHD.SDT` and the rest:
  speech, music, rides, ambience, guests): a count, a table of offsets,
  then each sound: a 0x28-byte header (its size, the data's size, a
  16-byte name like `sp_471.mp2`, the rate as a u16, the bits, a byte, the
  decoded length in bytes at +0x20) and MPEG-2 layer II data. 641 lines of
  speech, all 22050 Hz mono.
- **Playing one**: the game decodes the MP2 on a thread of its own, a
  9216-byte block at a time, and gives QMixer every sound as an input stream
  (`OpenWaveEx` type 0x11) whose callback, `sub_006BB4F0` with the sound
  object as its user data, waits up to 500 ms for the decoded block, copies
  it and wakes the decoder for the next (on a timeout it gives silence). The
  sound object keeps a copy of the bank header from +0x3C, so the length is
  at +0x5C. `sub_006D2380` plays one: `ConfigureChannel`, `OpenWaveEx`,
  `FreeWave` of the channel's last wave, `PlayEx` with flags 0x20 and no
  play parameters, so no notice comes back when a sound ends.
- **When a sound ends** the game knows only from its decoder: once it has
  given all of it, the game stops the channel, sets it up for another, frees
  the wave or plays another there. QMixer asks for a block just before
  playing it, so on Windows that is the sound's end. A mixer that asks
  further ahead (`src/host/qmixer.c` keeps 4 blocks, 0.8 s, to ride out the
  decoder) cuts the end off: in 7 minutes of a park, 651 sounds (speech,
  rides) lost up to half a second. The host plays what is left of a sound
  given whole, up to its length, in a channel the game has not opened.

## Widescreen: what is known

- **Screen size**: one switch sets the width (`0x7A1968`) and height
  (`0x7A1930`); all its paths meet at `0x44A156`, where `run_lift.py`
  patches in `--resolution WxH`.
- **Rendering**: vertices are transformed to clip space, then
  `sub_00579690` divides by `w` and maps to the screen with the render
  state's viewport (`0x8BCBC8` block: x scale `[0x8BCC00]`, y scale
  `[0x8BCC04]`, centre `[0x8BCBF0]`/`[0x8BCBF4]`), set by `0x582EB0` from
  the screen size: half the width, half the height. The render state also
  keeps the aspect `H/W` (`[0x8BCBCC]`).
- **The world** widens correctly: its frustum (`sub_0056B790`, from a field
  of view) and its billboards (`0x586274`) divide or multiply by that
  aspect. One camera object, always set up with the real aspect.
- **The HUD** is laid out in a virtual 2048x1536 screen and mapped to the
  real one per axis, so at widescreen it stretches sideways by
  `(W/H)/(4/3)`. Fixed in `src/host/widescreen.c`, by squeezing each piece
  back by `k = (4/3)(H/W)` towards its edge (left, centre or right):
  - **Widgets.** The GUI library (`0x659000`-`0x66F000`, debug-built,
    `ebp` frames) makes every widget through one constructor,
    `sub_0065BCAD(parent, flags, id, l, t, r, b)`, with absolute
    rectangles. A screen-sized widget is a layer; a widget on a layer
    starts a group anchored by its centre; its children share the anchor.
    Drawing, text and the mouse's hit tests all read these rectangles.
    Each screen is a layer (all children of the root widget, id -666); the
    Options page (id 120000) is one too, but laid out as one full-screen
    panel, so it is squeezed whole into the middle: anchored by groups, its
    columns went to the screen's edges and the Videocard row's button
    (a sibling of its socket, not a child) left the socket behind.
  - **Layout tables.** Screens are built from tables of int16s in the exe
    (the park HUD's at `0x752940`) by `sub_0065FD58`, a switch on tags:
    0 a child widget, 3 its text rectangle (the date's), 5 hit shapes
    (rectangle, circle, polygon), 10 an area, 11 list columns as
    left/right pairs. Readers: `0x65FC38` (rect), `0x65FCC5` (point),
    `0x65FC18` (short). Calls push a dummy return address, so the patches
    sit after each read in the builder.
  - **Offsets.** HUD text classes (`0x48F830`, and copies at `0x491300`,
    `0x491AB0`) place text at `[esi+0xDE] * width >> 11` from their
    element; those loads get the 4:3 width.
  - **Free models.** The advisor and a ride's preview are 3D models drawn
    straight in screen space (`w = 1` at `sub_00579690`), not fitted to a
    widget, scaled per axis by code not yet found. Widget-fitted models
    (`sub_00476C50` fits a model's box to a rectangle) and text sit flat
    on depth layers `z = 1 - d/40`; anything else in screen space is
    grouped by overlapping depth and squeezed at the projection.
  - **Hit circles.** A circle has one radius in virtual units, which are
    narrower than tall once squeezed. The builder's circles are noted
    with their widget, and their test (`sub_0066C0C4`) widens the x
    distance by `1/k`: the ellipse the button is drawn as.
  - **One squeeze a run.** With a size of its own, every screen-size switch
    is held to it, so `k` comes from that size, not the current screen:
    widgets made before the first switch are squeezed like the rest.
- **Pictures and movies** are 4:3 and fill the 4:3 width, centred, black
  at the sides. Splash and loading pictures (`Data\Init\640\*.tga`, the
  loading bar painted into them) are stretched into the locked back buffer
  by `sub_00587130`; the host draws them instead. Movies take their size
  from the screen at `0x51B0ED` (width `0x802460`, x `0x802458` when one is
  given), and get the 4:3 width there.
- **Picking.** What the mouse is over in the park is found by
  `sub_0045BF90`: a ray from the view's corners, built for a 4:3 view
  (`atan2(0.75, 1)`, `[0x6FE640]`, at `0x45C09A`), through the mouse as a
  fraction across them, `x / (2 * [0x7A0C9C])` and `y / (2 * [0x7A162C])`
  (half the screen's width and height). Its results: the point under the
  mouse (`[0x79E248]`) and what is there (`[0x7A1CD4]`, a ride's object, or
  0). The mouse comes in as pixels, as floats (`[0x790AC8]`, `[0x790ACC]`,
  from the GUI's virtual mouse at `0x42A855`). Wider than 4:3 the picked
  spot was pulled towards the middle by (4/3) / (width / height); the
  fraction across is widened about the middle by as much (`run_lift.py`,
  `tpw_pick_scale`). Swept at 3440x1440, a ride is picked where it is drawn,
  across and down. Only putting the real aspect in place of the 0.75 got
  part of the way (0.665 of the way out), the corners being 4:3 in more
  than their angle. Found by diffing the game's data with the mouse over a
  ride, then over grass, then over the ride again.
- **Open, seen in play, not reproduced here**: a lightning strike in a
  park flashed the whole screen purple for a moment (Metal, 3440x1440).
  What is known of lightning:
  - The weather (`0x512AE8`) counts `+0x3C` down and adds a strike to the
    weather renderer (`[0x8BCBAC]`) with `sub_00580320`: up to 16, 48 bytes
    each at `+0x3008` (top, bottom, the direction between, a countdown at
    `+0x24`, a random seed for the bolt's shape at `+0x2C`), the count at
    `+0x3308`. Nothing else: no flash of the screen or the fog; later, the
    thunder.
  - `sub_0057EE30` counts each down and gives the bolt a grey colour
    (`+0xA19C`, r = g = b); `sub_0057EC50` draws it in screen space (FVF
    0x1C4), from a sub-rectangle of the texture holding
    `Data\Generic\Weather\lightning.tga` (a white bolt in a blue glow).
  - Strikes put into that queue by hand, every 3 s at the point under the
    mouse, draw as they should on Metal at 1024x768 and 3440x1440, and a
    sampler looking for a frame much brighter or more purple than the one
    before found none of them purple.
  - Forcing storms in `Data\levels\Standard.sam` (`QualityForLightning*`
    wider than 0-15, with or without rain) crashes the game itself as a
    park loads (`sub_00463AB0`, reading near 0); only the countdown can be
    changed safely.
  - So the flash is something a real storm does that a lone strike does
    not, or the sky or fog in a storm. Next: a park with a storm, or a save
    made during one.
- **Movie sound skipping** (fixed in pcrecomp's `dsound.c`): the Bullfrog
  animation and the startup movie lost sound in their first seconds. Their
  sound is DirectSound's, not QMixer's: a looping 32 KB buffer (22050 Hz,
  stereo, 371 ms) the movie player refills a video frame at a time (about
  2912 bytes) as the play cursor leaves its write position. The mixer moved
  the cursor a callback at a time (23 ms), so the two could be exactly
  equal; the player took that for an empty buffer and wrote a lap ahead,
  over sound not yet played, and a whole lap (371 ms) was lost. Recording
  the mix and lining it up with the movie's own audio (`ffmpeg -i
  Data\Movies\bf.tgq`, EA ADPCM) showed two such jumps in the Bullfrog
  animation's first 4 s. The cursor now moves between callbacks with the
  time, as a sound card's does, and both movies play through whole.
- **Tried and dropped**: squeezing all `w = 1` geometry at the projection
  (pixel-sized text detaches), and giving the GUI a 4:3 width at
  `0xFAA5C4` (absolute positions then crowd to the left).
