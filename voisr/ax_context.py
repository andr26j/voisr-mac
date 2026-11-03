"""What is focused and what it says, read through the Accessibility API.

Raw text from here lives in memory for one dictation and is never logged or
written to disk: FocusContext's repr leaves it out, and errors are reduced to a
reason word. The adapter can only read: it has no way to set an attribute on
another app, including the one that wakes up an Electron app's AX tree.

Nothing here runs on the hotkey path. Captures run on one AXWorker thread;
callers that wait do so through Future.result(timeout).
"""
import os
import queue as queue_module
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

try:
    import objc
    _pool = objc.autorelease_pool
except ImportError:  # not on macOS: the fake adapter still works
    from contextlib import nullcontext as _pool

DEFAULT_BUDGET_S = 0.15
EXCERPT_CHARS = 500
MAX_VALUE_CHARS = 15_000
NEARBY_MAX_NODES = 200
NEARBY_MAX_STRINGS = 10
NEARBY_MAX_CHARS = 2_000

SECURE_ROLES = frozenset({"AXSecureTextField"})
EDITABLE_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})
STATIC_TEXT_ROLES = frozenset({"AXStaticText", "AXHeading"})

# Never read, whatever the role says. A family of bundle ids ends in ".".
DENYLIST = (
    "com.apple.keychainaccess", "com.apple.Passwords",
    "com.apple.systempreferences", "com.apple.SystemPreferences",
    "com.apple.SecurityAgent", "com.apple.LocalAuthentication.UIAgent",
    "com.apple.authenticationui",
    "com.1password.", "com.agilebits.", "1password.",
    "com.bitwarden.", "com.lastpass.", "com.dashlane.", "org.keepassxc.",
    "org.keepass.", "com.keepassium.", "in.sinew.", "com.nordsec.nordpass",
    "me.proton.pass.", "com.roboform.", "com.keeper.", "com.callpod.",
    "com.okta.mobile", "com.authy.",
)

# Absence proves nothing here (see the spec's C): terminals collapse or wrap
# what they receive, and web editors rewrite it.
TERMINALS = frozenset({
    "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable",
    "net.kovidgoyal.kitty", "org.alacritty", "io.alacritty",
    "com.github.wez.wezterm", "co.zeit.hyper", "com.mitchellh.ghostty",
})
BROWSERS = frozenset({
    "com.apple.Safari", "com.apple.SafariTechnologyPreview",
    "com.google.Chrome", "com.google.Chrome.canary", "org.mozilla.firefox",
    "com.microsoft.edgemac", "com.brave.Browser", "company.thebrowser.Browser",
    "com.operasoftware.Opera", "com.vivaldi.Vivaldi", "org.chromium.Chromium",
})


class _Unreadable:
    """An attribute that could not be read. Not the same as an empty one."""

    def __repr__(self):
        return "UNREADABLE"

    def __bool__(self):
        return False


UNREADABLE = _Unreadable()


def denied(bundle_id) -> bool:
    if not bundle_id:
        return False
    return any(bundle_id.startswith(entry) if entry.endswith(".")
               else bundle_id == entry for entry in DENYLIST)


@dataclass(frozen=True)
class FocusContext:
    bundle_id: str | None = None
    app_name: str | None = None
    role: str | None = None
    element_id: str | None = None
    secure: bool = False
    readable: bool = False
    # why there is no text: no_focused_element, not_editable, unreadable,
    # too_long, secure, timeout, self
    reason: str | None = None
    caret_known: bool = False
    trust_absence: bool = True  # a missing paste means it really didn't land
    window_title: str = field(default="", repr=False)
    before_text: str = field(default="", repr=False)
    after_text: str = field(default="", repr=False)
    selected_text: str = field(default="", repr=False)
    nearby_texts: tuple[str, ...] = field(default=(), repr=False)
    value: str | None = field(default=None, repr=False)
    element: Any = field(default=None, repr=False, compare=False)


