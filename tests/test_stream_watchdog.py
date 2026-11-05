"""StreamRecorder's zero watchdog, against a fake stream and a manual timer.
A device that sends only exact zeros for 6 s is reopened once, and never
after the recording has ended: that would turn the mic indicator on with
nobody dictating."""
import re
import threading
import time
import wave

import pytest
import sounddevice as sd

from voisr import stream_recorder
from voisr.stream_recorder import StreamRecorder

ZEROS = bytes(1280)          # 40 ms of exact zeros
VOICE = b"\x10\x02" * 640    # 40 ms of a non-zero sample
SLOW_MAC_S = 30.0            # above any stall seen on a busy Mac


class FakeStream:
    def __init__(self, callback, finished=lambda: None):
        self.callback = callback
        self.finished = finished
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def feed(self, chunk, times=1):
        for _ in range(times):
            self.callback(chunk, len(chunk) // 2, None, None)


class Streams:
    def __init__(self, fail_from=None):
        self.made = []
        self.fail_from = fail_from

    def __call__(self, samplerate, callback, finished):
        if self.fail_from is not None and len(self.made) >= self.fail_from:
            raise RuntimeError("no input device")
        stream = FakeStream(callback, finished)
        self.made.append(stream)
        return stream

    @property
    def open(self):
        return [s for s in self.made if s.started and not s.closed]


class ManualTimer:
    def __init__(self, interval, fn, args=()):
        self.interval, self.fn, self.args = interval, fn, args
        self.daemon = False
        self.started = self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # like a threading.Timer that was already running when cancel() came:
        # the guard against late firing is the recorder's job
        self.fn(*self.args)


class Timers:
    def __init__(self):
        self.made = []

    def __call__(self, interval, fn, args=()):
        timer = ManualTimer(interval, fn, args)
        self.made.append(timer)
        return timer


@pytest.fixture(autouse=True)
def quick_self_stop(monkeypatch):
    """Most fakes never stop themselves: don't wait out the limit for each."""
    monkeypatch.setattr(StreamRecorder, "SELF_STOP_WAIT_S", 0.01)


def make(fail_from=None):
    streams, timers = Streams(fail_from), Timers()
    return StreamRecorder(stream_factory=streams, timer_factory=timers), streams, timers


def frames(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


def test_the_watchdog_waits_six_seconds(tmp_path):
    rec, streams, timers = make()
    rec.start(tmp_path / "a.wav")
    timer = timers.made[0]
    assert timer.interval == 6.0 and timer.started and timer.daemon
    rec.cancel()


def test_only_zeros_reopens_the_stream_once(tmp_path):
    rec, streams, timers = make()
    rec.start(tmp_path / "a.wav")
    streams.made[0].feed(ZEROS, 150)
    timers.made[0].fire()
    assert len(streams.made) == 2 and rec.reopened
    assert streams.made[0].closed and streams.made[1].started
    timers.made[0].fire()
    assert len(streams.made) == 2
    rec.cancel()
    assert streams.open == []


def test_any_sound_cancels_the_reopen(tmp_path):
    rec, streams, timers = make()
    rec.start(tmp_path / "a.wav")
    streams.made[0].feed(ZEROS, 10)
    streams.made[0].feed(VOICE)
    timers.made[0].fire()
    assert len(streams.made) == 1 and not rec.reopened
    rec.cancel()


def test_no_reopen_after_stop(tmp_path):
    rec, streams, timers = make()
    rec.start(tmp_path / "a.wav")
    streams.made[0].feed(ZEROS, 10)
    rec.stop()
    assert timers.made[0].cancelled
    timers.made[0].fire()
    assert len(streams.made) == 1 and streams.open == []


def test_no_reopen_after_cancel(tmp_path):
    rec, streams, timers = make()
    rec.start(tmp_path / "a.wav")
    rec.cancel()
    timers.made[0].fire()
    assert len(streams.made) == 1 and streams.open == []


def test_an_old_recordings_timer_is_ignored(tmp_path):
    rec, streams, timers = make()
    rec.start(tmp_path / "a.wav")
    rec.stop()
    rec.start(tmp_path / "b.wav")
    timers.made[0].fire()  # the first recording's watchdog, firing late
    assert len(streams.made) == 2 and not rec.reopened
    rec.cancel()


def test_a_failed_reopen_still_writes_the_wav(tmp_path):
    rec, streams, timers = make(fail_from=1)
    rec.start(tmp_path / "a.wav")
    streams.made[0].feed(ZEROS, 5)
    timers.made[0].fire()  # must not raise
    rec.stop()
    assert frames(tmp_path / "a.wav") == 5 * 640
    assert streams.open == []


def test_audio_after_the_reopen_is_kept(tmp_path):
    rec, streams, timers = make()
    rec.start(tmp_path / "a.wav")
    streams.made[0].feed(ZEROS, 3)
    timers.made[0].fire()
    streams.made[1].feed(VOICE, 2)
    rec.stop()
    assert frames(tmp_path / "a.wav") == 5 * 640


# ----- a stop that deadlocks inside macOS (see the top of stream_recorder.py)


class HangingStream(FakeStream):
    """stop() blocks until the test ends, like the real CoreAudio deadlock."""

    def __init__(self, callback, finished=lambda: None):
        super().__init__(callback, finished)
        self.release = threading.Event()

    def stop(self):
        self.release.wait(10)


@pytest.fixture
def wedge(monkeypatch):
    """A clean process-wide flag, with notices recorded instead of sent."""
    notices = []
    monkeypatch.setattr(stream_recorder, "_mic_wedged", threading.Event())
    monkeypatch.setattr(stream_recorder, "_abandoned_streams", [])
    monkeypatch.setattr(stream_recorder, "_notify_wedged",
                        lambda: notices.append(1))
    unregistered = []
    monkeypatch.setattr(stream_recorder.atexit, "unregister", unregistered.append)
    monkeypatch.setattr(StreamRecorder, "STOP_TIMEOUT_S", 0.05)
    return notices, unregistered


def hanging_recorder():
    made = []

    def factory(_rate, callback, finished):
        made.append(HangingStream(callback, finished))
        return made[-1]

    return StreamRecorder(stream_factory=factory, timer_factory=Timers()), made


def test_a_deadlocked_stop_still_saves_the_dictation(tmp_path, wedge):
    rec, made = hanging_recorder()
    wav = tmp_path / "a.wav"
    rec.start(wav)
    made[0].feed(b"\x01\x00" * 100)
    try:
        started = time.monotonic()
        rec.stop()
        assert time.monotonic() - started < 2
        assert frames(wav) == 100
    finally:
        made[0].release.set()


def test_a_deadlocked_stop_warns_once_and_clears_the_exit_handler(tmp_path, wedge):
    notices, unregistered = wedge
    rec, made = hanging_recorder()
    rec.start(tmp_path / "a.wav")
    rec.stop()
    # a second stuck stream, already flagged: nothing new to say
    other = HangingStream(rec._on_audio)
    rec._stream = other
    rec._close_stream()
    for stream in (*made, other):
        stream.release.set()
    assert notices == [1]
    assert len(unregistered) == 1


def test_after_a_deadlock_new_streams_are_refused_so_the_fallback_runs(tmp_path, wedge):
    rec, made = hanging_recorder()
    rec.start(tmp_path / "a.wav")
    rec.stop()
    made[0].release.set()
    next_rec, more = hanging_recorder()
    with pytest.raises(RuntimeError):
        next_rec.start(tmp_path / "b.wav")
    assert more == []  # no stream was opened


def test_cancel_returns_from_a_deadlocked_stop(tmp_path, wedge):
    rec, made = hanging_recorder()
    rec.start(tmp_path / "a.wav")
    started = time.monotonic()
    rec.cancel()
    made[0].release.set()
    assert time.monotonic() - started < 2
    assert stream_recorder._mic_wedged.is_set()


def test_no_reopen_after_the_stop_deadlocks(tmp_path, wedge):
    rec, made = hanging_recorder()
    timers = rec._timer_factory
    rec.start(tmp_path / "a.wav")
    timers.made[-1].fire()
    made[0].release.set()
    assert len(made) == 1


def test_a_healthy_stop_is_not_abandoned(tmp_path, monkeypatch, wedge):
    monkeypatch.setattr(StreamRecorder, "STOP_TIMEOUT_S", SLOW_MAC_S)
    rec, streams, _ = make()
    rec.start(tmp_path / "a.wav")
    rec.stop()
    assert streams.made[0].stopped and streams.made[0].closed
    assert not stream_recorder._mic_wedged.is_set()


def test_a_stop_error_still_reaches_the_caller(tmp_path, monkeypatch, wedge):
    class Broken(FakeStream):
        def stop(self):
            raise OSError("device gone")

    monkeypatch.setattr(StreamRecorder, "STOP_TIMEOUT_S", SLOW_MAC_S)
    rec = StreamRecorder(stream_factory=lambda _r, cb, done: Broken(cb, done),
                         timer_factory=Timers())
    rec.start(tmp_path / "a.wav")
    with pytest.raises(OSError):
        rec.stop()
    assert not stream_recorder._mic_wedged.is_set()


# ----- the IO thread stops its own stream, so no second thread takes the locks


class IOThreadStream(FakeStream):
    """Delivers audio on its own thread and, like PortAudio, stops when the
    callback says so. Records whether stop() came before that finished."""

    def __init__(self, callback, finished):
        super().__init__(callback, finished)
        self.delivered = threading.Event()
        self.ended = threading.Event()
        self.stop_before_end = False
        self.outcome = None

    def start(self):
        super().start()
        threading.Thread(target=self._io, daemon=True).start()

    def _io(self):
        while True:
            try:
                self.callback(VOICE, 640, None, None)
            except (sd.CallbackAbort, sd.CallbackStop) as stop:
                self.outcome = stop
                break
            self.delivered.set()
            time.sleep(0.002)
        self.ended.set()
        self.finished()

    def stop(self):
        self.stop_before_end = not self.ended.is_set()
        super().stop()


@pytest.fixture
def io_recorder(wedge, monkeypatch):
    """Limits far above any stall on a busy Mac. They cost nothing: a stop ends
    as soon as the fake's IO thread does, whatever the limit."""
    monkeypatch.setattr(StreamRecorder, "SELF_STOP_WAIT_S", SLOW_MAC_S)
    monkeypatch.setattr(StreamRecorder, "STOP_TIMEOUT_S", SLOW_MAC_S)
    made = []

    def factory(_rate, callback, finished):
        made.append(IOThreadStream(callback, finished))
        return made[-1]

    return StreamRecorder(stream_factory=factory, timer_factory=Timers()), made


def stop_rows(capsys):
    return [r for r in capsys.readouterr().out.splitlines() if "mic stop" in r]


def test_the_io_thread_stops_itself_before_anyone_touches_the_stream(tmp_path, io_recorder):
    rec, made = io_recorder
    wav = tmp_path / "a.wav"
    rec.start(wav)
    assert made[0].delivered.wait(SLOW_MAC_S)
    rec.stop()
    assert isinstance(made[0].outcome, sd.CallbackAbort)
    assert not made[0].stop_before_end
    assert made[0].stopped and made[0].closed
    assert frames(wav) > 0
    assert not stream_recorder._mic_wedged.is_set()


def test_cancel_also_lets_the_io_thread_stop_itself(tmp_path, io_recorder):
    rec, made = io_recorder
    rec.start(tmp_path / "a.wav")
    assert made[0].delivered.wait(SLOW_MAC_S)
    rec.cancel()
    assert isinstance(made[0].outcome, sd.CallbackAbort)
    assert not made[0].stop_before_end and made[0].closed


def test_a_stop_before_any_audio_still_lets_the_io_thread_stop_itself(tmp_path, io_recorder):
    rec, made = io_recorder
    rec.start(tmp_path / "a.wav")
    rec.stop()  # however early: the first block is what stops it
    assert isinstance(made[0].outcome, sd.CallbackAbort)
    assert not made[0].stop_before_end and made[0].closed


def test_a_self_stop_does_not_wait_out_the_limit(tmp_path, io_recorder):
    rec, made = io_recorder
    rec.start(tmp_path / "a.wav")
    started = time.monotonic()
    rec.stop()
    assert time.monotonic() - started < SLOW_MAC_S / 2


def test_a_stream_sending_nothing_is_stopped_from_outside(tmp_path, monkeypatch, wedge, capsys):
    monkeypatch.setattr(StreamRecorder, "SELF_STOP_WAIT_S", 0.05)
    monkeypatch.setattr(StreamRecorder, "STOP_TIMEOUT_S", SLOW_MAC_S)
    rec, streams, _ = make()
    rec.start(tmp_path / "a.wav")
    started = time.monotonic()
    rec.stop()
    assert time.monotonic() - started >= 0.05
    assert streams.made[0].stopped and streams.made[0].closed
    assert "from outside" in stop_rows(capsys)[0]


def test_a_reopened_stream_keeps_delivering(tmp_path, io_recorder):
    rec, made = io_recorder
    rec.start(tmp_path / "a.wav")
    with rec._stream_lock:
        rec._close_stream()
        rec._stream = rec._open_stream()
    assert isinstance(made[0].outcome, sd.CallbackAbort)
    assert made[1].delivered.wait(SLOW_MAC_S)  # the new stream wasn't told to stop
    rec.stop()
    assert isinstance(made[1].outcome, sd.CallbackAbort)


def test_a_stream_stopped_at_the_cap_is_still_closed_without_waiting(tmp_path, io_recorder, capsys):
    rec, made = io_recorder
    rec._max_bytes = 3 * len(VOICE)
    wav = tmp_path / "a.wav"
    rec.start(wav)
    assert made[0].ended.wait(SLOW_MAC_S)  # it stopped itself, before any release
    assert isinstance(made[0].outcome, sd.CallbackStop)
    started = time.monotonic()
    rec.stop()
    assert time.monotonic() - started < SLOW_MAC_S / 2
    assert not made[0].stop_before_end
    assert made[0].stopped and made[0].closed
    assert frames(wav) == 3 * 640
    assert not stream_recorder._mic_wedged.is_set()
    assert "from outside" not in stop_rows(capsys)[0]


def test_each_stop_logs_its_time_and_nothing_else(tmp_path, io_recorder, capsys):
    rec, made = io_recorder
    rec.start(tmp_path / "a.wav")
    assert made[0].delivered.wait(SLOW_MAC_S)
    rec.stop()
    rows = stop_rows(capsys)
    assert len(rows) == 1
    assert re.fullmatch(r"Voisr audio: mic stop \d+ ms", rows[0])


def test_a_failed_stop_says_so_in_its_row(tmp_path, monkeypatch, wedge, capsys):
    class Broken(FakeStream):
        def stop(self):
            raise OSError("device gone")

    monkeypatch.setattr(StreamRecorder, "STOP_TIMEOUT_S", SLOW_MAC_S)
    rec = StreamRecorder(stream_factory=lambda _r, cb, done: Broken(cb, done),
                         timer_factory=Timers())
    rec.start(tmp_path / "a.wav")
    with pytest.raises(OSError):
        rec.stop()
    rows = stop_rows(capsys)
    assert len(rows) == 1
    assert re.fullmatch(
        r"Voisr audio: mic stop \d+ ms, from outside, failed: OSError", rows[0])


def test_a_failed_stop_shows_even_when_cancel_swallows_it(tmp_path, monkeypatch, wedge, capsys):
    class Broken(FakeStream):
        def stop(self):
            raise OSError("device gone")

    monkeypatch.setattr(StreamRecorder, "STOP_TIMEOUT_S", SLOW_MAC_S)
    rec = StreamRecorder(stream_factory=lambda _r, cb, done: Broken(cb, done),
                         timer_factory=Timers())
    rec.start(tmp_path / "a.wav")
    rec.cancel()  # must not raise
    assert "failed: OSError" in stop_rows(capsys)[0]


def test_a_stuck_stop_that_later_finishes_says_so(tmp_path, wedge, capsys):
    rec, made = hanging_recorder()
    rec.start(tmp_path / "a.wav")
    rec.stop()
    assert "mic stop" not in capsys.readouterr().out  # still stuck
    made[0].release.set()
    deadline = time.monotonic() + SLOW_MAC_S
    out = ""
    while "mic stop" not in out and time.monotonic() < deadline:
        out += capsys.readouterr().out
        time.sleep(0.01)
    assert "mic stop" in out
