"""StreamRecorder's zero watchdog, against a fake stream and a manual timer.
A device that sends only exact zeros for 6 s is reopened once, and never
after the recording has ended: that would turn the mic indicator on with
nobody dictating."""
import wave

from voisr.stream_recorder import StreamRecorder

ZEROS = bytes(1280)          # 40 ms of exact zeros
VOICE = b"\x10\x02" * 640    # 40 ms of a non-zero sample


class FakeStream:
    def __init__(self, callback):
        self.callback = callback
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

    def __call__(self, samplerate, callback):
        if self.fail_from is not None and len(self.made) >= self.fail_from:
            raise RuntimeError("no input device")
        stream = FakeStream(callback)
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
