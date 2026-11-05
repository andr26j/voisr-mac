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
    applied["band"] = False
    try:
        view = _titlebar_container(native_window, appkit)
        if view is not None and view.respondsToSelector_("setBackgroundColor:"):
            view.setBackgroundColor_(appkit.NSColor.clearColor())
            applied["band"] = True
    except Exception:
        pass

    return applied


def _titlebar_container(native_window, appkit):
    """The view pywebview paints the band on: the traffic lights' ancestor that
    sits directly under the window's frame view. Found from the close button,
    so no private class name is needed; if the button isn't there, fall back
    to the class name, which is what this used to depend on alone."""
    frame = native_window.contentView().superview()
    try:
        view = native_window.standardWindowButton_(appkit.NSWindowCloseButton)
        while view is not None and view.superview() is not None:
            if view.superview() == frame:
                return view
            view = view.superview()
    except Exception:
        pass
    for view in frame.subviews():
        if "TitlebarContainer" in type(view).__name__:
            return view
    return None


def band_status(chrome, cleared_before, final):
    """(cleared, message) after one apply_chrome pass. The first pass can run
    before the titlebar view exists, so a miss is only worth saying on the
    final pass, and only if no earlier pass cleared the band."""
    cleared = cleared_before or bool(chrome.get("band"))
    if final and not cleared:
        return cleared, ("Voisr chrome: titlebar band not cleared; a white or "
                         "grey strip may show over the sidebar")
    return cleared, None
