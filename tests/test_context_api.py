"""The context features as the Api drives them, with a fake AX adapter."""
import threading
import time
import wave
from concurrent.futures import Future, wait

import pytest

from tests.fake_ax import FakeAX, FakeElement, textbox
from voisr.history import HistoryStore
from voisr.ui import Api, HotkeyService, glossary_prompt


class FakeRecorder:
    def start(self, path):
        self.path = path

    def stop(self):
        with wave.open(str(self.path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\x10\x20" * 16000 * 3)
        return ""

    def cancel(self):
        pass

    def close(self):
        pass


CAPTURE_WAIT_S = 10


def release_capture_first(api):
    """Hold each dictation until its release capture is done. Real timing
    normally does this: transcribing outlasts the capture, and the paste check
    waits a second more, though an app that stalls can still break the order.
    Here both are instant, and the capture is only queued after the session is
    handed over, so a dictation could reach the check with no baseline, or land
    its text before the capture read the field."""
    released = set()
    changed = threading.Condition()
    capture_release = api._capture_release
    process = api._recordings._processor

    def capture_and_note(sequence):
        capture_release(sequence)
        with changed:
            released.add(sequence)
            changed.notify_all()

    def stuck(what):
        message = (f"release capture {what} within {CAPTURE_WAIT_S} s: "
                   "a fake app that hangs needs capture_first=False")
        print(message, flush=True)  # shown with the failing test's output
        raise TimeoutError(message)

    def process_in_order(session):
        with changed:
            if not changed.wait_for(lambda: session.sequence in released,
                                    timeout=CAPTURE_WAIT_S):
                stuck("not queued")
        state = api._focus.session(session.sequence)
        if state is not None and state.release is not None:
            if not wait([state.release], timeout=CAPTURE_WAIT_S).done:
                stuck("not finished")
        return process(session)

    api._capture_release = capture_and_note
    api._recordings._processor = process_in_order


def make_api(tmp_path, ax=None, capture_first=True, watcher_thread=False, **kwargs):
    """capture_first=False: for an app that hangs, where the capture never ends.
    watcher_thread=True: the watcher starts its thread as it does in the app."""
    kwargs.setdefault("transcribe_fn", lambda wav: "hello world")
    kwargs.setdefault("paste_fn", lambda text: True)
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "history.jsonl"),
              recorder_factory=FakeRecorder,
              clipboard_fn=lambda text: None,
              ax_adapter=ax, **kwargs)
    if ax is not None:
        if not watcher_thread:
            # run_windows alone runs a window: no thread can be part way through
            # delivering one while a test reads the result
            api._edit_watcher._autostart = False
        if capture_first:
            release_capture_first(api)
    return api


@pytest.fixture(autouse=True)
def no_speech_gate(monkeypatch):
    monkeypatch.setattr("voisr.speech_gate.gate_wav", lambda wav: None)


def settle(api, sequence=None):
    """Let the AX worker finish what is queued."""
    if api._focus is not None:
        api._focus.worker.submit(lambda: None).result(timeout=5)


def dictate(api, paste=True):
    api.start_recording(prewarm=False)
    api.prewarm_now()
    settle(api)
    return api.stop_recording(paste=paste)


def test_a_dictation_records_the_app_and_ax_time(tmp_path):
    api = make_api(tmp_path, FakeAX(textbox("some text")))
    out = dictate(api)
    assert "error" not in out
    item = api.get_history()[0]
    assert item["app"] == "com.example.app"
    assert "ax_context" in item["timings_ms"]
    api.close()


def test_a_denylisted_app_is_not_named_in_history(tmp_path):
    api = make_api(tmp_path, FakeAX(textbox("x"), bundle_id="com.1password.1password"))
    dictate(api)
    assert "app" not in api.get_history()[0]
    api.close()


def test_screen_text_never_reaches_history(tmp_path):
    secret = "ScreenOnlyWord"
    api = make_api(tmp_path, FakeAX(textbox(f"before {secret} after")))
    dictate(api)
    settle(api)
    assert secret not in (tmp_path / "history.jsonl").read_text()
    api.close()


def test_off_means_no_ax_call_at_all(tmp_path):
    ax = FakeAX(textbox("some text"))
    api = make_api(tmp_path, ax)
    api.set_config({"use_screen_context": False})
    out = dictate(api)
    settle(api)
    assert "error" not in out
    assert ax.calls == [] and ax.front_calls == 0
    assert "app" not in api.get_history()[0]
    api.close()


def test_no_adapter_runs_as_before(tmp_path):
    api = make_api(tmp_path)
    out = dictate(api)
    assert out["text"] == "Hello world" and "error" not in out
    assert "ax_context" not in api.get_history()[0].get("timings_ms", {})
    api.close()


