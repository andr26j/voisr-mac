import math
import operator
import threading
import wave

# Speaking voice fills the meter: a quiet room sits under the floor, a raised
# voice reaches the ceiling. Linear in decibels between the two.
LEVEL_FLOOR_DBFS = -55.0
LEVEL_CEIL_DBFS = -12.0


def level_from_int16(block) -> float:
    """Loudness of one mono int16 block as 0..1 (floor -> 0, ceiling -> 1).

    Plain Python, so it needs no numpy: about 0.02 ms for a 512-sample
    block, small next to the block's 32 ms of audio."""
    samples = memoryview(block).cast("B").cast("h")
    count = len(samples)
    if not count:
        return 0.0
    mean_square = sum(map(operator.mul, samples, samples)) / count
    if mean_square <= 0:
        return 0.0
    dbfs = 10 * math.log10(mean_square / (32768.0 * 32768.0))
    level = (dbfs - LEVEL_FLOOR_DBFS) / (LEVEL_CEIL_DBFS - LEVEL_FLOOR_DBFS)
    return min(1.0, max(0.0, level))


class StreamRecorder:
    """Push-to-talk recorder that opens the mic ONLY while recording.

    Opening a sounddevice stream costs ~80 ms — fast enough that capture
    starts effectively at the key-press (unlike ffmpeg's 0.6-0.9 s cold
    start) — while closing it on stop() means macOS's "microphone in use"
    indicator is off whenever you are not dictating, like Wispr.
    Same start(path)/stop() interface as voisr.recorder.Recorder.
    """

    SAMPLERATE = 16000
    MAX_SECONDS = 600  # bounds memory (~19 MB) and stops a stuck-key recording

    def __init__(self):
        self._lock = threading.Lock()
        self._frames = []
        self._recording = False
        self._out = None
        self._stream = None
        self._bytes = 0
        self._max_bytes = self.SAMPLERATE * 2 * self.MAX_SECONDS
        self._level = 0.0

    @property
    def level(self) -> float:
        """Loudness of the latest captured block, 0..1; 0.0 when not
        recording. A plain float swap, so any thread may read it."""
        return self._level

    def _on_audio(self, indata, frames, time_info, status):
        if not self._recording:
            return
        chunk = bytes(indata)
        with self._lock:
            keep = self._bytes < self._max_bytes
            if keep:
                self._frames.append(chunk)
                self._bytes += len(chunk)
        if keep:
            # Audio first, meter second: the meter is cosmetic, and an
            # exception escaping this callback would stop the stream.
            try:
                self._level = level_from_int16(chunk)
            except Exception:
                pass
            return
        # cap reached: stop capturing and release the mic (indicator off);
        # stop() still writes everything buffered so far
        import sounddevice as sd
        raise sd.CallbackStop

    def prime(self) -> None:
        """No-op: the mic is opened per-recording, never held open.

        (Kept for interface parity; the permission prompt now appears on
        the first real recording, and the mic indicator only shows then.)"""
        return

    def start(self, out_path) -> None:
        if self._recording:
            raise RuntimeError("already recording")
        import sounddevice as sd
        with self._lock:
            self._frames = []
            self._bytes = 0
        self._out = str(out_path)
        self._level = 0.0
        self._recording = True
        # Open the mic only now — the indicator turns on here and off in stop()
        self._stream = sd.RawInputStream(
            samplerate=self.SAMPLERATE, channels=1, dtype="int16",
            callback=self._on_audio)
        self._stream.start()

    def stop(self) -> str:
        """Stop, close the mic, and write the WAV; returns '' (parity)."""
        if not self._recording:
            raise RuntimeError("not recording")
        self._recording = False
        self._close_stream()
        # after the close: a callback already in flight can no longer write
        self._level = 0.0
        with self._lock:
            data = b"".join(self._frames)
            self._frames = []
            self._bytes = 0
        out, self._out = self._out, None
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLERATE)
            wav.writeframes(data)
        return ""

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()

    def cancel(self) -> None:
        """Close the mic and discard buffered audio; safe to repeat."""
        self._recording = False
        try:
            self._close_stream()
        except Exception:
            pass
        self._level = 0.0  # after the close, as in stop()
        with self._lock:
            self._frames = []
            self._bytes = 0
        self._out = None

    def close(self) -> None:
        self.cancel()
