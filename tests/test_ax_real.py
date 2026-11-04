"""The real Accessibility API and a real Cmd-V, against TextEdit: capture ->
paste -> verify -> edit -> suggestion. Opt-in like the clipboard test, since it
takes focus for a few seconds and briefly uses the clipboard (and restores it):

    VOISR_RUN_AX_INTEGRATION=1 python3 -m pytest -q tests/test_ax_real.py -s

Needs Accessibility for the app running pytest. Opens its own temp file and
closes it; other TextEdit windows are left alone."""
import os
import subprocess
import time
import wave

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VOISR_RUN_AX_INTEGRATION") != "1",
    reason="set VOISR_RUN_AX_INTEGRATION=1 to run")

DOC_MARK = "voisr-ax-test"


def osa(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True,
                          text=True, timeout=20, check=True).stdout.rstrip("\n")


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


def quit_textedit():
    # bounded, and not checked: a dialog must not hang the run or fail the test
    subprocess.run(["osascript", "-e", 'with timeout of 8 seconds\n'
                    'tell application "TextEdit" to quit\nend timeout'],
                   capture_output=True, timeout=20)


@pytest.fixture
def textedit(tmp_path):
    """A temp file, not a new untitled document: with iCloud on, TextEdit saves
    those as Untitled.rtf in iCloud Drive, and reopens them at every launch."""
    was_running = "true" == osa('application "TextEdit" is running')
    path = tmp_path / f"{DOC_MARK}-{os.getpid()}.txt"
    path.write_text(f"{DOC_MARK} Priya Raman wrote: ")
    subprocess.run(["open", "-a", "TextEdit", str(path)], check=True, timeout=20)
    osa('tell application "TextEdit" to activate')
    try:
        yield path.name
    finally:
        osa(f'''with timeout of 8 seconds
            tell application "TextEdit" to close document "{path.name}" saving no
        end timeout''')
        if not was_running:
            quit_textedit()


def spin_run_loop(seconds=0.05):
    """NSWorkspace learns the frontmost app through the main run loop, which
    a test has to turn by hand; the app's is always running."""
    from AppKit import NSDate, NSRunLoop
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def wait_focused(adapter, bundle_id="com.apple.TextEdit", timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        spin_run_loop()
        element = adapter.focused_element(0.2)
        if element and adapter.owner(element)[0] == bundle_id:
            return element
        time.sleep(0.05)
    pytest.skip(f"{bundle_id} never took focus")


def test_capture_paste_verify_edit_suggestion(tmp_path, textedit, monkeypatch):
    from voisr.ax_context import MacAXAdapter, capture_focus
    from voisr.history import HistoryStore
    from voisr.inject import prepare_paste_controller, paste_at_cursor
    from voisr.pasteboard import AppKitPasteboard
    from voisr.ui import Api

    monkeypatch.setattr("voisr.speech_gate.gate_wav", lambda wav: None)
    adapter = MacAXAdapter()
    if not adapter._hi.AXIsProcessTrusted():
        pytest.skip("this process has no Accessibility grant")
    wait_focused(adapter)

    focus = capture_focus(adapter, budget_s=1.0)
    assert focus.readable and focus.bundle_id == "com.apple.TextEdit"
    assert focus.role == "AXTextArea"
    assert DOC_MARK in focus.value

    prepare_paste_controller()
    clipboard_before = AppKitPasteboard().snapshot()
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "history.jsonl"),
              recorder_factory=FakeRecorder, ax_adapter=adapter,
              transcribe_fn=lambda wav: "voiser is great",
              paste_fn=paste_at_cursor)
    try:
        for _ in range(2):
            api.start_recording(prewarm=False)
            api.prewarm_now()
            api._focus.worker.submit(lambda: None).result(timeout=5)
            out = api.stop_recording(paste=True)
            assert out["delivery"] == "verified", out
            assert out["pasted"] is True
            item = api.get_history()[0]
            print("timings:", item["timings_ms"], "app:", item["app"])
            assert item["app"] == "com.apple.TextEdit"
            assert item["timings_ms"]["verify"] <= 300
            # the user fixes the word
            current = osa(f'tell application "TextEdit" to get text of document "{textedit}"')
            assert "Voiser is great" in current
            fixed = current.replace("Voiser is great", "Voisr is great")
            osa(f'tell application "TextEdit" to set text of document "{textedit}" to "{fixed}"')
            watcher = api._edit_watcher
            with watcher._cond:
                windows = list(watcher._windows)
            assert len(windows) == 1, "a verified paste opens a window"
            far = time.monotonic() + 3600
            for offset in range(2):
                watcher.tick(far + offset * 100)    # a changed read waits for its recheck
            assert windows[0].done
            item = api.get_history()[0]
            print("edit:", item.get("edit_end_reason"), item.get("words_corrected"))
            assert item["words_corrected"] == 1

        assert api.get_config()["dictionary"] == {"voiser": "Voisr"}
        assert api.get_suggestions()["auto_added"] == {"voiser": "Voisr"}
        assert "Priya" not in (tmp_path / "history.jsonl").read_text()
        assert "Priya" not in (tmp_path / "suggestions.json").read_text()
    finally:
        api.close()
    # back to what it was
    after = AppKitPasteboard().snapshot()
    assert [i.payloads for i in after.items] == [i.payloads for i in clipboard_before.items]


