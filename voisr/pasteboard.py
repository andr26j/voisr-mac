from contextlib import nullcontext
from dataclasses import dataclass


@dataclass(frozen=True)
class PasteboardItemSnapshot:
    payloads: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class PasteboardSnapshot:
    change_count: int
    items: tuple[PasteboardItemSnapshot, ...]


class PasteDeliveryError(RuntimeError):
    def __init__(self, message: str, *, pasted: bool = False,
                 fallback_safe: bool = False):
        super().__init__(message)
        self.pasted = pasted
        self.fallback_safe = fallback_safe


class ClipboardPreservationError(PasteDeliveryError):
    pass


class AppKitPasteboard:
    def __init__(self, board=None, item_class=None, data_class=None,
                 string_type=None, pool_factory=None):
        if board is None:
            from AppKit import NSPasteboard
            board = NSPasteboard.generalPasteboard()
            if board is None:
                raise ClipboardPreservationError("general pasteboard unavailable")
        if item_class is None or string_type is None:
            from AppKit import NSPasteboardItem, NSPasteboardTypeString
            item_class = NSPasteboardItem
            string_type = NSPasteboardTypeString
        if data_class is None:
            from Foundation import NSData
            data_class = NSData
        if pool_factory is None:
            try:
                import objc
            except ImportError:
                pool_factory = nullcontext
            else:
                pool_factory = objc.autorelease_pool
        self._board = board
        self._item_class = item_class
        self._data_class = data_class
        self._string_type = string_type
        self._pool_factory = pool_factory

    def snapshot(self) -> PasteboardSnapshot:
        with self._pool_factory():
            change_count = int(self._board.changeCount())
            items = []
            for source_item in self._board.pasteboardItems() or ():
                payloads = []
                for type_name in source_item.types() or ():
                    raw = source_item.dataForType_(type_name)
                    if raw is None:
                        raise ClipboardPreservationError(
                            f"clipboard type could not be captured: {type_name}")
                    try:
                        payload = bytes(raw)
                    except Exception as error:
                        raise ClipboardPreservationError(
                            f"clipboard type could not be captured: {type_name}"
                        ) from error
                    payloads.append((str(type_name), payload))
                items.append(PasteboardItemSnapshot(tuple(payloads)))
            if int(self._board.changeCount()) != change_count:
                raise ClipboardPreservationError("clipboard changed during capture")
            return PasteboardSnapshot(change_count, tuple(items))

    def _item_from_snapshot(self, snapshot_item):
        item = self._item_class.alloc().init()
        for type_name, payload in snapshot_item.payloads:
            data = self._data_class.dataWithBytes_length_(payload, len(payload))
            if not item.setData_forType_(data, type_name):
                raise ClipboardPreservationError(
                    f"clipboard type could not be restored: {type_name}")
        return item

    def _restore_owned(self, snapshot, expected_change_count):
        if int(self._board.changeCount()) != expected_change_count:
            return False
        restored = [self._item_from_snapshot(item) for item in snapshot.items]
        if int(self._board.changeCount()) != expected_change_count:
            return False
        clear_count = int(self._board.clearContents())
        if int(self._board.changeCount()) != clear_count:
            return False
        if restored and not self._board.writeObjects_(restored):
            raise ClipboardPreservationError("clipboard restoration failed")
        return True

    def write_text_if_unchanged(self, text, snapshot):
        with self._pool_factory():
            if int(self._board.changeCount()) != snapshot.change_count:
                raise ClipboardPreservationError(
                    "clipboard changed before temporary write")
            item = self._item_class.alloc().init()
            if not item.setString_forType_(text, self._string_type):
                raise ClipboardPreservationError(
                    "temporary clipboard text could not be created")
            if int(self._board.changeCount()) != snapshot.change_count:
                raise ClipboardPreservationError(
                    "clipboard changed before temporary write")
            clear_count = int(self._board.clearContents())
            if int(self._board.changeCount()) != clear_count:
                raise ClipboardPreservationError(
                    "clipboard changed during temporary write")
            try:
                wrote = self._board.writeObjects_([item])
            except Exception as error:
                try:
                    self._restore_owned(snapshot, clear_count)
                except Exception as restore_error:
                    raise ClipboardPreservationError(
                        "temporary clipboard write and repair failed"
                    ) from restore_error
                raise ClipboardPreservationError(
                    "temporary clipboard write failed") from error
            if not wrote:
                try:
                    self._restore_owned(snapshot, clear_count)
                except Exception as restore_error:
                    raise ClipboardPreservationError(
                        "temporary clipboard write and repair failed"
                    ) from restore_error
                raise ClipboardPreservationError(
                    "temporary clipboard write failed")
            return int(self._board.changeCount())

    def restore_if_unchanged(self, snapshot, expected_change_count):
        with self._pool_factory():
            return self._restore_owned(snapshot, expected_change_count)
