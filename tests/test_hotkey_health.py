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


def test_event_tap_defaults_to_listen_and_validates():
    assert DEFAULT_CONFIG["event_tap"] == "listen"
    assert config_errors({"event_tap": "active"}) == []
    assert config_errors({"event_tap": "grab"}) == ["invalid value for event_tap"]


def test_start_uses_the_configured_tap_mode(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, made = make(ConfigApi(event_tap="active"))
    svc.start()
    assert svc.tap_mode == "active" and made == ["active"]
    svc.stop()


def test_missing_key_means_listen(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, made = make(ConfigApi())
    svc.start()
    assert made == ["listen"]
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


def test_unknown_event_tap_value_falls_back_to_listen(monkeypatch):
    monkeypatch.setattr("voisr.ui.request_permissions", lambda mode="listen": None)
    svc, made = make(ConfigApi(event_tap="grab"))
    svc.start()
    assert svc.tap_mode == "listen" and made == ["listen"]
    svc.stop()
