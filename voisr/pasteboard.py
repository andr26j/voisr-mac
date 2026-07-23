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
        if pool_factory is None:
            try:
                import objc
            except ImportError:
                pool_factory = nullcontext
            else:
                pool_factory = objc.autorelease_pool
        self._pool_factory = pool_factory
        try:
            with self._pool_factory():
                if board is None:
                    from AppKit import NSPasteboard
                    board = NSPasteboard.generalPasteboard()
                    if board is None:
                        raise ClipboardPreservationError(
                            "general pasteboard unavailable")
                if item_class is None or string_type is None:
                    from AppKit import NSPasteboardItem, NSPasteboardTypeString
                    item_class = NSPasteboardItem
                    string_type = NSPasteboardTypeString
                if data_class is None:
                    from Foundation import NSData
                    data_class = NSData
        except ClipboardPreservationError:
            raise
        except Exception as error:
            raise ClipboardPreservationError(
                "general pasteboard unavailable") from error
        self._board = board
        self._item_class = item_class
        self._data_class = data_class
        self._string_type = string_type

    def snapshot(self) -> PasteboardSnapshot:
        try:
            with self._pool_factory():
                change_count = int(self._board.changeCount())
                items = []
                for source_item in self._board.pasteboardItems() or ():
                    payloads = []
                    for type_name in source_item.types() or ():
                        raw = source_item.dataForType_(type_name)
                        if raw is None:
                            raise ClipboardPreservationError(
                                "clipboard type could not be captured: "
                                f"{type_name}")
                        try:
                            payload = bytes(raw)
                        except Exception as error:
                            raise ClipboardPreservationError(
                                "clipboard type could not be captured: "
                                f"{type_name}") from error
                        payloads.append((str(type_name), payload))
                    items.append(PasteboardItemSnapshot(tuple(payloads)))
                if int(self._board.changeCount()) != change_count:
                    raise ClipboardPreservationError(
                        "clipboard changed during capture")
                return PasteboardSnapshot(change_count, tuple(items))
        except ClipboardPreservationError:
            raise
        except Exception as error:
            raise ClipboardPreservationError("clipboard capture failed") from error

    def is_owned(self, expected_count) -> bool:
        try:
            with self._pool_factory():
                return self._is_owned(expected_count)
        except ClipboardPreservationError:
            raise
        except Exception as error:
            raise ClipboardPreservationError(
                "clipboard ownership check failed") from error

    def _is_owned(self, expected_count):
        return int(self._board.changeCount()) == expected_count

    def _item_from_snapshot(self, snapshot_item):
        item = self._item_class.alloc().init()
        for type_name, payload in snapshot_item.payloads:
            data = self._data_class.dataWithBytes_length_(payload, len(payload))
            if not item.setData_forType_(data, type_name):
                raise ClipboardPreservationError(
                    f"clipboard type could not be restored: {type_name}")
        return item

    def _restore_owned(self, snapshot, expected_change_count):
        if not self._is_owned(expected_change_count):
            return False
        restored = [self._item_from_snapshot(item) for item in snapshot.items]
        if not self._is_owned(expected_change_count):
            return False
        clear_count = int(self._board.clearContents())
        if not self._is_owned(clear_count):
            return False
        try:
            if restored and not self._board.writeObjects_(restored):
                raise ClipboardPreservationError("clipboard restoration failed")
        except BaseException as error:
            try:
                if self._is_owned(clear_count):
                    self._board.writeObjects_(restored)
            except BaseException as repair_error:
                if not isinstance(error, Exception):
                    raise error from repair_error
                if not isinstance(repair_error, Exception):
                    raise repair_error from error
                raise ClipboardPreservationError(
                    "clipboard restoration and repair failed") from error
            raise
        return True

    def _repair_after_clear(self, snapshot, expected_count, original):
        try:
            self._restore_owned(snapshot, expected_count)
        except BaseException as repair_error:
            if not isinstance(original, Exception):
                raise original from repair_error
            if not isinstance(repair_error, Exception):
                raise repair_error from original
            raise ClipboardPreservationError(
                "temporary clipboard write and repair failed") from original
        if isinstance(original, ClipboardPreservationError):
            raise original
        if isinstance(original, Exception):
            raise ClipboardPreservationError(
                "temporary clipboard write failed") from original
        raise original

    def write_text_if_unchanged(self, text, snapshot):
        with self._pool_factory():
            try:
                if not self._is_owned(snapshot.change_count):
                    raise ClipboardPreservationError(
                        "clipboard changed before temporary write")
                item = self._item_class.alloc().init()
                if not item.setString_forType_(text, self._string_type):
                    raise ClipboardPreservationError(
                        "temporary clipboard text could not be created")
                if not self._is_owned(snapshot.change_count):
                    raise ClipboardPreservationError(
                        "clipboard changed before temporary write")
            except ClipboardPreservationError:
                raise
            except Exception as error:
                raise ClipboardPreservationError(
                    "temporary clipboard write failed") from error

            clear_count = None
            try:
                clear_count = int(self._board.clearContents())
                if not self._is_owned(clear_count):
                    raise ClipboardPreservationError(
                        "clipboard changed during temporary write")
                if not self._board.writeObjects_([item]):
                    raise ClipboardPreservationError(
                        "temporary clipboard write failed")
                if not self._is_owned(clear_count):
                    raise ClipboardPreservationError(
                        "clipboard ownership lost during temporary write")
                return clear_count
            except BaseException as error:
                if clear_count is None:
                    if isinstance(error, ClipboardPreservationError):
                        raise
                    if isinstance(error, Exception):
                        raise ClipboardPreservationError(
                            "temporary clipboard write failed") from error
                    raise
                self._repair_after_clear(snapshot, clear_count, error)

    def restore_if_unchanged(self, snapshot, expected_change_count):
        try:
            with self._pool_factory():
                return self._restore_owned(snapshot, expected_change_count)
        except ClipboardPreservationError:
            raise
        except Exception as error:
            raise ClipboardPreservationError("clipboard restoration failed") from error
