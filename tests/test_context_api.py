"""The context features as the Api drives them, with a fake AX adapter."""
import threading
import time
import wave

import pytest

from tests.fake_ax import FakeAX, textbox
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


def make_api(tmp_path, ax=None, **kwargs):
    kwargs.setdefault("transcribe_fn", lambda wav: "hello world")
    kwargs.setdefault("paste_fn", lambda text: True)
    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "history.jsonl"),
               recorder_factory=FakeRecorder,
               clipboard_fn=lambda text: None,
               ax_adapter=ax, **kwargs)


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
    api = make_api(tmp_path, ax, paste_fn=lambda text: pasted.append(text) or True)
    started = time.monotonic()
    api.start_recording(prewarm=False)
    api.prewarm_now()
    assert ax.entered.wait(2)
    future = api._release_recording(paste=True)
    assert time.monotonic() - started < 0.5, "the hotkey path waited on AX"
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
    api = make_api(tmp_path, ax, transcribe_fn=None)
    monkeypatch.setattr(api, "_default_transcribe",
                        lambda wav, cfg, terms=(): seen.append(list(terms)) or ("ok", {"mode": "cli", "timings": {}}))
    api.start_recording(prewarm=False)
    api.prewarm_now()
    assert ax.entered.wait(2)
    started = time.monotonic()
    out = api.stop_recording(paste=False)
    assert time.monotonic() - started < 0.5
    assert seen == [[]] and "error" not in out
    ax.release.set()
    api.close()
