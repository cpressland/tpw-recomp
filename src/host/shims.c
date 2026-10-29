/*
 * The Win32 calls Theme Park World makes that win32hle does not answer yet:
 * small ones, each answered as a Windows 98 machine with no MIDI device and
 * no IME would, and Uniscribe's ScriptString calls on win32hle's GDI. Candidates to move into pcrecomp's win32hle.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
#include <string.h>

#include "win32hle.h"

/* ---- kernel32 ------------------------------------------------------------- */

/* Code page 1252 has no lead bytes. */
static void k_IsDBCSLeadByte(void) { RET(0, 1); }

/* MapViewOfFile with a preferred address the caller may not get: answered as
 * MapViewOfFile, which pops five arguments; the sixth is popped here. */
static void k_MapViewOfFileEx(void) {
    recomp_func_t view = recomp_lookup_import(hle_resolve("MapViewOfFile"));
    view();
    g_esp += 4;
}

/* GetFileAttributesA as win32hle answers it, logged with TPW_PROBETRACE: the
 * game finds its data by probing paths. */
static uint32_t g_get_file_attributes;       /* the original's shim VA */
static void k_GetFileAttributesA(void) {
    const char *path = ASTR(0);
    recomp_lookup_import(g_get_file_attributes)();
    fprintf(stderr, "[probe] GetFileAttributesA \"%s\" -> %08X\n", path ? path : "(null)", g_eax);
}

/* The guest's return addresses, newest first. The game is built without frame
 * pointers, so the stack is scanned: a dword inside the code that follows a
 * call instruction (E8 rel32, FF 15 [abs], FF /2 by register or memory). */
static int after_call(uint32_t ra) {
    if (ra < 0x00401006u || ra >= 0x006FC800u) return 0;
    if (MEM8(ra - 5) == 0xE8) return 1;
    if (MEM8(ra - 6) == 0xFF && MEM8(ra - 5) == 0x15) return 1;
    if (MEM8(ra - 2) == 0xFF && (MEM8(ra - 1) & 0x38) == 0x10) return 1;            /* call reg / [reg] */
    if (MEM8(ra - 3) == 0xFF && (MEM8(ra - 2) & 0x38) == 0x10) return 1;            /* call [reg+d8] */
    if (MEM8(ra - 6) == 0xFF && (MEM8(ra - 5) & 0x38) == 0x10) return 1;            /* call [reg+d32] */
    return 0;
}

void tpw_backtrace(const char *why) {
    fprintf(stderr, "[bt] %s: in sub_%08X;", why, g_cur_func);
    int n = 0;
    for (uint32_t a = g_esp; a < g_esp + 0x1000 && n < 24; a += 4) {
        uint32_t v = MEM32(a);
        if (after_call(v)) { fprintf(stderr, " %08X", v); n++; }
    }
    fprintf(stderr, "\n");
}

/* CreateFileA as win32hle answers it; TPW_BT_FILE=substring prints a
 * backtrace when a matching file is opened. */
static uint32_t g_create_file;
static void k_CreateFileA(void) {
    const char *path = ASTR(0), *want = getenv("TPW_BT_FILE");
    if (path && want && strstr(path, want)) tpw_backtrace(path);
    recomp_lookup_import(g_create_file)();
}

/* VirtualAlloc and VirtualFree as win32hle answers them, logged with
 * TPW_VMTRACE: what the game's own allocators reserve and give back. */
static uint32_t g_virtual_alloc, g_virtual_free, g_map_view, g_unmap_view;
static void k_MapViewOfFile(void) {
    uint32_t h = A32(0), off = A32(3), n = A32(4);
    recomp_lookup_import(g_map_view)();
    fprintf(stderr, "[vm] MapViewOfFile(%X, off %08X, %08X) -> %08X  from sub_%08X\n", h, off, n, g_eax, g_cur_func);
}
static void k_UnmapViewOfFile(void) {
    uint32_t va = A32(0);
    recomp_lookup_import(g_unmap_view)();
    fprintf(stderr, "[vm] UnmapViewOfFile(%08X) -> %u  from sub_%08X\n", va, g_eax, g_cur_func);
}
static void k_VirtualAlloc(void) {
    uint32_t addr = A32(0), size = A32(1), type = A32(2);
    recomp_lookup_import(g_virtual_alloc)();
    fprintf(stderr, "[vm] VirtualAlloc(%08X, %08X, %X) -> %08X  from sub_%08X\n", addr, size, type, g_eax, g_cur_func);
}
static void k_VirtualFree(void) {
    uint32_t addr = A32(0), size = A32(1), type = A32(2);
    recomp_lookup_import(g_virtual_free)();
    fprintf(stderr, "[vm] VirtualFree(%08X, %08X, %X) -> %u  from sub_%08X\n", addr, size, type, g_eax, g_cur_func);
}

