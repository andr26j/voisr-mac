import json
import re
import threading
import time
from pathlib import Path

from voisr import launch_agent, permissions
from voisr.app import config_errors, load_config
from voisr.cleanup import clean
from voisr.history import HistoryStore
from voisr.inject import paste_at_cursor, set_clipboard
from voisr.keytap import TAP_MODES
from voisr.pasteboard import PasteDeliveryError
from voisr.recorder import Recorder
from voisr.recording_queue import (
    RecordingQueue,
    RecordingQueueError,
    RecordingSession,
    SessionResult,
)
from voisr.status_item import StatusItem
from voisr.window_chrome import apply_chrome


def _find_webview(native_window):
    """The WKWebView pywebview put in the window, or None.

    pywebview's Cocoa backend makes its WKWebView subclass
    (BrowserView.WebKitHost) the window's contentView directly --
    window.setContentView_(webview), fired from
    webView_didFinishNavigation_ once the initial page load completes --
    so the webview normally *is* native_window.contentView(), not a
    subview inside it (confirmed on the running app: contentView().
    subviews() holds only the webview's own internal WKFlippedView).
    Subviews are still checked as a fallback for a differently-shaped
    window. Either way, matching goes by class inheritance (isinstance
    against the real WebKit.WKWebView), not by name: at runtime the class
    is further KVO-swizzled to "NSKVONotifying_WebKitHost", so even a
    corrected name check would be fragile. Wrapped so a missing/failed
    WebKit import still returns None instead of raising -- chrome is
    cosmetic and must never stop the app."""
    try:
        import WebKit
        content = native_window.contentView()
        if isinstance(content, WebKit.WKWebView):
            return content
        for view in content.subviews():
            if isinstance(view, WebKit.WKWebView):
                return view
    except Exception:
        pass
    return None


def default_recorder():
    """Prefer the zero-latency persistent stream; fall back to ffmpeg."""
    try:
        from voisr.stream_recorder import StreamRecorder
        return StreamRecorder()
    except Exception:
        return Recorder()


def request_permissions(mode: str = "listen") -> None:
    """Register Voisr with TCC and prompt for the grants it needs, so the app
    APPEARS in System Settings → Privacy & Security under each pane:
      • Input Monitoring — listen-only tap only (mode "listen")
      • Accessibility    — posting Cmd-V, and the active tap (mode "active")"""
    try:
        import Quartz
        if mode == "listen" and not Quartz.CGPreflightListenEventAccess():
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


def apply_app_appearance(mode: str) -> None:
    """Point NSApp at a fixed appearance so the titlebar and vibrancy follow
    the user's choice. 'system' clears the override. Never raises.

    set_appearance() is a bridge call, and pywebview dispatches every
    bridge call on a fresh worker thread -- so without this, setAppearance_
    would hit AppKit off the main thread. Route it through AppHelper.callAfter
    like every other AppKit entry point on this branch (_run_save_panel,
    native_setup). Safe to call from the main thread too (e.g. native_setup's
    startup call): callAfter just schedules onto the run loop."""
    try:
        from PyObjCTools import AppHelper
        import AppKit

        def go():
            try:
                names = {"light": AppKit.NSAppearanceNameAqua,
                         "dark": AppKit.NSAppearanceNameDarkAqua}
                name = names.get(mode)
                appearance = (AppKit.NSAppearance.appearanceNamed_(name)
                              if name else None)
                AppKit.NSApp.setAppearance_(appearance)
            except Exception:
                pass

        AppHelper.callAfter(go)
    except Exception:
        pass


def _run_save_panel(suggested_name: str):
    """Show a save panel on the main thread and return the chosen path."""
    from PyObjCTools import AppHelper
    import AppKit
    result = {}
    done = threading.Event()

    def go():
        try:
            panel = AppKit.NSSavePanel.savePanel()
            panel.setNameFieldStringValue_(suggested_name)
            if panel.runModal() == AppKit.NSModalResponseOK:
                result["path"] = panel.URL().path()
        except Exception:
            pass
        finally:
            done.set()

    AppHelper.callAfter(go)
    # Untimed on purpose. panel.runModal() blocks the AppKit main thread
    # until the user dismisses the panel (Save or Cancel), so `go()` always
    # reaches its `finally` and calls done.set() -- there is no path where
    # nobody ever answers. A wait(120) used to cap this: if the user left
    # the panel open past two minutes, this thread gave up and returned
    # {"ok": False, "path": None} while the panel was still on screen. That
    # desync let a later Save write a file the caller had already been told
    # didn't happen, with no confirmation shown for it. Do not reintroduce a
    # timeout here -- see tests/test_ui_api.py::
    # test_run_save_panel_waits_without_a_timeout.
    done.wait()
    return result.get("path")


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


def wav_seconds(path) -> float:
    """Length of a WAV in seconds, -1 if unparseable (skip the check)."""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return -1.0


# Shorter than any spoken word: a tap, or a hotkey used as a modifier. The
# WAV is measured, not the key hold, because the hold includes mic start-up.
MIN_SPEECH_S = 0.3


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

# Adding a theme? Also update: widget.html (theme CSS + THEME_VALUES),
# style.css (mini-pill rule + keyframes), and app.js's dev-mock
# get_pill_themes. test_widget_html_covers_all_pill_themes guards widget.html.
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


_HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")


def sanitize_pill_color(value) -> str | None:
    """Whitelist a custom bar color from config before JS interpolation.
    Returns the hex string, or None (= use the theme's designed color)."""
    if isinstance(value, str) and _HEX_COLOR_RE.fullmatch(value):
        return value
    return None


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


# whisper.cpp truncates --prompt at ~224 tokens with no warning, so a large
# dictionary would silently lose its newest terms. Keep the most recently
# added ones (dict insertion order) and bound the text.
GLOSSARY_MAX_TERMS = 40
GLOSSARY_MAX_CHARS = 600


