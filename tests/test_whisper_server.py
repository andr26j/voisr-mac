"""The kept-loaded whisper-server manager, the routing around it, and the
parent-death guard. Fakes stand in for the binary and the HTTP calls; the
real binary is exercised by tests/test_whisper_server_real.py."""
import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from voisr import transcriber, whisper_server as ws
from voisr.history import HistoryStore
from voisr.ui import Api, glossary_prompt
from voisr.whisper_server import ServerError, WhisperServer

from tests.test_silence_pipeline import recorder_writing, tone


class FakePopen:
    def __init__(self, command):
        self.command = command
        self.stdin = self          # closing the pipe is how a guard is stopped
        self.code = None
        self.stopped = 0

    def close(self):
        self.stopped += 1
        self.code = 0

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


def loaded(server):
    with server._cond:
        return bool(server._proc and server._ready and server._proc.alive())


class Rig:
    """A WhisperServer wired to fakes, with a clock the test moves."""

    def __init__(self, tmp_path, binary="/bin/whisper-server", **kw):
        self.spawned = []
        self.requests = []
        self.now = 1000.0
        self.probe_ok = True
        self.infer_result = "hello world"
        self.model = tmp_path / "ggml-small.en.bin"
        self.model.write_bytes(b"m")
        self.wav = tmp_path / "clip.wav"
        self.wav.write_bytes(b"\0" * 32000)
        self.server = WhisperServer(
            tmp_path / "state.json", find=lambda name: binary,
            spawn=self._spawn, probe=lambda port, t: self.probe_ok,
            infer=self._infer, port_fn=lambda: 40000 + len(self.spawned),
            clock=kw.pop("clock", lambda: self.now), poll=0.01, **kw)

    def _spawn(self, command, state):
        p = FakePopen(command)
        self.spawned.append(p)
        return p

    def _infer(self, port, wav, fields, timeout):
        self.requests.append((port, wav, dict(fields), timeout))
        if isinstance(self.infer_result, Exception):
            raise self.infer_result
        return self.infer_result

    def ready(self, model=None, timeout=5):
        self.server.prewarm(model or self.model)
        end = time.monotonic() + timeout
        while not loaded(self.server) and time.monotonic() < end:
            time.sleep(0.005)
        assert loaded(self.server)


def test_prewarm_binds_loopback_only_and_loads_once(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.server.prewarm(rig.model)
    rig.server.prewarm(rig.model)
    time.sleep(0.05)
    assert len(rig.spawned) == 1
    cmd = rig.spawned[0].command
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert "0.0.0.0" not in cmd and "::" not in cmd
    assert cmd[cmd.index("-l") + 1] == "en"
    assert cmd[cmd.index("--beam-size") + 1] == "5"


def test_transcribe_sends_prompt_and_settings(tmp_path):
    rig = Rig(tmp_path)
    prompt = glossary_prompt({"voice are": "Voisr"})
    text, info = rig.server.transcribe(rig.wav, rig.model, prompt=prompt)
    assert text == "hello world"
    assert info["model_wait"] >= 0
    port, wav, fields, timeout = rig.requests[0]
    assert fields["prompt"] == "Glossary: Voisr."
    assert fields["language"] == "en" and fields["beam_size"] == "5"
    assert fields["token_timestamps"] == "false"   # else segments split mid-word
    assert wav == rig.wav.read_bytes()
    assert timeout == 120 + 2 * 1.0   # transcriber._timeout_for, same as cli


def test_no_prompt_field_without_a_dictionary(tmp_path):
    rig = Rig(tmp_path)
    rig.server.transcribe(rig.wav, rig.model)
    assert "prompt" not in rig.requests[0][2]


def test_immediately_never_starts_a_server(tmp_path):
    rig = Rig(tmp_path)
    rig.server.prewarm(rig.model, unload="immediately")
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model, unload="immediately")
    time.sleep(0.05)
    assert rig.spawned == []


def test_choosing_immediately_unloads_a_loaded_server(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.server.configure("immediately")
    assert not loaded(rig.server) and rig.spawned[0].stopped == 1


def test_missing_binary_raises_and_does_not_retry_during_cooldown(tmp_path):
    rig = Rig(tmp_path, binary=None)
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model)
    with pytest.raises(ServerError, match="recently failed"):
        rig.server.transcribe(rig.wav, rig.model)
    assert rig.spawned == []


