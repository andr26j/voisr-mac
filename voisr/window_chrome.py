"""Native window chrome for the dashboard.

The sidebar is flat: the page paints it, and the panel sits inset on it. So
the only native work is running the page up under a transparent titlebar, and
giving the window the sidebar's colour so nothing flashes before first paint.

frameless=True would be wrong here: it hides the traffic lights.
"""

NS_FULL_SIZE_CONTENT_VIEW = 1 << 15

# The window behind the page until its first paint. Must match --bg in
# web/css/tokens.css; tests/test_window_chrome.py checks that it does.
WINDOW_BACKGROUND = {"light": "#F5F4F1", "dark": "#141413"}


def launch_background(appearance, defaults=None) -> str:
    """The window colour for a saved appearance ("system", "light" or "dark").
    Never raises: a cosmetic failure must not stop the app."""
    if isinstance(appearance, str) and appearance in WINDOW_BACKGROUND:
        return WINDOW_BACKGROUND[appearance]
    try:
        if defaults is None:
            from Foundation import NSUserDefaults
            defaults = NSUserDefaults.standardUserDefaults()
        style = defaults.stringForKey_("AppleInterfaceStyle")
    except Exception:
        style = None
    return WINDOW_BACKGROUND["dark" if style == "Dark" else "light"]


def apply_chrome(native_window, appkit=None) -> dict:
    """Unified titlebar. Must run on the main thread. Never raises: a cosmetic
    failure must not stop the app."""
    applied = {"titlebar": False}
    if appkit is None:
        try:
            import AppKit as appkit
        except Exception:
            return applied

    try:
        # Extending the content keeps its height, so AppKit drops the old
        # titlebar's height off the window. Hold the frame: the design is 700 tall.
        frame = native_window.frame()
        native_window.setStyleMask_(
            native_window.styleMask() | NS_FULL_SIZE_CONTENT_VIEW)
        native_window.setFrame_display_(frame, True)
        native_window.setTitlebarAppearsTransparent_(True)
        native_window.setTitleVisibility_(appkit.NSWindowTitleHidden)
        applied["titlebar"] = True
    except Exception:
        return applied

    # pywebview paints the titlebar with windowBackgroundColor, which a
    # transparent titlebar doesn't remove: a white or grey band over the flat
    # sidebar, and it follows the appearance, so this has to be the view's own
    # colour rather than a one-off change to its layer.
    try:
        for view in native_window.contentView().superview().subviews():
            if "TitlebarContainer" in type(view).__name__:
                view.setBackgroundColor_(appkit.NSColor.clearColor())
    except Exception:
        pass

    return applied
