"""Component D: the span anchor, the differ, the auto-add rules, the store and
the observation window."""
import json

import pytest

from tests.fake_ax import FakeAX, textbox
from voisr.ax_context import FocusContext
from voisr.edit_learning import (EditWatcher, READ_AT_S, changed_share, diff_candidates,
                                 locate_span)
from voisr.suggestions import (ACCEPTED, AUTO_ADDED, DISMISSED, PENDING, Pair,
                               SuggestionStore, auto_add_kind, may_auto_add, pair_id)


@pytest.mark.parametrize("value, before, after, length, expected", [
    ("hello there world", "hello ", " world", 5, (6, 11)),
    ("hello there", "hello ", "", 5, (6, 11)),
    ("there world", "", " world", 5, (0, 5)),
    ("there", "", "", 5, (0, 5)),
    ("x there x", "gone", "", 5, None),
    ("x there x", "", "gone", 5, None),
    # after before before: not a span
    ("b a", "a", "b", 1, None),
    # a repeated anchor: the span closest to the pasted length wins
    ("a x a hello b y b", "a ", " b", 5, (6, 11)),
    # edited longer than pasted, still found
    ("start Voisr rocks end", "start ", " end", 5, (6, 17)),
])
def test_span_anchor(value, before, after, length, expected):
    assert locate_span(value, before, after, length) == expected


@pytest.mark.parametrize("pasted, final, terms, pairs, corrected", [
    ("hello world", "hello world", (), [], 0),
    ("I use voiser daily", "I use Voisr daily", (), [("voiser", "Voisr")], 1),
    ("on github now", "on GitHub now", (), [("github", "GitHub")], 1),
    # punctuation is not a correction, and is stripped from a real one
    ("hi there", "hi there!", (), [], 0),
    ("say voiser, ok", "say Voisr, ok", (), [("voiser", "Voisr")], 1),
    # a two-word run
    ("the trade mate app", "the TradeMate app", (), [("trade mate", "TradeMate")], 2),
    # no resemblance and no context term: not a spelling fix
    ("meet on Tuesday", "meet on Friday", (), [], 1),
    ("call the clod now", "call the Anthropic now", (), [], 1),
    # no resemblance, but it is on screen
    ("call the clod now", "call the Anthropic now", ("Anthropic",),
     [("clod", "Anthropic")], 1),
    # four words is more than a spelling fix
    ("a b c d e", "a v w x y", (), [], 4),
    # insertions and deletions aren't corrections
    ("hello world", "hello big world", (), [], 0),
    ("hello big world", "hello world", (), [], 0),
])
def test_edit_differ(pasted, final, terms, pairs, corrected):
    assert diff_candidates(pasted, final, terms) == (pairs, corrected)


@pytest.mark.parametrize("pasted, final, pairs", [
    # a fix to the last word, then more typing: the typing is not the fix
    ("see you tomorow", "see you tomorrow at nine", [("tomorow", "tomorrow")]),
    ("ask voiser", "ask Voisr about the plan today and more", [("voiser", "Voisr")]),
    # the correction itself may be two words
    ("use openai", "use Open AI", [("openai", "Open AI")]),
    ("use openai", "use Open AI for this", [("openai", "Open AI")]),
    # plain typing after an untouched paste
    ("see you soon", "see you soon at nine", []),
    # the last word replaced by something else altogether: nothing to learn
    ("see you tomorow", "see you Friday at nine", []),
])
def test_later_typing_never_forms_a_candidate(pasted, final, pairs):
    assert diff_candidates(pasted, final, open_end=True)[0] == pairs


def test_with_an_anchored_end_the_whole_run_is_the_correction():
    assert diff_candidates("the trade mate", "the Trade Mate", open_end=False)[0] == [
        ("trade mate", "Trade Mate")]
    # text after it was there before the paste, so this all sits inside the span
    assert diff_candidates("see tomorow", "see tomorrow at nine", open_end=False)[0] == [
        ("tomorow", "tomorrow at nine")]


