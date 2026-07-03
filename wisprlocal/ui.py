import json
import tempfile
import time
from pathlib import Path

from wisprlocal.app import load_config
from wisprlocal.cleanup import clean
from wisprlocal.history import HistoryStore
from wisprlocal.inject import set_clipboard
from wisprlocal.recorder import Recorder
from wisprlocal.transcriber import transcribe

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"


class Api:
    """JS<->Python bridge exposed to the dashboard via pywebview."""

    def __init__(self, config_path=ROOT / "config.json",
                 history=None, recorder=None, transcribe_fn=transcribe,
                 clock=time.monotonic):
        self.config_path = Path(config_path)
        self.history = history or HistoryStore(ROOT / "data" / "history.jsonl")
        self.recorder = recorder or Recorder()
        self.transcribe_fn = transcribe_fn
        self.clock = clock
        self._wav = None
        self._t0 = None

    def start_recording(self):
        if self._wav is not None:
            return {"error": "already recording"}
        self._wav = Path(tempfile.mkstemp(suffix=".wav")[1])
        self._t0 = self.clock()
        self.recorder.start(self._wav)
        return {"recording": True}

    def stop_recording(self):
        if self._wav is None:
            return {"error": "not recording"}
        err = self.recorder.stop()
        wav, self._wav = self._wav, None
        duration = max(self.clock() - self._t0, 0.1)
        try:
            if not wav.exists() or wav.stat().st_size < 1000:
                return {"error": f"recording failed (mic permission?) {err.strip()[:120]}"}
            cfg = self.get_config()
            text = clean(self.transcribe_fn(wav), dictionary=cfg.get("dictionary", {}))
            if not text:
                return {"error": "nothing transcribed — try speaking a bit longer"}
            item = self.history.add(text, duration_s=duration)
            set_clipboard(text)
            return {"text": text, "item": item, "copied": True}
        finally:
            wav.unlink(missing_ok=True)

    def get_history(self):
        return self.history.list()

    def delete_history_item(self, item_id):
        self.history.delete(item_id)
        return {"ok": True}

    def get_stats(self):
        return self.history.stats()

    def get_config(self):
        return load_config(self.config_path)

    def set_config(self, updates):
        cfg = self.get_config()
        cfg.update(updates)
        self.config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    def copy_text(self, text):
        set_clipboard(text)
        return {"ok": True}


def main():
    import webview
    api = Api()
    webview.create_window("Wispr Local", str(WEB_DIR / "index.html"),
                          js_api=api, width=1060, height=700, min_size=(860, 560))
    webview.start()


if __name__ == "__main__":
    main()
