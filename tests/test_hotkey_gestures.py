"""Robustness spec item 3: the hotkey's gestures on a fake clock. A quick
tap is dropped, a double-tap goes hands-free, a chord cancels only early,
Esc cancels anything, and a key whose release was lost is let go."""
import inspect
from concurrent.futures import Future

import pytest

from voisr import ui
from voisr.recording_queue import SessionResult
from voisr.ui import HotkeyService

RIGHT_OPTION_VK, RIGHT_OPTION_MASK = 61, 0x40
F13_VK = 105
E_VK = 14
ESC_VK = 53


class GestureApi:
    """Records what the service asked for, in order."""

    def __init__(self):
        self.log = []

    def get_config(self):
        return {"show_widget": True}

    def start_recording(self):
        self.log.append("start")
        return {"recording": True}

    def _release_recording(self, paste=False):
        self.log.append("stop")
        future = Future()
        future.set_result(SessionResult.success(1, text="hi"))
        return future

    def _cancel_recording(self):
        self.log.append("cancel")
        return True

    def note_user_typing(self):
        pass


class FakeTimer:
    def __init__(self, delay, fn):
        self.delay, self.fn = delay, fn
        self.started = self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


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


class Rig:
    """A service with no worker (ops run inline), a fake clock and fake
    timers, listening for `vk`."""

    def __init__(self, vk=RIGHT_OPTION_VK, gate=ui.TAP_GATE_S, widget=None,
                 **service_kw):
        self.api = GestureApi()
        self.timers = []
        self.now = 0.0
        self.vk = vk
        self.svc = HotkeyService(
            self.api, widget=widget, hide_delay=0, tap_gate_s=gate,
            timer_factory=lambda d, fn: self.timers.append(FakeTimer(d, fn))
            or self.timers[-1],
            listener_factory=lambda handler, mode: handler, **service_kw)
        self.svc._clock = lambda: self.now
        self.svc._stopped = False
        self.svc._target_vk = vk
        self.svc._spawn_listener()
        self.handler = self.svc._listener

    def at(self, t):
        self.now = t
        return self

    def down(self, t):
        self.now = t
        if self.vk == RIGHT_OPTION_VK:
            self.handler(self.vk, "flags", RIGHT_OPTION_MASK)
        else:
            self.handler(self.vk, "down", 0)

    def up(self, t):
        self.now = t
        if self.vk == RIGHT_OPTION_VK:
            self.handler(self.vk, "flags", 0)
        else:
            self.handler(self.vk, "up", 0)

    def other(self, vk, t):
        self.now = t
        self.handler(vk, "down", RIGHT_OPTION_MASK)


# ---- tap gate ---------------------------------------------------------------

def test_the_gate_defaults_to_half_a_second():
    assert ui.TAP_GATE_S == 0.5
    default = inspect.signature(HotkeyService.__init__).parameters["tap_gate_s"]
    assert default.default == ui.TAP_GATE_S


def test_a_hold_past_the_gate_dictates():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.6)  # the shortest real dictation in the user's history: 0.67 s
    assert rig.api.log == ["start", "stop"]
    assert rig.timers == []


def test_a_release_exactly_at_the_gate_dictates():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.5)
    assert rig.api.log == ["start", "stop"]


def test_a_quick_tap_keeps_recording_until_the_gate_runs_out():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    assert rig.api.log == ["start"]  # still recording: a second press may follow
    assert rig.svc.holding
    [timer] = rig.timers
    assert timer.started and timer.delay == pytest.approx(0.5)
    timer.fire()
    assert rig.api.log == ["start", "cancel"]
    assert not rig.svc.holding


def test_a_dropped_tap_shows_no_error_and_no_processing():
    widget = FakeWidget()
    rig = Rig(widget=widget)
    rig.down(0.0)
    rig.up(0.1)
    rig.timers[0].fire()
    assert widget.calls == ["recording", "hide"]


def test_the_gate_is_measured_on_the_keyboard_clock_not_the_worker():
    # the worker ran both ops 2 s late (a slow mic start): the hold was
    # still 0.3 s, and the second-press window is already over
    rig = Rig()
    rig.svc.press(at=0.0)
    rig.at(2.0).svc.release(at=0.3)
    assert rig.api.log == ["start"]
    assert rig.timers[0].delay == 0.0


def test_key_events_are_timed_when_the_key_moves():
    # the handler stamps each event; the worker runs it whenever it can
    rig = Rig()
    queued = []
    rig.svc._submit = queued.append
    rig.down(0.0)
    rig.up(0.6)
    rig.now = 3.0  # the worker gets to both only now
    for op in queued:
        op()
    assert rig.api.log == ["start", "stop"]  # a 0.6 s hold, not a 0 s tap


def test_the_next_dictation_works_after_a_dropped_tap():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.timers[0].fire()
    rig.down(5.0)
    rig.up(7.0)
    assert rig.api.log == ["start", "cancel", "start", "stop"]


