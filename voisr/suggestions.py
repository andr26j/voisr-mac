"""Corrected word pairs learned from edits, and what became of each one.

data/suggestions.json holds only {heard, written} pairs, their status and how
often they were seen, plus a use count per dictionary entry. Never any of the
text around them. Atomic and locked, like history.
"""
import difflib
import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from voisr.common_words import is_common_word, is_everyday_lowercase_word

AUTO_ADD_MIN_SEEN = 2
AUTO_ADD_SIMILARITY = 0.75
_MIN_TERM = 3
# Letters only, with the joiners a name can have. A digit anywhere rules it out.
_TERM = re.compile(r"[^\W\d_](?:[^\W\d_'’.-]|['’.-][^\W\d_])*")
_MAX_PENDING = 200
_MAX_CLOSED = 2000

PENDING = "pending"
DISMISSED = "dismissed"  # also: removed from the dictionary by the user
AUTO_ADDED = "auto_added"
ACCEPTED = "accepted"


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.casefold(), b.casefold(), autojunk=False).ratio()


def is_truncation(heard: str, written: str) -> bool:
    """Written is only the start of what was heard: "Tarek" -> "Tar", which may
    be a word left half-typed."""
    return len(written) < len(heard) and heard.casefold().startswith(written.casefold())


def auto_add_kind(heard: str, written: str, *, common=is_common_word,
                  everyday=is_everyday_lowercase_word) -> str | None:
    """Why this pair is safe to add unasked ("capitalization" or "spelling"),
    or None. The cheap checks run first: the last one reads a file."""
    if heard == written:
        return None
    for term in (heard, written):
        if len(term) < _MIN_TERM or _TERM.fullmatch(term) is None:
            return None
    if heard.casefold() == written.casefold():
        # adding capitals only: "Voisr" -> "voisr" is a house style or a
        # sentence start, not a term
        if sum(c.isupper() for c in written) <= sum(c.isupper() for c in heard):
            return None
        kind = "capitalization"
    elif is_truncation(heard, written):
        return None
    elif similarity(heard, written) >= AUTO_ADD_SIMILARITY:
        kind = "spelling"
    else:
        return None
    if common(heard) or common(written) or everyday(heard):
        return None
    return kind


def ready_to_auto_add(pair: "Pair", **checks) -> bool:
    """The rules about the pair itself. May read the system dictionary, so
    this runs outside the config lock."""
    return (pair.status == PENDING and pair.seen >= AUTO_ADD_MIN_SEEN
            and auto_add_kind(pair.heard, pair.written, **checks) is not None)


def dictionary_allows(pair: "Pair", dictionary: dict) -> bool:
    """A pair a dictionary entry already covers, or that turns one of its
    written forms into something else (undoing a replacement), is the user's
    business."""
    return (pair.key not in {k.casefold() for k in dictionary}
            and pair.heard.casefold() not in {v.casefold() for v in dictionary.values()})


def may_auto_add(pair: "Pair", dictionary: dict, **checks) -> bool:
    """Every rule from the spec's D."""
    return dictionary_allows(pair, dictionary) and ready_to_auto_add(pair, **checks)


@dataclass(frozen=True)
class Pair:
    id: str
    heard: str
    written: str
    seen: int = 1
    status: str = PENDING
    last_seen: float = 0.0

    @property
    def key(self) -> str:
        """Its dictionary key: entries match case-insensitively."""
        return self.heard.casefold()

    def to_json(self) -> dict:
        return {"id": self.id, "heard": self.heard, "written": self.written,
                "seen": self.seen, "status": self.status, "last_seen": self.last_seen}


def pair_id(heard: str, written: str) -> str:
    return hashlib.sha256(f"{heard.casefold()}\0{written}".encode()).hexdigest()[:16]


def _valid(raw) -> bool:
    return (isinstance(raw, dict)
            and all(isinstance(raw.get(k), str) for k in ("id", "heard", "written", "status"))
            and isinstance(raw.get("seen"), int)
            and raw["status"] in (PENDING, DISMISSED, AUTO_ADDED, ACCEPTED))


class SuggestionStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._thread_lock = threading.RLock()

    def _locked(self):
        store = self

        class Guard:
            def __enter__(self):
                store._thread_lock.acquire()
                try:
                    store.path.parent.mkdir(parents=True, exist_ok=True)
                    self.fd = os.open(store._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                    fcntl.flock(self.fd, fcntl.LOCK_EX)
                except BaseException:
                    store._thread_lock.release()
                    raise

            def __exit__(self, *exc):
                try:
                    os.close(self.fd)  # drops the flock too
                finally:
                    store._thread_lock.release()

        return Guard()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_bytes().decode("utf-8"))
        except FileNotFoundError:
            return {"pairs": [], "used": {}}
        except OSError:
            raise
        except ValueError:
            # keep it aside: losing the dismissed list would re-suggest them
            try:
                os.replace(self.path, f"{self.path}.corrupt-{int(time.time())}")
            except OSError:
                pass
            return {"pairs": [], "used": {}}
        if not isinstance(data, dict):
            return {"pairs": [], "used": {}}
        pairs = [p for p in data.get("pairs", []) if _valid(p)] \
            if isinstance(data.get("pairs"), list) else []
        used = data.get("used")
        used = {k: v for k, v in used.items() if isinstance(k, str) and isinstance(v, int)} \
            if isinstance(used, dict) else {}
        return {"pairs": pairs, "used": used}

    def _write(self, data: dict) -> None:
        pending = [p for p in data["pairs"] if p["status"] == PENDING]
        closed = [p for p in data["pairs"] if p["status"] != PENDING]
        pending.sort(key=lambda p: p.get("last_seen", 0))
        data = {**data, "pairs": closed[-_MAX_CLOSED:] + pending[-_MAX_PENDING:]}
        payload = json.dumps(data, indent=1)
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp",
                                   dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    @staticmethod
    def _pair(raw: dict) -> Pair:
        return Pair(raw["id"], raw["heard"], raw["written"], raw["seen"],
                    raw["status"], raw.get("last_seen", 0.0))

    def observe(self, pairs) -> list[Pair]:
        """One dictation's corrected pairs, each counted once however often it
        recurs. Returns the stored state of each, closed ones untouched:
        a dismissed pair stays dismissed however often it comes back."""
        unique = list(dict.fromkeys((h, w) for h, w in pairs))
        if not unique:
            return []
        now = time.time()
        out = []
        with self._locked():
            data = self._read()
            by_id = {p["id"]: p for p in data["pairs"]}
            for heard, written in unique:
                pid = pair_id(heard, written)
                raw = by_id.get(pid)
                if raw is None:
                    raw = Pair(pid, heard, written, 1, PENDING, now).to_json()
                    data["pairs"].append(raw)
                    by_id[pid] = raw
                elif raw["status"] == PENDING:
                    raw["seen"] += 1
                    raw["last_seen"] = now
                out.append(self._pair(raw))
            self._write(data)
        return out

    def pending(self) -> list[Pair]:
        if not self.path.exists():
            return []
        with self._locked():
            pairs = [self._pair(p) for p in self._read()["pairs"] if p["status"] == PENDING]
        return sorted(pairs, key=lambda p: p.last_seen, reverse=True)

    def get(self, pid: str) -> Pair | None:
        if not self.path.exists():
            return None
        with self._locked():
            for raw in self._read()["pairs"]:
                if raw["id"] == pid:
                    return self._pair(raw)
        return None

    def set_status(self, pid: str, status: str) -> bool:
        if not self.path.exists():
            return False
        with self._locked():
            data = self._read()
            for raw in data["pairs"]:
                if raw["id"] == pid:
                    raw["status"] = status
                    self._write(data)
                    return True
        return False

    def auto_added(self) -> dict[str, str]:
        """{dictionary key: written} of what is in the dictionary unasked."""
        if not self.path.exists():
            return {}
        with self._locked():
            return {self._pair(p).key: p["written"] for p in self._read()["pairs"]
                    if p["status"] == AUTO_ADDED}

    def dictionary_changed(self, old: dict, new: dict) -> None:
        """Remember what the user did in the Dictionary view, whichever way
        they did it. An entry they removed or changed is theirs: it isn't
        learned back. One they added themselves stays, even if it was dismissed
        before (Undo after a remove comes through here too)."""
        removed = {k.casefold(): v for k, v in old.items() if new.get(k) != v}
        added = {k.casefold(): v for k, v in new.items() if old.get(k) != v}
        if (not removed and not added) or not self.path.exists():
            return
        with self._locked():
            data = self._read()
            touched = False
            for raw in data["pairs"]:
                key = raw["heard"].casefold()
                status = raw["status"]
                if added.get(key) == raw["written"] and status != ACCEPTED:
                    raw["status"] = ACCEPTED
                    touched = True
                elif (removed.get(key) == raw["written"]
                      and status in (AUTO_ADDED, ACCEPTED)):
                    raw["status"] = DISMISSED
                    touched = True
            if touched:
                self._write(data)

    def bump_used(self, counts: dict[str, int]) -> None:
        if not counts:
            return
        with self._locked():
            data = self._read()
            for key, n in counts.items():
                data["used"][key.casefold()] = data["used"].get(key.casefold(), 0) + n
            self._write(data)

    def used(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        with self._locked():
            return self._read()["used"]
