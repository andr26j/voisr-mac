"""The speech gate inside Api._run_session: silence is rejected for the
right reason and never reaches whisper, speech is trimmed first, and a clip
the gate can't read is transcribed as before."""
import array
import math
import random
import wave

from voisr.history import HistoryStore
from voisr.ui import Api, wav_seconds

R = 16000


def noise(seconds, amp, seed=1):
    rng = random.Random(seed)
    return [rng.randint(-amp, amp) for _ in range(int(R * seconds))]


def tone(seconds, amp, hz=220):
    return [int(amp * math.sin(2 * math.pi * hz * i / R))
            for i in range(int(R * seconds))]


def write_wav(path, samples, width=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(R)
        if width == 2:
            w.writeframes(array.array("h", samples).tobytes())
        else:
            w.writeframes(bytes((s // 256 + 128) & 0xFF for s in samples))


def recorder_writing(samples, width=2):
    class Recorder:
        def start(self, path):
            self.path = path

        def stop(self):
            write_wav(self.path, samples, width)
            return ""

        def cancel(self):
            pass

    return Recorder


def make_api(tmp_path, samples, width=2):
    heard = []

    def transcribe(wav):
        heard.append(wav_seconds(wav))
        return "hello there"

    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              recorder_factory=recorder_writing(samples, width),
              transcribe_fn=transcribe, clock=lambda: 1.0)
    return api, heard


def dictate(api):
    api.start_recording()
    return api.stop_recording(paste=False)


def test_speech_is_trimmed_before_whisper(tmp_path):
    api, heard = make_api(tmp_path, noise(1.0, 10) + tone(0.5, 3000)
                          + noise(1.0, 10, seed=2))
    res = dictate(api)
    assert res["text"] == "Hello there"
    assert 0.5 < heard[0] < 0.8  # 0.5 s of speech plus 80 ms either side, not 2.5 s


def test_room_noise_alone_is_rejected_without_transcribing(tmp_path):
    api, heard = make_api(tmp_path, noise(1.0, 10))
    res = dictate(api)
    assert "didn't catch that" in res["error"]
    assert heard == [] and api.get_history() == []


def test_digital_silence_names_the_microphone(tmp_path):
    api, heard = make_api(tmp_path, [0] * R)
    res = dictate(api)
    assert "Microphone" in res["error"] and "silence" in res["error"]
    assert heard == []


def test_a_wav_the_gate_cannot_read_is_still_transcribed(tmp_path):
    api, heard = make_api(tmp_path, tone(1.0, 3000), width=1)
    res = dictate(api)
    assert res["text"] == "Hello there"
    assert len(heard) == 1


def test_a_failed_trim_still_transcribes(tmp_path, monkeypatch, capsys):
    def broken(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("voisr.speech_gate.trim_wav", broken)
    api, heard = make_api(tmp_path, noise(1.0, 10) + tone(0.5, 3000)
                          + noise(1.0, 10, seed=2))
    res = dictate(api)
    assert res["text"] == "Hello there"
    assert heard[0] > 2.0  # untrimmed
    assert "Voisr audio: trim failed: disk full" in capsys.readouterr().out
