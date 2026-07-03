import subprocess

import pytest

from wisprlocal.audio import to_wav16k


@pytest.fixture
def spoken_wav(tmp_path):
    def make(text: str):
        aiff = tmp_path / "speech.aiff"
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        return to_wav16k(aiff, tmp_path / "speech.wav")

    return make
