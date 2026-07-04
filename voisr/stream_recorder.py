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

    def __init__(self):
        self._lock = threading.Lock()
        self._frames = []
        self._recording = False
        self._out = None
        self._stream = None

    def _on_audio(self, indata, frames, time_info, status):
        if self._recording:
            with self._lock:
                self._frames.append(bytes(indata))

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
        with wave.open(self._out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.SAMPLERATE)
            w.writeframes(data)
        return ""

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def close(self) -> None:
        self._recording = False
        self._close_stream()