class HungAX(FakeAX):
    """A target app that never answers."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.release = threading.Event()
        self.entered = threading.Event()

    def focused_element(self, timeout_s):
        self.entered.set()
        self.release.wait(10)
        return self.focused


def test_a_hung_app_blocks_neither_press_release_nor_delivery(tmp_path):
    ax = HungAX(textbox("text"))
    pasted = []
    api = make_api(tmp_path, ax, capture_first=False,
                   paste_fn=lambda text: pasted.append(text) or True)
    started = time.monotonic()
    api.start_recording(prewarm=False)
    api.prewarm_now()
    assert ax.entered.wait(2)
    future = api._release_recording(paste=True)
    assert time.monotonic() - started < 2, "the hotkey path waited on AX"
    result = future.result(timeout=5).to_api_dict()
    assert pasted == ["Hello world"]
    assert "error" not in result
    ax.release.set()
    api.close()


def test_no_ax_call_runs_on_the_hotkey_thread(tmp_path):
    threads = set()

    class Spy(FakeAX):
        def frontmost(self):
            threads.add(threading.current_thread().name)
            return super().frontmost()

    api = make_api(tmp_path, Spy(textbox("text")))
    service = HotkeyService(api, tap_gate_s=0)
    service.press()
    settle(api)
    service.release()
    time.sleep(0.05)
    settle(api)
    assert threads == {"voisr-ax"}
    api.close()


def test_a_tap_that_never_became_a_dictation_reads_nothing(tmp_path):
    ax = FakeAX(textbox("text"))
    api = make_api(tmp_path, ax)
    timers = []

    class Timer:
        def __init__(self, delay, fn):
            self.fn = fn
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    service = HotkeyService(api, tap_gate_s=0.5, timer_factory=Timer)
    service.press()
    service.release()      # quicker than the gate: a tap
    api._cancel_recording()
    settle(api)
    assert ax.calls == [] and ax.front_calls == 0
    api.close()


def test_a_broken_adapter_loses_nothing(tmp_path):
    class Broken(FakeAX):
        def frontmost(self):
            raise OSError("boom")

    pasted = []
    api = make_api(tmp_path, Broken(), paste_fn=lambda t: pasted.append(t) or True)
    out = dictate(api)
    assert pasted == ["Hello world"] and "error" not in out
    api.close()


def test_a_broken_tracker_loses_nothing(tmp_path, monkeypatch):
    api = make_api(tmp_path, FakeAX(textbox("x")))
    monkeypatch.setattr(api._focus, "capture_press",
                        lambda seq: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr(api._focus, "capture_release",
                        lambda seq: (_ for _ in ()).throw(RuntimeError("no")))
    api.start_recording(prewarm=False)
    api.prewarm_now()
    assert api.stop_recording(paste=True)["text"] == "Hello world"
    api.close()


def test_the_session_forgets_its_screen_text_when_it_ends(tmp_path):
    api = make_api(tmp_path, FakeAX(textbox("text")))
    dictate(api)
    assert api._focus._sessions == {}
    api.close()


def test_cancel_drops_the_capture(tmp_path):
    api = make_api(tmp_path, FakeAX(textbox("text")))
    api.start_recording(prewarm=False)
    api.prewarm_now()
    settle(api)
    api._cancel_recording()
    assert api._focus._sessions == {}
    api.close()


def test_on_screen_terms_reach_whisper_cli_and_the_server(tmp_path, monkeypatch):
    # both transcribers take the same prompt, through transcribe_routed
    from voisr import transcriber

    prompts = {}

    class Server:
        def configure(self, unload):
            pass

        def set_busy(self, busy):
            pass

        def prewarm(self, *a, **k):
            pass

        def shutdown(self):
            pass

        def unload(self):
            pass

        def transcribe(self, wav, model, prompt=None):
            prompts["server"] = prompt
            return "hello world", {}

    def cli(wav, model_path=None, prompt=None):
        prompts["cli"] = prompt
        return "hello world"

    monkeypatch.setattr(transcriber, "transcribe", cli)
    box = textbox("say hi to Priya about Zedwick soon", caret=10)
    for server, unload, key in ((Server(), "min5", "server"), (None, "min5", "cli")):
        api = make_api(tmp_path, FakeAX(box), transcribe_fn=None, warm_server=server)
        api.set_config({"model_unload": unload,
                        "dictionary": {"voiser": "Voisr"}})
        out = dictate(api)
        assert "error" not in out, out
        assert prompts[key].startswith("Glossary: Voisr, ")
        assert "Priya" in prompts[key] and "Zedwick" in prompts[key]
        api.close()


def test_terms_only_bias_they_never_rewrite_the_text(tmp_path, monkeypatch):
    api = make_api(tmp_path, FakeAX(textbox("meet Priya today")), transcribe_fn=None)
    monkeypatch.setattr(api, "_default_transcribe",
                        lambda wav, cfg, terms=(): ("priya said hi", {"mode": "cli", "timings": {}}))
    assert dictate(api)["text"] == "Priya said hi"  # first-word capital, not a term
    api.close()


def test_off_sends_no_terms(tmp_path, monkeypatch):
    seen = []
    api = make_api(tmp_path, FakeAX(textbox("meet Priya today")), transcribe_fn=None)
    monkeypatch.setattr(api, "_default_transcribe",
                        lambda wav, cfg, terms=(): seen.append(list(terms)) or ("ok", {"mode": "cli", "timings": {}}))
    api.set_config({"use_screen_context": False})
    dictate(api)
    assert seen == [[]]
    api.close()


def test_a_failure_with_terms_retries_without_and_loses_nothing(tmp_path, monkeypatch):
    calls = []

    def fake(wav, cfg, terms=()):
        calls.append(list(terms))
        if terms:
            raise RuntimeError("prompt rejected")
        return "kept it", {"mode": "cli", "timings": {}}

    api = make_api(tmp_path, FakeAX(textbox("meet Priya today")), transcribe_fn=None)
    monkeypatch.setattr(api, "_default_transcribe", fake)
    out = dictate(api)
    assert out["text"] == "Kept it" and "error" not in out
    assert calls == [["Priya"], []]
    api.close()


def test_an_echoed_glossary_is_redone_without_terms(tmp_path, monkeypatch):
    # screen words must not reach a paste or History through a hallucination
    calls = []

    def fake(wav, cfg, terms=()):
        calls.append(list(terms))
        return (("Glossary: Priya." if terms else "real speech"),
                {"mode": "cli", "timings": {}})

    api = make_api(tmp_path, FakeAX(textbox("meet Priya today")), transcribe_fn=None)
    monkeypatch.setattr(api, "_default_transcribe", fake)
    assert dictate(api)["text"] == "Real speech"
    assert "Priya" not in (tmp_path / "history.jsonl").read_text()
    assert len(calls) == 2
    api.close()


def test_a_capture_that_has_not_finished_is_not_waited_for(tmp_path, monkeypatch):
    seen = []
    ax = HungAX(textbox("meet Priya today"))
    api = make_api(tmp_path, ax, capture_first=False, transcribe_fn=None)
    monkeypatch.setattr(api, "_default_transcribe",
                        lambda wav, cfg, terms=(): seen.append(list(terms)) or ("ok", {"mode": "cli", "timings": {}}))
    api.start_recording(prewarm=False)
    api.prewarm_now()
    assert ax.entered.wait(2)
    started = time.monotonic()
    out = api.stop_recording(paste=False)
    assert time.monotonic() - started < 2
    assert seen == [[]] and "error" not in out
    ax.release.set()
    api.close()


def test_a_capture_unfinished_at_the_paste_check_is_no_baseline_not_a_miss(tmp_path):
    box = textbox("start ")
    ax = HungAX(box)   # the release capture waits behind the press capture
    paste = verifying_paste(box, land=False)
    api = make_api(tmp_path, ax, capture_first=False, paste_fn=paste)
    api.start_recording(prewarm=False)
    api.prewarm_now()
    assert ax.entered.wait(2)
    out = api.stop_recording(paste=True)
    assert paste.kept is False, "the old clipboard goes back"
    assert out["pasted"] is True and "manual_paste" not in out
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "no_baseline")
    ax.release.set()
    api.close()


def verifying_paste(box, land=True):
    """The field gets the text when "Cmd-V" goes out, as a real one would."""
    def paste(text, on_sent=None, verifier=None):
        if land:
            box.attrs["AXValue"] = box.attrs["AXValue"] + text
        if on_sent:
            on_sent()
        keep = verifier("OLD COPY") if verifier else False
        paste.kept = keep
        return True
    paste.kept = None
    return paste


def test_verified_is_recorded_with_its_time(tmp_path):
    box = textbox("start ")
    api = make_api(tmp_path, FakeAX(box), paste_fn=verifying_paste(box))
    out = dictate(api)
    assert out["delivery"] == "verified" and out["pasted"] is True
    assert "manual_paste" not in out
    item = api.get_history()[0]
    assert item["delivery"] == "verified"
    assert "verify" in item["timings_ms"]
    api.close()


def test_a_missed_paste_asks_for_a_manual_one(tmp_path):
    box = textbox("start ")
    paste = verifying_paste(box, land=False)
    written = []
    api = make_api(tmp_path, FakeAX(box), paste_fn=paste)
    api._clipboard_fn = written.append
    out = dictate(api)
    assert paste.kept is True
    assert out["delivery"] == "not_found" and out["manual_paste"] is True
    assert out["pasted"] is False and out["copied"] is True and "error" not in out
    assert api.get_history()[0]["delivery"] == "not_found"
    assert written == [], "the paste already left it on the clipboard"
    assert api._last_paste_text is None, "no join onto text that didn't land"
    api.close()


PRESS_CAPTURE, RELEASE_CAPTURE = 1, 2


class StallsOnCapture(HungAX):
    """Hangs at one capture only, until `release` is set."""

    def __init__(self, box, stall_on):
        super().__init__(box)
        self.stall_on, self.captures = stall_on, 0

    def focused_element(self, timeout_s):
        self.captures += 1
        if self.captures == self.stall_on:
            return super().focused_element(timeout_s)
        return self.focused


def release_capture(api):
    """The latest dictation's release capture, once it has been queued."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        sessions = dict(api._focus._sessions)
        if sessions and sessions[max(sessions)].release is not None:
            return sessions[max(sessions)].release
        time.sleep(0.001)
    raise AssertionError("the release capture was never queued")