def test_a_late_second_press_drops_the_tap_and_starts_afresh():
    # 0.6 s after the release: too late for a double-tap, and the timer
    # hasn't run yet (the worker is behind)
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.down(0.8)
    assert rig.api.log == ["start", "cancel", "start"]
    assert rig.timers[0].cancelled
    rig.up(2.0)
    assert rig.api.log == ["start", "cancel", "start", "stop"]


def test_typing_right_after_a_quick_tap_drops_it_at_once():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.other(E_VK, 0.3)
    assert rig.api.log == ["start", "cancel"]
    assert rig.timers[0].cancelled
    assert not rig.svc.holding


def test_a_stale_timer_cannot_drop_a_later_dictation():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.down(0.9)  # late: the first tap is dropped, a new dictation starts
    rig.up(1.0)    # and is a quick tap too, waiting on its own timer
    rig.timers[0].cancelled = False  # as if it fired before cancel() landed
    rig.timers[0].fire()
    assert rig.api.log == ["start", "cancel", "start"]
    assert rig.svc.holding
    rig.timers[1].fire()
    assert rig.api.log == ["start", "cancel", "start", "cancel"]


# ---- double-tap latch -------------------------------------------------------

def test_a_double_tap_goes_hands_free_until_the_next_press():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.down(0.4)
    rig.up(0.5)
    assert rig.api.log == ["start"]  # one recording, still going
    assert rig.timers[0].cancelled
    rig.up(0.6)  # a stray edge changes nothing
    assert rig.api.log == ["start"]
    rig.down(9.0)
    assert rig.api.log == ["start", "stop"]
    rig.up(9.1)
    assert rig.api.log == ["start", "stop"]
    assert not rig.svc.holding


def test_the_second_press_is_timed_from_the_release():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.4)
    rig.down(0.9)  # 0.9 s after the first press, 0.5 s after its release
    rig.up(1.0)
    assert rig.api.log == ["start"]
    rig.down(4.0)
    assert rig.api.log == ["start", "stop"]


def test_auto_repeat_of_the_second_press_does_not_stop_it():
    rig = Rig(vk=F13_VK)
    rig.down(0.0)
    rig.up(0.2)
    rig.down(0.4)
    rig.down(0.45)  # auto-repeat while still held
    rig.down(0.5)
    assert rig.api.log == ["start"]
    rig.up(0.7)
    rig.down(3.0)
    assert rig.api.log == ["start", "stop"]


def test_typing_during_a_hands_free_dictation_is_left_alone():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.down(0.4)
    rig.up(0.5)
    rig.other(E_VK, 0.8)
    assert rig.api.log == ["start"]


def test_the_menu_stops_a_double_tapped_dictation():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.down(0.4)
    rig.up(0.5)
    rig.svc._toggle()
    assert rig.api.log == ["start", "stop"]


def test_the_hotkey_stops_a_menu_started_dictation():
    rig = Rig()
    rig.svc._toggle()  # menu "Dictate": hands-free from the start
    rig.down(3.0)
    assert rig.api.log == ["start", "stop"]
    rig.up(3.1)
    assert rig.api.log == ["start", "stop"]


def test_a_menu_start_skips_the_gate():
    rig = Rig()
    rig.svc._toggle()
    rig.at(0.1).svc._toggle()
    assert rig.api.log == ["start", "stop"]
    assert rig.timers == []


def test_the_menu_ends_a_waiting_tap_as_a_dictation():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.svc._toggle()  # menu "Stop" within the gate: the person meant it
    assert rig.api.log == ["start", "stop"]
    assert rig.timers[0].cancelled


# ---- chord window -------------------------------------------------------------

def test_a_chord_in_the_first_second_cancels():
    rig = Rig()
    rig.down(0.0)
    rig.other(E_VK, 0.9)
    assert rig.api.log == ["start", "cancel"]
    rig.up(1.0)
    assert rig.api.log == ["start", "cancel"]


def test_a_key_after_a_second_of_dictation_is_typing():
    rig = Rig()
    rig.down(0.0)
    rig.other(E_VK, 1.0)
    assert rig.api.log == ["start"]
    rig.up(4.0)
    assert rig.api.log == ["start", "stop"]


# ---- Esc ----------------------------------------------------------------------

def test_esc_cancels_a_held_dictation_after_the_chord_window():
    widget = FakeWidget()
    rig = Rig(widget=widget)
    rig.down(0.0)
    rig.other(ESC_VK, 3.0)
    assert rig.api.log == ["start", "cancel"]
    assert widget.calls == ["recording", "hide"]
    rig.up(3.5)  # nothing left to stop, nothing pasted
    assert rig.api.log == ["start", "cancel"]


def test_esc_cancels_a_hands_free_dictation():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.down(0.4)
    rig.up(0.5)
    rig.other(ESC_VK, 5.0)
    assert rig.api.log == ["start", "cancel"]
    assert not rig.svc.holding
    rig.down(8.0)  # the next press starts a new dictation, not a stop
    assert rig.api.log == ["start", "cancel", "start"]


def test_esc_cancels_a_menu_started_dictation():
    rig = Rig()
    rig.svc._toggle()
    rig.other(ESC_VK, 2.0)
    assert rig.api.log == ["start", "cancel"]


