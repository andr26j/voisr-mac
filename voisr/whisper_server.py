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

READY_TIMEOUT_S = 30.0       # model load, bounded; a cold small.en takes ~1.3 s
WAIT_FOR_READY_S = 15.0      # most a release waits for a still-loading server
FAILURE_COOLDOWN_S = 60.0
IDLE_POLL_S = 5.0
GUARD = Path(__file__).with_name("whisper_guard.py")


class ServerError(RuntimeError):
    """The warm path failed; the caller falls back to whisper-cli."""


def valid_unload(setting) -> bool:
    """A known unload choice. Anything else, including an unhashable value
    from a hand-edited config, is not."""
    return isinstance(setting, str) and setting in UNLOAD_SECONDS


def unload_seconds(setting) -> int | None:
    """Idle seconds before unloading; None = never. Unknown values read as the
    default, so a hand-edited config can't disable the unload."""
    if not valid_unload(setting):
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


def state_files(state_path) -> list[Path]:
    """Every per-guard state file next to `state_path` (BASE.<guard pid>)."""
    base = Path(state_path)
    try:
        return sorted(p for p in base.parent.glob(base.name + ".*")
                      if p.name[len(base.name) + 1:].isdigit())
    except OSError:
        return []


def _cleanup_one(path: Path) -> list[int]:
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


def cleanup_stale(state_path) -> list[int]:
    """Kill any whisper-server (and guard) a dead earlier Voisr left behind.

    Each guard wrote its own state file, so two guards that overlapped are
    both found. Only what a file names is touched, and only when the process
    still has the recorded start time and the expected command line, and the
    Voisr that owned it is gone. A whisper-server the user started is in no
    file, so it is never touched. Returns the pids stopped."""
    stopped = []
    for path in state_files(state_path):
        stopped += _cleanup_one(path)
    return stopped


