import re
import types
from pathlib import Path

from voisr import window_chrome

TOKENS = Path(__file__).resolve().parent.parent / "voisr" / "web" / "css" / "tokens.css"


class NSTitlebarContainerView:
    background = "windowBackgroundColor"

    def respondsToSelector_(self, name):
        return name == "setBackgroundColor:"

    def setBackgroundColor_(self, colour):
        self.background = colour


class FakeView:
    """A view in the frame's tree; responds like AppKit's titlebar container."""
    def __init__(self, parent=None, responds=True):
        self.parent = parent
        self.responds = responds
        self.background = "windowBackgroundColor"

    def superview(self):
        return self.parent

    def respondsToSelector_(self, name):
        return self.responds and name == "setBackgroundColor:"

    def setBackgroundColor_(self, colour):
        self.background = colour


class FakeThemeFrame:
    def __init__(self):
        self.container = NSTitlebarContainerView()
        self.other = types.SimpleNamespace()

    def subviews(self):
        return [self.other, self.container]


class FakeWindow:
    def __init__(self):
        self.theme_frame = FakeThemeFrame()
        self.style_mask = 1 | 2 | 4
        self.titlebar_transparent = None
        self.title_visibility = None
        self.frame_value = "FRAME"
        self.frames_set = []
        self.close_button = None

    def standardWindowButton_(self, kind):
        return self.close_button if kind == 0 else None

    def frame(self):
        return self.frame_value

    def setFrame_display_(self, frame, display):
        self.frames_set.append(frame)

    def contentView(self):
        return types.SimpleNamespace(superview=lambda: self.theme_frame)

    def styleMask(self):
        return self.style_mask

    def setStyleMask_(self, mask):
        self.style_mask = mask

    def setTitlebarAppearsTransparent_(self, value):
        self.titlebar_transparent = value

    def setTitleVisibility_(self, value):
        self.title_visibility = value


def fake_appkit():
    return types.SimpleNamespace(
        NSWindowTitleHidden=1, NSWindowCloseButton=0,
        NSColor=types.SimpleNamespace(clearColor=lambda: "clear"))


def test_apply_chrome_sets_full_size_content_and_transparent_titlebar():
    win = FakeWindow()
    out = window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert out == {"titlebar": True, "band": True}
    assert win.style_mask & (1 << 15)          # NSFullSizeContentView
    assert win.titlebar_transparent is True
    assert win.title_visibility == 1


def test_apply_chrome_keeps_existing_style_mask_bits():
    win = FakeWindow()
    window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert win.style_mask & 1 and win.style_mask & 2 and win.style_mask & 4


def test_apply_chrome_keeps_the_window_size():
    """AppKit shrinks the window by the old titlebar's height otherwise, so a
    1060x700 window came up 668 tall."""
    win = FakeWindow()
    window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert win.frames_set == ["FRAME"]


def test_applying_twice_is_harmless():
    # startup applies it at once and again when the page has loaded
    win = FakeWindow()
    window_chrome.apply_chrome(win, appkit=fake_appkit())
    mask = win.style_mask
    assert window_chrome.apply_chrome(win, appkit=fake_appkit()) == {
        "titlebar": True, "band": True}
    assert win.style_mask == mask


def test_the_titlebar_paints_nothing_over_the_sidebar():
    """pywebview's fill shows through a transparent titlebar as a band."""
    win = FakeWindow()
    window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert win.theme_frame.container.background == "clear"


def test_the_band_is_found_from_the_traffic_lights_not_a_class_name():
    """macOS may rename NSTitlebarContainerView; the close button's ancestor
    that sits directly under the frame view is the same view."""
    win = FakeWindow()
    frame = win.theme_frame
    renamed = FakeView(parent=frame)
    frame.subviews = lambda: [frame.other, renamed]
    inner = FakeView(parent=renamed)
    win.close_button = FakeView(parent=inner)
    win.contentView = lambda: types.SimpleNamespace(superview=lambda: frame)
    out = window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert out == {"titlebar": True, "band": True}
    assert renamed.background == "clear"


