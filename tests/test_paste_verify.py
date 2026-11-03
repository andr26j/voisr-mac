"""Component C: every verdict, and what each one does to the clipboard."""
import inspect

import pytest

from tests.fake_ax import FakeAX, textbox
from voisr import inject
from voisr.ax_context import FocusContext
from voisr.paste_verify import Verdict, check_delivery, skipped, verify_paste
from voisr.pasteboard import PasteboardItemSnapshot, PasteboardSnapshot


@pytest.mark.parametrize("before, after, text, old, status, reason", [
    # verified
    ("start ", "start hello there", "hello there", None, "verified", None),
    ("", "hello there", "hello there", None, "verified", None),
    ("hello there", "hello there hello there", "hello there", None, "verified", None),
    ("a", "a hello\n  there", "hello there", None, "verified", None),   # wrapped
    ("a", "a it’s “fine”", "it's \"fine\"", None, "verified", None),     # smart quotes
    # stale clipboard
    ("start ", "start OLD COPY", "hello", "OLD COPY", "stale_clipboard", None),
    # a truly missed paste: the field clearly didn't change
    ("start ", "start ", "hello", None, "not_found", None),
    ("start", "start  \n", "hello", "OLD", "not_found", None),
    ("hello", "hello", "hello", None, "not_found", None),       # already there
    # the app changed the paste: not a miss
    ("a ", "a Hello There", "hello there", None, "skipped", "transformed"),   # case
    ("a ", "a hello there", "Hello There", None, "skipped", "transformed"),
    ("a ", "a helloworld", "hello\nworld", None, "skipped", "transformed"),   # newline
    ("a ", "a h e l l o", "hello", None, "skipped", "transformed"),           # spacing
    ("a ", "a * call Sam", "call Sam", None, "verified", None),
    ("a ", "a 1. Buy some milk!", "buy milk", None, "skipped", "transformed"), # grew by about
    ("a ", "a colour of it", "color of it", None, "skipped", "transformed"),  # autocorrect
    # changed, but not by anything like the paste: still not a provable miss
    ("start ", "start x", "hello there friend", "OLD", "skipped", "field_changed"),
    ("long text here ", "long", "hello", None, "skipped", "field_changed"),
    ("a ", "a " + "y" * 90, "hello", None, "skipped", "field_changed"),
    # an old clipboard too short to mean anything
    ("start ", "start ab", "hello", "ab", "skipped", "field_changed"),
    # unreadable is not empty
    (None, "x", "hello", None, "skipped", "unreadable"),
    ("x", None, "hello", None, "skipped", "unreadable"),
    # empty proves nothing
    ("", "", "hello", None, "skipped", "empty_field"),
    ("sent message", "", "hello", None, "skipped", "empty_field"),
    ("x", "   \n", "hello", None, "skipped", "empty_field"),
    # too long
    ("x" * 15_001, "x", "hello", None, "skipped", "too_long"),
    ("x", "x" * 15_001, "hello", None, "skipped", "too_long"),
])
def test_verify_paste(before, after, text, old, status, reason):
    assert verify_paste(before, after, text, old) == Verdict(status, reason)


def ctx(value="", element_id="e1", **kwargs):
    fields = dict(readable=value is not None, value=value, element_id=element_id)
    return FocusContext(**{**fields, **kwargs})


@pytest.mark.parametrize("before, after, expected", [
    (None, ctx("x"), skipped("no_baseline")),
    (ctx("x"), None, skipped("timeout")),
    (ctx("x", secure=True), ctx("x hi"), skipped("secure")),
    (ctx("x"), ctx(None, secure=True, reason="secure"), skipped("secure")),
    (ctx("x"), ctx(None, reason="no_focused_element"), skipped("no_focused_element")),
    (ctx("x"), ctx(None, reason="not_editable"), skipped("not_editable")),
    (ctx("x"), ctx(None, reason="too_long"), skipped("too_long")),
    (ctx("x"), ctx(None, reason="timeout"), skipped("timeout")),
    (ctx("x"), ctx(None, reason="unreadable"), skipped("unreadable")),
    (ctx(None, reason="unreadable"), ctx("x"), skipped("unreadable")),
    (ctx(None, reason="too_long"), ctx("x"), skipped("too_long")),
    (ctx("x"), ctx("x hi", element_id="e2"), skipped("focus_changed")),
    (ctx("x"), ctx("x hi"), Verdict("verified")),
    (ctx("x"), ctx("x"), Verdict("not_found")),
    (ctx("x"), ctx("x", trust_absence=False), skipped("unverifiable_app")),
    (ctx("x"), ctx("x OLD", trust_absence=False), skipped("unverifiable_app")),
    (ctx("x"), ctx("x hi", trust_absence=False), Verdict("verified")),
])
def test_check_delivery(before, after, expected):
    assert check_delivery(before, after, "hi", "OLD") == expected


