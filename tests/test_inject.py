import pytest

from voisr.pasteboard import AppKitPasteboard


def _item(item_class, data_class, payloads):
    item = item_class.alloc().init()
    for type_name, payload in payloads:
        data = data_class.dataWithBytes_length_(payload, len(payload))
        assert item.setData_forType_(data, type_name)
    return item


def test_clipboard_roundtrip():
    AppKit = pytest.importorskip("AppKit")
    Foundation = pytest.importorskip("Foundation")
    adapter = AppKitPasteboard()
    original = adapter.snapshot()
    board = adapter._board
    test_owner = None
    try:
        board.clearContents()
        assert board.writeObjects_([
            _item(AppKit.NSPasteboardItem, Foundation.NSData, [
                ("public.utf8-plain-text", b"wispr-local test"),
                ("public.html", b"<b>wispr-local test</b>"),
            ]),
            _item(
                AppKit.NSPasteboardItem,
                Foundation.NSData,
                [("com.example.binary", b"\x00\xff")],
            ),
        ])
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
