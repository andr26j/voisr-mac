import contextlib
import fcntl
import json
import math
import os
import stat
import threading
import time
import uuid
import weakref
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path


TYPING_WPM = 40  # the baseline Time saved is measured against

_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)
_FORK_FD_GUARD = threading.Lock()
_OPEN_LOCK_FDS: set[int] = set()
_MUTATION_STATES = threading.local()
_FORK_EPOCH = 0

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_LOCK_CREATE_RETRIES = 32


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _finite_number(value) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if not isinstance(value, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _finite_timestamp_number(value) -> bool:
    if not _finite_number(value):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _valid_record(item) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and bool(item["id"])
        and _finite_timestamp_number(item.get("ts"))
        and isinstance(item.get("text"), str)
        and isinstance(item.get("words"), int)
        and not isinstance(item["words"], bool)
        and item["words"] >= 0
        and _finite_number(item.get("duration_s"))
        and item["duration_s"] >= 0
    )


def _mutation_states() -> dict[str, tuple[int, int]]:
    states = getattr(_MUTATION_STATES, "locks", None)
    if states is None:
        states = {}
        _MUTATION_STATES.locks = states
    return states


def _track_lock_fd(fd: int) -> None:
    _OPEN_LOCK_FDS.add(fd)


def _untrack_lock_fd(fd: int) -> None:
    _OPEN_LOCK_FDS.discard(fd)


def _open_tracked(path, flags: int, mode: int | None = None, *, dir_fd=None) -> int:
    """Open and register an fd while forks are excluded from the gap."""
    with _FORK_FD_GUARD:
        if mode is None:
            fd = os.open(path, flags, dir_fd=dir_fd)
        else:
            fd = os.open(path, flags, mode, dir_fd=dir_fd)
        _track_lock_fd(fd)
        return fd


def _close_tracked(fd: int) -> None:
    """Unregister and close an fd as one fork-safe lifecycle operation."""
    with _FORK_FD_GUARD:
        try:
            _untrack_lock_fd(fd)
        finally:
            os.close(fd)


def _dup_tracked(fd: int) -> int:
    with _FORK_FD_GUARD:
        duplicate = os.dup(fd)
        _track_lock_fd(duplicate)
        return duplicate


def _open_regular_at(parent_fd: int, name: str, flags: int,
                     mode: int | None = None) -> int:
    fd = _open_tracked(name, flags, mode, dir_fd=parent_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"history entry is not a regular file: {name}")
        return fd
    except BaseException:
        _close_tracked(fd)
        raise


def _open_or_create_regular_lock_at(parent_fd: int, name: str) -> int:
    """Create a sidecar once, retrying only its create-then-open race."""
    create_flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    open_flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    for _ in range(_LOCK_CREATE_RETRIES):
        try:
            return _open_regular_at(parent_fd, name, create_flags, 0o600)
        except FileExistsError:
            try:
                return _open_regular_at(parent_fd, name, open_flags)
            except FileNotFoundError:
                continue
    raise OSError("history sidecar changed too often while opening")


