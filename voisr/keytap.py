"""Minimal listen-only keyboard tap.

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
import os
import threading

_OWN_PID = os.getpid()


class QuartzKeyListener:
    """Calls handler(vk, kind, flags) for every key event.

    kind: "down" | "up" (normal keys, includes auto-repeat) or
          "flags" (modifier edges: Fn, Option, Command, ...)
    """

    def __init__(self, handler):
        self._handler = handler
        self._loop = None
        self._thread = None
        self.running = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import Quartz

        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
        tap_holder = {}

        def callback(_proxy, etype, event, _refcon):
            try:
                if etype in (Quartz.kCGEventTapDisabledByTimeout,
                             Quartz.kCGEventTapDisabledByUserInput):
                    tap = tap_holder.get("tap")
                    if tap is not None:
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
                self._handler(int(vk), kind, int(Quartz.CGEventGetFlags(event)))
            except Exception:
                pass  # a callback error must never take down the tap
            return event

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly, mask, callback, None)
        if tap is None:
            return  # no Input Monitoring permission yet; caller respawns later
        tap_holder["tap"] = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._loop, source,
                                  Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(tap, True)
        self.running = True
        try:
            Quartz.CFRunLoopRun()
        finally:
            self.running = False
            self._loop = None

    def stop(self) -> None:
        import Quartz
        loop, self._loop = self._loop, None
        if loop is not None:
            try:
                Quartz.CFRunLoopStop(loop)
            except Exception:
                pass


def capture_next_vk(timeout: float = 8.0):
    """Block until any key event arrives; return its vk or None."""
    result = {}
    got = threading.Event()

    def handler(vk, kind, flags):
        if kind in ("down", "flags") and "vk" not in result:
            result["vk"] = vk
            got.set()

    listener = QuartzKeyListener(handler)
    listener.start()
    got.wait(timeout)
    listener.stop()
    return result.get("vk")
