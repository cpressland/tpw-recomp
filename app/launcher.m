/*
 * Theme Park World.app's own program: it finds the game this Mac built
 * (tools/install/install.py, in Application Support) and runs it.
 *
 * The game is libtpw.dylib, the host compiled with the lift; it is entered at
 * tpw_main, as the tpw program is (src/host/exe.c). It runs in a child: this
 * program again, started with TPW_PLAY set, which loads the library before
 * anything here has touched AppKit, so SDL makes the app's one window. The
 * parent waits, and if the game crashed, says so and offers its log.
 *
 *   ~/Library/Application Support/Theme Park World/   the game (TPW_HOME: elsewhere)
 *   ~/Library/Logs/Theme Park World/tpw.log            its last run's output
 */
#import <AppKit/AppKit.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

#define TITLE "Theme Park World"
#define CRASHED 3                  /* the host's exit after a fault (main.c, on_fault) */

static char g_home[PATH_MAX], g_game[PATH_MAX], g_lib[PATH_MAX], g_logs[PATH_MAX], g_log[PATH_MAX];

static int exists(const char *path) { struct stat st; return stat(path, &st) == 0; }

static void paths(void) {
    const char *home = getenv("HOME") ? getenv("HOME") : "";
    const char *own = getenv("TPW_HOME");
    if (own && *own) snprintf(g_home, sizeof g_home, "%s", own);
    else snprintf(g_home, sizeof g_home, "%s/Library/Application Support/" TITLE, home);
    snprintf(g_game, sizeof g_game, "%s/game", g_home);
    snprintf(g_lib, sizeof g_lib, "%s/build/host/libtpw.dylib", g_home);
    snprintf(g_logs, sizeof g_logs, "%s/Library/Logs/" TITLE, home);
    snprintf(g_log, sizeof g_log, "%s/tpw.log", g_logs);
}

/* ---- the game, in the child ------------------------------------------------ */

static int play(int argc, char **argv) {
    void *lib = dlopen(g_lib, RTLD_NOW | RTLD_LOCAL);
    if (!lib) { fprintf(stderr, "cannot load %s: %s\n", g_lib, dlerror()); return 2; }
    int (*tpw_main)(int, char **) = (int (*)(int, char **))dlsym(lib, "tpw_main");
    if (!tpw_main) { fprintf(stderr, "%s has no tpw_main\n", g_lib); return 2; }
    /* The game folder and --run, then what the app was given, less Finder's
     * process serial number. The first time, the desktop's own size: after
     * that the host remembers whatever was chosen (data\Resolution.sam). */
    char **args = calloc((size_t)argc + 8, sizeof *args), first[PATH_MAX];
    int n = 0;
    args[n++] = argv[0];
    args[n++] = "--game";
    args[n++] = g_game;
    args[n++] = "--run";
    snprintf(first, sizeof first, "%s/.launched", g_home);
    if (!exists(first)) {
        args[n++] = "--resolution";
        args[n++] = "native";
        close(open(first, O_WRONLY | O_CREAT, 0644));
    }
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "-psn_", 5)) args[n++] = argv[i];
    args[n] = NULL;
    return tpw_main(n, args);
}

/* ---- the app: start the game, then wait for it ------------------------------ */

static void alert(NSString *title, NSString *text, BOOL offer_log) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        [NSApp activateIgnoringOtherApps:YES];
        NSAlert *a = [NSAlert new];
        a.messageText = title;
        a.informativeText = text;
        [a addButtonWithTitle:@"OK"];
        if (offer_log) [a addButtonWithTitle:@"Show Log"];
        if ([a runModal] == NSAlertSecondButtonReturn)
            [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:@(g_log)]];
    }
}

/* The last run's log kept as tpw.1.log; this run's output to tpw.log. Not
 * when started from a terminal, where the output belongs. */
static void log_actions(posix_spawn_file_actions_t *fa) {
    posix_spawn_file_actions_init(fa);
    if (isatty(STDERR_FILENO)) return;
    char old[PATH_MAX];
    mkdir(g_logs, 0755);
    snprintf(old, sizeof old, "%s/tpw.1.log", g_logs);
    rename(g_log, old);
    posix_spawn_file_actions_addopen(fa, STDOUT_FILENO, g_log, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    posix_spawn_file_actions_adddup2(fa, STDOUT_FILENO, STDERR_FILENO);
}

int main(int argc, char **argv) {
    paths();
    if (getenv("TPW_PLAY")) return play(argc, argv);

    if (!exists(g_lib) || !exists(g_game)) {
        alert(@"Theme Park World isn't set up yet",
              [NSString stringWithFormat:@"The game is built on this Mac from your own copy of the disc. "
                                         @"Until the app can do that itself, run tools/install/install.py "
                                         @"from the tpw-recomp repository; it puts the game in\n%s.", g_home], NO);
        return 1;
    }
    char self[PATH_MAX];
    uint32_t size = sizeof self;
    if (_NSGetExecutablePath(self, &size) != 0) return 1;
    setenv("TPW_PLAY", "1", 1);
    posix_spawn_file_actions_t fa;
    log_actions(&fa);
    pid_t pid;
    int err = posix_spawn(&pid, self, &fa, NULL, argv, environ);
    posix_spawn_file_actions_destroy(&fa);
    unsetenv("TPW_PLAY");
    if (err) {
        alert(@"Theme Park World couldn't start", @(strerror(err)), NO);
        return 1;
    }
    int status;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    if (WIFSIGNALED(status) || (WIFEXITED(status) && WEXITSTATUS(status) == CRASHED)) {
        alert(@"Theme Park World quit unexpectedly",
              @"The game's log says where it stopped: it helps to attach it to a bug report.", !isatty(STDERR_FILENO));
        return 1;
    }
    if (WIFEXITED(status) && WEXITSTATUS(status) == 2) {
        alert(@"Theme Park World couldn't start",
              @"The game this Mac built could not be loaded. Building it again may help.", !isatty(STDERR_FILENO));
        return 1;
    }
    return 0;
}
