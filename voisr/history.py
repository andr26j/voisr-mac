import contextlib
import fcntl
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}
_OPEN_LOCK_FDS_GUARD = threading.Lock()
_OPEN_LOCK_FDS: set[int] = set()
_MUTATION_STATES = threading.local()


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _mutation_states() -> dict[str, tuple[int, int]]:
    states = getattr(_MUTATION_STATES, "locks", None)
    if states is None:
        states = {}
        _MUTATION_STATES.locks = states
    return states


def _track_lock_fd(fd: int) -> None:
    with _OPEN_LOCK_FDS_GUARD:
        _OPEN_LOCK_FDS.add(fd)


def _untrack_lock_fd(fd: int) -> None:
    with _OPEN_LOCK_FDS_GUARD:
        _OPEN_LOCK_FDS.discard(fd)


def _reset_locks_after_fork() -> None:
    global _PATH_LOCKS_GUARD, _PATH_LOCKS
    global _OPEN_LOCK_FDS_GUARD, _OPEN_LOCK_FDS, _MUTATION_STATES

    for fd in _OPEN_LOCK_FDS:
        try:
            os.close(fd)
        except OSError:
            pass
    _PATH_LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS = {}
    _OPEN_LOCK_FDS_GUARD = threading.Lock()
    _OPEN_LOCK_FDS = set()
    _MUTATION_STATES = threading.local()


os.register_at_fork(after_in_child=_reset_locks_after_fork)


class HistoryStore:
    """Append-only JSONL store of dictations with derived usage stats."""

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextlib.contextmanager
    def _mutation_lock(self):
        path_key = str(self.path)
        with _thread_lock_for(self.path):
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
                    try:
                        os.close(fd)
                    finally:
                        _untrack_lock_fd(fd)

    def _read(self) -> list[dict]:
        try:
            contents = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        items = []
        for line in contents.splitlines():
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn line (crash mid-write) must not brick the store
        return items

    def _write_atomic(self, items: list[dict]) -> None:
        payload = "".join(json.dumps(item) + "\n" for item in items)
        fd, temp_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
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
            os.replace(temp_path, self.path)
            temp_path = None
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
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
        days = {date.fromtimestamp(i["ts"]) for i in items}
        streak, d = 0, date.today()
        while d in days:
            streak += 1
            d -= timedelta(days=1)
        return {"total_words": total_words, "dictations": len(items),
                "avg_wpm": round(total_words / (total_time / 60)),
                "streak_days": streak}
