/*
 * settings.ini in the game folder: the options that were environment
 * variables only (HLE_MAX_FPS, TPW_STRETCH_HUD, TPW_LIGHTNING_FLASH), in a
 * file a player can read and change, and a launcher too (RecompLauncher,
 * recomplauncher.json). A variable set in the environment still wins. The
 * first start writes the file with every option at its default, commented;
 * install.py carries it over a rebuilt game folder (KEEP).
 */
#include <ctype.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char DEFAULTS[] =
    "; Theme Park World's settings, read at each start. A variable of the same\n"
    "; meaning set in the environment wins over the line here.\n"
    "[game]\n"
    "\n"
    "; Frames a second at most: 60, as the game was made for, or 0 for no cap,\n"
    "; drawn as fast as the computer can (HLE_MAX_FPS).\n"
    "max_fps = 60\n"
    "\n"
    "; On a widescreen, true stretches the HUD to its width, as the game itself\n"
    "; would; false keeps its 4:3 shape (TPW_STRETCH_HUD).\n"
    "stretch_hud = false\n"
    "\n"
    "; true: Halloween's lightning flashes the whole screen to its negative, as\n"
    "; it did on a CRT; false leaves the flash out (TPW_LIGHTNING_FLASH).\n"
    "lightning_flash = false\n";

static char *trim(char *s) {
    while (isspace((unsigned char)*s)) s++;
    char *end = s + strlen(s);
    while (end > s && isspace((unsigned char)end[-1])) end--;
    *end = 0;
    return s;
}

static int is_true(const char *v) {
    return !strcmp(v, "true") || !strcmp(v, "1") || !strcmp(v, "yes") || !strcmp(v, "on");
}

static int is_number(const char *v) {
    if (!*v) return 0;
    for (; *v; v++)
        if (!isdigit((unsigned char)*v)) return 0;
    return 1;
}

/* The third argument of setenv, 0: what the environment already says stays. */
static void apply(const char *key, const char *value) {
    if (!strcmp(key, "max_fps")) {
        if (is_number(value)) setenv("HLE_MAX_FPS", value, 0);
        return;
    }
    if (!strcmp(key, "stretch_hud")) {
        if (is_true(value)) setenv("TPW_STRETCH_HUD", "1", 0);
        return;
    }
    if (!strcmp(key, "lightning_flash")) {
        if (is_true(value)) setenv("TPW_LIGHTNING_FLASH", "1", 0);
        return;
    }
}

void tpw_settings(const char *game_dir) {
    char path[PATH_MAX];
    snprintf(path, sizeof path, "%s/settings.ini", game_dir);
    FILE *f = fopen(path, "r");
    if (!f) {
        FILE *out = fopen(path, "w");
        if (out) fputs(DEFAULTS, out), fclose(out);
        return;
    }
    char line[512];
    while (fgets(line, sizeof line, f)) {
        char *s = trim(line);
        if (!*s || *s == ';' || *s == '#' || *s == '[') continue;
        char *eq = strchr(s, '=');
        if (!eq) continue;
        *eq = 0;
        apply(trim(s), trim(eq + 1));
    }
    fclose(f);
}
