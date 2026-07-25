"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  function miniPillNode(value) {
    const node = V.ui.el("div", { class: "mini-pill", data: { theme: value } });
    for (let i = 0; i < 7; i++) node.append(V.ui.el("span", { class: "mb" }));
    return node;
  }

  async function renderThemes() {
    const $ = V.ui.$;
    const [cfg, themes] = await Promise.all([V.api().get_config(), V.api().get_pill_themes()]);
    const active = cfg.pill_theme || "classic";
    $("#theme-grid").replaceChildren(...themes.map((t) =>
      V.ui.el("button", {
        type: "button",
        class: `theme-opt${t.value === active ? " active" : ""}`,
        data: { value: t.value },
        "aria-pressed": String(t.value === active),
      }, miniPillNode(t.value), V.ui.el("span", { class: "theme-name", text: t.label }))));
  }

  /* ----- custom color wheel (HSV, V=1; standard f(5),f(3),f(1) mapping) ----- */

  const WHEEL_R = 100;

  function hsvToHex(h, s) {
    const f = (n, k = (n + h / 60) % 6) => 1 - s * Math.max(0, Math.min(k, 4 - k, 1));
    return "#" + [f(5), f(3), f(1)]
      .map((v) => Math.round(v * 255).toString(16).padStart(2, "0").toUpperCase()).join("");
  }

  function hexToHs(hex) {
    const r = parseInt(hex.slice(1, 3), 16) / 255, g = parseInt(hex.slice(3, 5), 16) / 255,
          b = parseInt(hex.slice(5, 7), 16) / 255;
    const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
    let h = 0;
    if (d) {
      if (max === r) h = 60 * (((g - b) / d) % 6);
      else if (max === g) h = 60 * ((b - r) / d + 2);
      else h = 60 * ((r - g) / d + 4);
    }
    return { h: (h + 360) % 360, s: max ? d / max : 0 };
  }

  function drawColorWheel() {
    const $ = V.ui.$;
    const cv = $("#color-wheel"), ctx = cv.getContext("2d");
    const img = ctx.createImageData(200, 200);
    for (let y = 0; y < 200; y++) for (let x = 0; x < 200; x++) {
      const dx = x - WHEEL_R, dy = y - WHEEL_R, r = Math.hypot(dx, dy);
      const i = (y * 200 + x) * 4;
      if (r > WHEEL_R) { img.data[i + 3] = 0; continue; }
      const h = ((Math.atan2(dy, dx) * 180 / Math.PI) + 360) % 360, s = Math.min(r / WHEEL_R, 1);
      const f = (n, k = (n + h / 60) % 6) => 1 - s * Math.max(0, Math.min(k, 4 - k, 1));
      img.data[i] = 255 * f(5); img.data[i + 1] = 255 * f(3); img.data[i + 2] = 255 * f(1);
      img.data[i + 3] = r > WHEEL_R - 1.5 ? 255 * (WHEEL_R - r + 0.5) : 255; // soft edge
    }
    ctx.putImageData(img, 0, 0);
  }

  function setColorUi(hex) {
    const $ = V.ui.$;
    const preview = $("#color-preview"), thumb = $("#wheel-thumb");
    if (hex) {
      preview.style.setProperty("--mb-color", hex);
      const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16),
            b = parseInt(hex.slice(5, 7), 16);
      preview.style.setProperty("--mb-glow", `rgba(${r}, ${g}, ${b}, .75)`);
      preview.dataset.customColor = "";
      $("#color-swatch").style.background = hex;
      $("#color-hex").textContent = hex;
      const { h, s } = hexToHs(hex);
      const a = h * Math.PI / 180;
      thumb.style.left = (WHEEL_R + Math.cos(a) * s * (WHEEL_R - 2)) + "px";
      thumb.style.top = (WHEEL_R + Math.sin(a) * s * (WHEEL_R - 2)) + "px";
      thumb.hidden = false;
    } else {
      preview.style.removeProperty("--mb-color");
      preview.style.removeProperty("--mb-glow");
      delete preview.dataset.customColor;
      $("#color-swatch").style.background = "transparent";
      $("#color-hex").textContent = "theme default";
      thumb.hidden = true;
    }
  }

  async function renderColorWheel() {
    const $ = V.ui.$;
    const cfg = await V.api().get_config();
    $("#color-preview").dataset.theme = cfg.pill_theme || "classic";
    setColorUi(cfg.pill_color || null);
  }

  function initColorWheel() {
    const $ = V.ui.$;
    const cv = $("#color-wheel");
    let dragging = false, lastHex = null;

    function pick(e) {
      const rect = cv.getBoundingClientRect();
      let dx = e.clientX - rect.left - WHEEL_R, dy = e.clientY - rect.top - WHEEL_R;
      const r = Math.min(Math.hypot(dx, dy), WHEEL_R - 2), a = Math.atan2(dy, dx);
      lastHex = hsvToHex(((a * 180 / Math.PI) + 360) % 360, r / WHEEL_R);
      setColorUi(lastHex);  // live preview while dragging; config saved on release
    }

    cv.addEventListener("pointerdown", (e) => {
      dragging = true;
      pick(e);  // BEFORE capture: a capture failure must not swallow the pick
      try { cv.setPointerCapture(e.pointerId); } catch (_) {}
    });
    cv.addEventListener("pointermove", (e) => dragging && pick(e));
    cv.addEventListener("pointerup", async () => {
      if (!dragging) return;
      dragging = false;
      await V.api().set_config({ pill_color: lastHex });
      V.ui.toast(`Bar color: ${lastHex}`);
    });
    $("#color-reset").addEventListener("click", async () => {
      await V.api().set_config({ pill_color: null });
      setColorUi(null);
      V.ui.toast("Bar color: theme default");
    });
  }

  /* the 200x200 wheel canvas is only ever drawn (and its pointer handlers
     only ever bound) the first time the popover opens -- not on every
     Settings render, which would waste a full-canvas repaint nobody sees. */
  let wheelDrawn = false;

  function openColorPopover() {
    const pop = document.getElementById("color-popover");
    if (!wheelDrawn) { drawColorWheel(); initColorWheel(); wheelDrawn = true; }
    pop.hidden = !pop.hidden;
  }

  async function render() {
    const $ = V.ui.$;
    const [cfg, status, models, hotkey] = await Promise.all([
      V.api().get_config(), V.api().get_hotkey_status(),
      V.api().get_models(), V.api().hotkey_display(),
    ]);
    $("#paste-toggle").checked = !!cfg.paste;
    $("#widget-toggle").checked = cfg.show_widget !== false;
    $("#hotkey-current").textContent = hotkey.display || "unset";
    $("#hotkey-status").textContent = status.status;
    $("#appearance-select").value = cfg.appearance || "system";
    $("#launch-toggle").checked = !!cfg.launch_at_login;

    const select = $("#model-select");
    select.replaceChildren(...models.models.map((m) =>
      V.ui.el("option", {
        value: m.value,
        text: m.installed ? m.label : `${m.label} (not installed)`,
        disabled: !m.installed || null,
      })));
    select.value = models.active || (models.models.find((m) => m.installed) || {}).value || "";

    renderThemes();
    renderColorWheel();
  }

  /* ---------- bind, once (elements exist as soon as this script parses,
     since it loads after index.html's body) ---------- */

  document.getElementById("appearance-select").addEventListener("change", async (e) => {
    const res = await V.api().set_appearance(e.target.value);
    if (res.error) return V.ui.toast(res.error);
    V.applyAppearance(e.target.value);
    V.ui.toast(`Theme: ${e.target.value}`);
  });

  document.getElementById("model-select").addEventListener("change", async (e) => {
    await V.api().set_config({ model: e.target.value });
    V.ui.toast(`Model: ${e.target.value} — applies from your next dictation`);
  });

  document.getElementById("launch-toggle").addEventListener("change", async (e) => {
    const res = await V.api().set_launch_at_login(e.target.checked);
    if (res.error) { e.target.checked = !e.target.checked; return V.ui.toast(res.error); }
    V.ui.toast(e.target.checked ? "Voisr will start at login" : "Launch at login off");
  });

  document.getElementById("color-open").addEventListener("click", openColorPopover);

  document.getElementById("clear-history").addEventListener("click", async () => {
    // destructive and irreversible, so it keeps a confirmation -- an in-app
    // arm/disarm one, never the browser's native dialog
    const btn = document.getElementById("clear-history");
    if (btn.dataset.armed !== "1") {
      btn.dataset.armed = "1";
      btn.textContent = "Click again to erase everything";
      setTimeout(() => { btn.dataset.armed = "0"; btn.textContent = "Clear…"; }, 4000);
      return;
    }
    btn.dataset.armed = "0";
    btn.textContent = "Clear…";
    const res = await V.api().clear_history();
    if (res.error) return V.ui.toast(res.error);
    V.ui.toast(`History cleared — ${res.removed} removed`);
  });

  V.views = V.views || {};
  V.views.settings = { render, renderThemes, renderColorWheel, initColorWheel, openColorPopover };
})(window.Voisr);
