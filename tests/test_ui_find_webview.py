"""Regression test for the vibrancy sidebar bug: it took two independent
defects to explain why `_find_webview` never found pywebview's webview.

1. Name mismatch: pywebview's Cocoa backend installs a WKWebView
   *subclass* (BrowserView.WebKitHost, further KVO-swizzled at runtime to
   "NSKVONotifying_WebKitHost"). A `__class__.__name__.startswith(
   "WKWebView")` check never matches either name -- matching must go by
   class inheritance (isinstance) instead.

2. Shape mismatch: pywebview makes that webview the window's contentView
   *directly* (`window.setContentView_(webview)`, fired once the initial
   page load completes) -- it is not a subview inside the content view,
   it IS the content view. A lookup that only ever scanned
   contentView().subviews() found nothing but the webview's own internal
   WKFlippedView, no matter how good the class match was.

Both were confirmed on the real running app via runtime instrumentation
before the fix: contentView() was a bare placeholder NSView with 0
subviews before the page loaded, and the WKWebView itself (not a
subview) once it had.
"""
import pytest

WebKit = pytest.importorskip("WebKit")
from Foundation import NSMakeRect  # noqa: E402

from voisr.ui import _find_webview  # noqa: E402


class WebKitHost(WebKit.WKWebView):
    """Mirrors pywebview's real subclass name -- deliberately does NOT
    start with "WKWebView", so the old __class__.__name__.startswith(...)
    check would fail to find it (that is one regression this guards)."""


class NotAWebView:
    """An unrelated view that must never be mistaken for the webview."""

    def subviews(self):
        return []


def _make_real_webkit_host():
    cfg = WebKit.WKWebViewConfiguration.alloc().init()
    return WebKitHost.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, 10, 10), cfg)


class FakeWindow:
    def __init__(self, content):
        self._content = content

    def contentView(self):
        return self._content


def test_finds_webview_when_it_is_the_content_view_itself():
    """The real-world shape: pywebview's window.setContentView_(webview)
    makes the webview the top-level contentView, not a subview of it."""
    host = _make_real_webkit_host()
    window = FakeWindow(host)

    found = _find_webview(window)

    assert found is host
    assert not type(host).__name__.startswith("WKWebView")  # guards regression #1


def test_finds_webkit_subclass_nested_as_a_subview():
    """Defensive fallback for a differently-shaped window."""
    host = _make_real_webkit_host()
    other = NotAWebView()

    class FakeContentView:
        def subviews(self):
            return [other, host]

    window = FakeWindow(FakeContentView())

    found = _find_webview(window)

    assert found is host


def test_returns_none_when_no_webview_present():
    class FakeContentView(NotAWebView):
        def subviews(self):
            return [NotAWebView(), object()]

    window = FakeWindow(FakeContentView())
    assert _find_webview(window) is None


def test_never_raises_on_a_broken_window():
    assert _find_webview(object()) is None
