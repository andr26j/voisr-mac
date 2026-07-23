from contextlib import nullcontext

import pytest

from voisr.pasteboard import (
    AppKitPasteboard,
    ClipboardPreservationError,
    PasteboardItemSnapshot,
    PasteboardSnapshot,
)


class FakeData:
    def __init__(self, payload):
        self.payload = bytes(payload)

    def __bytes__(self):
        return self.payload


class FakeItem:
    def __init__(self, payloads=None, unreadable=()):
        self.payloads = dict(payloads or ())
        self.unreadable = set(unreadable)

    def types(self):
        return list(self.payloads)

    def dataForType_(self, type_name):
        if type_name in self.unreadable:
            return None
        return FakeData(self.payloads[type_name])

    def setData_forType_(self, data, type_name):
        self.payloads[str(type_name)] = bytes(data)
        return True

    def setString_forType_(self, text, type_name):
        self.payloads[str(type_name)] = text.encode("utf-8")
        return True


class FakeItemClass:
    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return FakeItem()


class FakeNSData:
    @classmethod
    def dataWithBytes_length_(cls, payload, length):
        return FakeData(bytes(payload)[:length])


class FakePasteboard:
    def __init__(self, items=()):
        self.items = list(items)
        self.count = 10
        self.clear_calls = 0
        self.write_outcomes = []

    def changeCount(self):
        return self.count

    def pasteboardItems(self):
        return list(self.items)

    def clearContents(self):
        self.clear_calls += 1
        self.items = []
        self.count += 1
        return self.count

    def writeObjects_(self, items):
        if self.write_outcomes and not self.write_outcomes.pop(0):
            return False
        self.items = list(items)
        self.count += 1
        return True


def make_adapter(board):
    return AppKitPasteboard(
        board=board,
        item_class=FakeItemClass,
        data_class=FakeNSData,
        string_type="public.utf8-plain-text",
        pool_factory=nullcontext,
    )


def test_snapshot_preserves_item_type_and_byte_order():
    board = FakePasteboard([
        FakeItem([
            ("public.utf8-plain-text", b"hello"),
            ("public.html", b"<b>hello</b>"),
        ]),
        FakeItem([
            ("public.png", b"\x89PNG"),
            ("com.example.custom", b"\x00\x01"),
        ]),
    ])

    snapshot = make_adapter(board).snapshot()

    assert snapshot == PasteboardSnapshot(
        change_count=10,
        items=(
            PasteboardItemSnapshot(payloads=(
                ("public.utf8-plain-text", b"hello"),
                ("public.html", b"<b>hello</b>"),
            )),
            PasteboardItemSnapshot(payloads=(
                ("public.png", b"\x89PNG"),
                ("com.example.custom", b"\x00\x01"),
            )),
        ),
    )


def test_snapshot_failure_does_not_clear_clipboard():
    board = FakePasteboard([
        FakeItem([("public.png", b"image")], unreadable={"public.png"}),
    ])

    with pytest.raises(ClipboardPreservationError, match="public.png"):
        make_adapter(board).snapshot()

    assert board.clear_calls == 0
    assert board.items[0].payloads["public.png"] == b"image"


def rich_snapshot():
    return PasteboardSnapshot(10, (
        PasteboardItemSnapshot((
            ("public.html", b"<b>old</b>"),
            ("public.png", b"\x89PNG"),
        )),
        PasteboardItemSnapshot((
            ("public.file-url", b"file:///tmp/example"),
        )),
    ))


def snapshot_items(board):
    return tuple(tuple(item.payloads.items()) for item in board.items)


def test_write_aborts_when_source_change_count_changed():
    board = FakePasteboard()
    adapter = make_adapter(board)
    snapshot = PasteboardSnapshot(9, ())

    with pytest.raises(ClipboardPreservationError, match="changed"):
        adapter.write_text_if_unchanged("dictation", snapshot)

    assert board.clear_calls == 0


def test_owned_write_and_restore_round_trip_all_raw_items():
    board = FakePasteboard()
    adapter = make_adapter(board)
    snapshot = rich_snapshot()

    owner_count = adapter.write_text_if_unchanged("dictation", snapshot)
    assert snapshot_items(board) == (
        (("public.utf8-plain-text", b"dictation"),),
    )

    assert adapter.restore_if_unchanged(snapshot, owner_count) is True
    assert snapshot_items(board) == tuple(
        tuple(item.payloads) for item in snapshot.items
    )


def test_restore_skips_newer_external_clipboard():
    board = FakePasteboard()
    adapter = make_adapter(board)
    snapshot = rich_snapshot()
    owner_count = adapter.write_text_if_unchanged("dictation", snapshot)
    board.items = [FakeItem([("public.utf8-plain-text", b"newer")])]
    board.count += 1

    assert adapter.restore_if_unchanged(snapshot, owner_count) is False
    assert board.items[0].payloads["public.utf8-plain-text"] == b"newer"


def test_failed_temporary_write_repairs_only_while_still_owned():
    board = FakePasteboard()
    board.write_outcomes = [False]
    adapter = make_adapter(board)
    snapshot = rich_snapshot()

    with pytest.raises(ClipboardPreservationError, match="temporary"):
        adapter.write_text_if_unchanged("dictation", snapshot)

    assert snapshot_items(board) == tuple(
        tuple(item.payloads) for item in snapshot.items
    )
