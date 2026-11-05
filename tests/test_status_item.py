import math

import pytest

import voisr.status_item as status_item
from voisr import logo_wave
from voisr.status_item import (GLYPH_POINTS, IDLE_GLYPH, LIVE_ON_DARK_BAR,
                               LIVE_ON_LIGHT_BAR, RECORDING_GLYPH, StatusItem)


class FakeButton:
    def __init__(self):
        self.title = None
        self.tooltip = None
        self.image = None
        self.appearance_name = "NSAppearanceNameAqua"

    def effectiveAppearance(self):
        # A real NSAppearance: the colour hangs on AppKit's own matching, so a
        # fake here would only test the fake. Off macOS the import fails, the
        # same way the code under test does there.
        import AppKit
        return AppKit.NSAppearance.appearanceNamed_(self.appearance_name)

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
    def boom(*_args):
        raise RuntimeError("cannot draw")

    monkeypatch.setattr(status_item, "_glyph_image", boom)


def test_idle_and_recording_states_differ():
    """The two states are distinguished by the drawn image, not the title.
    Idle must be a template, or it would render as literal black and vanish
    against a dark menu bar. Live must not be one, because a template carries
    no colour of ours, and colour is the only thing separating the two: they
    are the same shape. Without it the live state is invisible.

    Bitmap drawing works in this headless pytest process (no NSApplication, no
    window server) and is deterministic: the same state twice yields
    byte-identical TIFF, so a difference here is real pixel content rather
    than incidental metadata.
    """
    pytest.importorskip("AppKit")
    si, _ = make()
    si.create()
    button = si.item.button()

    idle = button.image
    assert idle is not None
    assert idle.isTemplate()
    assert tuple(idle.size()) == (GLYPH_POINTS, GLYPH_POINTS)
    assert button.title == "", "the title must yield to the image"

    si.set_recording(True)
    live = button.image
    assert live is not None
    assert not live.isTemplate()
    assert tuple(live.size()) == (GLYPH_POINTS, GLYPH_POINTS)
    assert button.title == "", "a live image must not fall back to text"
    assert _tiff(live) != _tiff(idle)

    si.set_recording(False)
    assert button.image.isTemplate()
    assert _tiff(button.image) == _tiff(idle)


def _rep(image, pixels):
    for rep in image.representations():
        if rep.pixelsWide() == pixels:
            return rep
    raise AssertionError(f"no {pixels}px bitmap in the image")


def _ink_on_curve(rep):
    """The colour at a pixel in the middle of the stroke, as 0-255 ints."""
    scale = rep.pixelsWide() / 100
    x, y = max(logo_wave.wave_points(logo_wave.SMALL), key=lambda p: p[1])
    c = rep.colorAtX_y_(int(x * scale), int(y * scale))
    assert c.alphaComponent() == pytest.approx(1.0, abs=0.01)
    return tuple(round(v * 255) for v in
                 (c.redComponent(), c.greenComponent(), c.blueComponent()))


def _hex_rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


@pytest.mark.parametrize("appearance, expected", [
    # The Vibrant pair is what a real status button reports on macOS 27.
    ("NSAppearanceNameAqua", LIVE_ON_LIGHT_BAR),
    ("NSAppearanceNameVibrantLight", LIVE_ON_LIGHT_BAR),
    ("NSAppearanceNameDarkAqua", LIVE_ON_DARK_BAR),
    ("NSAppearanceNameVibrantDark", LIVE_ON_DARK_BAR),
])
def test_the_live_colour_follows_the_menu_bar(appearance, expected):
    pytest.importorskip("AppKit")
    si, _ = make()
    si.create()
    button = si.item.button()
    button.appearance_name = appearance

    si.set_recording(True)

    for scale in status_item.GLYPH_SCALES:
        ink = _ink_on_curve(_rep(button.image, GLYPH_POINTS * scale))
        assert ink == pytest.approx(_hex_rgb(expected), abs=1)


def test_each_dictation_reads_the_bar_again():
    """The bar can change between dictations. A colour read once at launch
    would leave ultramarine on what has since become a dark bar."""
    pytest.importorskip("AppKit")
    si, _ = make()
    si.create()
    button = si.item.button()

    si.set_recording(True)
    first = _ink_on_curve(_rep(button.image, GLYPH_POINTS * 2))
    si.set_recording(False)
    button.appearance_name = "NSAppearanceNameDarkAqua"
    si.set_recording(True)
    second = _ink_on_curve(_rep(button.image, GLYPH_POINTS * 2))

    assert first == pytest.approx(_hex_rgb(LIVE_ON_LIGHT_BAR), abs=1)
    assert second == pytest.approx(_hex_rgb(LIVE_ON_DARK_BAR), abs=1)


def _alpha(rep):
    return [[rep.colorAtX_y_(x, y).alphaComponent()
             for x in range(rep.pixelsWide())]
            for y in range(rep.pixelsHigh())]


