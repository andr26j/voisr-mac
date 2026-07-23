import contextlib
import fcntl
import json
import math
import os
import stat
import tempfile
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}
_FORK_FD_GUARD = threading.Lock()
_OPEN_LOCK_FDS: set[int] = set()
_MUTATION_STATES = threading.local()


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _finite_number(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _ensure_private_parent(path: Path) -> None:
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(directory, flags)
        except PermissionError:
            os.chmod(directory, 0o700, follow_symlinks=False)
            fd = os.open(directory, flags)
        try:
            os.fchmod(fd, 0o700)
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                raise OSError("new history parent has unexpected mode")
        finally:
            os.close(fd)


def _valid_record(item) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and bool(item["id"])
        and _finite_number(item.get("ts"))
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


def _before_fork() -> None:
    _FORK_FD_GUARD.acquire()


def _after_fork_parent() -> None:
    _FORK_FD_GUARD.release()


def _after_fork_child() -> None:
    global _PATH_LOCKS_GUARD, _PATH_LOCKS
    global _FORK_FD_GUARD, _OPEN_LOCK_FDS, _MUTATION_STATES

    for fd in tuple(_OPEN_LOCK_FDS):
        try:
            os.close(fd)
        except OSError:
            pass
    _PATH_LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS = {}
    _OPEN_LOCK_FDS = set()
    _MUTATION_STATES = threading.local()
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
        self._path = self.path.expanduser().resolve(strict=False)
        _ensure_private_parent(self._path)
        self.lock_path = self._path.with_name(self._path.name + ".lock")
        if self._path.exists():
            os.chmod(self._path, 0o600)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)

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

            with _FORK_FD_GUARD:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                _track_lock_fd(fd)
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
                    with _FORK_FD_GUARD:
                        try:
                            _untrack_lock_fd(fd)
                        finally:
                            os.close(fd)

    def _read(self) -> list[dict]:
        try:
            contents = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        items = []
        for line in contents.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line (crash mid-write) must not brick the store
            if _valid_record(item):
                items.append(item)
        return items

    def _write_atomic(self, items: list[dict]) -> None:
        payload = "".join(json.dumps(item) + "\n" for item in items)
        fd, temp_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
            temp_path = None
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

    def _mutate(self, change):
        with self._mutation_lock():
            items = self._read()
            result = change(items)
            self._write_atomic(items)
            return result

    def add(self, text: str, duration_s: float) -> dict:
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

    def stats(self) -> dict:
        items = self._read()
        if not items:
            return {"total_words": 0, "dictations": 0, "avg_wpm": 0, "streak_days": 0}
        total_words = sum(i["words"] for i in items)
        total_time = sum(i["duration_s"] for i in items) or 1e-9
        days = set()
        for item in items:
            try:
                days.add(date.fromtimestamp(item["ts"]))
            except (OverflowError, OSError, ValueError):
                continue
        streak, d = 0, date.today()
        while d in days:
            streak += 1
            d -= timedelta(days=1)
        return {"total_words": total_words, "dictations": len(items),
                "avg_wpm": round(total_words / (total_time / 60)),
                "streak_days": streak}
