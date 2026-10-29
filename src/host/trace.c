/*
 * A portable function-entry trace for bring-up (built with -DTPW_TRACE=ON:
 * every lifted function calls recomp_trace_enter). pcrecomp's recomp_trace.c
 * is the Windows host's; this is the part of it a native host needs.
 *
 *   TPW_FIRSTHIT=FILE   each function the first time it runs, in order, with
 *                       the import last called: the newest lines are the
 *                       newest code reached.
 *   TPW_RING_AT=VA      the last 1024 entries, the first time VA is entered.
 *   TPW_ARGTRACE=VA     one line per entry to VA: ecx and the first four stack
 *                       arguments, each as a string too when it points at one.
 *   recomp_dump_trace   the last 1024 entries, oldest first (exit and faults).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "win32hle.h"

uint32_t g_enter_trace[RECOMP_ENTER_SIZE];
uint32_t g_enter_idx;

static FILE *g_firsthit;
static uint8_t *g_seen;             /* a bit per code byte, 0x00400000.. */
#define SEEN_BASE 0x00400000u
#define SEEN_SPAN 0x00400000u
static uint64_t g_entries;
static uint32_t g_ring_at, g_argtrace;

static void arg_line(uint32_t va) {
    fprintf(stderr, "[arg] %08X ecx=%08X", va, g_ecx);
    for (int i = 0; i < 4; i++) {
        uint32_t v = MEM32(g_esp + 4 + 4 * i);
        fprintf(stderr, " a%d=%08X", i, v);
        if (v >= 0x10000 && v < 0xC0000000u) {
            const uint8_t *p = (const uint8_t *)hle_ptr(v);
            int n = 0;
            while (n < 48 && p[n] >= 0x20 && p[n] < 0x7F) n++;
            if (n >= 3 && (p[n] == 0 || n == 48)) fprintf(stderr, "\"%.*s\"", n, (const char *)p);
        }
    }
    fprintf(stderr, "\n");
}

void tpw_trace_start(void) {
    const char *at = getenv("TPW_RING_AT");
    if (at) g_ring_at = (uint32_t)strtoul(at, NULL, 16);
    const char *arg = getenv("TPW_ARGTRACE");
    if (arg) g_argtrace = (uint32_t)strtoul(arg, NULL, 16);
    const char *path = getenv("TPW_FIRSTHIT");
    if (!path) return;
    g_firsthit = fopen(path, "w");
    g_seen = calloc(SEEN_SPAN / 8, 1);
}

void recomp_trace_enter(uint32_t va) {
    g_enter_trace[g_enter_idx++ & (RECOMP_ENTER_SIZE - 1)] = va;
    g_entries++;
    if (va == g_argtrace) arg_line(va);
    if (va == g_ring_at) {
        char why[32];
        snprintf(why, sizeof why, "entering %08X", va);
        g_ring_at = 0;
        recomp_dump_trace(why);
    }
    if (g_firsthit && va - SEEN_BASE < SEEN_SPAN) {
        uint32_t k = va - SEEN_BASE;
        if (!(g_seen[k >> 3] & (1u << (k & 7)))) {
            g_seen[k >> 3] |= (uint8_t)(1u << (k & 7));
            fprintf(g_firsthit, "%llu %08X %s\n", (unsigned long long)g_entries, va,
                    g_cur_import ? g_cur_import : "");
        }
    }
}

void recomp_dump_trace(const char *why) {
    if (g_firsthit) fflush(g_firsthit);
    fprintf(stderr, "--- last lifted functions entered (%s), oldest first ---\n", why);
    uint32_t n = g_enter_idx < RECOMP_ENTER_SIZE ? g_enter_idx : RECOMP_ENTER_SIZE;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t va = g_enter_trace[(g_enter_idx - n + i) & (RECOMP_ENTER_SIZE - 1)];
        fprintf(stderr, "%08X%s", va, (i + 1) % 8 ? " " : "\n");
    }
    fprintf(stderr, "\n");
}