/* Point the game's IAT slots for `name` at the shim registered as `wrapper`,
 * which can still reach the original through its VA, `*orig` (looked up
 * right before each call: the lookup hands back a trampoline keyed on it).
 * The IAT holds shim VAs, which nothing else in the image does, so a scan of
 * the image finds them. */
static void hook_import(uint32_t base, uint32_t span, const char *name, const char *wrapper,
                        uint32_t *orig) {
    uint32_t from = hle_resolve(name), to = hle_resolve(wrapper);
    *orig = from;
    for (uint32_t va = base; va + 4 <= base + span; va += 4)
        if (MEM32(va) == from) MEM32(va) = to;
}

/* After the image is bound: the logging wrappers, when asked for. */
void tpw_shims_bound(uint32_t base, uint32_t span) {
    if (getenv("TPW_PROBETRACE"))
        hook_import(base, span, "GetFileAttributesA", "tpw!GetFileAttributesA", &g_get_file_attributes);
    if (getenv("TPW_VMTRACE")) {
        hook_import(base, span, "VirtualAlloc", "tpw!VirtualAlloc", &g_virtual_alloc);
        hook_import(base, span, "VirtualFree", "tpw!VirtualFree", &g_virtual_free);
        hook_import(base, span, "MapViewOfFile", "tpw!MapViewOfFile", &g_map_view);
        hook_import(base, span, "UnmapViewOfFile", "tpw!UnmapViewOfFile", &g_unmap_view);
    }
    if (getenv("TPW_BT_FILE"))
        hook_import(base, span, "CreateFileA", "tpw!CreateFileA", &g_create_file);
}

/* ---- user32 --------------------------------------------------------------- */

static void u_GetKeyboardLayout(void) { RET(0x04090409u, 1); }    /* US English */

static void u_GetKeyboardLayoutList(void) {                      /* (n, list) */
    if (A32(0) && A32(1)) MEM32(A32(1)) = 0x04090409u;
    RET(1, 2);
}

/* The game's animated cursors (data\ui\cursors\*.ani): the host draws its own
 * pointer, so any nonzero handle will do. */
static void u_LoadCursorFromFileA(void) { RET(0x0000C0DEu, 1); }

static void u_GetDoubleClickTime(void) { RET(500, 0); }
static void u_SwapMouseButton(void) { RET(0, 1); }

/* (action, uiParam, pvParam, winIni): the work area is the whole screen; the
 * screen saver is off; anything else succeeds without changing a thing. */
static void u_SystemParametersInfoA(void) {
    uint32_t action = A32(0), pv = A32(2);
    switch (action) {
    case 0x0030:                                                 /* SPI_GETWORKAREA */
        if (pv) {
            recomp_func_t metrics = recomp_lookup_import(hle_resolve("GetSystemMetrics"));
            uint32_t w, h;
            g_esp -= 8; MEM32(g_esp + 4) = 0; metrics(); w = g_eax;  /* SM_CXSCREEN */
            g_esp -= 8; MEM32(g_esp + 4) = 1; metrics(); h = g_eax;  /* SM_CYSCREEN */
            MEM32(pv) = 0; MEM32(pv + 4) = 0; MEM32(pv + 8) = w; MEM32(pv + 12) = h;
        }
        break;
    case 0x0010:                                                 /* SPI_GETSCREENSAVEACTIVE */
        if (pv) MEM32(pv) = 0;
        break;
    default:
        break;
    }
    RET(1, 4);
}

static void u_MessageBoxW(void) {
    char text[512] = "", cap[128] = "";
    const uint16_t *t = (const uint16_t *)APTR(1), *c = (const uint16_t *)APTR(2);
    for (int i = 0; t && t[i] && i < 511; i++) text[i] = t[i] < 128 ? (char)t[i] : '?';
    for (int i = 0; c && c[i] && i < 127; i++) cap[i] = c[i] < 128 ? (char)c[i] : '?';
    fprintf(stderr, "[messagebox] %s: %s -> 1\n", cap, text);
    RET(1, 4);
}

