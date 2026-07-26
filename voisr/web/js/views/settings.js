"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  function miniPillNode(value) {
    const node = V.ui.el("div", { class: "mini-pill", data: { theme: value } });
    for (let i = 0; i < 7; i++) node.append(V.ui.el("span", { class: "mb" }));
    return node;
  }

  /* `known` lets render() hand down the config it has already fetched, so a
     Settings render costs one get_config rather than three — and a failed
     config read toasts once, not three times over. Called with no argument
     from main.js's theme-change hook, where there is nothing to hand down. */
  async function renderThemes(known) {
    const $ = V.ui.$;
    const [cfg, themes] = await Promise.all([
      known || V.ui.tryApi(V.api().get_config(), "Load settings"),
      V.ui.tryApi(V.api().get_pill_themes(), "Load pill themes"),
    ]);
    if (!cfg || !themes) return;
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

  async function renderColorWheel(known) {
    const $ = V.ui.$;
    const cfg = known || await V.ui.tryApi(V.api().get_config(), "Load settings");
    if (!cfg) return;
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
    cv.addEventListener("pointercancel", async () => {
      if (!dragging) return;
      dragging = false;
      // The gesture was interrupted (capture lost, system gesture took over,
      // etc.) before it ever reached pointerup, so unlike that handler there
      // is nothing to save here -- pointerup's own failure branch already
      // establishes the rule this follows: pick()'s live preview is only
      // trustworthy once it is confirmed written to config. A cancelled drag
      // never got that confirmation, so revert the preview to what is
      // actually stored rather than strand the interrupted colour on
      // screen -- otherwise hovering the wheel afterwards would look like it
      // is still dragging, since `dragging` would be the only state left
      // unrepaired.
      await renderColorWheel();
    });
    cv.addEventListener("pointerup", async () => {
      if (!dragging) return;
      dragging = false;
      // pick() has already painted the new colour as a live preview. If the
      // write fails, that preview is a lie about what the pill will actually
      // do, so re-read and repaint from what is really stored.
      if (!await V.ui.tryApi(
        V.api().set_config({ pill_color: lastHex }), "Save bar color")) {
        // Awaited so a DOM throw inside renderColorWheel (it has no try/catch
        // of its own) can't become an unhandled rejection nobody sees.
        await renderColorWheel();
        return;
      }
      V.ui.toast(`Bar color: ${lastHex}`);
    });
    $("#color-reset").addEventListener("click", async () => {
      if (!await V.ui.tryApi(
        V.api().set_config({ pill_color: null }), "Reset bar color")) return;
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

  function closeColorPopover() {
    document.getElementById("color-popover").hidden = true;
  }

  /* known-applied appearance value, kept in sync by render() and the change
     handler below -- lets the handler revert the <select> to what's actually
     applied if set_appearance() fails, rather than leaving it showing a
     theme that was never applied or persisted. */
  let appliedAppearance = "system";

  /* Settings is a list of independent rows fed by four independent bridge
     calls, so — like home.js's render() — each row is guarded on its own
     null and a failure degrades that row only. `models.models` on a failed
     get_models used to throw and blank every other row with it. */
  async function render() {
    const $ = V.ui.$;
    try {
      const [cfg, status, models, hotkey] = await Promise.all([
        V.ui.tryApi(V.api().get_config(), "Load settings"),
        V.ui.tryApi(V.api().get_hotkey_status(), "Load shortcut status"),
        V.ui.tryApi(V.api().get_models(), "Load models"),
        V.ui.tryApi(V.api().hotkey_display(), "Load shortcut"),
      ]);

      if (cfg) {
        $("#paste-toggle").checked = !!cfg.paste;
        $("#widget-toggle").checked = cfg.show_widget !== false;
        appliedAppearance = cfg.appearance || "system";
        $("#appearance-select").value = appliedAppearance;
        $("#launch-toggle").checked = !!cfg.launch_at_login;
      }
      if (hotkey) $("#hotkey-current").textContent = hotkey.display || "unset";
      if (status) $("#hotkey-status").textContent = status.status;

      if (models && models.models) {
        const select = $("#model-select");
        select.replaceChildren(...models.models.map((m) =>
          V.ui.el("option", {
            value: m.value,
            text: m.installed ? m.label : `${m.label} (not installed)`,
            disabled: !m.installed || null,
          })));
        select.value = models.active
          || (models.models.find((m) => m.installed) || {}).value || "";
      }

      // Hand `cfg` down rather than let each re-fetch it (see renderThemes).
      // A null cfg here already means get_config failed and toasted once
      // above -- calling these with nothing to hand down would each re-fetch,
      // each fail the same way, and each toast the same failure a second and
      // third time, which is exactly what `known` exists to prevent.
      if (cfg) await Promise.all([renderThemes(cfg), renderColorWheel(cfg)]);
    } catch (e) {
      V.ui.toast("Settings failed to load");
    }
  }

  /* destructive and irreversible, so it keeps a confirmation -- an in-app
     arm/disarm one, never the browser's native dialog. The disarm timer id
     is tracked (like ui.js's toastTimer) so it can always be cleared --
     re-arming, disarming, and firing all cancel any pending disarm, so a
     stale timeout can never fire against a button that's since moved on. */
  let clearHistoryDisarmTimer = null;
  let clearHistoryArmedAt = 0;

  function disarmClearHistory(btn) {
    clearTimeout(clearHistoryDisarmTimer);
    clearHistoryDisarmTimer = null;
    btn.dataset.armed = "0";
    btn.classList.remove("armed");
    btn.textContent = "Clear…";
  }

  /* ---------- bind, once ----------

     Called by main.js's init(), exactly like home/history/dictionary's
     init helpers and palette.init(). Everything below used to run at
     module-parse time; that worked (the script loads after index.html's
     body, so the elements exist) but made this the one view a reader had
     to learn a second convention for -- and made the module impossible to
     load outside a browser. Nothing here may be called twice: the
     document-level listeners in particular would stack. */

  function init() {
    document.getElementById("appearance-select").addEventListener("change", async (e) => {
      const prev = appliedAppearance;
      const next = e.target.value;
      const res = await V.ui.tryApi(V.api().set_appearance(next), "Change theme");
      if (!res) { e.target.value = prev; return; }
      appliedAppearance = next;
      V.applyAppearance(next);
      V.ui.toast(`Theme: ${next}`);
    });

    document.getElementById("model-select").addEventListener("change", async (e) => {
      const next = e.target.value;
      // A failed write that leaves the dropdown showing the new model is its
      // own bug — the user would dictate expecting a model that was never
      // saved. Re-render puts the <select> back on what is really stored.
      if (!await V.ui.tryApi(V.api().set_config({ model: next }), "Save model")) {
        render();
        return;
      }
      V.ui.toast(`Model: ${next} — applies from your next dictation`);
    });

    document.getElementById("launch-toggle").addEventListener("change", async (e) => {
      const res = await V.ui.tryApi(
        V.api().set_launch_at_login(e.target.checked), "Save launch at login");
      if (!res) { e.target.checked = !e.target.checked; return; }
      V.ui.toast(e.target.checked ? "Voisr will start at login" : "Launch at login off");
    });

    document.getElementById("color-open").addEventListener("click", openColorPopover);

    /* click-outside / Escape dismissal for the colour popover -- registered
       once here (init, called once), not inside openColorPopover, so repeated
       opens never stack extra listeners. The click listener bails out early
       on any click still inside the popover or on the swatch button that
       opened it, so the very click that opens the popover (which bubbles to
       document right after) can never immediately close it again. Mirrors the
       scrim/Escape pattern in palette.js. */
    document.addEventListener("click", (e) => {
      const pop = document.getElementById("color-popover");
      if (pop.hidden) return;
      if (pop.contains(e.target) || e.target.closest("#color-open")) return;
      closeColorPopover();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!document.getElementById("color-popover").hidden) closeColorPopover();
    });

    document.getElementById("clear-history").addEventListener("click", async () => {
      const btn = document.getElementById("clear-history");
      if (btn.dataset.armed !== "1") {
        clearTimeout(clearHistoryDisarmTimer);
        btn.dataset.armed = "1";
        btn.classList.add("armed");
        btn.textContent = "Click again to erase everything";
        clearHistoryArmedAt = Date.now();
        clearHistoryDisarmTimer = setTimeout(() => disarmClearHistory(btn), 4000);
        return;
      }
      // two click events landing in quick succession -- an accidental
      // double-click, or a stuck key/trackpad -- both arrive before any async
      // gap, so without a minimum dwell the second one reads as deliberate
      // confirmation. This is the app's only irreversible action; require the
      // confirm click to land at least ~600ms after arming.
      if (Date.now() - clearHistoryArmedAt < 600) return;
      disarmClearHistory(btn);
      const res = await V.ui.tryApi(V.api().clear_history(), "Clear history");
      if (!res) return;
      V.ui.toast(`History cleared — ${res.removed} removed`);
      // Reuses main.js's voisr:new-dictation listener, which refreshes
      // whichever of Home/History is on screen. From here on the event means
      // "history changed", not strictly "a dictation was added" -- a clear
      // is exactly the kind of change those views need to hear about too, or
      // their stats/rows stay stale until the user switches views.
      window.dispatchEvent(new Event("voisr:new-dictation"));
    });
  }

  V.views = V.views || {};
  V.views.settings = { init, render, renderThemes, renderColorWheel, initColorWheel, openColorPopover };
})(window.Voisr);
