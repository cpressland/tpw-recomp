/*
 * A game controller as Theme Park World's mouse and keys. SDL's
 * GameController reads an Xbox pad, a Steam Deck's controls and a
 * PlayStation pad alike (Xbox names below); each frame (the screen's poll
 * hook, on the window's thread) it becomes what the game already
 * understands, through win32hle's input path, so every screen works as
 * with a mouse:
 *
 *   left stick      the cursor (pushed to an edge, the park scrolls)
 *   A               the left button (held: a drag - paths, sliders)
 *   B               the right button (cancel, back)
 *   X (held)        Ctrl: click on to build more of the same
 *   right stick     scroll the park (the cursor pushed to that edge, then
 *                   put back)
 *   LB / RB         rotate the park (a middle-button drag, the cursor put
 *                   back)
 *   LT / RT         zoom out / in (the wheel, faster pressed harder)
 *   d-pad < >       rotate the item being placed (the , and . keys)
 *   d-pad ^ v       zoom in / out a notch
 *   Start           Escape: the pause menu, or out of a dialog
 *   left stick in   the cursor slow, for small things, while held
 *
 * The game's camera, found by trying it: the cursor at an edge scrolls, a
 * middle-button drag rotates, the wheel zooms.
 *
 * TPW_VPAD=FILE: a virtual controller (SDL's), driven by FILE, for tests
 * without one: lines "seconds control value", seconds from the game's
 * start; control a b x y back guide start ls rs lb rb up down left right
 * (value 0 or 1) or lx ly rx ry lt rt (value -1..1, triggers 0..1).
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

#define WM_LBUTTONDOWN 0x0201u
#define WM_LBUTTONUP   0x0202u
#define WM_RBUTTONDOWN 0x0204u
#define WM_RBUTTONUP   0x0205u
#define WM_MBUTTONDOWN 0x0207u
#define WM_MBUTTONUP   0x0208u

#define DEAD 0.18                  /* a stick's dead zone, of full tilt */
#define CURSOR_SPEED 1.1           /* screen heights a second, at full tilt */
#define ROTATE_SPEED 0.45          /* screen heights a second of middle-drag */
#define ROTATE_SPAN 200            /* pixels of drag before the button is taken up again */
#define ZOOM_RATE 10.0             /* wheel notches a second, a trigger fully in */

static SDL_GameController *g_pad;
static SDL_Joystick *g_vjoy;       /* TPW_VPAD's, the only one used then */
static int g_vindex = -1;
static uint32_t g_start_ms, g_last_ms, g_scan_ms;
static double g_fx, g_fy;          /* cursor movement not yet whole pixels */
static uint8_t g_held[SDL_CONTROLLER_BUTTON_MAX];

/* Scrolling: the cursor pushed toward an edge, by (pan_x, pan_y) so far. */
static int g_panning, g_pan_x, g_pan_y;
/* Rotating: the middle button down, the cursor dragged by rot_x so far. */
static int g_rotating, g_rot_x;
static double g_rot_f, g_zoom_f;

static void screen_size(int *w, int *h) {
    int bpp;
    hle_dd_mode(w, h, &bpp);
    if (*w <= 0 || *h <= 0) *w = 640, *h = 480;
}
/* The cursor moved by (dx, dy) as a mouse would move it; how far it went. */
static void move(int dx, int dy, int *mx, int *my) {
    int x0, y0, x1, y1;
    hle_input_position(&x0, &y0);
    if (dx || dy) hle_input_motion(dx, dy);
    hle_input_position(&x1, &y1);
    if (mx) *mx = x1 - x0;
    if (my) *my = y1 - y0;
}
static void button(uint32_t msg) {
    int x, y;
    hle_input_position(&x, &y);
    hle_input_mouse(msg, x, y);
}
static void key(int down, uint32_t vk, uint32_t scan) { hle_input_key(down, vk, scan); }

