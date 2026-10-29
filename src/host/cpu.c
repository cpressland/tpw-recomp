/*
 * The CPU the game is told it runs on: an Intel Pentium II (family 6, model
 * 5) with MMX, without SSE or 3DNow!. The game needs MMX and lifted MMX runs;
 * it is told nothing newer, so it takes no path the lifter has not covered.
 * Every lifted cpuid comes here (run_lift.py CPUID_HOST).
 */
#include <string.h>

#include "win32hle.h"

#define FEAT_FPU   (1u << 0)
#define FEAT_TSC   (1u << 4)
#define FEAT_CX8   (1u << 8)
#define FEAT_CMOV  (1u << 15)
#define FEAT_MMX   (1u << 23)

void tpw_cpuid(void) {
    uint32_t leaf = g_eax;
    if (leaf == 0) {                             /* the highest leaf, and the vendor */
        g_eax = 1;
        memcpy(&g_ebx, "Genu", 4);
        memcpy(&g_edx, "ineI", 4);
        memcpy(&g_ecx, "ntel", 4);
    } else if (leaf == 1) {
        g_eax = 0x00000652u;                     /* family 6, model 5, stepping 2 */
        g_ebx = 0;
        g_ecx = 0;
        g_edx = FEAT_FPU | FEAT_TSC | FEAT_CX8 | FEAT_CMOV | FEAT_MMX;
    } else if (leaf == 0x80000000u) {            /* no extended leaves */
        g_eax = 0x80000000u;
        g_ebx = g_ecx = g_edx = 0;
    } else {
        g_eax = g_ebx = g_ecx = g_edx = 0;
    }
}
