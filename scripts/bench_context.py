#!/usr/bin/env python3
"""What screen context costs, on this Mac, against real apps. Takes focus for a
few seconds (TextEdit, on its own temp file, closed after) and briefly uses the
clipboard. Prints text lengths, never text.

  python3 scripts/bench_context.py [--models-dir DIR] [--model small.en] [--no-whisper]

The numbers in the report: capture and verify reads, delivery with the feature
on and off, what context terms cost whisper, and a hung target app (a second
TextEdit, stopped with SIGSTOP, then killed)."""
import argparse
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voisr.ax_context import UNREADABLE, MacAXAdapter, capture_focus  # noqa: E402
from voisr.context_terms import context_terms  # noqa: E402
from voisr.history import HistoryStore  # noqa: E402
from voisr.inject import paste_at_cursor, prepare_paste_controller  # noqa: E402
from voisr.ui import Api, glossary_prompt  # noqa: E402

MARK = "voisr-bench-doc"
BODY = (f"{MARK} Thanks Priya Raman, the Kestrelway dashboard is ready for Zorblax. "
        "We can ship getUserName on Tuesday if GitHub agrees. ") * 8


def osa(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                          timeout=20, check=True).stdout.rstrip("\n")


def spin(seconds=0.05):
    from AppKit import NSDate, NSRunLoop
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def stats(name, values):
    values = sorted(values)
    print(f"  {name:38s} n={len(values):3d} median={statistics.median(values):7.1f} ms  "
          f"p95={values[int(len(values) * .95) - 1 if len(values) > 1 else 0]:7.1f}  "
          f"max={values[-1]:7.1f}")


class Pinned(MacAXAdapter):
    """Reads one app, focused or not. Bench only: nothing is written anywhere."""

    def __init__(self, pid):
        super().__init__()
        self.pid = pid

    def frontmost(self):
        return self._describe(self._running_app.runningApplicationWithProcessIdentifier_(self.pid))

    def focused_element(self, timeout_s):
        return self.attribute(self._hi.AXUIElementCreateApplication(self.pid),
                              "AXFocusedUIElement", timeout_s)


