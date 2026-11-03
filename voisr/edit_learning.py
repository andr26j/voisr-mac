"""Watch a pasted span for a minute and work out what the user corrected.

Re-reads the field on a schedule; no keystrokes are involved. All text here
lives in the window object, in memory, and goes when the window ends. The only
things that come out are {heard, written} pairs and a word count.
"""
import difflib
import heapq
import itertools
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

from voisr.ax_context import (AXUnavailable, DEFAULT_BUDGET_S, EDITABLE_ROLES,
                              read_textbox)
from voisr.suggestions import similarity

READ_AT_S = (3.0, 10.0, 30.0, 60.0)
ANCHOR_CHARS = 40
AWAY_LIMIT_S = 30.0
REWRITE_SHARE = 0.5
MAX_CANDIDATES = 4
MAX_RUN_WORDS = 3
MIN_SIMILARITY = 0.6
_STRIP = ".,:;!?()[]{}\"'“”‘’…"
_MAX_PAIR_CHARS = 60
MAX_WINDOWS = 8


def locate_span(value: str, before_anchor: str, after_anchor: str,
                expected_len: int) -> tuple[int, int] | None:
    """Where the pasted text sits now, between what was around it at release.
    Anchors repeat in real text, so the pair closest to the pasted length
    wins. None when an anchor has gone."""
    starts = []
    if before_anchor:
        at = value.find(before_anchor)
        while at != -1:
            starts.append(at + len(before_anchor))
            at = value.find(before_anchor, at + 1)
        if not starts:
            return None
    else:
        starts = [0]
    ends = []
    if after_anchor:
        at = value.find(after_anchor)
        while at != -1:
            ends.append(at)
            at = value.find(after_anchor, at + 1)
        if not ends:
            return None
    else:
        ends = [len(value)]
    pairs = [(s, e) for s in starts for e in ends if s <= e]
    if not pairs:
        return None
    return min(pairs, key=lambda p: abs((p[1] - p[0]) - expected_len))


def _bare(word: str) -> str:
    return word.strip(_STRIP)


def _opcodes(pasted: list[str], final: list[str]):
    # punctuation alone is not a correction
    return difflib.SequenceMatcher(
        None, [_bare(w) for w in pasted], [_bare(w) for w in final],
        autojunk=False).get_opcodes()


def changed_share(pasted: str, final: str) -> float:
    """How much of what was pasted has changed. Typing more after it doesn't
    count: that is not a rewrite of the dictation."""
    words = pasted.split()
    if not words:
        return 0.0
    changed = sum(i2 - i1 for tag, i1, i2, _, _ in _opcodes(words, final.split())
                  if tag in ("replace", "delete"))
    return changed / len(words)


def diff_candidates(pasted: str, final: str, context_terms=(),
                    limit: int = MAX_CANDIDATES) -> tuple[list[tuple[str, str]], int]:
    """([(heard, written)], words corrected). Each replaced run of 1-3 words
    that looks like a spelling fix; words corrected counts every replaced word
    whether or not it made a candidate."""
    before, after = pasted.split(), final.split()
    terms = {t.casefold() for t in context_terms}
    candidates = []
    corrected = 0
    for tag, i1, i2, j1, j2 in _opcodes(before, after):
        if tag != "replace":
            continue
        corrected += i2 - i1
        if i2 - i1 > MAX_RUN_WORDS or j2 - j1 > MAX_RUN_WORDS:
            continue
        heard = " ".join(_bare(w) for w in before[i1:i2]).strip()
        written = " ".join(_bare(w) for w in after[j1:j2]).strip()
        if not heard or not written or heard == written:
            continue
        if len(heard) > _MAX_PAIR_CHARS or len(written) > _MAX_PAIR_CHARS:
            continue
        if (heard.casefold() == written.casefold()
                or similarity(heard, written) >= MIN_SIMILARITY
                or any(_bare(w).casefold() in terms for w in after[j1:j2])):
            candidates.append((heard, written))
    return candidates[:limit], corrected


@dataclass
class EditResult:
    item_id: str | None
    reason: str
    pairs: list[tuple[str, str]]
    words_corrected: int


@dataclass
class _Window:
    item_id: str | None
    element: Any = field(repr=False)
    element_id: str = ""
    bundle_id: str | None = None
    pasted: str = field(default="", repr=False)
    before_anchor: str = field(default="", repr=False)
    after_anchor: str = field(default="", repr=False)
    terms: tuple[str, ...] = field(default=(), repr=False)
    last_final: str | None = field(default=None, repr=False)
    started: float = 0.0
    next_read: int = 0
    away_since: float | None = None
    done: bool = False


def anchors_from(focus) -> tuple[str, str]:
    """Up to 40 chars either side of where the paste went, from the release
    capture."""
    return focus.before_text[-ANCHOR_CHARS:], focus.after_text[:ANCHOR_CHARS]


