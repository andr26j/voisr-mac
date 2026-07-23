import os

import pytest

from voisr.pasteboard import AppKitPasteboard


def _item(item_class, data_class, payloads):
    item = item_class.alloc().init()
    for type_name, payload in payloads:
        data = data_class.dataWithBytes_length_(payload, len(payload))
        assert item.setData_forType_(data, type_name)
    return item


def _clear_if_unchanged(board, expected_count):
    if int(board.changeCount()) != expected_count:
        return None
    clear_count = int(board.clearContents())
    if int(board.changeCount()) != clear_count:
        return None
    return clear_count


class FakeRoundtripPasteboard:
    def __init__(self, count=10, after_clear=None):
        self.count = count
        self.clear_calls = 0
        self.after_clear = after_clear

    def changeCount(self):
        return self.count

    def clearContents(self):
        self.clear_calls += 1
        self.count += 1
        clear_count = self.count
        if self.after_clear is not None:
            self.after_clear()
        return clear_count


def test_clipboard_roundtrip_setup_skips_stale_clipboard_before_clear():
    board = FakeRoundtripPasteboard(count=11)

    assert _clear_if_unchanged(board, expected_count=10) is None
    assert board.clear_calls == 0


def test_clipboard_roundtrip_setup_skips_external_change_after_clear():
    board = FakeRoundtripPasteboard()
    board.after_clear = lambda: setattr(board, "count", board.count + 1)

    assert _clear_if_unchanged(board, expected_count=10) is None


def test_clipboard_roundtrip():
    if os.environ.get("VOISR_RUN_CLIPBOARD_INTEGRATION") != "1":
        pytest.skip("set VOISR_RUN_CLIPBOARD_INTEGRATION=1 to run")
    AppKit = pytest.importorskip("AppKit")
    Foundation = pytest.importorskip("Foundation")
    adapter = AppKitPasteboard()
    original = adapter.snapshot()
    board = adapter._board
    test_items = [
        _item(AppKit.NSPasteboardItem, Foundation.NSData, [
            ("public.utf8-plain-text", b"wispr-local test"),
            ("public.html", b"<b>wispr-local test</b>"),
        ]),
        _item(
            AppKit.NSPasteboardItem,
            Foundation.NSData,
            [("com.example.binary", b"\x00\xff")],
        ),
    ]
    test_owner = None
    try:
        test_owner = _clear_if_unchanged(board, original.change_count)
        if test_owner is None:
            pytest.skip("clipboard changed during test setup")
        assert board.writeObjects_(test_items)
        test_owner = int(board.changeCount())
        captured = adapter.snapshot()
        assert [item.payloads for item in captured.items] == [
            (
                ("public.utf8-plain-text", b"wispr-local test"),
                ("public.html", b"<b>wispr-local test</b>"),
            ),
            (("com.example.binary", b"\x00\xff"),),
        ]
    finally:
        if test_owner is not None:
            adapter.restore_if_unchanged(original, test_owner)