def glossary_prompt(dictionary) -> str | None:
    """Bias whisper toward the user's spellings: 'Glossary: A, B, C.'"""
    if not dictionary:
        return None
    seen = []
    for written in reversed(list(dictionary.values())):
        if written not in seen:
            seen.append(written)
        if len(seen) >= GLOSSARY_MAX_TERMS:
            break
    seen.reverse()
    while seen and len("Glossary: " + ", ".join(seen) + ".") > GLOSSARY_MAX_CHARS:
        seen.pop(0)
    return ("Glossary: " + ", ".join(seen) + ".") if seen else None


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

    # 6 px of headroom above and below the 44 px pill for its entrance rise;
    # the margin keeps the pill's centre where it has always been
    WIDTH, HEIGHT = 232, 56
    BOTTOM_MARGIN = 70
    EXIT_S = 0.18  # the pill's 160 ms exit (widget.html), plus a frame

    # AppKit constants (stable ABI values)
    _STYLE_NONACTIVATING = 1 << 7
    _CB_CAN_JOIN_ALL_SPACES = 1 << 0
    _CB_STATIONARY = 1 << 4
    _CB_FULLSCREEN_AUX = 1 << 8
    _SCREEN_SAVER_LEVEL = 1000
    _supports_ownership_action = True

    LEVEL_HZ = 30  # pill bars follow the voice; the page smooths between pushes

    def __init__(self, level_source=None):
        self._panel = None
        self._webview = None
        # () -> float | None; polled only while the pill shows "recording"
        self._level_source = level_source
        self._feed_gen = 0  # main thread only; bumping it ends the running feed
        self._vis_gen = 0   # main thread only; a show cancels a pending panel fade

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
            panel.setIgnoresMouseEvents_(True)  # invisible resident overlay: click-through
            panel.setAlphaValue_(0.0)           # resident but faded out until a dictation

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
            # Become resident on ALL Spaces immediately (invisible via alpha 0).
            # A later show then never has to (re)join another app's fullscreen
            # Space — orderOut/orderFront churn was intermittently leaving the
            # window on the wrong Space (onActiveSpace=False → no pill).
            panel.orderFrontRegardless()
        except Exception as e:
            print(f"Voisr widget: build failed: {e}", flush=True)

    def _set(self, state: str, theme: str | None = None,
             color: str | None = None, is_current=None, if_current=None) -> None:
        try:
            from PyObjCTools import AppHelper

            def go():
                def apply():
                    if self._panel is None:
                        self._build()
                    if self._webview is None:
                        return
                    # first, so a PyObjC call failing below still ends the
                    # previous state's level feed (and any pending fade)
                    self._feed_gen += 1
                    self._vis_gen += 1
                    if theme is not None:
                        # sanitize_* whitelist both values — config.json content
                        # can never be interpolated into JS verbatim
                        self._webview.evaluateJavaScript_completionHandler_(
                            f'setTheme("{sanitize_pill_theme(theme)}")', None)
                        safe = sanitize_pill_color(color)
                        self._webview.evaluateJavaScript_completionHandler_(
                            f'setColor("{safe}")' if safe else "setColor(null)", None)
                    self._panel.setAlphaValue_(1.0)   # window stays resident; the pill animates in
                    self._webview.evaluateJavaScript_completionHandler_(
                        f'setState("{state}")', None)
                    self._panel.orderFrontRegardless()
                    if state == "recording":
                        self._check_visible()
                        if self._level_source is not None:
                            self._feed_tick(self._feed_gen)

                if if_current is not None:
                    if_current(apply)
                elif is_current is None or is_current():
                    apply()

            AppHelper.callAfter(go)
        except Exception:
            pass  # never break the record path

    def _fade_panel(self, gen: int) -> None:
        """Main thread. Fade out but stay resident on all Spaces — do NOT
        orderOut. orderOut drops the window's Space membership, and the next
        orderFront intermittently fails to rejoin a fullscreen Space
        (onActiveSpace=False → the pill never appears)."""
        if gen != self._vis_gen or self._panel is None:
            return
        self._panel.setAlphaValue_(0.0)

    @staticmethod
    def level_js(level) -> str | None:
        """The JS that hands one level to the page, or None for no reading.
        Only a real number is ever interpolated into the script."""
        if isinstance(level, bool) or not isinstance(level, (int, float)):
            return None
        if level != level:  # NaN
            return None
        level = min(1.0, max(0.0, float(level)))
        return f"window.setLevel && setLevel({level:.3f})"

    def _feed_tick(self, gen: int) -> None:
        """Push the current level, then reschedule; main thread only. Any
        later state change bumps _feed_gen, which ends this loop. A None
        reading ends it too: StreamRecorder always reports a float, so None
        means a recorder with no meter (ffmpeg) or a capture already over."""
        if gen != self._feed_gen or self._webview is None:
            return
        try:
            level = self._level_source()
        except Exception:
            level = float("nan")  # a failed read is transient: skip, keep polling
        if level is None:
            return
        js = self.level_js(level)
        if js is not None:
            self._webview.evaluateJavaScript_completionHandler_(js, None)
        try:
            from PyObjCTools import AppHelper
            AppHelper.callLater(1 / self.LEVEL_HZ, self._feed_tick, gen)
        except Exception:
            pass

    def _check_visible(self) -> None:
        """Log if the pill still failed to appear after a show. With the panel
        resident on all Spaces this should not happen; the line captures any
        recurrence (with the window state) instead of it being silent."""
        try:
            from PyObjCTools import AppHelper

            def check():
                p = self._panel
                if p is None:
                    return
                if not p.isOnActiveSpace() or not (int(p.occlusionState()) & 2):
                    print("Voisr widget: pill still not visible after show "
                          f"(occ={int(p.occlusionState())}, "
                          f"onActiveSpace={bool(p.isOnActiveSpace())})", flush=True)

            AppHelper.callLater(0.8, check)
        except Exception:
            pass

    def show_recording(self, theme: str = "classic", color: str | None = None,
                       is_current=None, if_current=None):
        self._set("recording", theme=theme, color=color, is_current=is_current,
                  if_current=if_current)

    def show_processing(self, is_current=None, if_current=None):
        self._set("processing", is_current=is_current, if_current=if_current)

    def show_done(self, is_current=None, if_current=None):
        self._set("done", is_current=is_current, if_current=if_current)

    def show_error(self, is_current=None, if_current=None):
        self._set("error", is_current=is_current, if_current=if_current)

    def hide(self, is_current=None, if_current=None):
        try:
            from PyObjCTools import AppHelper

            def go():
                def apply():
                    self._feed_gen += 1  # first: ends the level feed even if the fade fails
                    self._vis_gen += 1
                    # The pill animates out first; the panel fades only once
                    # that exit is over, and not at all if a new dictation
                    # has shown the pill again in the meantime. Scheduled
                    # before the JS call so a dead webview still fades.
                    AppHelper.callLater(self.EXIT_S, self._fade_panel, self._vis_gen)
                    if self._webview is not None:
                        self._webview.evaluateJavaScript_completionHandler_(
                            "window.hide && hide()", None)

                if if_current is not None:
                    if_current(apply)
                elif is_current is None or is_current():
                    apply()

            AppHelper.callAfter(go)
        except Exception:
            pass


def input_permissions_ok(mode: str = "listen") -> bool:
    """True when Voisr may install its key tap in this mode."""
    try:
        import Quartz
        if mode == "active":
            return bool(Quartz.CGPreflightPostEventAccess())
        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return True  # non-pyobjc environments (tests, other platforms)


def _quartz_listener_factory(handler, mode="listen"):
    """Create and start the global key tap (see voisr.keytap for why this
    replaces pynput's listener, which crashes the process via TIS)."""
    from voisr.keytap import QuartzKeyListener
    listener = QuartzKeyListener(handler, mode=mode)
    listener.start()
    return listener


