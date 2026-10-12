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


# ---- L9 / L10 (2026-10-12 audit) ----

def test_readme_states_the_real_download_size_and_test_commands():
    readme = (ROOT / "README.md").read_text()
    assert "~141 MB" not in readme, "setup downloads both models, ~630 MB"
    assert "630 MB" in readme
    # a bare directory fails on Node 24; the glob works everywhere
    assert "node --test tests/js/*.test.mjs" in readme


def test_readme_says_the_device_key_is_cli_only():
    line = next(l for l in (ROOT / "README.md").read_text().splitlines()
                if '"device"' in l)
    assert "CLI" in line


def test_finder_metadata_is_ignored():
    assert ".DS_Store" in (ROOT / ".gitignore").read_text().split()


def test_an_api_built_in_a_test_cannot_paste_into_the_users_window(tmp_path):
    # an Api left on the real defaults pasted its fake transcript ("Hello",
    # "X") into whatever window had focus each time the suite ran
    from voisr.history import HistoryStore
    from voisr.inject import paste_at_cursor, set_clipboard
    from voisr.ui import Api

    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"))
    assert api._paste_fn is not paste_at_cursor
    assert api._clipboard_fn is not set_clipboard
