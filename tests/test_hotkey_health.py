"""HotkeyService picks its tap mode from config, probes the permission that
mode needs, and reports health from the tap macOS actually gave it."""
from voisr.app import DEFAULT_CONFIG, config_errors
from voisr.ui import HotkeyService


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
