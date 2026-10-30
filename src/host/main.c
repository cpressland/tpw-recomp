/*
 * Theme Park World - the native host, for macOS (and later Linux).
 *
 * The lifted C (src/recomp/gen, from run_lift.py) on pcrecomp's
 * runtime/win32hle: every Win32 import the game makes is answered by
 * win32hle's own implementation, so no Windows and no Wine. What is here is
 * what is specific to this game: where the image goes, the registry the
 * installer would have written, the command line, and the fault report.
 *
 * 64-bit (macOS) the guest's memory is one 4 GB block of the host's, and the
 * image goes at its own address, 0x00400000, inside it.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <limits.h>
#include <pthread.h>

#include <SDL2/SDL.h>
#include "win32hle.h"
#include "pe_loader.h"
#include "host.h"
#include "recomp_target.h"
#ifdef RECOMP_TRACE
void tpw_trace_start(void);           /* trace.c */
#else
void recomp_dump_trace(const char *why) { (void)why; }
#endif

extern const uint32_t tpw_entry_va;   /* recomp_dispatch.c */
int  script_arg(int argc, char **argv, int i);   /* script.c */
void script_start(void);

#define TPW_IMAGE_BASE 0x00400000u
#define HKLM 0x80000002u

static int g_headless;
static int g_windowed;                   /* --windowed: not full screen at launch (F11 toggles) */
static int g_free_mouse;                 /* --free-mouse: the pointer may leave the picture */
static unsigned g_watchdog_s;
static const char *g_resolution;         /* --resolution WxH | options */
static const char *g_shot;               /* --shot FILE: the screen, as a BMP */
static unsigned g_shot_s = 10;           /* --shot-after S */
static unsigned g_shot_every;            /* --shot-every S: FILE gets _1, _2, ... before .bmp */

/* ---- game-specific answers ------------------------------------------------ */

/* Message boxes print; headless, a question is answered No. */
static void tpw_MessageBoxA(void) {
    uint32_t type = A32(3), buttons = type & 0xF;
    uint32_t answer = g_headless && (buttons == 4 || buttons == 3) ? 7u : buttons == 4 || buttons == 3 ? 6u : 1u;
    fprintf(stderr, "[messagebox] %s: %s -> %u\n", ASTR(2) ? ASTR(2) : "", ASTR(1) ? ASTR(1) : "", answer);
    RET(answer, 4);
}

extern const win32hle_shim tpw_won_shims[];     /* won_stubs.c (generated): the online libraries */
extern const win32hle_shim tpw_qmixer_shims[];  /* qmixer.c: QSound's mixer */
extern const win32hle_shim tpw_shims[];         /* shims.c: what win32hle lacks */
void tpw_shims_bound(uint32_t base, uint32_t span);

static const win32hle_shim g_tpw_shims[] = {
    { "MessageBoxA", tpw_MessageBoxA },
    { 0, 0 }
};

/* --resolution: the 2.0 patch's own override, data\Resolution.sam (its
 * _Resolution.sam, renamed and set): 512x384 to 2048x1536, 4:3 (and 5:4).
 * Any other size, widescreen included, or "native" (the desktop's), takes
 * the 2048x1536 slot, and the size the game sets is replaced as it is set
 * (tpw_size_override, run_lift.py's patch); the file remembers it in a
 * comment the game skips. "options" removes the file, and the game's Options
 * screen decides again. */
static const int g_res[][2] = { { 0, 0 }, { 512, 384 }, { 640, 480 }, { 800, 600 }, { 1024, 768 },
                                { 1280, 1024 }, { 1600, 1200 }, { 2048, 1536 } };
static unsigned g_size_w, g_size_h;      /* a size of our own, not the game's; 0: the game's */
#define TPW_SIZE_TAG "#tpw-size"

