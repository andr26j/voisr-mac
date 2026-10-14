"""L2 (2026-10-12 audit): HotkeyService.press() must not hold _state_lock
while the recorder starts (1-2 s on Bluetooth, up to 5 s on the ffmpeg
fallback). The pill's if_current callbacks run on the AppKit main thread and
take that lock, so holding it across the start froze the whole UI."""
import threading
import time
from concurrent.futures import Future

from voisr.recording_queue import SessionResult
from voisr.ui import HotkeyService

PROMPT = 1.0  # seconds: anything that waits on the start would take ~forever


class BlockingApi:
    """start_recording() blocks until `go` is set, like a slow recorder."""

    def __init__(self, result=None):
        self.result = result or {"recording": True}
        self.go = threading.Event()
        self.entered = threading.Event()
        self.starts = 0
        self.releases = 0
        self.cancels = 0
        # closing the mic can block too; cancels run free unless a test clears it
        self.cancel_go = threading.Event()
        self.cancel_go.set()
        self.cancel_entered = threading.Event()

    def get_config(self):
        return {"show_widget": True}

    def start_recording(self):
        self.starts += 1
        self.entered.set()
        assert self.go.wait(5)
        if isinstance(self.result, BaseException):
            raise self.result
        return dict(self.result)

    def _release_recording(self, paste=False):
        self.releases += 1
        future = Future()
        future.set_result(SessionResult.success(1, text="hello"))
        return future

    def _cancel_recording(self):
        self.cancels += 1
        self.cancel_entered.set()
        assert self.cancel_go.wait(5)
        return True


class FakeWidget:
    def __init__(self):
        self.calls = []

    def show_recording(self, theme="classic", color=None):
        self.calls.append("recording")

    def show_processing(self):
        self.calls.append("processing")

    def show_done(self):
        self.calls.append("done")

    def show_error(self):
        self.calls.append("error")

    def hide(self):
        self.calls.append("hide")


