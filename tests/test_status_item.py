from voisr.status_item import StatusItem


class FakeButton:
    def __init__(self):
        self.title = None
        self.tooltip = None

    def setTitle_(self, t):
        self.title = t

    def setToolTip_(self, t):
        self.tooltip = t


class FakeItem:
    def __init__(self):
        self.button_obj = FakeButton()
        self.menu = None
        self.visible = True

    def button(self):
        return self.button_obj

    def setMenu_(self, m):
        self.menu = m

    def setVisible_(self, v):
        self.visible = v


class FakeStatusBar:
    def __init__(self):
        self.items = []
        self.removed = []

    def statusItemWithLength_(self, length):
        item = FakeItem()
        self.items.append(item)
        return item

    def removeStatusItem_(self, item):
        self.removed.append(item)


def make(**kw):
    bar = FakeStatusBar()
    si = StatusItem(on_dictate=kw.get("on_dictate", lambda: None),
                    on_open=kw.get("on_open", lambda: None),
                    on_quit=kw.get("on_quit", lambda: None),
                    hotkey_label_fn=kw.get("hotkey_label_fn", lambda: "Fn"),
                    status_bar=bar)
    return si, bar


def test_create_adds_one_status_item():
    si, bar = make()
    assert si.create() is True
    assert len(bar.items) == 1
    assert si.item is bar.items[0]


def test_create_is_idempotent():
    si, bar = make()
    si.create()
    si.create()
    assert len(bar.items) == 1


def test_idle_and_recording_states_differ():
    si, _ = make()
    si.create()
    idle = si.item.button().title
    si.set_recording(True)
    assert si.item.button().title != idle
    si.set_recording(False)
    assert si.item.button().title == idle


def test_tooltip_shows_the_hotkey_label():
    si, _ = make(hotkey_label_fn=lambda: "Right Option")
    si.create()
    assert "Right Option" in si.item.button().tooltip


def test_release_removes_the_item_and_is_idempotent():
    si, bar = make()
    si.create()
    si.release()
    si.release()
    assert len(bar.removed) == 1
    assert si.item is None


def test_set_recording_before_create_does_not_raise():
    si, _ = make()
    si.set_recording(True)  # must not raise


def _fake_pyobjc(monkeypatch, *, main_thread: bool):
    """Stand in for the two pyobjc seams set_recording uses, and record what
    would have been handed to the run loop. Patching the modules (rather than
    spawning a real thread) is what makes the off-main-thread branch testable
    at all: a real background thread touching AppKit is the very thing this
    is meant to prevent."""
    import Foundation
    import PyObjCTools
    import PyObjCTools.AppHelper  # noqa: F401 -- import so the attr exists to patch

    deferred = []

    class FakeNSThread:
        @staticmethod
        def isMainThread():
            return main_thread

    class FakeAppHelper:
        @staticmethod
        def callAfter(fn, *args):
            deferred.append(fn)

    monkeypatch.setattr(Foundation, "NSThread", FakeNSThread)
    monkeypatch.setattr(PyObjCTools, "AppHelper", FakeAppHelper)
    return deferred


def test_set_recording_off_the_main_thread_defers_instead_of_touching_appkit(
        monkeypatch):
    """set_recording is called from the Quartz event-tap thread on the hotkey
    path and from a pywebview worker on the dashboard path. AppKit off the
    main thread is undefined behaviour, and a crash here would take down the
    process hosting the event tap that makes push-to-talk work -- so the
    render must be handed to the run loop, not run inline."""
    si, _ = make()
    si.create()
    idle = si.item.button().title
    deferred = _fake_pyobjc(monkeypatch, main_thread=False)

    si.set_recording(True)

    assert si.item.button().title == idle, \
        "AppKit was touched inline on a non-main thread"
    assert len(deferred) == 1
    deferred[0]()  # what the main thread would run
    assert si.item.button().title != idle


def test_set_recording_on_the_main_thread_still_applies_synchronously(
        monkeypatch):
    """Api calls set_recording synchronously and the test fakes read the glyph
    straight after, so the main-thread path must not become deferred."""
    si, _ = make()
    si.create()
    idle = si.item.button().title
    deferred = _fake_pyobjc(monkeypatch, main_thread=True)

    si.set_recording(True)

    assert deferred == []
    assert si.item.button().title != idle