class HotkeyService:
    """Global push-to-talk listener driving the shared Api record path."""

    PANE_REOPEN_S = 60  # a refused permission's pane opens at most this often

    def __init__(self, api, widget=None, hide_delay=1.2,
                 permissions_check=None, poll_interval=3.0,
                 listener_factory=None):
        self._api = api
        self._widget = widget
        self._hide_delay = hide_delay
        self.tap_mode = "listen"
        self._permissions_check = permissions_check or (
            lambda: input_permissions_ok(self.tap_mode))
        self._poll_interval = poll_interval
        self._listener_factory = listener_factory or _quartz_listener_factory
        self._pane_opener = permissions.open_pane
        self._clock = time.monotonic
        self._pane_opened_at = None
        self._listener = None
        self._watcher = None
        self._wake = threading.Event()  # set by stop() to end a sleeping poll
        self._stopped = True  # until start(); see running
        self._pressed = False
        self._latched = False  # pressed by a toggle key, not held
        self._rejected = False  # this press already reported it couldn't start
        # press() calls start_recording() with _state_lock released (it can
        # take seconds, and the pill's main-thread callbacks take the lock).
        # While it runs _pressed is already True, so auto-repeat stays out;
        # a release or chord in the gap is noted here and settled after.
        self._starting = False
        self._start_released = False
        self._start_cancelled = False
        self._key_name = None
        self._target_vk = None
        self._state_lock = threading.RLock()
        self._epoch = 0  # bumped per recording; stale _finish threads must not touch the widget
        # Key events are handed to this serial worker instead of being acted
        # on inside the Quartz tap callback: press() can wait on the previous
        # recorder's stop() (up to 5 s on the ffmpeg fallback), and a tap
        # whose callback stalls is disabled by macOS with every event in the
        # gap lost -- including the release that ends the dictation. One
        # worker keeps press/release ordering exactly as the keyboard sent it.
        self._ops = None
        self._ops_thread = None
        self.status = "not started"
        # active | waiting | not_effective | off -- see _refresh_status
        self.health = "off"

    _OPS_STOP = object()

    @property
    def running(self) -> bool:
        """False while stopped, e.g. for capture_hotkey's 8 s key capture,
        when status still reads "active on ..."."""
        return not self._stopped

    @property
    def holding(self) -> bool:
        """True while the key itself owns the current recording."""
        return self._pressed

    def _submit(self, fn) -> None:
        """Run fn on the serial worker; inline if the worker is not running
        (direct callers such as tests and the CLI)."""
        ops = self._ops
        if ops is None:
            fn()
            return
        ops.put(fn)

    def _start_worker(self) -> None:
        import queue as queue_module
        if self._ops is not None:
            return
        ops = queue_module.Queue()

        def run():
            while True:
                fn = ops.get()
                if fn is self._OPS_STOP:
                    return
                try:
                    fn()
                except Exception:
                    pass  # a failed op must never kill the worker

        self._ops = ops
        self._ops_thread = threading.Thread(target=run, name="voisr-hotkey-ops",
                                            daemon=True)
        self._ops_thread.start()

    def _stop_worker(self) -> None:
        ops, self._ops = self._ops, None
        if ops is not None:
            ops.put(self._OPS_STOP)

    def _widget_enabled(self) -> bool:
        return self._widget is not None and bool(self._api.get_config().get("show_widget", True))

    def _run_widget_action(self, epoch: int, action) -> None:
        with self._state_lock:
            if epoch == self._epoch:
                action()

    def _widget_action(self, name: str, epoch: int, *args, **kwargs) -> None:
        method = getattr(self._widget, name)
        if getattr(self._widget, "_supports_ownership_action", False):
            method(*args, **kwargs,
                   if_current=lambda action: self._run_widget_action(epoch, action))
        else:
            method(*args, **kwargs)

    def press(self, latched=False) -> None:
        with self._state_lock:
            if self._pressed:
                return
            self._pressed = True
            self._latched = latched
            self._starting = True
            self._start_released = False
            self._start_cancelled = False
        try:
            result = self._api.start_recording()
        except Exception as error:
            print(f"Voisr hotkey: recording start failed: {error}", flush=True)
            result = {"error": str(error)}
        except BaseException:
            # nothing will settle this press: un-mark it or the key is dead
            with self._state_lock:
                self._starting = False
                self._pressed = False
                self._latched = False
            raise
        finish = None
        pane = None
        refused = False
        with self._state_lock:
            self._starting = False
            released, cancelled = self._start_released, self._start_cancelled
            if "error" in result:
                self._pressed = False
                self._latched = False
                # e.g. the dock owns the mic, or the queue is full. Say so
                # once per press; a held key's auto-repeat lands here too.
                if not self._rejected:
                    self._flash_error()
                # a key already let go needs no guard against its repeats
                self._rejected = not released
                pane = self._claim_pane_open(result.get("permission"))
                refused = True
            else:
                self._rejected = False
                self._epoch += 1
                epoch = self._epoch
                if cancelled:
                    # still hide: the new epoch orphans an older pill's own hide
                    self._drop_chord(epoch)
                else:
                    cfg = self._api.get_config()
                    if self._widget is not None and bool(cfg.get("show_widget", True)):
                        self._widget_action(
                            "show_recording", epoch,
                            theme=sanitize_pill_theme(cfg.get("pill_theme")),
                            color=sanitize_pill_color(cfg.get("pill_color")))
                    if released:
                        finish = self._end_recording()
        if refused:
            # outside _state_lock: the opener dispatches to AppKit
            if pane is not None:
                try:
                    self._pane_opener(pane)
                except Exception:
                    pass
            return
        if cancelled:
            self._api._cancel_recording()
        if finish is not None:
            self._spawn_finish(*finish)

    def _claim_pane_open(self, key):
        """Open a refused permission's pane at most once a minute; caller
        holds _state_lock."""
        if not key:
            return None
        now = self._clock()
        if (self._pane_opened_at is not None
                and now - self._pane_opened_at < self.PANE_REOPEN_S):
            return None
        self._pane_opened_at = now
        return key

    def _flash_error(self) -> None:
        """Error pill, then hide; caller holds _state_lock."""
        if not self._widget_enabled():
            return
        self._epoch += 1
        epoch = self._epoch
        self._widget_action("show_error", epoch)

        def hide_later():
            if self._hide_delay:
                time.sleep(self._hide_delay)
            with self._state_lock:
                if epoch == self._epoch:
                    self._widget_action("hide", epoch)

        threading.Thread(target=hide_later, daemon=True).start()

    def release(self) -> None:
        with self._state_lock:
            if not self._pressed:
                self._rejected = False
                return
            if self._starting:
                self._start_released = True  # press() ends it once started
                return
            finish = self._end_recording()
        self._spawn_finish(*finish)

    def _end_recording(self):
        """Close the mic and show processing; caller holds _state_lock and
        passes the result to _spawn_finish after letting go of it."""
        self._pressed = False
        epoch = self._epoch
        show = self._widget_enabled()
        if show:
            self._widget_action("show_processing", epoch)
        future = self._api._release_recording(paste=True)
        return future, show, epoch

    def _spawn_finish(self, future, show: bool, epoch: int) -> None:
        threading.Thread(
            target=self._finish,
            args=(future, show, epoch),
            name=f"voisr-widget-{epoch}",
            daemon=True,
        ).start()

    def _finish(self, future, show: bool, epoch: int) -> None:
        try:
            result = future.result().to_api_dict()
        except Exception as error:
            result = {"error": f"processing failed: {error}"[:250]}
        try:
            with self._state_lock:
                if not show or epoch != self._epoch:
                    return
                if "error" in result:
                    self._widget_action("show_error", epoch)
                else:
                    self._widget_action("show_done", epoch)
            if self._hide_delay:
                time.sleep(self._hide_delay)
            with self._state_lock:
                if epoch == self._epoch:
                    self._widget_action("hide", epoch)
        except Exception:
            pass

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
        mode = self._api.get_config().get("event_tap", "listen")
        # a hand-edited config must not leave the service with no listener
        self.tap_mode = mode if mode in TAP_MODES else "listen"
        request_permissions(self.tap_mode)
        self._start_worker()
        self._spawn_listener()
        self._refresh_status()
        self._watch()

    def _toggle(self) -> None:
        """flagsChanged-only keys (Fn) report both edges identically."""
        if self._pressed:
            self.release()
        else:
            # every toggle is a press of its own, and a refused one gets no
            # release() to clear the once-per-press guard
            self._rejected = False
            self.press(latched=True)

    def cancel_chord(self) -> None:
        """Another key went down while the hotkey was held: it's a shortcut
        (⌥e for é with Right Option), not a dictation. A latched toggle key
        isn't held, so typing during it is left alone."""
        with self._state_lock:
            if not self._pressed or self._latched:
                return
            if self._starting:
                # once let go, a key going down is typing, not a chord
                if not self._start_released:
                    self._start_cancelled = True
                return
            self._drop_chord(self._epoch)
        self._api._cancel_recording()

    def _drop_chord(self, epoch: int) -> None:
        """Un-press and hide; caller holds _state_lock, then cancels the
        recording after letting go of it: closing a mic can block, and the
        pill's main-thread callbacks take the lock."""
        self._pressed = False
        if self._widget_enabled():
            self._widget_action("hide", epoch)

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
                        self._submit(self.cancel_chord)
                    return
                # Everything below is queued, never run here (see _submit).
                if kind == "down":       # auto-repeats; press() guards
                    self._submit(self.press)
                elif kind == "up":
                    self._submit(self.release)
                else:                    # "flags": modifier edges (Fn, ⌥, ⌘...)
                    mask = _VK_FLAG_MASKS.get(vk)
                    if mask is not None:
                        # the flag bit carries the key's true state — immune
                        # to the desync a missed edge causes with toggling
                        if flags & mask:
                            self._submit(self.press)
                        else:
                            self._submit(self.release)
                    else:                # e.g. Caps Lock: flag ≠ key state
                        self._submit(self._toggle)   # modifiers never auto-repeat
            except Exception:
                pass

        old, self._listener = self._listener, None
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        try:
            self._listener = self._listener_factory(handler, self.tap_mode)
        except Exception as e:
            print(f"Voisr hotkey: listener start failed: {e}", flush=True)

    def _tap_state(self, granted: bool) -> str:
        """What macOS actually gave us: "running", "starting" (not decided
        yet) or "failed". A listener without wait_started is a test double
        (a bare handler, not a tap): it counts as running only if the
        permission check passed."""
        listener = self._listener
        if listener is None:
            return "failed"
        wait = getattr(listener, "wait_started", None)
        if wait is None:
            return "running" if granted else "failed"
        return wait(0.5)

    def _refresh_status(self) -> None:
        # May block up to 0.5 s in wait_started: only start() and the poll
        # thread call this, never the tap callback.
        pane = permissions.PANES[permissions.hotkey_key(self.tap_mode)][0]
        before = self.status
        granted = self._permissions_check()
        tap = self._tap_state(granted)
        if self._stopped:
            return  # stop() already ran: health stays "off"
        if tap == "running":
            self.health = "active"
            self.status = f"active on {self._key_name}"
        elif tap == "starting":
            # a slow tap is undecided, not broken: no reset advice yet
            self.health = "starting"
            self.status = f"starting push-to-talk on {self._key_name}…"
        elif granted:
            # macOS says yes, the tap says no: a stale TCC entry
            self.health = "not_effective"
            self.status = (f"{pane} is switched on for Voisr but macOS isn't "
                           "honouring it — reset it from Home")
        else:
            self.health = "waiting"
            panes = ("BOTH Input Monitoring and Accessibility"
                     if self.tap_mode == "listen" else "Accessibility")
            self.status = ("waiting for permission — open System Settings → "
                           f"Privacy & Security and switch Voisr ON under {panes}")
        if self.status != before:  # the poll re-derives this every interval
            print(f"Voisr hotkey: {self.status}", flush=True)

    def _watch(self) -> None:
        if self._watcher is not None:
            return
        self._wake.clear()

        def poll():
            me = threading.current_thread()
            while (not self._stopped and self._watcher is me
                   and self.health != "active"):
                # wait, not sleep: stop() wakes this immediately
                if self._wake.wait(self._poll_interval):
                    return
                if self._stopped or self._watcher is not me:
                    return
                listener = self._listener
                state = getattr(listener, "state", None)
                if state not in ("starting", "running") \
                        and self._permissions_check():
                    # granted (or re-granted after a reset): try for a LIVE tap
                    self._spawn_listener()
                # otherwise the tap is coming up (or just came up) and
                # respawning would stop a live tap, or nothing is granted:
                # either way just re-derive health from the tap's state
                self._refresh_status()

        self._watcher = threading.Thread(target=poll, daemon=True)
        self._watcher.start()

    def stop(self) -> None:
        self._stopped = True
        self._stop_worker()
        # Wake and join the poll so a following start() never races a
        # watcher that is still winding down (it would see _watcher set and
        # spawn nothing, leaving a later permission grant unnoticed).
        watcher, self._watcher = self._watcher, None
        if watcher is not None and watcher is not threading.current_thread():
            self._wake.set()
            watcher.join(timeout=2.0)
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass  # a dead listener's run loop teardown must not crash the app
        # last, so a poll that was mid-refresh can't leave a stale health
        self.health = "off"

    def restart(self, key_name=None) -> None:
        self.stop()
        self.start(key_name)


