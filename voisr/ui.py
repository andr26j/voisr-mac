import json
import os
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

PILL_THEMES = [
    {"value": "classic", "label": "Classic"},
    {"value": "ember", "label": "Ember"},
    {"value": "aurora", "label": "Aurora"},
    {"value": "ocean", "label": "Ocean"},
    {"value": "neon", "label": "Neon EQ"},
    {"value": "breathe", "label": "Breathe"},
    {"value": "scanner", "label": "Scanner"},
]
_PILL_THEME_VALUES = {t["value"] for t in PILL_THEMES}


def sanitize_pill_theme(name) -> str:
    """Whitelist a theme name from config before it reaches JS interpolation."""
    return name if isinstance(name, str) and name in _PILL_THEME_VALUES else "classic"


def resolve_key(key_name: str):
    """Map a config key name to a pynput key object."""
    from pynput import keyboard
    named = getattr(keyboard.Key, key_name, None)
    return named if named is not None else keyboard.KeyCode.from_char(key_name)


# macOS virtual keycodes for keys pynput delivers via flagsChanged but has
# no modifier-flag mapping for — it reports BOTH edges as on_release, so the
# service must use toggle semantics (1st event = press, 2nd = release).
# Modifiers never auto-repeat, so toggling is sound.
TOGGLE_VKS = {63}  # Fn / Globe

# CGEventFlags bit that mirrors each modifier key's physical state, so press
# vs release can be read from the event itself instead of blind toggling
# (a missed edge would otherwise invert the toggle until the next miss).
# Side-specific keys use the stable NX_DEVICE* bits.
_VK_FLAG_MASKS = {
    63: 0x800000,   # Fn / Globe (kCGEventFlagMaskSecondaryFn)
    58: 0x20, 61: 0x40,      # Left / Right Option
    55: 0x8, 54: 0x10,       # Left / Right Command
    56: 0x2, 60: 0x4,        # Left / Right Shift
    59: 0x1, 62: 0x2000,     # Left / Right Control
}

VK_LABELS = {
    63: "Fn / Globe", 57: "Caps Lock",
    58: "Left Option (⌥)", 61: "Right Option (⌥)",
    55: "Left Command (⌘)", 54: "Right Command (⌘)",
    56: "Left Shift (⇧)", 60: "Right Shift (⇧)",
    59: "Left Control (⌃)", 62: "Right Control (⌃)",
    49: "Space", 48: "Tab", 36: "Return", 53: "Esc",
    122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6",
    98: "F7", 100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12",
    105: "F13", 107: "F14", 113: "F15", 106: "F16", 64: "F17",
    79: "F18", 80: "F19",
}

_LEGACY_NAME_TO_VK = {
    "alt_r": 61, "alt_l": 58, "alt": 58,
    "cmd_r": 54, "cmd_l": 55, "cmd": 55,
    "ctrl_r": 62, "ctrl_l": 59, "ctrl": 59,
    "shift_r": 60, "shift_l": 56, "shift": 56,
    "f13": 105, "f14": 107, "f15": 113, "f16": 106,
    "f17": 64, "f18": 79, "f19": 80,
}


def legacy_name_to_vk(name: str):
    return _LEGACY_NAME_TO_VK.get(name)


def event_vk(key):
    """Virtual keycode of a pynput event key (KeyCode or Key enum)."""
    vk = getattr(key, "vk", None)
    if vk is None:
        vk = getattr(getattr(key, "value", None), "vk", None)
    return vk


def key_label(vk: int, key=None) -> str:
    """Human-readable name for a captured key."""
    if vk in VK_LABELS:
        return VK_LABELS[vk]
    char = getattr(key, "char", None)
    if char:
        return char.upper()
    name = getattr(key, "name", None)
    if name:
        return name.replace("_", " ").title()
    return f"Key {vk}"


def capture_next_key(timeout: float = 8.0):
    """Block until the user presses any key; return (vk, label) or None."""
    from voisr.keytap import capture_next_vk
    vk = capture_next_vk(timeout)
    if vk is None:
        return None
    return vk, key_label(vk)


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


