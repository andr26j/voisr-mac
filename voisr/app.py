import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from voisr.cleanup import clean
from voisr.inject import paste_at_cursor, set_clipboard
from voisr.pasteboard import PasteDeliveryError
from voisr.recorder import Recorder
from voisr.transcriber import transcribe

DEFAULT_CONFIG = {"hotkey": "alt_r", "paste": True, "device": ":0", "dictionary": {},
                  "show_widget": True, "pill_theme": "classic", "pill_color": None,
                  "appearance": "system", "launch_at_login": False, "model": None,
                  "event_tap": "active"}


def _is_str(v):
    return isinstance(v, str)


def _is_bool(v):
    return isinstance(v, bool)


def _is_optional_str(v):
    return v is None or isinstance(v, str)


def _is_vk(v):
    # bool is an int subclass; True is not a key code
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 0xFFFF


def _is_tap_mode(v):
    return v in ("listen", "active")


def _is_dictionary(v):
    return isinstance(v, dict) and all(
        isinstance(k, str) and k and isinstance(w, str) for k, w in v.items())


CONFIG_VALIDATORS = {
    "hotkey": _is_str, "paste": _is_bool, "device": _is_str,
    "dictionary": _is_dictionary, "show_widget": _is_bool,
    "pill_theme": _is_str, "pill_color": _is_optional_str,
    "appearance": _is_str, "launch_at_login": _is_bool,
    "model": _is_optional_str, "hotkey_vk": _is_vk, "hotkey_label": _is_str,
    "event_tap": _is_tap_mode,
}


def config_errors(updates: dict) -> list[str]:
    """Why `updates` can't be saved, one line per bad key; [] when it can."""
    if not isinstance(updates, dict):
        return ["settings must be an object"]
    errors = []
    for key, value in updates.items():
        check = CONFIG_VALIDATORS.get(key)
        if check is None:
            errors.append(f"unknown setting: {key}")
        elif not check(value):
            errors.append(f"invalid value for {key}")
    return errors


def _salvage(data: dict, path) -> dict:
    """Keep every valid value from a hand-edited file. A bad value falls back
    to its default instead of crashing whatever reads it later; a dictionary
    keeps its good entries, since those are the user's work."""
    out = {}
    for key, value in data.items():
        check = CONFIG_VALIDATORS.get(key)
        if check is None or check(value):
            out[key] = value  # unknown keys are the user's; leave them be
        elif key == "dictionary" and isinstance(value, dict):
            out[key] = {k: w for k, w in value.items()
                        if isinstance(k, str) and k and isinstance(w, str)}
            print(f"voisr: dropped invalid dictionary entries in {path}", flush=True)
        else:
            print(f"voisr: ignoring invalid {key} in {path}", flush=True)
    return out


def _quarantine(p: Path) -> Path | None:
    """Move an unparseable config aside so the next save can't overwrite the
    only copy of the user's dictionary."""
    import time
    target = p.with_name(f"{p.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    n = 1
    while target.exists():
        n += 1
        target = target.with_name(f"{p.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
    try:
        os.replace(p, target)
    except OSError as e:
        print(f"voisr: could not move aside corrupt config {p}: {e}", flush=True)
        return None
    return target


def load_config(path, on_corrupt=None) -> dict:
    """Defaults overlaid with config.json. A file that can't be parsed is
    moved to config.json.corrupt-<ts> and on_corrupt(saved_path) is called."""
    cfg = dict(DEFAULT_CONFIG)
    p = Path(path)
    if not p.exists():
        return cfg
    try:
        data = json.loads(p.read_bytes().decode("utf-8"))
    except OSError as e:
        # unreadable right now, not proven corrupt: leave the file alone
        print(f"voisr: ignoring unreadable config {p}: {e}", flush=True)
        return cfg
    except ValueError as e:  # JSONDecodeError and UnicodeDecodeError
        data = None
        print(f"voisr: config {p} is not valid JSON: {e}", flush=True)
    if not isinstance(data, dict):
        saved = _quarantine(p)
        if saved is not None and on_corrupt is not None:
            on_corrupt(saved)
        return cfg
    cfg.update(_salvage(data, p))
    return cfg


def process_audio(wav_path, dictionary) -> str:
    return clean(transcribe(wav_path), dictionary=dictionary)


def deliver(text: str, paste: bool) -> None:
    if not text:
        print("(nothing transcribed)")
        return
    if paste:
        try:
            pasted = paste_at_cursor(text)
        except PasteDeliveryError as error:
            if error.pasted:
                print(f"pasted: {text} (clipboard warning: {error})")
            elif error.fallback_safe:
                set_clipboard(text)
                print(f"copied to clipboard (paste blocked or disabled): {text}")
            else:
                print(f"delivery failed; {error}: {text}")
            return
        if pasted:
            print(f"pasted: {text}")
            return
    set_clipboard(text)
    print(f"copied to clipboard (paste blocked or disabled): {text}")


def toggle_mode(cfg) -> None:
    recorder = Recorder(cfg["device"])
    wav = None
    print("Toggle mode. Enter = start/stop recording, Ctrl-C to quit.")
    try:
        while True:
            input("  [Enter] to START recording... ")
            fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            wav = Path(wav_path)
            try:
                recorder.start(wav)
                input("  recording — [Enter] to STOP... ")
                err = recorder.stop()
                if not wav.exists() or wav.stat().st_size < 1000:
                    print(f"  recording failed (mic permission?): {err.strip()[:200]}")
                    continue
                deliver(process_audio(wav, cfg["dictionary"]), cfg["paste"])
            finally:
                recorder.cancel()
                wav.unlink(missing_ok=True)
                wav = None
    finally:
        recorder.close()
        if wav is not None:
            wav.unlink(missing_ok=True)


def hotkey_mode(cfg, config_path) -> None:
    # Reuse the app's HotkeyService: zero-latency stream capture and
    # transcription off the event-tap thread (a blocked tap gets disabled
    # by macOS and pynput never re-enables it).
    import time

    from voisr.ui import Api, HotkeyService, default_recorder, request_permissions

    api = None
    service = None
    try:
        request_permissions()
        api = Api(config_path=config_path, recorder_factory=default_recorder)
        service = HotkeyService(api)
        service.start()  # config-driven: prefers captured hotkey_vk
        print(f"Hold [{cfg['hotkey']}] to talk. Ctrl-C to quit. ({service.status})")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if service is not None:
                service.stop()
        finally:
            if api is not None:
                api.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="voisr: on-device push-to-talk dictation")
    ap.add_argument("--mode", choices=["toggle", "hotkey", "file"], default="toggle")
    ap.add_argument("--file", help="audio file to transcribe (mode=file)")
    ap.add_argument("--config", default=Path(__file__).resolve().parent.parent / "config.json")
    ap.add_argument("--no-paste", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.no_paste:
        cfg["paste"] = False
    if args.mode == "file":
        if not args.file:
            ap.error("--file is required with --mode file")
        from voisr.audio import to_wav16k
        wav = Path(args.file + ".16k.wav")
        try:
            print(process_audio(to_wav16k(args.file, wav), cfg["dictionary"]))
        finally:
            wav.unlink(missing_ok=True)  # a scratch file beside the user's memo
    elif args.mode == "hotkey":
        hotkey_mode(cfg, args.config)
    else:
        try:
            toggle_mode(cfg)
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)


if __name__ == "__main__":
    main()