void tpw_size_override(uint32_t *w, uint32_t *h) {
    static int told;
    if (!g_size_w) return;
    *w = g_size_w, *h = g_size_h;
    if (!told++) fprintf(stderr, "[tpw] screen %ux%u\n", g_size_w, g_size_h);
}

static int desktop_size(int *w, int *h) {
    SDL_DisplayMode m;
    if (SDL_InitSubSystem(SDL_INIT_VIDEO) != 0 || SDL_GetDesktopDisplayMode(0, &m) != 0) return -1;
    *w = m.w, *h = m.h;
    return 0;
}

static int set_resolution(const char *game, const char *want) {
    char path[PATH_MAX + 32];
    snprintf(path, sizeof path, "%s/data/Resolution.sam", game);
    if (!strcmp(want, "options")) { remove(path); return 0; }
    int w = 0, h = 0, n = -1;
    if (!strcmp(want, "native")) {
        if (desktop_size(&w, &h) != 0) { fprintf(stderr, "--resolution native: no desktop size (%s)\n", SDL_GetError()); return -1; }
    } else if (sscanf(want, "%dx%d", &w, &h) != 2) {
        w = 0;
    }
    if (w < 640 || h < 480 || w > 8192 || h > 4096) {
        fprintf(stderr, "--resolution: WxH (640x480 up to 8192x4096, any shape), native, or options\n");
        return -1;
    }
    for (int i = 1; i < 8; i++) if (g_res[i][0] == w && g_res[i][1] == h) n = i;
    FILE *f = fopen(path, "w");
    if (!f) return -1;
    fprintf(f, "#\n#\tTheme Park World Resolution Override, written by tpw --resolution\n#\n");
    if (n < 0) fprintf(f, TPW_SIZE_TAG " %dx%d\n", w, h), n = 7;
    fprintf(f, "\nRes.RESOLUTION\t%d\n", n);
    fclose(f);
    return 0;
}

/* The size a run uses: a size of our own, if the override names one. */
static void read_resolution(const char *game) {
    char path[PATH_MAX + 32], line[128];
    unsigned w, h;
    snprintf(path, sizeof path, "%s/data/Resolution.sam", game);
    FILE *f = fopen(path, "r");
    if (!f) return;
    while (fgets(line, sizeof line, f))
        if (sscanf(line, TPW_SIZE_TAG " %ux%u", &w, &h) == 2) g_size_w = w, g_size_h = h;
    fclose(f);
}

/* What the installer and the 2.0 patch leave in the registry (the no-CD
 * build's tp.reg, docs/RECON.md). The game folder is drive C:. The language
 * picks Data\Language\<name>: this European disc carries English (0x0809),
 * not the American release's 0x0409. */
static void seed_registry(void) {
    const char *key = "Software\\Bullfrog Productions Ltd\\Theme Park World";
    hle_reg_set_string(HKLM, key, "Version", "2.0");
    hle_reg_set_dword(HKLM, key, "Language", 0x0809);
    hle_reg_set_dword(HKLM, key, "BuildTypeCode", 0);
    hle_reg_set_string(HKLM, "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\TP.exe", "Path", "C:\\");
}

/* ---- faults and the watchdog ---------------------------------------------- */
static void report(const char *why) {
    fprintf(stderr, "\n=== %s ===\n  in lifted sub_%08X, last import %s\n", why, g_cur_func,
            g_cur_import ? g_cur_import : "(none)");
    fprintf(stderr, "  eax=%08X ecx=%08X edx=%08X ebx=%08X esp=%08X ebp=%08X esi=%08X edi=%08X\n",
            g_eax, g_ecx, g_edx, g_ebx, g_esp, g_ebp, g_esi, g_edi);
    fprintf(stderr, "last indirect calls (newest first):\n");
    for (int i = 1; i <= 40 && i <= (int)g_icall_trace_idx; i++) {
        uint32_t k = (g_icall_trace_idx - i) & (ICALL_TRACE_SIZE - 1);
        const char *nm = hle_name(g_icall_trace[k]);
        fprintf(stderr, "  0x%08X  from 0x%08X  %s\n", g_icall_trace[k], g_icall_from[k], nm ? nm : "");
    }
    fprintf(stderr, "guest stack at %08X:", g_esp);
    for (int k = 0; k < 32; k++) fprintf(stderr, "%s%08X", k % 8 ? " " : "\n  ", MEM32(g_esp + 4 * k));
    fprintf(stderr, "\n");
}

