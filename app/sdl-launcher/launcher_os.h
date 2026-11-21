/*
 * launcher_os.h - provides OS-specific functions for launcher.c to use in a
 * cross-platform manner. Covers starting the installer and the game, and a
 * few file operations that differ between platforms.
 *
 * launcher_os_posix.c handles Linux, launcher_os_win32.c handles Windows.
 *
 * Paths are UTF-8 on every platform.
 */
#ifndef LAUNCHER_OS_H
#define LAUNCHER_OS_H

#include <stddef.h>
#include <stdio.h>

/* When TPW_SHARE, TPW_PYTHON, and TPW_HOME aren't set, these are the defaults
 * for where things are kept.
 * Each is written to its buffer of n bytes. */
void os_default_paths(char *share, char *python, char *home, size_t n);

/* Point the build at the tools bundled in the package, by setting the
 * environment variables for install.py (like CC and TPW_SDL_PREFIX) in the
 * launcher's environment, which is then inherited by the installer.
 * A variable that is already set is left alone.
 * Returns 0 on success, -1 on failure.
 * On failure, writes the reason into why, up to n bytes.
 * On Linux this does nothing and returns 0: the AppImage's AppRun and the
 * Flatpak's manifest handle setting the build up instead. */
int os_bundled_tools(char *why, size_t n);

/* The game's executable in the build directory, like build/host/tpw. */
extern const char *const OS_TPW_EXE;

/* Path separator for paths built by the launcher, as a string literal to join
 * with others (like "%s" OS_SEP "logs").
 *
 * We still use backslashes for Windows because the Win32 API returns paths that
 * are backslash-separated, and if we join those with our own paths for display
 * to the user, we don't want to mix both separators in the same path.
 * For instance, if we just used / everywhere, on failure the user might see:
 *
 *    The logs are in C:\Users\you\AppData\Local\Theme Park World/logs
 *
 * Hardly the end of the world, but it looks amateurish.
 *
 * Note: This is here to keep launcher.c unaware of which platform it's on. The
 * launcher_os_*.c files just use their own separators directly, mostly because
 * "a/b/c" is easier to read than "a" OS_SEP "b" OS_SEP "c". */
#ifdef _WIN32
#define OS_SEP "\\"
#else
#define OS_SEP "/"
#endif

/* Return 1 if path exists, else 0. */
int os_exists(const char *path);
/* Create the directory path, and any of its parents that are missing. */
void os_mkdirs(const char *path);
/* Rename from to to, replacing to if it exists.
 * Returns 0 on success, -1 on failure. */
int os_rename(const char *from, const char *to);
/* Open a file at a UTF-8 path. */
FILE *os_fopen(const char *path, const char *mode);

/* ---- processes ---- */
typedef struct os_proc os_proc;

/* Where a started process's output goes. */
enum os_output {
    OS_OUT_PIPE,          /* stdout and stderr to a pipe, for os_read */
    OS_OUT_PIPE_QUIET,    /* stdout to a pipe, stderr discarded */
    OS_OUT_LOG,           /* stdout and stderr to the file at log_path.
                             On Linux, when the launcher's stderr is a
                             terminal, the process writes to the launcher's
                             own stdout and stderr instead. */
};

/* Start argv[0] with the arguments argv[1..]. argv is NULL-terminated.
 * env, if not NULL, is a NULL-terminated list like "NAME=value", set for
 * the new process only.
 * When cancellable is set, os_cancel can stop the process and everything it
 * starts in turn, such as the compilers run by the build process.
 * Returns NULL if the process couldn't be started. */
os_proc *os_spawn(const char *const *argv, enum os_output out, const char *log_path,
                  const char *const *env, int cancellable);

/* Read the process's piped output into buf, up to n bytes.
 * When block is 0 and nothing is waiting, returns -1 immediately.
 * Returns the number of bytes read, or 0 once the process has ended. */
long os_read(os_proc *p, char *buf, size_t n, int block);

/* Stop the process and everything it started. */
void os_cancel(os_proc *p);

/* Process exit status. */
typedef struct {
    int exited;           /* 1: it exited with code; 0: it was killed or
                             crashed, and code is the signal (Linux) or the
                             exception (Windows) */
    long code;
} os_status;

/* Wait for the process to end, then free p. */
os_status os_wait(os_proc *p);

/* Human-readable description of an os_status.
 * Examples:
 * - "exit 1"
 * - "signal 9"
 * - "exception 0xC0000005" */
void os_describe(os_status st, char *out, size_t n);

#endif