class AXAdapter(Protocol):
    """Read-only, and every read reports failure as UNREADABLE, not a raise."""

    def frontmost(self) -> tuple[str | None, str | None, int | None]:
        """(bundle id, name, pid) of the frontmost app."""

    def focused_element(self, timeout_s: float) -> Any:
        """The focused element, or UNREADABLE."""

    def attribute(self, element, name: str, timeout_s: float) -> Any:
        """A str, int, list, element, (location, length) tuple, or UNREADABLE."""

    def owner(self, element) -> tuple[str | None, str | None, int | None]:
        """(bundle id, name, pid) of the app the element belongs to."""

    def element_id(self, element) -> str:
        """Stable for one element while it lives."""

    def is_electron(self, pid) -> bool: ...


def _split_at_utf16(value: str, location: int, length: int) -> tuple[str, str, str]:
    """AX ranges count UTF-16 units, Python strings count code points: split
    with emoji before the caret must not land one character off per emoji."""
    if value.isascii():
        end = location + length
        return value[:location], value[location:end], value[end:]
    units = value.encode("utf-16-le", "surrogatepass")
    start, end = location * 2, (location + length) * 2

    def part(data):
        return data.decode("utf-16-le", "ignore")

    return part(units[:start]), part(units[start:end]), part(units[end:])


def _trim_before(text: str, limit: int = EXCERPT_CHARS) -> str:
    """The last `limit` chars, cut back to start on a whole word."""
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    for i, ch in enumerate(cut):
        if ch.isspace():
            return cut[i + 1:]
    return cut


