/* Voisr launcher stub.
 *
 * Loads Python as a library (dlopen + Py_BytesMain) instead of exec-ing the
 * interpreter. exec would replace the process image with the shared Python
 * binary, so macOS TCC would attribute all permissions (Microphone, Input
 * Monitoring, Accessibility) to "Python" — and Voisr would never appear in
 * Privacy & Security. With this stub the process identity stays Voisr.app.
 */
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>

/* Both are overridable at compile time — the installer passes -DPYFW/-DPYVER
 * matching whatever python3 built the venv (see install_app.sh). */
#ifndef PYFW
#define PYFW "/Library/Frameworks/Python.framework/Versions/3.14"
#endif
#ifndef PYVER
#define PYVER "3.14"
#endif

int main(int argc, char **argv) {
    const char *home = getenv("HOME");
    if (!home) home = "";

    char appsup[PATH_MAX], sitepkgs[PATH_MAX], logfile[PATH_MAX];
    char pythonpath[2 * PATH_MAX], dylib[PATH_MAX], path[2 * PATH_MAX];

    snprintf(appsup, sizeof appsup, "%s/Library/Application Support/Voisr", home);
    snprintf(sitepkgs, sizeof sitepkgs,
             "%s/venv/lib/python" PYVER "/site-packages", appsup);
    snprintf(logfile, sizeof logfile, "%s/Library/Logs/Voisr.log", home);
    snprintf(pythonpath, sizeof pythonpath, "%s:%s", appsup, sitepkgs);
    snprintf(dylib, sizeof dylib, "%s/Python", PYFW);
    snprintf(path, sizeof path, "/opt/homebrew/bin:/usr/local/bin:%s",
             getenv("PATH") ? getenv("PATH") : "/usr/bin:/bin");

    freopen(logfile, "a", stdout);
    freopen(logfile, "a", stderr);
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);

    setenv("PYTHONPATH", pythonpath, 1);
    setenv("PYTHONHOME", PYFW, 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    setenv("PATH", path, 1);

    void *lib = dlopen(dylib, RTLD_NOW | RTLD_GLOBAL);
    if (!lib) {
        fprintf(stderr, "Voisr stub: dlopen failed: %s\n", dlerror());
        return 1;
    }
    int (*py_main)(int, char **) = (int (*)(int, char **))dlsym(lib, "Py_BytesMain");
    if (!py_main) {
        fprintf(stderr, "Voisr stub: Py_BytesMain not found\n");
        return 1;
    }

    char *py_argv[] = {argv[0], "-m", "voisr.ui", NULL};
    return py_main(3, py_argv);
}