def test_retries_after_the_cooldown(tmp_path):
    rig = Rig(tmp_path, cooldown=60)
    rig.infer_result = ServerError("boom")
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model)
    assert not loaded(rig.server)
    rig.now += 61
    rig.infer_result = "back"
    assert rig.server.transcribe(rig.wav, rig.model)[0] == "back"


def test_request_failure_unloads_the_server(tmp_path):
    rig = Rig(tmp_path)
    rig.infer_result = ServerError("bad response")
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model)
    assert rig.spawned[0].stopped == 1


def test_a_server_that_never_answers_is_bounded(tmp_path):
    rig = Rig(tmp_path, ready_timeout=0.2, wait_for_ready=0.3,
              clock=time.monotonic)
    rig.probe_ok = False
    start = time.monotonic()
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model)
    assert time.monotonic() - start < 3


def test_a_server_that_exits_while_loading_fails_fast(tmp_path):
    rig = Rig(tmp_path)
    rig.probe_ok = False
    orig = rig._spawn
    rig._spawn = lambda c, s: (lambda p: (setattr(p, "code", 1), p)[1])(orig(c, s))
    rig.server._spawn = rig._spawn
    start = time.monotonic()
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model)
    assert time.monotonic() - start < 3


def test_switching_models_replaces_the_loaded_one(tmp_path):
    rig = Rig(tmp_path)
    other = tmp_path / "ggml-base.en.bin"
    other.write_bytes(b"m")
    rig.ready()
    rig.server.transcribe(rig.wav, other)
    assert len(rig.spawned) == 2
    assert rig.spawned[0].stopped == 1
    assert rig.spawned[1].command[2] == str(other)
    assert rig.requests[0][0] == 40001   # served by the new process


