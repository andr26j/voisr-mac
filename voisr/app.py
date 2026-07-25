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
                  "appearance": "system", "launch_at_login": False, "model": None}


def load_config(path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    p = Path(path)
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError) as e:
            # a corrupt config must not brick the app; run on defaults
            print(f"voisr: ignoring unreadable config {p}: {e}", flush=True)
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
        wav = to_wav16k(args.file, args.file + ".16k.wav")
        print(process_audio(wav, cfg["dictionary"]))
    elif args.mode == "hotkey":
        hotkey_mode(cfg, args.config)
    else:
        try:
            toggle_mode(cfg)
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)


if __name__ == "__main__":
    main()
