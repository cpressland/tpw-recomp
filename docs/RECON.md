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

**This is the gating problem.** Nothing can be lifted until `.text` is
decrypted, and the public write-ups agree the SafeDisc 1 key needs the
original disc (or a 1:1 raw copy). A 2048-byte ISO may not carry what the
disc check reads.

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
- So the patched `TP.ICD` is **still SafeDisc-encrypted**, and applying the
  patch needs an RTPatch applier (proprietary format; the applier DLL could be
  run under emulation).

## The executable we lift: Sim Theme Park World 2.0, no-CD

`game/tp.exe`, MD5 `e4c2a8979d1a5d7e705976c1d4d511be`, 3,734,528 bytes: the
2.0-patched executable with SafeDisc removed (a 2000-era no-CD release; its
PE timestamp, `0x63732172`, was rewritten when it was unwrapped, and
`run_lift.py` and the host check for it). It is a plain MSVC image: entry
`0x0067EE10` is `_WinMainCRTStartup`, and every section is decrypted.

| Section  | RVA      | Virtual size | Entropy |
|----------|----------|--------------|---------|
| .text    | 0x001000 | 0x2e5a7a     | 6.68    |
| cseg     | 0x2e7000 | 0x0156b2     | 6.57    |
| .rdata   | 0x2fd000 | 0x029a99     | 4.80    |
| .data    | 0x327000 | 0x88dcd8     | 4.29    |
| dseg, uva_data, idct_dat, grpoly_d, tqia_dat, lbmpeg_d, .rsrc | | | |

`.text` and `cseg` (Bullfrog's hand-written code) are adjacent, one code
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
| `pe_analyze.py` | 21 import DLLs |
| `rtti.py` | 52 classes, 167 virtual methods (the engine is mostly C) |
| `disasm32.py` | 33,994 functions, 94.8% byte coverage |
| `run_lift.py --all` | 33,997 lifted, 0 errors, 198 without a terminator, 4.29M lines of C |

## SafeDisc 1.35, emulated (tools/safedisc)

Before the no-CD build was available we ran the retail loader headlessly,
under Unicorn, against the ISO. It gets through the outer loader and its
disc check; what it learned is kept here because it is the route to a
build from the ISO alone.

- **Anti-debug**: `IsDebuggerPresent`, `NtQueryInformationProcess(7)`,
  `\\.\SICE`/`\\.\NTICE`, and `int 1`/`int 3` whose exceptions must look
  exactly as Windows NT reports them (an `int 1` from ring 3 is an access
  violation at the instruction).
- **secdrv.sys**: one IOCTL, `0xEF002407`, answered in user mode with the
  latest driver's constants (from RibShark's SafeDiscShim).
- **The disc**: SCSI pass-through READ(10) on `\\.\D:`, plus TOC and address
  queries. A band of filler sectors, LBA 510 to 10510, follows
  `00000001.TMP` and belongs to no file.
- **Weak sectors**: the loader times reads of ordinary filler sectors, then
  of one it calls weak, and wants the weak read to take more than twice the
  average. Which sectors are weak is a hash over the LBA, in one function of
  the loader (`0x00404590` in 1.35): `((lba ^ seed) * K + 0x6A7F) >> 8`
  modulo a count from the disc's descriptor. The emulator finds that function
  by its constant and makes those reads slow on a virtual clock. The check
  passes.
- **Then** the loader starts a second `tp.exe` suspended, writes a stub onto
  its stack and resumes it; the stub loads `dplayerx.dll`, which decrypts
  with TEA in place and keeps re-checking (secdrv, debuggers, timing) in a
  loop the emulator does not yet leave. That is where it stops.
