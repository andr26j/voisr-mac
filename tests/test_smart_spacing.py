import json

from voisr.history import HistoryStore
from voisr.transcriber import resolve_model
from voisr.ui import Api


class FakeRecorder:
    def start(self, path):
        self.path = path

    def stop(self):
        with open(self.path, "wb") as f:
            f.write(b"\0" * 2000)
        return ""


def make_api(tmp_path, texts, paste_log):
    it = iter(texts)
    (tmp_path / "config.json").write_text(json.dumps({"paste": True}))

    def fake_paste(text, restore_clipboard=True):
        paste_log.append(text)
        return True

    return Api(config_path=tmp_path / "config.json",
               history=HistoryStore(tmp_path / "h.jsonl"),
               recorder=FakeRecorder(),
               transcribe_fn=lambda wav: next(it),
               clock=lambda: 1.0,
               paste_fn=fake_paste)


def dictate(api):
    api.start_recording()
    return api.stop_recording(paste=True)


def test_consecutive_dictations_get_joining_space(tmp_path):
    log = []
    api = make_api(tmp_path, ["Hello world.", "it will not apply the space"], log)
    dictate(api)
    dictate(api)
    assert log == ["Hello world.", " It will not apply the space"]


def test_typing_in_between_suppresses_the_space(tmp_path):
    log = []
    api = make_api(tmp_path, ["Hello world.", "second thought"], log)
    dictate(api)
    api.note_user_typing()          # user typed something manually
    dictate(api)
    assert log == ["Hello world.", "Second thought"]


def test_first_dictation_never_gets_a_space(tmp_path):
    log = []
    api = make_api(tmp_path, ["Only one."], log)
    dictate(api)
    assert log == ["Only one."]


def test_history_and_clipboard_store_text_without_lead_space(tmp_path):
    log = []
    api = make_api(tmp_path, ["First.", "second"], log)
    dictate(api)
    out = dictate(api)
    assert out["text"] == "Second"                    # api result unpadded
    assert api.get_history()[0]["text"] == "Second"   # history unpadded


def test_resolve_model_prefers_configured_then_small_then_base(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "ggml-base.en.bin").write_bytes(b"x")
    assert resolve_model(models_dir=models).name == "ggml-base.en.bin"
    (models / "ggml-small.en.bin").write_bytes(b"x")
    assert resolve_model(models_dir=models).name == "ggml-small.en.bin"
    (models / "ggml-medium.en.bin").write_bytes(b"x")
    assert resolve_model("medium.en", models_dir=models).name == "ggml-medium.en.bin"