class Api:
    """What the dashboard talks to. Only BRIDGE_METHODS reach the page, via
    bridge_functions; never pass this as js_api."""

    SPACE_JOIN_WINDOW_S = 300  # only join dictations this close together

    def __init__(self, config_path=ROOT / "config.json",
                 history=None, recorder_factory=default_recorder,
                 transcribe_fn=None,
                 clock=time.monotonic, hotkey_service=None,
                 fallback_recorder_factory=Recorder,
                 capture_fn=capture_next_key,
                 paste_fn=paste_at_cursor,
                 clipboard_fn=set_clipboard,
                 mic_check=None):
        self._config_path = Path(config_path)
        self._history = history or HistoryStore(ROOT / "data" / "history.jsonl")
        self._transcribe_fn = transcribe_fn
        self._clock = clock
        self._hotkey_service = hotkey_service
        self._capture_fn = capture_fn
        self._paste_fn = paste_fn
        self._clipboard_fn = clipboard_fn
        # None = no check, so tests never probe the runner's real TCC state
        self._mic_check = mic_check
        self._last_paste_text = None
        self._last_paste_ts = None
        self._typing_generation = 0
        self._last_paste_typing_generation = 0
        self._paste_state_lock = threading.Lock()
        self._window = None
        self._status_item = None
        self._is_recording = False
        self._quitting = False  # set by close(): queued dictations still land
        self._sessions_in_flight = set()
        self._glyph_busy = False
        self._active_sequence = None
        # Reentrant: start_recording/_release_recording claim under it and
        # _mark_session takes it again to push the glyph.
        self._busy_lock = threading.RLock()
        self._save_panel_fn = None
        # Serialises every read-modify-write of config.json in this process
        # (set_config from pywebview workers, capture_hotkey, launch-at-login).
        self._config_lock = threading.RLock()
        self._config_cache = None  # (stat key, parsed config) or None
        self._config_notice = None  # set when a corrupt config was moved aside
        self._recordings = RecordingQueue(
            recorder_factory=recorder_factory,
            fallback_recorder_factory=fallback_recorder_factory,
            processor=self._process_session,
            clock=clock,
        )

    def _default_transcribe(self, wav, cfg):
        """Transcribe with the release-time model and dictionary prompt."""
        from voisr.transcriber import resolve_model, transcribe as _transcribe
        return _transcribe(wav, model_path=resolve_model(cfg.get("model")),
                           prompt=glossary_prompt(cfg.get("dictionary")))

    def _transcribe_audio(self, wav, cfg):
        if self._transcribe_fn is not None:
            return self._transcribe_fn(wav)
        return self._default_transcribe(wav, cfg)

    def current_level(self):
        """Live mic level of the dictation being captured (0..1), or None.
        Read by the pill's 30 Hz feed; deliberately not a bridge method."""
        return self._recordings.active_level()

    def get_level(self):
        """The dock's 30 Hz poll while it records: the live mic level
        (0..1), or None when nothing is metered. Bridge method."""
        return self.current_level()

    def note_user_typing(self):
        """Record manual typing without blocking the event-tap callback."""
        with self._paste_state_lock:
            self._typing_generation += 1

    def attach_hotkey_service(self, service):
        self._hotkey_service = service

    def attach_window(self, window):
        """Dashboard window handle, used to push live-refresh events to JS."""
        self._window = window

    def attach_status_item(self, status):
        self._status_item = status

    def is_recording(self) -> bool:
        """True while the microphone is open -- the mic, not the whole
        dictation. main()'s menu_dictate reads this to decide start-vs-stop,
        so it must go False the moment capture ends. The menu-bar glyph
        tracks the longer 'session in flight' state instead; see
        _mark_session."""
        return self._is_recording

    def _mark_session(self, sequence, in_flight: bool) -> None:
        """Track dictations that are still in flight and mirror the aggregate
        to the menu-bar glyph.

        A dictation is in flight from a successful start until its
        transcription completes -- releasing the key only closes the mic, and
        whisper is the slow part. Sessions are tracked by sequence rather than
        with a single flag because the queue admits a new dictation while an
        earlier one is still transcribing: retiring the old one must not clear
        the glyph out from under the new one, and a repeated retire (belt and
        braces on the failure paths) must be a no-op.

        Only pushes on a change, so the AppKit call is not repeated
        needlessly. Never lets a status-item failure break recording."""
        with self._busy_lock:
            if in_flight:
                self._sessions_in_flight.add(sequence)
            else:
                self._sessions_in_flight.discard(sequence)
            busy = bool(self._sessions_in_flight)
            status = self._status_item
            # Read the status item before committing _glyph_busy: committing
            # with nothing to push to would record a state that was never
            # rendered, and the next push would be skipped as "no change".
            if status is None or busy == self._glyph_busy:
                return
            self._glyph_busy = busy
            try:
                status.set_recording(busy)
            except Exception:
                pass

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
            self._update_config({"hotkey_vk": vk, "hotkey_label": label})
            return {"vk": vk, "label": label}
        finally:
            if svc is not None:
                svc.start()

    def _process_session(self, session: RecordingSession) -> SessionResult:
        """Queue processor. The dictation ends here -- not when the mic
        closed -- so this is where the session is retired from the in-flight
        set and the glyph is allowed to go idle. Retiring in the `finally`
        (rather than on the completion future) also keeps the ordering
        deterministic: the glyph is already idle by the time a caller
        blocking on stop_recording() gets its result."""
        try:
            return self._run_session(session)
        finally:
            self._mark_session(session.sequence, False)

    def _run_session(self, session: RecordingSession) -> SessionResult:
        wav = session.wav_path
        cfg = session.context
        duration = max((session.released_at - session.started_at), 0.1)
        # Some audio but too little is a tap, not a mic fault. No audio at
        # all still falls through to the permission message below.
        if 0 < wav_seconds(wav) < MIN_SPEECH_S:
            return SessionResult.failure(
                session.sequence, "audio",
                "too short — hold the key while you speak")
        if not wav.exists() or wav.stat().st_size < 1000:
            detail = session.capture_stderr.strip()[:120]
            return SessionResult.failure(
                session.sequence, "audio",
                f"recording failed (mic permission?) {detail}".rstrip())
        peak = wav_peak(wav)
        if 0 <= peak < 60:
            return SessionResult.failure(
                session.sequence, "audio",
                "no audio captured — allow Voisr to use the Microphone in "
                "System Settings → Privacy & Security, then try again")
        try:
            raw = self._transcribe_audio(wav, cfg)
            text = clean(raw, dictionary=cfg.get("dictionary", {}))
        except Exception as error:
            return SessionResult.failure(
                session.sequence, "transcription",
                f"transcription failed: {error}"[:250])
        if not text:
            return SessionResult.failure(
                session.sequence, "transcription",
                "nothing transcribed — try speaking a bit longer")

        item = None
        warning = None
        try:
            item = self._history.add(text, duration_s=duration)
            self._notify_dictation()
        except Exception as error:
            warning = f"history failed: {error}"[:250]

        pasted = False
        paste_error = None
        fallback_safe = True
        typing_generation = None
        if session.paste_requested and cfg.get("paste") and not self._quitting:
            with self._paste_state_lock:
                typing_generation = self._typing_generation
                join = (
                    self._last_paste_text
                    and typing_generation == self._last_paste_typing_generation
                    and self._clock() - self._last_paste_ts <= self.SPACE_JOIN_WINDOW_S
                    and not self._last_paste_text[-1].isspace()
                )
            try:
                pasted = self._paste_fn((" " if join else "") + text)
            except PasteDeliveryError as error:
                pasted = error.pasted
                fallback_safe = error.fallback_safe
                paste_error = error
            except Exception as error:
                paste_error = error
            if pasted:
                with self._paste_state_lock:
                    self._last_paste_text = text
                    self._last_paste_ts = self._clock()
                    self._last_paste_typing_generation = typing_generation

        copied = pasted
        clipboard_error = None
        if not pasted and fallback_safe and not self._quitting:
            try:
                self._clipboard_fn(text)
                copied = True
            except Exception as error:
                clipboard_error = error

        fields = {"text": text, "copied": copied, "pasted": pasted}
        if item is not None:
            fields["item"] = item
        if warning is not None:
            fields["warning"] = warning
        if isinstance(paste_error, PasteDeliveryError):
            return SessionResult.failure(
                session.sequence, "delivery",
                f"delivery failed: {paste_error}"[:250], **fields)
        if clipboard_error is not None:
            detail = clipboard_error if paste_error is None else (
                f"{paste_error}; clipboard fallback failed: {clipboard_error}")
            return SessionResult.failure(
                session.sequence, "delivery",
                f"delivery failed: {detail}"[:250], **fields)
        return SessionResult.success(session.sequence, **fields)

    def start_recording(self):
        if self._mic_check is not None:
            try:
                mic = self._mic_check()
            except Exception:
                mic = None  # unknown never blocks
            if mic is False:
                return {"error": "Microphone is switched off for Voisr",
                        "permission": "microphone"}
        try:
            session = self._recordings.start()
        except RecordingQueueError as error:
            return {"error": str(error)}  # rejected: marks neither flag
        # Publishing the sequence and lighting the glyph is one unit, under
        # the lock _release_recording claims through, so a release can never
        # land between them and leave the glyph lit for a retired session.
        # The queue call stays *outside*: start() waits on the previous
        # capture's finalizer, and holding _busy_lock across that would let a
        # wedged recorder.stop() block every glyph push in the process.
        with self._busy_lock:
            self._active_sequence = session.sequence
            self._is_recording = True
            self._mark_session(session.sequence, True)  # reentrant: RLock
        return {"recording": True, "session_id": session.sequence}

    def _release_recording(self, paste=False):
        """Close the mic. Deliberately does NOT clear the glyph: the dictation
        runs on until transcription finishes (see _process_session).

        Two threads really can be in here at once: HotkeyService.release()
        holds _state_lock, while Api.stop_recording() arrives on the
        pywebview/JS bridge holding nothing. Claiming _active_sequence is a
        read-modify-write (LOAD_ATTR ... STORE_ATTR, with a GIL switch point
        between), and the claim only means anything if it agrees with which
        caller RecordingQueue.release() hands the live session to -- so the
        two happen together, under _busy_lock.

        Split them and either half can disagree: both callers read the same
        sequence, or the caller that claimed it loses the queue race. Either
        way one of them holds a sequence but gets back the queue's
        *already-resolved* "not recording" failure, so the callback below
        fires immediately and retires a session that is still transcribing --
        a dark glyph mid-dictation, the exact defect this lifecycle fixed.
        Paired, the loser claims None and registers nothing.

        Lock ordering: _busy_lock is only ever taken before RecordingQueue's
        lock, never after -- the queue's worker calls _process_session (and so
        _mark_session) outside its own lock, and _finalize_capture never
        reaches back into Api. _busy_lock is an RLock because _mark_session
        takes it too; the retire below still runs outside the claim, so a
        status-item push is never made while the queue lock could be held."""
        claimed = None
        try:
            with self._busy_lock:
                sequence, self._active_sequence = self._active_sequence, None
                claimed = sequence
                self._is_recording = False
                future = self._recordings.release(
                    paste_requested=paste,
                    context=self.get_config(),
                )
                claimed = None  # handed off; the queue will retire it
        finally:
            # Nothing downstream will ever retire this session now. Runs
            # after the `with` unwinds, so the push is made outside the lock.
            if claimed is not None:
                self._mark_session(claimed, False)
        if sequence is not None:
            # Safety net for the paths that never reach _process_session --
            # a recorder whose stop() throws is completed straight from the
            # queue worker. Retiring twice is a no-op.
            future.add_done_callback(
                lambda _f, s=sequence: self._mark_session(s, False))
        return future

    def stop_recording(self, paste=False):
        return self._release_recording(paste).result().to_api_dict()

    def _cancel_recording(self) -> bool:
        """Close the mic and drop the capture. Private so the JS bridge
        can't reach it; only the hotkey path cancels."""
        with self._busy_lock:
            sequence, self._active_sequence = self._active_sequence, None
            self._is_recording = False
            cancelled = self._recordings.cancel()
        if sequence is not None:
            self._mark_session(sequence, False)
        return cancelled

    def close(self):
        # Quit still finishes queued transcriptions (they are the user's
        # words and go to History), but nothing may paste or touch the
        # clipboard after the user has left the app.
        self._quitting = True
        self._recordings.close()
        if getattr(self, "_status_item", None) is not None:
            self._status_item.release()

    def get_history(self):
        return self._history.list()

    def delete_history_item(self, item_id):
        self._history.delete(item_id)
        return {"ok": True}

    def get_stats(self):
        return self._history.stats()

    def _config_stat_key(self):
        try:
            st = self._config_path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def get_config(self):
        """Parsed config.json, served from memory while the file is unchanged
        (this runs on the tap thread for every press and release). The
        launch-at-login flag is read from the LaunchAgent plist itself, so a
        plist removed by hand cannot leave Settings claiming it is on."""
        import copy
        key = self._config_stat_key()
        with self._config_lock:
            cached = self._config_cache
            if cached is not None and cached[0] == key:
                cfg = copy.deepcopy(cached[1])
            else:
                cfg = load_config(self._config_path,
                                  on_corrupt=self._note_corrupt_config)
                self._config_cache = (key, copy.deepcopy(cfg))
        cfg["launch_at_login"] = launch_agent.is_enabled(path=launch_agent.PLIST_PATH)
        return cfg

    def _write_config_atomic(self, cfg: dict) -> None:
        """Temp file + fsync + os.replace, so a crash mid-write can never
        leave a truncated config.json (which load_config would silently
        replace with defaults, wiping the user's dictionary)."""
        import os
        import tempfile
        path = self._config_path
        payload = json.dumps(cfg, indent=2)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                   dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _update_config(self, updates: dict) -> dict:
        """Locked read-modify-write; returns the merged config."""
        with self._config_lock:
            cfg = self.get_config()
            if "hotkey" in updates and updates["hotkey"] != cfg.get("hotkey"):
                # the legacy name is now the source of truth; a stale captured
                # vk would otherwise keep winning in HotkeyService._resolve_target
                cfg.pop("hotkey_vk", None)
                cfg.pop("hotkey_label", None)
            cfg.update(updates)
            self._write_config_atomic(cfg)
            return cfg

    def _note_corrupt_config(self, saved):
        self._config_notice = (
            f"Your settings file couldn't be read, so Voisr started on "
            f"defaults. The old file is kept as {saved.name}.")

    def take_config_notice(self):
        """One-shot: the dashboard toasts this once at boot."""
        notice, self._config_notice = self._config_notice, None
        return {"notice": notice} if notice else {}

    def set_config(self, updates):
        # The bridge is the only way in from the dashboard: a wrong type here
        # would be saved and then break every dictation that reads it.
        errors = config_errors(updates)
        if errors:
            return {"error": "; ".join(errors)}
        with self._config_lock:
            old_cfg = self.get_config()
            old_hotkey = old_cfg.get("hotkey")
            old_tap = old_cfg.get("event_tap")
            cfg = self._update_config(updates)
        new_hotkey = cfg.get("hotkey")
        if self._hotkey_service and (
                new_hotkey != old_hotkey or cfg.get("event_tap") != old_tap):
            self._hotkey_service.restart(new_hotkey)
        return cfg

    def get_hotkey_options(self):
        return HOTKEY_OPTIONS

    def hotkey_display(self):
        """Human-readable label for the configured hotkey, resolved in
        priority order so a raw config identifier (e.g. "alt_r") never
        reaches the UI:
          1. config["hotkey_label"] — set once the user completes a
             "press any key" capture.
          2. the HOTKEY_OPTIONS label matching config["hotkey"] — covers
             a fresh install, which ships a known value with no label.
          3. the raw config["hotkey"] value, if it's something unrecognized.
          4. "unset".
        The hero, Settings, and the menu-bar tooltip all resolve through
        this single source so they never disagree."""
        cfg = self.get_config()
        label = cfg.get("hotkey_label")
        if label:
            return {"display": label}
        hotkey = cfg.get("hotkey")
        if hotkey:
            for opt in HOTKEY_OPTIONS:
                if opt["value"] == hotkey:
                    return {"display": opt["label"]}
            return {"display": hotkey}
        return {"display": "unset"}

    def get_pill_themes(self):
        return PILL_THEMES

    def get_hotkey_status(self):
        """status is the sentence Settings shows; state is what Home's dot
        shows: "listening", "busy" (another recording owns the mic, so press()
        drops the key) or "off"."""
        svc = self._hotkey_service
        if svc is None:
            return {"status": "push-to-talk not running", "state": "off",
                    "health": "off"}
        status = svc.status
        if not (svc.running and status.startswith("active")):
            state = "off"
        elif self._is_recording and not svc.holding:
            state = "busy"
        else:
            state = "listening"
        if status.startswith("active") and self.get_config().get("paste"):
            status += " · paste ready" if paste_ready() \
                else " · grant Accessibility to auto-paste"
        return {"status": status, "state": state,
                "health": getattr(svc, "health", "off")}

    def get_permissions(self):
        mode = self.get_config().get("event_tap", "listen")
        panes = permissions.states(mode=mode)
        svc = self._hotkey_service
        if (svc is not None and getattr(svc, "running", False)
                and getattr(svc, "health", None) == "not_effective"):
            for pane in panes:
                if pane["hotkey"]:
                    pane["effective"] = False
        return {"panes": panes}

    def open_settings_pane(self, key):
        return {"ok": permissions.open_pane(key)}

    def reset_permission(self, key):
        """Bridge: reset Voisr's own grant, then open the pane to re-grant.
        The hotkey watcher notices the new grant and respawns the tap."""
        result = permissions.reset_grant(key)
        if result.get("ok"):
            permissions.open_pane(key)
        return result

    def relaunch_app(self):
        """Bridge: reopen Voisr.app a second after this process quits."""
        try:
            import subprocess
            import Foundation
            path = str(Foundation.NSBundle.mainBundle().bundlePath())
        except Exception:
            return {"ok": False, "error": "not running as an app"}
        # Python.app (from `python -m voisr.ui`) also ends in ".app"; only
        # relaunch the installed Voisr bundle.
        if (not path.endswith(".app") or self._window is None
                or permissions.own_bundle_id() != permissions.OWN_BUNDLE_ID):
            return {"ok": False, "error": "not running as an app"}
        subprocess.Popen(["/bin/sh", "-c", 'sleep 1; /usr/bin/open "$0"', path],
                         start_new_session=True)
        try:
            self._window.destroy()  # same exit path as the menu-bar Quit
        except Exception:
            return {"ok": False, "error": "couldn't quit"}
        return {"ok": True}

    def restore_history_item(self, item):
        try:
            self._history.restore(item)
        except Exception as e:
            return {"error": str(e)}
        return {"ok": True}

    def clear_history(self):
        try:
            removed = self._history.clear()
        except Exception as e:
            return {"error": str(e)}
        return {"ok": True, "removed": removed}

    def get_models(self):
        from voisr.transcriber import MODELS_DIR, PREFERRED_MODELS
        labels = {"small.en": "Small — best accuracy",
                  "base.en": "Base — fastest"}
        models = []
        for name in PREFERRED_MODELS:
            path = MODELS_DIR / f"ggml-{name}.bin"
            installed = path.exists()
            models.append({"value": name, "label": labels.get(name, name),
                           "installed": installed,
                           "bytes": path.stat().st_size if installed else 0})
        return {"models": models, "active": self.get_config().get("model")}

    def set_launch_at_login(self, enabled):
        try:
            if enabled:
                launch_agent.enable(path=launch_agent.PLIST_PATH)
            else:
                launch_agent.disable(path=launch_agent.PLIST_PATH)
        except Exception as e:
            return {"error": str(e)}
        self.set_config({"launch_at_login": bool(enabled)})
        return {"ok": True, "enabled": bool(enabled)}

    APPEARANCES = ("system", "light", "dark")

    def set_appearance(self, mode):
        if mode not in self.APPEARANCES:
            return {"error": f"unknown appearance: {mode}"}
        self.set_config({"appearance": mode})
        apply_app_appearance(mode)
        return {"ok": True, "appearance": mode}

    def export_history(self, fmt):
        import datetime
        items = self._history.list()
        stamp = datetime.date.today().isoformat()
        if fmt == "json":
            text = json.dumps(items, indent=2)
        elif fmt == "md":
            def when(ts):
                # history keeps records whose ts can't be a date (e.g.
                # milliseconds); one of those must not sink the export
                try:
                    return f"{datetime.datetime.fromtimestamp(ts):%Y-%m-%d %H:%M}"
                except (OverflowError, OSError, ValueError):
                    return "unknown time"
            text = "\n".join(f"- **{when(i['ts'])}** — {i['text']}" for i in items)
        elif fmt == "txt":
            text = "\n".join(i["text"] for i in items)
        else:
            return {"error": f"unsupported format: {fmt}"}
        return {"text": text, "suggested_name": f"voisr-history-{stamp}.{fmt}"}

    def save_text_file(self, text, suggested_name):
        """Write text to a user-chosen path via NSSavePanel. Returns
        ok=False with path=None when the user cancels."""
        chooser = self._save_panel_fn or _run_save_panel
        try:
            path = chooser(suggested_name)
        except Exception as e:
            return {"error": str(e)}
        if not path:
            return {"ok": False, "path": None}
        try:
            Path(path).write_text(text)
        except OSError as e:
            return {"error": str(e)}
        return {"ok": True, "path": str(path)}

    def prime_microphone(self):
        """Retained for interface parity — intentionally does NOT open the
        mic. The stream is opened only while recording so the macOS "mic in
        use" indicator is off when idle; the permission prompt appears on
        the first real dictation."""
        return {"ok": True}

    def copy_text(self, text):
        set_clipboard(text)
        return {"ok": True}