static double axis(SDL_GameControllerAxis a) {
    double v = SDL_GameControllerGetAxis(g_pad, a) / 32767.0;
    return v < -1 ? -1 : v > 1 ? 1 : v;
}
/* A stick, its dead zone taken out and the rest curved (fine near the
 * middle, fast at full tilt). */
static void stick(SDL_GameControllerAxis ax, SDL_GameControllerAxis ay, double *x, double *y) {
    double sx = axis(ax), sy = axis(ay), m = sqrt(sx * sx + sy * sy);
    if (m < DEAD) { *x = *y = 0; return; }
    double t = ((m > 1 ? 1 : m) - DEAD) / (1 - DEAD);   /* 0 at the dead zone's edge, 1 at full tilt */
    *x = sx / m * t * t, *y = sy / m * t * t;
}

/* ---- scrolling: the right stick pushes the cursor to an edge ---- */
static void pan(double sx, double sy) {
    int want = fabs(sx) > 0.3 || fabs(sy) > 0.3;
    if (!want) {
        if (g_panning) move(-g_pan_x, -g_pan_y, NULL, NULL), g_panning = 0, g_pan_x = g_pan_y = 0;
        return;
    }
    int w, h, x, y;
    screen_size(&w, &h);
    hle_input_position(&x, &y);
    int ox = x - g_pan_x, oy = y - g_pan_y;             /* where it was before the push */
    int tx = sx > 0.3 ? w - 1 : sx < -0.3 ? 0 : ox, ty = sy > 0.3 ? h - 1 : sy < -0.3 ? 0 : oy;
    int mx, my;
    move(tx - x, ty - y, &mx, &my);
    g_pan_x += mx, g_pan_y += my, g_panning = 1;
}

/* ---- rotating: a shoulder drags with the middle button ---- */
static void rotate(int dir, double dt) {
    if (!dir) {
        if (g_rotating) button(WM_MBUTTONUP), move(-g_rot_x, 0, NULL, NULL), g_rotating = 0, g_rot_x = 0;
        return;
    }
    int w, h;
    screen_size(&w, &h);
    if (!g_rotating) button(WM_MBUTTONDOWN), g_rotating = 1, g_rot_x = 0, g_rot_f = 0;
    g_rot_f += dir * ROTATE_SPEED * h * dt;
    int dx = (int)g_rot_f, mx;
    g_rot_f -= dx;
    move(dx, 0, &mx, NULL);
    g_rot_x += mx;
    if (abs(g_rot_x) >= ROTATE_SPAN || (dx && !mx)) {  /* far enough (or at an edge): take the button up, go back, down again */
        button(WM_MBUTTONUP);
        move(-g_rot_x, 0, NULL, NULL);
        g_rot_x = 0;
        button(WM_MBUTTONDOWN);
    }
}