def test_a_window_with_no_text_after_the_paste_is_open_ended():
    box = textbox("start see you tomorow")
    results = []

    class Worker:
        def submit(self, fn):
            from concurrent.futures import Future
            future = Future()
            future.set_result(fn())
            return future

    now = [0.0]
    watcher = EditWatcher(FakeAX(box), Worker(), lambda: True, results.append,
                          clock=lambda: now[0], budget_s=1.0, autostart=False)
    focus = FocusContext(bundle_id="com.example.app", before_text="start ", after_text="",
                         element=box, element_id=box.id, caret_known=True)
    watcher.watch("i", focus, box.id, "see you tomorow", ())
    box.attrs["AXValue"] = "start see you tomorrow at nine"
    for t in READ_AT_S:
        now[0] = t
        watcher.tick()
    assert results[0].pairs == [("tomorow", "tomorrow")]


def test_at_most_four_candidates():
    pasted = "voiser one github two brunswik three kestrelwey four zorblaks"
    final = "Voisr one GitHub two Brunswick three Kestrelway four Zorblax"
    pairs, corrected = diff_candidates(pasted, final)
    assert len(pairs) == 4 and corrected == 5


@pytest.mark.parametrize("pasted, final, share", [
    ("one two three four", "one two three four", 0.0),
    ("one two three four", "one two three four and lots more typing here", 0.0),
    ("one two three four", "one two three", 0.25),
    ("one two three four", "a b c d", 1.0),
])
def test_changed_share_counts_only_pasted_words(pasted, final, share):
    assert changed_share(pasted, final) == share


# common: a bundled-list stand-in; everyday: the system dictionary stand-in
_COMMON = {"tuesday", "thursday", "two", "the", "their", "there", "form", "from",
           "march", "receive", "apple"}
_EVERYDAY = {"clod", "voicer", "march", "apple"}


def kind(heard, written):
    return auto_add_kind(heard, written, common=lambda w: w.lower() in _COMMON,
                         everyday=lambda w: w.lower() in _EVERYDAY)


@pytest.mark.parametrize("heard, written, expected", [
    # the safe kinds
    ("github", "GitHub", "capitalization"),
    ("Github", "GitHub", "capitalization"),
    ("brunswick", "Brunswick", "capitalization"),
    ("voiser", "Voisr", "spelling"),
    ("brunswik", "Brunswick", "spelling"),
    ("kestrelwey", "Kestrelway", "spelling"),
    ("o'brian", "O'Brien", "spelling"),
    # two valid common words, however close: the owner's own example
    ("Tuesday", "Thursday", None),
    ("their", "there", None),
    ("form", "from", None),
    # common words, in either position
    ("march", "March", None),
    ("the", "The", None),
    ("recieve", "receive", None),
    ("apple", "Appel", None),
    # an everyday word is rewritten everywhere it appears
    ("clod", "Clode", None),
    ("voicer", "Voisr", None),
    # numbers
    ("two", "2", None),
    ("gpt4", "GPT4", None),
    ("100", "1000", None),
    ("v2", "v3", None),
    # not a single term
    ("trade mate", "TradeMate", None),
    ("open ai", "Open AI", None),
    # too different
    ("clode", "Anthropic", None),
    # lowering is style, not a term
    ("Voisr", "voisr", None),
    ("GITHUB", "github", None),
    # too short, and symbols
    ("ab", "AB", None),
    ("foo@bar.com", "Foo@bar.com", None),
    ("a/b/c", "a/b/d", None),
    ("same", "same", None),
])
def test_auto_add_rules(heard, written, expected):
    assert kind(heard, written) == expected


def pair(heard="voiser", written="Voisr", seen=2, status=PENDING):
    return Pair(pair_id(heard, written), heard, written, seen, status)


CHECKS = {"common": lambda w: False, "everyday": lambda w: False}