_NS_LEFT_MOUSE_DOWN, _NS_LEFT_MOUSE_UP, _NS_KEY_DOWN = 1, 2, 10
_NS_COMMAND_FLAG = 1 << 20
_CLOSE_EVENT_FRESH_S = 1.0


def close_hides_window(event, ns_window, now) -> bool:
    """Should this close only hide the dashboard?

    pywebview fires the same `closing` event for the red button and for
    applicationShouldTerminate_ (app-menu Quit, Dock Quit, logout), so the
    event that triggered it is the only way to tell them apart. A click in
    this window or ⌘W hides; anything else, including no event at all, is a
    quit and must be allowed — refusing it would block logout.

    currentEvent is the last event the app *took*, so a quit arriving as an
    Apple Event can still see an old click. `now` (system uptime, the clock
    NSEvent timestamps use) rules out anything that isn't this moment's."""
    try:
        if event is None or now - event.timestamp() > _CLOSE_EVENT_FRESH_S:
            return False
        kind = event.type()
        if kind in (_NS_LEFT_MOUSE_DOWN, _NS_LEFT_MOUSE_UP):
            # == is isEqual: on an NSWindow; the proxies needn't be identical
            return ns_window is not None and event.window() == ns_window
        if kind == _NS_KEY_DOWN:
            return (event.charactersIgnoringModifiers() == "w"
                    and bool(event.modifierFlags() & _NS_COMMAND_FLAG))
    except Exception:
        pass
    return False


