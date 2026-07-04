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


def default_recorder():
    """Prefer the zero-latency persistent stream; fall back to ffmpeg."""
    try:
        from voisr.stream_recorder import StreamRecorder
        return StreamRecorder()
    except Exception:
        return Recorder()


def request_permissions() -> None:
    """Register Voisr with TCC and prompt for the grants it needs, so the app
    APPEARS in System Settings → Privacy & Security under each pane:
      • Input Monitoring — CGRequestListenEventAccess + the event tap itself
      • Accessibility    — AXIsProcessTrustedWithOptions(prompt) posts events"""
    try:
        import Quartz
        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
        if not Quartz.CGPreflightPostEventAccess():
            Quartz.CGRequestPostEventAccess()
    except Exception:
        pass
    try:
        from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                         kAXTrustedCheckOptionPrompt)
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        pass


def paste_ready() -> bool:
    try:
        import Quartz
        return bool(Quartz.CGPreflightPostEventAccess())
    except Exception:
        return False

def wav_peak(path) -> int:
    """Peak |amplitude| of a 16-bit WAV, -1 if unparseable (skip the check)."""
    import array
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                return -1
            data = w.readframes(w.getnframes())
        if not data:
            return 0
        samples = array.array("h")
        samples.frombytes(data[:len(data) // 2 * 2])
        return max(abs(s) for s in samples) if samples else 0
    except Exception:
        return -1


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


class RecordingWidget:
    """Floating Wispr-style pill shown while dictating.

    Built as a native, non-activating NSPanel hosting a WKWebView (which
    renders web/widget.html). A plain NSWindow — which is all pywebview can
    create — will NOT appear over another app's native-fullscreen Space, no
    matter the level or collection behavior. Only a panel with the
    NSWindowStyleMaskNonactivating style crosses onto a fullscreen Space,
    which is how Wispr's pill floats over everything. It never takes focus,
    so paste-at-cursor is undisturbed.
    """

    WIDTH, HEIGHT = 232, 52
    BOTTOM_MARGIN = 72

    # AppKit constants (stable ABI values)
    _STYLE_NONACTIVATING = 1 << 7
    _CB_CAN_JOIN_ALL_SPACES = 1 << 0
    _CB_STATIONARY = 1 << 4
    _CB_FULLSCREEN_AUX = 1 << 8
    _SCREEN_SAVER_LEVEL = 1000

    def __init__(self):
        self._panel = None
        self._webview = None

    def create(self) -> None:
        """Build the panel + webview on the main thread and preload the HTML.
        Safe to call from a worker thread (dispatches onto the main thread)."""
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._build)
        except Exception:
            pass

    def _build(self) -> None:
        try:
            from AppKit import (NSPanel, NSColor, NSBackingStoreBuffered,
                                NSScreen)
            from WebKit import WKWebView, WKWebViewConfiguration
            from Foundation import NSMakeRect, NSURL

            frame = NSScreen.mainScreen().frame()
            x = (frame.size.width - self.WIDTH) / 2
            y = self.BOTTOM_MARGIN
            rect = NSMakeRect(x, y, self.WIDTH, self.HEIGHT)

            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                rect, self._STYLE_NONACTIVATING, NSBackingStoreBuffered, False)
            panel.setFloatingPanel_(True)
            panel.setBecomesKeyOnlyIfNeeded_(True)
            panel.setLevel_(self._SCREEN_SAVER_LEVEL)
            panel.setCollectionBehavior_(
                self._CB_CAN_JOIN_ALL_SPACES | self._CB_STATIONARY
                | self._CB_FULLSCREEN_AUX)
            panel.setHidesOnDeactivate_(False)
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.clearColor())
            panel.setTitle_("Voisr Recording")  # for identification, not shown

            cfg = WKWebViewConfiguration.alloc().init()
            wv = WKWebView.alloc().initWithFrame_configuration_(
                NSMakeRect(0, 0, self.WIDTH, self.HEIGHT), cfg)
            wv.setValue_forKey_(False, "drawsBackground")  # transparent
            html = WEB_DIR / "widget.html"
            wv.loadFileURL_allowingReadAccessToURL_(
                NSURL.fileURLWithPath_(str(html)),
                NSURL.fileURLWithPath_(str(WEB_DIR)))
            panel.setContentView_(wv)

            self._panel = panel
            self._webview = wv
        except Exception as e:
            print(f"Voisr widget: build failed: {e}", flush=True)

    def _set(self, state: str) -> None:
        try:
            from PyObjCTools import AppHelper

            def go():
                if self._panel is None:
                    self._build()
                if self._webview is None:
                    return
                self._webview.evaluateJavaScript_completionHandler_(
                    f'setState("{state}")', None)
                self._panel.orderFrontRegardless()

            AppHelper.callAfter(go)
        except Exception:
            pass  # never break the record path

    def show_recording(self):
        self._set("recording")

    def show_processing(self):
        self._set("processing")

    def show_done(self):
        self._set("done")

    def show_error(self):
        self._set("error")

    def hide(self):
        try:
            from PyObjCTools import AppHelper

            def go():
                if self._panel is not None:
                    self._panel.orderOut_(None)

            AppHelper.callAfter(go)
        except Exception:
            pass