@pytest.mark.parametrize("candidate, dictionary, expected", [
    (pair(seen=2), {}, True),
    (pair(seen=5), {"other": "Thing"}, True),
    (pair(seen=1), {}, False),                         # seen once
    (pair(status=DISMISSED), {}, False),               # dismissed or removed
    (pair(status=ACCEPTED), {}, False),
    (pair(status=AUTO_ADDED), {}, False),
    (pair(), {"voiser": "Voicer"}, False),             # already has an entry
    (pair(), {"VOISER": "Voicer"}, False),
    # undoing a replacement must not start a loop
    (pair(heard="Voisr", written="voisrr"), {"voicer": "Voisr"}, False),
])
def test_may_auto_add(candidate, dictionary, expected):
    assert may_auto_add(candidate, dictionary, **CHECKS) is expected


def test_store_counts_a_pair_once_per_dictation(tmp_path):
    store = SuggestionStore(tmp_path / "s.json")
    pairs = store.observe([("voiser", "Voisr"), ("voiser", "Voisr")])
    assert [p.seen for p in pairs] == [1]
    pairs = store.observe([("voiser", "Voisr")])
    assert [p.seen for p in pairs] == [2]
    assert store.pending()[0].seen == 2


def test_dismissed_stays_dismissed(tmp_path):
    store = SuggestionStore(tmp_path / "s.json")
    pid = store.observe([("voiser", "Voisr")])[0].id
    assert store.set_status(pid, DISMISSED)
    again = store.observe([("voiser", "Voisr")])[0]
    assert again.status == DISMISSED and again.seen == 1
    assert store.pending() == []


def test_store_keeps_only_the_pair_and_is_private(tmp_path):
    path = tmp_path / "s.json"
    store = SuggestionStore(path)
    store.observe([("voiser", "Voisr")])
    store.bump_used({"voiser": 2})
    data = json.loads(path.read_text())
    assert set(data) == {"pairs", "used"}
    assert set(data["pairs"][0]) == {"id", "heard", "written", "seen", "status", "last_seen"}
    assert data["used"] == {"voiser": 2}
    assert path.stat().st_mode & 0o077 == 0


def test_a_corrupt_store_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{nope")
    store = SuggestionStore(path)
    assert store.pending() == []
    assert list(tmp_path.glob("s.json.corrupt-*"))


def test_store_ignores_bad_records(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"pairs": [{"id": 1}, "x"], "used": {"a": "b"}}))
    store = SuggestionStore(path)
    assert store.pending() == [] and store.used() == {}


@pytest.mark.parametrize("status, old, new, after", [
    # removed by the user: never again
    (AUTO_ADDED, {"voiser": "Voisr"}, {}, DISMISSED),
    (ACCEPTED, {"voiser": "Voisr"}, {}, DISMISSED),
    # edited: their entry now, and not this pair
    (AUTO_ADDED, {"voiser": "Voisr"}, {"voiser": "VoisR"}, DISMISSED),
    # added back by hand, or Undo
    (DISMISSED, {}, {"voiser": "Voisr"}, ACCEPTED),
    (PENDING, {}, {"voiser": "Voisr"}, ACCEPTED),
    # unrelated changes leave it alone
    (AUTO_ADDED, {"voiser": "Voisr"}, {"voiser": "Voisr", "a": "B"}, AUTO_ADDED),
    (PENDING, {"a": "B"}, {}, PENDING),
])
def test_dictionary_changes_set_status(tmp_path, status, old, new, after):
    store = SuggestionStore(tmp_path / "s.json")
    pid = store.observe([("voiser", "Voisr")])[0].id
    store.set_status(pid, status)
    store.dictionary_changed(old, new)
    assert store.get(pid).status == after


def make_watcher(box, terms=(), enabled=lambda: True, ax=None):
    ax = ax or FakeAX(box)
    results = []
    now = [0.0]

    class Worker:
        def submit(self, fn):
            from concurrent.futures import Future
            future = Future()
            try:
                future.set_result(fn())
            except Exception as error:
                future.set_exception(error)
            return future

    watcher = EditWatcher(ax, Worker(), enabled, results.append, clock=lambda: now[0],
                          budget_s=1.0, autostart=False)
    before_text = "start "
    focus = FocusContext(bundle_id=ax.bundle_id, before_text=before_text, after_text=" end",
                         element=box, element_id=box.id, caret_known=True)
    watcher.watch("item1", focus, box.id, "voiser is great", terms)

    def run_to(t):
        now[0] = t
        watcher.tick()

    return watcher, results, run_to, ax


