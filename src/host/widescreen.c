/*
 * The HUD at widescreen.
 *
 * The game lays its GUI out in a virtual 2048x1536 screen and maps each axis
 * to the real one on its own, so wider than 4:3 everything is stretched
 * sideways by (W/H)/(4/3). Here each piece is squeezed back by
 * k = (4/3)(H/W) towards the edge it belongs to: left, centre or right. At
 * 4:3, k is 1 and nothing changes. run_lift.py patches the calls in.
 *
 * - Widgets (tpw_widget): every GUI widget's rectangle goes through one
 *   constructor. A widget the size of the screen is a layer and is left
 *   alone, but for a page squeezed whole (Options); a widget on a layer
 *   starts a group, anchored by where its centre is; every widget under it
 *   uses its group's anchor, so the group moves and shrinks as one. Drawing, text placement and the mouse's hit tests all
 *   read these rectangles, so they follow.
 * - Layout extras (tpw_layout_x): the layout tables also give a widget a
 *   text rectangle, hit shapes (rectangles, circles, polygons) and list
 *   columns, read by the layout builder; their x goes as the widget's did.
 * - Offsets (tpw_rel_w): some HUD text sits at an offset from its element,
 *   in virtual units times the screen width; those use the 4:3 width.
 * - Free models (tpw_hud_models): the advisor and a ride's preview are 3D
 *   models drawn straight in screen space, not fitted to a widget, and come
 *   out per axis too. They are squeezed at the projection (sub_00579690),
 *   which they reach with w = 1.
 * - Hit circles (tpw_hit_circle, tpw_circle_dx): tested as the ellipse a
 *   squeezed round button is drawn as.
 * - Pictures and movies (tpw_splash, tpw_movie_w, tpw_movie_x): 4:3, at the
 *   4:3 width, centred, with black at the sides.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "win32hle.h"
#include "recomp_target.h"

#define SCREEN_W 0x7A1968u
#define SCREEN_H 0x7A1930u
#define GUI_H    0xFAA5C0u   /* the GUI's idea of the screen height (int16) */

static float squeeze_for(uint32_t w, uint32_t h) {
    float k = w > 0 ? 4.0f * (float)h / (3.0f * (float)w) : 1.0f;
    return k < 0.999f && !getenv("TPW_STRETCH_HUD") ? k : 1.0f;
}

/* The GUI's squeeze: from the run's own size when it has one (main.c), which
 * every screen-size switch is held to, so a widget made before the first
 * switch is squeezed as much as the rest; else the game's size, always 4:3. */
static float squeeze(void) {
    extern void tpw_run_size(uint32_t *, uint32_t *);
    uint32_t w = 0, h = 0;
    tpw_run_size(&w, &h);
    return w ? squeeze_for(w, h) : squeeze_for(MEM32(SCREEN_W), MEM32(SCREEN_H));
}

/* Each widget's anchor: -2 a layer, -1 left, 0 centre, 1 right. Keyed by the
 * widget's address; a new widget at a freed one's address overwrites it.
 * Nothing tells us a widget is gone, so the table would fill: hours of play
 * open dialogs at thousands of addresses, and once full a new dialog's
 * widgets found no group, each placed on its own, so a dialog came out
 * differently from one open to the next. A key lives within WPROBE slots of
 * its hash; when those are all taken the one least recently made or asked
 * for gives way (a long-lived parent is asked for by every child it gets). */
#define WBITS  14
#define WTAB   (1u << WBITS)
#define WPROBE 32
static uint32_t g_wkey[WTAB], g_wused[WTAB], g_wclock;
static int8_t g_wanchor[WTAB];

static int8_t *anchor_of(uint32_t w, int add) {
    uint32_t i = ((w >> 3) * 2654435761u) >> (32 - WBITS), old = i;
    int full = 1;
    for (int n = 0; n < WPROBE; n++, i = (i + 1) & (WTAB - 1)) {
        if (g_wkey[i] == w) { g_wused[i] = ++g_wclock; return &g_wanchor[i]; }
        if (!g_wkey[i]) { full = 0; break; }
        if (g_wused[i] < g_wused[old]) old = i;
    }
    if (!add) return NULL;
    if (full) i = old;
    g_wkey[i] = w, g_wused[i] = ++g_wclock;
    return &g_wanchor[i];
}

static int16_t place(int x, int anchor, float k) {
    float a = (float)(anchor + 1) * 1024.0f;
    return (int16_t)lroundf(a + ((float)x - a) * k);
}

/* A page laid out across the whole screen, by its id, is squeezed whole into
 * a 4:3 panel in the middle, as it looks at 4:3: the Options page, whose
 * columns would otherwise go to the screen's edges. */
static int whole_page(uint32_t id) { return id == 120000; }

/* sub_0065BCAD, the widget constructor, after its push ebp: [sp+8] parent,
 * [sp+0x10] id, [sp+0x14..0x20] left, top, right, bottom. */
