/* launcher_os_win32.c - launcher_os.h for Windows (CreateProcessW, anonymous
 * pipes, job objects). */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#include "launcher_os.h"

const char *const OS_TPW_EXE = "build/host/tpw.exe";

/* ---- UTF-8 and UTF-16 ---- */

/* path as UTF-16, in a buffer the caller frees. NULL if it isn't valid UTF-8. */
static wchar_t *wide(const char *s) {
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, NULL, 0);
    if (n <= 0) return NULL;
    wchar_t *w = malloc((size_t)n * sizeof *w);
    if (w) MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, w, n);
    return w;
}

/* w as UTF-8, written to out (n bytes). */
static void narrow(const wchar_t *w, char *out, size_t n) {
    if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, (int)n, NULL, NULL) && n) out[0] = 0;
}

/* ---- paths and files ---- */

/* The launcher's own directory holds the bundle (T9): the sources under share,
 * and an embedded Python under python. The game and its build go in
 * %LOCALAPPDATA%\Theme Park World, as install.py's default. */
void os_default_paths(char *share, char *python, char *home, size_t n) {
    wchar_t exe[MAX_PATH];
    char dir[MAX_PATH * 3];
    DWORD len = GetModuleFileNameW(NULL, exe, MAX_PATH);
    if (len == 0 || len == MAX_PATH) wcscpy(exe, L".\\launcher.exe");
    wchar_t *slash = wcsrchr(exe, L'\\');
    if (slash) *slash = 0;
    narrow(exe, dir, sizeof dir);
    snprintf(share, n, "%s\\share", dir);
    snprintf(python, n, "%s\\python\\python.exe", dir);

    const wchar_t *local = _wgetenv(L"LOCALAPPDATA");
    char base[MAX_PATH * 3];
    if (local && *local) narrow(local, base, sizeof base);
    else snprintf(base, sizeof base, "%s", dir);
    snprintf(home, n, "%s\\Theme Park World", base);
}

int os_exists(const char *path) {
    wchar_t *w = wide(path);
    int yes = w && GetFileAttributesW(w) != INVALID_FILE_ATTRIBUTES;
    free(w);
    return yes;
}

void os_mkdirs(const char *path) {
    wchar_t *w = wide(path);
    if (!w) return;
    /* Each parent in turn, skipping a drive's root (like C:\), which already
     * exists and can't be created. */
    for (wchar_t *s = w + 1; *s; s++) {
        if ((*s == L'/' || *s == L'\\') && s[-1] != L':') {
            wchar_t c = *s;
            *s = 0;
            CreateDirectoryW(w, NULL);
            *s = c;
        }
    }
    CreateDirectoryW(w, NULL);
    free(w);
}

int os_rename(const char *from, const char *to) {
    wchar_t *f = wide(from), *t = wide(to);
    int ok = f && t && MoveFileExW(f, t, MOVEFILE_REPLACE_EXISTING);
    free(f), free(t);
    return ok ? 0 : -1;
}

FILE *os_fopen(const char *path, const char *mode) {
    wchar_t *w = wide(path), *m = wide(mode);
    FILE *f = w && m ? _wfopen(w, m) : NULL;
    free(w), free(m);
    return f;
}

/* ---- processes ---- */

struct os_proc {
    HANDLE process;
    HANDLE out;           /* the read end of the output pipe, or NULL */
    HANDLE job;           /* the job holding it and its children, or NULL */
};

/* Append arg to the command line cmd (of cap characters), quoted so that
 * CommandLineToArgvW and the C runtime split it back out unchanged: inside
 * quotes, backslashes are literal except when they come before a quote,
 * where each one must be doubled and the quote itself escaped. */
