import json
import tempfile
import time
from pathlib import Path

from voisr.app import load_config
from voisr.cleanup import clean
from voisr.history import HistoryStore
from voisr.inject import paste_at_cursor, set_clipboard
from voisr.recorder import Recorder
from voisr.transcriber import transcribe

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"

HOTKEY_OPTIONS = [
    {"value": "alt_r", "label": "Right Option (⌥)"},
    {"value": "alt_l", "label": "Left Option (⌥)"},
    {"value": "cmd_r", "label": "Right Command (⌘)"},
    {"value": "ctrl_r", "label": "Right Control (⌃)"},
    {"value": "shift_r", "label": "Right Shift (⇧)"},
    {"value": "f13", "label": "F13"},
    {"value": "f14", "label": "F14"},
    {"value": "f15", "label": "F15"},
    {"value": "f16", "label": "F16"},
    {"value": "f17", "label": "F17"},
    {"value": "f18", "label": "F18"},
    {"value": "f19", "label": "F19"},
]


def resolve_key(key_name: str):
    """Map a config key name to a pynput key object."""
    from pynput import keyboard
    named = getattr(keyboard.Key, key_name, None)
    return named if named is not None else keyboard.KeyCode.from_char(key_name)


class HotkeyService:
    """Global push-to-talk listener driving the shared Api record path."""

    def __init__(self, api):
        self._api = api
        self._listener = None
        self.status = "not started"

    def start(self, key_name: str) -> None:
        try:
            from pynput import keyboard
            target = resolve_key(key_name)

            def on_press(key):
                if key == target:
                    self._api.start_recording()

            def on_release(key):
                if key == target:
                    self._api.stop_recording(paste=True)

            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.daemon = True
            self._listener.start()
            self.status = f"active on {key_name}"
        except Exception as e:  # missing Input Monitoring permission, etc.
            self.status = f"unavailable: {e}"

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def restart(self, key_name: str) -> None:
        self.stop()
        self.start(key_name)


class Api:
    """JS<->Python bridge exposed to the dashboard via pywebview."""

    def __init__(self, config_path=ROOT / "config.json",
                 history=None, recorder=None, transcribe_fn=transcribe,
                 clock=time.monotonic, hotkey_service=None):
        self._config_path = Path(config_path)
        self._history = history or HistoryStore(ROOT / "data" / "history.jsonl")
        self._recorder = recorder or Recorder()
        self._transcribe_fn = transcribe_fn
        self._clock = clock
        self._hotkey_service = hotkey_service
        self._wav = None
        self._t0 = None

    def attach_hotkey_service(self, service):
        self._hotkey_service = service

    def start_recording(self):
        if self._wav is not None:
            return {"error": "already recording"}
        self._wav = Path(tempfile.mkstemp(suffix=".wav")[1])
        self._t0 = self._clock()
        self._recorder.start(self._wav)
        return {"recording": True}

    def stop_recording(self, paste=False):
        if self._wav is None:
            return {"error": "not recording"}
        err = self._recorder.stop()
        wav, self._wav = self._wav, None
        duration = max(self._clock() - self._t0, 0.1)
        try:
            if not wav.exists() or wav.stat().st_size < 1000:
                return {"error": f"recording failed (mic permission?) {err.strip()[:120]}"}
            cfg = self.get_config()
            text = clean(self._transcribe_fn(wav), dictionary=cfg.get("dictionary", {}))
            if not text:
                return {"error": "nothing transcribed — try speaking a bit longer"}
            item = self._history.add(text, duration_s=duration)
            pasted = False
            if paste and cfg.get("paste"):
                pasted = paste_at_cursor(text)
            if not pasted:
                set_clipboard(text)
            return {"text": text, "item": item, "copied": True, "pasted": pasted}
        finally:
            wav.unlink(missing_ok=True)

    def get_history(self):
        return self._history.list()

    def delete_history_item(self, item_id):
        self._history.delete(item_id)
        return {"ok": True}

    def get_stats(self):
        return self._history.stats()

    def get_config(self):
        return load_config(self._config_path)

    def set_config(self, updates):
        old_hotkey = self.get_config().get("hotkey")
        cfg = self.get_config()
        cfg.update(updates)
        self._config_path.write_text(json.dumps(cfg, indent=2))
        new_hotkey = cfg.get("hotkey")
        if self._hotkey_service and new_hotkey != old_hotkey:
            self._hotkey_service.restart(new_hotkey)
        return cfg

    def get_hotkey_options(self):
        return HOTKEY_OPTIONS

    def get_hotkey_status(self):
        if self._hotkey_service is None:
            return {"status": "push-to-talk not running"}
        return {"status": self._hotkey_service.status}

    def copy_text(self, text):
        set_clipboard(text)
        return {"ok": True}


def main():
    import webview
    api = Api()
    service = HotkeyService(api)
    api.attach_hotkey_service(service)
    service.start(api.get_config().get("hotkey", "alt_r"))
    window = webview.create_window("Voisr", str(WEB_DIR / "index.html"),
                                   js_api=api, width=1060, height=700, min_size=(860, 560))
    try:
        webview.start()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