def input_permissions_ok() -> bool:
    """True when Voisr may listen for global keys (and post events)."""
    try:
        import Quartz
        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return True  # non-pyobjc environments (tests, other platforms)


def _pynput_listener_factory(on_press, on_release):
    """Create and start a real global keyboard listener (daemon thread)."""
    from pynput import keyboard
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
    return listener


class HotkeyService:
    """Global push-to-talk listener driving the shared Api record path."""

    def __init__(self, api, widget=None, hide_delay=1.2,
                 permissions_check=input_permissions_ok, poll_interval=3.0,
                 listener_factory=None):
        self._api = api
        self._widget = widget
        self._hide_delay = hide_delay
        self._permissions_check = permissions_check
        self._poll_interval = poll_interval
        self._listener_factory = listener_factory or _pynput_listener_factory
        self._listener = None
        self._watcher = None
        self._stopped = False
        self._pressed = False
        self._key_name = None
        self.status = "not started"

    def _widget_enabled(self) -> bool:
        return self._widget is not None and bool(self._api.get_config().get("show_widget", True))

    def press(self) -> None:
        if self._pressed:
            return
        result = self._api.start_recording()
        if "error" in result:
            return  # e.g. a dashboard recording is already running
        self._pressed = True
        if self._widget_enabled():
            self._widget.show_recording()

    def release(self) -> None:
        if not self._pressed:
            return
        self._pressed = False
        show = self._widget_enabled()
        if show:
            self._widget.show_processing()
        # Transcription takes seconds; it must NOT run inside the pynput
        # callback: macOS disables event taps whose callbacks stall, and
        # pynput never re-enables them (the hotkey would die after one use).
        import threading
        threading.Thread(target=self._finish, args=(show,), daemon=True).start()

    def _finish(self, show: bool) -> None:
        result = self._api.stop_recording(paste=True)
        if show:
            if "error" in result:
                self._widget.show_error()
            else:
                self._widget.show_done()
            if self._hide_delay:
                import time
                time.sleep(self._hide_delay)
            self._widget.hide()

    def start(self, key_name: str) -> None:
        self._stopped = False
        self._key_name = key_name
        # Attempt the listener NOW even if unpermitted: creating the event
        # tap is exactly what lists Voisr under Input Monitoring and shows
        # the prompt. An untrusted tap is null (pynput returns cleanly) — it
        # does not crash — so this is safe. request_permissions() also fires
        # the Accessibility prompt so Voisr appears in that pane too.
        request_permissions()
        self._spawn_listener(key_name)
        self._refresh_status()
        self._watch(key_name)

    def _spawn_listener(self, key_name: str) -> None:
        target = resolve_key(key_name)

        # Callbacks must never raise: pynput kills the listener on any
        # callback exception and the hotkey would go permanently dead.
        def on_press(key):
            try:
                if key == target:
                    self.press()
            except Exception:
                pass

        def on_release(key):
            try:
                if key == target:
                    self.release()
            except Exception:
                pass

        old, self._listener = self._listener, None
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        try:
            self._listener = self._listener_factory(on_press, on_release)
        except Exception as e:
            print(f"Voisr hotkey: listener start failed: {e}", flush=True)

    def _refresh_status(self) -> None:
        if self._permissions_check():
            self.status = f"active on {self._key_name}"
        else:
            self.status = ("waiting for permission — open System Settings → "
                           "Privacy & Security and switch Voisr ON under BOTH "
                           "Input Monitoring and Accessibility")
        print(f"Voisr hotkey: {self.status}", flush=True)

    def _watch(self, key_name: str) -> None:
        if self._watcher is not None:
            return
        import threading
        import time

        def poll():
            was_granted = self._permissions_check()
            while not self._stopped:
                granted = self._permissions_check()
                if granted and not was_granted:
                    # permission just landed → respawn to get a LIVE tap
                    # (the untrusted listener spawned at start() exited)
                    self._spawn_listener(key_name)
                    self._refresh_status()
                was_granted = granted
                if granted:
                    break
                time.sleep(self._poll_interval)
            self._watcher = None

        self._watcher = threading.Thread(target=poll, daemon=True)
        self._watcher.start()

    def stop(self) -> None:
        self._stopped = True
        listener, self._listener = self._listener, None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            pass  # a dead listener's run loop teardown must not crash the app

    def restart(self, key_name: str) -> None:
        self.stop()
        self.start(key_name)