static int append_arg(wchar_t *cmd, size_t cap, size_t *len, const wchar_t *arg) {
#define PUT(c) do { if (*len + 1 >= cap) return -1; cmd[(*len)++] = (c); } while (0)
    if (*len) PUT(L' ');
    PUT(L'"');
    for (const wchar_t *s = arg;; s++) {
        size_t backslashes = 0;
        while (*s == L'\\') backslashes++, s++;
        if (!*s) {
            while (backslashes--) { PUT(L'\\'); PUT(L'\\'); }
            break;
        }
        if (*s == L'"') {
            while (backslashes--) { PUT(L'\\'); PUT(L'\\'); }
            PUT(L'\\');
        } else {
            while (backslashes--) PUT(L'\\');
        }
        PUT(*s);
    }
    PUT(L'"');
    cmd[*len] = 0;
    return 0;
#undef PUT
}

/* Set or restore environment variables around CreateProcessW: the child
 * gets a copy of the launcher's environment as it is at that moment. */
typedef struct { wchar_t *name, *old; int had; } env_save;

static int env_apply(const char *const *env, env_save *saved, int max) {
    int n = 0;
    for (int i = 0; env && env[i] && n < max; i++) {
        wchar_t *w = wide(env[i]);
        wchar_t *eq = w ? wcschr(w, L'=') : NULL;
        if (!eq) { free(w); continue; }
        *eq = 0;
        env_save *e = &saved[n++];
        e->name = w;
        DWORD need = GetEnvironmentVariableW(w, NULL, 0);
        e->had = need > 0;
        e->old = e->had ? malloc(need * sizeof(wchar_t)) : NULL;
        if (e->old) GetEnvironmentVariableW(w, e->old, need);
        SetEnvironmentVariableW(w, eq + 1);
    }
    return n;
}

static void env_restore(env_save *saved, int n) {
    for (int i = 0; i < n; i++) {
        SetEnvironmentVariableW(saved[i].name, saved[i].had ? saved[i].old : NULL);
        free(saved[i].name), free(saved[i].old);
    }
}

/* An inheritable handle to the NUL device. */
static HANDLE nul(DWORD access) {
    SECURITY_ATTRIBUTES sa = { sizeof sa, NULL, TRUE };
    return CreateFileW(L"NUL", access, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, 0, NULL);
}

