import subprocess

import pytest

from voisr.audio import to_wav16k


AUDIO_MODULES = {"test_stream_recorder.py"}


def pytest_collection_modifyitems(items):
    """Tag every test that needs speech synthesis, whisper-cli, or the real
    microphone so CI (no mic, no `say`) can run `pytest -m "not audio"`."""
    for item in items:
        uses_speech = "spoken_wav" in getattr(item, "fixturenames", ())
        if uses_speech or item.path.name in AUDIO_MODULES:
            item.add_marker(pytest.mark.audio)


@pytest.fixture
def spoken_wav(tmp_path):
    def make(text: str):
        aiff = tmp_path / "speech.aiff"
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        return to_wav16k(aiff, tmp_path / "speech.wav")

    return make