/* ---- gdi32 ---------------------------------------------------------------- */

static void g_SetMapMode(void) { RET(1, 2); }                     /* the old mode: MM_TEXT */

/* ---- winmm: no MIDI device ------------------------------------------------ */

static void w_midiOutGetNumDevs(void) { RET(0, 0); }
static void w_midiOutGetDevCapsA(void) { RET(2, 3); }            /* MMSYSERR_BADDEVICEID */
static void w_midiOutOpen(void) { RET(2, 5); }
static void w_midiOutClose(void) { RET(0, 1); }
static void w_midiOutPrepareHeader(void) { RET(0, 3); }
static void w_midiOutShortMsg(void) { RET(0, 2); }
static void w_midiOutLongMsg(void) { RET(0, 3); }
static void w_mmioFlush(void) { RET(0, 2); }

/* ---- imm32: no input method ----------------------------------------------- */

static void i_ImmCreateContext(void) { RET(0, 0); }
static void i_ImmDestroyContext(void) { RET(1, 1); }
static void i_ImmGetOpenStatus(void) { RET(0, 1); }
static void i_ImmSetCompositionWindow(void) { RET(1, 2); }
static void i_ImmSetCompositionStringA(void) { RET(0, 6); }
static void i_ImmGetDescriptionA(void) { RET(0, 3); }
static void i_ImmGetProperty(void) { RET(0, 2); }

/* ---- usp10: Uniscribe's ScriptString calls, on win32hle's GDI ------------
 * The game draws TrueType text by selecting a font into a DIB section's DC
 * and having Uniscribe lay out and draw each string. Text here is
 * left-to-right Latin, so the layout is GDI's: each character's advance is
 * measured with GetTextExtentPoint32A, and ScriptStringOut is TextOutA. */
#define E_FAIL 0x80004005u

/* Call a registered shim as the guest would: stdcall arguments on the guest
 * stack under a (zero) return address; the shim pops them. */
static uint32_t call_shim(const char *name, int n, const uint32_t *args) {
    g_esp -= 4u * (uint32_t)(n + 1);
    MEM32(g_esp) = 0;
    for (int i = 0; i < n; i++) MEM32(g_esp + 4u + 4u * (uint32_t)i) = args[i];
    recomp_lookup_import(hle_resolve(name))();
    return g_eax;
}

/* The analysis, in guest memory: what ScriptString_pSize points into. */
typedef struct {
    uint32_t hdc, n;
    int32_t cx, cy;                     /* the SIZE the game reads */
    char text[1];                       /* n bytes, then n advances (int32) */
} ssa_t;
static int32_t *ssa_widths(ssa_t *a) { return (int32_t *)(void *)(a->text + ((a->n + 4) & ~3u)); }

static void s_ScriptStringAnalyse(void) {
    /* (hdc, string, count, glyphs, charset, flags, reqwidth, control, state, dx, tabdef, inclass, &ssa) */
    uint32_t hdc = A32(0), str = A32(1), n = A32(2), out = A32(12);
    int wide = (int32_t)A32(4) == -1;
    if (!out || (int32_t)n <= 0) RET(E_FAIL, 13);
    uint32_t size = (uint32_t)sizeof(ssa_t) + ((n + 4) & ~3u) + 4u * n;
    ssa_t *a = (ssa_t *)hle_alloc(size);
    a->hdc = hdc, a->n = n;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t c = wide ? MEM16(str + 2 * i) : MEM8(str + i);
        a->text[i] = (char)(c < 256 ? c : '?');
    }
    uint32_t va = hle_va(a), text = va + (uint32_t)offsetof(ssa_t, text);
    uint32_t mark = hle_scratch_mark(), sz = hle_scratch(8);
    int32_t *w = ssa_widths(a), prev = 0;
    for (uint32_t i = 1; i <= n; i++) {            /* each advance: the prefix's width, less the last */
        uint32_t args[4] = { hdc, text, i, sz };
        call_shim("GetTextExtentPoint32A", 4, args);
        int32_t cx = (int32_t)MEM32(sz);
        w[i - 1] = cx - prev, prev = cx;
        a->cx = cx, a->cy = (int32_t)MEM32(sz + 4);
    }
    hle_scratch_release(mark);
    MEM32(out) = va;
    RET(0, 13);
}
static void s_ScriptStringFree(void) {                      /* (&ssa) */
    uint32_t p = A32(0);
    if (p && MEM32(p)) { hle_free(hle_ptr(MEM32(p))); MEM32(p) = 0; }
    RET(0, 1);
}
static void s_ScriptStringOut(void) {                       /* (ssa, x, y, options, rect, minsel, maxsel, disabled) */
    uint32_t va = A32(0);
    if (!va) RET(E_FAIL, 8);
    ssa_t *a = (ssa_t *)hle_ptr(va);
    uint32_t args[5] = { a->hdc, A32(1), A32(2), va + (uint32_t)offsetof(ssa_t, text), a->n };
    call_shim("TextOutA", 5, args);
    RET(0, 8);
}
static void s_ScriptString_pSize(void) {                    /* (ssa) -> const SIZE* */
    uint32_t va = A32(0);
    RET(va ? va + (uint32_t)offsetof(ssa_t, cx) : 0, 1);
}
static void s_ScriptStringGetLogicalWidths(void) {          /* (ssa, &dx) */
    uint32_t va = A32(0), dx = A32(1);
    if (!va || !dx) RET(E_FAIL, 2);
    ssa_t *a = (ssa_t *)hle_ptr(va);
    memcpy(hle_ptr(dx), ssa_widths(a), 4u * a->n);
    RET(0, 2);
}