os_proc *os_spawn(const char *const *argv, enum os_output out, const char *log_path,
                  const char *const *env, int cancellable) {
    wchar_t cmd[32768];
    size_t len = 0;
    cmd[0] = 0;
    for (int i = 0; argv[i]; i++) {
        wchar_t *w = wide(argv[i]);
        int bad = !w || append_arg(cmd, sizeof cmd / sizeof cmd[0], &len, w) != 0;
        free(w);
        if (bad) return NULL;
    }
    wchar_t *exe = wide(argv[0]);
    if (!exe) return NULL;

    SECURITY_ATTRIBUTES sa = { sizeof sa, NULL, TRUE };
    HANDLE rd = NULL, wr = NULL, err = NULL, in = nul(GENERIC_READ);
    if (out == OS_OUT_LOG) {
        wchar_t *lw = wide(log_path);
        wr = lw ? CreateFileW(lw, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_DELETE, &sa, CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, NULL) : INVALID_HANDLE_VALUE;
        free(lw);
        if (wr == INVALID_HANDLE_VALUE) wr = nul(GENERIC_WRITE);
        err = wr;
    } else {
        if (!CreatePipe(&rd, &wr, &sa, 0)) { free(exe); CloseHandle(in); return NULL; }
        SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);   /* the child gets only the write end */
        err = out == OS_OUT_PIPE ? wr : nul(GENERIC_WRITE);
    }

    /* Only these handles are inherited, not every inheritable handle the
     * launcher has open (like its own install.log). */
    HANDLE list[3] = { in, wr, err };
    DWORD nlist = err == wr ? 2 : 3;
    SIZE_T attr_size = 0;
    InitializeProcThreadAttributeList(NULL, 1, 0, &attr_size);
    LPPROC_THREAD_ATTRIBUTE_LIST attrs = malloc(attr_size);
    STARTUPINFOEXW si;
    memset(&si, 0, sizeof si);
    si.StartupInfo.cb = sizeof si;
    si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    si.StartupInfo.hStdInput = in, si.StartupInfo.hStdOutput = wr, si.StartupInfo.hStdError = err;
    int have_attrs = attrs && InitializeProcThreadAttributeList(attrs, 1, 0, &attr_size)
                     && UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, list,
                                                  nlist * sizeof(HANDLE), NULL, NULL);
    if (have_attrs) si.lpAttributeList = attrs;

    /* CREATE_NO_WINDOW: install.py, CMake, Ninja, clang and the game are
     * console programs, and the launcher has no console of its own, so
     * without it each one would open a console window. A cancellable process
     * starts suspended, so it's in its job before it can start anything. */
    DWORD flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT
                  | (have_attrs ? EXTENDED_STARTUPINFO_PRESENT : 0) | (cancellable ? CREATE_SUSPENDED : 0);
    env_save saved[8];
    int nsaved = env_apply(env, saved, 8);
    PROCESS_INFORMATION pi;
    BOOL ok = CreateProcessW(exe, cmd, NULL, NULL, TRUE, flags, NULL, NULL, &si.StartupInfo, &pi);
    env_restore(saved, nsaved);

    if (have_attrs) DeleteProcThreadAttributeList(attrs);
    free(attrs);
    free(exe);
    CloseHandle(in);
    if (err != wr) CloseHandle(err);
    CloseHandle(wr);                  /* the child has its own; ours would keep the pipe from ending */
    if (!ok) {
        if (rd) CloseHandle(rd);
        return NULL;
    }

    HANDLE job = NULL;
    if (cancellable) {
        /* KILL_ON_JOB_CLOSE: if the launcher itself ends, so does the build. */
        job = CreateJobObjectW(NULL, NULL);
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION lim;
        memset(&lim, 0, sizeof lim);
        lim.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (job && (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &lim, sizeof lim)
                    || !AssignProcessToJobObject(job, pi.hProcess))) {
            CloseHandle(job);
            job = NULL;
        }
        ResumeThread(pi.hThread);
    }
    CloseHandle(pi.hThread);

    os_proc *p = malloc(sizeof *p);
    if (!p) {
        TerminateProcess(pi.hProcess, 1);
        CloseHandle(pi.hProcess);
        if (rd) CloseHandle(rd);
        if (job) CloseHandle(job);
        return NULL;
    }
    p->process = pi.hProcess, p->out = rd, p->job = job;
    return p;
}

long os_read(os_proc *p, char *buf, size_t n, int block) {
    if (!p->out) return 0;
    DWORD want = n > 0x7FFFFFFF ? 0x7FFFFFFF : (DWORD)n, got = 0;
    if (!block) {
        DWORD avail = 0;
        if (!PeekNamedPipe(p->out, NULL, 0, NULL, &avail, NULL)) return 0;   /* broken: every writer is gone */
        if (!avail) return -1;
        if (avail < want) want = avail;
    }
    if (!ReadFile(p->out, buf, want, &got, NULL)) return 0;
    return (long)got;
}

void os_cancel(os_proc *p) {
    if (p->job) TerminateJobObject(p->job, 1);
    else TerminateProcess(p->process, 1);
}

os_status os_wait(os_proc *p) {
    if (p->out) CloseHandle(p->out);
    WaitForSingleObject(p->process, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(p->process, &code);
    CloseHandle(p->process);
    if (p->job) CloseHandle(p->job);
    free(p);
    /* A process that crashed without its own handler ends with the
     * exception's code, an NTSTATUS error or warning (like 0xC0000005 for an
     * access violation): those count as crashes, like a signal on Linux. */
    if ((code & 0xC0000000u) == 0xC0000000u || (code & 0xC0000000u) == 0x80000000u)
        return (os_status){ 0, (long)code };
    return (os_status){ 1, (long)code };
}

void os_describe(os_status st, char *out, size_t n) {
    if (st.exited) snprintf(out, n, "exit %ld", st.code);
    else snprintf(out, n, "exception 0x%08lX", (unsigned long)st.code);
}
