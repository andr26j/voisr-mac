"""HotkeyService picks its tap mode from config, probes the permission that
mode needs, and reports health from the tap macOS actually gave it."""
import sys
import types

from voisr.app import DEFAULT_CONFIG, config_errors
from voisr.ui import HotkeyService, input_permissions_ok, request_permissions


class ConfigApi:
    def __init__(self, **cfg):
        self.cfg = {"hotkey": "alt_r", **cfg}

    def get_config(self):
        return dict(self.cfg)

    def note_user_typing(self):
        pass


class FakeListener:
    def __init__(self, state):
        self.state = state

    def wait_started(self, timeout):
        return self.state

    def stop(self):
        pass


def make(api, *, granted=True, tap_state="running"):
    made = []

    def factory(handler, mode):
        made.append(mode)
        return FakeListener(tap_state)

    svc = HotkeyService(api, permissions_check=lambda: granted,
                        listener_factory=factory, poll_interval=60)
    return svc, made


def test_event_tap_defaults_to_active_and_validates():
    assert DEFAULT_CONFIG["event_tap"] == "active"
    assert config_errors({"event_tap": "active"}) == []
    assert config_errors({"event_tap": "grab"}) == ["invalid value for event_tap"]


def test_start_uses_the_configured_tap_mode(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, made = make(ConfigApi(event_tap="active"))
    svc.start()
    assert svc.tap_mode == "active" and made == ["active"]
    svc.stop()


def test_missing_key_means_active(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, made = make(ConfigApi())
    svc.start()
    assert made == ["active"]
    svc.stop()


def fake_quartz(monkeypatch, *, listen=True, post=True):
    """Stub Quartz (and the Accessibility prompt) so no real TCC is touched."""
    calls = []
    q = types.SimpleNamespace(
        CGPreflightListenEventAccess=lambda: calls.append("preflight_listen") or listen,
        CGPreflightPostEventAccess=lambda: calls.append("preflight_post") or post,
        CGRequestListenEventAccess=lambda: calls.append("request_listen"),
        CGRequestPostEventAccess=lambda: calls.append("request_post"))
    monkeypatch.setitem(sys.modules, "Quartz", q)
    monkeypatch.setitem(sys.modules, "ApplicationServices", types.SimpleNamespace(
        AXIsProcessTrustedWithOptions=lambda opts: calls.append("ax_prompt"),
        kAXTrustedCheckOptionPrompt="prompt"))
    return calls


def test_input_permissions_ok_probes_only_what_the_mode_needs(monkeypatch):
    calls = fake_quartz(monkeypatch, listen=False, post=True)
    assert input_permissions_ok("active") is True
    assert calls == ["preflight_post"]
    calls.clear()
    assert input_permissions_ok("listen") is False
    assert calls == ["preflight_listen"]
    calls.clear()
    assert input_permissions_ok() is False  # default is listen
    assert calls == ["preflight_listen"]


def test_request_permissions_active_never_asks_for_input_monitoring(monkeypatch):
    calls = fake_quartz(monkeypatch, listen=False, post=False)
    request_permissions("active")
    assert "request_listen" not in calls and "preflight_listen" not in calls
    assert "request_post" in calls


def test_request_permissions_listen_asks_for_input_monitoring(monkeypatch):
    calls = fake_quartz(monkeypatch, listen=False, post=False)
    request_permissions("listen")
    assert "request_listen" in calls and "request_post" in calls
    calls = fake_quartz(monkeypatch, listen=True, post=True)  # already granted
    request_permissions("listen")
    assert "request_listen" not in calls


def test_default_permissions_check_follows_tap_mode(monkeypatch):
    calls = fake_quartz(monkeypatch, listen=False, post=True)
    svc = HotkeyService(ConfigApi(), listener_factory=lambda h, m: None)
    assert svc._permissions_check() is False  # starts in listen mode
    svc.tap_mode = "active"
    assert svc._permissions_check() is True
    assert calls == ["preflight_listen", "preflight_post"]


def test_unknown_event_tap_value_falls_back_to_active(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, made = make(ConfigApi(event_tap="grab"))
    svc.start()
    assert svc.tap_mode == "active" and made == ["active"]
    svc.stop()


def test_running_tap_is_active(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, _ = make(ConfigApi(event_tap="active"), granted=True, tap_state="running")
    svc.start()
    assert svc.health == "active" and svc.status.startswith("active on ")
    svc.stop()


def test_no_grant_and_no_tap_is_waiting(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, _ = make(ConfigApi(event_tap="active"), granted=False, tap_state="failed")
    svc.start()
    assert svc.health == "waiting"
    assert "Accessibility" in svc.status and "Input Monitoring" not in svc.status
    svc.stop()


def test_granted_but_tap_refused_is_not_effective(monkeypatch):
    # Review Focus 3: the stale-grant case must not read "waiting for permission"
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, _ = make(ConfigApi(event_tap="active"), granted=True, tap_state="failed")
    svc.start()
    assert svc.health == "not_effective"
    assert not svc.status.startswith("waiting")
    svc.stop()


def test_get_permissions_flags_the_hotkey_pane_when_not_effective(tmp_path, monkeypatch):
    from voisr import permissions
    from voisr.history import HistoryStore
    from voisr.ui import Api

    monkeypatch.setattr(permissions, "DEFAULT_PROBES", {
        "microphone": lambda: True, "input_monitoring": lambda: True,
        "accessibility": lambda: True})

    class Svc:
        health = "not_effective"
        running = True

    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"))
    api.set_config({"event_tap": "active"})
    api.attach_hotkey_service(Svc())
    panes = {p["key"]: p for p in api.get_permissions()["panes"]}
    assert panes["accessibility"]["effective"] is False
    assert panes["microphone"]["effective"] is True


def test_listener_without_wait_started_follows_the_permission_check(monkeypatch):
    # a bare handler (test double) has no tap state: it is running only if the
    # permission check says so, so a denied check still reads "waiting"
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    for granted, health in ((True, "active"), (False, "waiting")):
        svc = HotkeyService(ConfigApi(), permissions_check=lambda g=granted: g,
                            listener_factory=lambda handler, mode="listen": handler,
                            poll_interval=60)
        svc.start()
        assert svc.health == health
        svc.stop()


def test_stop_sets_health_off_and_status_reports_it(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, _ = make(ConfigApi(), granted=True, tap_state="running")
    assert svc.health == "off"
    svc.start()
    assert svc.health == "active"
    svc.stop()
    assert svc.health == "off"


def test_get_hotkey_status_reports_health(tmp_path):
    from voisr.history import HistoryStore
    from voisr.ui import Api

    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"))
    assert api.get_hotkey_status()["health"] == "off"

    class Svc:
        health = "not_effective"
        status = "Accessibility is switched on for Voisr but macOS isn't honouring it"
        running = True
        holding = False

    api.attach_hotkey_service(Svc())
    assert api.get_hotkey_status()["health"] == "not_effective"


# --- fix round 1 ---------------------------------------------------------

import threading
import time


class SeqListener:
    """A listener whose state the test controls; wait_started reports it."""

    def __init__(self, state, block=None, entered=None):
        self.state = state
        self.stopped = False
        self._block = block
        self._entered = entered

    def wait_started(self, timeout):
        if self._entered is not None:
            self._entered.set()
        if self._block is not None:
            self._block.wait(5)
        return self.state

    def stop(self):
        self.stopped = True


def seq_service(states, gate=None, **kw):
    """gate: an Event the respawn (every factory call after the first) waits
    for, so a test can look at the state before the poll moves it on."""
    made = []

    def factory(handler, mode):
        if gate is not None and made:
            gate.wait(5)
        lst = states[min(len(made), len(states) - 1)]
        made.append(lst)
        return lst

    svc = HotkeyService(ConfigApi(event_tap="active"),
                        permissions_check=lambda: True,
                        listener_factory=factory, poll_interval=0.05, **kw)
    return svc, made


def wait_until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_slow_tap_is_starting_not_not_effective_and_is_not_respawned(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    slow = SeqListener("starting")
    svc, made = seq_service([slow])
    svc.start()
    assert svc.health == "starting"
    assert svc.status.startswith("starting push-to-talk on ")
    assert "reset" not in svc.status and not svc.status.startswith("waiting")
    time.sleep(0.3)  # several polls while the tap is still coming up
    assert made == [slow] and not slow.stopped and svc.health == "starting"
    slow.state = "running"
    assert wait_until(lambda: svc.health == "active")
    assert made == [slow]
    watcher = svc._watcher
    assert wait_until(lambda: not watcher.is_alive())
    svc.stop()


def test_failed_tap_recovers_when_a_respawn_runs(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    first, second = SeqListener("failed"), SeqListener("running")
    gate = threading.Event()
    svc, made = seq_service([first, second], gate=gate)
    svc.start()
    assert svc.health == "not_effective"  # the respawn is held at the gate
    gate.set()
    watcher = svc._watcher
    assert wait_until(lambda: svc.health == "active")
    assert made == [first, second] and first.stopped
    assert wait_until(lambda: not watcher.is_alive())
    svc.stop()


def test_health_stays_off_after_stop_even_if_a_refresh_races(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    release, entered = threading.Event(), threading.Event()
    svc, made = seq_service([SeqListener("failed"),
                             SeqListener("running", block=release, entered=entered)])
    svc.start()
    assert svc.health == "not_effective"
    assert entered.wait(3)  # the poll is now blocked inside wait_started
    threading.Timer(0.1, release.set).start()
    svc.stop()
    assert svc.health == "off"
    time.sleep(0.1)
    assert svc.health == "off"


def test_get_permissions_flags_only_a_running_service_that_is_not_effective(tmp_path, monkeypatch):
    from voisr import permissions
    from voisr.history import HistoryStore
    from voisr.ui import Api

    monkeypatch.setattr(permissions, "DEFAULT_PROBES", {
        "microphone": lambda: True, "input_monitoring": lambda: True,
        "accessibility": lambda: True})
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"))
    api.set_config({"event_tap": "active"})

    def hotkey_effective(health, running):
        svc = types.SimpleNamespace(health=health, running=running)
        api.attach_hotkey_service(svc)
        return {p["key"]: p for p in api.get_permissions()["panes"]}["accessibility"]["effective"]

    assert hotkey_effective("not_effective", True) is False
    assert hotkey_effective("not_effective", False) is True
    assert hotkey_effective("starting", True) is True


def test_starting_then_failed_without_permission_ends_up_waiting(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    lst = SeqListener("starting")
    made = []

    def factory(handler, mode):
        made.append(lst)
        return lst

    svc = HotkeyService(ConfigApi(event_tap="active"), permissions_check=lambda: False,
                        listener_factory=factory, poll_interval=0.05)
    svc.start()
    assert svc.health == "starting"
    lst.state = "failed"  # macOS refused: no grant, and the tap is dead
    assert wait_until(lambda: svc.health == "waiting")
    assert svc.status.startswith("waiting for permission")
    assert made == [lst]
    svc.stop()


from voisr.history import HistoryStore
from voisr.ui import Api


def _api(tmp_path, mic):
    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "h.jsonl"),
               mic_check=lambda: mic)


def test_denied_mic_refuses_the_press_and_names_the_permission(tmp_path):
    result = _api(tmp_path, False).start_recording()
    assert result["permission"] == "microphone" and "error" in result


def test_unasked_mic_does_not_block(tmp_path):
    # Review Focus 5: None = never asked; recording is what makes macOS ask
    from types import SimpleNamespace
    api = _api(tmp_path, None)
    api._recordings.start = lambda: SimpleNamespace(sequence=1)  # never opens a real mic
    assert api.start_recording() == {"recording": True, "session_id": 1}


def test_settings_opens_at_most_once_a_minute_on_repeated_refusals():
    # Review Focus 4
    class RefusingApi(ConfigApi):
        def start_recording(self):
            return {"error": "mic off", "permission": "microphone"}

    opened = []
    svc = HotkeyService(RefusingApi(), permissions_check=lambda: True,
                        listener_factory=lambda h: h)
    svc._pane_opener = opened.append
    clock = [1000.0]
    svc._clock = lambda: clock[0]
    for _ in range(5):            # auto-repeat and fresh presses alike
        svc.press(); svc.release()
    assert opened == ["microphone"]
    clock[0] += 61
    svc.press(); svc.release()
    assert opened == ["microphone", "microphone"]


def test_a_late_event_after_stop_runs_nothing_on_the_tap_thread(monkeypatch):
    # Between _stop_worker() and listener.stop() -- or from a tap that has
    # not wound down yet -- _submit has no worker and would run press()
    # inline on the tap thread.
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    handlers = []
    typed = []

    class TypingApi(ConfigApi):
        def note_user_typing(self):
            typed.append(True)

    svc = HotkeyService(TypingApi(), permissions_check=lambda: True,
                        listener_factory=lambda h, mode: handlers.append(h)
                        or FakeListener("running"), poll_interval=60)
    ran = []
    svc.press = lambda *a, **k: ran.append("press")
    svc.release = lambda *a, **k: ran.append("release")
    svc.cancel_chord = lambda *a, **k: ran.append("chord")
    svc.start()
    svc.stop()
    handler = handlers[-1]
    handler(61, "down", 0)      # the hotkey (Right Option)
    handler(61, "up", 0)
    handler(14, "down", 0)      # any other key
    assert ran == [] and typed == []