def dictate_snippet(api):
    api.start_recording(prewarm=False)
    api.prewarm_now()
    api._focus.worker.submit(lambda: None).result(timeout=5)
    return api.stop_recording(paste=True)


def snippet_api(tmp_path, adapter, saved):
    from voisr.history import HistoryStore
    from voisr.inject import paste_at_cursor, prepare_paste_controller
    from voisr.ui import Api
    from tests.test_snippets_guard import ClipRecorder, spoken

    prepare_paste_controller()
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "history.jsonl"),
              recorder_factory=type("R", (ClipRecorder,), {"samples": spoken()}),
              ax_adapter=adapter,
              transcribe_fn=lambda wav: "My email.", paste_fn=paste_at_cursor)
    assert "error" not in api.set_config({"snippets": {"my email": saved}})
    return api


def test_a_long_multi_line_snippet_lands_exactly_and_teaches_nothing(tmp_path, textedit):
    from voisr.ax_context import MacAXAdapter
    from voisr.pasteboard import AppKitPasteboard

    adapter = MacAXAdapter()
    if not adapter._hi.AXIsProcessTrusted():
        pytest.skip("this process has no Accessibility grant")
    wait_focused(adapter)
    saved = "Please review:\n\n  - um, the diff\n  - the tests\n\n" + "Keep it short. " * 200 + "\n"
    clipboard_before = AppKitPasteboard().snapshot()
    api = snippet_api(tmp_path, adapter, saved)
    try:
        out = dictate_snippet(api)
        print("delivery:", out.get("delivery"), api.get_history()[0].get("timings_ms"))
        assert out["delivery"] == "verified", out
        assert out["pasted"] is True and "manual_paste" not in out
        assert saved in adapter.attribute(wait_focused(adapter), "AXValue", 1.0)
        assert api._edit_watcher._windows == []
        item = api.get_history()[0]
        assert item["text"] == "my email" and item["snippet"] is True
        assert "Keep it short" not in (tmp_path / "history.jsonl").read_text()
    finally:
        api.close()
    after = AppKitPasteboard().snapshot()
    assert [i.payloads for i in after.items] == [i.payloads for i in clipboard_before.items]


def test_a_snippet_in_a_real_password_field(tmp_path):
    """A hidden-answer dialog, whose focused field is a real secure one. What a
    hotkey does under Secure Input can't be tested here: no key is pressed."""
    from voisr.ax_context import MacAXAdapter, capture_focus

    adapter = MacAXAdapter()
    if not adapter._hi.AXIsProcessTrusted():
        pytest.skip("this process has no Accessibility grant")
    dialog = subprocess.Popen(
        ["osascript", "-e", "tell me to activate", "-e",
         f'display dialog "{DOC_MARK}" default answer "" with hidden answer giving up after 15'],
        stdout=subprocess.PIPE, text=True)
    api = None
    try:
        end = time.monotonic() + 6
        while True:
            spin_run_loop()
            focus = capture_focus(adapter, budget_s=0.5)
            if focus.secure:
                break
            if time.monotonic() > end:
                pytest.skip(f"the dialog's field never took focus: {focus.reason}")
        saved = "not-a-real-password-123"
        api = snippet_api(tmp_path, adapter, saved)
        out = dictate_snippet(api)
        print("result:", {k: out.get(k) for k in ("delivery", "pasted", "copied", "manual_paste")})
        item = api.get_history()[0]
        print("row:", {k: v for k, v in item.items() if k != "timings_ms"})
        assert out["delivery"] == "skipped" and "manual_paste" not in out
        assert item["delivery_reason"] == "secure" and "app" not in item
        assert item["text"] == "my email"
        assert saved not in (tmp_path / "history.jsonl").read_text()
        assert api._edit_watcher._windows == []
        osa('tell application "System Events" to key code 36')  # Return: OK
        answer = dialog.communicate(timeout=10)[0]
        print("dialog got the text:", saved in answer)
        assert f"text returned:{saved}" in answer
    finally:
        if api is not None:
            api.close()
        if dialog.poll() is None:
            dialog.terminate()
