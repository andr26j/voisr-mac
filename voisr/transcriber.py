import subprocess
from pathlib import Path

from voisr.paths import find_binary

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "ggml-base.en.bin"


class TranscriptionError(RuntimeError):
    pass


def transcribe(wav_path, model_path=MODEL_PATH, whisper_bin=None) -> str:
    """Transcribe a 16 kHz mono WAV file with whisper.cpp, return plain text."""
    whisper = whisper_bin or find_binary("whisper-cli")
    if not whisper:
        raise TranscriptionError(
            "whisper-cli not found — run: brew install whisper-cpp")
    if not Path(model_path).exists():
        raise TranscriptionError(f"model missing: {model_path} — run setup.sh")
    out_prefix = str(wav_path) + ".out"
    proc = subprocess.run(
        [whisper, "-m", str(model_path), "-f", str(wav_path),
         "--output-txt", "--output-file", out_prefix, "--no-prints"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscriptionError(proc.stderr.strip() or "whisper-cli failed")
    txt = Path(out_prefix + ".txt")
    text = txt.read_text().strip()
    txt.unlink(missing_ok=True)
    return text
