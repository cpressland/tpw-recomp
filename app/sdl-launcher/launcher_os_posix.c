/* launcher_os_posix.c - launcher_os.h implemented for Linux. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "launcher_os.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

const char *const OS_TPW_EXE = "build/host/tpw";

/* Describes the layout of the Flatpak:
 * - The sources
 * - Python in the app
 * - The game in $XDG_DATA_HOME */
void os_default_paths(char *share, char *python, char *home, size_t n) {
    snprintf(share, n, "/app/share/theme-park-world");
    snprintf(python, n, "/app/venv/bin/python3");
    const char *xdg = getenv("XDG_DATA_HOME"), *h = getenv("HOME");
    if (xdg && *xdg) snprintf(home, n, "%s/theme-park-world", xdg);
    else snprintf(home, n, "%s/.local/share/theme-park-world", h && *h ? h : ".");
}

int os_bundled_tools(char *why, size_t n) { (void)why, (void)n; return 0; }

int os_exists(const char *path) { struct stat st; return stat(path, &st) == 0; }

void os_mkdirs(const char *path) {
    char p[1024];
    snprintf(p, sizeof p, "%s", path);
    for (char *s = p + 1; *s; s++)
        if (*s == '/') { *s = 0; mkdir(p, 0755); *s = '/'; }
    mkdir(p, 0755);
}

int os_rename(const char *from, const char *to) { return rename(from, to); }

FILE *os_fopen(const char *path, const char *mode) { return fopen(path, mode); }

struct os_proc {
    pid_t pid;
    int fd;               /* the read end of the output pipe, or -1 */
};

os_proc *os_spawn(const char *const *argv, enum os_output out, const char *log_path,
                  const char *const *env, int cancellable) {
    int fds[2] = { -1, -1 };
    if (out != OS_OUT_LOG && pipe(fds) != 0) return NULL;
    pid_t pid = fork();
    if (pid == 0) {
        if (out == OS_OUT_LOG) {
            if (!isatty(2)) {
                int fd = open(log_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
                if (fd >= 0) dup2(fd, 1), dup2(fd, 2);
            }
        } else {
            dup2(fds[1], 1);
            if (out == OS_OUT_PIPE) {
                dup2(fds[1], 2);
            } else {
                int null = open("/dev/null", O_WRONLY);
                if (null >= 0) dup2(null, 2);
            }
            close(fds[0]);
        }
        /* It gets its own process group, so that os_cancel can also stop its
         * children. */
        if (cancellable) setpgid(0, 0);
        for (int i = 0; env && env[i]; i++) putenv((char *)env[i]);
        execv(argv[0], (char **)argv);
        fprintf(stderr, "cannot run %s: %s\n", argv[0], strerror(errno));
        _exit(127);
    }
    if (fds[1] >= 0) close(fds[1]);
    /* Group ID set again here, so an os_cancel immediately after this still
     * clears the group, even if the child hasn't set its own GID yet. */
    if (pid > 0 && cancellable) setpgid(pid, pid);
    if (pid < 0) {
        if (fds[0] >= 0) close(fds[0]);
        return NULL;
    }
    if (fds[0] >= 0) fcntl(fds[0], F_SETFL, O_NONBLOCK);
    os_proc *p = malloc(sizeof *p);
    if (!p) { if (fds[0] >= 0) close(fds[0]); return NULL; }
    p->pid = pid, p->fd = fds[0];
    return p;
}

long os_read(os_proc *p, char *buf, size_t n, int block) {
    if (p->fd < 0) return 0;
    for (;;) {
        ssize_t r = read(p->fd, buf, n);
        if (r >= 0) return (long)r;
        if (errno == EINTR) continue;
        if (errno != EAGAIN) return 0;
        if (!block) return -1;
        struct pollfd pf = { p->fd, POLLIN, 0 };
        poll(&pf, 1, -1);
    }
}

void os_cancel(os_proc *p) { kill(-p->pid, SIGTERM); }

os_status os_wait(os_proc *p) {
    if (p->fd >= 0) close(p->fd);
    int status = 0;
    while (waitpid(p->pid, &status, 0) < 0 && errno == EINTR) {}
    free(p);
    if (WIFEXITED(status)) return (os_status){ 1, WEXITSTATUS(status) };
    return (os_status){ 0, WIFSIGNALED(status) ? WTERMSIG(status) : 0 };
}

void os_describe(os_status st, char *out, size_t n) {
    snprintf(out, n, "%s %ld", st.exited ? "exit" : "signal", st.code);
}