def in_thread(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


def returns_promptly(fn, *args):
    t = in_thread(fn, *args)
    t.join(PROMPT)
    return not t.is_alive()


def start_blocked_press(svc, api, **kwargs):
    t = in_thread(lambda: svc.press(**kwargs))
    assert api.entered.wait(2)
    return t


def finish_start(api, press_thread):
    api.go.set()
    press_thread.join(PROMPT)
    assert not press_thread.is_alive()


def wait_for_hide(widget, timeout=3.0):
    deadline = time.monotonic() + timeout
    while widget.calls[-1:] != ["hide"] and time.monotonic() < deadline:
        time.sleep(0.02)


def test_main_thread_widget_callback_runs_while_the_recorder_starts():
    api = BlockingApi()
    svc = HotkeyService(api, widget=FakeWidget(), hide_delay=0)
    press = start_blocked_press(svc, api)
    ran = []
    # what an if_current callback queued by an earlier pill does on AppKit
    callback = in_thread(svc._run_widget_action, svc._epoch,
                         lambda: ran.append(True))
    callback.join(PROMPT)
    stalled = callback.is_alive()
    finish_start(api, press)
    assert not stalled and ran == [True]


def test_auto_repeat_while_starting_neither_waits_nor_starts_twice():
    api = BlockingApi()
    svc = HotkeyService(api, widget=FakeWidget(), hide_delay=0)
    press = start_blocked_press(svc, api)
    repeat = in_thread(svc.press)
    repeat.join(PROMPT)
    stalled = repeat.is_alive()
    finish_start(api, press)
    repeat.join(PROMPT)
    assert not stalled
    assert api.starts == 1


def test_release_mid_start_ends_and_transcribes_the_recording():
    api = BlockingApi()
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    press = start_blocked_press(svc, api)
    release = in_thread(svc.release)
    release.join(PROMPT)
    stalled = release.is_alive()
    finish_start(api, press)
    wait_for_hide(widget)
    assert not stalled
    assert api.releases == 1 and api.cancels == 0
    assert not svc.holding
    assert widget.calls == ["recording", "processing", "done", "hide"]


def test_toggle_off_mid_start_ends_the_recording():
    # Caps Lock and the menu-bar Dictate: the second toggle is the release
    api = BlockingApi()
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    press = in_thread(svc._toggle)
    assert api.entered.wait(2)
    toggle = in_thread(svc._toggle)
    toggle.join(PROMPT)
    stalled = toggle.is_alive()
    finish_start(api, press)
    wait_for_hide(widget)
    assert not stalled
    assert api.starts == 1 and api.releases == 1
    assert not svc.holding


def test_chord_mid_start_cancels_once_the_start_completes():
    api = BlockingApi()
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    press = start_blocked_press(svc, api)
    chord = in_thread(svc.cancel_chord)
    chord.join(PROMPT)
    stalled = chord.is_alive()
    finish_start(api, press)
    assert not stalled
    assert api.cancels == 1 and api.releases == 0
    assert not svc.holding
    assert "recording" not in widget.calls
    assert widget.calls[-1:] == ["hide"]


def test_release_after_a_mid_start_chord_does_not_also_release():
    api = BlockingApi()
    svc = HotkeyService(api, widget=FakeWidget(), hide_delay=0)
    press = start_blocked_press(svc, api)
    assert returns_promptly(svc.cancel_chord)
    assert returns_promptly(svc.release)
    finish_start(api, press)
    assert api.cancels == 1 and api.releases == 0


def test_chord_after_a_mid_start_release_is_just_typing():
    api = BlockingApi()
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    press = start_blocked_press(svc, api)
    assert returns_promptly(svc.release)
    assert returns_promptly(svc.cancel_chord)
    finish_start(api, press)
    wait_for_hide(widget)
    assert api.releases == 1 and api.cancels == 0


def test_chord_mid_start_leaves_a_latched_toggle_recording():
    api = BlockingApi()
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    press = start_blocked_press(svc, api, latched=True)
    in_thread(svc.cancel_chord).join(PROMPT)
    finish_start(api, press)
    assert api.cancels == 0
    assert svc.holding
    assert widget.calls == ["recording"]


def test_refused_start_released_mid_start_flashes_once_and_rearms():
    api = BlockingApi(result={"error": "busy"})
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    press = start_blocked_press(svc, api)
    in_thread(svc.release).join(PROMPT)
    finish_start(api, press)
    wait_for_hide(widget)
    assert widget.calls == ["error", "hide"]
    assert api.releases == 0 and not svc.holding
    # the key is already up, so the next press is a fresh one and may flash
    assert svc._rejected is False


def test_refused_start_still_held_keeps_the_once_per_press_guard():
    api = BlockingApi(result={"error": "busy"})
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    press = start_blocked_press(svc, api)
    finish_start(api, press)
    wait_for_hide(widget)
    svc.press()  # auto-repeat of the held key: refused again, no new flash
    wait_for_hide(widget)
    assert widget.calls == ["error", "hide"]
    assert svc._rejected is True and not svc.holding


def test_a_start_that_raises_flashes_an_error_and_does_not_wedge_the_key():
    api = BlockingApi(result=RuntimeError("recorder exploded"))
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    api.go.set()
    svc.press()
    wait_for_hide(widget)
    assert widget.calls == ["error", "hide"]
    assert not svc.holding
    svc.release()
    api.result = {"recording": True}
    svc.press()
    assert api.starts == 2 and svc.holding


def test_a_stale_finish_cannot_touch_the_pill_of_a_start_in_flight():
    # the earlier dictation's _finish lands while the next start is in flight;
    # once that start succeeds it owns the pill, so the old hide must not run
    api = BlockingApi()
    svc = HotkeyService(api, widget=FakeWidget(), hide_delay=0)
    api.go.set()
    svc.press()
    old_epoch = svc._epoch
    svc.release()
    api.go.clear()
    api.entered.clear()
    press = start_blocked_press(svc, api)
    finish_start(api, press)
    ran = []
    svc._run_widget_action(old_epoch, lambda: ran.append(True))
    assert ran == [] and svc._epoch == old_epoch + 1


def test_main_thread_widget_callback_runs_while_a_chord_closes_the_mic():
    api = BlockingApi()
    svc = HotkeyService(api, widget=FakeWidget(), hide_delay=0)
    api.go.set()
    svc.press()
    api.cancel_go.clear()
    chord = in_thread(svc.cancel_chord)
    assert api.cancel_entered.wait(2)
    callback_ran = returns_promptly(svc._run_widget_action, svc._epoch,
                                    lambda: None)
    api.cancel_go.set()
    chord.join(PROMPT)
    assert callback_ran
    assert api.cancels == 1 and not svc.holding


def test_main_thread_widget_callback_runs_while_a_mid_start_chord_closes_the_mic():
    api = BlockingApi()
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    api.cancel_go.clear()
    press = start_blocked_press(svc, api)
    assert returns_promptly(svc.cancel_chord)
    api.go.set()
    assert api.cancel_entered.wait(2)
    callback_ran = returns_promptly(svc._run_widget_action, svc._epoch,
                                    lambda: None)
    api.cancel_go.set()
    press.join(PROMPT)
    assert callback_ran
    assert api.cancels == 1 and not svc.holding
    assert widget.calls[-1:] == ["hide"]


def test_a_start_that_raises_is_logged(capsys):
    api = BlockingApi(result=RuntimeError("recorder exploded"))
    svc = HotkeyService(api, widget=FakeWidget(), hide_delay=0)
    api.go.set()
    svc.press()
    assert "recorder exploded" in capsys.readouterr().out


def test_a_start_interrupted_by_a_base_exception_does_not_wedge_the_key():
    import pytest

    api = BlockingApi(result=KeyboardInterrupt())
    svc = HotkeyService(api, widget=FakeWidget(), hide_delay=0)
    api.go.set()
    with pytest.raises(KeyboardInterrupt):
        svc.press()
    assert not svc.holding
    api.result = {"recording": True}
    svc.press()
    assert api.starts == 2 and svc.holding


class NullTimer:
    def start(self):
        pass

    def cancel(self):
        pass


def test_a_quick_release_mid_start_still_waits_for_a_second_press():
    # the mic took a second to open, but the key was only down 0.2 s
    api = BlockingApi()
    delays = []
    svc = HotkeyService(api, widget=FakeWidget(), hide_delay=0, tap_gate_s=0.5,
                        timer_factory=lambda d, fn: delays.append(d) or NullTimer())
    now = [0.0]
    svc._clock = lambda: now[0]
    press = start_blocked_press(svc, api)
    assert returns_promptly(svc.release, 0.2)
    now[0] = 1.0
    finish_start(api, press)
    assert api.releases == 0 and api.cancels == 0
    assert svc.holding
    assert delays == [0.0]  # the window closed while the mic was opening


def test_esc_mid_start_cancels_once_the_start_completes():
    api = BlockingApi()
    widget = FakeWidget()
    svc = HotkeyService(api, widget=widget, hide_delay=0)
    press = start_blocked_press(svc, api)
    assert returns_promptly(svc.cancel_escape)
    finish_start(api, press)
    assert api.cancels == 1 and api.releases == 0
    assert not svc.holding
    assert "recording" not in widget.calls