void tpw_widget(uint32_t self, uint32_t sp) {
    int l = (int16_t)MEM32(sp + 0x14), r = (int16_t)MEM32(sp + 0x1C);
    uint32_t parent = MEM32(sp + 8);
    int8_t *pa = parent ? anchor_of(parent, 0) : NULL;
    int anchor;
    if (r - l >= 1900) anchor = whole_page(MEM32(sp + 0x10)) ? 0 : -2;
    else if (pa && *pa != -2) anchor = *pa;
    else {
        int c = (l + r) / 2;
        anchor = abs(c - 1024) <= 128 ? 0 : c < 1024 ? -1 : 1;
    }
    int8_t *me = anchor_of(self, 1);
    if (me) *me = (int8_t)anchor;
    float k = squeeze();
    if (anchor == -2 || k == 1.0f) return;
    MEM32(sp + 0x14) = (uint32_t)(int32_t)place(l, anchor, k);
    MEM32(sp + 0x1C) = (uint32_t)(int32_t)place(r, anchor, k);
}

/* An x (int16 at `at`) the layout builder just read for `widget`. */
void tpw_layout_x(uint32_t widget, uint32_t at) {
    int8_t *a = anchor_of(widget, 0);
    float k = squeeze();
    if (!a || *a == -2 || k == 1.0f) return;
    int16_t x;
    memcpy(&x, hle_ptr(at), 2);
    x = place(x, *a, k);
    memcpy(hle_ptr(at), &x, 2);
}

/* The width an offset in the GUI's virtual space scales by. */
uint32_t tpw_rel_w(uint32_t w) {
    int16_t h;
    memcpy(&h, hle_ptr(GUI_H), 2);
    int32_t r = (h * 4 + 1) / 3;
    return squeeze() == 1.0f || r >= (int32_t)w ? w : (uint32_t)r;
}

/* Free models, at the projection: (arr, stride, n) vertices already in
 * screen space when w = 1. Widget-fitted models and text sit flat on the
 * GUI's depth layers (z = 1 - d/40) and are skipped. The rest are the
 * advisor, drawn near the eye (z about 0.2) in the bottom-right corner, and
 * the previews in dialogs (a ride, a shop, a crate; z about 0.55), which the
 * game projects about the middle of their (squeezed) box.
 *
 * Draws whose x ranges meet are one model, so a ride's spinning ornaments
 * go with its base; each model is squeezed about one point, where the
 * previous frame drew it (or, new, where its first part is), so its parts
 * keep together and it stays in its box. The advisor goes towards the edge
 * of the outer third it is in. (Grouped by depth instead, a preview's parts
 * split into models squeezed about different points, and one in an outer
 * third was pulled towards the screen's edge, out of its box.) A draw of
 * the world (w != 1) starts a new frame. */
#define MODELS 64
#define ADVISOR_Z 0.35f      /* nearer than this: the advisor */
#define MODEL_GAP 0.15f      /* x ranges this close (of the screen's 2) meet */
typedef struct { float lo, hi, o; int advisor; } model_t;
static model_t g_m[MODELS], g_prev[MODELS];
static int g_nm, g_nprev, g_hud_drawn;

static void models_trace(void) {
    static int tr = -1, fr;
    if (tr < 0) tr = getenv("TPW_MODEL_TRACE") != NULL;
    if (!tr || ++fr % 30) return;
    fprintf(stderr, "[models]");
    for (int m = 0; m < g_nm; m++) fprintf(stderr, " %s x%.2f..%.2f about %.2f", g_m[m].advisor ? "advisor" : "preview", g_m[m].lo, g_m[m].hi, g_m[m].o);
    fprintf(stderr, "\n");
}

