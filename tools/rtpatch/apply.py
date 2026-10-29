"""Apply the Theme Park World 2.0 patch to the disc's files, headlessly.

The patch (themeparkworld20patch.zip, ModDB) is an InstallShield installer
around Pocket Soft's RTPatch 5.00: PATCH.RTP files, applied by the vendor's
own patchw32.dll (`RTPatchApply32`). Here that DLL runs in the Win32 emulator
from tools/safedisc, against a folder of the disc's files: drive C: is the
folder being patched, drive D: holds the patch files.

The installer's script (setup.ins) applies three, in order: the main
PATCH.RTP, the build's (EuroAmer for this disc), then the language's, by
LCID (2057: UK English).

    .venv/bin/python -I tools/rtpatch/apply.py themeparkworld20patch.zip work/cd work/v20
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PCRECOMP = os.environ.get('PCRECOMP', os.path.join(HERE, '..', '..', '..', 'pcrecomp'))
sys.path.insert(0, os.path.join(HERE, '..', 'safedisc'))

import sdemu   # noqa: E402
import winapi  # noqa: E402

# RTPatchApply32's callback: (Id, Param) -> a pointer the DLL reads as an
# answer. A non-NULL pointer to an empty string means "carry on"; the
# messages (ids 1..3) are printed.
CALLBACK_MESSAGES = {1: 'warning', 2: 'error', 3: 'info', 4: 'info'}


def _callback(e, a):
    cid, param = a(0), a(1)
    text = e.cstr(param) if cid in CALLBACK_MESSAGES and param else None
    if text and text.strip():
        print(f'  [rtpatch {CALLBACK_MESSAGES[cid]}] {text.rstrip()}')
    return e.os.empty_answer


winapi.APIS['RTPatchCallback'] = (2, _callback)


def extract(zip_path, work):
    """themeparkworld20patch.zip -> its installer -> the installer's cabinets."""
    with zipfile.ZipFile(zip_path) as z:
        exe = next(n for n in z.namelist() if n.lower().endswith('.exe'))
        z.extract(exe, work)
    out = os.path.join(work, 'payload')
    subprocess.run(['7z', 'x', '-y', '-o' + out, os.path.join(work, exe)], check=True,
                   stdout=subprocess.DEVNULL)
    return out


def applier(payload, work):
    """patchw32.dll, from the installer's _user1.cab."""
    cab = os.path.join(payload, '_user1.cab')
    out = os.path.join(work, 'rtp')
    # pcrecomp's InstallShield 5 reader: the header is in the cabinet itself.
    subprocess.run([sys.executable, '-I', os.path.join(PCRECOMP, 'tools', 'assets', 'isextract.py'),
                    cab, cab, '-o', out], check=True, stdout=subprocess.DEVNULL)
    return os.path.join(out, 'patchw32.dll')


def apply_one(dll, payload, target, rtp_rel, verbose=False):
    """Run RTPatchApply32("C:\\ D:\\<rtp>") in a fresh emulated process."""
    emu = sdemu.Emu(payload, iso=os.devnull, verbose=verbose)
    emu.os.c_root = os.path.abspath(target)
    emu.os.version = 'win98'         # the applier takes its ANSI path there
    emu.os.empty_answer = emu.os._static(b'\0\0\0\0')
    mod = emu.load_pe(dll)
    emu.os.main_module = mod
    if emu.call_dllmain(mod) == 0:
        raise SystemExit('patchw32.dll refused to load')
    cmd = emu.os._static(f'C:\\ D:\\{rtp_rel}'.encode() + b'\0')
    cb = emu.thunk('host', 'RTPatchCallback')
    rc = emu.call(mod['exports']['RTPatchApply32@12'], [cmd, cb, 1])
    print(f'  {rtp_rel}: RTPatchApply32 returned {rc}, {emu.api_count} API calls')
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('patch_zip')
    ap.add_argument('disc', help='the extracted disc (left unchanged)')
    ap.add_argument('out', help='where the patched copy goes')
    ap.add_argument('--lcid', default='2057', help='the language patch to apply (2057: UK English)')
    ap.add_argument('--build', default='EuroAmer')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    if os.path.exists(a.out):
        raise SystemExit(f'{a.out} exists; give a new folder')
    work = tempfile.mkdtemp(prefix='tpw-rtpatch-')
    try:
        payload = extract(a.patch_zip, work)
        dll = applier(payload, work)
        # A copy to patch: APFS clones it instantly where it can.
        subprocess.run(['cp', '-Rc', a.disc, a.out], check=False)
        if not os.path.exists(a.out):
            shutil.copytree(a.disc, a.out)
        failed = 0
        for rtp in ('PATCH.RTP', os.path.join(a.build, 'PATCH.RTP'), os.path.join(a.lcid, 'PATCH.RTP')):
            if os.path.exists(os.path.join(payload, rtp)):
                failed += apply_one(dll, payload, a.out, rtp.replace('/', '\\'), a.verbose) != 0
        print('done' if not failed else f'{failed} patch(es) reported errors')
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