def _quartz_listener_factory(handler):
    """Create and start the global key tap (see voisr.keytap for why this
    replaces pynput's listener, which crashes the process via TIS)."""
    from voisr.keytap import QuartzKeyListener
    listener = QuartzKeyListener(handler)
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
        self._listener_factory = listener_factory or _quartz_listener_factory
        self._listener = None
        self._watcher = None
        self._stopped = False
        self._pressed = False
        self._key_name = None
        self._target_vk = None
        self._epoch = 0  # bumped per recording; stale _finish threads must not touch the widget
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
        self._epoch += 1
        cfg = self._api.get_config()
        if self._widget is not None and bool(cfg.get("show_widget", True)):
            self._widget.show_recording(theme=cfg.get("pill_theme", "classic"))

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
        threading.Thread(target=self._finish, args=(show, self._epoch),
                         daemon=True).start()

    def _finish(self, show: bool, epoch: int) -> None:
        result = self._api.stop_recording(paste=True)
        if show and epoch == self._epoch:
            if "error" in result:
                self._widget.show_error()
            else:
                self._widget.show_done()
            if self._hide_delay:
                import time
                time.sleep(self._hide_delay)
            if epoch == self._epoch:  # a newer recording owns the widget now
                self._widget.hide()

    def _resolve_target(self, key_name=None):
        """Return (vk, label) for the push-to-talk key.

        Explicit key_name (tests/CLI) wins; otherwise the config's captured
        hotkey_vk, falling back to the legacy 'hotkey' name."""
        if key_name is not None:
            vk = legacy_name_to_vk(key_name)
            if vk is None:
                vk = event_vk(resolve_key(key_name))
            return vk, key_label(vk) if vk is not None else key_name
        cfg = self._api.get_config()
        vk = cfg.get("hotkey_vk")
        if vk is not None:
            return int(vk), cfg.get("hotkey_label") or key_label(int(vk))
        name = cfg.get("hotkey", "alt_r")
        vk = legacy_name_to_vk(name)
        if vk is None:
            vk = event_vk(resolve_key(name))
        return vk, key_label(vk) if vk is not None else name

    def start(self, key_name=None) -> None:
        self._stopped = False
        vk, label = self._resolve_target(key_name)
        self._target_vk = vk
        self._key_name = label
        # Attempt the listener NOW even if unpermitted: creating the event
        # tap is exactly what lists Voisr under Input Monitoring and shows
        # the prompt. An untrusted tap is null (pynput returns cleanly) — it
        # does not crash — so this is safe. request_permissions() also fires
        # the Accessibility prompt so Voisr appears in that pane too.
        request_permissions()
        self._spawn_listener()
        self._refresh_status()
        self._watch()

    def _toggle(self) -> None:
        """flagsChanged-only keys (Fn) report both edges identically."""
        if self._pressed:
            self.release()
        else:
            self.press()

    def _spawn_listener(self) -> None:
        target_vk = self._target_vk

        def handler(vk, kind, flags):
            # Never raise: a callback error must not kill the tap.
            try:
                if vk != target_vk:
                    if kind == "down":
                        # user is typing manually -> don't smart-join the
                        # next dictation onto the previous one
                        self._api.note_user_typing()
                    return
                if kind == "down":       # auto-repeats; press() guards
                    self.press()
                elif kind == "up":
                    self.release()
                else:                    # "flags": modifier edges (Fn, ⌥, ⌘...)
                    mask = _VK_FLAG_MASKS.get(vk)
                    if mask is not None:
                        # the flag bit carries the key's true state — immune
                        # to the desync a missed edge causes with toggling
                        if flags & mask:
                            self.press()
                        else:
                            self.release()
                    else:                # e.g. Caps Lock: flag ≠ key state
                        self._toggle()   # modifiers never auto-repeat
            except Exception:
                pass

        old, self._listener = self._listener, None
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        try:
            self._listener = self._listener_factory(handler)
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

    def _watch(self) -> None:
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
                    self._spawn_listener()
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

    def restart(self, key_name=None) -> None:
        self.stop()
        self.start(key_name)


