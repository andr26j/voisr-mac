import os

import pytest

from voisr.pasteboard import AppKitPasteboard, ClipboardPreservationError


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


def _observe_test_write(board, test_owner):
    expected_owner = test_owner[0] + 1
    test_owner[0] = expected_owner
    return int(board.changeCount()) == expected_owner


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


def test_roundtrip_cleanup_keeps_expected_token_when_write_observation_fails():
    class FakeAdapter:
        def __init__(self):
            self.value = b"wispr-local test"
            self.restore_calls = []

        def restore_if_unchanged(self, original, owner_count):
            self.restore_calls.append(owner_count)
            if owner_count == 12:
                self.value = original

    board = FakeRoundtripPasteboard(count=12)
    board.changeCount = lambda: (_ for _ in ()).throw(OSError("unavailable"))
    adapter = FakeAdapter()
    test_owner = [11]
    original = b"original"

    try:
        _observe_test_write(board, test_owner)
    except OSError:
        pass
    finally:
        adapter.restore_if_unchanged(original, test_owner[0])

    assert adapter.restore_calls == [12]
    assert adapter.value == original


def test_roundtrip_setup_skips_stale_clipboard_before_clear():
    board = FakeRoundtripPasteboard(count=11)

    assert _clear_if_unchanged(board, expected_count=10) is None
    assert board.clear_calls == 0


def test_roundtrip_setup_skips_external_change_after_clear():
    board = FakeRoundtripPasteboard()
    board.after_clear = lambda: setattr(board, "count", board.count + 1)

    assert _clear_if_unchanged(board, expected_count=10) is None


def test_clipboard_roundtrip():
    if os.environ.get("VOISR_RUN_CLIPBOARD_INTEGRATION") != "1":
        pytest.skip("set VOISR_RUN_CLIPBOARD_INTEGRATION=1 to run")
    AppKit = pytest.importorskip("AppKit")
    Foundation = pytest.importorskip("Foundation")
    try:
        adapter = AppKitPasteboard()
        original = adapter.snapshot()
    except ClipboardPreservationError as error:
        pytest.skip(f"pasteboard service unavailable: {error}")
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
    test_owner = [None]
    try:
        test_owner[0] = _clear_if_unchanged(board, original.change_count)
        if test_owner[0] is None:
            pytest.skip("clipboard changed during test setup")
        assert board.writeObjects_(test_items)
        if not _observe_test_write(board, test_owner):
            pytest.skip("clipboard changed during test write")
        captured = adapter.snapshot()
        assert [item.payloads for item in captured.items] == [
            (
                ("public.utf8-plain-text", b"wispr-local test"),
                ("public.html", b"<b>wispr-local test</b>"),
            ),
            (("com.example.binary", b"\x00\xff"),),
        ]
    finally:
        if test_owner[0] is not None:
            adapter.restore_if_unchanged(original, test_owner[0])
