"""QuartzKeyListener against a fake Quartz: which tap option each mode asks
for, what a failed tap reports, and that the callback always passes events
through (an active tap that swallowed or crashed would freeze the keyboard)."""
import threading
import time

import pytest

from voisr.keytap import TAP_MODES, QuartzKeyListener


class FakeQuartz:
    kCGEventKeyDown, kCGEventKeyUp, kCGEventFlagsChanged = 10, 11, 12
    kCGSessionEventTap, kCGHeadInsertEventTap = 1, 0
    kCGEventTapOptionDefault, kCGEventTapOptionListenOnly = 0, 1
    kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput = 0xFFFFFFFE, 0xFFFFFFFF
    kCGEventSourceUnixProcessID, kCGKeyboardEventKeycode = 41, 9
    kCFRunLoopDefaultMode = "default"

    def __init__(self, tap="TAP"):
        self.tap = tap
        self.created = None
        self.callback = None
        self.enabled = []  # (tap, enable) for every CGEventTapEnable call
        self.release = threading.Event()

    def CGEventMaskBit(self, t):
        return 1 << t

    def CGEventTapCreate(self, where, place, option, mask, callback, refcon):
        self.created = option
        self.callback = callback
        return self.tap

    def CFMachPortCreateRunLoopSource(self, *_):
        return "SRC"

    def CFRunLoopGetCurrent(self):
        return "LOOP"

    def CFRunLoopAddSource(self, *_):
        pass

    def CGEventTapEnable(self, tap, enable):
        self.enabled.append((tap, enable))

    def CFRunLoopRun(self):
        self.release.wait(2)

    def CFRunLoopStop(self, _loop):
        self.release.set()

    def CGEventGetIntegerValueField(self, event, field):
        return event[field]

    def CGEventGetFlags(self, event):
        return event["flags"]


def _event(vk, pid=999, flags=0):
    return {FakeQuartz.kCGEventSourceUnixProcessID: pid,
            FakeQuartz.kCGKeyboardEventKeycode: vk, "flags": flags}


def _start(mode, quartz, handler=lambda *a: None):
    listener = QuartzKeyListener(handler, mode=mode, quartz=quartz)
    listener.start()
    return listener


def test_modes_are_listen_and_active():
    assert TAP_MODES == ("listen", "active")


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        QuartzKeyListener(lambda *a: None, mode="grab")


@pytest.mark.parametrize("mode, option", [
    ("listen", FakeQuartz.kCGEventTapOptionListenOnly),
    ("active", FakeQuartz.kCGEventTapOptionDefault),
])
def test_each_mode_creates_its_tap_option(mode, option):
    q = FakeQuartz()
    listener = _start(mode, q)
    assert listener.wait_started(1) == "running"
    assert q.created == option
    listener.stop()


def test_a_tap_macos_refuses_reports_failed():
    listener = _start("active", FakeQuartz(tap=None))
    assert listener.wait_started(1) == "failed"
    assert listener.running is False


@pytest.mark.parametrize("step", [
    "CGEventTapCreate", "CFMachPortCreateRunLoopSource",
    "CFRunLoopAddSource", "CGEventTapEnable",
])
def test_a_setup_error_reports_failed_promptly_and_says_why(step, capsys):
    q = FakeQuartz()

    def boom(*_args):
        raise RuntimeError("boom")

    setattr(q, step, boom)
    listener = QuartzKeyListener(lambda *a: None, mode="active", quartz=q)
    listener.start()
    began = time.monotonic()
    assert listener.wait_started(1) == "failed"
    assert time.monotonic() - began < 0.5
    assert listener.running is False
    assert "Voisr keytap: tap setup failed: boom" in capsys.readouterr().out


def test_stop_ends_in_stopped():
    q = FakeQuartz()
    listener = _start("listen", q)
    listener.wait_started(1)
    listener.stop()
    listener._thread.join(1)
    assert listener.state == "stopped"


def test_active_callback_passes_every_event_through_even_when_the_handler_raises():
    calls = []

    def boom(*args):
        calls.append(args)
        raise RuntimeError("handler bug")

    q = FakeQuartz()
    listener = _start("active", q, handler=boom)
    listener.wait_started(1)
    event = _event(61)
    assert q.callback(None, q.kCGEventKeyDown, event, None) is event
    assert calls == [(61, "down", 0)]
    listener.stop()


@pytest.mark.parametrize("disabled", [
    FakeQuartz.kCGEventTapDisabledByTimeout,
    FakeQuartz.kCGEventTapDisabledByUserInput,
])
def test_active_tap_reenables_itself_when_macos_disables_it(disabled):
    seen = []
    q = FakeQuartz()
    listener = _start("active", q, handler=lambda *a: seen.append(a))
    listener.wait_started(1)
    q.enabled.clear()
    event = _event(14)
    assert q.callback(None, disabled, event, None) is event
    assert q.enabled == [("TAP", True)]
    assert seen == []
    listener.stop()


def test_callback_reports_down_up_and_flags():
    seen = []
    q = FakeQuartz()
    listener = _start("active", q, handler=lambda vk, kind, flags: seen.append((vk, kind, flags)))
    listener.wait_started(1)
    q.callback(None, q.kCGEventKeyDown, _event(14), None)
    q.callback(None, q.kCGEventKeyUp, _event(14), None)
    q.callback(None, q.kCGEventFlagsChanged, _event(61, flags=0x40), None)
    assert seen == [(14, "down", 0), (14, "up", 0), (61, "flags", 0x40)]
    listener.stop()
