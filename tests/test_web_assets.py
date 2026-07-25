import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "voisr" / "web"
INDEX = WEB / "index.html"

JS_FILES = ["js/api.js", "js/ui.js", "js/palette.js",
            "js/views/home.js", "js/views/history.js",
            "js/views/dictionary.js", "js/views/settings.js", "js/main.js"]
CSS_FILES = ["css/tokens.css", "css/base.css",
             "css/components.css", "css/views.css"]


def test_all_web_files_exist():
    for rel in JS_FILES + CSS_FILES:
        assert (WEB / rel).exists(), f"missing {rel}"


def test_old_monolith_files_are_gone():
    assert not (WEB / "app.js").exists()
    assert not (WEB / "style.css").exists()


def test_index_loads_scripts_in_dependency_order():
    html = INDEX.read_text()
    order = [m for m in re.findall(r'<script src="([^"]+)"', html)]
    assert order == JS_FILES, f"got {order}"


def test_index_loads_all_stylesheets():
    html = INDEX.read_text()
    hrefs = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert hrefs == CSS_FILES


def test_no_es_module_syntax_anywhere():
    # WebKit blocks module imports over file://
    for rel in JS_FILES:
        src = (WEB / rel).read_text()
        assert not re.search(r'^\s*(import|export)\s', src, re.M), rel
    assert 'type="module"' not in INDEX.read_text()


def test_every_js_file_attaches_to_the_voisr_namespace():
    for rel in JS_FILES:
        if rel.endswith("main.js"):
            continue
        assert "window.Voisr" in (WEB / rel).read_text(), rel
