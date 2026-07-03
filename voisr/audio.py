import subprocess
from pathlib import Path

from voisr.paths import find_binary


def to_wav16k(src, dst) -> Path:
    """Convert any audio file to 16 kHz mono PCM WAV (whisper.cpp's input format)."""
    subprocess.run(
        [find_binary("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        check=True)
    return Path(dst)