@pytest.mark.parametrize("bundle_id, electron, trusted", [
    ("com.apple.TextEdit", False, True),
    ("com.tinyspeck.slackmacgap", True, False),
    ("com.microsoft.VSCode", True, False),
    ("com.apple.Terminal", False, False),
    ("com.googlecode.iterm2", False, False),
    ("com.google.Chrome", False, False),
    ("com.apple.Safari", False, False),
])
def test_where_a_missing_paste_means_something(bundle_id, electron, trusted):
    from voisr.ax_context import capture_focus
    focus = capture_focus(FakeAX(textbox("x"), bundle_id=bundle_id, electron=electron))
    assert focus.trust_absence is trusted


class Board:
    def __init__(self, concealed=False):
        payloads = [("public.utf8-plain-text", b"OLD COPY")]
        if concealed:
            payloads.append(("org.nspasteboard.ConcealedType", b""))
        self.snap = PasteboardSnapshot(1, (PasteboardItemSnapshot(tuple(payloads)),))
        self.written = []
        self.restored = 0

    def snapshot(self):
        return self.snap

    def write_text_if_unchanged(self, text, snapshot):
        self.written.append(text)
        return 2

    def is_owned(self, count):
        return True

    def restore_if_unchanged(self, snapshot, count):
        self.restored += 1


def paste(board, verifier, events=None):
    events = [] if events is None else events
    cmd_v = []

    def post():
        cmd_v.append(1)
        events.append("cmd-v")
        return True

    result = inject._paste_with_adapter(
        "hello", adapter=board, post_cmd_v=post,
        applescript_cmd_v=lambda: False,
        sleeper=lambda s: events.append(f"sleep {s}"), verifier=verifier)
    return result, cmd_v


def test_a_miss_skips_the_restore_and_never_sends_cmd_v_again():
    board = Board()
    seen = []
    result, cmd_v = paste(board, lambda old: seen.append(old) or True)
    assert result is True
    assert len(cmd_v) == 1, "must never re-send Cmd-V"
    assert board.restored == 0, "the dictation stays on the clipboard"
    assert seen == ["OLD COPY"]


def test_a_landed_or_skipped_paste_restores_as_today():
    board = Board()
    _, cmd_v = paste(board, lambda old: False)
    assert board.restored == 1 and len(cmd_v) == 1


def test_a_failing_verifier_restores_as_today():
    def broken(old):
        raise RuntimeError("private text")

    board = Board()
    result, cmd_v = paste(board, broken)
    assert result is True and board.restored == 1 and len(cmd_v) == 1


def test_the_read_comes_after_the_grace_and_before_the_restore():
    events = []

    class Traced(Board):
        def restore_if_unchanged(self, snapshot, count):
            events.append("restore")

    paste(Traced(), lambda old: events.append("verify") or False, events)
    assert events == ["cmd-v", f"sleep {inject.PASTE_GRACE_S}", "verify", "restore"]


def test_a_concealed_clipboard_is_not_verified_or_handed_over():
    calls = []
    board = Board(concealed=True)
    result, _ = paste(board, lambda old: calls.append(old) or True)
    assert result is True and calls == [] and board.restored == 0


def test_a_blocked_paste_never_verifies():
    calls = []
    board = Board()
    with pytest.raises(Exception):
        inject._paste_with_adapter(
            "hello", adapter=board, post_cmd_v=lambda: False,
            applescript_cmd_v=lambda: False, sleeper=lambda s: None,
            verifier=lambda old: calls.append(old))
    assert calls == []


def test_paste_at_cursor_takes_a_verifier():
    assert "verifier" in inspect.signature(inject.paste_at_cursor).parameters


def test_old_clipboard_text_inside_a_reformatted_paste_is_not_a_miss():
    text = "Dear team,\nplease review the diff.\nThanks"
    landed = "Dear team, Please review the diff. Thanks!"
    verdict = verify_paste("", landed, text, old_clipboard="review")
    assert not verdict.missed


def test_a_real_stale_clipboard_is_still_caught():
    verdict = verify_paste("", "OLD COPY", "my saved text", old_clipboard="OLD COPY")
    assert verdict.status == "stale_clipboard"
