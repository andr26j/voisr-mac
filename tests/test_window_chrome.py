import types

from voisr import window_chrome


class FakeWindow:
    def __init__(self):
        self.style_mask = 1 | 2 | 4
        self.titlebar_transparent = None
        self.title_visibility = None
        self.content = FakeView()

    def styleMask(self):
        return self.style_mask

    def setStyleMask_(self, mask):
        self.style_mask = mask

    def setTitlebarAppearsTransparent_(self, value):
        self.titlebar_transparent = value

    def setTitleVisibility_(self, value):
        self.title_visibility = value

    def contentView(self):
        return self.content


class FakeView:
    def __init__(self):
        self.subviews_added = []
        self.frame_value = "FRAME"

    def frame(self):
        return self.frame_value

    def addSubview_positioned_relativeTo_(self, view, position, rel):
        self.subviews_added.append((view, position, rel))


class FakeEffectView:
    def __init__(self):
        self.material = None
        self.state = None
        self.blending = None

    @classmethod
    def new(cls):
        return cls()

    def setFrame_(self, f):
        self.frame_set = f

    def setAutoresizingMask_(self, m):
        self.mask = m

    def setMaterial_(self, m):
        self.material = m

    def setState_(self, s):
        self.state = s

    def setBlendingMode_(self, b):
        self.blending = b

    def setWantsLayer_(self, v):
        self.layer = v


class FakeWebView:
    def __init__(self):
        self.values = {}

    def setValue_forKey_(self, value, key):
        self.values[key] = value


def fake_appkit():
    return types.SimpleNamespace(
        NSVisualEffectView=FakeEffectView,
        NSVisualEffectMaterialSidebar=7,
        NSVisualEffectStateActive=1,
        NSVisualEffectBlendingModeBehindWindow=0,
        NSViewWidthSizable=2,
        NSViewHeightSizable=16,
        NSWindowBelow=-1,
        NSWindowTitleHidden=1,
    )


def test_apply_chrome_sets_full_size_content_and_transparent_titlebar():
    win = FakeWindow()
    out = window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert out["titlebar"] is True
    assert win.style_mask & (1 << 15)          # NSFullSizeContentView
    assert win.titlebar_transparent is True
    assert win.title_visibility == 1


def test_apply_chrome_keeps_existing_style_mask_bits():
    win = FakeWindow()
    window_chrome.apply_chrome(win, appkit=fake_appkit())
    assert win.style_mask & 1 and win.style_mask & 2 and win.style_mask & 4


def test_vibrancy_inserts_a_sidebar_effect_view_below_the_webview():
    win, wv = FakeWindow(), FakeWebView()
    out = window_chrome.apply_chrome(win, webview=wv, vibrancy=True,
                                     appkit=fake_appkit())
    assert out["vibrancy"] is True
    assert len(win.content.subviews_added) == 1
    view, position, rel = win.content.subviews_added[0]
    assert view.material == 7
    assert position == -1
    assert rel is wv
    assert wv.values["drawsBackground"] is False


def test_vibrancy_disabled_leaves_the_webview_opaque():
    win, wv = FakeWindow(), FakeWebView()
    out = window_chrome.apply_chrome(win, webview=wv, vibrancy=False,
                                     appkit=fake_appkit())
    assert out["vibrancy"] is False
    assert win.content.subviews_added == []
    assert wv.values == {}


def test_apply_chrome_never_raises_on_a_broken_window():
    out = window_chrome.apply_chrome(object(), appkit=fake_appkit())
    assert out == {"titlebar": False, "vibrancy": False}