static void on_fault(int sig, siginfo_t *si, void *uc) {
    static volatile int once;
    (void)uc;
    if (__sync_lock_test_and_set(&once, 1)) _exit(3);
    char why[96];
    snprintf(why, sizeof why, "fault: signal %d, address %p", sig, si->si_addr);
    report(why);
    recomp_dump_trace(why);
    fflush(stderr);
    _exit(3);
}

static void *watchdog(void *arg) {
    (void)arg;
    sleep(g_watchdog_s);
    fprintf(stderr, "\n[watchdog] %u s: in sub_%08X, last import %s, %u indirect calls\n",
            g_watchdog_s, g_cur_func, g_cur_import ? g_cur_import : "(none)", g_icall_count);
    fflush(stderr);
    hle_exit(4);
    return NULL;
}

#ifdef RECOMP_TRACE
static void on_exit_trace(int code) {
    char why[48];
    snprintf(why, sizeof why, "exit %d", code);
    recomp_dump_trace(why);
}
#endif

/* The primary surface as a 24-bit BMP: 16-bit 565, or 8-bit through its palette. */
static int write_bmp(const char *path) {
    const uint8_t *px;
    const uint32_t *pal;
    int w, h, pitch, bpp;
    /* Copied holding the machine, so a Flip on the game's thread cannot land
     * half way through the copy. */
    mach_enter();
    if (!hle_dd_frame(&px, &w, &h, &pitch, &bpp, &pal, NULL)) { mach_leave(); return -1; }
    size_t bytes = (size_t)pitch * (size_t)h;
    uint8_t *copy = malloc(bytes);
    uint32_t palcopy[256];
    if (pal) memcpy(palcopy, pal, sizeof palcopy);
    if (copy) memcpy(copy, px, bytes);
    mach_leave();
    if (!copy) return -1;
    px = copy;
    if (pal) pal = palcopy;
    FILE *f = fopen(path, "wb");
    if (!f) { free(copy); return -1; }
    int row = (w * 3 + 3) & ~3;
    uint32_t size = 54u + (uint32_t)row * (uint32_t)h;
    uint8_t hdr[54] = { 'B', 'M' };
    memcpy(hdr + 2, &size, 4);
    hdr[10] = 54, hdr[14] = 40;
    memcpy(hdr + 18, &w, 4);
    memcpy(hdr + 22, &h, 4);
    hdr[26] = 1, hdr[28] = 24;
    fwrite(hdr, 1, 54, f);
    uint8_t *line = calloc(1, (size_t)row);
    for (int y = h - 1; y >= 0; y--) {
        const uint8_t *src = px + (size_t)y * (size_t)pitch;
        for (int x = 0; x < w; x++) {
            uint8_t r, g, b;
            if (bpp == 16) {
                uint16_t v = ((const uint16_t *)src)[x];
                r = (uint8_t)((v >> 11) << 3), g = (uint8_t)(((v >> 5) & 63) << 2), b = (uint8_t)((v & 31) << 3);
            } else if (bpp == 8) {
                uint32_t e = pal ? pal[src[x]] : 0;
                r = (uint8_t)e, g = (uint8_t)(e >> 8), b = (uint8_t)(e >> 16);
            } else {
                uint32_t v = ((const uint32_t *)src)[x];
                r = (uint8_t)(v >> 16), g = (uint8_t)(v >> 8), b = (uint8_t)v;
            }
            line[x * 3] = b, line[x * 3 + 1] = g, line[x * 3 + 2] = r;
        }
        fwrite(line, 1, (size_t)row, f);
    }
    free(line);
    free(copy);
    fclose(f);
    return 0;
}

