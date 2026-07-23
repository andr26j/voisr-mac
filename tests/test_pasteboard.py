from contextlib import nullcontext
from types import SimpleNamespace
import sys

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
    def __init__(self, payloads=None, unreadable=(), on_data_for_type=None,
                 on_set_data=None, on_set_string=None):
        self.payloads = dict(payloads or ())
        self.unreadable = set(unreadable)
        self.on_data_for_type = on_data_for_type
        self.on_set_data = on_set_data
        self.on_set_string = on_set_string

    def types(self):
        return list(self.payloads)

    def dataForType_(self, type_name):
        if self.on_data_for_type is not None:
            self.on_data_for_type()
        if type_name in self.unreadable:
            return None
        return FakeData(self.payloads[type_name])

    def setData_forType_(self, data, type_name):
        if self.on_set_data is not None:
            self.on_set_data()
        self.payloads[str(type_name)] = bytes(data)
        return True

    def setString_forType_(self, text, type_name):
        if self.on_set_string is not None:
            self.on_set_string()
        self.payloads[str(type_name)] = text.encode("utf-8")
        return True


class FakeItemClass:
    on_set_data = None
    on_set_string = None

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return FakeItem(
            on_set_data=type(self).on_set_data,
            on_set_string=type(self).on_set_string,
        )


class FakeNSData:
    @classmethod
    def dataWithBytes_length_(cls, payload, length):
        return FakeData(bytes(payload)[:length])


class FakePasteboard:
    def __init__(self, items=()):
        self.items = list(items)
        self.count = 10
        self.clear_calls = 0
        self.write_calls = 0
        self.write_outcomes = []
        self.write_error = None
        self.after_clear = None
        self.after_write = None
        self.after_failed_write = None
        self.change_count_errors = []

    def changeCount(self):
        if self.change_count_errors:
            error = self.change_count_errors.pop(0)
            if error is not None:
                raise error
        return self.count

    def pasteboardItems(self):
        return list(self.items)

    def clearContents(self):
        self.clear_calls += 1
        self.items = []
        self.count += 1
        clear_count = self.count
        if self.after_clear is not None:
            self.after_clear()
        return clear_count

    def writeObjects_(self, items):
        self.write_calls += 1
        if self.write_error is not None:
            raise self.write_error
        if self.write_outcomes and not self.write_outcomes.pop(0):
            if self.after_failed_write is not None:
                self.after_failed_write()
            return False
        self.items = list(items)
        if self.after_write is not None:
            self.after_write()
        return True


def make_adapter(board, item_class=FakeItemClass):
    return AppKitPasteboard(
        board=board,
        item_class=item_class,
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


def test_snapshot_rejects_change_during_enumeration_without_clearing():
    board = FakePasteboard()
    board.items = [FakeItem(
        [("public.png", b"image")],
        on_data_for_type=lambda: setattr(board, "count", board.count + 1),
    )]

    with pytest.raises(ClipboardPreservationError, match="changed"):
        make_adapter(board).snapshot()

    assert board.clear_calls == 0
    assert board.items[0].payloads["public.png"] == b"image"


def test_snapshot_and_restore_empty_pasteboard():
    board = FakePasteboard()
    adapter = make_adapter(board)
    snapshot = adapter.snapshot()

    owner_count = adapter.write_text_if_unchanged("dictation", snapshot)

    assert adapter.restore_if_unchanged(snapshot, owner_count) is True
    assert board.items == []


def test_snapshot_and_restore_type_less_item():
    board = FakePasteboard([FakeItem()])
    adapter = make_adapter(board)
    snapshot = adapter.snapshot()

    owner_count = adapter.write_text_if_unchanged("dictation", snapshot)

    assert adapter.restore_if_unchanged(snapshot, owner_count) is True
    assert snapshot_items(board) == ((),)


def test_constructor_rejects_unavailable_general_pasteboard(monkeypatch):
    class FakeNSPasteboard:
        @classmethod
        def generalPasteboard(cls):
            return None

    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSPasteboard=FakeNSPasteboard,
        NSPasteboardItem=FakeItemClass,
        NSPasteboardTypeString="public.utf8-plain-text",
    ))
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(
        NSData=FakeNSData,
    ))

    with pytest.raises(ClipboardPreservationError, match="unavailable"):
        AppKitPasteboard()


def test_constructor_translates_general_pasteboard_failure(monkeypatch):
    class FakeNSPasteboard:
        @classmethod
        def generalPasteboard(cls):
            raise OSError("service unavailable")

    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSPasteboard=FakeNSPasteboard,
        NSPasteboardItem=FakeItemClass,
        NSPasteboardTypeString="public.utf8-plain-text",
    ))
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(
        NSData=FakeNSData,
    ))

    with pytest.raises(ClipboardPreservationError) as caught:
        AppKitPasteboard()

    assert isinstance(caught.value.__cause__, OSError)


def test_snapshot_translates_native_selector_failure():
    board = FakePasteboard()
    board.pasteboardItems = lambda: (_ for _ in ()).throw(OSError("unavailable"))

    with pytest.raises(ClipboardPreservationError) as caught:
        make_adapter(board).snapshot()

    assert isinstance(caught.value.__cause__, OSError)


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


def test_write_aborts_when_item_creation_loses_ownership():
    board = FakePasteboard()

    class StringMutatingItemClass(FakeItemClass):
        on_set_string = lambda: setattr(board, "count", board.count + 1)

    adapter = make_adapter(board, StringMutatingItemClass)
    snapshot = PasteboardSnapshot(10, ())

    with pytest.raises(ClipboardPreservationError, match="changed"):
        adapter.write_text_if_unchanged("dictation", snapshot)

    assert board.clear_calls == 0


