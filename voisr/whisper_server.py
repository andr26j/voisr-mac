"""A kept-loaded whisper.cpp server, so a dictation doesn't reload the model.

Voisr starts one `whisper-server` for the configured model when a recording
begins, sends each clip to it over a loopback-only connection, and unloads it
after an idle timeout. This follows Handy's behaviour: load on key press, in
the background, in parallel with capture; an idle watcher that never fires
during a recording or transcription; "Immediately" unloads after each
dictation (here: never starts a server, so today's per-dictation whisper-cli
path stays exactly as it was).

Rules this module keeps:
  * The server listens on 127.0.0.1 only, on a free port. Requests use
    http.client directly, which never consults system proxy settings.
  * Every wait is bounded. Any failure raises ServerError and the caller falls
    back to whisper-cli for that dictation. After a failure the server is not
    retried for FAILURE_COOLDOWN_S, so one broken server costs one slow
    dictation, not all of them.
  * The server dies with Voisr: it runs under whisper_guard.py (see there).
    start_cleanup() also removes a server left by an earlier run, but only
    one whose pid and start time match what a guard of ours recorded.
"""
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from voisr.paths import find_binary

# Handy's ModelUnloadTimeout choices; "Immediately" keeps per-dictation cli.
DEFAULT_UNLOAD = "min5"
UNLOAD_SECONDS = {
    "never": None, "immediately": 0,
    "min2": 120, "min5": 300, "min10": 600, "min15": 900, "hour1": 3600,
}
UNLOAD_LABELS = {
    "never": "Never", "immediately": "Immediately", "min2": "After 2 minutes",
    "min5": "After 5 minutes", "min10": "After 10 minutes",
    "min15": "After 15 minutes", "hour1": "After 1 hour",
}

READY_TIMEOUT_S = 30.0       # model load, bounded; a cold small.en takes ~1.3 s
WAIT_FOR_READY_S = 15.0      # most a release waits for a still-loading server
FAILURE_COOLDOWN_S = 60.0
IDLE_POLL_S = 5.0
GUARD = Path(__file__).with_name("whisper_guard.py")


class ServerError(RuntimeError):
    """The warm path failed; the caller falls back to whisper-cli."""


def unload_seconds(setting) -> int | None:
    """Idle seconds before unloading; None = never. Unknown values read as the
    default, so a hand-edited config can't disable the unload."""
    if setting not in UNLOAD_SECONDS:
        setting = DEFAULT_UNLOAD
    return UNLOAD_SECONDS[setting]


def uses_server(setting) -> bool:
    return setting != "immediately"