def install_dock_reopen(on_reopen, delegate_cls=None) -> bool:
    """Make a Dock click call on_reopen. The red button only hides the
    dashboard, and pywebview's AppDelegate lacks
    applicationShouldHandleReopen:hasVisibleWindows:, so without this the Dock
    icon is dead. Added to pywebview's class (it builds the instance itself in
    webview.start). Returns False if that isn't possible."""
    try:
        import objc
        if delegate_cls is None:
            from webview.platforms.cocoa import BrowserView
            delegate_cls = BrowserView.AppDelegate

        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
            # Show whatever the flag says: the recording pill counts as a
            # visible window while the dashboard is hidden.
            try:
                on_reopen()
            except Exception:
                pass
            return True

        objc.classAddMethods(delegate_cls, [objc.selector(
            applicationShouldHandleReopen_hasVisibleWindows_,
            selector=b"applicationShouldHandleReopen:hasVisibleWindows:",
            signature=b"Z@:@Z")])
        return True
    except Exception:
        return False


# Every Api method the dashboard calls, and nothing else.
# tests/test_js_bridge_surface.py pins this to the V.api() calls in voisr/web/js.
BRIDGE_METHODS = (
    "capture_hotkey", "clear_history", "copy_text", "delete_history_item",
    "export_history", "get_config", "get_history", "get_hotkey_status",
    "get_level", "get_models", "get_permissions", "get_pill_themes", "get_stats",
    "hotkey_display", "open_settings_pane", "restore_history_item",
    "save_text_file", "set_appearance", "set_config", "set_launch_at_login",
    "start_recording", "stop_recording", "take_config_notice",
)


