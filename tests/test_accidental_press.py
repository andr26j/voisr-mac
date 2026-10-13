"""H1 (2026-10-12 audit): a brief or accidental hotkey press must not paste
whisper's non-speech annotations such as `[BLANK_AUDIO]`."""
import array
import wave

import pytest

from voisr.cleanup import clean
from voisr.history import HistoryStore
from voisr.ui import Api, HotkeyService

RIGHT_OPTION_VK, RIGHT_OPTION_MASK = 61, 0x40
CAPS_LOCK_VK = 57
E_VK = 14


@pytest.mark.parametrize("raw", [
    "[BLANK_AUDIO]",
    " [BLANK_AUDIO] ",
    "[MUSIC]",
    "[ Silence ]",
    "[inaudible]",
    "(silence)",
    "(wind blowing)",
    "[BLANK_AUDIO] [BLANK_AUDIO]",
])
def test_non_speech_annotations_clean_to_nothing(raw):
    assert clean(raw, {}) == ""


def test_non_speech_annotations_are_dropped_around_real_words():
    assert clean("[MUSIC] hello there [BLANK_AUDIO]", {}) == "Hello there"


def test_parentheses_the_user_dictated_are_kept():
    assert clean("call me (maybe) later", {}) == "Call me (maybe) later"


def _write_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(array.array("h", samples).tobytes())


def _noisy_recorder(seconds):
    class NoisyRecorder:
        def start(self, path):
            self.path = path

        def stop(self):
            # peak 1200: well above the silence gate, like a key click
            _write_wav(self.path, [0, 900, -800, 1200] * int(4000 * seconds))
            return ""

        def cancel(self):
            self.cancelled = True

    return NoisyRecorder


def make_api(tmp_path, transcript="[BLANK_AUDIO]", seconds=1.0):
    calls = {"transcribe": 0, "paste": [], "clipboard": []}

    def transcribe(wav):
        calls["transcribe"] += 1
        return transcript

    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder_factory=_noisy_recorder(seconds),
              transcribe_fn=transcribe,
              clock=lambda: 1.0,
              paste_fn=lambda text: calls["paste"].append(text) or True,
              clipboard_fn=lambda text: calls["clipboard"].append(text))
    return api, calls


def test_blank_audio_transcript_is_neither_pasted_nor_kept(tmp_path):
    api, calls = make_api(tmp_path)
    api.start_recording()
    res = api.stop_recording(paste=True)
    assert "error" in res
    assert calls["paste"] == [] and calls["clipboard"] == []
    assert api.get_history() == []


def test_a_too_short_recording_is_not_transcribed(tmp_path):
    api, calls = make_api(tmp_path, transcript="you", seconds=0.15)
    api.start_recording()
    res = api.stop_recording(paste=True)
    assert "error" in res and "short" in res["error"]
    assert calls["transcribe"] == 0
    assert calls["paste"] == [] and api.get_history() == []


def test_a_recording_just_over_the_minimum_is_transcribed(tmp_path):
    api, calls = make_api(tmp_path, transcript="yes", seconds=0.4)
    api.start_recording()
    res = api.stop_recording(paste=True)
    assert res["text"] == "Yes"
    assert calls["transcribe"] == 1


class FakeWidget:
    def __init__(self):
        self.calls = []

    def show_recording(self, theme="classic", color=None):
        self.calls.append("recording")

    def show_processing(self):
        self.calls.append("processing")

    def show_done(self):
        self.calls.append("done")

    def show_error(self):
        self.calls.append("error")

    def hide(self):
        self.calls.append("hide")


def make_service(tmp_path, target_vk):
    api, calls = make_api(tmp_path, transcript="real words")
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0,
                        listener_factory=lambda handler, mode="listen": handler)
    # No worker: ops run inline, so the handler's effects are synchronous.
    svc._target_vk = target_vk
    svc._spawn_listener()
    return svc, svc._listener, api, calls, widget


def test_typing_while_the_hotkey_is_held_cancels_the_dictation(tmp_path):
    # ⌥e (é) with the default Right Option hotkey: the option press starts a
    # recording, and the e that follows proves it was a chord, not speech.
    svc, handler, api, calls, widget = make_service(tmp_path, RIGHT_OPTION_VK)
    handler(RIGHT_OPTION_VK, "flags", RIGHT_OPTION_MASK)
    assert api.is_recording()
    handler(E_VK, "down", RIGHT_OPTION_MASK)
    assert not api.is_recording()
    assert widget.calls == ["recording", "hide"]
    handler(RIGHT_OPTION_VK, "flags", 0)   # the release is now a no-op
    assert calls["transcribe"] == 0
    assert calls["paste"] == [] and api.get_history() == []
    assert widget.calls == ["recording", "hide"]


def test_a_cancelled_chord_leaves_the_next_dictation_working(tmp_path):
    import time
    svc, handler, api, calls, widget = make_service(tmp_path, RIGHT_OPTION_VK)
    handler(RIGHT_OPTION_VK, "flags", RIGHT_OPTION_MASK)
    handler(E_VK, "down", RIGHT_OPTION_MASK)
    handler(RIGHT_OPTION_VK, "flags", 0)
    handler(RIGHT_OPTION_VK, "flags", RIGHT_OPTION_MASK)
    assert api.is_recording()
    handler(RIGHT_OPTION_VK, "flags", 0)
    deadline = time.monotonic() + 3
    while not calls["paste"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert calls["paste"] == ["Real words"]


def test_typing_without_the_hotkey_held_changes_nothing(tmp_path):
    svc, handler, api, calls, widget = make_service(tmp_path, RIGHT_OPTION_VK)
    handler(E_VK, "down", 0)
    assert not api.is_recording() and widget.calls == []


def test_typing_during_a_latched_caps_lock_dictation_does_not_cancel_it(tmp_path):
    # Caps Lock toggles: the key is not held, so other keys are not chords.
    svc, handler, api, calls, widget = make_service(tmp_path, CAPS_LOCK_VK)
    handler(CAPS_LOCK_VK, "flags", 0x10000)
    assert api.is_recording()
    handler(E_VK, "down", 0)
    assert api.is_recording()
    svc.stop()
    api.close()


def test_cancel_recording_discards_the_capture(tmp_path):
    api, calls = make_api(tmp_path, transcript="real words")
    api.start_recording()
    api._cancel_recording()
    assert not api.is_recording()
    assert "error" in api.stop_recording()     # nothing left to stop
    assert api.start_recording()["recording"] is True
    assert api.stop_recording()["text"] == "Real words"
    assert calls["transcribe"] == 1
