"""First run: who sees it, what remembers it, and the launch prompt it holds."""
import json

import pytest

from voisr.history import HistoryStore
from voisr.ui import Api, HotkeyService


def _api(tmp_path, **kwargs):
    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "data" / "history.jsonl"), **kwargs)


def _saved(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


def test_a_fresh_install_needs_it_and_says_so_on_disk(tmp_path):
    api = _api(tmp_path)
    assert api.first_run_pending() is True
    assert _saved(tmp_path)["first_run_done"] is False


def test_quitting_midway_cant_make_it_look_like_an_existing_install(tmp_path):
    api = _api(tmp_path)
    api.first_run_pending()
    api.set_config({"hotkey": "cmd_r"})  # what step 2 can write
    assert _api(tmp_path).first_run_pending() is True


def test_an_existing_config_skips_it_and_stays_unwritten(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": "alt_r"}))
    before = path.read_bytes()
    assert _api(tmp_path).first_run_pending() is False
    assert path.read_bytes() == before


def test_history_with_no_config_skips_it_and_writes_nothing(tmp_path):
    # someone who dictated for months and never changed a setting has no config
    history = tmp_path / "data" / "history.jsonl"
    history.parent.mkdir()
    history.write_text('{"id": "a", "text": "hello"}\n')
    assert _api(tmp_path).first_run_pending() is False
    assert not (tmp_path / "config.json").exists()


def test_finishing_is_remembered(tmp_path):
    api = _api(tmp_path)
    api.first_run_pending()
    assert "error" not in api.set_config({"first_run_done": True})
    assert api.get_first_run() == {"pending": False}
    assert _api(tmp_path).first_run_pending() is False


def test_a_bad_value_cant_be_saved_or_reopen_it(tmp_path):
    api = _api(tmp_path)
    assert "error" in api.set_config({"first_run_done": "yes"})
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"first_run_done": "yes"}))
    assert _api(tmp_path).first_run_pending() is False  # dropped, so it's an existing file


def test_the_key_stays_out_of_the_defaults():
    # in DEFAULT_CONFIG, every existing install would read as not finished
    from voisr.app import DEFAULT_CONFIG
    assert "first_run_done" not in DEFAULT_CONFIG


def test_an_unwritable_config_still_shows_first_run(tmp_path, monkeypatch):
    api = _api(tmp_path)

    def refuse(cfg):
        raise OSError("read-only")
    monkeypatch.setattr(api, "_write_config_atomic", refuse)
    assert api.first_run_pending() is True


# ---------- the first trip to the pane asks macOS ----------

def _opens(monkeypatch):
    opened = []
    monkeypatch.setattr("voisr.ui.permissions.open_pane",
                        lambda key: opened.append(key) or True)
    return opened


def test_the_first_trip_to_a_pane_asks_macos_so_voisr_is_listed(tmp_path, monkeypatch):
    opened = _opens(monkeypatch)
    asked = []
    api = _api(tmp_path, grant_requester=asked.append)
    api.first_run_pending()
    assert api.open_settings_pane("accessibility") == {"ok": True}
    assert api.open_settings_pane("accessibility") == {"ok": True}
    assert asked == ["accessibility"]
    assert opened == ["accessibility", "accessibility"]


def test_a_key_change_doesnt_refill_what_was_asked(tmp_path, monkeypatch):
    _opens(monkeypatch)
    asked = []
    api = _api(tmp_path, grant_requester=asked.append)
    api.first_run_pending()
    api.open_settings_pane("accessibility")
    api.first_run_pending()  # capture_hotkey restarts the service, which asks again
    api.open_settings_pane("accessibility")
    assert asked == ["accessibility"]


def test_set_up_later_still_asks_when_home_opens_the_pane(tmp_path, monkeypatch):
    _opens(monkeypatch)
    asked = []
    api = _api(tmp_path, grant_requester=asked.append)
    api.first_run_pending()
    api.set_config({"first_run_done": True})
    api.open_settings_pane("accessibility")
    assert asked == ["accessibility"]


def test_an_existing_install_only_opens_the_pane(tmp_path, monkeypatch):
    opened = _opens(monkeypatch)
    asked = []
    (tmp_path / "config.json").write_text("{}")
    api = _api(tmp_path, grant_requester=asked.append)
    api.first_run_pending()
    api.open_settings_pane("accessibility")
    assert asked == []
    assert opened == ["accessibility"]