static void *shooter(void *arg) {
    (void)arg;
    sleep(g_shot_s);
    for (int k = 1;; k++) {
        char path[PATH_MAX + 16];
        if (g_shot_every) {
            const char *dot = strrchr(g_shot, '.');
            int stem = dot ? (int)(dot - g_shot) : (int)strlen(g_shot);
            snprintf(path, sizeof path, "%.*s_%d%s", stem, g_shot, k, dot ? dot : ".bmp");
        } else {
            snprintf(path, sizeof path, "%s", g_shot);
        }
        fprintf(stderr, "[shot] %s: %s\n", path, write_bmp(path) ? "no primary surface yet" : "written");
        if (!g_shot_every) return NULL;
        sleep(g_shot_every);
    }
}

static uint32_t exe_stamp(const char *path) {
    uint8_t head[4096];
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    size_t n = fread(head, 1, sizeof head, f);
    fclose(f);
    if (n < 0x40) return 0;
    uint32_t nt = head[0x3C] | head[0x3D] << 8 | head[0x3E] << 16 | (uint32_t)head[0x3F] << 24;
    if (nt + 12 > n) return 0;
    return head[nt + 8] | head[nt + 9] << 8 | head[nt + 10] << 16 | (uint32_t)head[nt + 11] << 24;
}

