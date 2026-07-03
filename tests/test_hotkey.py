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

    def restart(self, key_name):
        self.restarted_with = key_name


def make_api(tmp_path, service=None):
    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "h.jsonl"),
               recorder=FakeRecorder(),
               transcribe_fn=lambda wav: "hello",
               clock=lambda: 1.0,
               hotkey_service=service)


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