class Api:
    """JS<->Python bridge exposed to the dashboard via pywebview."""

    def __init__(self, config_path=ROOT / "config.json",
                 history=None, recorder=None, transcribe_fn=transcribe,
                 clock=time.monotonic, hotkey_service=None,
                 fallback_recorder_factory=Recorder):
        self._config_path = Path(config_path)
        self._history = history or HistoryStore(ROOT / "data" / "history.jsonl")
        self._recorder = recorder or default_recorder()
        self._fallback_recorder_factory = fallback_recorder_factory
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
        wav = Path(tempfile.mkstemp(suffix=".wav")[1])
        try:
            self._recorder.start(wav)
        except Exception:
            # e.g. the stream backend can't load; self-heal onto ffmpeg
            try:
                fallback = self._fallback_recorder_factory()
                fallback.start(wav)
                self._recorder = fallback
            except Exception as e:
                wav.unlink(missing_ok=True)
                return {"error": f"recorder failed: {e}"[:200]}
        self._wav = wav
        self._t0 = self._clock()
        return {"recording": True}

    def stop_recording(self, paste=False):
        if self._wav is None:
            return {"error": "not recording"}
        wav, self._wav = self._wav, None
        try:
            err = self._recorder.stop()
        except Exception as e:
            wav.unlink(missing_ok=True)
            return {"error": f"recorder failed: {e}"[:200]}
        duration = max(self._clock() - self._t0, 0.1)
        try:
            if not wav.exists() or wav.stat().st_size < 1000:
                return {"error": f"recording failed (mic permission?) {err.strip()[:120]}"}
            peak = wav_peak(wav)
            if 0 <= peak < 60:
                # digital silence: whisper would hallucinate ("You", "Thank you")
                return {"error": "no audio captured — allow Voisr to use the "
                                 "Microphone in System Settings → Privacy & "
                                 "Security, then try again"}
            cfg = self.get_config()
            try:
                text = clean(self._transcribe_fn(wav), dictionary=cfg.get("dictionary", {}))
            except Exception as e:
                return {"error": f"transcription failed: {e}"[:250]}
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
        status = self._hotkey_service.status
        if status.startswith("active") and self.get_config().get("paste"):
            status += " · paste ready" if paste_ready() \
                else " · grant Accessibility to auto-paste"
        return {"status": status}

    def prime_microphone(self):
        """Retained for interface parity — intentionally does NOT open the
        mic. The stream is opened only while recording so the macOS "mic in
        use" indicator is off when idle; the permission prompt appears on
        the first real dictation."""
        return {"ok": True}

    def copy_text(self, text):
        set_clipboard(text)
        return {"ok": True}


def main():
    import platform

    import webview
    try:
        import sounddevice
        audio = "stream-audio ok"
    except Exception as e:
        audio = f"stream-audio unavailable ({e.__class__.__name__}), using ffmpeg"
    from voisr.paths import find_binary
    whisper = find_binary("whisper-cli") or "MISSING"
    print(f"Voisr starting (python arch: {platform.machine()}, {audio}, "
          f"whisper: {whisper})", flush=True)
    from voisr.inject import prepare_paste_controller
    prepare_paste_controller()  # main thread, fast, non-blocking (TIS APIs)
    api = Api()
    widget = RecordingWidget()
    service = HotkeyService(api, widget=widget)
    api.attach_hotkey_service(service)
    window = webview.create_window("Voisr", str(WEB_DIR / "index.html"),
                                   js_api=api, width=1060, height=700, min_size=(860, 560))
    widget.create()

    def on_started():
        # Runs on a worker thread AFTER the GUI run loop is up, so the
        # Input Monitoring / Accessibility prompts can display and blocking
        # calls never freeze startup. The mic is NOT opened here — it opens
        # only during an actual dictation, so the "mic in use" indicator is
        # off while idle.
        service.start(api.get_config().get("hotkey", "alt_r"))

    try:
        webview.start(on_started)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