def test_set_recording_never_raises_when_the_render_fails():
    si, _ = make()
    si.create()

    def boom(_t):
        raise RuntimeError("button blew up")

    si.item.button().setTitle_ = boom
    si.set_recording(True)  # must not raise


def test_a_deferred_set_recording_after_release_does_nothing(monkeypatch,
                                                             capsys):
    """The run loop can drain a deferred render after release() has already
    torn the item down. That is an ordinary race, not a fault: it must be a
    silent no-op rather than an exception logged as a failure."""
    si, _ = make()
    si.create()
    deferred = _fake_pyobjc(monkeypatch, main_thread=False)
    si.set_recording(True)
    si.release()

    deferred[0]()  # must not raise

    assert "set_recording failed" not in capsys.readouterr().out


def test_dispatch_routes_each_menu_action_to_its_handler():
    calls = []
    si, _ = make(on_dictate=lambda: calls.append("dictate"),
                 on_open=lambda: calls.append("open"),
                 on_quit=lambda: calls.append("quit"))
    si._dispatch("dictate")
    si._dispatch("open")
    si._dispatch("quit")
    assert calls == ["dictate", "open", "quit"]


def test_menu_items_carry_the_expected_selector_and_route_to_dispatch():
    """The ObjC target's dictate_/openDashboard_/quitApp_ selectors are the
    only wiring between a menu click and _dispatch -- untested, a typo in
    either the NSMenuItem selector string or the target's method name would
    silently kill the only menu-bar affordance (no exception, the item
    would just never fire)."""
    calls = []
    si, _ = make(on_dictate=lambda: calls.append("dictate"),
                 on_open=lambda: calls.append("open"),
                 on_quit=lambda: calls.append("quit"))
    si.create()

    by_title = {str(item.title()): item for item in si.item.menu.itemArray()}

    dictate_item = by_title["Dictate now"]
    assert str(dictate_item.action()) == "dictate:"
    assert dictate_item.target() is si._menu_target

    open_item = by_title["Open Dashboard"]
    assert str(open_item.action()) == "openDashboard:"
    assert open_item.target() is si._menu_target

    quit_item = by_title["Quit Voisr"]
    assert str(quit_item.action()) == "quitApp:"
    assert quit_item.target() is si._menu_target

    # Firing each selector directly (as AppKit would on a real click) must
    # route through _dispatch to the matching handler.
    si._menu_target.dictate_(None)
    si._menu_target.openDashboard_(None)
    si._menu_target.quitApp_(None)
    assert calls == ["dictate", "open", "quit"]


def test_dispatch_ignores_an_unknown_action_and_swallows_handler_errors():
    def boom():
        raise RuntimeError("handler blew up")

    si, _ = make(on_dictate=boom)
    si._dispatch("nonsense")   # must not raise
    si._dispatch("dictate")    # must not raise


def test_a_partial_create_failure_does_not_leak_a_status_item():
    class ExplodingButton(FakeButton):
        def setTitle_(self, t):
            raise RuntimeError("button blew up")

    class ExplodingItem(FakeItem):
        def __init__(self):
            super().__init__()
            self.button_obj = ExplodingButton()

    class ExplodingBar(FakeStatusBar):
        def statusItemWithLength_(self, length):
            item = ExplodingItem()
            self.items.append(item)
            return item

    bar = ExplodingBar()
    si = StatusItem(on_dictate=lambda: None, on_open=lambda: None,
                    on_quit=lambda: None, hotkey_label_fn=lambda: "Fn",
                    status_bar=bar)

    assert si.create() is False
    assert si.create() is False
    assert len(bar.items) == len(bar.removed)


def test_a_broken_status_bar_reports_failure_without_raising():
    class Broken:
        def statusItemWithLength_(self, _):
            raise RuntimeError("no window server")

    si = StatusItem(on_dictate=lambda: None, on_open=lambda: None,
                    on_quit=lambda: None, hotkey_label_fn=lambda: "Fn",
                    status_bar=Broken())
    assert si.create() is False
    assert si.item is None
