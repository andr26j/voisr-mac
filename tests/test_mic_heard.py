"""When recording is what brought up macOS's mic prompt, AVCaptureDevice keeps
answering "not determined" until the app relaunches. Real audio settles it."""
import random

from test_snippets_guard import dictate, guard_api, noise, spoken
from voisr import permissions


def mic_probe(monkeypatch, answer):
    monkeypatch.setattr(permissions, "DEFAULT_PROBES", {
        "microphone": lambda: answer, "input_monitoring": lambda: True,
        "accessibility": lambda: True})


def mic(api):
    return {p["key"]: p for p in api.get_permissions()["panes"]}["microphone"]["granted"]


def test_a_mic_macos_says_it_never_asked_about_is_granted_once_it_has_been_heard(tmp_path, monkeypatch):
    mic_probe(monkeypatch, None)
    api, _ = guard_api(tmp_path, spoken())
    assert mic(api) is None, "not asked yet, so Home still says macOS will ask"
    dictate(api)
    assert mic(api) is True
    api.close()


def test_a_clip_too_quiet_to_be_speech_still_proves_the_mic_works(tmp_path, monkeypatch):
    mic_probe(monkeypatch, None)
    api, _ = guard_api(tmp_path, noise(2.0, random.Random(1)))
    out = dictate(api)
    assert "didn't catch that" in out["error"]
    assert mic(api) is True
    api.close()


def test_a_silent_mic_proves_nothing(tmp_path, monkeypatch):
    # zeros are what a mic without the grant sends
    mic_probe(monkeypatch, None)
    api, _ = guard_api(tmp_path, [0.0] * 32000)
    dictate(api)
    assert mic(api) is None
    api.close()


def test_a_known_refusal_is_never_overruled(tmp_path, monkeypatch):
    mic_probe(monkeypatch, None)
    api, _ = guard_api(tmp_path, spoken())
    dictate(api)
    mic_probe(monkeypatch, False)
    assert mic(api) is False
    api.close()


def test_a_fresh_launch_starts_from_what_macos_says(tmp_path, monkeypatch):
    mic_probe(monkeypatch, None)
    api, _ = guard_api(tmp_path, spoken())
    assert api._mic_heard is False
    api.close()


def test_on_the_fallback_it_holds_too(tmp_path, monkeypatch):
    mic_probe(monkeypatch, None)
    api, _ = guard_api(tmp_path, spoken())

    class Svc:
        running = True
        on_fallback = True
        health = "active"
        status = "active on Right Option"
        holding = False

        def stop(self):
            pass

    dictate(api)
    api.attach_hotkey_service(Svc())
    assert api.get_permissions()["fallback"] is True
    assert mic(api) is True
    api.close()
