"""Menu-bar presence for Voisr.

A hotkey-driven app should be reachable without its window. The Dock icon
stays — this app has a real dashboard, so LSUIElement would be wrong.

UI only: this touches no input APIs and cannot interfere with the Quartz
event tap that drives push-to-talk.
"""

NS_VARIABLE_STATUS_ITEM_LENGTH = -1.0

IDLE_GLYPH = "▫▫▫"       # outline
RECORDING_GLYPH = "▬▬▬"  # filled

_MENU_TARGET_CLASS = None  # ObjC class, built once on first menu construction


def _glyph_image(recording: bool):
    """Three-bar waveform as a template NSImage (auto-inverts with the bar).

    Drawn programmatically rather than shipped as an asset so there is no
    file to lose in the bundle. A template image is what makes the icon
    legible in both light and dark menu bars: AppKit recolours it to match
    the bar instead of painting our literal black.
    """
    import AppKit
    w, h = 22.0, 14.0
    img = AppKit.NSImage.alloc().initWithSize_((w, h))
    img.lockFocus()
    try:
        AppKit.NSColor.blackColor().set()
        heights = (10.0, 6.0, 10.0) if recording else (4.0, 4.0, 4.0)
        x = 2.0
        for bh in heights:
            rect = AppKit.NSMakeRect(x, (h - bh) / 2.0, 4.0, bh)
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, 2.0, 2.0).fill()
            x += 7.0
    finally:
        img.unlockFocus()
    img.setTemplate_(True)
    return img


def _on_main_thread(action) -> None:
    """Run an AppKit call on the main thread.

    set_recording is invoked from whatever thread drove the dictation: the
    Quartz event-tap thread on the hotkey path, a pywebview worker on the
    dashboard path, and the queue's completion worker when transcription
    finishes. AppKit off the main thread is undefined behaviour, and a crash
    here would take down the process hosting the event tap that makes
    push-to-talk work.

    Main-thread callers stay synchronous, so callers can still read the glyph
    straight after the call. Matches the AppHelper.callAfter convention used
    by ui._run_save_panel/native_setup and permissions.open_pane.

    The import failure and the dispatch failure are caught separately, and
    only the first one falls back to running inline. Without pyobjc there is
    no run loop and nothing that could be a real AppKit call, so inline is the
    only option and is safe. A *dispatch* that fails means we are on a genuine
    background thread with a run loop that would not take the work -- running
    inline there is the off-main AppKit call this function exists to prevent,
    in the process that hosts the Quartz event tap. A missed glyph update is
    the cheaper failure, so that path renders nothing.
    """
    try:
        from Foundation import NSThread
        from PyObjCTools import AppHelper
    except Exception:
        action()  # no pyobjc (tests off macOS): no run loop to defer to
        return
    try:
        if not NSThread.isMainThread():
            AppHelper.callAfter(action)
            return
    except Exception:
        return  # dispatch failed: skip the render rather than run off-main
    action()


