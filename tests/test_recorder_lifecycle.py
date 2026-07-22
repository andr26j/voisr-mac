from voisr.recorder import Recorder
from voisr.stream_recorder import StreamRecorder


class FakePipe:
    def __init__(self):
        self.closed = False

    def read(self):
        return b""

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stderr = FakePipe()
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return 0 if self.waited else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


class ExplodingStream:
    def __init__(self):
        self.stop_calls = 0
        self.close_calls = 0

    def stop(self):
        self.stop_calls += 1
        raise RuntimeError("stream already stopped")

    def close(self):
        self.close_calls += 1


def test_ffmpeg_cancel_terminates_waits_and_is_idempotent():
    proc = FakeProcess()
    recorder = Recorder()
    recorder.proc = proc

    recorder.cancel()
    recorder.cancel()
    recorder.close()

    assert proc.terminated is True
    assert proc.waited is True
    assert proc.stderr.closed is True
    assert recorder.proc is None


def test_stream_cancel_closes_and_discards_capture_even_if_stop_raises():
    stream = ExplodingStream()
    recorder = StreamRecorder()
    recorder._stream = stream
    recorder._recording = True
    recorder._frames = [b"audio"]
    recorder._bytes = 5
    recorder._out = "/tmp/unused.wav"

    recorder.cancel()
    recorder.cancel()
    recorder.close()

    assert stream.stop_calls == 1
    assert stream.close_calls == 1
    assert recorder._recording is False
    assert recorder._frames == []
    assert recorder._bytes == 0
    assert recorder._out is None