def _trim_after(text: str, limit: int = EXCERPT_CHARS) -> str:
    """The first `limit` chars, cut back to end on a whole word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for i in range(len(cut) - 1, -1, -1):
        if cut[i].isspace():
            return cut[:i]
    return cut


def _is_secure(role, subrole) -> bool:
    return role in SECURE_ROLES or subrole in SECURE_ROLES


def trust_absence(adapter, bundle_id, pid) -> bool:
    if bundle_id in TERMINALS or bundle_id in BROWSERS:
        return False
    try:
        return not adapter.is_electron(pid)
    except Exception:
        return False


def read_textbox(adapter, element, remaining: Callable[[], float]) -> dict:
    """The field's text as FocusContext fields. Role comes first: until it is
    known that the field isn't secure, no text is asked for. `remaining` is
    the budget left in seconds; out of budget is a reason, never an empty
    string."""
    role = adapter.attribute(element, "AXRole", remaining())
    if role is UNREADABLE or not isinstance(role, str):
        return {"reason": "unreadable"}
    subrole = adapter.attribute(element, "AXSubrole", remaining())
    if _is_secure(role, subrole):
        return {"role": role, "secure": True, "reason": "secure"}
    if role not in EDITABLE_ROLES:
        return {"role": role, "reason": "not_editable"}

    selected = adapter.attribute(element, "AXSelectedText", remaining())
    selected = selected if isinstance(selected, str) else ""
    count = adapter.attribute(element, "AXNumberOfCharacters", remaining())
    if isinstance(count, int) and count > MAX_VALUE_CHARS:
        return {"role": role, "reason": "too_long", "selected_text": selected}
    if remaining() <= 0:
        return {"role": role, "reason": "timeout"}
    value = adapter.attribute(element, "AXValue", remaining())
    if value is UNREADABLE or not isinstance(value, str):
        reason = "timeout" if remaining() <= 0 else "unreadable"
        return {"role": role, "reason": reason}
    if len(value) > MAX_VALUE_CHARS:
        return {"role": role, "reason": "too_long", "selected_text": selected}
    fields = {"role": role, "readable": True, "value": value,
              "selected_text": selected}
    selection = adapter.attribute(element, "AXSelectedTextRange", remaining())
    if isinstance(selection, tuple) and len(selection) == 2:
        before, chosen, after = _split_at_utf16(value, *selection)
        fields.update(before_text=_trim_before(before),
                      after_text=_trim_after(after),
                      selected_text=chosen or selected, caret_known=True)
    return fields


def _nearby_texts(adapter, element, remaining) -> tuple[str, tuple[str, ...]]:
    """(window title, static text from the focused window), breadth first.
    Static text only: other fields aren't read, and a secure subtree is left
    alone entirely."""
    window = adapter.attribute(element, "AXWindow", remaining())
    if window is UNREADABLE:
        return "", ()
    title = adapter.attribute(window, "AXTitle", remaining())
    title = title if isinstance(title, str) else ""
    texts: list[str] = []
    total = 0
    queue = [window]
    seen_nodes = 0
    while (queue and seen_nodes < NEARBY_MAX_NODES and remaining() > 0
           and len(texts) < NEARBY_MAX_STRINGS and total < NEARBY_MAX_CHARS):
        node = queue.pop(0)
        seen_nodes += 1
        role = adapter.attribute(node, "AXRole", remaining())
        if role is UNREADABLE:
            continue
        subrole = adapter.attribute(node, "AXSubrole", remaining())
        if _is_secure(role, subrole):
            continue
        if role in STATIC_TEXT_ROLES:
            text = adapter.attribute(node, "AXValue", remaining())
            if isinstance(text, str) and text.strip():
                text = text.strip()[:NEARBY_MAX_CHARS - total]
                texts.append(text)
                total += len(text)
            continue
        children = adapter.attribute(node, "AXChildren", remaining())
        if isinstance(children, (list, tuple)):
            queue.extend(children[:NEARBY_MAX_NODES])
    return title, tuple(texts)


def capture_focus(adapter, budget_s: float = DEFAULT_BUDGET_S, *,
                  textbox_only: bool = False,
                  clock: Callable[[], float] = time.monotonic) -> FocusContext:
    """Snapshot the focused field. Never raises."""
    try:
        return _capture(adapter, budget_s, textbox_only, clock)
    except Exception:
        return FocusContext(reason="unreadable")


def _capture(adapter, budget_s, textbox_only, clock) -> FocusContext:
    deadline = clock() + budget_s

    def remaining():
        return deadline - clock()

    bundle_id, app_name, pid = adapter.frontmost()
    if denied(bundle_id):
        return FocusContext(bundle_id=bundle_id, secure=True, reason="secure")
    if pid == os.getpid():
        # AX to yourself waits on your own main thread
        return FocusContext(bundle_id=bundle_id, app_name=app_name, reason="self")
    element = adapter.focused_element(remaining())
    if element is UNREADABLE or element is None:
        reason = "timeout" if remaining() <= 0 else "no_focused_element"
        return FocusContext(bundle_id=bundle_id, app_name=app_name, reason=reason)
    # A password manager's quick-access panel takes focus without becoming the
    # frontmost app, so the frontmost check alone isn't enough.
    owner_id, owner_name, owner_pid = adapter.owner(element)
    if denied(owner_id):
        return FocusContext(bundle_id=owner_id, secure=True, reason="secure")
    if owner_pid == os.getpid():
        return FocusContext(bundle_id=owner_id, app_name=owner_name, reason="self")
    bundle_id = owner_id or bundle_id
    app_name = owner_name or app_name
    fields = read_textbox(adapter, element, remaining)
    if fields.get("secure"):
        return FocusContext(bundle_id=bundle_id, app_name=app_name,
                            role=fields.get("role"), secure=True, reason="secure")
    title, nearby = "", ()
    if not textbox_only and remaining() > 0:
        title, nearby = _nearby_texts(adapter, element, remaining)
    return FocusContext(
        bundle_id=bundle_id, app_name=app_name,
        element_id=adapter.element_id(element), element=element,
        window_title=title, nearby_texts=nearby,
        trust_absence=trust_absence(adapter, bundle_id, owner_pid or pid),
        **fields)


class AXUnavailable(RuntimeError):
    pass


class AXWorker:
    """One thread for AX work, one job at a time. The messaging timeout should
    end every call, but a call that doesn't return anyway is abandoned after
    WEDGED_S and a new thread takes over the queue. A second wedged thread
    means something is badly wrong: jobs are refused until one comes back, so
    wedged threads can't pile up."""

    WEDGED_S = 2.0
    MAX_ABANDONED = 2

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._queue = None
        self._thread = None
        self._running_since = None
        self._abandoned: set[threading.Thread] = set()
        self._closed = False

    def submit(self, fn) -> Future:
        """Never blocks. The Future fails with AXUnavailable when refused."""
        future = Future()
        with self._lock:
            if self._closed or not self._ready_locked():
                future.set_exception(AXUnavailable("AX worker unavailable"))
                return future
            self._queue.put((fn, future))
        return future

    def _ready_locked(self) -> bool:
        self._abandoned = {t for t in self._abandoned if t.is_alive()}
        if (self._thread is not None and self._running_since is not None
                and self._clock() - self._running_since > self.WEDGED_S):
            self._abandoned.add(self._thread)
            self._thread = None
        if self._thread is None:
            if len(self._abandoned) >= self.MAX_ABANDONED:
                return False
            if self._queue is None:
                self._queue = queue_module.Queue()
            self._running_since = None
            self._thread = threading.Thread(
                target=self._run, args=(self._queue,), name="voisr-ax", daemon=True)
            self._thread.start()
        return True

    def _run(self, jobs) -> None:
        me = threading.current_thread()
        while True:
            job = jobs.get()
            if job is None:
                return
            fn, future = job
            with self._lock:
                if self._thread is me:
                    self._running_since = self._clock()
            if future.set_running_or_notify_cancel():
                try:
                    with _pool():
                        result = fn()
                except BaseException as error:
                    future.set_exception(error)
                else:
                    future.set_result(result)
            with self._lock:
                if self._thread is not me:
                    return  # replaced while wedged; the new thread has the queue
                self._running_since = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._queue is not None:
                self._queue.put(None)


