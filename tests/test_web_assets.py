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


TOKENS = WEB / "css" / "tokens.css"

COLOR_TOKENS = ["--bg", "--surface", "--ink", "--muted", "--border",
                "--accent", "--accent-hover", "--accent-soft", "--on-accent",
                "--success", "--success-soft", "--destructive", "--ring"]


def test_every_colour_token_is_defined_in_both_themes():
    css = TOKENS.read_text()
    parts = css.split(':root[data-appearance="dark"]', 1)
    assert len(parts) == 2, "no dark block in tokens.css"
    light, dark = parts
    for token in COLOR_TOKENS:
        assert f"{token}:" in light, f"{token} missing from light"
        assert f"{token}:" in dark, f"{token} missing from dark"


def test_each_token_is_declared_exactly_once_per_theme():
    """'system' is resolved to a concrete mode in JS, so the palette needs
    exactly two blocks and no value is written twice."""
    css = TOKENS.read_text()
    for token in COLOR_TOKENS:
        assert css.count(f"{token}:") == 2, \
            f"{token} declared {css.count(f'{token}:')} times, expected 2"


def test_tokens_do_not_use_a_colour_scheme_media_query():
    # the media query lives in main.js via matchMedia, not in CSS
    assert "prefers-color-scheme" not in TOKENS.read_text()


def test_boot_script_sets_the_theme_before_stylesheets_paint():
    """Without this, the window flashes light before main.js runs."""
    html = INDEX.read_text()
    head = html.split("</head>", 1)[0]
    assert "matchMedia" in head
    assert "data-appearance" in head or "dataset.appearance" in head


def test_no_hardcoded_hex_outside_tokens():
    # brand SVG markup in index.html is exempt; stylesheets are not
    for rel in ["css/base.css", "css/components.css", "css/views.css"]:
        css = (WEB / rel).read_text()
        stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        hexes = re.findall(r"#[0-9A-Fa-f]{3,8}\b", stripped)
        assert hexes == [], f"{rel} has hardcoded colours: {hexes}"


def test_titlebar_inset_is_reserved():
    assert "--titlebar-h" in TOKENS.read_text()
    assert "var(--titlebar-h)" in (WEB / "css" / "base.css").read_text()
