import subprocess
from pathlib import Path

from voisr.models import MODELS_DIR, filename_for
from voisr.paths import find_binary

MODEL_PATH = MODELS_DIR / "ggml-base.en.bin"  # legacy default (kept for compat)

# Fallback order when the configured model is missing: the two models
# setup.sh installs, so an existing install always finds one. Everything
# else in the registry is opt-in and never chosen automatically.
FALLBACK_MODELS = ["small.en", "base.en"]


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
    """Best model on disk: the configured one (a registry id or the name of a
    custom ggml-<name>.bin), else small.en, else base.en."""
    candidates = ([name] if name else []) + FALLBACK_MODELS
    for n in candidates:
        p = Path(models_dir) / filename_for(n)
        if p.is_file():
            return p
    return Path(models_dir) / filename_for(FALLBACK_MODELS[-1])


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
           "--beam-size", "5",
           # English only. A multilingual model (large-v3-turbo) would
           # otherwise auto-detect and can transcribe in another language.
           "-l", "en"]
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