def test_with_no_close_button_the_class_name_still_finds_the_band():
    win = FakeWindow()
    out = window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert out["band"] is True
    assert win.theme_frame.container.background == "clear"


def test_a_band_we_cannot_clear_is_reported_not_hidden():
    """The band returning must show in the log, not pass as success."""
    win = FakeWindow()
    win.contentView = lambda: None            # no frame view to look in
    out = window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert out == {"titlebar": True, "band": False}


def test_a_container_that_cannot_take_a_colour_is_reported():
    win = FakeWindow()
    frame = win.theme_frame
    stubborn = FakeView(parent=frame, responds=False)
    frame.subviews = lambda: [stubborn]
    win.close_button = FakeView(parent=stubborn)
    win.contentView = lambda: types.SimpleNamespace(superview=lambda: frame)
    out = window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert out["band"] is False
    assert stubborn.background == "windowBackgroundColor"


def test_a_window_that_raises_while_looking_still_does_not_raise():
    win = FakeWindow()
    win.standardWindowButton_ = lambda kind: 1 / 0
    frame = win.theme_frame
    frame.subviews = lambda: 1 / 0
    out = window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert out == {"titlebar": True, "band": False}


def test_apply_chrome_never_raises_on_a_broken_window():
    out = window_chrome.apply_chrome(object(), appkit=fake_appkit())
    assert out == {"titlebar": False}


def test_the_sidebar_is_flat_with_no_effect_view():
    """The design's sidebar is a flat colour. An NSVisualEffectView would tint
    it with the wallpaper, so the frame around the panel would stop matching."""
    source = Path(window_chrome.__file__).read_text()
    assert "NSVisualEffectView" not in source
    assert "drawsBackground" not in source


def _theme_value(theme, token):
    css = TOKENS.read_text()
    start = css.index(f':root[data-appearance="{theme}"]')
    block = css[start:css.index("}", start)]
    return re.search(rf"{token}:\s*(#[0-9A-Fa-f]{{6}})", block).group(1)


def test_the_window_background_is_the_sidebar_colour():
    """Different colours would show as a flash before the page's first paint."""
    for theme, colour in window_chrome.WINDOW_BACKGROUND.items():
        assert colour == _theme_value(theme, "--bg"), theme


class FakeDefaults:
    def __init__(self, style):
        self.style = style

    def stringForKey_(self, key):
        assert key == "AppleInterfaceStyle"
        return self.style


def test_a_pinned_appearance_ignores_the_system():
    dark = FakeDefaults("Dark")
    assert window_chrome.launch_background("light", dark) == "#F5F4F1"
    assert window_chrome.launch_background("dark", FakeDefaults(None)) == "#141413"


def test_system_appearance_follows_macos():
    assert window_chrome.launch_background("system", FakeDefaults("Dark")) == "#141413"
    assert window_chrome.launch_background("system", FakeDefaults(None)) == "#F5F4F1"


def test_launch_background_never_raises():
    assert window_chrome.launch_background("system", object()) == "#F5F4F1"
    # a hand-edited config can hold anything
    assert window_chrome.launch_background(["dark"], FakeDefaults(None)) == "#F5F4F1"


def test_a_band_missed_on_the_first_pass_is_not_a_false_alarm():
    """apply_chrome runs at once and again on load; the first may be too early."""
    cleared, message = window_chrome.band_status({"band": False}, False, final=False)
    assert (cleared, message) == (False, None)
    cleared, message = window_chrome.band_status({"band": True}, cleared, final=True)
    assert (cleared, message) == (True, None)


def test_a_band_never_cleared_is_logged_on_the_final_pass():
    cleared, message = window_chrome.band_status({"band": False}, False, final=True)
    assert cleared is False
    assert "band not cleared" in message


def test_a_band_cleared_early_stays_cleared_if_the_last_pass_misses():
    cleared, message = window_chrome.band_status({"band": False}, True, final=True)
    assert (cleared, message) == (True, None)
