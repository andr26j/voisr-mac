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
from voisr.common_words import is_common_word
from voisr.suggestions import is_truncation, similarity

READ_AT_S = (3.0, 10.0, 30.0, 60.0)
RECHECK_S = 3.0
MAX_RECHECKS = 5
ANCHOR_CHARS = 40
AWAY_LIMIT_S = 30.0
REWRITE_SHARE = 0.5
MAX_CANDIDATES = 4
MAX_RUN_WORDS = 3
MIN_SIMILARITY = 0.6
_STRIP = ".,:;!?()[]{}\"'“”‘’…"
_MAX_PAIR_CHARS = 60
_SENTENCE_END = ".!?…"
MAX_WINDOWS = 8
# What a held read counts as if the window ends before its recheck.
_HELD_REWRITE = "rewrite"      # yields nothing, earlier reads included
_HELD_TRUNCATED = "truncated"  # may be half-typed: the read before it stands
_HELD_WHOLE = "whole"          # reads as finished: it stands


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


def _without_later_typing(heard: list[str], written: list[str]) -> list[str]:
    """A replaced run at the very end of a paste with nothing after it to anchor
    on can run on into what was typed next ("tomorow" -> "tomorrow at nine").
    Keep only as many leading words as read most like what was heard, so
    "openai" -> "Open AI" still stays whole."""
    heard_text = " ".join(_bare(w) for w in heard)
    prefixes = [written[:n] for n in range(1, len(written) + 1)]
    return max(prefixes, key=lambda p: similarity(heard_text, " ".join(_bare(w) for w in p)))


def _replaced_runs(pasted: str, final: str, open_end: bool):
    """(words replaced, (heard, written), set apart) for each replaced run. The
    pair is None for a run that can't be one correction: more than 1-3 words, or
    nothing left of a side once its punctuation is gone. open_end: nothing
    anchored the end of the span, so words past the paste may be later typing,
    not a correction; set apart says a sentence end divides them from it."""
    before, after = pasted.split(), final.split()
    for tag, i1, i2, j1, j2 in _opcodes(before, after):
        if tag != "replace":
            continue
        new_words = after[j1:j2]
        set_apart = True
        if open_end and i2 == len(before) and j2 == len(after):
            kept = _without_later_typing(before[i1:i2], new_words)
            set_apart = len(kept) == len(new_words) or kept[-1][-1] in _SENTENCE_END
            new_words = kept
        heard = " ".join(_bare(w) for w in before[i1:i2]).strip()
        written = " ".join(_bare(w) for w in new_words).strip()
        fits = (i2 - i1 <= MAX_RUN_WORDS and len(new_words) <= MAX_RUN_WORDS
                and heard and written and heard != written
                and len(heard) <= _MAX_PAIR_CHARS and len(written) <= _MAX_PAIR_CHARS)
        yield i2 - i1, (heard, written) if fits else None, set_apart


def _reads_alike(heard: str, written: str) -> bool:
    return (heard.casefold() == written.casefold()
            or similarity(heard, written) >= MIN_SIMILARITY)


def diff_candidates(pasted: str, final: str, context_terms=(),
                    limit: int = MAX_CANDIDATES, open_end: bool = False,
                    ) -> tuple[list[tuple[str, str]], int]:
    """([(heard, written)], words corrected). Each replaced run of 1-3 words
    that looks like a spelling fix; words corrected counts every replaced word
    whether or not it made a candidate."""
    terms = {t.casefold() for t in context_terms}
    candidates = []
    corrected = 0
    for replaced, pair, _ in _replaced_runs(pasted, final, open_end):
        corrected += replaced
        if pair is not None and (_reads_alike(*pair)
                                 or any(w.casefold() in terms for w in pair[1].split())):
            candidates.append(pair)
    return candidates[:limit], corrected


def _is_spelling_fix(heard: str, written: str) -> bool:
    """Reads alike, and isn't one ordinary word chosen over another ("Monday" ->
    "Sunday"). A change of case is never a choice of word."""
    return heard.casefold() == written.casefold() or is_term_fix(heard, written)