def bridge_functions(api):
    """The dashboard's bridge: one plain function per BRIDGE_METHODS name,
    for window.expose(). Passing the Api as js_api instead exposes it whole:
    pywebview resolves a call's dotted name with getattr and no underscore
    filter, so page script could reach "_history.path.unlink" or a method's
    __func__.__globals__. Exposed functions are looked up by exact name."""
    def forward(name):
        def call(*args):
            return getattr(api, name)(*args)
        call.__name__ = call.__qualname__ = name
        return call
    return [forward(name) for name in BRIDGE_METHODS]


def start_hotkey_service(service):
    """Start push-to-talk without letting a failure abort the rest of startup:
    on_started schedules the menu-bar item and window chrome after this, and
    an app with no hotkey can still be fixed from its own Settings. Returns
    the error text, or None."""
    try:
        service.start()
    except Exception as e:
        service.status = f"push-to-talk not running: {e}"[:200]
        print(f"Voisr hotkey: {service.status}", flush=True)
        return str(e)
    return None


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
    api = None
    service = None
    try:
        api = Api(mic_check=permissions.microphone_granted)
        widget = RecordingWidget(level_source=api.current_level)
        service = HotkeyService(api, widget=widget)
        api.attach_hotkey_service(service)
        # No js_api: see bridge_functions
        window = webview.create_window("Voisr", str(WEB_DIR / "index.html"),
                                       width=1060, height=700,
                                       min_size=(900, 620))
        window.expose(*bridge_functions(api))
        api.attach_window(window)
        widget.create()

        def on_closing():
            # Closing the last pywebview window ends webview.start(), and the
            # finally below then stops push-to-talk. The menu-bar item is the
            # way back in, so the red button only hides. Returning False
            # cancels the close; anything else lets it (and a quit) proceed.
            try:
                import AppKit
                import Foundation
                # pywebview's `native` is the NSWindow itself
                if close_hides_window(
                        AppKit.NSApp.currentEvent(),
                        getattr(window, "native", None),
                        Foundation.NSProcessInfo.processInfo().systemUptime()):
                    window.hide()
                    return False
            except Exception:
                pass
            return True

        try:
            window.events.closing += on_closing
        except Exception as e:  # startup must not die over this
            print(f"Voisr: hide-on-close unavailable ({e}); closing the "
                  "window will quit", flush=True)

        def menu_dictate():
            # Routed through HotkeyService (not Api directly): an NSMenuItem
            # action runs on the AppKit main thread, and api.stop_recording()
            # blocks on the worker future for the whole transcription --
            # that would freeze the window, the menu bar, and every queued
            # AppHelper.callAfter (including the pill's own state updates)
            # for the duration. HotkeyService.release() hands the blocking
            # wait off to its own worker thread instead. It's also the only
            # path that drives the widget, so a menu-started dictation shows
            # the pill instead of leaving just the menu-bar glyph -- the
            # spec requires the two to never disagree. service._toggle() is
            # the same press()/release() pair used for the Fn/Globe-style
            # toggle keys, which is exactly this click-to-start,
            # click-to-stop shape. Submitted to the service's worker so the
            # AppKit main thread never waits on queue admission either.
            service._submit(service._toggle)

        def menu_open():
            # Called on the AppKit main thread (menu item, Dock click), and
            # window.show() waits up to 20 s for the window's `shown` event,
            # which only the main thread can set. Never wait there.
            def show():
                try:
                    window.show()
                except Exception:
                    pass
            threading.Thread(target=show, daemon=True).start()

        if not install_dock_reopen(menu_open):
            print("Voisr: Dock reopen unavailable; use the menu-bar Open",
                  flush=True)

        def menu_quit():
            try:
                window.destroy()
            except Exception:
                pass

        status = StatusItem(
            on_dictate=menu_dictate,
            on_open=menu_open,
            on_quit=menu_quit,
            hotkey_label_fn=lambda: api.hotkey_display()["display"])
        api.attach_status_item(status)

        def on_started():
            # Runs on a worker thread AFTER the GUI run loop is up, so the
            # Input Monitoring / Accessibility prompts can display and blocking
            # calls never freeze startup. The mic is NOT opened here — it opens
            # only during an actual dictation, so the "mic in use" indicator is
            # off while idle.
            start_hotkey_service(service)  # config-driven: prefers captured hotkey_vk

            from PyObjCTools import AppHelper

            def apply_native_chrome(native):
                # Chrome is AppKit and must be on the main thread. Cosmetic:
                # failures are logged, not fatal.
                chrome = apply_chrome(native, webview=_find_webview(native))
                if not chrome["titlebar"]:
                    print("Voisr chrome: titlebar setup failed", flush=True)
                if not chrome["vibrancy"]:
                    print("Voisr chrome: vibrancy unavailable, using flat "
                          "fallback", flush=True)
                    # No native vibrancy effect view -- tell base.css's
                    # :root[data-vibrancy="off"] fallback to paint a
                    # solid tint instead of leaving the sidebar assuming
                    # a translucency that was never applied. Fire-and-
                    # forget off-thread, same pattern as
                    # Api._notify_dictation: a slow or dead webview must
                    # never block startup.
                    def set_vibrancy_off():
                        try:
                            window.evaluate_js(
                                'document.documentElement.setAttribute('
                                '"data-vibrancy", "off")')
                        except Exception:
                            pass
                    threading.Thread(target=set_vibrancy_off,
                                     daemon=True).start()

            def native_setup():
                apply_app_appearance(api.get_config().get("appearance", "system"))
                native = getattr(window, "native", None)
                if native is not None:
                    # pywebview's Cocoa backend only makes the WKWebView the
                    # window's contentView once the initial page finishes
                    # loading (webView_didFinishNavigation_ calls
                    # window.setContentView_(webview)) -- calling
                    # apply_chrome() any earlier finds a bare placeholder
                    # NSView with no subviews, so _find_webview() always
                    # returns None and vibrancy silently never engages, even
                    # with a correct webview lookup. Defer to window.events
                    # .loaded, falling back to an immediate call if the page
                    # has (rarely) already finished by the time we get here.
                    try:
                        loaded = window.events.loaded
                        if loaded.is_set():
                            apply_native_chrome(native)
                        else:
                            loaded += lambda *a: AppHelper.callAfter(
                                apply_native_chrome, native)
                    except Exception:
                        apply_native_chrome(native)
                status.create()

            AppHelper.callAfter(native_setup)

        webview.start(on_started)
    finally:
        try:
            if service is not None:
                service.stop()
        finally:
            if api is not None:
                api.close()


if __name__ == "__main__":
    main()