void tpw_hud_models(uint32_t arr, uint32_t stride, uint32_t n) {
    if (!n || stride < 16 || stride > 256) return;
    float lo = 1e9f, hi = -1e9f, zl = 1e9f, zh = -1e9f;
    for (uint32_t i = 0; i < n; i++) {
        float v[4];
        memcpy(v, hle_ptr(arr + i * stride), 16);
        if (v[3] != 1.0f) {
            if (g_hud_drawn) {
                models_trace();
                memcpy(g_prev, g_m, sizeof g_m), g_nprev = g_nm;
                g_hud_drawn = 0;
            }
            g_nm = 0;
            return;
        }
        if (v[0] < lo) lo = v[0];
        if (v[0] > hi) hi = v[0];
        if (v[2] < zl) zl = v[2];
        if (v[2] > zh) zh = v[2];
    }
    g_hud_drawn = 1;
    float layer = (1.0f - zl) * 40.0f;
    if (zh - zl < 1e-5f && fabsf(layer - roundf(layer)) < 4e-4f) return;
    int advisor = zh < ADVISOR_Z;
    model_t *m = NULL;
    for (int i = 0; i < g_nm && !m; i++)
        if (g_m[i].advisor == advisor && lo <= g_m[i].hi + MODEL_GAP && hi >= g_m[i].lo - MODEL_GAP) m = &g_m[i];
    if (m) {
        if (lo < m->lo) m->lo = lo;
        if (hi > m->hi) m->hi = hi;
    } else {
        if (g_nm >= MODELS) return;
        m = &g_m[g_nm++];
        m->lo = lo, m->hi = hi, m->advisor = advisor;
        m->o = (lo + hi) * 0.5f;
        float best = 1e9f;                                   /* the same model last frame: the nearest that met */
        for (int i = 0; i < g_nprev; i++) {
            const model_t *p = &g_prev[i];
            float c = (p->lo + p->hi) * 0.5f, d = fabsf(c - m->o);
            if (p->advisor == advisor && lo <= p->hi + MODEL_GAP && hi >= p->lo - MODEL_GAP && d < best) best = d, m->o = c;
        }
        if (advisor && m->o < -1.0f / 3) m->o = -1.0f;
        else if (advisor && m->o > 1.0f / 3) m->o = 1.0f;
    }
    float k = squeeze();
    if (k == 1.0f) return;
    for (uint32_t i = 0; i < n; i++) {
        float x;
        memcpy(&x, hle_ptr(arr + i * stride), 4);
        x = m->o + (x - m->o) * k;
        memcpy(hle_ptr(arr + i * stride), &x, 4);
    }
}

/* Pictures and movies drawn to the whole screen are 4:3: they get the 4:3
 * width, centred, black at the sides. */
static uint32_t width_43(void) {
    uint32_t w = MEM32(SCREEN_W), h = MEM32(SCREEN_H);
    return squeeze_for(w, h) == 1.0f ? w : (h * 4 + 1) / 3;
}

/* sub_00587130, with the back buffer locked: the splash and loading pictures.
 * The game stretches the picture (pic: pixels, width, height, bytes a pixel;
 * rows bottom up) across the screen; here it fills the 4:3 width, centred.
 * desc is the locked surface's description. Returns 0 to let the game draw. */
int tpw_splash(uint32_t pic, uint32_t desc) {
    uint32_t bpp = MEM32(pic + 0xC);
    int h = (int)MEM32(desc + 8), w = (int)MEM32(desc + 0xC), pitch = (int)MEM32(desc + 0x10);
    int sw = (int)MEM32(pic + 4), sh = (int)MEM32(pic + 8);
    if (squeeze_for(w, h) == 1.0f || (bpp != 2 && bpp != 4) || sw <= 0 || sh <= 0 || h <= 0) return 0;
    int pw = (int)((int64_t)h * sw / sh);
    if (pw >= w) return 0;
    int x0 = (w - pw) / 2;
    uint8_t *src = hle_ptr(MEM32(pic)), *dst = hle_ptr(MEM32(desc + 0x24));
    for (int y = 0; y < h; y++) {
        uint8_t *row = dst + (size_t)y * pitch;
        const uint8_t *s = src + (size_t)((int64_t)(h - y - 1) * sh / h) * sw * bpp;
        memset(row, 0, (size_t)x0 * bpp);
        for (int x = 0; x < pw; x++)
            memcpy(row + (size_t)(x0 + x) * bpp, s + (size_t)((int64_t)x * sw / pw) * bpp, bpp);
        memset(row + (size_t)(x0 + pw) * bpp, 0, (size_t)(w - x0 - pw) * bpp);
    }
    return 1;
}

/* sub_0051B010 sets a movie's size from the screen's: the 4:3 width. */
uint32_t tpw_movie_w(void) { return width_43(); }

/* ...and, when it is given an x, the bars' width added to it. */
uint32_t tpw_movie_x(uint32_t x) { return x + (MEM32(SCREEN_W) - width_43()) / 2; }

/* Hit circles: one radius, in virtual units, which are narrower than tall
 * once a widget is squeezed. The layout builder makes one for a widget
 * (tpw_hit_circle), and its test (sub_0066C0C4) widens the x distance back
 * (tpw_circle_dx), so it tests the ellipse the squeezed button is drawn as. */
void tpw_hit_circle(uint32_t circle, uint32_t widget) {
    int8_t *a = anchor_of(widget, 0), *me;
    if (circle && (me = anchor_of(circle, 1))) *me = a ? *a : -2;
}

int32_t tpw_circle_dx(uint32_t circle, int32_t dx) {
    int8_t *a = anchor_of(circle, 0);
    float k = squeeze();
    return !a || *a == -2 || k == 1.0f ? dx : (int32_t)lroundf((float)dx / k);
}

/* Picking (sub_0045BF90): how much wider than a 4:3 view this one is. */
double tpw_pick_scale(void) {
    uint32_t w = MEM32(SCREEN_W), h = MEM32(SCREEN_H);
    return w && h ? 0.75 * (double)w / (double)h : 1.0;
}