def _python_for_guard() -> str | None:
    """A real interpreter for the guard. In the app, sys.executable is the
    Voisr launcher, which would start another Voisr instead."""
    names = ("python3", "python")
    candidates = []
    if os.path.basename(sys.executable).startswith("python"):
        candidates.append(sys.executable)
    for prefix in (sys.base_exec_prefix, sys.base_prefix, sys.exec_prefix):
        candidates += [os.path.join(prefix, "bin", n) for n in names]
    candidates += [shutil.which(n) for n in names]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _multipart(fields: dict, wav_bytes: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
                 + wav_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _http_probe(port: int, timeout: float) -> bool:
    """True once the server answers HTTP. whisper-server loads the model
    before it listens, so an answer means the model is loaded."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", "/")
            conn.getresponse().read()
            return True
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False


def _http_infer(port: int, wav_bytes: bytes, fields: dict, timeout: float) -> str:
    body, ctype = _multipart(fields, wav_bytes)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", "/inference", body=body,
                     headers={"Content-Type": ctype})
        resp = conn.getresponse()
        raw = resp.read()
    except (OSError, http.client.HTTPException) as error:
        raise ServerError(f"server request failed: {error}") from error
    finally:
        conn.close()
    try:
        data = json.loads(raw)
        if resp.status != 200 or "error" in data:
            raise ServerError(f"server error: {data.get('error', resp.status)}")
        return data["text"].strip()
    except (ValueError, KeyError, AttributeError, TypeError) as error:
        raise ServerError(f"bad server response: {raw[:80]!r}") from error


def _ps_start(pid: int) -> str:
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _ps_command(pid: int) -> str:
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale(state_path) -> list[int]:
    """Kill a whisper-server (and guard) a dead earlier Voisr left behind.

    Only what the state file names, and only when the process still has the
    start time recorded and the expected command line, and the Voisr that
    owned it is gone. A whisper-server the user started is never listed in
    the file, so it is never touched. Returns the pids stopped."""
    path = Path(state_path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        owner, guard, server = state["owner"], state["guard"], state["server"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if _alive(owner) and owner != os.getpid():
        return []  # another running Voisr owns it
    stopped = []
    for pid, start_key, needle in (
            (guard, "guard_start", "whisper_guard"),
            (server, "server_start", "whisper-server")):
        if not _alive(pid) or not state.get(start_key):
            continue
        if _ps_start(pid) != state[start_key] or needle not in _ps_command(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except OSError:
            pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(_alive(p) for p in stopped):
        time.sleep(0.05)
    for pid in stopped:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        path.unlink()
    except OSError:
        pass
    return stopped


class _Process:
    """A running guard + server pair."""

    def __init__(self, popen, port, model, state_path):
        self.popen, self.port, self.model = popen, port, model
        self.state_path = state_path

    def alive(self) -> bool:
        return self.popen.poll() is None

    def stop(self, grace: float = 5.0) -> None:
        """Close the guard's pipe (it stops the server and exits), then make
        sure: SIGTERM the guard, then SIGKILL it and the server by pid."""
        server_pid = None
        try:
            server_pid = json.loads(
                Path(self.state_path).read_text(encoding="utf-8")).get("server")
        except (OSError, ValueError, AttributeError):
            pass
        try:
            if self.popen.stdin:
                self.popen.stdin.close()
        except OSError:
            pass
        try:
            self.popen.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.popen.kill()
            self.popen.wait()
        if isinstance(server_pid, int) and _alive(server_pid) \
                and "whisper-server" in _ps_command(server_pid):
            try:
                os.kill(server_pid, signal.SIGKILL)
            except OSError:
                pass


def _spawn_guarded(command, state_path):
    py = _python_for_guard()
    if py is None:
        raise ServerError("no python interpreter for the server guard")
    return subprocess.Popen(
        [py, "-I", str(GUARD), "--owner", str(os.getpid()),
         "--state", str(state_path), "--", *command],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True)


class WhisperServer:
    """One kept-loaded whisper-server for one model at a time.

    prewarm() never blocks. transcribe() blocks for a bounded time and
    raises ServerError on any failure. set_busy() marks a recording or
    transcription in flight, which holds off the idle unload."""

    def __init__(self, state_path, *, find=find_binary, spawn=_spawn_guarded,
                 probe=_http_probe, infer=_http_infer, port_fn=free_port,
                 clock=time.monotonic, ready_timeout=READY_TIMEOUT_S,
                 wait_for_ready=WAIT_FOR_READY_S, poll=IDLE_POLL_S,
                 cooldown=FAILURE_COOLDOWN_S):
        self._state_path = Path(state_path)
        self._find, self._spawn = find, spawn
        self._probe, self._infer, self._port_fn = probe, infer, port_fn
        self._clock = clock
        self._ready_timeout = ready_timeout
        self._wait_for_ready = wait_for_ready
        self._poll = poll
        self._cooldown = cooldown
        self._cond = threading.Condition()
        self._proc = None            # _Process, once started
        self._ready = False          # the model answered
        self._wanted = None          # model path most recently asked for
        self._loading = set()        # model paths a loader thread is on
        self._inflight = 0           # requests being served
        self._failed_at = None
        self._unload = DEFAULT_UNLOAD
        self._busy = False
        self._last_activity = clock()
        self._closed = False
        self._watcher = None

    # -- public ---------------------------------------------------------

    def start_cleanup(self) -> list[int]:
        """Remove a server a dead earlier run left. Safe to call at startup."""
        return cleanup_stale(self._state_path)

    def configure(self, unload) -> None:
        """Apply the unload setting; 'immediately' unloads right now."""
        with self._cond:
            self._unload = unload if unload in UNLOAD_SECONDS else DEFAULT_UNLOAD
            self._cond.notify_all()
        if not uses_server(unload):
            self.unload()

    def set_busy(self, busy: bool) -> None:
        with self._cond:
            if self._busy and not busy:
                self._last_activity = self._clock()
            self._busy = bool(busy)

    def prewarm(self, model_path, unload=None) -> None:
        """Start loading `model_path` in the background. Returns at once."""
        if unload is not None:
            self.configure(unload)
        if not uses_server(self._unload):
            return
        with self._cond:
            if self._closed or self._in_cooldown():
                return
            self._failed_at = None
            self._last_activity = self._clock()
            self._wanted = str(model_path)
            if self._proc and self._proc.model == self._wanted \
                    and self._proc.alive():
                return  # loaded, or its loader thread is waiting for it
            if self._wanted in self._loading:
                return
            self._loading.add(self._wanted)
        threading.Thread(target=self._load, args=(str(model_path),),
                         name="whisper-server-load", daemon=True).start()

    def transcribe(self, wav_path, model_path, prompt=None, unload=None):
        """Text for `wav_path` from the warm server, loading it if needed.
        Returns (text, info) with info {"model_wait": ms}. Raises ServerError."""
        if unload is not None:
            self.configure(unload)
        model_path = str(model_path)
        if not uses_server(self._unload):
            raise ServerError("kept-loaded mode is off")
        if not Path(model_path).is_file():
            raise ServerError(f"model missing: {model_path}")
        started = self._clock()
        with self._cond:
            if self._closed:
                raise ServerError("server shut down")
            if self._in_cooldown():
                raise ServerError("server recently failed; using whisper-cli")
            self._last_activity = started
        self.prewarm(model_path)
        deadline = started + self._wait_for_ready
        with self._cond:
            while True:
                proc = self._proc
                if self._closed:
                    raise ServerError("server shut down")
                if proc is not None and proc.model == model_path and self._ready:
                    break
                if self._failed_at is not None:
                    raise ServerError("server failed to load")
                left = deadline - self._clock()
                if left <= 0:
                    self._fail_locked()
                    raise ServerError("server not ready in time")
                self._cond.wait(min(left, 0.25))
            port = proc.port
            self._inflight += 1
        waited = (self._clock() - started) * 1000
        try:
            wav = Path(wav_path).read_bytes()
            # The same decode settings whisper-cli runs with today.
            # token_timestamps must be off: this server build turns them on
            # unless told otherwise, which wraps every segment at 60
            # characters, mid-word ("Dash" / "board").
            fields = {"response_format": "json", "language": "en",
                      "beam_size": "5", "best_of": "5", "temperature": "0.0",
                      "token_timestamps": "false"}
            if prompt:
                fields["prompt"] = prompt
            seconds = len(wav) / 32000
            text = self._infer(port, wav, fields, 30 + seconds)
        except ServerError:
            self._fail()
            raise
        except OSError as error:
            self._fail()
            raise ServerError(f"cannot read clip: {error}") from error
        finally:
            with self._cond:
                self._inflight -= 1
                self._last_activity = self._clock()
        return text, {"model_wait": waited}

    def unload(self) -> None:
        """Stop the server and free its memory. Safe to call any time."""
        with self._cond:
            proc, self._proc = self._proc, None
            self._ready = False
            self._wanted = None
            self._cond.notify_all()
        if proc is not None:
            proc.stop()

    def forget_model(self, model_path) -> None:
        """A model file is being deleted or replaced: drop it if loaded."""
        with self._cond:
            current = self._proc.model if self._proc else self._wanted
        if current is not None and str(current) == str(model_path):
            self.unload()

    def is_loaded(self) -> bool:
        with self._cond:
            return bool(self._proc and self._ready and self._proc.alive())

    def shutdown(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self.unload()

    # -- internals ------------------------------------------------------

    def _in_cooldown(self) -> bool:
        return (self._failed_at is not None
                and self._clock() - self._failed_at < self._cooldown)

    def _fail(self) -> None:
        with self._cond:
            self._failed_at = self._clock()
            self._cond.notify_all()
        self.unload()

    def _fail_locked(self) -> None:
        self._failed_at = self._clock()
        self._cond.notify_all()
        threading.Thread(target=self.unload, daemon=True).start()

    def _load(self, model_path: str) -> None:
        try:
            self._load_one(model_path)
        finally:
            with self._cond:
                self._loading.discard(model_path)

    def _load_one(self, model_path: str) -> None:
        with self._cond:
            # another model, or a server that died: replace it
            old = self._proc
            if old is not None and (old.model != model_path or not old.alive()):
                self._proc, self._ready = None, False
            else:
                old = None
        if old is not None:
            old.stop()
        binary = self._find("whisper-server")
        if not binary:
            self._load_failed("whisper-server not found")
            return
        try:
            port = self._port_fn()
            command = [binary, "-m", model_path, "--host", "127.0.0.1",
                       "--port", str(port), "-l", "en", "--beam-size", "5"]
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            popen = self._spawn(command, self._state_path)
        except (OSError, ServerError) as error:
            self._load_failed(str(error))
            return
        proc = _Process(popen, port, model_path, self._state_path)
        with self._cond:
            superseded = self._closed or self._wanted != model_path
            if not superseded:
                self._proc, self._ready = proc, False
        if superseded:
            proc.stop()
            return
        deadline = self._clock() + self._ready_timeout
        while True:
            with self._cond:
                if self._proc is not proc:   # unloaded or replaced meanwhile
                    return
            if not proc.alive():
                self._load_failed("server exited while loading", proc)
                return
            if self._probe(port, 1.0):
                break
            if self._clock() >= deadline:
                self._load_failed("server load timed out", proc)
                return
            time.sleep(0.05)
        with self._cond:
            if self._proc is not proc:
                return
            self._ready = True
            self._failed_at = None
            self._last_activity = self._clock()
            self._cond.notify_all()
        self._ensure_watcher()

    def _load_failed(self, why: str, proc=None) -> None:
        print(f"Voisr: warm whisper-server unavailable ({why}); using whisper-cli",
              flush=True)
        with self._cond:
            self._failed_at = self._clock()
            if proc is None or self._proc is proc:
                self._proc, self._ready = None, False
                self._wanted = None
            self._cond.notify_all()
        if proc is not None:
            proc.stop()

    def _ensure_watcher(self) -> None:
        with self._cond:
            if self._watcher is not None and self._watcher.is_alive():
                return
            self._watcher = threading.Thread(
                target=self._watch, name="whisper-server-idle", daemon=True)
            self._watcher.start()

    def _watch(self) -> None:
        """Handy's idle watcher: unload once idle longer than the limit, and
        never while a recording or transcription is in flight."""
        while True:
            with self._cond:
                if self._closed or self._proc is None:
                    self._watcher = None
                    return
                self._cond.wait(self._poll)
                if self._closed or self._proc is None:
                    self._watcher = None
                    return
                limit = unload_seconds(self._unload)
                if self._busy or self._inflight:
                    self._last_activity = self._clock()
                    continue
                if not self._proc.alive():
                    due = True
                else:
                    due = (limit is not None
                           and self._clock() - self._last_activity > limit)
            if due:
                self.unload()