class Api:
    """JS<->Python bridge exposed to the dashboard via pywebview."""

    SPACE_JOIN_WINDOW_S = 300  # only join dictations this close together

    def __init__(self, config_path=ROOT / "config.json",
                 history=None, recorder=None, transcribe_fn=None,
                 clock=time.monotonic, hotkey_service=None,
                 fallback_recorder_factory=Recorder,
                 capture_fn=capture_next_key,
                 paste_fn=paste_at_cursor):
        self._config_path = Path(config_path)
        self._history = history or HistoryStore(ROOT / "data" / "history.jsonl")
        self._recorder = recorder or default_recorder()
        self._fallback_recorder_factory = fallback_recorder_factory
        self._transcribe_fn = transcribe_fn or self._default_transcribe
        self._clock = clock
        self._hotkey_service = hotkey_service
        self._capture_fn = capture_fn
        self._paste_fn = paste_fn
        self._wav = None
        self._t0 = None
        self._last_paste_text = None
        self._last_paste_ts = None
        self._typed_since_paste = False
        self._window = None

    def _default_transcribe(self, wav):
        """Transcribe with the best local model, biased toward the user's
        dictionary vocabulary (names/jargon transcribe correctly first try)."""
        from voisr.transcriber import resolve_model, transcribe as _transcribe
        cfg = self.get_config()
        vocab = sorted({v for v in (cfg.get("dictionary") or {}).values()})
        prompt = ("Glossary: " + ", ".join(vocab) + ".") if vocab else None
        return _transcribe(wav, model_path=resolve_model(cfg.get("model")),
                           prompt=prompt)

    def note_user_typing(self):
        """Called by the key listener when any non-hotkey key is typed;
        suppresses smart joining of the next dictation."""
        self._typed_since_paste = True

    def attach_hotkey_service(self, service):
        self._hotkey_service = service

    def attach_window(self, window):
        """Dashboard window handle, used to push live-refresh events to JS."""
        self._window = window

    def _notify_dictation(self):
        """Tell the dashboard a dictation landed (hotkey dictations must show
        up without a view switch). Fire-and-forget off-thread: a slow or dead
        webview must never delay or break the record path."""
        win = self._window
        if win is None:
            return
        import threading

        def go():
            try:
                win.evaluate_js(
                    'window.dispatchEvent(new Event("voisr:new-dictation"))')
            except Exception:
                pass

        threading.Thread(target=go, daemon=True).start()

    def capture_hotkey(self):
        """'Press any key' flow: suspend the live listener (so the pressed
        key doesn't trigger a dictation), capture the next key — any key,
        including Fn/Globe — persist it, and restart the listener on it."""
        svc = self._hotkey_service
        if svc is not None:
            svc.stop()
        try:
            got = self._capture_fn(8.0)
            if not got:
                return {"error": "no key detected — click Change and press a key"}
            vk, label = got
            cfg = self.get_config()
            cfg["hotkey_vk"] = vk
            cfg["hotkey_label"] = label
            self._config_path.write_text(json.dumps(cfg, indent=2))
            return {"vk": vk, "label": label}
        finally:
            if svc is not None:
                svc.start()

    def start_recording(self):
        if self._wav is not None:
            return {"error": "already recording"}
        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)  # the recorder writes by path; keeping fd open leaks one per dictation
        wav = Path(wav_path)
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
            self._notify_dictation()
            pasted = False
            if paste and cfg.get("paste"):
                lead = ""
                if (self._last_paste_text
                        and not self._typed_since_paste
                        and self._clock() - self._last_paste_ts <= self.SPACE_JOIN_WINDOW_S
                        and not self._last_paste_text[-1].isspace()):
                    lead = " "  # joining consecutive dictations, like Wispr
                pasted = self._paste_fn(lead + text)
                if pasted:
                    self._last_paste_text = text
                    self._last_paste_ts = self._clock()
                    self._typed_since_paste = False
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

    def get_pill_themes(self):
        return PILL_THEMES

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
    api.attach_window(window)
    widget.create()

    def on_started():
        # Runs on a worker thread AFTER the GUI run loop is up, so the
        # Input Monitoring / Accessibility prompts can display and blocking
        # calls never freeze startup. The mic is NOT opened here — it opens
        # only during an actual dictation, so the "mic in use" indicator is
        # off while idle.
        service.start()  # config-driven: prefers captured hotkey_vk

    try:
        webview.start(on_started)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
