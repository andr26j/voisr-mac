"""Runs one child process and makes sure it dies with Voisr.

    python whisper_guard.py --owner PID --state BASE -- COMMAND [ARG...]

whisper-server is a C++ program that cannot watch its parent, so a Voisr that
is force quit or crashes would leave it holding hundreds of MB. Voisr starts it
through this guard instead. The guard keeps the read end of a pipe whose write
end only Voisr holds: when Voisr exits for any reason, SIGKILL included, the
kernel closes that end and the guard sees end-of-file. It also notices a
changed parent pid and the child exiting by itself. Then it stops the child
(SIGTERM, then SIGKILL) and removes its state file. The state file is
BASE.<guard pid>: one per guard, so a newer guard never overwrites an older
one's record of its server.

Standard library only and no imports from voisr, so it runs with `python -I`.
"""
import json
import os
import select
import signal
import subprocess
import sys
import time

POLL_S = 0.2
TERM_GRACE_S = 3.0


def _start_time(pid: int) -> str:
    """`ps` start time of a process, to tell it from a later one that reuses
    the pid. Empty when it can't be read."""
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _write_state(path, owner, child, argv) -> None:
    state = {"owner": owner, "guard": os.getpid(),
             "guard_start": _start_time(os.getpid()),
             "server": child.pid, "server_start": _start_time(child.pid),
             "command": argv[0]}
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def _stop(child) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    deadline = time.monotonic() + TERM_GRACE_S
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if child.poll() is None:
        child.kill()
        child.wait()


def main(argv) -> int:
    try:
        sep = argv.index("--")
        opts, command = argv[:sep], argv[sep + 1:]
        owner = int(opts[opts.index("--owner") + 1])
        state = f'{opts[opts.index("--state") + 1]}.{os.getpid()}'
    except (ValueError, IndexError):
        print("usage: whisper_guard.py --owner PID --state FILE -- COMMAND...",
              file=sys.stderr)
        return 2
    if not command:
        return 2
    try:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
    except OSError as error:
        print(f"whisper_guard: cannot start {command[0]}: {error}",
              file=sys.stderr)
        return 127

    def on_term(_sig, _frame):
        _stop(child)
        _forget(state)
        os._exit(0)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)
    try:
        _write_state(state, owner, child, command)
    except OSError:
        pass  # the guard still protects the child; only stale cleanup is lost
    code = 0
    try:
        while True:
            code = child.poll()
            if code is not None:
                break
            if os.getppid() != owner:
                break  # the owner is gone and we were reparented
            ready, _, _ = select.select([sys.stdin.buffer], [], [], POLL_S)
            if ready and not sys.stdin.buffer.read1(1):
                break  # the write end closed: the owner exited
    finally:
        _stop(child)
        _forget(state)
    return code if isinstance(code, int) and code >= 0 else 0


def _forget(state) -> None:
    """Remove the state file, but only if it is still ours."""
    try:
        with open(state, encoding="utf-8") as f:
            if json.load(f).get("guard") != os.getpid():
                return
        os.unlink(state)
    except (OSError, ValueError):
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
