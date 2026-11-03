"""Component A: what may be read from the focused field, and what must not."""
import os
import threading
import time
from pathlib import Path

import pytest

from tests.fake_ax import FakeAX, FakeElement, textbox
from voisr.ax_context import (DENYLIST, MAX_VALUE_CHARS, UNREADABLE, AXWorker,
                              FocusContext, FocusTracker, _split_at_utf16,
                              capture_focus, denied)

ROOT = Path(__file__).resolve().parent.parent
TEXT_ATTRS = {"AXValue", "AXSelectedText", "AXSelectedTextRange", "AXTitle"}


def test_splits_at_the_caret_and_reads_the_window():
    label = FakeElement(AXRole="AXStaticText", AXValue="Priya Raman")
    window = FakeElement(AXRole="AXWindow", AXTitle="Inbox",
                         AXChildren=[FakeElement(AXRole="AXGroup",
                                                 AXChildren=[label])])
    box = textbox("hello there world", caret=6, selection=5, AXWindow=window)
    focus = capture_focus(FakeAX(box))
    assert focus.readable and focus.caret_known and not focus.secure
    assert (focus.before_text, focus.selected_text, focus.after_text) == (
        "hello ", "there", " world")
    assert focus.window_title == "Inbox"
    assert focus.nearby_texts == ("Priya Raman",)
    assert focus.value == "hello there world"
    assert focus.element_id == box.id


def test_excerpts_stop_at_500_on_a_word_boundary():
    before = "word " * 300
    box = textbox(before + "after " * 300, caret=len(before))
    focus = capture_focus(FakeAX(box))
    assert len(focus.before_text) <= 500
    assert focus.before_text.startswith("word")
    assert len(focus.after_text) <= 500
    assert focus.after_text.endswith("after")


@pytest.mark.parametrize("value, unit_caret, expected_before", [
    ("abc def", 3, "abc"),
    ("😀 hi there", 3, "😀 "),                # the emoji is two UTF-16 units
    ("a😀b😀c", 6, "a😀b😀"),
])
def test_caret_offsets_are_utf16_units(value, unit_caret, expected_before):
    assert _split_at_utf16(value, unit_caret, 0)[0] == expected_before


def _assert_read_no_text(ax):
    assert not TEXT_ATTRS & {a for _, a in ax.calls}, ax.calls


@pytest.mark.parametrize("attrs", [
    {"AXRole": "AXTextField", "AXSubrole": "AXSecureTextField"},
    {"AXRole": "AXSecureTextField"},
])
def test_a_secure_field_reads_nothing(attrs):
    # AXValue is set so a read would really return it
    box = FakeElement(AXValue="hunter2", AXSelectedText="hunter2",
                      AXSelectedTextRange=(0, 7), AXNumberOfCharacters=7,
                      AXWindow=FakeElement(AXRole="AXWindow", AXTitle="Log in"),
                      **attrs)
    ax = FakeAX(box)
    focus = capture_focus(ax)
    assert focus.secure and not focus.readable
    assert focus.value is None and focus.before_text == focus.after_text == ""
    assert focus.window_title == "" and focus.nearby_texts == ()
    _assert_read_no_text(ax)
    assert "hunter2" not in repr(focus)


def test_a_role_that_cannot_be_read_means_no_text_is_asked_for():
    box = FakeElement(AXValue="maybe a password")
    ax = FakeAX(box)
    focus = capture_focus(ax)
    assert not focus.readable and focus.reason == "unreadable"
    _assert_read_no_text(ax)


@pytest.mark.parametrize("bundle_id", [
    "com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop",
    "com.lastpass.LastPass", "com.dashlane.dashlanephonefinal",
    "org.keepassxc.keepassxc", "com.apple.keychainaccess", "com.apple.Passwords",
    "com.apple.systempreferences", "com.apple.SecurityAgent",
])
def test_the_denylist_makes_no_ax_call_at_all(bundle_id):
    ax = FakeAX(textbox("secret"), bundle_id=bundle_id)
    focus = capture_focus(ax)
    assert focus.secure and focus.value is None
    assert ax.calls == []