def test_window_uses_the_last_read_and_reports_at_the_end():
    box = textbox("start voiser is great end")
    _, results, run_to, _ = make_watcher(box)
    run_to(READ_AT_S[0])
    box.attrs["AXValue"] = "start Voisr is great end"
    run_to(READ_AT_S[1])
    run_to(READ_AT_S[2])
    assert results == []
    run_to(READ_AT_S[3])
    assert results[0].reason == "window_complete"
    assert results[0].pairs == [("voiser", "Voisr")]
    assert results[0].words_corrected == 1


def test_field_emptied_keeps_the_last_read():
    box = textbox("start Voisr is great end")
    _, results, run_to, _ = make_watcher(box)
    run_to(READ_AT_S[0])
    box.attrs["AXValue"] = ""
    run_to(READ_AT_S[1])
    assert results[0].reason == "field_emptied"
    assert results[0].pairs == [("voiser", "Voisr")]


def test_an_unreadable_read_is_not_an_emptied_field():
    box = textbox("start voiser is great end")
    _, results, run_to, _ = make_watcher(box)
    del box.attrs["AXValue"]
    run_to(READ_AT_S[0])
    assert results == [], "unreadable must not end the window as sent"
    box.attrs["AXValue"] = "start Voisr is great end"
    run_to(READ_AT_S[1])
    run_to(READ_AT_S[2])
    run_to(READ_AT_S[3])
    assert results[0].reason == "window_complete"
    assert results[0].pairs == [("voiser", "Voisr")]


def test_a_field_unreadable_to_the_end_says_so_and_changes_nothing():
    box = textbox("start voiser is great end")
    _, results, run_to, _ = make_watcher(box)
    del box.attrs["AXValue"]
    for t in READ_AT_S:
        run_to(t)
    assert results[0].reason == "unreadable" and results[0].pairs == []


def test_anchor_lost_keeps_the_last_read():
    box = textbox("start Voisr is great end")
    _, results, run_to, _ = make_watcher(box)
    run_to(READ_AT_S[0])
    box.attrs["AXValue"] = "something else"
    run_to(READ_AT_S[1])
    assert results[0].reason == "anchor_lost"
    assert results[0].pairs == [("voiser", "Voisr")]


def test_a_rewrite_yields_nothing_even_from_earlier_reads():
    box = textbox("start Voisr is great end")
    _, results, run_to, _ = make_watcher(box)
    run_to(READ_AT_S[0])
    box.attrs["AXValue"] = "start totally new words here end"
    run_to(READ_AT_S[1])
    assert results[0].reason == "rewrite"
    assert results[0].pairs == [] and results[0].words_corrected == 0


def test_focus_away_too_long_ends_it():
    box = textbox("start Voisr is great end")
    _, results, run_to, ax = make_watcher(box)
    run_to(READ_AT_S[0])
    ax.bundle_id = "com.other.app"
    run_to(READ_AT_S[1])                  # first seen away
    assert results == []
    run_to(READ_AT_S[1] + 25)             # not yet over 30 s
    assert results == []
    run_to(READ_AT_S[3])
    assert results and results[0].reason == "focus_moved"
    assert results[0].pairs == [("voiser", "Voisr")]


def test_coming_back_resets_the_away_clock():
    box = textbox("start voiser is great end")
    _, results, run_to, ax = make_watcher(box)
    ax.bundle_id = "com.other.app"
    run_to(READ_AT_S[0])
    ax.bundle_id = "com.example.app"
    run_to(READ_AT_S[1])
    ax.bundle_id = "com.other.app"
    run_to(READ_AT_S[2])
    assert results == []


