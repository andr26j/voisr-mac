"""Minimal keyboard tap: listen-only, or active pass-through.

An "active" tap (kCGEventTapOptionDefault) needs only Accessibility; a
listen-only tap needs Input Monitoring. The active tap returns every event
unchanged — it observes exactly like the listen-only one.

Why not pynput's Listener: it reads the keyboard layout via Text Input
Services on its own listener thread (keycode_context in _run). TIS is a
main-queue-only API on modern macOS — the dispatch assertion fires
intermittently (e.g. while the input-source list refreshes on layout
switches) and kills the whole process (EXC_BREAKPOINT in
islGetInputSourceListWithAdditions). Voisr only needs raw virtual
keycodes, so this tap skips character translation entirely.

It also re-enables the tap if macOS disables it by timeout — a pynput
weakness (a disabled tap stays dead there).
"""
import importlib
import os
import threading

_OWN_PID = os.getpid()

TAP_MODES = ("listen", "active")


class QuartzKeyListener:
    """Calls handler(vk, kind, flags) for every key event.

    kind: "down" | "up" (normal keys, includes auto-repeat) or
          "flags" (modifier edges: Fn, Option, Command, ...)
    """

    def __init__(self, handler, mode="listen", quartz=None):
        if mode not in TAP_MODES:
            raise ValueError(f"unknown tap mode: {mode!r}")
        self._handler = handler
        self._mode = mode
        self._quartz = quartz
        self._loop = None
        self._tap = None
        self._thread = None
        self._started = threading.Event()
        self._stop_requested = False
        self.running = False
        self.state = "starting"

    def wait_started(self, timeout: float) -> str:
        """Block until the tap is running or has failed; return state."""
        self._started.wait(timeout)
        return self.state

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _quartz_module(self):
        return self._quartz if self._quartz is not None \
            else importlib.import_module("Quartz")

    @staticmethod
    def _teardown(Quartz, tap, loop, source) -> None:
        """Disable, unhook and invalidate a created tap. An enabled active
        tap that nobody services holds every key event until macOS times it
        out, so a tap must never outlive its run loop enabled. Each step is
        guarded: one failure must not skip the rest."""
        try:
            Quartz.CGEventTapEnable(tap, False)
        except Exception:
            pass
        if source is not None and loop is not None:
            try:
                Quartz.CFRunLoopRemoveSource(loop, source,
                                             Quartz.kCFRunLoopDefaultMode)
            except Exception:
                pass
        try:
            Quartz.CFMachPortInvalidate(tap)
        except Exception:
            pass

    def _run(self) -> None:
        Quartz = None
        tap = loop = source = None  # source: only once added to the loop
        try:
            Quartz = self._quartz_module()

            mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                    | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                    | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
            tap_holder = {}

            def callback(_proxy, etype, event, _refcon):
                try:
                    if etype in (Quartz.kCGEventTapDisabledByTimeout,
                                 Quartz.kCGEventTapDisabledByUserInput):
                        tap = tap_holder.get("tap")
                        # a tap being torn down stays off
                        if tap is not None and not self._stop_requested:
                            Quartz.CGEventTapEnable(tap, True)
                        return event
                    src_pid = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGEventSourceUnixProcessID)
                    if src_pid == _OWN_PID:
                        return event  # our own posted Cmd-V, not user input
                    vk = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode)
                    if etype == Quartz.kCGEventKeyDown:
                        kind = "down"
                    elif etype == Quartz.kCGEventKeyUp:
                        kind = "up"
                    else:
                        kind = "flags"
                    self._handler(int(vk), kind,
                                  int(Quartz.CGEventGetFlags(event)))
                except Exception:
                    pass  # a callback error must never take down the tap
                return event

            option = (Quartz.kCGEventTapOptionDefault if self._mode == "active"
                      else Quartz.kCGEventTapOptionListenOnly)
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                option, mask, callback, None)
            if tap is None:
                # listen: no Input Monitoring; active: no Accessibility — or a
                # grant macOS lists but no longer honours. Caller decides which.
                self.state = "failed"
                self._started.set()
                return
            tap_holder["tap"] = tap
            self._tap = tap
            src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            loop = Quartz.CFRunLoopGetCurrent()
            self._loop = loop
            Quartz.CFRunLoopAddSource(loop, src, Quartz.kCFRunLoopDefaultMode)
            source = src
            Quartz.CGEventTapEnable(tap, True)
        except Exception as e:
            # Any setup error must still release wait_started(); otherwise
            # the state stays "starting" and the caller waits out the timeout.
            if tap is not None:
                self._teardown(Quartz, tap, loop, source)
            self._tap = None
            self._loop = None
            self.running = False
            self.state = "failed"
            print(f"Voisr keytap: tap setup failed: {e}", flush=True)
            self._started.set()
            return
        # stop() sets the flag BEFORE reading _loop, and _loop was set above
        # before this check: either stop() saw the loop (and stops it) or
        # this sees the flag. A stop() that came before the loop existed
        # would otherwise leave this thread running the tap as an orphan.
        if self._stop_requested:
            self._teardown(Quartz, tap, loop, source)
            self._tap = None
            self._loop = None
            self.state = "stopped"
            self._started.set()
            return
        self.running = True
        self.state = "running"
        self._started.set()
        try:
            Quartz.CFRunLoopRun()
        finally:
            self._teardown(Quartz, tap, loop, source)
            self.running = False
            self.state = "stopped"
            self._tap = None
            self._loop = None

    def stop(self) -> None:
        self._stop_requested = True
        Quartz = self._quartz_module()
        # Disable now, from this thread: if the run loop has not been
        # entered yet, CFRunLoopStop below is lost (CFRunLoopRun clears the
        # stopped flag on entry), and an enabled tap nobody services would
        # stall the keyboard. The tap thread still does the full teardown.
        tap = self._tap
        if tap is not None:
            try:
                Quartz.CGEventTapEnable(tap, False)
            except Exception:
                pass
        loop, self._loop = self._loop, None
        if loop is not None:
            try:
                Quartz.CFRunLoopStop(loop)
            except Exception:
                pass


def capture_next_vk(timeout: float = 8.0, mode: str = "listen"):
    """Block until any key event arrives; return its vk or None. `mode` is
    the configured tap kind: an active-tap user may have no Input
    Monitoring, so a listen-only capture would never see the key."""
    result = {}
    got = threading.Event()

    def handler(vk, kind, flags):
        if kind in ("down", "flags") and "vk" not in result:
            result["vk"] = vk
            got.set()

    listener = QuartzKeyListener(handler, mode=mode)
    listener.start()
    got.wait(timeout)
    listener.stop()
    return result.get("vk")