class _Process:
    """A running guard + server pair."""

    def __init__(self, popen, port, model, state_path):
        self.popen, self.port, self.model = popen, port, model
        self.state_path = state_path
        self.inflight = 0       # requests being served (WhisperServer's lock)
        self.retired = False    # unloaded while a request was in flight

    def alive(self) -> bool:
        return self.popen.poll() is None

    def _own_server_pid(self):
        """The server pid from this guard's own state file (BASE.<guard pid>),
        which no other guard writes."""
        guard = getattr(self.popen, "pid", None)
        if guard is None:
            return None
        try:
            state = json.loads(
                Path(f"{self.state_path}.{guard}").read_text(encoding="utf-8"))
            if state.get("guard") == guard:
                return state.get("server")
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def stop(self, grace: float = 5.0) -> None:
        """Close the guard's pipe (it stops the server and exits), then make
        sure: kill the guard, and our own server by pid if it is still up."""
        server_pid = self._own_server_pid()
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
    transcription in flight, which holds off the idle unload.

    An unload that is on purpose (idle, a new model, a new setting) is not a
    failure: it starts no cooldown, and a request already being served is
    left to finish before its server is stopped. Each unload bumps `_gen`, so
    a loader thread that started before it can tell it is stale and stops
    its own process; a later request always gets a loader of the current
    generation."""

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
        self._gen = 0                # bumped by every unload
        self._loading = {}           # model path -> generation of its loader
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
        """Apply the unload setting; 'immediately' unloads right now (after
        any request in flight)."""
        with self._cond:
            self._unload = unload if valid_unload(unload) else DEFAULT_UNLOAD
            self._cond.notify_all()
            off = not uses_server(self._unload)
        if off:
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
        with self._cond:
            if not uses_server(self._unload) or self._closed \
                    or self._in_cooldown():
                return
            self._failed_at = None
            self._last_activity = self._clock()
            self._ensure_loading_locked(str(model_path))

    def transcribe(self, wav_path, model_path, prompt=None):
        """Text for `wav_path` from the warm server, loading it if needed.
        Returns (text, info) with info {"model_wait": ms}. Raises ServerError.
        The unload setting is not taken from the caller: a queued dictation
        carries the setting from when it began, and must not overwrite a
        newer one (the Api calls configure() when Settings changes)."""
        model_path = str(model_path)
        if not uses_server(self._unload):
            raise ServerError("kept-loaded mode is off")
        if not Path(model_path).is_file():
            raise ServerError(f"model missing: {model_path}")
        started = self._clock()
        deadline = started + self._wait_for_ready
        with self._cond:
            if self._closed:
                raise ServerError("server shut down")
            if self._in_cooldown():
                raise ServerError("server recently failed; using whisper-cli")
            self._failed_at = None
            self._last_activity = started
            while True:
                proc = self._proc
                if self._closed:
                    raise ServerError("server shut down")
                if (proc is not None and proc.model == model_path
                        and self._ready and proc.alive()):
                    break
                if self._failed_at is not None:
                    raise ServerError("server failed to load")
                # an unload since we began leaves no loader: make sure one runs
                self._ensure_loading_locked(model_path)
                left = deadline - self._clock()
                if left <= 0:
                    self._fail_locked()
                    raise ServerError("server not ready in time")
                self._cond.wait(min(left, 0.25))
            proc.inflight += 1
            port = proc.port
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
            # Bounded tighter than whisper-cli's two minutes: a hung server
            # holds the queue for this long before the cli fallback runs.
            text = self._infer(port, wav, fields, 30 + 2 * len(wav) / 32000)
        except ServerError as error:
            self._request_failed(proc, error)
            raise
        except OSError as error:
            raise ServerError(f"cannot read clip: {error}") from error
        finally:
            self._request_done(proc)
        return text, {"model_wait": waited}

    def unload(self) -> None:
        """Free the server's memory. A request in flight finishes first; the
        server is then stopped by whoever finishes it. Safe to call any time."""
        with self._cond:
            proc = self._detach_locked()
        self._stop_or_retire(proc)

    def forget_model(self, model_path) -> None:
        """A model file is being deleted or replaced: drop it if loaded."""
        model_path = str(model_path)
        with self._cond:
            hit = ((self._proc is not None and self._proc.model == model_path)
                   or model_path in self._loading)
        if hit:
            self.unload()

    def shutdown(self) -> None:
        """Quit: stop the server now, in-flight request or not."""
        with self._cond:
            self._closed = True
            proc = self._detach_locked()
            self._cond.notify_all()
        if proc is not None:
            proc.stop()

    # -- internals ------------------------------------------------------

    def _in_cooldown(self) -> bool:
        return (self._failed_at is not None
                and self._clock() - self._failed_at < self._cooldown)

    def _detach_locked(self):
        """Forget the current server and invalidate its loader. Returns the
        process to stop, or None. Never starts a cooldown."""
        proc, self._proc = self._proc, None
        self._ready = False
        self._gen += 1
        self._cond.notify_all()
        return proc

    def _stop_or_retire(self, proc) -> None:
        if proc is None:
            return
        with self._cond:
            if proc.inflight:
                proc.retired = True   # the last request out stops it
                return
        proc.stop()

    def _request_done(self, proc) -> None:
        with self._cond:
            proc.inflight -= 1
            self._last_activity = self._clock()
            stop = proc.retired and proc.inflight == 0
        if stop:
            proc.stop()

    def _request_failed(self, proc, error) -> None:
        """A request to `proc` failed. If it is still the current server that
        is a real failure (cooldown, stop it). If it was unloaded meanwhile,
        the failure is ours: the caller falls back without a cooldown."""
        with self._cond:
            if self._proc is not proc:
                return
            self._failed_at = self._clock()
            detached = self._detach_locked()
        if detached is not None:
            detached.stop()

    def _fail_locked(self) -> None:
        self._failed_at = self._clock()
        detached = self._detach_locked()
        if detached is not None:
            threading.Thread(target=detached.stop, daemon=True).start()

    def _ensure_loading_locked(self, model: str) -> None:
        """Make sure `model` is loaded or has a live loader. Lock held."""
        proc = self._proc
        if proc is not None and proc.model == model and proc.alive():
            return
        if self._loading.get(model) == self._gen:
            return   # a loader of the current generation is on it
        if any(m != model and g == self._gen for m, g in self._loading.items()):
            self._gen += 1   # a loader for another model: make it stale
        if proc is not None:   # another model, or a server that died
            old = self._detach_locked()
            threading.Thread(target=self._stop_or_retire, args=(old,),
                             daemon=True).start()
        self._loading[model] = self._gen
        threading.Thread(target=self._load, args=(model, self._gen),
                         name="whisper-server-load", daemon=True).start()

    def _load(self, model_path: str, gen: int) -> None:
        try:
            self._load_one(model_path, gen)
        finally:
            with self._cond:
                if self._loading.get(model_path) == gen:
                    del self._loading[model_path]
                self._cond.notify_all()

    def _current(self, gen) -> bool:
        return not self._closed and self._gen == gen

    def _load_one(self, model_path: str, gen: int) -> None:
        binary = self._find("whisper-server")
        if not binary:
            self._load_failed("whisper-server not found", gen)
            return
        try:
            port = self._port_fn()
            command = [binary, "-m", model_path, "--host", "127.0.0.1",
                       "--port", str(port), "-l", "en", "--beam-size", "5"]
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            popen = self._spawn(command, self._state_path)
        except (OSError, ServerError) as error:
            self._load_failed(str(error), gen)
            return
        proc = _Process(popen, port, model_path, self._state_path)
        with self._cond:
            stale = not self._current(gen)
            if not stale:
                self._proc, self._ready = proc, False
        if stale:
            proc.stop()
            return
        deadline = self._clock() + self._ready_timeout
        while True:
            with self._cond:
                if self._proc is not proc:   # unloaded or replaced meanwhile
                    return
            if not proc.alive():
                self._load_failed("server exited while loading", gen, proc)
                return
            if self._probe(port, 1.0):
                break
            if self._clock() >= deadline:
                self._load_failed("server load timed out", gen, proc)
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

    def _load_failed(self, why: str, gen: int, proc=None) -> None:
        """A load failed. Only the current generation's failure counts; a
        loader that was unloaded underneath has nothing to report."""
        with self._cond:
            current = self._current(gen)
            if current:
                print(f"Voisr: warm whisper-server unavailable ({why}); "
                      "using whisper-cli", flush=True)
                self._failed_at = self._clock()
                if proc is None or self._proc is proc:
                    self._detach_locked()
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
        never while a recording or transcription is in flight. The decision
        and the detach happen under one lock hold, so a request or activity
        that arrives meanwhile is never cut off."""
        while True:
            with self._cond:
                if self._closed or self._proc is None:
                    self._watcher = None
                    return
                self._cond.wait(self._poll)
                if self._closed or self._proc is None:
                    self._watcher = None
                    return
                proc = self._proc
                if self._busy or proc.inflight:
                    self._last_activity = self._clock()
                    continue
                limit = unload_seconds(self._unload)
                due = (not proc.alive()) or (
                    limit is not None
                    and self._clock() - self._last_activity > limit)
                if not due:
                    continue
                self._detach_locked()
            proc.stop()