@dataclass
class SessionFocus:
    """The captures of one dictation. Their Futures are checked, not waited
    for, so a slow app costs a dictation its context and nothing else."""
    press: Future | None = None
    release: Future | None = None
    press_ms: float = 0.0
    release_ms: float = 0.0

    @staticmethod
    def _finished(future) -> FocusContext | None:
        if future is None or not future.done() or future.cancelled():
            return None
        try:
            return future.result(timeout=0)
        except Exception:
            return None

    def press_now(self) -> FocusContext | None:
        return self._finished(self.press)

    def release_now(self) -> FocusContext | None:
        return self._finished(self.release)

    def for_terms(self) -> FocusContext | None:
        """The capture to pick terms from. Any secure capture means none: the
        user was in a password field while speaking. Else the first whose field
        was readable, and failing that the press capture, whose window title
        and nearby labels may still be good. A failed capture is a truthy
        object, so `press or release` would never reach the good one."""
        captures = [c for c in (self.press_now(), self.release_now()) if c is not None]
        if any(c.secure for c in captures):
            return None
        for capture in captures:
            if capture.readable:
                return capture
        return captures[0] if captures else None

    def for_app(self) -> FocusContext | None:
        """The capture that names the app, the release one first as the paste
        goes there. One that couldn't tell (no bundle id) doesn't count."""
        for capture in (self.release_now(), self.press_now()):
            if capture is not None and capture.bundle_id:
                return capture
        return None

    def ax_ms(self) -> float:
        return self.press_ms + self.release_ms


class FocusTracker:
    """Runs and keeps each dictation's captures, keyed by session sequence.
    `enabled` is read inside each job, so turning use_screen_context off stops
    even a capture already queued."""

    def __init__(self, adapter, enabled: Callable[[], bool], worker: AXWorker | None = None,
                 budget_s: float = DEFAULT_BUDGET_S,
                 clock: Callable[[], float] = time.monotonic):
        self.adapter = adapter
        self._enabled = enabled
        self.worker = worker or AXWorker(clock=clock)
        self._budget_s = budget_s
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[int, SessionFocus] = {}

    def enabled(self) -> bool:
        try:
            return bool(self._enabled())
        except Exception:
            return False

    def _run_capture(self, state: SessionFocus, slot: str, textbox_only: bool):
        if not self.enabled():
            return None
        started = self._clock()
        try:
            return capture_focus(self.adapter, self._budget_s,
                                 textbox_only=textbox_only, clock=self._clock)
        finally:
            setattr(state, f"{slot}_ms", (self._clock() - started) * 1000)

    def _state(self, sequence) -> SessionFocus:
        return self._sessions.setdefault(sequence, SessionFocus())

    def capture_press(self, sequence) -> None:
        """Once per dictation. Only queues; never blocks its caller."""
        with self._lock:
            state = self._state(sequence)
            if state.press is not None:
                return
            state.press = self.worker.submit(
                lambda: self._run_capture(state, "press", False))

    def capture_release(self, sequence) -> None:
        with self._lock:
            state = self._state(sequence)
            if state.release is not None:
                return
            state.release = self.worker.submit(
                lambda: self._run_capture(state, "release", True))

    def session(self, sequence) -> SessionFocus | None:
        with self._lock:
            return self._sessions.get(sequence)

    def forget(self, sequence) -> None:
        """The dictation is over; its screen text goes with it."""
        with self._lock:
            self._sessions.pop(sequence, None)

    def read_now(self, budget_s: float) -> FocusContext | None:
        """A textbox capture on the worker, waited for at most budget_s. None
        when it couldn't be had in time."""
        future = self.worker.submit(lambda: capture_focus(
            self.adapter, budget_s, textbox_only=True, clock=self._clock))
        try:
            return future.result(timeout=budget_s)
        except (FutureTimeout, AXUnavailable):
            future.cancel()
            return None
        except Exception:
            return None

    def close(self) -> None:
        self.worker.close()


