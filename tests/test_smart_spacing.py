import json
from types import SimpleNamespace

import pytest

from voisr.ax_context import FocusContext
from voisr.history import HistoryStore
from voisr.snippets import dictation_wants_space
from voisr.transcriber import resolve_model
from voisr.ui import Api, install_click_watch


class FakeRecorder:
    def start(self, path):
        self.path = path

    def stop(self):
        with open(self.path, "wb") as f:
            f.write(b"\0" * 2000)
        return ""


def make_api(tmp_path, texts, paste_log):
    it = iter(texts)
    (tmp_path / "config.json").write_text(json.dumps({"paste": True}))

    def fake_paste(text, restore_clipboard=True):
        paste_log.append(text)
        return True

    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "h.jsonl"),
               recorder_factory=FakeRecorder,
               transcribe_fn=lambda wav: next(it),
               clock=lambda: 1.0,
               paste_fn=fake_paste,
               clipboard_fn=lambda text: None)


def dictate(api):
    api.start_recording()
    return api.stop_recording(paste=True)


def test_consecutive_dictations_get_joining_space(tmp_path):
    log = []
    api = make_api(tmp_path, ["Hello world.", "it will not apply the space"], log)
    dictate(api)
    dictate(api)
    assert log == ["Hello world.", " It will not apply the space"]


def test_typing_in_between_suppresses_the_space(tmp_path):
    log = []
    api = make_api(tmp_path, ["Hello world.", "second thought"], log)
    dictate(api)
    api.note_user_typing()          # user typed something manually
    dictate(api)
    assert log == ["Hello world.", "Second thought"]


def test_first_dictation_never_gets_a_space(tmp_path):
    log = []
    api = make_api(tmp_path, ["Only one."], log)
    dictate(api)
    assert log == ["Only one."]


def test_history_and_clipboard_store_text_without_lead_space(tmp_path):
    log = []
    api = make_api(tmp_path, ["First.", "second"], log)
    dictate(api)
    out = dictate(api)
    assert out["text"] == "Second"                    # api result unpadded
    assert api.get_history()[0]["text"] == "Second"   # history unpadded


def test_resolve_model_prefers_configured_then_small_then_base(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "ggml-base.en.bin").write_bytes(b"x")
    assert resolve_model(models_dir=models).name == "ggml-base.en.bin"
    (models / "ggml-small.en.bin").write_bytes(b"x")
    assert resolve_model(models_dir=models).name == "ggml-small.en.bin"
    (models / "ggml-medium.en.bin").write_bytes(b"x")
    assert resolve_model("medium.en", models_dir=models).name == "ggml-medium.en.bin"


# ---- going by the field, and what happens where it can't be read

def focus(before="", **fields):
    return FocusContext(**{"bundle_id": "com.example.app", "readable": True, "caret_known": True,
                           "before_text": before, **fields})


UNREADABLE = {"readable": False, "caret_known": False, "reason": "no_focused_element"}


@pytest.mark.parametrize("before,wanted", [
    ("", False),                  # a new or empty field, the reported bug
    ("Line one\n", False),
    ("Already spaced ", False),
    ("Hello world.", True),
    ("Hello", True),
])
def test_a_readable_field_decides_whatever_the_timing(before, wanted):
    for recent in (True, False):
        assert dictation_wants_space(focus(before), "Next", recent, "com.example.app") is wanted


def test_an_unreadable_field_joins_only_a_recent_dictation_in_the_same_app():
    field = focus(**UNREADABLE)
    assert dictation_wants_space(field, "Next", True, "com.example.app") is True
    assert dictation_wants_space(field, "Next", False, "com.example.app") is False
    assert dictation_wants_space(field, "Next", True, "com.example.other") is False


def test_an_unknown_app_or_no_read_falls_back_on_recent():
    assert dictation_wants_space(None, "Next", True, "com.example.app") is True
    assert dictation_wants_space(None, "Next", False, None) is False
    assert dictation_wants_space(focus(**UNREADABLE), "Next", True, None) is True


def test_a_secure_field_never_gets_one():
    assert dictation_wants_space(focus(secure=True), "Next", True, "com.example.app") is False


def test_an_empty_read_that_cant_be_trusted_falls_back_on_recent():
    # browsers, Electron and terminals can report a field with text in it as empty
    shaky = focus("", trust_absence=False)
    assert dictation_wants_space(shaky, "Next", True, "com.example.app") is True
    assert dictation_wants_space(shaky, "Next", False, "com.example.app") is False
    assert dictation_wants_space(focus("Hello", trust_absence=False), "Next", False, None) is True


def test_a_click_counts_like_typing(tmp_path):
    log = []
    api = make_api(tmp_path, ["Hello world.", "second thought"], log)
    clicks = []

    class FakeEvent:
        @staticmethod
        def addGlobalMonitorForEventsMatchingMask_handler_(mask, handler):
            clicks.append(handler)
            return "monitor"

    appkit = SimpleNamespace(NSEvent=FakeEvent, NSEventMaskLeftMouseDown=1,
                             NSEventMaskRightMouseDown=2, NSEventMaskOtherMouseDown=4)
    assert install_click_watch(api.note_user_typing, appkit=appkit) == "monitor"
    dictate(api)
    clicks[0](None)
    dictate(api)
    assert log == ["Hello world.", "Second thought"]


def test_a_click_watch_that_fails_never_raises():
    assert install_click_watch(lambda: None, appkit=SimpleNamespace()) is None
    handlers = []

    class FakeEvent:
        @staticmethod
        def addGlobalMonitorForEventsMatchingMask_handler_(mask, handler):
            handlers.append(handler)

    def boom():
        raise RuntimeError

    install_click_watch(boom, appkit=SimpleNamespace(
        NSEvent=FakeEvent, NSEventMaskLeftMouseDown=1, NSEventMaskRightMouseDown=2,
        NSEventMaskOtherMouseDown=4))
    handlers[0](None)  # must not raise into AppKit


def test_a_new_empty_field_gets_no_space_through_the_real_path(tmp_path):
    from tests.fake_ax import FakeAX, textbox
    from tests.test_context_api import make_api as ax_api
    from tests.test_snippets_api import SpeechRecorder
    ax = FakeAX(textbox(""))
    pasted = []
    said = iter(["Hello world.", "second thought", "third one"])
    api = ax_api(tmp_path, ax, transcribe_fn=lambda wav: next(said),
                 paste_fn=lambda text: pasted.append(text) or True)
    api._recordings._recorder_factory = SpeechRecorder

    def dictate_into(field):
        ax.focused = field
        api.start_recording()
        api.stop_recording(paste=True)

    dictate_into(textbox(""))
    dictate_into(textbox("Hello world."))  # follows on
    dictate_into(textbox(""))              # moments later, a new empty field
    assert pasted == ["Hello world.", " Second thought", "Third one"]
    api.close()


def test_inside_a_word_gets_no_space_but_before_a_space_does():
    assert dictation_wants_space(focus("hel", after_text="lo"), "Next", True, None) is False
    assert dictation_wants_space(focus("Hello", after_text=" there"), "Next", False, None) is True


def test_leading_punctuation_gets_no_space_even_where_the_field_is_unreadable():
    field = focus(**UNREADABLE)
    assert dictation_wants_space(field, ", and then", True, "com.example.app") is False
    assert dictation_wants_space(None, ". Next", True, None) is False
    assert dictation_wants_space(None, "", True, None) is False
