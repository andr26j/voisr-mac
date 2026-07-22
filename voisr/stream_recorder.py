import threading
import wave


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

    def _on_audio(self, indata, frames, time_info, status):
        if not self._recording:
            return
        with self._lock:
            if self._bytes < self._max_bytes:
                self._frames.append(bytes(indata))
                self._bytes += len(indata)
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
        with self._lock:
            self._frames = []
            self._bytes = 0
        self._out = None

    def close(self) -> None:
        self.cancel()
