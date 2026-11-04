import signal
import subprocess

from voisr.paths import find_binary

# The macOS default input, the same one StreamRecorder records from. ffmpeg's
# device numbers aren't stable: a virtual device or a nearby iPhone can take :0.
DEFAULT_DEVICE = ":default"
# Every config used to be saved with this, so it doesn't mean anyone chose it.
_OLD_DEFAULT_DEVICE = ":0"


class Recorder:
    """Records mic audio to 16 kHz mono WAV using ffmpeg/avfoundation."""

    MAX_SECONDS = 600  # stops a stuck-key recording (parity with StreamRecorder)

    def __init__(self, device: str = DEFAULT_DEVICE):
        self.device = DEFAULT_DEVICE if device == _OLD_DEFAULT_DEVICE else device
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
        proc = self.proc
        if proc is None:
            raise RuntimeError("not recording")
        reaped = False
        try:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            reaped = True
            output = proc.stderr.read().decode() if proc.stderr else ""
        finally:
            if reaped:
                self.proc = None
                self._close_stderr(proc)
        return output

    def cancel(self) -> None:
        """Stop capture without requiring a usable WAV; safe to repeat."""
        proc = self.proc
        if proc is None:
            return
        reaped = False
        try:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            reaped = True
        except Exception:
            try:
                proc.kill()
                proc.wait()
                reaped = True
            except Exception:
                return
        finally:
            if reaped:
                self.proc = None
                self._close_stderr(proc)

    def close(self) -> None:
        self.cancel()
