import signal
import subprocess


class Recorder:
    """Records mic audio to 16 kHz mono WAV using ffmpeg/avfoundation."""

    def __init__(self, device: str = ":0"):
        self.device = device
        self.proc: subprocess.Popen | None = None

    def start(self, out_path) -> None:
        if self.proc:
            raise RuntimeError("already recording")
        self.proc = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "avfoundation",
             "-i", self.device, "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", str(out_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def stop(self) -> str:
        """Stop recording; returns ffmpeg's stderr (empty on clean capture)."""
        if not self.proc:
            raise RuntimeError("not recording")
        self.proc.send_signal(signal.SIGINT)  # graceful: finalizes the WAV header
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        err = self.proc.stderr.read().decode() if self.proc.stderr else ""
        self.proc = None
        return err