int main(int argc, char **argv) {
    const char *game = "game";
    int run = 0;
    setvbuf(stderr, NULL, _IONBF, 0);
    for (int i = 1; i < argc; i++) {
        int n = script_arg(argc, argv, i);
        if (n) { i += n - 1; continue; }
        if (!strcmp(argv[i], "--run")) run = 1;
        else if (!strcmp(argv[i], "--headless")) g_headless = 1;
        else if (!strcmp(argv[i], "--windowed")) g_windowed = 1;
        else if (!strcmp(argv[i], "--free-mouse")) g_free_mouse = 1;
        else if (!strcmp(argv[i], "--game") && i + 1 < argc) game = argv[++i];
        else if (!strcmp(argv[i], "--mute")) hle_dsound_set_master(0.0f);
        else if (!strcmp(argv[i], "--watchdog") && i + 1 < argc) g_watchdog_s = (unsigned)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--trace")) win32hle_trace = 1;
        else if (!strcmp(argv[i], "--resolution") && i + 1 < argc) g_resolution = argv[++i];
        else if (!strcmp(argv[i], "--shot") && i + 1 < argc) {
            static char path[PATH_MAX];          /* absolute: the run enters the game folder */
            const char *p = argv[++i];
            if (p[0] == '/') snprintf(path, sizeof path, "%s", p);
            else { char cwd[PATH_MAX]; snprintf(path, sizeof path, "%s/%s", getcwd(cwd, sizeof cwd) ? cwd : ".", p); }
            g_shot = path;
        }
        else if (!strcmp(argv[i], "--shot-after") && i + 1 < argc) g_shot_s = (unsigned)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--shot-every") && i + 1 < argc) g_shot_every = (unsigned)strtoul(argv[++i], NULL, 0);
        else {
            printf("usage: tpw [--run] [--headless] [--windowed] [--free-mouse] [--resolution WxH|native|options] [--game DIR] [--mute] [--watchdog S] [--trace] [--shot FILE.bmp] [--shot-after S] [--shot-every S]\n"
                   "           [--move x,y@s] [--click x,y@s] [--rclick x,y@s] [--key vk@s]\n");
            return !strcmp(argv[i], "--help") || !strcmp(argv[i], "-h") ? 0 : 1;
        }
    }
    char game_full[PATH_MAX], exe_host[PATH_MAX + 16];
    if (!realpath(game, game_full)) { fprintf(stderr, "no game folder at %s\n", game); return 1; }
    hle_set_drive('C', game_full);
    if (!hle_host_path("C:\\" TPW_EXE_NAME, exe_host, sizeof exe_host)) {
        fprintf(stderr, "no " TPW_EXE_NAME " in %s\n", game_full);
        return 1;
    }
    uint32_t stamp = exe_stamp(exe_host);
    if (stamp != TPW_EXE_STAMP) {
        fprintf(stderr, "%s is not the build this host was lifted from (PE timestamp 0x%08X, wanted 0x%08X).\n",
                exe_host, stamp, TPW_EXE_STAMP);
        return 1;
    }

    {   /* faults report on their own stack: a guest stack overflow is one */
        static uint8_t alt[1 << 16];
        stack_t ss = { .ss_sp = alt, .ss_size = sizeof alt };
        sigaltstack(&ss, NULL);
        struct sigaction sa;
        memset(&sa, 0, sizeof sa);
        sa.sa_sigaction = on_fault;
        sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
        sigaction(SIGSEGV, &sa, NULL);
        sigaction(SIGBUS, &sa, NULL);
        sigaction(SIGFPE, &sa, NULL);
        sigaction(SIGILL, &sa, NULL);
    }

    win32hle_register(g_tpw_shims);    /* first: the game's own answers win */
    win32hle_register(tpw_won_shims);
    win32hle_register(tpw_qmixer_shims);
    win32hle_register(tpw_shims);
    recomp_host_init();
    seed_registry();
    printf(TPW_TITLE " recomp host\n  lifted functions in dispatch: %u\n", recomp_dispatch_count);

    pe_image img;
    if (recomp_pe_map(exe_host, &img) != 0 || img.base != TPW_IMAGE_BASE) {
        fprintf(stderr, "cannot map %s at 0x%08X\n", exe_host, TPW_IMAGE_BASE);
        return 1;
    }
    hle_module_add("C:\\" TPW_EXE_NAME, img.base, img.resource_rva, img.resource_size, 1);
    int unresolved = recomp_pe_bind(&img, hle_resolve_or_stub);
    tpw_shims_bound(img.base, img.span);
    printf("  mapped %s: 0x%08X-0x%08X, %d imports not implemented yet\n", exe_host, img.base,
           img.base + img.span, unresolved);
    hle_set_command_line("\"C:\\" TPW_EXE_NAME "\"");

    if (!run) {
        printf("\n(dry run: image mapped and bound; --run enters 0x%08X)\n", tpw_entry_va);
        return 0;
    }
    if (g_resolution && set_resolution(game_full, g_resolution) != 0) return 1;
    read_resolution(game_full);
    if (chdir(game_full) != 0) { fprintf(stderr, "cannot enter %s\n", game_full); return 1; }
    /* Full screen in its own Space, as a 1999 game ran: the game draws its own
     * cursor, and the mouse is held in the picture either way (Ctrl+Cmd lets go). */
    hle_screen_confine(!g_free_mouse);
    if (hle_screen_open(TPW_TITLE, !g_windowed, g_headless) != 0) return 1;
#ifdef RECOMP_TRACE
    tpw_trace_start();
    hle_on_exit = on_exit_trace;
#endif
    script_start();
    if (g_shot) {
        pthread_t t;
        pthread_create(&t, NULL, shooter, NULL);
        pthread_detach(t);
    }
    if (g_watchdog_s) {
        pthread_t t;
        pthread_create(&t, NULL, watchdog, NULL);
        pthread_detach(t);
    }
    printf("  entering 0x%08X\n\n", tpw_entry_va);
    fflush(stdout);
    uint32_t rc = hle_call_guest(tpw_entry_va, 0, NULL);
    printf("\nentry returned eax=%08X\n", rc);
    hle_exit((int)rc);
    return 0;
}
