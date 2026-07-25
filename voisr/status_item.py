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
        button.setTitle_(RECORDING_GLYPH if recording else IDLE_GLYPH)
        try:
            label = self._hotkey_label_fn()
        except Exception:
            label = "unset"
        button.setToolTip_(f"Voisr — hold {label} to dictate")

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
        try:
            self._render(recording=bool(is_recording))
        except Exception as e:
            print(f"Voisr status item: set_recording failed: {e}", flush=True)

    def release(self) -> None:
        item, self.item = self.item, None
        if item is None:
            return
        try:
            self._bar().removeStatusItem_(item)
        except Exception:
            pass