def test_a_password_managers_panel_is_denied_when_another_app_is_frontmost():
    # quick-access panels take focus without becoming the frontmost app
    box = textbox("secret item")
    ax = FakeAX(box, bundle_id="com.apple.Safari")
    ax.owner_bundle_id = "com.1password.1password"
    focus = capture_focus(ax)
    assert focus.secure and focus.value is None
    assert ("AXValue" not in {a for _, a in ax.calls})


def test_the_denylist_matches_whole_ids_and_families_only():
    assert denied("com.agilebits.onepassword-osx-helper")
    assert not denied("com.apple.keychainaccess.evil-lookalike")
    assert not denied(None)
    assert any(entry.endswith(".") for entry in DENYLIST)


@pytest.mark.parametrize("attrs, reason", [
    ({"AXRole": "AXTextArea"}, "unreadable"),                # no AXValue
    ({"AXRole": "AXTextArea", "AXValue": 3}, "unreadable"),  # not text
    ({"AXRole": "AXButton", "AXValue": "OK"}, "not_editable"),
])
def test_an_unreadable_field_is_unreadable_not_empty(attrs, reason):
    focus = capture_focus(FakeAX(FakeElement(**attrs)))
    assert focus.readable is False
    assert focus.value is None, "must not look like an empty field"
    assert focus.reason == reason


def test_a_truly_empty_field_is_readable_and_empty():
    focus = capture_focus(FakeAX(textbox("")))
    assert focus.readable is True and focus.value == ""


def test_no_focused_element():
    focus = capture_focus(FakeAX(None))
    assert focus.reason == "no_focused_element" and not focus.readable


def test_too_long_keeps_only_the_selection_and_does_not_fetch_the_value():
    box = textbox("x" * 10, AXSelectedText="pick")
    box.attrs["AXNumberOfCharacters"] = MAX_VALUE_CHARS + 1
    ax = FakeAX(box)
    focus = capture_focus(ax)
    assert focus.reason == "too_long" and focus.value is None
    assert focus.selected_text == "pick"
    assert "AXValue" not in {a for _, a in ax.calls}


def test_voisrs_own_process_is_not_read():
    ax = FakeAX(textbox("mine"), pid=os.getpid())
    focus = capture_focus(ax)
    assert focus.reason == "self"
    assert ax.calls == []


def test_capture_never_raises():
    class Broken(FakeAX):
        def focused_element(self, timeout_s):
            raise RuntimeError("private text in an error")

    focus = capture_focus(Broken())
    assert focus.readable is False and focus.reason == "unreadable"
    assert "private" not in repr(focus)


def test_repr_never_shows_text():
    window = FakeElement(AXRole="AXWindow", AXTitle="Secret title",
                         AXChildren=[FakeElement(AXRole="AXStaticText",
                                                 AXValue="Secret label")])
    box = textbox("Secret body", caret=6, AXWindow=window)
    assert "Secret" not in repr(capture_focus(FakeAX(box)))


def test_the_walk_is_bounded_and_skips_secure_subtrees():
    secret = FakeElement(AXRole="AXStaticText", AXValue="in a secure subtree")
    secure = FakeElement(AXRole="AXGroup", AXSubrole="AXSecureTextField",
                         AXChildren=[secret])
    field = FakeElement(AXRole="AXTextField", AXValue="another field's text")
    labels = [FakeElement(AXRole="AXStaticText", AXValue=f"label {i}")
              for i in range(50)]
    window = FakeElement(AXRole="AXWindow", AXChildren=[secure, field, *labels])
    focus = capture_focus(FakeAX(textbox("x", AXWindow=window)))
    assert len(focus.nearby_texts) == 10
    assert "in a secure subtree" not in focus.nearby_texts
    assert "another field's text" not in focus.nearby_texts

    wide = FakeElement(AXRole="AXWindow", AXChildren=[
        FakeElement(AXRole="AXGroup") for _ in range(1000)])
    ax = FakeAX(textbox("x", AXWindow=wide))
    capture_focus(ax, budget_s=5.0)
    assert sum(1 for _, a in ax.calls if a == "AXRole") <= 201  # 200 + the field