/* ---- dinput: unavailable for now, so the game reads windows' messages ----- */

static void d_DirectInputCreateA(void) {                         /* (hinst, version, out, outer) */
    if (A32(2)) MEM32(A32(2)) = 0;
    RET(E_FAIL, 4);
}

const win32hle_shim tpw_shims[] = {
    { "tpw!GetFileAttributesA", k_GetFileAttributesA },
    { "tpw!CreateFileA", k_CreateFileA },
    { "tpw!VirtualAlloc", k_VirtualAlloc },
    { "tpw!VirtualFree", k_VirtualFree },
    { "tpw!MapViewOfFile", k_MapViewOfFile },
    { "tpw!UnmapViewOfFile", k_UnmapViewOfFile },
    { "IsDBCSLeadByte", k_IsDBCSLeadByte },
    { "MapViewOfFileEx", k_MapViewOfFileEx },
    { "GetKeyboardLayout", u_GetKeyboardLayout },
    { "GetKeyboardLayoutList", u_GetKeyboardLayoutList },
    { "LoadCursorFromFileA", u_LoadCursorFromFileA },
    { "GetDoubleClickTime", u_GetDoubleClickTime },
    { "SwapMouseButton", u_SwapMouseButton },
    { "SystemParametersInfoA", u_SystemParametersInfoA },
    { "MessageBoxW", u_MessageBoxW },
    { "SetMapMode", g_SetMapMode },
    { "midiOutGetNumDevs", w_midiOutGetNumDevs },
    { "midiOutGetDevCapsA", w_midiOutGetDevCapsA },
    { "midiOutOpen", w_midiOutOpen },
    { "midiOutClose", w_midiOutClose },
    { "midiOutPrepareHeader", w_midiOutPrepareHeader },
    { "midiOutShortMsg", w_midiOutShortMsg },
    { "midiOutLongMsg", w_midiOutLongMsg },
    { "mmioFlush", w_mmioFlush },
    { "ImmCreateContext", i_ImmCreateContext },
    { "ImmDestroyContext", i_ImmDestroyContext },
    { "ImmGetOpenStatus", i_ImmGetOpenStatus },
    { "ImmSetCompositionWindow", i_ImmSetCompositionWindow },
    { "ImmSetCompositionStringA", i_ImmSetCompositionStringA },
    { "ImmGetDescriptionA", i_ImmGetDescriptionA },
    { "ImmGetProperty", i_ImmGetProperty },
    { "ScriptStringAnalyse", s_ScriptStringAnalyse },
    { "ScriptStringFree", s_ScriptStringFree },
    { "ScriptStringOut", s_ScriptStringOut },
    { "ScriptString_pSize", s_ScriptString_pSize },
    { "ScriptStringGetLogicalWidths", s_ScriptStringGetLogicalWidths },
    { "DirectInputCreateA", d_DirectInputCreateA },
    { 0, 0 }
};