def test_next_paste_into_the_same_element_ends_the_first_window():
    box = textbox("start Voisr is great end")
    watcher, results, run_to, _ = make_watcher(box)
    run_to(READ_AT_S[0])
    focus = FocusContext(bundle_id="com.example.app", before_text="start ", after_text=" end",
                         element=box, element_id=box.id, caret_known=True)
    watcher.watch("item2", focus, box.id, "more", ())
    assert results == [], "delivery belongs to the watcher thread"
    watcher.tick()
    assert results[0].item_id == "item1" and results[0].reason == "next_paste"
    assert results[0].pairs == [("voiser", "Voisr")]


def test_turning_it_off_stops_reading_at_once():
    box = textbox("start voiser is great end")
    on = [True]
    _, results, run_to, ax = make_watcher(box, enabled=lambda: on[0])
    on[0] = False
    ax.calls.clear()
    run_to(READ_AT_S[0])
    assert ax.calls == []
    assert results[0].reason == "setting_off" and results[0].pairs == []


def test_a_field_that_turns_secure_ends_it_with_nothing():
    box = textbox("start Voisr is great end")
    _, results, run_to, ax = make_watcher(box)
    run_to(READ_AT_S[0])
    box.attrs["AXSubrole"] = "AXSecureTextField"
    ax.calls.clear()
    run_to(READ_AT_S[1])
    assert results[0].reason == "secure" and results[0].pairs == []
    assert "AXValue" not in {a for _, a in ax.calls}


def test_context_terms_make_a_candidate_of_an_unlike_word():
    box = textbox("start Anthropic is great end")
    _, results, run_to, _ = make_watcher(box, terms=("Anthropic",))
    for t in READ_AT_S:
        run_to(t)
    assert results[0].pairs == [("voiser", "Anthropic")]


def test_a_stop_drops_open_windows_and_their_text():
    box = textbox("start Voisr is great end")
    watcher, results, _, _ = make_watcher(box)
    window = watcher._windows[0]
    watcher.stop()
    assert results == [] and window.pasted == "" and window.element is None
    assert watcher._windows == []


def test_the_thread_runs_a_window_end_to_end():
    import threading
    import time

    done = threading.Event()
    box = textbox("start Voisr is great end")
    results = []

    class Worker:
        def submit(self, fn):
            from concurrent.futures import Future
            future = Future()
            future.set_result(fn())
            return future

    now = [0.0]
    watcher = EditWatcher(FakeAX(box), Worker(), lambda: True,
                          lambda r: (results.append(r), done.set()),
                          clock=lambda: now[0], budget_s=1.0)
    focus = FocusContext(bundle_id="com.example.app", before_text="start ", after_text=" end",
                         element=box, element_id=box.id, caret_known=True)
    watcher.watch("i", focus, box.id, "voiser is great", ())
    now[0] = 1000.0
    with watcher._cond:
        watcher._cond.notify_all()
    deadline = time.monotonic() + 3
    while not done.is_set() and time.monotonic() < deadline:
        with watcher._cond:
            watcher._cond.notify_all()
        time.sleep(0.01)
    watcher.stop()
    assert results and results[0].pairs == [("voiser", "Voisr")]


def test_an_ended_window_is_delivered_on_the_watcher_thread_not_the_callers():
    import threading
    import time

    box = textbox("start Voisr is great end")
    delivered = threading.Event()
    gate = threading.Event()
    threads = []

    def slow_on_end(result):
        threads.append(threading.current_thread().name)
        gate.wait(5)     # file writes, the word list, a config write
        delivered.set()

    class Worker:
        def submit(self, fn):
            from concurrent.futures import Future
            future = Future()
            future.set_result(fn())
            return future

    watcher = EditWatcher(FakeAX(box), Worker(), lambda: True, slow_on_end, budget_s=1.0)
    focus = FocusContext(bundle_id="com.example.app", before_text="start ", after_text=" end",
                         element=box, element_id=box.id, caret_known=True)
    watcher.watch("first", focus, box.id, "voiser is great", ())
    started = time.monotonic()
    watcher.watch("second", focus, box.id, "more", ())   # ends the first
    assert time.monotonic() - started < 0.5, "the dictation queue waited on delivery"
    gate.set()
    assert delivered.wait(5)
    assert threads == ["voisr-edit-watch"]
    watcher.stop()