class EditWatcher:
    """One thread and a schedule, however many windows are open. Reads go
    through the AX worker with a bound, so a hung app costs a skipped read.
    `enabled` is asked before every read: switching the setting off stops a
    window already open."""

    def __init__(self, adapter, worker, enabled: Callable[[], bool],
                 on_end: Callable[[EditResult], None],
                 clock: Callable[[], float] = time.monotonic,
                 budget_s: float = DEFAULT_BUDGET_S, autostart: bool = True):
        self._adapter = adapter
        self._worker = worker
        self._enabled = enabled
        self._on_end = on_end
        self._clock = clock
        self._budget_s = budget_s
        self._autostart = autostart
        self._cond = threading.Condition()
        self._heap: list = []
        self._order = itertools.count()
        self._windows: list[_Window] = []
        self._thread = None
        self._stopped = False

    def watch(self, item_id, focus, element_id, pasted: str, terms=()) -> None:
        """Open a window. `focus` is the release capture, read for its anchors;
        `pasted` is the text as sent. A second paste into the same element ends
        the first window: its anchors no longer hold."""
        before_anchor, after_anchor = anchors_from(focus)
        now = self._clock()
        window = _Window(item_id=item_id, element=focus.element, element_id=element_id,
                         bundle_id=focus.bundle_id, pasted=pasted.strip(),
                         before_anchor=before_anchor, after_anchor=after_anchor,
                         terms=tuple(terms), last_final=pasted.strip(), started=now)
        ended = []
        with self._cond:
            if self._stopped:
                return
            for other in self._windows:
                if other.element_id == element_id and not other.done:
                    ended.append(self._finish_locked(other, "next_paste"))
            while len(self._windows) >= MAX_WINDOWS:
                ended.append(self._finish_locked(self._windows[0], "too_many"))
            self._windows.append(window)
            self._schedule_locked(window)
            if self._autostart and self._thread is None:
                self._thread = threading.Thread(target=self._run, name="voisr-edit-watch",
                                                daemon=True)
                self._thread.start()
            self._cond.notify_all()
        for result in ended:
            self._deliver(result)

    def _schedule_locked(self, window: _Window) -> None:
        due = window.started + READ_AT_S[window.next_read]
        heapq.heappush(self._heap, (due, next(self._order), window))

    def _finish_locked(self, window: _Window, reason: str, *, keep: bool = True) -> EditResult:
        window.done = True
        if window in self._windows:
            self._windows.remove(window)
        pairs, corrected = [], 0
        if keep and window.last_final is not None:
            pairs, corrected = diff_candidates(window.pasted, window.last_final, window.terms)
        result = EditResult(window.item_id, reason, pairs, corrected)
        # the text goes now; the object may sit in the heap until its next due
        window.pasted = window.last_final = window.before_anchor = window.after_anchor = ""
        window.terms = ()
        window.element = None
        return result

    def _deliver(self, result: EditResult) -> None:
        try:
            self._on_end(result)
        except Exception as error:
            print(f"Voisr: edit learning skipped ({error.__class__.__name__})", flush=True)

    def _read(self, element):
        """The field's fields, or None when it couldn't be had in time."""
        started = self._clock()

        def job():
            def remaining():
                return started + self._budget_s - self._clock()
            return read_textbox(self._adapter, element, remaining)

        future = self._worker.submit(job)
        try:
            return future.result(timeout=self._budget_s)
        except (FutureTimeout, AXUnavailable):
            future.cancel()
            return None
        except Exception:
            return None

    def _step(self, window: _Window, now: float) -> str | None:
        """One scheduled read. Returns why the window ended, or None."""
        if not self._enabled():
            return "setting_off"
        try:
            bundle_id = self._adapter.frontmost()[0]
        except Exception:
            bundle_id = window.bundle_id
        if bundle_id != window.bundle_id:
            if window.away_since is None:
                window.away_since = now
            elif now - window.away_since > AWAY_LIMIT_S:
                return "focus_moved"
        else:
            window.away_since = None
        fields = self._read(window.element)
        if fields is None or fields.get("reason") == "timeout":
            return None if self._has_more_reads(window) else "unreadable"
        if fields.get("secure"):
            return "secure"
        if not fields.get("readable") or fields.get("role") not in EDITABLE_ROLES:
            # unreadable is not empty: it may read again, and it proves nothing
            return None if self._has_more_reads(window) else "unreadable"
        value = fields["value"]
        if not value.strip():
            return "field_emptied"
        span = locate_span(value, window.before_anchor, window.after_anchor,
                           len(window.pasted))
        if span is None:
            return "anchor_lost"
        final = value[span[0]:span[1]].strip()
        if changed_share(window.pasted, final) > REWRITE_SHARE:
            return "rewrite"
        window.last_final = final
        return None

    @staticmethod
    def _has_more_reads(window: _Window) -> bool:
        return window.next_read + 1 < len(READ_AT_S)

    def tick(self, now: float | None = None) -> None:
        """Run everything due. The thread calls this; so do tests."""
        now = self._clock() if now is None else now
        while True:
            with self._cond:
                if not self._heap or self._heap[0][0] > now:
                    return
                _, _, window = heapq.heappop(self._heap)
                if window.done:
                    continue
            reason = self._step(window, now)
            with self._cond:
                if window.done:
                    continue
                if reason is None:
                    window.next_read += 1
                    if window.next_read >= len(READ_AT_S):
                        reason = "window_complete"
                    else:
                        self._schedule_locked(window)
                        continue
                # a rewrite is the user's own wording, and off means off: their
                # earlier reads yield nothing either
                result = self._finish_locked(
                    window, reason, keep=reason not in ("rewrite", "setting_off", "secure"))
            self._deliver(result)

    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stopped:
                    return
                wait = 3600.0
                if self._heap:
                    wait = max(0.0, self._heap[0][0] - self._clock())
                if wait > 0:
                    self._cond.wait(wait)
                    if self._stopped:
                        return
            try:
                self.tick()
            except Exception as error:
                print(f"Voisr: edit watch error ({error.__class__.__name__})", flush=True)

    def stop(self) -> None:
        """Quit: open windows are dropped, along with their text."""
        with self._cond:
            self._stopped = True
            for window in list(self._windows):
                self._finish_locked(window, "quit", keep=False)
            self._heap.clear()
            self._cond.notify_all()