/* ---- the buttons ---- */
static int pressed(SDL_GameControllerButton b, int *changed) {
    int now = SDL_GameControllerGetButton(g_pad, b) != 0;
    *changed = now != g_held[b];
    g_held[b] = (uint8_t)now;
    return now;
}
static void buttons(void) {
    int ch, d;
    d = pressed(SDL_CONTROLLER_BUTTON_A, &ch);
    if (ch) button(d ? WM_LBUTTONDOWN : WM_LBUTTONUP);
    d = pressed(SDL_CONTROLLER_BUTTON_B, &ch);
    if (ch) button(d ? WM_RBUTTONDOWN : WM_RBUTTONUP);
    d = pressed(SDL_CONTROLLER_BUTTON_X, &ch);
    if (ch) key(d, 0x11, 0x1D);                         /* VK_CONTROL */
    d = pressed(SDL_CONTROLLER_BUTTON_START, &ch);
    if (ch) key(d, 0x1B, 0x01);                         /* VK_ESCAPE */
    d = pressed(SDL_CONTROLLER_BUTTON_DPAD_LEFT, &ch);
    if (ch) key(d, 0xBC, 0x33);                         /* VK_OEM_COMMA */
    d = pressed(SDL_CONTROLLER_BUTTON_DPAD_RIGHT, &ch);
    if (ch) key(d, 0xBE, 0x34);                         /* VK_OEM_PERIOD */
    d = pressed(SDL_CONTROLLER_BUTTON_DPAD_UP, &ch);
    if (ch && d) hle_input_wheel(120);
    d = pressed(SDL_CONTROLLER_BUTTON_DPAD_DOWN, &ch);
    if (ch && d) hle_input_wheel(-120);
    pressed(SDL_CONTROLLER_BUTTON_LEFTSTICK, &ch);
    pressed(SDL_CONTROLLER_BUTTON_LEFTSHOULDER, &ch);
    pressed(SDL_CONTROLLER_BUTTON_RIGHTSHOULDER, &ch);
}
/* Let go of everything held, as a pad goes. */
static void release_all(void) {
    if (g_held[SDL_CONTROLLER_BUTTON_A]) button(WM_LBUTTONUP);
    if (g_held[SDL_CONTROLLER_BUTTON_B]) button(WM_RBUTTONUP);
    if (g_held[SDL_CONTROLLER_BUTTON_X]) key(0, 0x11, 0x1D);
    if (g_held[SDL_CONTROLLER_BUTTON_START]) key(0, 0x1B, 0x01);
    if (g_held[SDL_CONTROLLER_BUTTON_DPAD_LEFT]) key(0, 0xBC, 0x33);
    if (g_held[SDL_CONTROLLER_BUTTON_DPAD_RIGHT]) key(0, 0xBE, 0x34);
    pan(0, 0);
    rotate(0, 0);
    memset(g_held, 0, sizeof g_held);
}

/* ---- the virtual controller (TPW_VPAD) ---- */
typedef struct { double t; char what[8]; double v; } vev_t;
static vev_t *g_vev;
static int g_nvev, g_ivev;
static void vpad_open(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "[pad] TPW_VPAD: cannot read %s\n", path); return; }
    char line[128];
    while (fgets(line, sizeof line, f)) {
        vev_t e;
        if (sscanf(line, "%lf %7s %lf", &e.t, e.what, &e.v) != 3) continue;
        g_vev = (vev_t *)realloc(g_vev, sizeof *g_vev * (size_t)(g_nvev + 1));
        g_vev[g_nvev++] = e;
    }
    fclose(f);
    int i = SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER, SDL_CONTROLLER_AXIS_MAX,
                                      SDL_CONTROLLER_BUTTON_MAX, 0);
    g_vjoy = i >= 0 ? SDL_JoystickOpen(i) : NULL;
    g_vindex = g_vjoy ? i : -1;
    fprintf(stderr, "[pad] TPW_VPAD: a virtual controller, %d events from %s%s\n", g_nvev, path, g_vjoy ? "" : " (SDL refused it)");
}
static void vpad_step(double now_s) {
    static const char *const bn[SDL_CONTROLLER_BUTTON_MAX] = { "a", "b", "x", "y", "back", "guide", "start", "ls", "rs",
        "lb", "rb", "up", "down", "left", "right" };
    static const char *const an[SDL_CONTROLLER_AXIS_MAX] = { "lx", "ly", "rx", "ry", "lt", "rt" };
    for (; g_vjoy && g_ivev < g_nvev && g_vev[g_ivev].t <= now_s; g_ivev++) {
        const vev_t *e = &g_vev[g_ivev];
        for (int b = 0; b < SDL_CONTROLLER_BUTTON_MAX && bn[b]; b++)
            if (!strcmp(e->what, bn[b])) SDL_JoystickSetVirtualButton(g_vjoy, b, e->v != 0);
        for (int a = 0; a < SDL_CONTROLLER_AXIS_MAX; a++)
            if (!strcmp(e->what, an[a])) {
                double v = e->v < -1 ? -1 : e->v > 1 ? 1 : e->v;
                SDL_JoystickSetVirtualAxis(g_vjoy, a, (Sint16)(a >= 4 ? v * 32767 : v * 32767));
            }
        fprintf(stderr, "[pad] %.1fs virtual %s %g\n", now_s, e->what, e->v);
    }
    SDL_JoystickUpdate();
}