def test_idle_and_live_are_the_same_shape():
    """On the approved board only the colour changes. Note this makes colour
    the sole signal, which is why a colour that can't be worked out falls back
    to text instead of drawing something that looks idle."""
    pytest.importorskip("AppKit")
    si, _ = make()
    si.create()
    button = si.item.button()
    idle = _alpha(_rep(button.image, GLYPH_POINTS * 2))

    si.set_recording(True)

    assert _alpha(_rep(button.image, GLYPH_POINTS * 2)) == idle


def _distance_to_polyline(px, py, points):
    best = math.inf
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx, dy = x1 - x0, y1 - y0
        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy)
                         / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - x0 - t * dx, py - y0 - t * dy))
    return best


@pytest.mark.parametrize("scale", status_item.GLYPH_SCALES)
def test_the_glyph_is_the_small_ribbon_curve_at_every_density(scale):
    """Checked pixel by pixel against the geometry, since "some wave" would
    pass any weaker test. Inside the stroke must be solid and well outside it
    empty, in both bitmaps: a missing @2x one would be blurred on Retina."""
    pytest.importorskip("AppKit")
    assert logo_wave.style_for(GLYPH_POINTS) is logo_wave.SMALL

    si, _ = make()
    si.create()
    side = GLYPH_POINTS * scale
    rep = _rep(si.item.button().image, side)
    assert rep.pixelsHigh() == side
    assert tuple(rep.size()) == (GLYPH_POINTS, GLYPH_POINTS)

    k = side / 100
    points = [(x * k, y * k) for x, y in logo_wave.wave_points(logo_wave.SMALL)]
    radius = logo_wave.SMALL.stroke * k / 2
    solid = empty = 0
    for py in range(side):
        for px in range(side):
            d = _distance_to_polyline(px + 0.5, py + 0.5, points)
            alpha = rep.colorAtX_y_(px, py).alphaComponent()
            if d <= radius - 1:
                assert alpha > 0.99, f"hole in the stroke at {px},{py}"
                solid += 1
            elif d >= radius + 1:
                assert alpha < 0.01, f"stray ink at {px},{py}"
                empty += 1
    assert empty > 0
    assert solid > 0


def test_idle_does_not_need_to_know_the_bar(monkeypatch):
    """A template adapts by itself, so a broken appearance read must not cost
    the idle icon. Only the live colour depends on it."""
    pytest.importorskip("AppKit")

    def boom(_self):
        raise RuntimeError("no appearance")

    monkeypatch.setattr(FakeButton, "effectiveAppearance", boom)
    si, _ = make()

    assert si.create() is True
    assert si.item.button().image.isTemplate()


def test_an_unknown_bar_falls_back_to_text_rather_than_looking_idle(monkeypatch):
    """Idle and live share a shape, so without a colour a live glyph can't be
    told from idle, and a guessed colour might vanish against the bar. The text
    glyph is ugly but says the right thing."""
    pytest.importorskip("AppKit")
    si, _ = make()
    si.create()
    button = si.item.button()

    class NoMatch:
        def bestMatchFromAppearancesWithNames_(self, _names):
            return None

    monkeypatch.setattr(FakeButton, "effectiveAppearance",
                        lambda _self: NoMatch())
    si.set_recording(True)

    assert button.image is None
    assert button.title == RECORDING_GLYPH


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
    pytest.importorskip("AppKit")
    si, _ = make()
    si.create()
    button = si.item.button()

    def reject(img):
        # Rejects the real image, the way AppKit might refuse bad image
        # data -- but a clear-to-None (the fallback's stale-image cleanup)
        # is not "an image" and must still go through, or this mock would
        # block the very seam it exists to exercise.
        if img is None:
            button.image = None
            return
        raise RuntimeError("button refused the image")

    button.setImage_ = reject
    si.set_recording(True)

    assert si.item.button().image is None, \
        "the stale-image clear must have run despite the reject"
    assert si.item.button().title == RECORDING_GLYPH
    assert "Fn" in si.item.button().tooltip


def test_tooltip_shows_the_hotkey_label():
    si, _ = make(hotkey_label_fn=lambda: "Right Option")
    si.create()
    assert "Right Option" in si.item.button().tooltip


def test_the_tooltip_is_set_even_when_the_glyph_render_blows_up():
    """_render deliberately lets a broken setTitle_ propagate -- see
    test_set_recording_never_raises_when_the_render_fails and
    test_a_partial_create_failure_does_not_leak_a_status_item, which both
    depend on it. But a raise skips everything after it, and while the
    tooltip was written last that meant the one failure took the glyph *and*
    the only remaining hint of what the menu-bar item is. Ordering is the
    whole fix: set the tooltip first and both properties hold.

    Mutation check: move the tooltip block back below the glyph block in
    status_item._render and this test fails on the tooltip assertion."""
    label = ["Fn"]
    si, _ = make(hotkey_label_fn=lambda: label[0])
    si.create()
    button = si.item.button()

    # Breaks BOTH glyph paths: the image path's setTitle_("") and the text
    # fallback's setTitle_(GLYPH) alike, so _render really does raise.
    def boom(_t):
        raise RuntimeError("button blew up")

    button.setTitle_ = boom
    label[0] = "Right Option"

    with pytest.raises(RuntimeError):
        si._render(recording=True)

    assert "Right Option" in button.tooltip, \
        "a glyph that blows up must not take the tooltip down with it"


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