def is_term_fix(heard: str, written: str) -> bool:
    """Reads alike and isn't all ordinary words: worth saying out loud. "form" ->
    "from" or "the" -> "The" is everyday editing, and "Bob" -> "Alice" another
    name, however it got to be a candidate."""
    return (_reads_alike(heard, written)
            and not all(is_common_word(w) for w in f"{heard} {written}".split()))


def is_rewrite(pasted: str, final: str, open_end: bool = False) -> bool:
    """Most of what was pasted has gone, not counting words a spelling fix
    accounts for: "Tarek." -> "Tariq." is every word and still a correction. A
    word that is merely on screen doesn't count here, or a short reply replaced
    by one naming someone in view would be kept as a pair. Nor does a word that
    just runs on into a new sentence ("Tarek." -> "Tara is coming tomorrow")."""
    words = pasted.split()
    if not words:
        return False
    fixed = sum(replaced for replaced, pair, set_apart in _replaced_runs(pasted, final, open_end)
                if pair is not None and set_apart and _is_spelling_fix(*pair))
    return changed_share(pasted, final) - fixed / len(words) > REWRITE_SHARE


def _hold_as(pasted: str, final: str, last_final: str, open_end: bool,
             rewrite: bool) -> str | None:
    """How a read must wait for the recheck, or None when it can stand. Only a
    read that says something new about the dictation can be mid-typing: words
    typed after it can't."""
    if rewrite:
        return _HELD_REWRITE
    pairs = [pair for _, pair, _ in _replaced_runs(pasted, final, open_end)]
    if pairs == [pair for _, pair, _ in _replaced_runs(pasted, last_final, open_end)]:
        return None
    if any(pair is not None and is_truncation(*pair) for pair in pairs):
        return _HELD_TRUNCATED
    return _HELD_WHOLE


@dataclass
class EditResult:
    item_id: str | None
    reason: str
    pairs: list[tuple[str, str]]
    words_corrected: int