class MacAXAdapter:
    """The real thing, through HIServices. Import failure surfaces at
    construction; see make_ax_adapter."""

    def __init__(self):
        import HIServices
        from AppKit import NSRunningApplication, NSWorkspace
        self._hi = HIServices
        self._running_app = NSRunningApplication
        self._workspace = NSWorkspace
        self._system = HIServices.AXUIElementCreateSystemWide()
        self._electron: dict[str, bool] = {}

    def _describe(self, app) -> tuple[str | None, str | None, int | None]:
        if app is None:
            return None, None, None
        bundle_id = app.bundleIdentifier()
        name = app.localizedName()
        return (str(bundle_id) if bundle_id else None,
                str(name) if name else None,
                int(app.processIdentifier()))

    def frontmost(self):
        return self._describe(self._workspace.sharedWorkspace().frontmostApplication())

    def focused_element(self, timeout_s):
        """System-wide first: it sees a panel that has focus without its app
        being frontmost. It can also just refuse (seen as an instant
        kAXErrorCannotComplete on macOS 27, for any app), so fall back to
        asking the frontmost app."""
        started = time.monotonic()
        element = self.attribute(self._system, "AXFocusedUIElement", timeout_s)
        if element is not UNREADABLE:
            return element
        pid = self.frontmost()[2]
        if pid is None:
            return UNREADABLE
        return self.attribute(self._hi.AXUIElementCreateApplication(pid),
                              "AXFocusedUIElement",
                              timeout_s - (time.monotonic() - started))

    def attribute(self, element, name, timeout_s):
        hi = self._hi
        if timeout_s <= 0:
            return UNREADABLE
        hi.AXUIElementSetMessagingTimeout(element, float(timeout_s))
        error, value = hi.AXUIElementCopyAttributeValue(element, name, None)
        if error != 0 or value is None:
            return UNREADABLE
        if name == "AXSelectedTextRange":
            ok, cf_range = hi.AXValueGetValue(value, hi.kAXValueCFRangeType, None)
            return (int(cf_range[0]), int(cf_range[1])) if ok else UNREADABLE
        if isinstance(value, str):
            return str(value)
        if name == "AXNumberOfCharacters":
            return int(value)
        if isinstance(value, (list, tuple)) or hasattr(value, "count"):
            try:
                return list(value)
            except TypeError:
                pass
        return value

    def owner(self, element):
        error, pid = self._hi.AXUIElementGetPid(element, None)
        if error != 0:
            return None, None, None
        return self._describe(
            self._running_app.runningApplicationWithProcessIdentifier_(pid))

    def element_id(self, element):
        _, pid = self._hi.AXUIElementGetPid(element, None)
        return f"{pid}:{hash(element)}"

    def is_electron(self, pid):
        app = self._running_app.runningApplicationWithProcessIdentifier_(pid)
        url = app.bundleURL() if app is not None else None
        path = str(url.path()) if url is not None else ""
        if path not in self._electron:
            self._electron[path] = (
                Path(path) / "Contents/Frameworks/Electron Framework.framework"
            ).exists() if path else False
        return self._electron[path]


def make_ax_adapter():
    """The real adapter, or None when this Python can't reach the API; then
    Voisr simply runs without screen context."""
    try:
        return MacAXAdapter()
    except Exception as error:
        print(f"Voisr: screen context unavailable ({error.__class__.__name__})",
              flush=True)
        return None
