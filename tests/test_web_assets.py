import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "voisr" / "web"
INDEX = WEB / "index.html"

JS_FILES = ["js/api.js", "js/ui.js", "js/lists.js", "js/palette.js",
            "js/meter.js", "js/dock-meter.js", "js/views/home.js", "js/views/history.js",
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


# Literal colour strings permitted in the consumer stylesheets (base.css,
# components.css, views.css), keyed to a short reason. Everything here is a
# deliberate exception to "no hardcoded colours outside tokens.css" — most
# are the recording pill's fixed per-theme brand colours, which intentionally
# do NOT follow light/dark appearance because the pill renders on its own
# dark chrome in every theme. A few are pre-existing generic shadow/glow
# tints unrelated to the pill. Adding a new literal colour anywhere in these
# files requires adding it here explicitly — the test will otherwise fail.
ALLOWED_LITERAL_COLOURS = {
    # -- recording-pill per-theme brand colours (components.css, views.css) --
    "rgb(255, 255, 255)": "pill theme: default/white waveform bars, and the colour-wheel thumb's white ring",
    "rgb(232, 100, 60)": "pill theme: Ember (orange)",
    "rgba(232, 100, 60, .8)": "pill theme: Ember glow (box-shadow)",
    "rgb(74, 222, 222)": "pill theme: Aurora (cyan half of the gradient)",
    "rgb(155, 93, 229)": "pill theme: Aurora (purple half of the gradient)",
    "rgb(90, 169, 230)": "pill theme: Ocean (blue)",
    "rgb(57, 255, 136)": "pill theme: Neon (green)",
    "rgba(57, 255, 136, .75)": "pill theme: Neon glow (box-shadow)",
    "rgb(168, 230, 207)": "pill theme: Breathe (mint)",
    "rgb(237, 237, 237)": "pill theme: Scanner (grey)",
    # -- pre-existing generic shadow/glow tints, not theme-bound --
    "rgba(31, 29, 26, .25)": "switch thumb drop shadow",
    "rgba(28, 26, 23, .96)": "mini-pill's own fixed dark chrome background",
    "rgba(0, 0, 0, .35)": "colour-wheel thumb shadow ring",
    "rgba(0, 0, 0, .4)": "colour-wheel thumb shadow ring",
    # -- appearance thumbnails: fixed light/dark preview swatches (views.css).
    #    Like the pill colours these must NOT follow the active appearance --
    #    a Light thumbnail has to still look light while the app is in dark
    #    mode, which is the whole point of showing the choice as a picture.
    "rgb(246, 243, 238)": "theme thumbnail: light --bg",
    "rgb(232, 227, 218)": "theme thumbnail: light --border",
    "rgb(35, 32, 25)": "theme thumbnail: dark --surface",
    "rgb(26, 24, 21)": "theme thumbnail: dark --bg",
    "rgb(51, 47, 39)": "theme thumbnail: dark --border",
    "rgba(255, 255, 255, .08)": "toast-action button: a fixed white-on-dark "
        "hover surface for the undo button. The toast chip itself uses "
        "var(--ink), which flips light/dark with the theme (light chip in "
        "dark mode), so this fixed white overlay is nearly invisible in "
        "dark mode there -- cosmetic only, since the button text still "
        "uses var(--accent) and stays legible",
}

LITERAL_COLOUR_RE = re.compile(
    r"#[0-9A-Fa-f]{3,8}\b|rgba?\([^)]*\)"
)


def test_no_unlisted_literal_colours_outside_tokens():
    """Enforces the "no hardcoded colours outside tokens.css" constraint for
    base.css, components.css and views.css. Catches hex (#rgb/#rrggbb/
    #rrggbbaa), rgb(...) and rgba(...) forms alike -- not just hex -- so a
    literal colour can't slip through by switching syntax. Anything found
    must be pre-approved in ALLOWED_LITERAL_COLOURS above; unlisted literals
    fail the test. (tokens.css itself legitimately uses rgba() inside token
    definitions and is intentionally not scanned here.)
    """
    # brand SVG markup in index.html is exempt; stylesheets are not
    for rel in ["css/base.css", "css/components.css", "css/views.css"]:
        css = (WEB / rel).read_text()
        stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        found = LITERAL_COLOUR_RE.findall(stripped)
        unlisted = [c for c in found if c not in ALLOWED_LITERAL_COLOURS]
        assert unlisted == [], f"{rel} has unlisted literal colours: {unlisted}"


def test_titlebar_inset_is_reserved():
    assert "--titlebar-h" in TOKENS.read_text()
    assert "var(--titlebar-h)" in (WEB / "css" / "base.css").read_text()


def test_row_renderers_do_not_use_innerhtml():
    for rel in ["js/views/home.js", "js/views/history.js",
                "js/views/dictionary.js"]:
        src = (WEB / rel).read_text()
        assert "innerHTML" not in src, f"{rel} still builds rows with innerHTML"


def test_ui_exposes_the_el_builder_and_dollar_shorthand():
    src = (WEB / "js" / "ui.js").read_text()

    # el() must be the DOM-builder helper — (tag, props, ...children) —
    # not the querySelector shorthand.
    assert re.search(r"function el\(tag, props, \.\.\.children\)", src), \
        "ui.js's el() is no longer the (tag, props, ...children) builder"

    # $ must independently be the querySelector shorthand.
    assert re.search(r"const \$ = \(sel\) => document\.querySelector\(sel\)", src), \
        "ui.js no longer defines $ as the querySelector shorthand"

    export_obj = re.search(r"V\.ui = \{(.*?)\};", src, re.S)
    assert export_obj, "could not find the V.ui export object in ui.js"
    exported = export_obj.group(1)

    # Both must be exported under their own names, not aliased to each other.
    assert re.search(r"(?<![.\w])el(?![.\w])(?!\s*:\s*\$)", exported), \
        "V.ui.el is not exported"
    assert "el: $" not in exported, \
        "V.ui.el is still aliased to the querySelector shorthand, not the builder"
    assert re.search(r"(?<![.\w])\$(?![.\w])(?!\s*:\s*el)", exported), \
        "V.ui.$ (querySelector shorthand) is not exported"


def test_home_has_a_permission_strip_container():
    assert 'id="perm-strip"' in INDEX.read_text()


def test_home_view_calls_the_permission_api():
    src = (WEB / "js" / "views" / "home.js").read_text()
    assert "get_permissions" in src
    assert "open_settings_pane" in src


def test_api_mock_provides_permissions():
    assert "get_permissions" in (WEB / "js" / "api.js").read_text()


def test_permission_strip_refreshes_on_window_focus():
    src = (WEB / "js" / "views" / "home.js").read_text()
    assert '"focus"' in src or "'focus'" in src


def test_home_leads_with_a_status_line_not_a_record_card():
    """Dictation happens outside this window — the old hero record card
    implied otherwise, and gave the fallback recorder the whole fold."""
    html = INDEX.read_text()
    assert 'id="ptt-bar"' in html
    assert 'id="ptt-keycap"' in html
    # the keycap and the sentence must both be able to name the key
    assert 'id="ptt-key-name"' in html
    assert 'class="record-card' not in html
    assert 'id="hero-wave"' not in html


def test_record_button_is_docked_not_the_headline():
    """Click-to-record survives as the fallback for when the key can't be
    used, so it lives in the dock: never a primary button, never the hero."""
    html = INDEX.read_text()
    assert 'class="dock"' in html
    assert 'id="record-btn" class="dock-mic"' in html
    assert 'id="dock-time"' in html, "the dock reports elapsed time while listening"
    dock = html[html.index('class="dock"'):html.index("</section>", html.index('class="dock"'))]
    assert "btn-primary" not in dock


def test_hero_renders_the_configured_hotkey_label():
    # the raw config value (e.g. "alt_r") must never reach the hero — it
    # resolves through Api.hotkey_display() so a fresh install with no
    # captured hotkey_label still shows a human-readable key name.
    src = (WEB / "js" / "views" / "home.js").read_text()
    assert "hotkey_display" in src
    assert "cfg.hotkey" not in src


def test_stats_are_three_tiles_each_carrying_its_own_trend():
    """One lead stat, a sparkline floating between unrelated numbers, and two
    shrunken also-rans became three equal tiles: every chart now belongs to
    the number above it."""
    html = INDEX.read_text()
    assert 'class="tiles"' in html
    # `class="tile"` or `class="tile tile-lead"`, but not the `tiles` grid
    assert len(re.findall(r'class="tile(?:"| )', html)) == 3
    for el_id in ["stat-saved", "stat-words", "stat-streak",
                  "trend-saved", "trend-words", "trend-streak"]:
        assert f'id="{el_id}"' in html, el_id
    assert 'class="stats-strip"' not in html
    assert 'id="spark"' not in html
    assert 'class="home-side"' not in html


def test_home_uses_time_saved_and_daily():
    src = (WEB / "js" / "views" / "home.js").read_text()
    assert "time_saved_seconds" in src
    assert "daily" in src
    assert "trendBars" in src
    assert "weekDelta" in src


def test_a_blocked_permission_takes_the_canvas_from_the_status_line():
    """Without Input Monitoring, holding the key does nothing — a chip above
    a line still promising it works was the app lying to the user."""
    src = (WEB / "js" / "views" / "home.js").read_text()
    assert "perm-block" in src
    assert "#ptt-bar" in src, "the status line has to be hideable"
    assert "perm-chip" not in src, "the chip strip is what the block replaced"


def test_ui_exposes_format_duration():
    assert "formatDuration" in (WEB / "js" / "ui.js").read_text()


def test_mock_stats_include_the_new_keys():
    src = (WEB / "js" / "api.js").read_text()
    assert "time_saved_seconds" in src and "daily" in src


def test_format_duration_rounds_to_minutes_before_splitting_hours():
    """Ensures formatDuration does not independently round minutes from the
    remainder, which can produce m=60. Instead, it must round to whole minutes
    first, then derive both h and m from that single rounded value."""
    src = (WEB / "js" / "ui.js").read_text()

    # The old broken pattern: independently rounding (s % 3600) / 60
    # This can produce m=60, leading to "60m" or "1h 60m".
    # Allow flexible whitespace: Math.round(\s*(\(\s*s\s*%\s*3600\s*\)\s*/\s*60|...))
    old_pattern = re.compile(
        r"Math\.round\s*\(\s*\(\s*s\s*%\s*3600\s*\)\s*/\s*60\s*\)"
    )
    assert not old_pattern.search(src), \
        "formatDuration still independently rounds (s % 3600) / 60, risking m=60"

    # The new correct pattern: round to whole minutes first
    # const totalM = Math.round(s / 60);
    # This must exist and be followed by deriving h and m from totalM
    new_pattern = re.compile(
        r"const\s+totalM\s*=\s*Math\.round\s*\(\s*s\s*/\s*60\s*\)"
    )
    assert new_pattern.search(src), \
        "formatDuration is not rounding to whole minutes first (totalM = Math.round(s / 60))"


def test_palette_markup_and_binding_exist():
    assert 'id="palette"' in INDEX.read_text()
    src = (WEB / "js" / "palette.js").read_text()
    assert "metaKey" in src and '"k"' in src
    assert "Escape" in src
    assert "ArrowDown" in src and "ArrowUp" in src


def test_palette_registers_the_specified_commands():
    src = (WEB / "js" / "palette.js").read_text()
    for cmd in ["Start dictation", "Search history", "Settings",
                "push-to-talk", "appearance", "Copy last dictation"]:
        assert cmd.lower() in src.lower(), cmd


def test_history_has_search_and_export_controls():
    html = INDEX.read_text()
    assert 'id="history-search"' in html
    assert 'id="history-export"' in html
    assert 'id="history-more"' in html


def test_history_binds_keyboard_navigation():
    src = (WEB / "js" / "views" / "history.js").read_text()
    for key in ["ArrowDown", "ArrowUp", "Backspace", "Enter"]:
        assert key in src, key
    assert "PAGE" in src


def test_no_confirm_dialogs_remain_in_the_web_layer():
    for path in WEB.rglob("*.js"):
        assert "confirm(" not in path.read_text(), path.name
    assert "confirm(" not in INDEX.read_text()


def test_delete_offers_undo():
    src = (WEB / "js" / "views" / "history.js").read_text()
    assert "restore_history_item" in src
    assert "Undo" in src


def test_export_is_wired_to_the_save_panel():
    src = (WEB / "js" / "views" / "history.js").read_text()
    assert "export_history" in src and "save_text_file" in src


def test_dictionary_supports_inline_edit_and_search():
    html = INDEX.read_text()
    assert 'id="dict-search"' in html
    src = (WEB / "js" / "views" / "dictionary.js").read_text()
    assert "contenteditable" in src or "beginEdit" in src
    assert "innerHTML" not in src


def test_settings_is_split_into_four_sections():
    html = INDEX.read_text()
    for section in ["Dictation", "Push-to-talk", "Appearance", "Privacy & data"]:
        assert section in html, section
    assert html.count('class="settings-section') >= 4
    # four stacked cards read as four objects; this is one list of
    # preferences, so the sections are headings over hairline rows
    assert 'class="settings-section card"' not in html


def test_appearance_is_chosen_by_picture():
    """An appearance choice is the one control that should show its own
    result — and it sits beside the pill-theme grid, which already did."""
    html = INDEX.read_text()
    assert 'id="appearance-grid"' in html
    assert 'id="appearance-select"' not in html
    src = (WEB / "js" / "views" / "settings.js").read_text()
    assert "renderAppearance" in src
    assert "thumb-art" in src


def test_settings_has_the_new_controls():
    html = INDEX.read_text()
    for el_id in ["appearance-grid", "model-select", "launch-toggle",
                  "color-popover", "clear-history"]:
        assert f'id="{el_id}"' in html, el_id


def test_colour_wheel_is_drawn_lazily():
    src = (WEB / "js" / "views" / "settings.js").read_text()
    assert "drawColorWheel" in src
    # the wheel must not be drawn during initial render
    assert "wheelDrawn" in src


def test_settings_binds_its_listeners_in_init():
    """settings.js must follow the same convention as every other view:
    listeners bind inside an init() that main.js calls once, never at
    module-parse time."""
    src = (WEB / "js" / "views" / "settings.js").read_text()

    assert re.search(r"^  function init\(\)\s*\{", src, re.M), \
        "settings.js no longer declares a module-level init()"

    export_obj = re.search(r"V\.views\.settings = \{(.*?)\};", src, re.S)
    assert export_obj, "could not find the V.views.settings export object"
    # \binit\b will not match initColorWheel — the export must name init itself
    assert re.search(r"\binit\b", export_obj.group(1)), \
        "settings.js does not export init"

    # Every statement sitting directly inside the module IIFE is indented two
    # spaces; anything inside a function body is indented four or more. The
    # parse-time bindings this refactor removed all started with `document.`
    # at two spaces, so a revert reintroduces them here.
    parse_time = re.findall(r"^  document\b.*$", src, re.M)
    assert parse_time == [], \
        f"settings.js still touches the DOM at parse time: {parse_time}"


def test_main_init_calls_settings_init():
    """The call must live inside main.js's own init(), alongside
    history.initKeys() / dictionary.initSearch() / palette.init()."""
    src = (WEB / "js" / "main.js").read_text()
    assert "V.views.settings.init();" in src, \
        "main.js never calls V.views.settings.init()"

    init_body = re.search(r"async function init\(\)\s*\{(.*?)\n  \}", src, re.S)
    assert init_body, "could not find main.js's init()"
    assert "V.views.settings.init();" in init_body.group(1), \
        "V.views.settings.init() is called from outside main.js's init()"


def test_view_transition_class_exists_and_respects_reduced_motion():
    css = (WEB / "css" / "base.css").read_text()
    assert ".view.entering" in css
    assert "prefers-reduced-motion" in css
    block = css.split("prefers-reduced-motion", 1)[1]
    assert "animation: none" in block
    # both the view fade and the row stagger must be disabled together — a
    # revert that drops only one half of the selector list would otherwise
    # still pass, silently leaving rows (or the view) stuck at opacity: 0
    rule = re.search(r"([^{}]+)\{\s*animation:\s*none;?\s*\}", block)
    assert rule, "expected an 'animation: none' rule inside the reduced-motion block"
    disabled_selectors = rule.group(1)
    assert ".view.entering" in disabled_selectors
    assert ".item-list.is-staggering .item-row" in disabled_selectors
    # the landing wash is colour, not movement: reduced motion keeps it
    assert ".item-row.is-landed" not in disabled_selectors


def test_show_applies_the_entering_class():
    assert "entering" in (WEB / "js" / "main.js").read_text()


def test_switching_views_returns_to_the_top():
    """.main is the single scroll container, so its offset survives a view
    switch — scrolled to the bottom of Home, Settings would otherwise open
    scrolled past its own content, showing a blank pane."""
    src = (WEB / "js" / "main.js").read_text()
    show = re.search(r"function show\(view\)\s*\{(.*?)\n  \}", src, re.S)
    assert show, "could not find main.js's show()"
    assert "scrollTop = 0" in show.group(1)


# ---- M2: copy must not claim the clipboard holds every dictation ----
# In hotkey mode with auto-paste on, a successful paste RESTORES the previous
# clipboard (voisr/inject.py), so the dictation is not left on it. The old
# Settings row ("Always on", "placed on your clipboard") and the pill's done
# label ("Dictation copied") both said otherwise.

def test_settings_does_not_claim_clipboard_is_always_on():
    html = INDEX.read_text()
    assert "Always on" not in html
    assert "placed on your clipboard" not in html
    assert "Copied when auto-paste is off or blocked" in html


def test_pill_done_label_does_not_say_copied():
    widget = (WEB / "widget.html").read_text()
    assert 'aria-label="Dictation copied"' not in widget
    assert 'aria-label="Dictation ready"' in widget


# ---- M3: on-accent text must meet WCAG AA (4.5:1) in both themes ----

def _theme_block(css, selector):
    start = css.index(selector)
    return css[start:css.index("}", start)]


def _token(block, name):
    return re.search(rf"{name}:\s*(#[0-9A-Fa-f]{{6}})", block).group(1)


def _luminance(hex_colour):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_primary_button_text_meets_aa_contrast_in_both_themes():
    css = TOKENS.read_text()
    for selector in (':root[data-appearance="light"]', ':root[data-appearance="dark"]'):
        block = _theme_block(css, selector)
        ratio = contrast_ratio(_token(block, "--on-accent"), _token(block, "--accent"))
        assert ratio >= 4.5, f"{selector}: on-accent/accent is {ratio:.2f}:1"


def test_settings_explains_smart_join_spacing():
    """L8: consecutive dictations are joined with a space unless a key was
    pressed in between; a mouse click does not count. Say so."""
    html = INDEX.read_text()
    assert "joined with a space" in html
    assert "mouse" in html


def test_boot_toasts_a_moved_aside_config_and_the_mock_answers_it():
    # H3: a corrupt config.json is quarantined at load; boot must say so.
    assert "take_config_notice" in (WEB / "js" / "main.js").read_text()
    assert "take_config_notice" in (WEB / "js" / "api.js").read_text()


def test_words_tile_says_its_number_is_all_time():
    # M9: the number is total_words; "last 14 days" described only the bars
    src = INDEX.read_text()
    tile = src[src.index('id="stat-words"'):]
    tile = tile[:tile.index("</div>\n          </div>") if "</div>\n          </div>" in tile else 600]
    assert "all time" in tile
    assert "last 14 days" not in tile


def test_palette_dictation_goes_through_home():
    # M7: a dock recording started from another view was invisible
    assert "dictateFromAnywhere" in (WEB / "js" / "palette.js").read_text()


# ---- M11 (2026-10-12 audit) ----

def test_theme_pickers_are_radio_groups():
    html = INDEX.read_text()
    for grid in ("appearance-grid", "theme-grid"):
        tag = re.search(rf'<div id="{grid}"[^>]*>', html).group(0)
        assert 'role="radiogroup"' in tag, grid


def test_dock_status_is_announced():
    tag = re.search(r'<p id="record-status"[^>]*>', INDEX.read_text()).group(0)
    assert 'role="status"' in tag


def test_apply_appearance_tells_the_rest_of_the_app():
    src = (WEB / "js" / "main.js").read_text()
    assert "voisr:appearance" in src
    assert "V.appearancePref" in src


def test_accent_text_meets_aa_on_every_background_in_both_themes():
    css = TOKENS.read_text()
    for selector in (':root[data-appearance="light"]', ':root[data-appearance="dark"]'):
        block = _theme_block(css, selector)
        for bg in ("--bg", "--surface", "--surface-2"):
            ratio = contrast_ratio(_token(block, "--accent-text"), _token(block, bg))
            assert ratio >= 4.5, f"{selector}: accent-text on {bg} is {ratio:.2f}:1"


def test_the_more_disclosure_uses_the_text_safe_accent():
    css = (WEB / "css" / "views.css").read_text()
    rule = css[css.index(".more summary {"):]
    rule = rule[:rule.index("}")]
    assert "var(--accent-text)" in rule


# ---- L8 (2026-10-12 audit) ----

def test_settings_status_colour_follows_its_state():
    css = (WEB / "css" / "views.css").read_text()
    assert '.setting-status[data-state="off"]' in css


def test_dock_sits_near_the_window_edge_not_a_padding_above_it():
    # sticky bottom:0 is inset by .main's 60px bottom padding, so the dock
    # floated 60px up with rows showing through the gap
    css = (WEB / "css" / "views.css").read_text()
    rule = css[css.index(".dock {"):]
    rule = rule[:rule.index("}")]
    assert "bottom: 0;" not in rule


def test_dead_home_code_is_gone():
    assert "PERM_HELP" not in (WEB / "js" / "views" / "home.js").read_text()
    assert 'id="delta-streak"' not in INDEX.read_text()


def test_dock_offset_derives_from_the_main_padding():
    # review: -44px silently encoded .main's 60px padding
    views = (WEB / "css" / "views.css").read_text()
    base = (WEB / "css" / "base.css").read_text()
    assert "var(--main-pad-b)" in views and "-44px" not in views
    assert "--main-pad-b:" in base


def test_landed_wash_outranks_the_row_stagger():
    """Opening a page replays the stagger (.is-staggering) while a refresh
    lands new rows. The stagger's `animation` (specificity 0,3,0) used to
    beat the wash's (0,2,0), so a row that arrived while you were away never
    showed where it went -- and under reduced motion, the stagger's
    `animation: none` erased it too."""
    css = re.sub(r"/\*.*?\*/", "", (WEB / "css" / "base.css").read_text(), flags=re.S)
    assert re.search(
        r"\.item-list\.is-staggering \.item-row\.is-landed[^{]*\{[^}]*animation:\s*wash", css)


def test_trend_bars_keep_round_caps_when_scaled():
    """Trend bars grow with scaleY, which squashes a plain 2px radius into a
    flat top. Like the pill and dock bars, the vertical radius is
    pre-stretched by the inverse of the scale (--h)."""
    css = re.sub(r"/\*.*?\*/", "", (WEB / "css" / "views.css").read_text(), flags=re.S)
    rule = re.search(r"\.trend \.bar \{([^}]*)\}", css).group(1)
    assert "scaleY(var(--h))" in rule
    assert re.search(r"border-radius:\s*2px\s*/\s*calc\(2px\s*/\s*var\(--h\)\)", rule)
