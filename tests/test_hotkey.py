from voisr.history import HistoryStore
from voisr.ui import Api, HOTKEY_OPTIONS, resolve_key


class FakeRecorder:
    def start(self, path):
        self.path = path

    def stop(self):
        with open(self.path, "wb") as f:
            f.write(b"\0" * 2000)
        return ""


class FakeHotkeyService:
    def __init__(self):
        self.restarted_with = None
        self.status = "active on alt_r"
        self.running = True
        self.holding = False

    def restart(self, key_name):
        self.restarted_with = key_name


def make_api(tmp_path, service=None):
    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "h.jsonl"),
               recorder_factory=FakeRecorder,
               transcribe_fn=lambda wav: "hello",
               clock=lambda: 1.0,
               hotkey_service=service,
               paste_fn=lambda text: True,
               clipboard_fn=lambda text: None)


def test_hotkey_options_shape():
    values = [o["value"] for o in HOTKEY_OPTIONS]
    assert "alt_r" in values and "f13" in values
    assert all(set(o) == {"value", "label"} for o in HOTKEY_OPTIONS)


def test_get_hotkey_options(tmp_path):
    api = make_api(tmp_path)
    assert api.get_hotkey_options() == HOTKEY_OPTIONS


def test_resolve_key_named_and_char():
    from pynput import keyboard
    assert resolve_key("alt_r") == keyboard.Key.alt_r
    assert resolve_key("f13") == keyboard.Key.f13
    assert resolve_key("x") == keyboard.KeyCode.from_char("x")


def test_set_hotkey_persists_and_restarts_service(tmp_path):
    svc = FakeHotkeyService()
    api = make_api(tmp_path, service=svc)
    api.set_config({"hotkey": "f13"})
    assert api.get_config()["hotkey"] == "f13"
    assert svc.restarted_with == "f13"


def test_set_config_without_hotkey_change_does_not_restart(tmp_path):
    svc = FakeHotkeyService()
    api = make_api(tmp_path, service=svc)
    api.set_config({"paste": False})
    assert svc.restarted_with is None


def test_get_hotkey_status(tmp_path):
    svc = FakeHotkeyService()
    api = make_api(tmp_path, service=svc)
    status = api.get_hotkey_status()["status"]
    assert status.startswith("active on alt_r")
    assert ("paste ready" in status) or ("Accessibility" in status)
    api_no_svc = make_api(tmp_path)
    assert "hotkey" in api_no_svc.get_hotkey_status()["status"].lower() or api_no_svc.get_hotkey_status()["status"]


def test_hotkey_display_prefers_captured_label(tmp_path):
    """Priority 1: a captured key's stored hotkey_label always wins, even
    when hotkey also happens to match a known HOTKEY_OPTIONS value."""
    api = make_api(tmp_path)
    api.set_config({"hotkey": "alt_r", "hotkey_label": "Fn / Globe"})
    assert api.hotkey_display() == {"display": "Fn / Globe"}


def test_hotkey_display_falls_back_to_hotkey_options_label(tmp_path):
    """Priority 2: a fresh install ships hotkey="alt_r" with no
    hotkey_label — the raw config identifier must never reach the UI."""
    api = make_api(tmp_path)
    assert api.get_config().get("hotkey_label") is None
    assert api.get_config()["hotkey"] == "alt_r"
    assert api.hotkey_display() == {"display": "Right Option (⌥)"}


def test_hotkey_display_falls_back_to_raw_value_when_unrecognized(tmp_path):
    """Priority 3: an unrecognized hotkey value (not in HOTKEY_OPTIONS, no
    label) is shown as-is rather than crashing or going blank."""
    api = make_api(tmp_path)
    api.set_config({"hotkey": "some_future_key"})
    assert api.hotkey_display() == {"display": "some_future_key"}


def test_hotkey_display_unset_when_no_hotkey_configured(tmp_path):
    """Priority 4: nothing configured at all resolves to "unset"."""
    api = make_api(tmp_path)
    api.set_config({"hotkey": ""})
    assert api.hotkey_display() == {"display": "unset"}


def test_set_config_hotkey_name_clears_a_captured_vk(tmp_path):
    """L2: a legacy-name change restarts the listener with that name, so a
    stale captured hotkey_vk must not keep winning in _resolve_target."""
    import json
    (tmp_path / "config.json").write_text(json.dumps(
        {"hotkey": "alt_r", "hotkey_vk": 63, "hotkey_label": "Fn / Globe"}))
    svc = FakeHotkeyService()
    api = make_api(tmp_path, service=svc)
    api.set_config({"hotkey": "f13"})
    cfg = api.get_config()
    assert cfg["hotkey"] == "f13"
    assert "hotkey_vk" not in cfg
    assert "hotkey_label" not in cfg
    assert svc.restarted_with == "f13"


# ---- M6 (2026-10-12 audit): the Home dot reports the listener's real state ----

class StateService:
    def __init__(self, status="active on Right Option (⌥)", running=True, holding=False):
        self.status, self.running, self.holding = status, running, holding


def test_hotkey_state_listening_when_active(tmp_path):
    api = make_api(tmp_path, StateService())
    assert api.get_hotkey_status()["state"] == "listening"


def test_hotkey_state_off_without_a_service_or_permission(tmp_path):
    assert make_api(tmp_path)._hotkey_service is None
    assert make_api(tmp_path).get_hotkey_status()["state"] == "off"
    waiting = make_api(tmp_path, StateService(status="waiting for permission — open"))
    assert waiting.get_hotkey_status()["state"] == "off"


def test_hotkey_state_off_while_the_listener_is_stopped(tmp_path):
    # capture_hotkey stops the listener for up to 8 s; status still reads
    # "active on ..." then, so it can't be trusted alone
    api = make_api(tmp_path, StateService(running=False))
    assert api.get_hotkey_status()["state"] == "off"


def test_hotkey_state_busy_while_the_dock_is_recording(tmp_path):
    # press() drops every key press while another recording owns the mic
    api = make_api(tmp_path, StateService())
    api.start_recording()
    assert api.get_hotkey_status()["state"] == "busy"
    api.stop_recording()
    assert api.get_hotkey_status()["state"] == "listening"


def test_hotkey_state_listening_while_the_key_itself_records(tmp_path):
    api = make_api(tmp_path, StateService(holding=True))
    api.start_recording()
    assert api.get_hotkey_status()["state"] == "listening"
    api.stop_recording()


def test_hotkey_service_reports_running_and_holding(tmp_path):
    from voisr.ui import HotkeyService
    svc = HotkeyService(make_api(tmp_path), listener_factory=lambda h, mode="listen": None,
                        permissions_check=lambda: True)
    assert svc.running is False and svc.holding is False
    svc.start("alt_r")
    assert svc.running is True
    svc.press()
    assert svc.holding is True
    svc.release()
    svc.stop()
    assert svc.running is False


def test_changing_event_tap_restarts_the_service(tmp_path):
    svc = FakeHotkeyService()
    api = make_api(tmp_path, service=svc)
    api.set_config({"event_tap": "listen"})
    assert svc.restarted_with is not None
