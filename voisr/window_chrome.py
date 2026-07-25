"""Native window chrome for the dashboard.

pywebview's own create_window(vibrancy=True) is unusable here: it adds the
NSVisualEffectView as a subview of the webview, so it only shows through when
transparent=True — which also calls setHasShadow_(False) and drops the window
shadow. Instead the effect view goes into the window's contentView below the
webview, and the webview stops drawing its background. Same technique the pill
already uses (voisr/ui.py drawsBackground).

frameless=True is likewise wrong: it hides the traffic lights.
"""

NS_FULL_SIZE_CONTENT_VIEW = 1 << 15


def apply_chrome(native_window, webview=None, vibrancy=True, appkit=None) -> dict:
    """Unified titlebar + optional vibrancy sidebar. Must run on the main
    thread. Never raises: a cosmetic failure must not stop the app."""
    applied = {"titlebar": False, "vibrancy": False}
    if appkit is None:
        try:
            import AppKit as appkit
        except Exception:
            return applied

    try:
        native_window.setStyleMask_(
            native_window.styleMask() | NS_FULL_SIZE_CONTENT_VIEW)
        native_window.setTitlebarAppearsTransparent_(True)
        native_window.setTitleVisibility_(appkit.NSWindowTitleHidden)
        applied["titlebar"] = True
    except Exception:
        return applied

    if not vibrancy or webview is None:
        return applied

    try:
        content = native_window.contentView()
        effect = appkit.NSVisualEffectView.new()
        effect.setMaterial_(appkit.NSVisualEffectMaterialSidebar)
        effect.setState_(appkit.NSVisualEffectStateActive)
        effect.setBlendingMode_(appkit.NSVisualEffectBlendingModeBehindWindow)
        effect.setWantsLayer_(True)
        effect.setFrame_(content.frame())
        effect.setAutoresizingMask_(
            appkit.NSViewWidthSizable | appkit.NSViewHeightSizable)
        content.addSubview_positioned_relativeTo_(
            effect, appkit.NSWindowBelow, webview)
        webview.setValue_forKey_(False, "drawsBackground")
        applied["vibrancy"] = True
    except Exception:
        applied["vibrancy"] = False
    return applied
