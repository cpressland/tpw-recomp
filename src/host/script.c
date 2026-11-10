/*
 * Scripted input for headless runs: what a mouse and keyboard would give, at
 * set times, through win32hle's own input path (hle_input_mouse and
 * hle_input_key, as screen.c feeds them from SDL), so the game sees exactly
 * what a player's input becomes.
 *
 *   --move x,y@s        the mouse to x,y (screen pixels)
 *   --click x,y@s       the left button pressed and released there
 *   --rclick x,y@s      the right button
 *   --key vk@s          a key pressed and released (a virtual-key code:
 *                       27 Escape, 13 Return, 0x41 'A', ...)
 *   --ldown/--lup x,y@s the left button pressed, or released, there (a drag:
 *   --rdown/--rup x,y@s  --move between them); the right; the middle
 *   --mdown/--mup x,y@s
 *   --kdown/--kup vk@s  a key pressed, or released
 *   --wheel n@s         the wheel turned n notches (away from the user positive)
 *
 * s is seconds from the game's entry. Events run in time order on their own
 * thread, each touch of the window manager made holding the machine
 * (mach_enter), as the game's own threads do. A button or key is held for
 * 150 ms, long enough for a game that polls once a frame to see it.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include "win32hle.h"

#define WM_MOUSEMOVE   0x0200u
#define WM_LBUTTONDOWN 0x0201u
#define WM_LBUTTONUP   0x0202u
#define WM_RBUTTONDOWN 0x0204u
#define WM_RBUTTONUP   0x0205u

typedef struct { char kind; int x, y; double t; } ev_t;   /* m move, c click, r right click, k key */
#define MAX_EV 1024
static ev_t g_ev[MAX_EV];
static int g_nev;

/* Consume one option at argv[i]; how many argv entries it took, 0 if not ours. */
int script_arg(int argc, char **argv, int i) {
    if (i + 1 >= argc || g_nev >= MAX_EV) return 0;
    ev_t e = { 0, 0, 0, 0 };
    const char *a = argv[i + 1];
    if (!strcmp(argv[i], "--move")) e.kind = 'm';
    else if (!strcmp(argv[i], "--click")) e.kind = 'c';
    else if (!strcmp(argv[i], "--rclick")) e.kind = 'r';
    else if (!strcmp(argv[i], "--key")) e.kind = 'k';
    else if (!strcmp(argv[i], "--ldown")) e.kind = 'L';
    else if (!strcmp(argv[i], "--lup")) e.kind = 'l';
    else if (!strcmp(argv[i], "--rdown")) e.kind = 'R';
    else if (!strcmp(argv[i], "--rup")) e.kind = 'u';
    else if (!strcmp(argv[i], "--mdown")) e.kind = 'M';
    else if (!strcmp(argv[i], "--mup")) e.kind = 'n';
    else if (!strcmp(argv[i], "--kdown")) e.kind = 'K';
    else if (!strcmp(argv[i], "--kup")) e.kind = 'j';
    else if (!strcmp(argv[i], "--wheel")) e.kind = 'w';
    else return 0;
    if (e.kind == 'k' || e.kind == 'K' || e.kind == 'j' || e.kind == 'w') {
        char *at = strchr(a, '@');
        if (!at) return 0;
        e.x = (int)strtol(a, NULL, 0);
        e.t = atof(at + 1);
    } else if (sscanf(a, "%d,%d@%lf", &e.x, &e.y, &e.t) != 3) {
        return 0;
    }
    g_ev[g_nev++] = e;
    return 2;
}

static void sleep_ms(unsigned ms) { usleep(ms * 1000u); }
static void mouse(uint32_t m, int x, int y) { mach_enter(); hle_input_mouse(m, x, y); mach_leave(); }
static void key(int down, uint32_t vk) { mach_enter(); hle_input_key(down, vk, 0); mach_leave(); }

static int by_time(const void *a, const void *b) {
    double d = ((const ev_t *)a)->t - ((const ev_t *)b)->t;
    return d < 0 ? -1 : d > 0;
}
static void *run(void *arg) {
    (void)arg;
    uint32_t t0 = hle_ticks_ms();
    for (int i = 0; i < g_nev; i++) {
        const ev_t *e = &g_ev[i];
        int32_t wait = (int32_t)(e->t * 1000.0) - (int32_t)(hle_ticks_ms() - t0);
        if (wait > 0) sleep_ms((unsigned)wait);
        switch (e->kind) {
        case 'm':
            mouse(WM_MOUSEMOVE, e->x, e->y);
            fprintf(stderr, "[input] %.1fs move %d,%d\n", e->t, e->x, e->y);
            break;
        case 'c': case 'r': {
            uint32_t dn = e->kind == 'c' ? WM_LBUTTONDOWN : WM_RBUTTONDOWN, up = e->kind == 'c' ? WM_LBUTTONUP : WM_RBUTTONUP;
            mouse(WM_MOUSEMOVE, e->x, e->y);
            sleep_ms(100);
            mouse(dn, e->x, e->y);
            sleep_ms(150);
            mouse(up, e->x, e->y);
            fprintf(stderr, "[input] %.1fs %s %d,%d\n", e->t, e->kind == 'c' ? "click" : "rclick", e->x, e->y);
            break;
        }
        case 'L': case 'l': case 'R': case 'u': case 'M': case 'n': {
            static const struct { char k; uint32_t m; const char *name; } b[6] = {
                { 'L', WM_LBUTTONDOWN, "ldown" }, { 'l', WM_LBUTTONUP, "lup" },
                { 'R', WM_RBUTTONDOWN, "rdown" }, { 'u', WM_RBUTTONUP, "rup" },
                { 'M', 0x0207u, "mdown" }, { 'n', 0x0208u, "mup" } };   /* WM_MBUTTONDOWN, WM_MBUTTONUP */
            for (int j = 0; j < 6; j++)
                if (b[j].k == e->kind) {
                    mouse(WM_MOUSEMOVE, e->x, e->y);
                    mouse(b[j].m, e->x, e->y);
                    fprintf(stderr, "[input] %.1fs %s %d,%d\n", e->t, b[j].name, e->x, e->y);
                }
            break;
        }
        case 'K': case 'j':
            key(e->kind == 'K', (uint32_t)e->x);
            fprintf(stderr, "[input] %.1fs key %d %s\n", e->t, e->x, e->kind == 'K' ? "down" : "up");
            break;
        case 'w':
            mach_enter();
            hle_input_wheel(e->x * 120);
            mach_leave();
            fprintf(stderr, "[input] %.1fs wheel %d\n", e->t, e->x);
            break;
        case 'k':
            key(1, (uint32_t)e->x);
            sleep_ms(150);
            key(0, (uint32_t)e->x);
            fprintf(stderr, "[input] %.1fs key %d\n", e->t, e->x);
            break;
        }
    }
    return NULL;
}

/* Start the script, if there is one: after the window exists. */
void script_start(void) {
    if (!g_nev) return;
    qsort(g_ev, (size_t)g_nev, sizeof *g_ev, by_time);
    pthread_t t;
    pthread_create(&t, NULL, run, NULL);
    pthread_detach(t);
}