def dictate_through_a_stall(tmp_path, stall_on, recovers_in_grace=True, lands_again=False):
    """The text lands while the app is stalled, so the release capture can only
    end after it. The Api is built here because nothing may hold the dictation
    back until that capture is done."""
    box = textbox("start ")
    ax = StallsOnCapture(box, stall_on)

    def paste(text, on_sent=None, verifier=None):
        assert ax.entered.wait(5)
        box.attrs["AXValue"] += text
        on_sent()
        if recovers_in_grace:
            ax.release.set()
            release_capture(api).result(timeout=5)
        if lands_again:
            box.attrs["AXValue"] += text
        paste.kept = verifier("OLD COPY")
        return True

    paste.kept = None
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "history.jsonl"),
              recorder_factory=FakeRecorder, clipboard_fn=lambda text: None,
              ax_adapter=ax, transcribe_fn=lambda wav: "hello world", paste_fn=paste)
    api.start_recording(prewarm=False)
    api.prewarm_now()
    if stall_on == RELEASE_CAPTURE:
        settle(api)
    out = api.stop_recording(paste=True)
    ax.release.set()
    assert "start Hello world" in box.attrs["AXValue"]
    return api, paste, out


@pytest.mark.parametrize("stall_on", [PRESS_CAPTURE, RELEASE_CAPTURE],
                         ids=["queued_behind_the_press_capture", "stalled_in_its_own_read"])