def test_esc_cancels_a_waiting_tap():
    rig = Rig()
    rig.down(0.0)
    rig.up(0.2)
    rig.other(ESC_VK, 0.3)
    assert rig.api.log == ["start", "cancel"]
    assert rig.timers[0].cancelled


def test_esc_with_nothing_recording_does_nothing():
    rig = Rig()
    rig.other(ESC_VK, 1.0)
    assert rig.api.log == []


def test_esc_as_the_hotkey_dictates():
    rig = Rig(vk=ESC_VK)
    rig.down(0.0)
    rig.up(2.0)
    assert rig.api.log == ["start", "stop"]


def wait_for(cond, timeout=2.0):
    import time
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()


# ---- stuck keys ---------------------------------------------------------------

class Keyboard:
    """The keyboard's real state, as hotkey_key_down would read it."""

    def __init__(self, reading=None):
        self.down = set()
        self.reading = reading  # overrides: False (blind), None (unknown), raise
        self.reads = 0

    def __call__(self, vk):
        self.reads += 1
        if self.reading == "raise":
            raise RuntimeError("no Quartz")
        if self.reading != "real":
            return self.reading
        return vk in self.down


def held_then_lost(keyboard, at=0.0):
    """Press (the probe sees the key down, so it is trusted), then let go
    with the release event lost."""
    rig = Rig(key_state=keyboard)
    keyboard.down.add(RIGHT_OPTION_VK)
    rig.down(at)
    keyboard.down.discard(RIGHT_OPTION_VK)  # no up event: the tap missed it
    return rig


def test_a_key_up_without_its_release_is_let_go_after_ten_seconds(capsys):
    rig = held_then_lost(Keyboard("real"))
    rig.at(10.0).svc._check_stale_key()
    assert rig.api.log == ["start", "stop"]
    assert "release never arrived" in capsys.readouterr().out


def test_a_hold_under_ten_seconds_is_not_checked():
    keyboard = Keyboard("real")
    rig = held_then_lost(keyboard)
    reads = keyboard.reads
    rig.at(9.9).svc._check_stale_key()
    assert rig.api.log == ["start"]
    assert keyboard.reads == reads


def test_a_key_still_down_keeps_dictating():
    keyboard = Keyboard("real")
    rig = Rig(key_state=keyboard)
    keyboard.down.add(RIGHT_OPTION_VK)
    rig.down(0.0)
    rig.at(30.0).svc._check_stale_key()
    assert rig.api.log == ["start"]


def test_a_blind_probe_never_ends_a_long_dictation():
    # a probe macOS never lets read the key (always False) is never trusted
    rig = Rig(key_state=Keyboard(False))
    rig.down(0.0)
    rig.at(60.0).svc._check_stale_key()
    assert rig.api.log == ["start"]


def test_an_unknown_or_raising_reading_is_never_a_release():
    for reading in (None, "raise"):
        keyboard = Keyboard("real")
        rig = held_then_lost(keyboard)
        keyboard.reading = reading
        rig.at(20.0).svc._check_stale_key()
        assert rig.api.log == ["start"], reading


def test_a_reenabled_tap_checks_at_once():
    rig = held_then_lost(Keyboard("real"))
    rig.now = 2.0
    rig.handler(-1, "reenabled", 0)
    assert rig.api.log == ["start", "stop"]


def test_a_hands_free_dictation_is_never_stale():
    keyboard = Keyboard("real")
    rig = Rig(key_state=keyboard)
    keyboard.down.add(RIGHT_OPTION_VK)
    rig.down(0.0)
    keyboard.down.discard(RIGHT_OPTION_VK)
    rig.up(0.2)
    rig.down(0.4)  # a latch, not a start: the probe isn't asked here
    rig.up(0.5)
    reads = keyboard.reads
    rig.at(60.0).svc._check_stale_key()
    assert rig.api.log == ["start"]
    assert keyboard.reads == reads  # the key is up by design: nothing to read


def test_no_probe_means_no_check_and_no_ticker():
    rig = Rig(key_state=None)
    rig.down(0.0)
    rig.at(60.0).svc._check_stale_key()
    rig.svc._start_ticker()
    assert rig.api.log == ["start"]
    assert rig.svc._ticker is None


def test_the_ticker_queues_checks_until_stop():
    rig = Rig(key_state=Keyboard("real"))
    rig.svc._stale_interval = 0.01
    checks = []
    rig.svc._check_stale_key = lambda: checks.append(1)
    rig.svc._start_ticker()
    assert wait_for(lambda: len(checks) >= 2)
    ticker = rig.svc._ticker
    rig.svc.stop()
    assert rig.svc._ticker is None and not ticker.is_alive()


def test_hotkey_key_down_reads_quartz_and_never_raises(monkeypatch):
    import sys
    from types import SimpleNamespace
    calls = []
    fake = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        CGEventSourceKeyState=lambda state, vk: calls.append((state, vk)) or 1)
    monkeypatch.setitem(sys.modules, "Quartz", fake)
    assert ui.hotkey_key_down(61) is True
    assert calls == [(1, 61)]
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace())
    assert ui.hotkey_key_down(61) is None
