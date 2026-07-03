import subprocess
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "ggml-base.en.bin"


class TranscriptionError(RuntimeError):
    pass


def transcribe(wav_path, model_path=MODEL_PATH, whisper_bin="whisper-cli") -> str:
    """Transcribe a 16 kHz mono WAV file with whisper.cpp, return plain text."""
    out_prefix = str(wav_path) + ".out"
    proc = subprocess.run(
        [whisper_bin, "-m", str(model_path), "-f", str(wav_path),
         "--output-txt", "--output-file", out_prefix, "--no-prints"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscriptionError(proc.stderr.strip() or "whisper-cli failed")
    txt = Path(out_prefix + ".txt")
    text = txt.read_text().strip()
    txt.unlink(missing_ok=True)
    return text
