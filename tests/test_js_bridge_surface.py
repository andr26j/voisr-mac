"""L3 (2026-10-12 audit): the dashboard's JS bridge reached private Api
internals. pywebview resolves a call's dotted name with getattr and no
underscore filter, so with js_api=api, "_history.path.unlink" (or any
method's __func__.__globals__) was callable from page script. The bridge is
now one plain function per method the dashboard calls, exposed by name."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from voisr.ui import BRIDGE_METHODS, Api, bridge_functions

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "voisr" / "web" / "js"
API_USE = re.compile(r"V\.api\(\)")
CALL = re.compile(r"V\.api\(\)\.([A-Za-z_]\w*)\(")
# api.js defines V.api itself; that line is the one use that isn't a call
DEFINITION = "V.api = () => window.pywebview.api;"


def _js_sources():
    return {path: path.read_text() for path in JS_DIR.rglob("*.js")}


def _js_calls():
    names = set()
    for text in _js_sources().values():
        names |= set(CALL.findall(text))
    assert names, f"no V.api() calls found under {JS_DIR}"
    return names


def test_every_bridge_use_is_a_direct_named_call():
    # anything else (an alias, ?., [name]) would slip past _js_calls and the
    # function would be missing at runtime only
    for path, text in _js_sources().items():
        text = text.replace(DEFINITION, "")
        assert len(API_USE.findall(text)) == len(CALL.findall(text)), path


def test_the_bridge_is_exactly_what_the_dashboard_calls():
    # a JS call missing here fails at runtime; an extra one is surface for
    # nothing
    assert set(BRIDGE_METHODS) == _js_calls()


def test_every_bridge_method_is_public_api():
    for name in BRIDGE_METHODS:
        assert not name.startswith("_")
        assert callable(getattr(Api, name, None)), name


NODE = shutil.which("node")
MOCK_KEYS_JS = """
import { loadVoisr } from "./tests/js/load.mjs";
loadVoisr(["js/api.js"]);
await new Promise((r) => setTimeout(r, 400));  // api.js installs it at 300 ms
console.log(JSON.stringify(Object.keys(globalThis.window.pywebview.api)));
"""


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_the_browser_mock_matches_the_bridge():
    # the dashboard-preview mock in api.js must answer every real call, and
    # offer nothing the app doesn't. Run it rather than parse it.
    proc = subprocess.run([NODE, "--input-type=module", "-e", MOCK_KEYS_JS],
                          capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert set(json.loads(proc.stdout)) == set(BRIDGE_METHODS)


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


def test_main_exposes_the_bridge_and_never_the_api(monkeypatch):
    from voisr import ui

    seen = {}

    class FakeApi(Recorder):
        def __init__(self, **kwargs):
            pass

    class FakeService:
        def __init__(self, api, widget, fallback_check=None):
            pass

        def stop(self):
            pass

    class FakeWindow:
        def expose(self, *funcs):
            seen["exposed"] = [f.__name__ for f in funcs]

    def create_window(*args, **kwargs):
        seen["kwargs"] = kwargs
        return FakeWindow()

    def start(callback):
        raise KeyboardInterrupt  # stop before the GUI would run

    monkeypatch.setattr(ui, "Api", FakeApi)
    monkeypatch.setattr(ui, "HotkeyService", FakeService)
    monkeypatch.setattr(ui, "RecordingWidget", lambda **kwargs: SimpleNamespace(create=lambda: None))
    monkeypatch.setattr(ui, "StatusItem", lambda **kwargs: None)
    monkeypatch.setattr("voisr.inject.prepare_paste_controller", lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        create_window=create_window, start=start))

    with pytest.raises(KeyboardInterrupt):
        ui.main()

    assert "js_api" not in seen["kwargs"]
    assert seen["exposed"] == list(BRIDGE_METHODS)
