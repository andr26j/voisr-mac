"""H3 (2026-10-12 audit): a hand-edited config.json must never cost the user
their dictionary, and a bad value must never stop push-to-talk starting."""
import json

import pytest

from voisr.app import DEFAULT_CONFIG, load_config
from voisr.history import HistoryStore
from voisr.ui import Api, HotkeyService, start_hotkey_service

DICTIONARY = {"trademake": "TradeMate", "brunswik": "Brunswick"}


def make_api(tmp_path):
    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "h.jsonl"),
               transcribe_fn=lambda wav: "", clock=lambda: 1.0,
               clipboard_fn=lambda text: None)


def quarantined(tmp_path):
    return sorted(tmp_path.glob("config.json.corrupt-*"))


def test_a_syntax_error_does_not_wipe_the_dictionary_on_the_next_write(tmp_path):
    good = json.dumps({"dictionary": DICTIONARY, "paste": False}, indent=2)
    broken = good.replace('"Brunswick"', '"Brunswick",')   # trailing comma
    (tmp_path / "config.json").write_text(broken)
    api = make_api(tmp_path)
    api.set_config({"show_widget": False})
    saved = quarantined(tmp_path)
    assert len(saved) == 1 and saved[0].read_text() == broken
    assert json.loads((tmp_path / "config.json").read_text())["show_widget"] is False


def test_the_user_is_told_once_where_the_broken_file_went(tmp_path):
    (tmp_path / "config.json").write_text("{not json")
    api = make_api(tmp_path)
    api.get_config()
    notice = api.take_config_notice()["notice"]
    assert quarantined(tmp_path)[0].name in notice
    assert api.take_config_notice() == {}


def test_no_notice_for_a_healthy_config(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"paste": False}))
    api = make_api(tmp_path)
    assert api.get_config()["paste"] is False
    assert api.take_config_notice() == {} and quarantined(tmp_path) == []


@pytest.mark.parametrize("payload", [b"\xff\xfe{}", b"null", b"[1, 2]", b'"text"'])
def test_unreadable_or_non_object_config_falls_back_without_raising(tmp_path, payload):
    (tmp_path / "config.json").write_bytes(payload)
    cfg = load_config(tmp_path / "config.json")
    assert cfg == DEFAULT_CONFIG
    assert quarantined(tmp_path)[0].read_bytes() == payload


def test_wrongly_typed_values_fall_back_to_defaults_and_keep_the_rest(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "hotkey_vk": "F13", "paste": "yes", "pill_color": 7,
        "dictionary": {"ok": "OK", "bad": 5, "": "ZxZ"},
        "show_widget": False,
    }))
    cfg = load_config(tmp_path / "config.json")
    assert "hotkey_vk" not in cfg
    assert cfg["paste"] is True and cfg["pill_color"] is None
    assert cfg["dictionary"] == {"ok": "OK"}
    assert cfg["show_widget"] is False
    assert quarantined(tmp_path) == []   # salvaged, not quarantined


def test_a_string_hotkey_vk_does_not_stop_the_listener_starting(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"hotkey_vk": "F13"}))
    svc = HotkeyService(make_api(tmp_path), permissions_check=lambda: True,
                        listener_factory=lambda handler, mode="listen": handler)
    svc.start()
    assert svc.status.startswith("active on")
    svc.stop()


@pytest.mark.parametrize("updates", [
    {"dictionary": {"x": 5}},
    {"dictionary": ["x"]},
    {"dictionary": {"": "empty"}},
    {"paste": "yes"},
    {"hotkey_vk": "F13"},
    {"hotkey_vk": True},
    {"pill_color": 7},
    {"no_such_setting": 1},
])
def test_set_config_rejects_bad_updates_without_writing(tmp_path, updates):
    api = make_api(tmp_path)
    api.set_config({"dictionary": DICTIONARY})
    before = (tmp_path / "config.json").read_text()
    res = api.set_config(updates)
    assert "error" in res
    assert (tmp_path / "config.json").read_text() == before


@pytest.mark.parametrize("updates", [
    {"pill_color": None}, {"pill_color": "#ff8800"}, {"model": None},
    {"model": "base.en"}, {"dictionary": {}}, {"hotkey": "f13"},
    {"appearance": "dark"}, {"launch_at_login": False},
])
def test_set_config_accepts_every_shape_the_dashboard_sends(tmp_path, updates):
    api = make_api(tmp_path)
    assert "error" not in api.set_config(updates)


def test_start_hotkey_service_survives_a_failing_start():
    class Exploding:
        def start(self):
            raise ValueError("invalid literal for int() with base 10: 'F13'")

    assert "F13" in start_hotkey_service(Exploding())


def test_start_hotkey_service_reports_nothing_on_success():
    class Fine:
        def start(self):
            pass

    assert start_hotkey_service(Fine()) is None