def test_the_mic_is_never_held(tmp_path, monkeypatch):
    # macOS asks for it on the first dictation, as the step says
    _opens(monkeypatch)
    asked = []
    api = _api(tmp_path, grant_requester=asked.append)
    api.first_run_pending()
    api.open_settings_pane("microphone")
    assert asked == []


def test_reset_counts_as_asking(tmp_path, monkeypatch):
    # reset_permission asks macOS itself, so Open Settings mustn't ask again
    _opens(monkeypatch)
    monkeypatch.setattr("voisr.ui.permissions.reset_grant", lambda key: {"ok": True})
    asked = []
    api = _api(tmp_path, grant_requester=asked.append)
    api.first_run_pending()
    api.reset_permission("accessibility")
    api.open_settings_pane("accessibility")
    assert asked == ["accessibility"]


def test_a_failed_reset_still_leaves_the_ask_to_come(tmp_path, monkeypatch):
    _opens(monkeypatch)
    monkeypatch.setattr("voisr.ui.permissions.reset_grant",
                        lambda key: {"ok": False, "error": "no"})
    asked = []
    api = _api(tmp_path, grant_requester=asked.append)
    api.first_run_pending()
    api.reset_permission("accessibility")
    api.open_settings_pane("accessibility")
    assert asked == ["accessibility"]


def test_a_failed_ask_still_opens_the_pane(tmp_path, monkeypatch):
    opened = _opens(monkeypatch)

    def boom(key):
        raise RuntimeError("no")
    api = _api(tmp_path, grant_requester=boom)
    api.first_run_pending()
    assert api.open_settings_pane("accessibility") == {"ok": True}
    assert opened == ["accessibility"]


# ---------- the launch prompt waits ----------

class FakeApi:
    def __init__(self, pending):
        self.pending = pending

    def get_config(self):
        return {"hotkey": "alt_r", "event_tap": "active"}

    def first_run_pending(self):
        if isinstance(self.pending, Exception):
            raise self.pending
        return self.pending


class FakeWidget:
    def show(self, *a, **k): pass
    def hide(self, *a, **k): pass


class FakeListener:
    state = "running"

    def wait_started(self, timeout):
        return "running"

    def stop(self):
        pass


def _service(monkeypatch, pending, granted):
    prompts = []
    monkeypatch.setattr("voisr.ui.request_permissions",
                        lambda mode="listen": prompts.append(mode))
    spawned = []

    def factory(handler, mode):
        spawned.append(mode)
        return FakeListener()

    svc = HotkeyService(FakeApi(pending), widget=FakeWidget(),
                        permissions_check=lambda: granted["now"],
                        listener_factory=factory, poll_interval=0.01)
    return svc, prompts, spawned


def _wait_for(check, seconds=2.0):
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return check()


def test_first_run_holds_the_prompt_and_the_tap(monkeypatch):
    svc, prompts, spawned = _service(monkeypatch, True, {"now": False})
    svc.start()
    try:
        assert prompts == []
        assert spawned == []
        assert svc.health == "waiting"
    finally:
        svc.stop()


def test_the_grant_arriving_starts_the_tap_without_a_restart(monkeypatch):
    granted = {"now": False}
    svc, prompts, spawned = _service(monkeypatch, True, granted)
    svc.start()
    try:
        granted["now"] = True
        assert _wait_for(lambda: spawned == ["active"] and svc.health == "active")
        assert prompts == []
    finally:
        svc.stop()


def test_a_grant_already_on_starts_straight_away(monkeypatch):
    svc, prompts, spawned = _service(monkeypatch, True, {"now": True})
    svc.start()
    try:
        assert spawned == ["active"]
        assert prompts == []
    finally:
        svc.stop()


@pytest.mark.parametrize("pending", [False, RuntimeError("no")])
def test_otherwise_it_asks_at_launch_as_before(monkeypatch, pending):
    svc, prompts, spawned = _service(monkeypatch, pending, {"now": False})
    svc.start()
    try:
        assert prompts == ["active"]
        assert spawned == ["active"]
    finally:
        svc.stop()
