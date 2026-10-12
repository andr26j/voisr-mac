import subprocess
from pathlib import Path

from voisr.paths import find_binary

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_PATH = MODELS_DIR / "ggml-base.en.bin"  # legacy default (kept for compat)

# best available first: small.en is a large accuracy jump over base.en and
# still near-instant on Apple Silicon with Metal
PREFERRED_MODELS = ["small.en", "base.en"]


class TranscriptionError(RuntimeError):
    pass


def _timeout_for(wav_path) -> float:
    """A hung whisper-cli held the queue (and Quit) forever. Allow two
    minutes plus twice the audio length: real runs on Apple Silicon take a
    fraction of the audio length, so this only ever catches a hang."""
    try:
        seconds = Path(wav_path).stat().st_size / 32000   # 16 kHz mono int16
    except OSError:
        seconds = 0
    return 120 + 2 * seconds


def resolve_model(name: str | None = None, models_dir: Path = MODELS_DIR) -> Path:
    """Best model on disk: the configured one, else small.en, else base.en."""
    candidates = ([name] if name else []) + PREFERRED_MODELS
    for n in candidates:
        p = Path(models_dir) / f"ggml-{n}.bin"
        if p.exists():
            return p
    return Path(models_dir) / f"ggml-{PREFERRED_MODELS[-1]}.bin"


def transcribe(wav_path, model_path=None, whisper_bin=None, prompt=None) -> str:
    """Transcribe a 16 kHz mono WAV file with whisper.cpp, return plain text.

    prompt: optional context string (e.g. the user's dictionary terms) that
    biases decoding toward the right spellings of names and jargon."""
    whisper = whisper_bin or find_binary("whisper-cli")
    if not whisper:
        raise TranscriptionError(
            "whisper-cli not found — run: brew install whisper-cpp")
    model = Path(model_path) if model_path else resolve_model()
    if not model.exists():
        raise TranscriptionError(f"model missing: {model} — run setup.sh")
    out_prefix = str(wav_path) + ".out"
    cmd = [whisper, "-m", str(model), "-f", str(wav_path),
           "--output-txt", "--output-file", out_prefix, "--no-prints",
           "--beam-size", "5"]
    if prompt:
        cmd += ["--prompt", prompt]
    timeout = _timeout_for(wav_path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TranscriptionError(f"whisper-cli timed out after {timeout:.0f} s")
    if proc.returncode != 0:
        raise TranscriptionError(proc.stderr.strip() or "whisper-cli failed")
    txt = Path(out_prefix + ".txt")
    text = txt.read_text().strip()
    txt.unlink(missing_ok=True)
    return text
