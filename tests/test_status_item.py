import pytest

import voisr.status_item as status_item
from voisr.status_item import IDLE_GLYPH, RECORDING_GLYPH, StatusItem


class FakeButton:
    def __init__(self):
        self.title = None
        self.tooltip = None
        self.image = None

    def setTitle_(self, t):
        self.title = t

    def setImage_(self, img):
        self.image = img

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


def _tiff(image):
    return bytes(image.TIFFRepresentation())


def _force_text_glyphs(monkeypatch):
    """Make the image path fail the way a missing pyobjc or an absent window
    server would, so the text fallback is what gets exercised.

    The dispatch tests below also use this. They are about *whether* the
    render ran, not about how it looks, and the image path makes the title
    "" in both states -- so asserting on the title there would pass whether
    or not the render happened. Pinning them to the text path restores a
    two-valued observable, which is the only thing that gives them teeth."""
    def boom(_recording):
        raise RuntimeError("cannot draw")

    monkeypatch.setattr(status_item, "_glyph_image", boom)


def test_idle_and_recording_states_differ():
    """The two states are distinguished by the drawn image now, not the
    title. Both must be real template images -- a non-template image would
    render as literal black and vanish against a dark menu bar -- and their
    pixels must actually differ, or the recording state is invisible.

    NSImage.lockFocus works in this headless pytest process (no
    NSApplication, no window server) and draws deterministically: the same
    state twice yields byte-identical TIFF, so a difference here is real
    pixel content rather than incidental metadata.
    """
    pytest.importorskip("AppKit")
    si, _ = make()
    si.create()

    idle = si.item.button().image
    assert idle is not None
    assert idle.isTemplate()
    assert si.item.button().title == "", "the title must yield to the image"

    si.set_recording(True)
    recording = si.item.button().image
    assert recording is not None
    assert recording.isTemplate()
    assert _tiff(recording) != _tiff(idle)

    si.set_recording(False)
    assert _tiff(si.item.button().image) == _tiff(idle)


def test_a_failed_glyph_image_falls_back_to_the_text_glyphs(monkeypatch):
    """The image is cosmetic; the status item is not. If drawing fails the
    item must still be usable and still tell the two states apart -- this is
    the original text-glyph behaviour, preserved as the safety net."""
    _force_text_glyphs(monkeypatch)
    si, _ = make()

    assert si.create() is True
    assert si.item.button().image is None
    assert si.item.button().title == IDLE_GLYPH
    assert "Fn" in si.item.button().tooltip, \
        "the tooltip must survive a failed image"

    si.set_recording(True)
    assert si.item.button().title == RECORDING_GLYPH
    si.set_recording(False)
    assert si.item.button().title == IDLE_GLYPH


def test_falling_back_after_a_good_render_clears_the_stale_image(monkeypatch):
    """A render can succeed and a later one fail. Without clearing the image
    the button would carry the previous state's picture *and* the new text
    glyph -- showing idle bars beside "recording"."""
    pytest.importorskip("AppKit")
    si, _ = make()
    si.create()
    assert si.item.button().image is not None  # the good render

    _force_text_glyphs(monkeypatch)
    si.set_recording(True)

    assert si.item.button().image is None, "a stale image survived the fallback"
    assert si.item.button().title == RECORDING_GLYPH


def test_a_button_that_rejects_the_image_falls_back_to_the_text_glyphs():
    """The other failure seam: drawing succeeds but AppKit refuses the
    image. Same requirement -- text glyphs and a tooltip, no exception."""
    si, bar = make()
    si.create()

    def reject(_img):
        raise RuntimeError("button refused the image")

    si.item.button().setImage_ = reject
    si.set_recording(True)

    assert si.item.button().title == RECORDING_GLYPH
    assert "Fn" in si.item.button().tooltip


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
    _force_text_glyphs(monkeypatch)
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
    _force_text_glyphs(monkeypatch)
    si, _ = make()
    si.create()
    idle = si.item.button().title
    deferred = _fake_pyobjc(monkeypatch, main_thread=True)

    si.set_recording(True)

    assert deferred == []
    assert si.item.button().title != idle


def test_a_failed_dispatch_skips_the_render_instead_of_running_off_main(
        monkeypatch):
    """The inline fallback is correct only for "no pyobjc at all". If
    callAfter itself throws we are on a genuine background thread, and
    falling through to run the render inline is precisely the off-main AppKit
    call this guards against -- in the process hosting the Quartz event tap.
    A missed glyph update is strictly the cheaper failure."""
    import PyObjCTools

    _force_text_glyphs(monkeypatch)
    si, _ = make()
    si.create()
    idle = si.item.button().title
    _fake_pyobjc(monkeypatch, main_thread=False)

    class ThrowingAppHelper:
        @staticmethod
        def callAfter(fn, *args):
            raise RuntimeError("run loop is gone")

    monkeypatch.setattr(PyObjCTools, "AppHelper", ThrowingAppHelper)

    si.set_recording(True)  # must not raise

    assert si.item.button().title == idle, \
        "a failed dispatch fell through to AppKit on a non-main thread"


def test_set_recording_never_raises_when_the_render_fails():
    """A broken setTitle_ now breaks *both* render paths: the image path
    clears the title, and the text fallback sets it. So this is the
    everything-is-broken case -- _render raises and set_recording must still
    swallow it. Nothing about the status item is worth crashing the process
    that hosts the push-to-talk event tap."""
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
    """ExplodingButton inherits a working setImage_, so the image path is
    reached and it is the title -- primary clear and text fallback alike --
    that blows up. _render still propagates, so create must still report
    failure and hand the half-built item back to the status bar."""
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