/* ---- each frame ---- */
static void find_pad(uint32_t now) {
    if (g_pad && !SDL_GameControllerGetAttached(g_pad)) {
        fprintf(stderr, "[pad] %s gone\n", SDL_GameControllerName(g_pad));
        release_all();
        SDL_GameControllerClose(g_pad);
        g_pad = NULL;
    }
    if (g_pad || now - g_scan_ms < 1000) return;
    g_scan_ms = now;
    for (int i = 0; i < SDL_NumJoysticks(); i++) {
        if (g_vindex >= 0 && i != g_vindex) continue;
        if (!SDL_IsGameController(i) || !(g_pad = SDL_GameControllerOpen(i))) continue;
        fprintf(stderr, "[pad] %s: the cursor on the left stick, A to click, B to cancel\n",
                SDL_GameControllerName(g_pad) ? SDL_GameControllerName(g_pad) : "a controller");
        memset(g_held, 0, sizeof g_held);
        break;
    }
}
static void poll(void) {
    uint32_t now = SDL_GetTicks();
    double dt = g_last_ms ? (now - g_last_ms) / 1000.0 : 0;
    if (dt > 0.1) dt = 0.1;                             /* a long load: no leap */
    g_last_ms = now;
    if (g_vjoy) vpad_step((now - g_start_ms) / 1000.0);
    find_pad(now);
    if (!g_pad) return;
    mach_enter();
    buttons();
    double rx, ry;
    stick(SDL_CONTROLLER_AXIS_RIGHTX, SDL_CONTROLLER_AXIS_RIGHTY, &rx, &ry);
    int lb = g_held[SDL_CONTROLLER_BUTTON_LEFTSHOULDER], rb = g_held[SDL_CONTROLLER_BUTTON_RIGHTSHOULDER];
    if (!g_rotating) pan(rx, ry);
    if (!g_panning) rotate(lb && !rb ? -1 : rb && !lb ? 1 : 0, dt);
    if (!g_panning && !g_rotating) {                    /* the cursor, when the camera is not using it */
        double lx, ly;
        stick(SDL_CONTROLLER_AXIS_LEFTX, SDL_CONTROLLER_AXIS_LEFTY, &lx, &ly);
        int w, h;
        screen_size(&w, &h);
        double speed = CURSOR_SPEED * h * (g_held[SDL_CONTROLLER_BUTTON_LEFTSTICK] ? 0.25 : 1);
        g_fx += lx * speed * dt, g_fy += ly * speed * dt;
        int dx = (int)g_fx, dy = (int)g_fy;
        g_fx -= dx, g_fy -= dy;
        move(dx, dy, NULL, NULL);
    }
    double zoom = axis(SDL_CONTROLLER_AXIS_TRIGGERRIGHT) - axis(SDL_CONTROLLER_AXIS_TRIGGERLEFT);
    if (fabs(zoom) > 0.15) {
        g_zoom_f += zoom * ZOOM_RATE * dt;
        int n = (int)g_zoom_f;
        g_zoom_f -= n;
        if (n) hle_input_wheel(n * 120);
    } else {
        g_zoom_f = 0;
    }
    mach_leave();
}

void pad_start(void) {
    if (SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER) != 0) {
        fprintf(stderr, "[pad] no game controllers: %s\n", SDL_GetError());
        return;
    }
    g_start_ms = SDL_GetTicks();
    const char *v = getenv("TPW_VPAD");
    if (v && *v) vpad_open(v);
    hle_screen_poll_hook = poll;
}