def test_forget_model_unloads_only_that_model(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.server.forget_model(tmp_path / "other.bin")
    assert loaded(rig.server)
    rig.server.forget_model(rig.model)
    assert not loaded(rig.server)


def test_deleted_model_file_is_a_clean_error_not_a_hang(tmp_path):
    rig = Rig(tmp_path)
    rig.model.unlink()
    with pytest.raises(ServerError, match="model missing"):
        rig.server.transcribe(rig.wav, rig.model)


def wait_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_idle_timeout_unloads(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.now += 299
    time.sleep(0.1)
    assert loaded(rig.server)
    rig.now += 2
    assert wait_until(lambda: not loaded(rig.server))


def test_idle_timer_does_not_expire_while_a_dictation_is_in_flight(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.server.set_busy(True)
    rig.now += 7200
    time.sleep(0.15)
    assert loaded(rig.server)
    rig.server.set_busy(False)       # the clock restarts when it ends
    time.sleep(0.15)
    assert loaded(rig.server)
    rig.now += 301
    assert wait_until(lambda: not loaded(rig.server))


def test_idle_timer_does_not_expire_during_a_request(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    gate = threading.Event()

    def slow(port, wav, fields, timeout):
        gate.wait(3)
        return "done"
    rig.server._infer = slow
    result = []
    t = threading.Thread(target=lambda: result.append(
        rig.server.transcribe(rig.wav, rig.model)))
    t.start()
    time.sleep(0.05)
    rig.now += 7200
    time.sleep(0.15)
    assert loaded(rig.server)
    gate.set()
    t.join(3)
    assert result[0][0] == "done"


def test_never_means_never(tmp_path):
    rig = Rig(tmp_path)
    rig.server.configure("never")
    rig.ready()
    rig.now += 10 ** 7
    time.sleep(0.15)
    assert loaded(rig.server)


def test_shutdown_stops_the_server_and_refuses_new_work(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.server.shutdown()
    assert rig.spawned[0].stopped == 1
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model)


def test_unload_choices_match_handys(tmp_path):
    assert set(ws.UNLOAD_SECONDS) == {
        "never", "immediately", "min2", "min5", "min10", "min15", "hour1"}
    assert ws.DEFAULT_UNLOAD == "min5"
    assert ws.unload_seconds("min2") == 120 and ws.unload_seconds("hour1") == 3600
    assert ws.unload_seconds("never") is None
    assert ws.unload_seconds("garbage") == 300


# ---- routing ---------------------------------------------------------------

class FakeServer:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def transcribe(self, wav, model, prompt=None, unload=None):
        self.calls.append((wav, model, prompt, unload))
        if self.error:
            raise self.error
        return self.text, {"model_wait": 12}


@pytest.fixture
def cli(monkeypatch):
    calls = []
    monkeypatch.setattr(transcriber, "transcribe",
                        lambda wav, model_path=None, whisper_bin=None, prompt=None:
                        calls.append((wav, model_path, prompt)) or "from cli")
    return calls


def test_routed_uses_the_server_when_it_works(cli):
    srv = FakeServer("from server")
    text, info = transcriber.transcribe_routed("a.wav", "m", "Glossary: X.", srv, "min5")
    assert (text, info["mode"], info["timings"]) == ("from server", "warm", {"model_wait": 12})
    assert cli == []


def test_routed_falls_back_to_cli_on_any_server_failure(cli):
    for error in (ServerError("x"), OSError("y"), RuntimeError("z"), ValueError("w")):
        text, info = transcriber.transcribe_routed(
            "a.wav", "m", "Glossary: X.", FakeServer(error=error), "min5")
        assert text == "from cli" and info["mode"] == "cli-fallback"
        assert "server_attempt" in info["timings"]
    assert cli[0] == ("a.wav", "m", "Glossary: X.")


def test_routed_immediately_and_no_server_are_plain_cli(cli):
    srv = FakeServer("from server")
    assert transcriber.transcribe_routed("a", "m", None, srv, "immediately")[1]["mode"] == "cli"
    assert transcriber.transcribe_routed("a", "m", None, None, "min5")[1]["mode"] == "cli"
    assert srv.calls == []


# ---- Api -------------------------------------------------------------------

class FakeWarm:
    def __init__(self, **kw):
        self.events = []
        self.server = FakeServer(**kw)

    def prewarm(self, model, unload=None):
        self.events.append(("prewarm", str(model), unload))

    def set_busy(self, busy):
        self.events.append(("busy", busy))

    def transcribe(self, *a, **k):
        return self.server.transcribe(*a, **k)

    def unload(self):
        self.events.append(("unload",))

    def configure(self, unload):
        self.events.append(("configure", unload))

    def forget_model(self, path):
        self.events.append(("forget", path.name))

    def shutdown(self):
        self.events.append(("shutdown",))


def make_api(tmp_path, warm, history=None):
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    for name in ("small.en", "base.en"):
        (models / f"ggml-{name}.bin").write_bytes(b"m")
    return Api(config_path=tmp_path / "config.json",
               history=history or HistoryStore(tmp_path / "h.jsonl"),
               recorder_factory=recorder_writing(tone(1.0, 8000)),
               models_dir=models, warm_server=warm)


def dictate(api):
    assert api.start_recording().get("recording")
    return api.stop_recording()


def test_key_down_starts_the_load_and_a_dictation_runs_warm(tmp_path):
    warm = FakeWarm(text="Hello Voisr")
    api = make_api(tmp_path, warm)
    api.PREWARM_DELAY_S = 0
    res = dictate(api)
    assert res["text"] == "Hello Voisr"
    assert wait_until(lambda: any(e[0] == "prewarm" for e in warm.events))
    pre = next(e for e in warm.events if e[0] == "prewarm")
    assert pre[1].endswith("ggml-small.en.bin") and pre[2] == "min5"
    # busy from key down to the end of the dictation, then released
    busy = [e[1] for e in warm.events if e[0] == "busy"]
    assert busy[0] is True and busy[-1] is False
    item = api.get_history()[0]
    assert item["mode"] == "warm" and item["model"] == "small.en"
    assert {"transcribe", "cleanup", "model_wait"} <= set(item["timings_ms"])


def test_a_failing_server_loses_no_dictation_and_is_recorded(tmp_path, cli):
    api = make_api(tmp_path, FakeWarm(error=ServerError("crashed")))
    res = dictate(api)
    assert res["text"] == "From cli"      # cleaned text from whisper-cli
    item = api.get_history()[0]
    assert item["mode"] == "cli-fallback"
    assert "server_attempt" in item["timings_ms"]


def test_queued_dictations_stay_in_order_through_a_failure(tmp_path, cli):
    warm = FakeWarm(error=ServerError("x"))
    api = make_api(tmp_path, warm)
    results = [dictate(api) for _ in range(3)]
    assert all("error" not in r and r["text"] for r in results)
    assert len(api.get_history()) == 3


def test_immediately_setting_uses_cli_and_records_it(tmp_path, cli):
    warm = FakeWarm(text="never used")
    api = make_api(tmp_path, warm)
    api.set_config({"model_unload": "immediately"})
    dictate(api)
    assert api.get_history()[0]["mode"] == "cli"
    assert ("configure", "immediately") in warm.events


def test_changing_model_unloads_the_old_one(tmp_path):
    warm = FakeWarm(text="x")
    api = make_api(tmp_path, warm)
    api.set_config({"model": "base.en"})
    assert ("unload",) in warm.events
    warm.events.clear()
    api.set_config({"paste": False})
    assert ("unload",) not in warm.events


def test_deleting_a_model_drops_it_from_the_server_first(tmp_path):
    warm = FakeWarm(text="x")
    api = make_api(tmp_path, warm)
    assert api.delete_model("base.en") == {"ok": True}
    assert ("forget", "ggml-base.en.bin") in warm.events


def test_close_shuts_the_server_down(tmp_path):
    warm = FakeWarm(text="x")
    make_api(tmp_path, warm).close()
    assert ("shutdown",) in warm.events


def test_model_unload_setting_is_validated(tmp_path):
    api = make_api(tmp_path, FakeWarm(text="x"))
    assert "error" in api.set_config({"model_unload": "soon"})
    assert api.set_config({"model_unload": "hour1"})["model_unload"] == "hour1"
    assert api.get_config()["model_unload"] == "hour1"


def test_default_unload_is_five_minutes(tmp_path):
    api = make_api(tmp_path, None)
    assert api.get_config()["model_unload"] == "min5"


def test_prewarm_failure_never_reaches_the_hotkey_path(tmp_path):
    warm = FakeWarm(text="x")
    warm.prewarm = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    api = make_api(tmp_path, warm)
    assert api.start_recording().get("recording")
    api.stop_recording()


# ---- parent-death guard and stale cleanup -----------------------------------

PARENT = """
import sys, time
from pathlib import Path
from voisr import whisper_server as ws
state = Path(sys.argv[1])
popen = ws._spawn_guarded(["sleep", "600"], state)
for _ in range(100):
    if state.exists():
        break
    time.sleep(0.05)
print(popen.pid, flush=True)
time.sleep(600)
"""


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def gone(pid, timeout=6):
    return wait_until(lambda: not alive(pid), timeout)


def start_parent(tmp_path):
    state = tmp_path / "state.json"
    parent = subprocess.Popen([sys.executable, "-c", PARENT, str(state)],
                              stdout=subprocess.PIPE, text=True,
                              cwd=os.getcwd())
    guard_pid = int(parent.stdout.readline())
    assert wait_until(state.exists)
    info = json.loads(state.read_text())
    return parent, state, guard_pid, info


def test_server_dies_when_its_parent_is_sigkilled(tmp_path):
    parent, state, guard_pid, info = start_parent(tmp_path)
    try:
        assert alive(info["server"]) and alive(guard_pid)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait()
        assert gone(info["server"]), "child outlived a force-quit parent"
        assert gone(guard_pid)
        assert wait_until(lambda: not state.exists())
    finally:
        for pid in (info["server"], guard_pid, parent.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_stale_cleanup_stops_only_the_recorded_server(tmp_path):
    parent, state, guard_pid, info = start_parent(tmp_path)
    bystander = subprocess.Popen(["sleep", "600"])   # not ours, not recorded
    try:
        # kill the guard too, so the server is a true orphan
        os.kill(guard_pid, signal.SIGKILL)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait()
        assert alive(info["server"])
        # the recorded server is `sleep`, so the name check refuses it ...
        assert ws.cleanup_stale(state) == []
        assert alive(info["server"])
    finally:
        for pid in (info["server"], bystander.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        bystander.wait()


def test_stale_cleanup_kills_a_matching_orphan_and_spares_a_lookalike(tmp_path):
    fake_bin = tmp_path / "whisper-server"
    fake_bin.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(600)\n")
    fake_bin.chmod(0o755)
    mine = subprocess.Popen([str(fake_bin)])
    theirs = subprocess.Popen([str(fake_bin)])       # same name, not ours
    try:
        time.sleep(0.2)
        state = tmp_path / "state.json"
        state.write_text(json.dumps({
            "owner": 2 ** 22 + 12345, "guard": 2 ** 22 + 54321,
            "guard_start": "x", "server": mine.pid,
            "server_start": ws._ps_start(mine.pid)}))
        stopped = ws.cleanup_stale(state)
        assert stopped == [mine.pid]
        assert mine.wait(5) is not None
        assert theirs.poll() is None
        assert not state.exists()
    finally:
        for p in (mine, theirs):
            p.kill()
            p.wait()


def test_stale_cleanup_leaves_a_live_owners_server_alone(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "owner": os.getppid(), "guard": 1, "server": 1}))
    assert ws.cleanup_stale(state) == []
    assert state.exists()


def test_stale_cleanup_ignores_a_missing_or_garbled_state_file(tmp_path):
    assert ws.cleanup_stale(tmp_path / "none.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert ws.cleanup_stale(bad) == []


def test_guard_stops_the_server_on_a_clean_close(tmp_path):
    state = tmp_path / "state.json"
    popen = ws._spawn_guarded(["sleep", "600"], state)
    assert wait_until(state.exists)
    server = json.loads(state.read_text())["server"]
    proc = ws._Process(popen, 1, "m", state)
    proc.stop()
    assert gone(server) and popen.poll() is not None


def test_guard_module_uses_a_real_interpreter_not_the_app_launcher(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/Applications/Voisr.app/Contents/MacOS/Voisr")
    py = ws._python_for_guard()
    assert py and os.path.basename(py).startswith("python")


def test_requests_go_to_loopback_without_proxy_settings(monkeypatch):
    seen = []
    real = ws.http.client.HTTPConnection

    class Spy(real):
        def __init__(self, host, port=None, **kw):
            seen.append(host)
            super().__init__(host, port, **kw)
    monkeypatch.setattr(ws.http.client, "HTTPConnection", Spy)
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    assert ws._http_probe(ws.free_port(), 0.2) is False    # nothing listens
    assert seen == ["127.0.0.1"]


# ---- review fixes ------------------------------------------------------------

def test_hand_edited_unhashable_unload_value_is_rejected_not_a_crash(tmp_path):
    from voisr.app import config_errors, load_config
    assert config_errors({"model_unload": []}) == ["invalid value for model_unload"]
    assert config_errors({"model_unload": {}}) == ["invalid value for model_unload"]
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model_unload": [], "paste": False}))
    cfg = load_config(path)
    assert cfg["model_unload"] == "min5" and cfg["paste"] is False
    server = WhisperServer(tmp_path / "s.json")
    server.configure([])                       # no TypeError
    assert ws.unload_seconds([]) == 300


def _sleeper(tmp_path):
    script = tmp_path / "whisper-server"
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(600)\n")
    script.chmod(0o755)
    proc = subprocess.Popen([str(script)])
    time.sleep(0.2)
    return proc


def test_stop_never_kills_a_server_that_belongs_to_a_newer_guard(tmp_path):
    other = _sleeper(tmp_path)               # a newer guard's server
    state = tmp_path / "state.json"
    try:
        state.write_text(json.dumps({"guard": 4242, "server": other.pid}))
        old = FakePopen(["x"])
        old.pid = 1111                         # our guard is not the recorded one
        ws._Process(old, 1, "m", state).stop()
        assert other.poll() is None, "stopped a server it did not start"
        # when the state does name our guard, our own leftover server is killed
        state.write_text(json.dumps({"guard": 1111, "server": other.pid}))
        old2 = FakePopen(["x"])
        old2.pid = 1111
        ws._Process(old2, 1, "m", state).stop()
        assert other.wait(5) is not None
    finally:
        other.kill()
        other.wait()


def test_a_request_after_an_unload_always_gets_a_live_loader(tmp_path):
    rig = Rig(tmp_path)
    rig.probe_ok = False                       # the first loader never finishes
    rig.server.prewarm(rig.model)
    assert wait_until(lambda: len(rig.spawned) == 1)
    rig.server.unload()                        # its loader is now stale
    rig.probe_ok = True
    result = []
    t = threading.Thread(target=lambda: result.append(
        rig.server.transcribe(rig.wav, rig.model)))
    start = time.monotonic()
    t.start()
    t.join(5)
    assert result and result[0][0] == "hello world"
    assert time.monotonic() - start < 3, "waited for a loader that was gone"
    assert len(rig.spawned) == 2
    assert wait_until(lambda: rig.spawned[0].stopped == 1)   # stale one stopped


def test_prewarm_after_unload_starts_a_new_loader(tmp_path):
    rig = Rig(tmp_path)
    rig.probe_ok = False
    rig.server.prewarm(rig.model)
    assert wait_until(lambda: len(rig.spawned) == 1)
    rig.server.unload()
    rig.probe_ok = True
    rig.server.prewarm(rig.model)
    assert wait_until(lambda: loaded(rig.server))
    assert len(rig.spawned) == 2


def test_unload_during_a_request_lets_it_finish_and_starts_no_cooldown(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    gate = threading.Event()

    def slow(port, wav, fields, timeout):
        gate.wait(5)
        return "finished"
    rig.server._infer = slow
    result = []
    t = threading.Thread(target=lambda: result.append(
        rig.server.transcribe(rig.wav, rig.model)))
    t.start()
    assert wait_until(lambda: rig.server._proc and rig.server._proc.inflight == 1)
    rig.server.unload()                        # a setting or model change
    time.sleep(0.1)
    assert rig.spawned[0].stopped == 0, "killed a request in flight"
    gate.set()
    t.join(5)
    assert result[0][0] == "finished"
    assert wait_until(lambda: rig.spawned[0].stopped == 1)
    rig.server._infer = rig._infer
    assert rig.server.transcribe(rig.wav, rig.model)[0] == "hello world"  # no cooldown


def test_a_request_cut_off_by_an_unload_is_not_a_server_failure(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    box = {}

    def dies(port, wav, fields, timeout):
        rig.server.unload()                    # shutdown-style unload mid-request
        raise ServerError("connection reset")
    rig.server._infer = dies
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model)
    assert rig.server._failed_at is None       # no cooldown
    rig.server._infer = rig._infer
    assert rig.server.transcribe(rig.wav, rig.model)[0] == "hello world"


def test_a_real_request_failure_still_starts_the_cooldown(tmp_path):
    rig = Rig(tmp_path)
    rig.infer_result = ServerError("bad")
    with pytest.raises(ServerError):
        rig.server.transcribe(rig.wav, rig.model)
    assert rig.server._failed_at is not None


def test_idle_watcher_skips_when_activity_just_happened(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.now += 400
    rig.server.set_busy(True)                  # a recording starts as it fires
    time.sleep(0.15)
    assert loaded(rig.server)
    rig.server.set_busy(False)                 # the clock restarts at the end
    time.sleep(0.15)
    assert loaded(rig.server)


def test_a_stale_loader_failure_does_not_start_a_cooldown(tmp_path):
    rig = Rig(tmp_path)
    rig.probe_ok = False
    rig.server.prewarm(rig.model)
    assert wait_until(lambda: len(rig.spawned) == 1)
    rig.server.unload()
    rig.spawned[0].code = 1                    # the stale server dies afterwards
    time.sleep(0.2)
    assert rig.server._failed_at is None


def test_a_tap_loads_nothing_but_a_held_key_does(tmp_path):
    warm = FakeWarm(text="x")
    api = make_api(tmp_path, warm)
    api.PREWARM_DELAY_S = 0.2
    assert api.start_recording().get("recording")
    api.stop_recording()                       # released before the delay
    time.sleep(0.5)
    assert not [e for e in warm.events if e[0] == "prewarm"]
    assert api.start_recording().get("recording")
    assert wait_until(lambda: any(e[0] == "prewarm" for e in warm.events))
    assert api.is_recording()                  # loaded while still held
    api.stop_recording()
