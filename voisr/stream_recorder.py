import threading
import wave


class StreamRecorder:
    """Zero-latency push-to-talk recorder.

    Keeps a persistent 16 kHz mono input stream open (like Wispr's audio
    engine) so start() captures from the first millisecond of a key hold —
    unlike spawning ffmpeg, which loses ~0.6-0.9 s warming up avfoundation.
    Same start(path)/stop() interface as voisr.recorder.Recorder.
    """

    SAMPLERATE = 16000

    def __init__(self):
        self._lock = threading.Lock()
        self._frames = []
        self._recording = False
        self._out = None
        self._stream = None

    def _ensure_stream(self):
        if self._stream is not None:
            return
        import sounddevice as sd
        self._stream = sd.RawInputStream(
            samplerate=self.SAMPLERATE, channels=1, dtype="int16",
            callback=self._on_audio)
        self._stream.start()

    def _on_audio(self, indata, frames, time_info, status):
        if self._recording:
            with self._lock:
                self._frames.append(bytes(indata))

    def start(self, out_path) -> None:
        if self._recording:
            raise RuntimeError("already recording")
        self._ensure_stream()
        with self._lock:
            self._frames = []
        self._out = str(out_path)
        self._recording = True

    def stop(self) -> str:
        """Stop and write the WAV; returns '' (parity with Recorder.stop)."""
        if not self._recording:
            raise RuntimeError("not recording")
        self._recording = False
        with self._lock:
            data = b"".join(self._frames)
            self._frames = []
        with wave.open(self._out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.SAMPLERATE)
            w.writeframes(data)
        return ""

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
