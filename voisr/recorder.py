import signal
import subprocess

from voisr.paths import find_binary


class Recorder:
    """Records mic audio to 16 kHz mono WAV using ffmpeg/avfoundation."""

    MAX_SECONDS = 600  # stops a stuck-key recording (parity with StreamRecorder)

    def __init__(self, device: str = ":0"):
        self.device = device
        self.proc: subprocess.Popen | None = None

    def start(self, out_path) -> None:
        if self.proc:
            raise RuntimeError("already recording")
        ffmpeg = find_binary("ffmpeg") or "ffmpeg"
        self.proc = subprocess.Popen(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "avfoundation",
             "-i", self.device, "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", "-t", str(self.MAX_SECONDS), str(out_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    @staticmethod
    def _close_stderr(proc) -> None:
        if proc.stderr is not None:
            try:
                proc.stderr.close()
            except Exception:
                pass

    def stop(self) -> str:
        """Gracefully stop capture, finalize the WAV, and return stderr."""
        proc, self.proc = self.proc, None
        if proc is None:
            raise RuntimeError("not recording")
        try:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return proc.stderr.read().decode() if proc.stderr else ""
        finally:
            self._close_stderr(proc)

    def cancel(self) -> None:
        """Stop capture without requiring a usable WAV; safe to repeat."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except Exception:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass
        finally:
            self._close_stderr(proc)

    def close(self) -> None:
        self.cancel()