def test_restore_aborts_when_item_creation_loses_ownership():
    board = FakePasteboard()

    class DataMutatingItemClass(FakeItemClass):
        on_set_data = lambda: setattr(board, "count", board.count + 1)

    adapter = make_adapter(board, DataMutatingItemClass)

    assert adapter.restore_if_unchanged(rich_snapshot(), 10) is False
    assert board.clear_calls == 0


def test_write_aborts_when_external_change_follows_clear():
    board = FakePasteboard()

    def external_change():
        board.items = [FakeItem([("public.utf8-plain-text", b"newer")])]
        board.count += 1

    board.after_clear = external_change
    adapter = make_adapter(board)

    with pytest.raises(ClipboardPreservationError, match="changed"):
        adapter.write_text_if_unchanged("dictation", PasteboardSnapshot(10, ()))

    assert board.write_calls == 0
    assert board.items[0].payloads["public.utf8-plain-text"] == b"newer"


def test_restore_aborts_when_external_change_follows_clear():
    board = FakePasteboard()

    def external_change():
        board.items = [FakeItem([("public.utf8-plain-text", b"newer")])]
        board.count += 1

    board.after_clear = external_change
    adapter = make_adapter(board)

    assert adapter.restore_if_unchanged(rich_snapshot(), 10) is False
    assert board.write_calls == 0
    assert board.items[0].payloads["public.utf8-plain-text"] == b"newer"


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


def test_temporary_write_returns_clear_generation_as_owner():
    board = FakePasteboard()

    owner = make_adapter(board).write_text_if_unchanged(
        "dictation", PasteboardSnapshot(10, ()))

    assert owner == 11
    assert board.count == 11
    assert snapshot_items(board) == (
        (("public.utf8-plain-text", b"dictation"),),
    )


def test_write_rejects_external_generation_after_successful_write():
    board = FakePasteboard()
    snapshot = rich_snapshot()

    def external_write():
        board.items = [FakeItem([("public.utf8-plain-text", b"external")])]
        board.count += 1

    board.after_write = external_write

    with pytest.raises(ClipboardPreservationError, match="ownership"):
        make_adapter(board).write_text_if_unchanged("dictation", snapshot)

    assert board.items[0].payloads["public.utf8-plain-text"] == b"external"


def test_final_generation_read_failure_repairs_with_expected_owner_token():
    board = FakePasteboard()
    snapshot = rich_snapshot()
    board.after_write = lambda: board.change_count_errors.append(
        OSError("generation unavailable"))

    with pytest.raises(ClipboardPreservationError) as caught:
        make_adapter(board).write_text_if_unchanged("dictation", snapshot)

    assert isinstance(caught.value.__cause__, OSError)
    assert snapshot_items(board) == tuple(
        tuple(item.payloads) for item in snapshot.items
    )


@pytest.mark.parametrize("process_error", [KeyboardInterrupt(), SystemExit()])
def test_process_control_during_write_repairs_then_reraises(process_error):
    board = FakePasteboard()
    snapshot = rich_snapshot()
    board.write_error = process_error

    with pytest.raises(type(process_error)) as caught:
        make_adapter(board).write_text_if_unchanged("dictation", snapshot)

    assert caught.value is process_error
    assert board.write_calls >= 2


def test_restore_skips_newer_external_clipboard():
    board = FakePasteboard()
    adapter = make_adapter(board)
    snapshot = rich_snapshot()
    owner_count = adapter.write_text_if_unchanged("dictation", snapshot)
    board.items = [FakeItem([("public.utf8-plain-text", b"newer")])]
    board.count += 1

    assert adapter.restore_if_unchanged(snapshot, owner_count) is False
    assert board.items[0].payloads["public.utf8-plain-text"] == b"newer"


def test_failed_restoration_write_attempts_conditional_repair():
    board = FakePasteboard()
    board.write_outcomes = [False, True]
    snapshot = rich_snapshot()

    with pytest.raises(ClipboardPreservationError, match="restoration"):
        make_adapter(board).restore_if_unchanged(snapshot, 10)

    assert board.write_calls == 2
    assert snapshot_items(board) == tuple(
        tuple(item.payloads) for item in snapshot.items
    )


@pytest.mark.parametrize("repair_error", [KeyboardInterrupt(), SystemExit()])
def test_restoration_repair_process_control_outranks_original_error(repair_error):
    board = FakePasteboard()
    board.write_outcomes = [False]
    board.after_failed_write = lambda: setattr(board, "write_error", repair_error)

    with pytest.raises(type(repair_error)) as caught:
        make_adapter(board).restore_if_unchanged(rich_snapshot(), 10)

    assert caught.value is repair_error
    assert isinstance(caught.value.__cause__, ClipboardPreservationError)


@pytest.mark.parametrize("repair_error", [KeyboardInterrupt(), SystemExit()])
def test_temporary_write_repair_process_control_outranks_original_error(
        repair_error):
    board = FakePasteboard()
    adapter = make_adapter(board)
    original = OSError("temporary write unavailable")
    adapter._restore_owned = lambda snapshot, count: (_ for _ in ()).throw(
        repair_error)

    with pytest.raises(type(repair_error)) as caught:
        adapter._repair_after_clear(rich_snapshot(), 11, original)

    assert caught.value is repair_error
    assert caught.value.__cause__ is original


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