class StatusItem:
    def __init__(self, on_dictate, on_open, on_quit, hotkey_label_fn,
                 status_bar=None):
        self._on_dictate = on_dictate
        self._on_open = on_open
        self._on_quit = on_quit
        self._hotkey_label_fn = hotkey_label_fn
        self._status_bar = status_bar
        self.item = None

    def _bar(self):
        if self._status_bar is not None:
            return self._status_bar
        import AppKit
        return AppKit.NSStatusBar.systemStatusBar()

    def create(self) -> bool:
        """Create the menu-bar item. Must run on the main thread.
        Returns False (and logs) if it cannot be created — a missing icon
        must never stop the app from running."""
        if self.item is not None:
            return True
        try:
            self.item = self._bar().statusItemWithLength_(
                NS_VARIABLE_STATUS_ITEM_LENGTH)
            self._render(recording=False)
            self._build_menu()
        except Exception as e:
            print(f"Voisr status item: create failed: {e}", flush=True)
            if self.item is not None:
                try:
                    self._bar().removeStatusItem_(self.item)
                except Exception:
                    pass
            self.item = None
            return False
        return True

    def _render(self, recording: bool) -> None:
        button = self.item.button()
        # Tooltip first, glyph second. The glyph block below deliberately lets
        # a broken setTitle_ propagate -- a status item with no legible glyph
        # is worth surfacing, and create() relies on that to report failure.
        # But when it does propagate, everything after it is skipped, and the
        # tooltip is the last thing left telling the user what the item is and
        # which key drives it. Setting it first costs nothing and keeps both
        # properties: the tooltip is always applied, and _render still raises.
        try:
            label = self._hotkey_label_fn()
        except Exception:
            label = "unset"
        button.setToolTip_(f"Voisr — hold {label} to dictate")
        try:
            button.setImage_(_glyph_image(recording))
            button.setTitle_("")
        except Exception:
            # The image is cosmetic; the status item is not. Anything that
            # goes wrong drawing it (no pyobjc, no window server, an AppKit
            # change) falls back to the text glyphs the app shipped with.
            # Clear any image a previous successful render left behind, or
            # the button would show a stale glyph next to the text one.
            try:
                button.setImage_(None)
            except Exception:
                pass
            button.setTitle_(RECORDING_GLYPH if recording else IDLE_GLYPH)

    def _dispatch(self, action: str) -> None:
        """Single entry point for every menu action. The ObjC target below is
        thin glue over this, so the routing is testable without pyobjc."""
        handler = {"dictate": self._on_dictate,
                   "open": self._on_open,
                   "quit": self._on_quit}.get(action)
        if handler is None:
            return
        try:
            handler()
        except Exception as e:
            print(f"Voisr status item: {action} failed: {e}", flush=True)

    def _target(self):
        """Lazily build the ObjC target class. Defined inside the method so
        importing this module never requires pyobjc — the tests do not have
        a window server."""
        global _MENU_TARGET_CLASS
        import objc
        from Foundation import NSObject

        if _MENU_TARGET_CLASS is None:
            class _VoisrMenuTarget(NSObject):
                def initWithOwner_(self, owner):
                    self = objc.super(_VoisrMenuTarget, self).init()
                    if self is None:
                        return None
                    self._owner = owner
                    return self

                def dictate_(self, sender):
                    self._owner._dispatch("dictate")

                def openDashboard_(self, sender):
                    self._owner._dispatch("open")

                def quitApp_(self, sender):
                    self._owner._dispatch("quit")

            _MENU_TARGET_CLASS = _VoisrMenuTarget

        # retained on self: an ObjC menu target is not retained by the menu
        self._menu_target = _MENU_TARGET_CLASS.alloc().initWithOwner_(self)
        return self._menu_target

    def _build_menu(self) -> None:
        try:
            import AppKit
        except Exception:
            return
        target = self._target()
        menu = AppKit.NSMenu.alloc().init()

        for title, selector, key in (
                ("Dictate now", "dictate:", ""),
                ("Open Dashboard", "openDashboard:", "")):
            entry = AppKit.NSMenuItem.alloc() \
                .initWithTitle_action_keyEquivalent_(title, selector, key)
            entry.setTarget_(target)
            menu.addItem_(entry)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        hk = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Push-to-talk: {self._hotkey_label_fn()}", None, "")
        hk.setEnabled_(False)
        menu.addItem_(hk)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        quit_item = AppKit.NSMenuItem.alloc() \
            .initWithTitle_action_keyEquivalent_("Quit Voisr", "quitApp:", "q")
        quit_item.setTarget_(target)
        menu.addItem_(quit_item)

        self.item.setMenu_(menu)

    def set_recording(self, is_recording: bool) -> None:
        if self.item is None:
            return
        recording = bool(is_recording)

        def apply():
            if self.item is None:
                return  # released while the render sat in the run-loop queue
            try:
                self._render(recording=recording)
            except Exception as e:
                print(f"Voisr status item: set_recording failed: {e}",
                      flush=True)

        _on_main_thread(apply)

    def release(self) -> None:
        item, self.item = self.item, None
        if item is None:
            return
        try:
            self._bar().removeStatusItem_(item)
        except Exception:
            pass
