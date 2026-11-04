import re
import types
from pathlib import Path

from voisr import window_chrome

TOKENS = Path(__file__).resolve().parent.parent / "voisr" / "web" / "css" / "tokens.css"


class NSTitlebarContainerView:
    background = "windowBackgroundColor"

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
        NSWindowTitleHidden=1,
        NSColor=types.SimpleNamespace(clearColor=lambda: "clear"))


def test_apply_chrome_sets_full_size_content_and_transparent_titlebar():
    win = FakeWindow()
    out = window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert out == {"titlebar": True}
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
    assert window_chrome.apply_chrome(win, appkit=fake_appkit()) == {"titlebar": True}
    assert win.style_mask == mask


def test_the_titlebar_paints_nothing_over_the_sidebar():
    """pywebview's fill shows through a transparent titlebar as a band."""
    win = FakeWindow()
    window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert win.theme_frame.container.background == "clear"


def test_a_titlebar_view_we_cannot_reach_still_counts_as_applied():
    win = FakeWindow()
    win.contentView = lambda: None
    assert window_chrome.apply_chrome(win, appkit=fake_appkit()) == {"titlebar": True}


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
