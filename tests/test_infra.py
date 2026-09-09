"""M4: the repo must be reproducible and runnable without a microphone."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_requirement_is_pinned():
    lines = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    assert lines, "requirements.txt is empty"
    unpinned = [l for l in lines if not re.match(r"^[A-Za-z0-9_.\-\[\]]+==\S+$", l)]
    assert unpinned == [], f"unpinned: {unpinned}"


def test_audio_marker_excludes_every_microphone_and_speech_test():
    """`pytest -m "not audio"` is what CI runs: it must deselect the tests
    that need `say`, whisper-cli, or a real input device."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "-m", "not audio"],
        capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    collected = proc.stdout
    for needle in ("test_stream_recorder.py", "spoken_wav",
                   "test_transcribes_synthesized_speech",
                   "test_full_pipeline_from_audio_to_clean_text",
                   "test_push_to_talk_end_to_end_real_transcription",
                   "test_transcribe_works_with_launchd_path"):
        assert needle not in collected, f"{needle} still collected under -m 'not audio'"
    assert "test_infra.py" in collected
