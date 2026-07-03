import argparse
import json
import sys
import tempfile
from pathlib import Path

from voisr.cleanup import clean
from voisr.inject import paste_at_cursor, set_clipboard
from voisr.recorder import Recorder
from voisr.transcriber import transcribe

DEFAULT_CONFIG = {"hotkey": "alt_r", "paste": True, "device": ":0", "dictionary": {}}


def load_config(path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    p = Path(path)
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    return cfg


def process_audio(wav_path, dictionary) -> str:
    return clean(transcribe(wav_path), dictionary=dictionary)


def deliver(text: str, paste: bool) -> None:
    if not text:
        print("(nothing transcribed)")
        return
    if paste and paste_at_cursor(text):
        print(f"pasted: {text}")
    else:
        set_clipboard(text)
        print(f"copied to clipboard (paste blocked or disabled): {text}")


def toggle_mode(cfg) -> None:
    rec = Recorder(cfg["device"])
    print("Toggle mode. Enter = start/stop recording, Ctrl-C to quit.")
    while True:
        input("  [Enter] to START recording... ")
        wav = Path(tempfile.mkstemp(suffix=".wav")[1])
        rec.start(wav)
        input("  recording — [Enter] to STOP... ")
        err = rec.stop()
        if not wav.exists() or wav.stat().st_size < 1000:
            print(f"  recording failed (mic permission?): {err.strip()[:200]}")
            continue
        deliver(process_audio(wav, cfg["dictionary"]), cfg["paste"])
        wav.unlink(missing_ok=True)


def hotkey_mode(cfg) -> None:
    from pynput import keyboard

    rec = Recorder(cfg["device"])
    state = {"wav": None}
    key_name = cfg["hotkey"]
    target = getattr(keyboard.Key, key_name, None) or keyboard.KeyCode.from_char(key_name)
    print(f"Hold [{key_name}] to talk. Ctrl-C to quit.")

    def on_press(key):
        if key == target and state["wav"] is None:
            state["wav"] = Path(tempfile.mkstemp(suffix=".wav")[1])
            rec.start(state["wav"])
            print("● recording...")

    def on_release(key):
        if key == target and state["wav"] is not None:
            rec.stop()
            wav, state["wav"] = state["wav"], None
            if wav.exists() and wav.stat().st_size >= 1000:
                deliver(process_audio(wav, cfg["dictionary"]), cfg["paste"])
            else:
                print("(recording too short — hold the key while speaking)")
            wav.unlink(missing_ok=True)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


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
        hotkey_mode(cfg)
    else:
        try:
            toggle_mode(cfg)
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)


if __name__ == "__main__":
    main()