def test_the_budget_ends_the_walk():
    now = [0.0]

    class Slow(FakeAX):
        def attribute(self, element, name, timeout_s):
            now[0] += 0.02
            return super().attribute(element, name, timeout_s)

    window = FakeElement(AXRole="AXWindow", AXChildren=[
        FakeElement(AXRole="AXStaticText", AXValue=f"t{i}") for i in range(100)])
    ax = Slow(textbox("x", AXWindow=window))
    capture_focus(ax, budget_s=0.15, clock=lambda: now[0])
    assert now[0] < 0.3


def test_the_adapter_has_no_way_to_change_another_app():
    source = (ROOT / "voisr" / "ax_context.py").read_text()
    assert "AXUIElementSetAttributeValue" not in source
    assert "AXUIElementPerformAction" not in source
    code = "\n".join(p.read_text() for p in (ROOT / "voisr").glob("*.py"))
    assert "AXManualAccessibility" not in code


def test_worker_runs_jobs_and_reports_errors():
    worker = AXWorker()
    assert worker.submit(lambda: 5).result(timeout=2) == 5
    with pytest.raises(ValueError):
        worker.submit(lambda: (_ for _ in ()).throw(ValueError())).result(timeout=2)
    worker.close()


def test_a_wedged_call_is_abandoned_and_refused_when_it_keeps_happening():
    release = threading.Event()
    now = [0.0]
    worker = AXWorker(clock=lambda: now[0])
    started = threading.Event()

    def wedge():
        started.set()
        release.wait(5)

    worker.submit(wedge)
    assert started.wait(2)
    now[0] += worker.WEDGED_S + 1
    # a new thread takes over: later work still runs
    assert worker.submit(lambda: "fine").result(timeout=2) == "fine"
    started.clear()
    worker.submit(wedge)
    assert started.wait(2)
    now[0] += worker.WEDGED_S + 1
    refused = worker.submit(lambda: "no")
    with pytest.raises(Exception):
        refused.result(timeout=2)
    release.set()
    time.sleep(0.05)
    assert worker.submit(lambda: "back").result(timeout=2) == "back"
    worker.close()


def test_submit_never_blocks_on_a_hung_call():
    release = threading.Event()
    worker = AXWorker()
    worker.submit(lambda: release.wait(5))
    started = time.monotonic()
    for _ in range(5):
        worker.submit(lambda: None)
    assert time.monotonic() - started < 0.1
    release.set()
    worker.close()


def test_tracker_does_nothing_when_turned_off():
    ax = FakeAX(textbox("text"))
    tracker = FocusTracker(ax, enabled=lambda: False)
    tracker.capture_press(1)
    state = tracker.session(1)
    assert state.press.result(timeout=2) is None
    assert ax.calls == [] and ax.front_calls == 0
    tracker.close()


def test_tracker_read_now_is_bounded_against_a_hung_app():
    release = threading.Event()

    class Hung(FakeAX):
        def focused_element(self, timeout_s):
            release.wait(5)  # as if the messaging timeout were ignored
            return UNREADABLE

    tracker = FocusTracker(Hung(), enabled=lambda: True)
    started = time.monotonic()
    assert tracker.read_now(0.05) is None
    assert time.monotonic() - started < 0.5
    release.set()
    tracker.close()


def test_tracker_forgets_a_finished_dictation():
    tracker = FocusTracker(FakeAX(textbox("text")), enabled=lambda: True)
    tracker.capture_press(7)
    assert tracker.session(7) is not None
    tracker.forget(7)
    assert tracker.session(7) is None
    tracker.close()


def test_focuscontext_defaults_to_unreadable():
    focus = FocusContext()
    assert not focus.readable and focus.value is None
