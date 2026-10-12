"""L3 (2026-10-12 audit): the dashboard's JS bridge reached private Api
internals. pywebview resolves a call's dotted name with getattr and no
underscore filter, so with js_api=api, "_history.path.unlink" (or any
method's __func__.__globals__) was callable from page script. The bridge is
now one plain function per method the dashboard calls, exposed by name."""
import re
from pathlib import Path

from voisr.ui import BRIDGE_METHODS, Api, bridge_functions

JS_DIR = Path(__file__).resolve().parent.parent / "voisr" / "web" / "js"
CALL = re.compile(r"V\.api\(\)\.([A-Za-z_]\w*)\(")
MOCK_KEY = re.compile(r"^\s{6}([a-z_]+): async", re.M)


def _js_calls():
    names = set()
    for path in JS_DIR.rglob("*.js"):
        names |= set(CALL.findall(path.read_text()))
    assert names, f"no V.api() calls found under {JS_DIR}"
    return names


def test_the_bridge_is_exactly_what_the_dashboard_calls():
    # a JS call missing here fails at runtime; an extra one is surface for
    # nothing
    assert set(BRIDGE_METHODS) == _js_calls()


def test_every_bridge_method_is_public_api():
    for name in BRIDGE_METHODS:
        assert not name.startswith("_")
        assert callable(getattr(Api, name, None)), name


def test_the_browser_mock_covers_the_bridge():
    # the dashboard-preview mock in api.js must answer every real call
    mock = set(MOCK_KEY.findall((JS_DIR / "api.js").read_text()))
    assert set(BRIDGE_METHODS) <= mock, set(BRIDGE_METHODS) - mock


class Recorder:
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args: (name, args)


def test_each_bridge_function_forwards_its_arguments():
    funcs = {f.__name__: f for f in bridge_functions(Recorder())}
    assert set(funcs) == set(BRIDGE_METHODS)
    assert funcs["set_config"]({"paste": False}) == ("set_config", ({"paste": False},))
    assert funcs["get_history"]() == ("get_history", ())


def test_bridge_functions_are_plain_functions():
    # not bound methods: those carry __self__, the Api, as an attribute
    for f in bridge_functions(Recorder()):
        assert not hasattr(f, "__self__")