@dataclass
class _Ended:
    """A finished window, waiting for the watcher thread to work out and hand
    over its result. final is None when its reads yield nothing."""
    item_id: str | None
    reason: str
    pasted: str = field(default="", repr=False)
    final: str | None = field(default=None, repr=False)
    terms: tuple[str, ...] = field(default=(), repr=False)
    open_end: bool = False
    judge_final: bool = False  # final is the next paste's capture, not yet judged


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
    unsettled: str | None = field(default=None, repr=False)  # seen once, maybe mid-typing
    unsettled_as: str = _HELD_WHOLE
    rechecks: int = 0
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
        self._ended: list[_Ended] = []  # for the watcher thread to deliver
        self._thread = None
        self._stopped = False

    def watch(self, item_id, focus, element_id, pasted: str, terms=()) -> None:
        """Open a window. `focus` is the release capture, read for its anchors;
        `pasted` is the text as sent. A second paste into the same element ends
        the first window: its anchors no longer hold. Called on the dictation
        queue, so this only records it: the differ, the file writes, the word
        list and the config write all happen on the watcher thread."""
        before_anchor, after_anchor = anchors_from(focus)
        now = self._clock()
        window = _Window(item_id=item_id, element=focus.element, element_id=element_id,
                         bundle_id=focus.bundle_id, pasted=pasted.strip(),
                         before_anchor=before_anchor, after_anchor=after_anchor,
                         terms=tuple(terms), last_final=pasted.strip(), started=now)
        with self._cond:
            if self._stopped:
                return
            for other in list(self._windows):
                if other.element_id == element_id and not other.done:
                    self._ended.append(self._finish_locked(
                        other, "next_paste", closing=self._closing_read(other, focus)))
            while len(self._windows) >= MAX_WINDOWS:
                self._ended.append(self._finish_locked(self._windows[0], "too_many"))
            self._windows.append(window)
            self._schedule_locked(window)
            if self._autostart and self._thread is None:
                self._thread = threading.Thread(target=self._run, name="voisr-edit-watch",
                                                daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def end_for_snippet(self, focus) -> None:
        """A snippet is about to land. It must never read as an edit, so windows
        on its field end now, on what they have seen so far and the release
        capture, which was taken before it. Not knowing the field ends them all."""
        known = focus.element_id if focus is not None else None
        with self._cond:
            for other in list(self._windows):
                if not other.done and known in (None, other.element_id):
                    self._ended.append(self._finish_locked(
                        other, "next_paste",
                        closing=self._closing_read(other, focus) if known else None))
            self._cond.notify_all()

    def _schedule_locked(self, window: _Window) -> None:
        due = window.started + READ_AT_S[window.next_read]
        heapq.heappush(self._heap, (due, next(self._order), window))

    @staticmethod
    def _closing_read(window: _Window, focus) -> str | None:
        """The span as the next dictation's release capture has it: a later read
        than the schedule's, taken when the user had stopped typing. None when
        it can't be had. An empty one proves nothing: some apps report "" for a
        field that holds text."""
        if (focus.element_id != window.element_id or focus.secure
                or not focus.readable or not focus.caret_known):
            return None
        value = focus.before_text + focus.selected_text + focus.after_text
        span = locate_span(value, window.before_anchor, window.after_anchor,
                           len(window.pasted))
        return (value[span[0]:span[1]].strip() or None) if span else None

    def _finish_locked(self, window: _Window, reason: str, *, keep: bool = True,
                       closing: str | None = None) -> _Ended:
        window.done = True
        if window in self._windows:
            self._windows.remove(window)
        if keep and closing is None and window.unsettled is not None:
            if window.unsettled_as == _HELD_REWRITE:
                reason, keep = "rewrite", False
            elif window.unsettled_as == _HELD_WHOLE:
                window.last_final = window.unsettled
        result = _Ended(window.item_id, reason)
        if keep and window.last_final is not None:
            result.pasted, result.final = window.pasted, window.last_final
            result.terms = window.terms
            result.open_end = not window.after_anchor
            if closing is not None:
                result.final, result.judge_final = closing, True
        # the text goes now; the object may sit in the heap until its next due
        window.pasted = window.last_final = window.before_anchor = window.after_anchor = ""
        window.unsettled = None
        window.terms = ()
        window.element = None
        return result

    def _deliver(self, ended: _Ended) -> None:
        try:
            pairs, corrected = [], 0
            if ended.judge_final and is_rewrite(ended.pasted, ended.final, ended.open_end):
                ended.reason, ended.final = "rewrite", None
            if ended.final is not None:
                pairs, corrected = diff_candidates(ended.pasted, ended.final, ended.terms,
                                                   open_end=ended.open_end)
            self._on_end(EditResult(ended.item_id, ended.reason, pairs, corrected))
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
        open_end = not window.after_anchor
        rewrite = is_rewrite(window.pasted, final, open_end)
        if rewrite and len(_bare(final)) >= len(_bare(window.pasted)):
            return "rewrite"
        # the same text on the recheck means they stopped there; until then it
        # neither ends the window nor replaces the last good read
        hold = None if final == window.unsettled else _hold_as(
            window.pasted, final, window.last_final, open_end, rewrite)
        if rewrite and hold is None:
            return "rewrite"
        with self._cond:
            # this ran outside the lock: a window finished meanwhile keeps no text
            if not window.done:
                if hold is None:
                    window.unsettled, window.last_final = None, final
                else:
                    window.unsettled, window.unsettled_as = final, hold
        return None

    @staticmethod
    def _has_more_reads(window: _Window) -> bool:
        return window.next_read + 1 < len(READ_AT_S)

    def tick(self, now: float | None = None) -> None:
        """Run everything due. The thread calls this; so do tests."""
        now = self._clock() if now is None else now
        self._deliver_ended()
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
                    if window.unsettled is not None and window.rechecks < MAX_RECHECKS:
                        # a held read stays this scheduled read's until it settles
                        window.rechecks += 1
                        heapq.heappush(self._heap, (now + RECHECK_S, next(self._order), window))
                        continue
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

    def _deliver_ended(self) -> None:
        with self._cond:
            ended, self._ended = self._ended, []
        for result in ended:
            self._deliver(result)

    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stopped:
                    return
                wait = 3600.0
                if self._ended:
                    wait = 0.0
                elif self._heap:
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
            self._ended.clear()
            self._cond.notify_all()