def test_a_baseline_read_after_the_text_landed_is_not_a_miss(tmp_path, stall_on):
    api, paste, out = dictate_through_a_stall(tmp_path, stall_on)
    assert out["delivery"] == "skipped" and out["pasted"] is True
    assert "manual_paste" not in out
    assert paste.kept is False, "the old clipboard goes back"
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "late_baseline")
    assert api._edit_watcher._windows == [], "nothing to learn from without a baseline"
    assert api._last_paste_text == "Hello world", "the next dictation joins onto it"
    api.close()


def test_a_late_capture_holding_the_text_never_anchors_an_edit_window(tmp_path):
    """A second copy in the grace would read as verified against that capture,
    and its anchors would have the pasted text inside them."""
    api, paste, out = dictate_through_a_stall(tmp_path, RELEASE_CAPTURE, lands_again=True)
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "late_baseline")
    assert api._edit_watcher._windows == []
    api.close()


def test_a_capture_still_running_at_the_check_is_no_baseline(tmp_path):
    api, paste, out = dictate_through_a_stall(tmp_path, RELEASE_CAPTURE, recovers_in_grace=False)
    assert paste.kept is False and out["pasted"] is True
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "no_baseline")
    api.close()


def test_a_real_miss_is_still_a_miss_when_the_baseline_came_first(tmp_path):
    box = textbox("start ")
    paste = verifying_paste(box, land=False)

    def transcribe(wav):
        release_capture(api).result(timeout=5)
        return "hello world"

    api = make_api(tmp_path, FakeAX(box), transcribe_fn=transcribe, paste_fn=paste)
    out = dictate(api)
    assert paste.kept is True
    assert out["delivery"] == "not_found" and out["manual_paste"] is True
    assert out["pasted"] is False
    api.close()


def test_a_skip_records_its_reason_and_changes_nothing(tmp_path):
    box = textbox("")     # stays empty: proves nothing
    paste = verifying_paste(box, land=False)
    api = make_api(tmp_path, FakeAX(box), paste_fn=paste)
    out = dictate(api)
    assert paste.kept is False and out["pasted"] is True
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "empty_field")
    api.close()


def test_verification_off_reads_nothing(tmp_path):
    box = textbox("start ")
    ax = FakeAX(box)
    api = make_api(tmp_path, ax, paste_fn=verifying_paste(box))
    api.set_config({"use_screen_context": False})
    out = dictate(api)
    assert ax.calls == [] and ax.front_calls == 0
    assert out["pasted"] is True
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "context_off")
    api.close()


def test_a_hung_app_at_verify_time_is_bounded_and_restores(tmp_path):
    from voisr.paste_verify import VERIFY_BUDGET_S

    box = textbox("start ")
    release = threading.Event()

    class HangsLater(FakeAX):
        hang = False

        def focused_element(self, timeout_s):
            if self.hang:
                release.wait(10)
            return super().focused_element(timeout_s)

    ax = HangsLater(box)
    spent = {}

    def paste(text, on_sent=None, verifier=None):
        ax.hang = True
        started = time.monotonic()
        spent["keep"] = verifier("OLD")
        spent["s"] = time.monotonic() - started
        return True

    api = make_api(tmp_path, ax, paste_fn=paste)
    out = dictate(api)
    assert spent["keep"] is False
    assert spent["s"] < VERIFY_BUDGET_S + 1
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "timeout")
    assert out["pasted"] is True
    release.set()
    api.close()


def test_verification_does_not_move_total_or_paste_timing(tmp_path):
    box = textbox("start ")
    now = [0.0]

    def clock():
        return now[0]

    def paste(text, on_sent=None, verifier=None):
        now[0] += 0.01
        on_sent()
        now[0] += 1.0     # the grace
        verifier("OLD")
        return True

    api = make_api(tmp_path, FakeAX(box), paste_fn=paste, clock=clock)
    dictate(api)
    timings = api.get_history()[0]["timings_ms"]
    assert timings["paste"] == 10
    assert timings["total"] == 10
    api.close()


def test_a_concealed_clipboard_is_recorded_as_skipped(tmp_path):
    def paste(text, on_sent=None, verifier=None):
        return True  # returned without calling the verifier

    api = make_api(tmp_path, FakeAX(textbox("x")), paste_fn=paste)
    dictate(api)
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "concealed_clipboard")
    api.close()


