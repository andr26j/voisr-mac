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


@pytest.fixture(autouse=True)
def _no_real_paste(monkeypatch):
    """An Api built in a test must never paste into the user's focused
    window or overwrite their clipboard. The release path pastes from a
    background thread after the test has already passed, so a missing
    paste_fn typed fake transcripts into whatever app was in front. An Api
    built without paste_fn / clipboard_fn gets inert ones; a test that
    needs the real thing passes it explicitly."""
    from voisr.ui import Api

    real_init = Api.__init__

    def init(self, *args, **kwargs):
        kwargs.setdefault("paste_fn", lambda text: True)
        kwargs.setdefault("clipboard_fn", lambda text: None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(Api, "__init__", init)


@pytest.fixture
def spoken_wav(tmp_path):
    def make(text: str):
        aiff = tmp_path / "speech.aiff"
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        return to_wav16k(aiff, tmp_path / "speech.wav")

    return make