def _ensure_private_parent(path: Path) -> int:
    """Create missing canonical parent components and return its stable fd."""
    parent = path.parent
    root = parent.anchor or os.sep
    fd = _open_tracked(root, _DIRECTORY_FLAGS)
    try:
        for component in parent.parts[1:]:
            created = False
            try:
                child_fd = _open_tracked(component, _DIRECTORY_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                child_fd = _open_tracked(component, _DIRECTORY_FLAGS, dir_fd=fd)
            try:
                child_stat = os.fstat(child_fd)
                if not stat.S_ISDIR(child_stat.st_mode):
                    raise OSError("history parent is not a directory")
                if created:
                    if (child_stat.st_uid != os.geteuid()
                            or stat.S_IMODE(child_stat.st_mode) & 0o077):
                        raise OSError("new history parent changed before validation")
                    os.fchmod(child_fd, 0o700)
                    if stat.S_IMODE(os.fstat(child_fd).st_mode) != 0o700:
                        raise OSError("new history parent has unexpected mode")
            except BaseException:
                _close_tracked(child_fd)
                raise
            _close_tracked(fd)
            fd = child_fd
        return fd
    except BaseException:
        _close_tracked(fd)
        raise


def _before_fork() -> None:
    _FORK_FD_GUARD.acquire()


def _after_fork_parent() -> None:
    _FORK_FD_GUARD.release()


def _after_fork_child() -> None:
    global _PATH_LOCKS_GUARD, _PATH_LOCKS
    global _FORK_FD_GUARD, _OPEN_LOCK_FDS, _MUTATION_STATES, _FORK_EPOCH

    for fd in tuple(_OPEN_LOCK_FDS):
        try:
            os.close(fd)
        except OSError:
            pass
    _PATH_LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS = weakref.WeakValueDictionary()
    _OPEN_LOCK_FDS = set()
    _MUTATION_STATES = threading.local()
    _FORK_EPOCH += 1
    _FORK_FD_GUARD = threading.Lock()


os.register_at_fork(
    before=_before_fork,
    after_in_parent=_after_fork_parent,
    after_in_child=_after_fork_child,
)


class HistoryStore:
    """Append-only JSONL store of dictations with derived usage stats."""

    def __init__(self, path):
        self.path = Path(path)
        self._path = Path(os.path.abspath(self.path)).resolve(strict=False)
        self._name = self._path.name
        self._lifecycle_lock = threading.RLock()
        self._owner_pid = os.getpid()
        self._parent_fd = None
        self._parent_epoch = _FORK_EPOCH
        self._parent_fd = _ensure_private_parent(self._path)
        self.lock_path = self._path.with_name(self._path.name + ".lock")
        try:
            with self._parent_fd_lease() as parent_fd:
                self._tighten_existing_history_file(parent_fd)
                self._create_or_tighten_lock_file(parent_fd)
        except BaseException:
            self.close()
            raise

    def __del__(self):
        try:
            self.close()
        except (AttributeError, OSError):
            pass

    def close(self) -> None:
        self._ensure_current_process()
        with self._lifecycle_lock:
            fd = self._parent_fd
            self._parent_fd = None
            if fd is not None:
                _close_tracked(fd)

    def __getstate__(self):
        state = self.__dict__.copy()
        state["_parent_fd"] = None
        state["_lifecycle_lock"] = None
        state["_parent_epoch"] = _FORK_EPOCH
        state["_owner_pid"] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._parent_fd = None
        self._lifecycle_lock = threading.RLock()
        self._parent_epoch = _FORK_EPOCH
        self._owner_pid = os.getpid()

    def _ensure_current_process(self) -> None:
        if (getattr(self, "_owner_pid", None) != os.getpid()
                or getattr(self, "_parent_epoch", None) != _FORK_EPOCH):
            self._parent_fd = None
            self._parent_epoch = _FORK_EPOCH
            self._owner_pid = os.getpid()
            self._lifecycle_lock = threading.RLock()

    @contextlib.contextmanager
    def _parent_fd_lease(self):
        self._ensure_current_process()
        with self._lifecycle_lock:
            if self._parent_fd is None:
                self._parent_fd = _ensure_private_parent(self._path)
                self._parent_epoch = _FORK_EPOCH
            lease_fd = _dup_tracked(self._parent_fd)
        try:
            yield lease_fd
        finally:
            _close_tracked(lease_fd)

    def _tighten_existing_history_file(self, parent_fd: int) -> None:
        try:
            fd = _open_regular_at(parent_fd, self._name,
                                  _FILE_READ_FLAGS)
        except FileNotFoundError:
            return
        try:
            os.fchmod(fd, 0o600)
        finally:
            _close_tracked(fd)

    def _create_or_tighten_lock_file(self, parent_fd: int | None = None) -> None:
        if parent_fd is None:
            with self._parent_fd_lease() as lease_fd:
                self._create_or_tighten_lock_file(lease_fd)
            return
        fd = _open_or_create_regular_lock_at(parent_fd, self.lock_path.name)
        try:
            os.fchmod(fd, 0o600)
        finally:
            _close_tracked(fd)

    @contextlib.contextmanager
    def _mutation_lock(self):
        path_key = str(self._path)
        with _thread_lock_for(self._path):
            states = _mutation_states()
            state = states.get(path_key)
            if state is not None:
                fd, depth = state
                states[path_key] = (fd, depth + 1)
                try:
                    yield
                finally:
                    states[path_key] = (fd, depth)
                return

            with self._parent_fd_lease() as parent_fd:
                fd = _open_or_create_regular_lock_at(parent_fd, self.lock_path.name)
            locked = False
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
                states[path_key] = (fd, 1)
                yield
            finally:
                states.pop(path_key, None)
                try:
                    if locked:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    _close_tracked(fd)

    def _read(self) -> list[dict]:
        with self._parent_fd_lease() as parent_fd:
            try:
                fd = _open_regular_at(parent_fd, self._name, _FILE_READ_FLAGS)
            except FileNotFoundError:
                return []
            try:
                chunks = []
                while True:
                    chunk = os.read(fd, 64 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                _close_tracked(fd)
        items = []
        # Decode per line: one undecodable byte (a torn write from an older
        # build, a disk error) must cost that line only, not the whole store.
        for raw_line in b"".join(chunks).splitlines():
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _valid_record(item):
                items.append(item)
        return items

    def _write_atomic(self, items: list[dict]) -> None:
        payload = "".join(json.dumps(item) + "\n" for item in items).encode("utf-8")
        with self._parent_fd_lease() as parent_fd:
            temp_name = None
            fd = -1
            try:
                while True:
                    candidate = f".{self._name}.{uuid.uuid4().hex}.tmp"
                    try:
                        fd = _open_regular_at(
                            parent_fd, candidate,
                            os.O_CREAT | os.O_EXCL | _FILE_WRITE_FLAGS, 0o600,
                        )
                        temp_name = candidate
                        break
                    except FileExistsError:
                        continue
                os.fchmod(fd, 0o600)
                written = 0
                while written < len(payload):
                    written += os.write(fd, payload[written:])
                os.fsync(fd)
                _close_tracked(fd)
                fd = -1
                os.replace(temp_name, self._name, src_dir_fd=parent_fd,
                           dst_dir_fd=parent_fd)
                temp_name = None
                os.fsync(parent_fd)
            finally:
                if fd >= 0:
                    _close_tracked(fd)
                if temp_name is not None:
                    try:
                        os.unlink(temp_name, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass

    def _mutate(self, change):
        with self._mutation_lock():
            items = self._read()
            result = change(items)
            self._write_atomic(items)
            return result

    def add(self, text: str, duration_s: float) -> dict:
        if not _finite_number(duration_s) or duration_s < 0:
            raise ValueError("duration_s must be a finite, nonnegative number")
        item = {"id": uuid.uuid4().hex, "ts": time.time(),
                "text": text, "words": len(text.split()), "duration_s": duration_s}

        def append(items):
            items.append(item)
            return item

        return self._mutate(append)

    def list(self) -> list[dict]:
        return sorted(self._read(), key=lambda i: i["ts"], reverse=True)

    def delete(self, item_id: str) -> None:
        def remove(items):
            items[:] = [item for item in items if item["id"] != item_id]

        self._mutate(remove)

    def restore(self, item: dict) -> dict:
        """Re-insert a previously deleted record, keeping its id and ts.
        Used by the dashboard's undo-delete. Idempotent."""
        def put_back(items):
            if any(i["id"] == item["id"] for i in items):
                return item
            items.append(dict(item))
            return item

        return self._mutate(put_back)

    def clear(self) -> int:
        """Remove every record in a single atomic write. Deleting row by row
        would rewrite the whole file once per row."""
        def wipe(items):
            count = len(items)
            items[:] = []
            return count

        return self._mutate(wipe)

    def _daily_series(self, items, days: int = 14) -> list[dict]:
        """Words and time saved per day for the last `days` days, oldest
        first, zero-filled. Entries outside the window are ignored.

        saved_seconds is the same sum stats() uses for time_saved_seconds,
        per day: Home's Time saved tile draws its own trend and week delta
        from it rather than borrowing the word series."""
        today = date.today()
        window = [today - timedelta(days=n) for n in range(days - 1, -1, -1)]
        counts = {d: 0 for d in window}
        spoken = {d: Fraction() for d in window}
        for item in items:
            try:
                d = date.fromtimestamp(item["ts"])
            except (OverflowError, OSError, ValueError):
                continue
            if d in counts:
                counts[d] += item["words"]
                spoken[d] += Fraction(item["duration_s"])
        return [{"date": d.isoformat(), "words": counts[d],
                 "saved_seconds": round(max(
                     Fraction(counts[d] * 60, TYPING_WPM) - spoken[d], 0))}
                for d in window]

    def stats(self) -> dict:
        items = self._read()
        if not items:
            return {"total_words": 0, "dictations": 0, "avg_wpm": 0,
                    "streak_days": 0, "total_seconds": 0,
                    "time_saved_seconds": 0,
                    "daily": self._daily_series([])}
        total_words = sum(i["words"] for i in items)
        total_time = sum((Fraction(i["duration_s"]) for i in items), Fraction())
        typed_time = Fraction(total_words * 60, TYPING_WPM)
        saved = typed_time - total_time
        if saved < 0:
            saved = Fraction(0)
        total_seconds = total_time
        if not total_time:
            total_time = Fraction(1, 1_000_000_000)
        days = set()
        for item in items:
            try:
                days.add(date.fromtimestamp(item["ts"]))
            except (OverflowError, OSError, ValueError):
                continue
        # A streak is alive until today ends: one that ran through yesterday
        # must not read 0 every morning before the first dictation.
        streak, d = 0, date.today()
        if d not in days:
            d -= timedelta(days=1)
        while d in days:
            streak += 1
            d -= timedelta(days=1)
        return {"total_words": total_words, "dictations": len(items),
                "avg_wpm": round(Fraction(total_words * 60, 1) / total_time),
                "streak_days": streak,
                "total_seconds": round(total_seconds),
                "time_saved_seconds": round(saved),
                "daily": self._daily_series(items)}