def test_a_broken_verifier_inside_the_api_never_loses_the_dictation(tmp_path, monkeypatch):
    box = textbox("x")
    api = make_api(tmp_path, FakeAX(box), paste_fn=verifying_paste(box))

    def explode(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr("voisr.ui.check_delivery", explode)
    paste = verifying_paste(box)
    api._paste_fn = paste
    out = dictate(api)
    assert out["text"] == "Hello world" and out["pasted"] is True
    assert paste.kept is False
    item = api.get_history()[0]
    assert (item["delivery"], item["delivery_reason"]) == ("skipped", "error")
    api.close()


def test_the_pill_says_copied_on_a_missed_paste(tmp_path):
    box = textbox("start ")
    api = make_api(tmp_path, FakeAX(box), paste_fn=verifying_paste(box, land=False))

    class Widget:
        calls = []

        def show_recording(self, theme="classic", color=None):
            self.calls.append("recording")

        def show_processing(self):
            self.calls.append("processing")

        def show_done(self):
            self.calls.append("done")

        def show_error(self):
            self.calls.append("error")

        def show_copied(self):
            self.calls.append("copied")

        def hide(self):
            self.calls.append("hide")

    widget = Widget()
    service = HotkeyService(api, widget=widget, hide_delay=0, tap_gate_s=0)
    service.press()
    settle(api)
    service.release()
    deadline = time.monotonic() + 3
    while widget.calls[-1:] != ["hide"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert widget.calls == ["recording", "processing", "copied", "hide"]
    api.close()


def learning_api(tmp_path, box, ax=None, **config):
    """Each dictate() pastes `voiser is great` into box; correct() fixes it and
    runs the window to its end."""
    ax = ax or FakeAX(box)
    api = make_api(tmp_path, ax, transcribe_fn=lambda wav: "voiser is great",
                   paste_fn=verifying_paste(box))
    # the system dictionary isn't what these tests are about
    api.set_config({**config})
    return api, ax


def correct(api, box, typed="Voisr is great"):
    box.attrs["AXValue"] = box.attrs["AXValue"].replace("Voiser is great", typed)
    box.attrs["AXValue"] = box.attrs["AXValue"].replace("voiser is great", typed)
    run_windows(api)


def run_windows(api):
    from voisr.edit_learning import READ_AT_S

    watcher = api._edit_watcher
    with watcher._cond:
        windows = list(watcher._windows)
    far = time.monotonic() + 10_000
    for offset in range(len(READ_AT_S) + 1):
        watcher.tick(far + offset * 100)    # a changed read waits for its recheck
    # a window whose schedule was walked past has ended
    assert all(w.done for w in windows)


@pytest.fixture
def no_system_dictionary(monkeypatch):
    monkeypatch.setattr("voisr.suggestions.is_everyday_lowercase_word", lambda w: False)
    from voisr import suggestions
    original = suggestions.auto_add_kind

    def checked(heard, written, **kwargs):
        kwargs.setdefault("everyday", lambda w: False)
        return original(heard, written, **kwargs)

    monkeypatch.setattr(suggestions, "auto_add_kind", checked)


def test_first_sighting_is_a_suggestion_second_is_auto_added(tmp_path, no_system_dictionary):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    dictate(api)
    correct(api, box)
    item = api.get_history()[0]
    assert item["edit_end_reason"] == "window_complete"
    assert item["words_corrected"] == 1
    state = api.get_suggestions()
    assert [(p["heard"], p["written"], p["seen"]) for p in state["pending"]] == [
        ("Voiser", "Voisr", 1)]
    assert api.get_config()["dictionary"] == {}

    box.attrs["AXValue"] = ""
    dictate(api)
    correct(api, box)
    assert api.get_config()["dictionary"] == {"voiser": "Voisr"}
    state = api.get_suggestions()
    assert state["pending"] == []
    assert state["auto_added"] == {"voiser": "Voisr"}
    api.close()


def test_the_auto_add_toggle_keeps_it_a_suggestion(tmp_path, no_system_dictionary):
    box = textbox("")
    api, _ = learning_api(tmp_path, box, dictionary_auto_add=False)
    for _ in range(3):
        box.attrs["AXValue"] = ""
        dictate(api)
        correct(api, box)
    assert api.get_config()["dictionary"] == {}
    assert api.get_suggestions()["pending"][0]["seen"] == 3
    api.close()


def test_a_removed_auto_added_entry_is_never_added_again(tmp_path, no_system_dictionary):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    for _ in range(2):
        box.attrs["AXValue"] = ""
        dictate(api)
        correct(api, box)
    assert "voiser" in api.get_config()["dictionary"]

    api.set_config({"dictionary": {}})  # the Dictionary row's one Remove button
    for _ in range(3):
        box.attrs["AXValue"] = ""
        dictate(api)
        correct(api, box)
    assert api.get_config()["dictionary"] == {}
    assert api.get_suggestions()["pending"] == []
    api.close()


def test_a_dismissed_pair_is_never_suggested_or_added(tmp_path, no_system_dictionary):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    dictate(api)
    correct(api, box)
    pair = api.get_suggestions()["pending"][0]
    assert api.dismiss_suggestion(pair["id"]) == {"ok": True}
    for _ in range(3):
        box.attrs["AXValue"] = ""
        dictate(api)
        correct(api, box)
    assert api.get_config()["dictionary"] == {}
    assert api.get_suggestions()["pending"] == []
    api.close()


def test_accept_adds_it_whatever_its_kind(tmp_path):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    dictate(api)
    correct(api, box)
    pair = api.get_suggestions()["pending"][0]
    assert api.accept_suggestion(pair["id"]) == {"ok": True}
    assert api.get_config()["dictionary"] == {"voiser": "Voisr"}
    assert api.get_suggestions()["pending"] == []
    assert api.get_suggestions()["auto_added"] == {}
    assert "error" in api.accept_suggestion("gone")
    assert "error" in api.dismiss_suggestion("gone")
    assert "error" in api.accept_suggestion(5)
    api.close()


def test_tuesday_to_thursday_never_qualifies_end_to_end(tmp_path):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    api._transcribe_fn = lambda wav: "see you Tuesday then"
    for _ in range(3):
        box.attrs["AXValue"] = ""
        dictate(api)
        correct_to = box.attrs["AXValue"].replace("Tuesday", "Thursday")
        box.attrs["AXValue"] = correct_to
        run_windows(api)
    assert api.get_config()["dictionary"] == {}
    pending = api.get_suggestions()["pending"]
    assert [(p["heard"], p["written"]) for p in pending] == [("Tuesday", "Thursday")]
    api.close()


def test_only_the_word_pair_reaches_disk(tmp_path, no_system_dictionary):
    box = textbox("confidential project context ")
    api, _ = learning_api(tmp_path, box)
    dictate(api)
    correct(api, box)
    on_disk = "".join(p.read_text() for p in tmp_path.iterdir()
                      if p.is_file() and not p.name.endswith(".lock"))
    assert "confidential" not in on_disk
    assert "Voisr" in (tmp_path / "suggestions.json").read_text()
    api.close()


@pytest.mark.parametrize("setting", [{"use_screen_context": False},
                                     {"learn_from_edits": False}])
def test_either_setting_opens_no_window(tmp_path, setting):
    box = textbox("")
    api, ax = learning_api(tmp_path, box, **setting)
    dictate(api)
    assert api._edit_watcher._windows == []
    assert "edit_end_reason" not in api.get_history()[0]
    api.close()


def test_the_api_runs_a_window_on_the_watcher_thread(tmp_path, monkeypatch):
    monkeypatch.setattr("voisr.edit_learning.READ_AT_S", (0.0,))
    box = textbox("")
    api = make_api(tmp_path, FakeAX(box), watcher_thread=True,
                   transcribe_fn=lambda wav: "voiser is great", paste_fn=verifying_paste(box))
    dictate(api)
    deadline = time.monotonic() + 10
    while ("edit_end_reason" not in api.get_history()[0]
           and time.monotonic() < deadline):
        time.sleep(0.01)
    assert api.get_history()[0]["edit_end_reason"] == "window_complete"
    assert api._edit_watcher._thread.name == "voisr-edit-watch"
    api.close()


def test_learning_off_still_verifies(tmp_path):
    box = textbox("")
    api, _ = learning_api(tmp_path, box, learn_from_edits=False)
    assert dictate(api)["delivery"] == "verified"
    api.close()


def test_switching_screen_context_off_ends_an_open_window(tmp_path):
    box = textbox("")
    api, ax = learning_api(tmp_path, box)
    dictate(api)
    assert len(api._edit_watcher._windows) == 1
    api.set_config({"use_screen_context": False})
    ax.calls.clear()
    box.attrs["AXValue"] = "Voisr is great"
    run_windows(api)
    assert ax.calls == []
    assert api.get_suggestions()["pending"] == []
    assert api.get_history()[0]["edit_end_reason"] == "setting_off"
    api.close()


def test_a_failed_paste_opens_no_window(tmp_path):
    box = textbox("start ")
    api = make_api(tmp_path, FakeAX(box), paste_fn=verifying_paste(box, land=False))
    dictate(api)
    assert api._edit_watcher._windows == []
    api.close()


def test_a_failure_in_learning_never_touches_the_dictation(tmp_path, monkeypatch):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    monkeypatch.setattr(api._edit_watcher, "watch",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    out = dictate(api)
    assert out["pasted"] is True and "error" not in out
    api.close()


def test_a_failing_store_never_touches_the_dictation_or_the_watcher(tmp_path, monkeypatch):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    dictate(api)
    monkeypatch.setattr(api._suggestions, "observe",
                        lambda pairs: (_ for _ in ()).throw(OSError("disk full")))
    correct(api, box)     # would raise here if the watcher didn't contain it
    assert api.get_history()[0]["edit_end_reason"] == "window_complete"
    api.close()


def test_used_counts_bump_when_an_entry_fires(tmp_path):
    api = make_api(tmp_path, FakeAX(textbox("")),
                   transcribe_fn=lambda wav: "voiser and voiser")
    api.set_config({"dictionary": {"voiser": "Voisr"}})
    dictate(api)
    deadline = time.monotonic() + 2
    while not api.get_suggestions()["used"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert api.get_suggestions()["used"] == {"voiser": 2}
    api.close()


def test_the_notice_goes_to_the_pill_when_idle(tmp_path, no_system_dictionary):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    calls = []

    class Widget:
        def show_notice(self, text):
            calls.append(("notice", text))

        def hide(self):
            calls.append(("hide",))

        def show_recording(self, theme="classic", color=None):
            pass

        def show_processing(self):
            pass

        def show_done(self):
            pass

        def show_error(self):
            pass

    service = HotkeyService(api, widget=Widget(), hide_delay=0, tap_gate_s=0)
    service.NOTICE_HOLD_S = 0
    api.attach_hotkey_service(service)
    for _ in range(2):
        box.attrs["AXValue"] = ""
        dictate(api)
        correct(api, box)
    deadline = time.monotonic() + 2
    while ("hide",) not in calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ("notice", "Suggested: Voisr") in calls, "the first sighting"
    assert ("notice", "Added Voisr to Dictionary") in calls
    assert ("hide",) in calls
    api.close()


def test_a_suggestion_goes_to_the_pill_only_the_first_time(tmp_path, no_system_dictionary):
    box = textbox("")
    api, _ = learning_api(tmp_path, box)
    api._update_config({"dictionary_auto_add": False})
    calls = []

    class Widget:
        def show_notice(self, text):
            calls.append(text)

        def hide(self):
            pass

    service = HotkeyService(api, widget=Widget(), hide_delay=0, tap_gate_s=0)
    service.NOTICE_HOLD_S = 0
    api.attach_hotkey_service(service)
    for _ in range(3):
        box.attrs["AXValue"] = ""
        dictate(api)
        correct(api, box)
    for thread in threading.enumerate():
        if thread.name == "voisr-notice":
            thread.join(timeout=2)
    assert api.get_suggestions()["pending"][0]["seen"] == 3
    assert calls == ["Suggested: Voisr"]
    api.close()


def test_a_notice_does_not_cover_a_dictation_result(tmp_path):
    api = make_api(tmp_path)
    calls = []

    class Widget:
        def show_notice(self, text):
            calls.append(text)

        def show_copied(self):
            calls.append("copied")

        def hide(self):
            calls.append("hide")

    class Result:
        def to_api_dict(self):
            return {"manual_paste": True}

    service = HotkeyService(api, widget=Widget(), hide_delay=0.1)
    service.NOTICE_RETRY_S = 0.01
    service.NOTICE_HOLD_S = 0
    done = Future()
    done.set_result(Result())
    finish = threading.Thread(target=service._finish, args=(done, True, service._epoch))
    finish.start()
    deadline = time.monotonic() + 2
    while "copied" not in calls and time.monotonic() < deadline:
        time.sleep(0.005)
    service.show_notice("Suggested: Voisr")
    time.sleep(0.05)
    assert calls == ["copied"], "the prompt keeps the pill for its whole hold"
    finish.join(timeout=2)
    for thread in threading.enumerate():
        if thread.name == "voisr-notice":
            thread.join(timeout=2)
    assert "Suggested: Voisr" in calls, "and has it once the hold is over"
    api.close()


def test_the_notice_waits_while_a_dictation_is_in_flight(tmp_path):
    api = make_api(tmp_path)
    calls = []

    class Widget:
        def show_notice(self, text):
            calls.append(text)

        def hide(self):
            pass

    service = HotkeyService(api, widget=Widget(), hide_delay=0)
    service.NOTICE_RETRY_S = 0.01
    service.NOTICE_HOLD_S = 0
    api._sessions_in_flight.add(99)
    service.show_notice("Added X to Dictionary")
    time.sleep(0.05)
    assert calls == []
    api._sessions_in_flight.discard(99)
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == ["Added X to Dictionary"]
    api.close()


def test_a_word_cannot_break_out_of_the_dashboard_event(tmp_path):
    from voisr.suggestions import Pair

    scripts = []

    class Window:
        def evaluate_js(self, js):
            scripts.append(js)

    api = make_api(tmp_path)
    api.attach_window(Window())
    bad = 'x"}));alert(1);//'
    api._notify_dictionary([Pair("id", "heard", bad)])
    deadline = time.monotonic() + 2
    while not scripts and time.monotonic() < deadline:
        time.sleep(0.01)
    assert '\\"' in scripts[0], "the word must be a JSON-escaped string"
    api.close()


def test_new_settings_are_validated():
    from voisr.app import DEFAULT_CONFIG, config_errors

    for key in ("use_screen_context", "learn_from_edits", "dictionary_auto_add"):
        assert DEFAULT_CONFIG[key] is True
        assert config_errors({key: False}) == []
        assert config_errors({key: "yes"})


def test_no_screen_text_reaches_the_log_even_when_things_fail(tmp_path, capsys, monkeypatch):
    secret = "Hushword"
    box = textbox(f"around {secret} text ")

    class Failing(FakeAX):
        def owner(self, element):
            return super().owner(element)

    def bad_transcribe(wav, cfg, terms=()):
        if terms:
            raise RuntimeError(f"failed with prompt {terms}")
        return "ok", {"mode": "cli", "timings": {}}

    def bad_paste(text, on_sent=None, verifier=None):
        verifier("OLD")
        return True

    api = make_api(tmp_path, Failing(box), transcribe_fn=None, paste_fn=bad_paste)
    monkeypatch.setattr(api, "_default_transcribe", bad_transcribe)
    monkeypatch.setattr("voisr.ui.check_delivery",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"bad {secret}")))
    dictate(api)
    api.close()
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    for path in tmp_path.iterdir():
        if path.is_file():
            assert secret not in path.read_text(errors="replace"), path.name


def test_the_hotkey_path_only_queues_work(tmp_path):
    # the worker takes every AX call; press and release just hand it over
    ax = FakeAX(textbox("text"))
    api = make_api(tmp_path, ax)
    seen = []
    original = api._focus.worker.submit

    def spy(fn):
        seen.append(threading.current_thread().name)
        return original(fn)

    api._focus.worker.submit = spy
    api.start_recording(prewarm=False)
    assert seen == [] and ax.calls == [], "nothing before the press is a real dictation"
    api.prewarm_now()
    api._release_recording(paste=False).result(timeout=5)
    assert len(seen) == 2
    api.close()


def test_a_failed_press_capture_does_not_hide_a_good_release(tmp_path):
    from types import SimpleNamespace

    from voisr.ax_context import UNREADABLE

    class FailsOnce(FakeAX):
        fail = True

        def focused_element(self, timeout_s):
            if self.fail:
                self.fail = False
                return UNREADABLE
            return super().focused_element(timeout_s)

    recorder = FakeRecorder()
    recorder.start(tmp_path / "clip.wav")
    recorder.stop()
    api = make_api(tmp_path, FailsOnce(textbox("say hi to Priya today")))
    api._focus.capture_press(1)
    settle(api)
    api._focus.capture_release(1)
    settle(api)
    terms = api._session_terms(SimpleNamespace(sequence=1), tmp_path / "clip.wav")
    assert terms == ["Priya"]
    api.close()


def test_a_secure_press_capture_yields_no_terms_even_with_a_good_release(tmp_path):
    from types import SimpleNamespace

    secure = FakeElement(AXRole="AXTextField", AXSubrole="AXSecureTextField")
    ax = FakeAX(secure)
    recorder = FakeRecorder()
    recorder.start(tmp_path / "clip.wav")
    recorder.stop()
    api = make_api(tmp_path, ax)
    api._focus.capture_press(1)
    settle(api)
    ax.focused = textbox("say hi to Priya today")
    api._focus.capture_release(1)
    settle(api)
    assert api._session_terms(SimpleNamespace(sequence=1), tmp_path / "clip.wav") == []
    api.close()


class _Server:
    def __init__(self, fail=False):
        self.fail = fail
        self.prompts = []

    def configure(self, unload):
        pass

    def set_busy(self, busy):
        pass

    def prewarm(self, *a, **k):
        pass

    def shutdown(self):
        pass

    def unload(self):
        pass

    def transcribe(self, wav, model, prompt=None):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("server down")
        return "hello world", {}


@pytest.mark.parametrize("server, cli_runs", [
    (_Server(), False),            # kept loaded: mixed term over loopback only
    (_Server(fail=True), True),    # its fallback must not leak it
    (None, True),                  # whisper-cli by choice
])
def test_a_mixed_term_never_reaches_the_cli_command_line(tmp_path, monkeypatch, server, cli_runs):
    from voisr import transcriber

    argv = []

    class Done:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        argv.append(cmd)
        out = cmd[cmd.index("--output-file") + 1]
        with open(out + ".txt", "w") as f:
            f.write("hello world")
        return Done()

    monkeypatch.setattr(transcriber.subprocess, "run", fake_run)
    monkeypatch.setattr(transcriber, "find_binary", lambda name: "/bin/whisper-cli")
    monkeypatch.setattr(transcriber, "resolve_model", lambda *a, **k: tmp_path / "m.bin")
    (tmp_path / "m.bin").write_bytes(b"x")
    box = textbox("ask about gpt4x with Priya today", caret=0)
    api = make_api(tmp_path, FakeAX(box), transcribe_fn=None, warm_server=server)
    out = dictate(api)
    assert "error" not in out, out
    if server is not None:
        assert "gpt4x" in server.prompts[0]
    assert bool(argv) is cli_runs
    for cmd in argv:
        joined = " ".join(cmd)
        assert "gpt4x" not in joined
        assert "Priya" in joined
    api.close()


@pytest.mark.parametrize("raw, retries", [
    ("Priya, Zedwick, and Mina, all Priya Mina Zedwick Raman Priya Mina.", True),
    ("Send the Priya report to Mina today, she asked for it this morning.", False),
])
def test_only_unsupported_term_soup_is_redone(tmp_path, monkeypatch, raw, retries):
    calls = []

    def fake(wav, cfg, terms=()):
        calls.append(list(terms))
        return (raw if terms else "the real words"), {"mode": "cli", "timings": {}}

    box = textbox("x Priya met Zedwick and Mina with Raman", caret=0)
    api = make_api(tmp_path, FakeAX(box), transcribe_fn=None)
    monkeypatch.setattr(api, "_default_transcribe", fake)
    monkeypatch.setattr("voisr.ui.wav_seconds", lambda wav: 1.5)
    out = dictate(api)
    assert len(calls) == (2 if retries else 1)
    assert ("real words" in out["text"].lower()) is retries
    api.close()