class Recorder:
    def start(self, path):
        self.path = path

    def stop(self):
        with wave.open(str(self.path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\x10\x20" * 16000 * 3)
        return ""

    def cancel(self):
        pass

    def close(self):
        pass


def textedit_reads(adapter):
    print("TextEdit, real AX, focused document:")
    press, release, term = [], [], []
    for _ in range(30):
        start = time.perf_counter()
        focus = capture_focus(adapter, 0.15)
        press.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        capture_focus(adapter, 0.15, textbox_only=True)
        release.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        terms = context_terms(focus)
        term.append((time.perf_counter() - start) * 1000)
    print(f"  field {len(focus.value or '')} chars, readable={focus.readable}, "
          f"{len(terms)} terms, {len(focus.nearby_texts)} nearby")
    stats("press capture (with window walk)", press)
    stats("release / verify read (textbox only)", release)
    stats("term extraction", term)


def delivery(adapter, tmp):
    print("Real Cmd-V into TextEdit, whisper stubbed (history timings):")
    prepare_paste_controller()
    for label, ax in (("feature on", adapter), ("no adapter", None)):
        api = Api(config_path=tmp / f"{label}.json",
                  history=HistoryStore(tmp / f"{label}.jsonl"),
                  recorder_factory=Recorder, ax_adapter=ax,
                  transcribe_fn=lambda wav: "benchmark words", paste_fn=paste_at_cursor)
        walls = []
        for _ in range(8):
            spin()
            api.start_recording(prewarm=False)
            api.prewarm_now()
            if api._focus is not None:
                api._focus.worker.submit(lambda: None).result(timeout=5)
            started = time.perf_counter()
            api.stop_recording(paste=True)
            walls.append((time.perf_counter() - started) * 1000)
        items = api.get_history()
        print(f" {label}:")
        for key in ("paste", "total", "ax_context", "verify"):
            values = [i["timings_ms"][key] for i in items if key in i.get("timings_ms", {})]
            if values:
                stats(key, values)
        stats("release to result (includes 1 s grace)", walls)
        verdicts = {i.get("delivery") for i in items}
        print(f"  verdicts: {verdicts}")
        api.close()


def whisper_cost(models_dir, model_name, tmp):
    from voisr.transcriber import resolve_model, transcribe_routed
    from voisr.whisper_server import WhisperServer

    print(f"What context terms cost whisper ({model_name}, a 6 s clip):")
    aiff, wav = tmp / "clip.aiff", tmp / "clip.wav"
    sentence = ("We hired Zorblax Quindleton to build the Kestrelway dashboard, "
                "and it ships next Tuesday.")
    subprocess.run(["say", "-o", str(aiff), sentence], check=True, timeout=30)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
                   check=True, timeout=30)
    model = resolve_model(model_name, models_dir)
    focus_terms = [f"Termname{i}" for i in range(20)]
    with_terms = glossary_prompt({}, focus_terms)
    server = WhisperServer(tmp / "state.json")
    try:
        transcribe_routed(wav, model, server=server)  # load
        for label, prompt in (("server, no prompt", None), ("server, 20 terms", with_terms)):
            values = []
            for _ in range(6):
                start = time.perf_counter()
                _, info = transcribe_routed(wav, model, prompt=prompt, server=server)
                assert info["mode"] == "warm"
                values.append((time.perf_counter() - start) * 1000)
            stats(label, values)
    finally:
        server.shutdown()
    for label, prompt in (("cli, no prompt", None), ("cli, 20 terms", with_terms)):
        values = []
        for _ in range(4):
            start = time.perf_counter()
            transcribe_routed(wav, model, prompt=prompt)
            values.append((time.perf_counter() - start) * 1000)
        stats(label, values)


def hung_app():
    print("A hung target (scratch TextEdit, SIGSTOP):")
    before = set(subprocess.run(["pgrep", "-x", "TextEdit"], capture_output=True,
                                text=True).stdout.split())
    subprocess.run(["open", "-n", "-g", "-a", "TextEdit"], check=True, timeout=20)
    pid = None
    for _ in range(100):
        time.sleep(0.1)
        now = set(subprocess.run(["pgrep", "-x", "TextEdit"], capture_output=True,
                                 text=True).stdout.split())
        new = now - before
        if new:
            pid = int(new.pop())
            break
    if pid is None:
        print("  could not start a scratch instance")
        return
    try:
        time.sleep(1.0)
        adapter = Pinned(pid)
        os.kill(pid, signal.SIGSTOP)
        gaps, stop = [], threading.Event()

        def ticker():
            last = time.perf_counter()
            while not stop.is_set():
                time.sleep(0.001)
                now = time.perf_counter()
                gaps.append((now - last) * 1000)
                last = now

        thread = threading.Thread(target=ticker)
        thread.start()
        captures = []
        reasons = set()
        for _ in range(5):
            start = time.perf_counter()
            focus = capture_focus(adapter, 0.15)
            captures.append((time.perf_counter() - start) * 1000)
            reasons.add(focus.reason)
        stop.set()
        thread.join()
        stats("capture against the stopped app", captures)
        print(f"  reasons {reasons}; other threads' longest stall {max(gaps):.1f} ms "
              "(the GIL is free during an AX call if this stays small)")
    finally:
        try:
            os.kill(pid, signal.SIGCONT)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        print("  scratch instance killed")


def other_apps():
    from AppKit import NSWorkspace
    print("Other running apps, read-only, as they are now (lengths and counts only):")
    front = NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier()
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() != 0 or app.bundleIdentifier() in ("com.local.voisr",
                                                                    "com.apple.TextEdit"):
            continue
        pid = app.processIdentifier()
        adapter = Pinned(pid)
        start = time.perf_counter()
        focus = capture_focus(adapter, 0.15)
        spent = (time.perf_counter() - start) * 1000
        root = adapter._hi.AXUIElementCreateApplication(pid)
        window = adapter.attribute(root, "AXFocusedWindow", 0.15)
        kids = adapter.attribute(window, "AXChildren", 0.15) if window is not UNREADABLE else UNREADABLE
        print(f"  {app.bundleIdentifier()}{' (front)' if app.bundleIdentifier() == front else ''}: "
              f"role={focus.role} reason={focus.reason} readable={focus.readable} "
              f"value={len(focus.value) if focus.value is not None else None} "
              f"nearby={len(focus.nearby_texts)} electron={adapter.is_electron(pid)} "
              f"window_children={len(kids) if kids is not UNREADABLE else None} {spent:.1f} ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=str(
        Path.home() / "Library/Application Support/Voisr/models"))
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--no-whisper", action="store_true")
    args = ap.parse_args()
    adapter = MacAXAdapter()
    if not adapter._hi.AXIsProcessTrusted():
        sys.exit("this process has no Accessibility grant")
    other_apps()
    was_running = osa('application "TextEdit" is running') == "true"
    # A temp file, not a new document: with iCloud on, TextEdit saves untitled
    # documents to iCloud Drive and reopens them at every launch.
    with tempfile.TemporaryDirectory() as doc_dir:
        doc = Path(doc_dir) / f"{MARK}-{os.getpid()}.txt"
        doc.write_text(BODY)
        subprocess.run(["open", "-a", "TextEdit", str(doc)], check=True, timeout=20)
        osa('tell application "TextEdit" to activate')
        try:
            for _ in range(100):
                spin()
                front = adapter.frontmost()[0]
                if front == "com.apple.TextEdit":
                    break
            spin(0.3)
            textedit_reads(adapter)
            with tempfile.TemporaryDirectory() as tmp:
                delivery(adapter, Path(tmp))
        finally:
            osa(f'''with timeout of 8 seconds
                tell application "TextEdit" to close document "{doc.name}" saving no
            end timeout''')
            if not was_running:
                subprocess.run(["osascript", "-e", 'with timeout of 8 seconds\n'
                                'tell application "TextEdit" to quit\nend timeout'],
                               capture_output=True, timeout=20)
    hung_app()
    if not args.no_whisper:
        with tempfile.TemporaryDirectory() as tmp:
            whisper_cost(Path(args.models_dir), args.model, Path(tmp))


if __name__ == "__main__":
    main()
