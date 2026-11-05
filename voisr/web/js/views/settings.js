"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const byId = (id) => document.getElementById(id);

  /* ----- pill previews -----

     Still pictures of the real pill, so a Settings page left open draws
     nothing. The bars are the board's, and only their height is per-bar. */

  const BAR_HEIGHTS = [3, 6, 4.9, 10.1, 7.8, 14.3, 11.3, 15.4, 9.8, 13.6, 7.1, 9.5, 6.2, 6, 3];

  /* A custom colour recolours Signature's bars and Quiet's transcribing dots.
     Quiet's live dot is always periwinkle, so its picker picture shows that
     dot, and the colour popover shows the dots the colour actually changes. */
  function pillArt(theme, showColour) {
    const el = V.ui.el;
    if (theme === "quiet") {
      return showColour
        ? [el("i", { class: "pp-dot" }), el("i", { class: "pp-dot" }), el("i", { class: "pp-dot" })]
        : [el("i", { class: "pp-live" })];
    }
    return BAR_HEIGHTS.map((h) => {
      const bar = el("i", { class: "pp-bar" });
      bar.style.height = `${h}px`;
      return bar;
    });
  }

  function pillPreviewNode(theme, showColour) {
    const node = V.ui.el("span", { class: "pill-preview", data: { theme } });
    node.append(...pillArt(theme, showColour));
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
    const labels = Object.fromEntries(themes.map((t) => [t.value, t.label]));
    V.ui.syncRadios($("#theme-grid"), {
      values: themes.map((t) => t.value),
      checked: cfg.pill_theme || "classic",
      cls: "theme-opt",
      build: (value) => V.ui.el("button", { type: "button", data: { value } },
        V.ui.el("span", { class: "theme-art" }, pillPreviewNode(value, false)),
        V.ui.el("span", { class: "theme-name", text: labels[value] })),
    });
  }

  /* ----- appearance thumbnails -----

     An appearance choice is the one control that should show its own result,
     so it is picked by picture rather than from a <select>. Built here (not
     in index.html) so the active state has a single source of truth, exactly
     like the pill grid above. */

  const APPEARANCES = [
    { value: "system", label: "Match system" },
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
  ];

  function thumbNode(mode) {
    return V.ui.el("span", { class: "thumb-art", data: { mode }, "aria-hidden": "true" },
      V.ui.el("i"), V.ui.el("b"));
  }

  /* Learning reads other apps' text, so it can't be on without screen context;
     the switches say so rather than claim a state the app isn't in. */
  function showContext(cfg) {
    const $ = V.ui.$;
    const context = $("#screen-context-toggle");
    if (!context) return;
    const on = cfg.use_screen_context !== false;
    const learning = on && cfg.learn_from_edits !== false;
    context.checked = on;
    $("#learn-toggle").checked = learning;
    $("#learn-toggle").disabled = !on;
    $("#auto-add-toggle").checked = learning && cfg.dictionary_auto_add !== false;
    $("#auto-add-toggle").disabled = !learning;
  }

  async function saveContextToggle(e, key, label) {
    const res = await V.ui.tryApi(
      V.api().set_config({ [key]: e.target.checked }), `Save ${label}`);
    if (!res) { e.target.checked = !e.target.checked; return; }
    showContext(res);
  }

  function renderAppearance() {
    const labels = Object.fromEntries(APPEARANCES.map((a) => [a.value, a.label]));
    V.ui.syncRadios(V.ui.$("#appearance-grid"), {
      values: APPEARANCES.map((a) => a.value),
      checked: appliedAppearance,
      cls: "thumb",
      build: (value) => V.ui.el("button", { type: "button", data: { value } },
        thumbNode(value), V.ui.el("span", { class: "thumb-name", text: labels[value] })),
    });
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

  /* The picker's Signature picture shows the colour too, so it stays a true
     picture of the pill while the wheel is being dragged. */
  function setColorUi(hex) {
    const $ = V.ui.$;
    const preview = $("#color-preview"), thumb = $("#wheel-thumb"), grid = $("#theme-grid");
    if (hex) {
      preview.style.setProperty("--mb-color", hex);
      if (grid) grid.style.setProperty("--mb-color", hex);
      $("#color-swatch").style.background = hex;
      $("#color-hex").textContent = hex;
      const { h, s } = hexToHs(hex);
      const a = h * Math.PI / 180;
      thumb.style.left = (WHEEL_R + Math.cos(a) * s * (WHEEL_R - 2)) + "px";
      thumb.style.top = (WHEEL_R + Math.sin(a) * s * (WHEEL_R - 2)) + "px";
      thumb.hidden = false;
    } else {
      preview.style.removeProperty("--mb-color");
      if (grid) grid.style.removeProperty("--mb-color");
      $("#color-swatch").style.removeProperty("background");
      $("#color-hex").textContent = "Theme default";
      thumb.hidden = true;
    }
  }

  async function renderColorWheel(known) {
    const $ = V.ui.$;
    const cfg = known || await V.ui.tryApi(V.api().get_config(), "Load settings");
    if (!cfg) return;
    const preview = $("#color-preview"), theme = cfg.pill_theme || "classic";
    if (preview.dataset.theme !== theme) {
      preview.dataset.theme = theme;
      preview.replaceChildren(...pillArt(theme, true));
    }
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

  /* Fixed to the window, because the scrolling pane would cut it off: it hangs
     from the button's right edge and opens upward when there's no room below. */
  function placeColorPopover(pop) {
    const opener = byId("color-open");
    if (!opener) return;
    const r = opener.getBoundingClientRect();
    const gap = 8, h = pop.offsetHeight, w = pop.offsetWidth;
    const below = r.bottom + gap + h <= window.innerHeight - gap;
    pop.style.left = `${Math.max(gap, r.right - w)}px`;
    pop.style.top = `${below ? r.bottom + gap : Math.max(gap, r.top - gap - h)}px`;
    pop.dataset.side = below ? "below" : "above";
  }

  function setColorExpanded(open) {
    const opener = byId("color-open");
    if (opener) opener.setAttribute("aria-expanded", String(open));
  }

  function openColorPopover() {
    const pop = byId("color-popover");
    if (!wheelDrawn) { drawColorWheel(); initColorWheel(); wheelDrawn = true; }
    pop.hidden = !pop.hidden;
    setColorExpanded(!pop.hidden);
    if (!pop.hidden) placeColorPopover(pop);
  }

  function closeColorPopover() {
    const pop = byId("color-popover");
    if (pop) pop.hidden = true;
    setColorExpanded(false);
  }

  /* known-applied appearance value, kept in sync by render() and the change
     handler below -- lets the handler revert the <select> to what's actually
     applied if set_appearance() fails, rather than leaving it showing a
     theme that was never applied or persisted. */
  let appliedAppearance = "system";

  /* The stored "keep model loaded" choice. Set when Settings renders (not
     read from the select, which holds the HTML default until then), so a
     failed save puts back what is really saved. */
  let appliedUnload = "min5";
  function showUnload(value) {
    appliedUnload = value || "min5";
    const sel = V.ui.$("#unload-select");
    if (sel) sel.value = appliedUnload;
  }

  /* ----- speech model list -----

     One row per model: size, plain description, install state and the
     buttons that apply. Everything the row says about this Mac (memory
     warning, Intel block) is decided by get_models; this only draws it.
     `benchmark` stays null until measured figures exist, and the row shows
     it only then -- never a claim about speed or accuracy without numbers. */

  const CHECK_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';

  function formatSize(bytes) {
    if (!bytes) return "";
    const mb = bytes / (1024 * 1024);
    return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
  }

  function benchmarkText(b) {
    if (!b) return "";
    const parts = [];
    if (typeof b.wer === "number") parts.push(`${(b.wer * 100).toFixed(1)}% word errors`);
    if (typeof b.median_s === "number") parts.push(`median ${b.median_s.toFixed(1)} s`);
    return parts.length ? `Measured: ${parts.join(", ")}.` : "";
  }

  function modelState(m) {
    if (m.active) return "In use";
    if (!m.installed) return "";
    return m.custom ? "Custom" : "Installed";
  }

  function downloadShare(d) {
    return d.total ? Math.min(1, d.done / d.total) : 0;
  }
  function downloadPercent(d) {
    return `${Math.floor(100 * downloadShare(d))}%`;
  }
  function downloadAmount(d) {
    return `${formatSize(d.done) || "0 MB"} of ${formatSize(d.total)}`;
  }

  function modelRow(m, download) {
    const el = V.ui.el;
    const busy = !!download && download.state === "downloading";
    const mine = busy && download.id === m.id;
    const size = formatSize(m.bytes);
    const desc = [m.description, size ? `${size}.` : "", benchmarkText(m.benchmark)]
      .filter(Boolean).join(" ");
    const state = modelState(m);
    const actions = [state ? el("span", { class: "pill", text: state }) : null];
    if (mine) {
      actions.push(el("span", { class: "pill model-percent", text: downloadPercent(download) }));
    } else if (!m.installed && !m.custom) {
      actions.push(el("button", { type: "button", class: "btn-quiet model-btn",
        data: { act: "download", id: m.id }, text: "Download",
        disabled: (m.blocked || busy) ? true : null }));
    }
    if (m.installed && !m.active) {
      actions.push(el("button", { type: "button", class: "btn-quiet model-btn",
        data: { act: "use", id: m.id }, text: "Use" }));
      actions.push(el("button", { type: "button", class: "btn-danger model-btn",
        data: { act: "delete", id: m.id, custom: m.custom ? "1" : "0" },
        text: "Delete" }));
    }
    let bar = null;
    if (mine) {
      const fill = el("i", { class: "model-bar-fill" });
      fill.style.transform = `scaleX(${downloadShare(download)})`;
      bar = el("span", { class: "model-bar", "aria-hidden": "true" }, fill);
    }
    return el("div", { class: "model-row" + (m.active ? " active" : ""),
        data: { id: m.id } },
      el("span", { class: "model-check", html: m.active ? CHECK_ICON : "" }),
      el("div", { class: "setting-copy" },
        el("h3", { text: m.label }),
        el("p", { class: "setting-desc", text: desc }),
        m.warning ? el("p", { class: "setting-desc", text: m.warning }) : null,
        m.blocked ? el("p", { class: "setting-desc", text: m.blocked }) : null,
        mine ? el("p", { class: "setting-desc model-progress",
          text: downloadAmount(download) }) : null),
      el("div", { class: "model-actions" }, actions),
      bar);
  }

  function renderModels(models) {
    const list = V.ui.$("#model-list");
    if (!list || !models || !models.models) return;
    // New buttons read "Delete", so none of them may count as already armed.
    if (modelArmed) disarmModelDelete(modelArmed.btn);
    list.replaceChildren(...models.models.map((m) => modelRow(m, models.download)));
    const sys = models.system || {};
    const note = V.ui.$("#model-system");
    if (note) {
      const bits = [sys.chip, sys.memory_gb ? `${sys.memory_gb} GB memory` : ""]
        .filter(Boolean);
      note.textContent = bits.length ? `This Mac: ${bits.join(", ")}.` : "";
    }
    reportDownloadOutcome(models.download);
    if (models.download && models.download.state === "downloading") pollDownload();
  }

  /* A download can finish while Settings is closed or before its first
     render. The server keeps the last outcome and numbers each download, so
     the page reports a given outcome once -- not on every later render. */
  let reportedDownloadSeq = 0;
  function reportDownloadOutcome(d) {
    if (!d || !d.seq || d.seq === reportedDownloadSeq) return;
    if (d.state === "error") V.ui.toast(d.error || "Download failed");
    else if (d.state === "done") V.ui.toast("Model downloaded");
    else return;
    reportedDownloadSeq = d.seq;
  }

  /* The row may be gone or redrawn between polls, so each part is looked up
     fresh and may be missing: closing the pop-up doesn't stop a download. */
  function paintDownload(d) {
    const $ = V.ui.$;
    const percent = $(".model-percent"), amount = $(".model-progress"), fill = $(".model-bar-fill");
    if (percent) percent.textContent = downloadPercent(d);
    if (amount) amount.textContent = downloadAmount(d);
    if (fill) fill.style.transform = `scaleX(${downloadShare(d)})`;
  }

  let downloadPoll = null;
  function pollDownload() {
    if (downloadPoll) return;
    downloadPoll = setTimeout(async () => {
      downloadPoll = null;
      const d = await V.ui.tryApi(V.api().get_download_progress(), "Check download");
      if (d && d.state === "downloading") {
        paintDownload(d);
        pollDownload();
        return;
      }
      reportDownloadOutcome(d);
      render();
    }, 1000);
  }

  /* A Custom model is the user's own file, and deleting it is permanent, so
     it asks twice -- the same arm/confirm as Clear history. Registry models
     can be downloaded again and stay one click. */
  let modelArmed = null;  // { id, at, timer, btn }

  function disarmModelDelete(btn) {
    if (modelArmed) clearTimeout(modelArmed.timer);
    modelArmed = null;
    if (btn) { btn.classList.remove("armed"); btn.textContent = "Delete"; }
  }

  async function onModelClick(e) {
    const btn = e.target.closest(".model-btn");
    if (!btn || !btn.dataset || !btn.dataset.act) return;
    const { act, id } = btn.dataset;
    if (act === "use") {
      if (!await V.ui.tryApi(V.api().set_config({ model: id }), "Save model")) return;
      V.ui.toast("Model changed — applies from your next dictation");
    } else if (act === "download") {
      const res = await V.ui.tryApi(V.api().download_model(id), "Download model");
      if (res && res.error) V.ui.toast(res.error);
    } else if (act === "delete") {
      if (btn.dataset.custom === "1") {
        if (!modelArmed || modelArmed.id !== id) {
          disarmModelDelete(modelArmed && modelArmed.btn);
          btn.classList.add("armed");
          btn.textContent = "Click again to delete the file";
          modelArmed = { id, btn, at: Date.now(),
            timer: setTimeout(() => disarmModelDelete(btn), 4000) };
          return;
        }
        if (Date.now() - modelArmed.at < 600) return;  // a double-click isn't consent
        disarmModelDelete(btn);
      }
      const res = await V.ui.tryApi(V.api().delete_model(id), "Delete model");
      if (res && res.error) V.ui.toast(res.error);
    }
    render();
  }

  /* ----- push-to-talk key ----- */

  /* A keycap and a name. A key with no symbol is just its name, because a blank
     keycap would say nothing. */
  function showKey(display) {
    const $ = V.ui.$;
    const name = $("#hotkey-current"), cap = $("#hotkey-cap");
    const key = V.ui.keyLabel(display);
    if (name) name.textContent = key.name;
    if (cap) {
      cap.textContent = key.symbol || "";
      cap.hidden = !key.symbol;
    }
  }

  /* The sentence comes from Python; its colour from state, so "push-to-talk
     not running" reads as needing you and "active" stays quiet. */
  function renderHotkeyStatus(status) {
    const node = V.ui.$("#hotkey-status");
    node.textContent = status.status;
    // A slow tap is undecided, not broken, so it isn't asking for anything yet.
    const starting = status.health === "starting";
    node.dataset.state = starting ? "busy" : (status.state || "unknown");
    clearTimeout(statusRecheck);
    if (starting && isOpen) statusRecheck = setTimeout(recheckStatus, STATUS_RECHECK_MS);
  }

  /* Nothing else tells this row when the tap makes up its mind. */
  const STATUS_RECHECK_MS = 1000;
  let statusRecheck = null;

  async function recheckStatus() {
    if (!isOpen || capturing) return;
    const status = await V.ui.tryApi(V.api().get_hotkey_status(), "Load shortcut status");
    if (status && isOpen && !capturing) renderHotkeyStatus(status);
  }

  /* The app stops listening for up to 8 s and takes the next key it sees, from
     anywhere, so while it waits nothing else here may change: Esc belongs to
     the capture (Python treats it as cancel), and the pop-up stays put. */
  let capturing = false;
  const capture = V.ui.captureGuard();

  function setListening(on) {
    const control = byId("key-control");
    if (control) control.classList.toggle("is-listening", on);
    const cap = byId("hotkey-cap");
    if (on && cap) cap.hidden = true;
    const name = byId("hotkey-current");
    if (on && name) name.textContent = "Press a key…";
  }

  async function captureKey() {
    if (capturing || capture.recent()) return;
    capturing = true;
    const btn = byId("hotkey-capture"), dialog = byId("settings-dialog");
    btn.disabled = true;
    setListening(true);
    if (dialog) dialog.focus();  // a disabled button can't hold it
    try {
      const res = await V.api().capture_hotkey();
      if (res.error) V.ui.toast(res.error);
      else if (!res.cancelled) V.ui.toast(`Push-to-talk key set to ${res.label}`);
    } catch (err) {
      V.ui.toast(`Change key failed: ${err.message}`);
    } finally {
      capturing = false;
      capture.ended();
      setListening(false);
      btn.disabled = false;
      if (isOpen) btn.focus();
      await render();
    }
  }

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
        renderAppearance();
        $("#launch-toggle").checked = !!cfg.launch_at_login;
        showContext(cfg);
        showUnload(cfg.model_unload);
      }
      // While capturing, the key's own row is saying "Press a key…", and the
      // status reads as off because the listener is stopped on purpose.
      if (!capturing) {
        if (hotkey) showKey(hotkey.display);
        if (status) renderHotkeyStatus(status);
      }

      if (models && models.models) renderModels(models);

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

  /* An armed button must not survive being looked away from: a click on it
     later would erase something the user no longer has in mind. */
  function disarmEverything() {
    const clear = byId("clear-history");
    if (clear && clear.dataset.armed === "1") disarmClearHistory(clear);
    if (modelArmed) disarmModelDelete(modelArmed.btn);
  }

  /* ----- the pop-up ----- */

  const SECTIONS = ["dictation", "models", "key", "appearance", "privacy"];
  const LEAVE_MS = 140;  // keep in step with .settings-scrim's closing transition

  let isOpen = false;
  let section = SECTIONS[0];     // reopens where it was left, until the app restarts
  let openedFrom = null;
  let leaveTimer = null;
  let pressedScrim = false;

  function showSection(name) {
    section = name;
    document.querySelectorAll(".settings-section").forEach((s) => {
      s.hidden = s.dataset.section !== name;
    });
    document.querySelectorAll(".settings-nav-item").forEach((b) => {
      const on = b.dataset.section === name;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    const body = byId("settings-body");
    if (body) body.scrollTop = 0;
    closeColorPopover();
    disarmEverything();
  }

  /* The page behind must be out of reach, or Tab walks into it. */
  function setPageInert(inert) {
    const shell = V.ui.$(".shell");
    if (shell) shell.inert = inert;
  }

  function activeNavItem() {
    return Array.from(document.querySelectorAll(".settings-nav-item"))
      .find((b) => b.dataset.section === section);
  }

  /* opts.section names one of SECTIONS. Anything else is ignored, so a caller
     can't open onto a blank pane. */
  function open(opts) {
    const wanted = opts && opts.section;
    showSection(SECTIONS.includes(wanted) ? wanted : section);
    render();
    if (isOpen) return;
    isOpen = true;
    openedFrom = document.activeElement;
    clearTimeout(leaveTimer);  // reopened mid-close: carry on from where it is
    const scrim = byId("settings-scrim");
    scrim.hidden = false;
    void scrim.offsetWidth;    // commit the closed look so the transition runs
    scrim.classList.add("is-open");
    setPageInert(true);
    const first = activeNavItem();
    if (first) first.focus();
  }

  function close() {
    if (!isOpen || capturing) return;
    isOpen = false;
    closeColorPopover();
    disarmEverything();
    const scrim = byId("settings-scrim");
    scrim.classList.remove("is-open");
    leaveTimer = setTimeout(() => { scrim.hidden = true; }, LEAVE_MS);
    setPageInert(false);
    if (openedFrom && openedFrom.focus) openedFrom.focus();
    openedFrom = null;
    // main.js moves the sidebar highlight back and refreshes the page behind.
    window.dispatchEvent(new Event("voisr:settings-closed"));
  }

  function paletteOpen() {
    const palette = byId("palette");
    return !!palette && !palette.hidden;
  }

  /* Esc peels one layer: the capture keeps it, then the colour popover takes
     it, then the pop-up does. The palette sits above all of this and handles
     its own Esc, marking it with preventDefault. */
  function onEscape(e) {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    const pop = byId("color-popover");
    if (pop && !pop.hidden) {
      closeColorPopover();
      const opener = byId("color-open");
      if (opener && isOpen) opener.focus();
      return;
    }
    if (!isOpen || capture.recent() || paletteOpen()) return;
    close();
  }

  const isPaletteKey = (e) =>
    (e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === "k";

  /* The pages listen for keys on the document, and History's arrows, Return
     and Backspace act on its selected row. A pop-up over History must not
     delete a dictation nobody can see, so keys stop here. ⌘K alone goes on,
     because the palette works from anywhere. */
  function onScrimKey(e) {
    if (e.key === "Tab") V.ui.walkTab(e, byId("settings-scrim"));
    else if (e.key === "Escape") onEscape(e);
    if (!isPaletteKey(e)) e.stopPropagation();
  }

  /* The same rule for a key with nowhere better to land, such as when focus has
     fallen back to the body. Runs on the way down, so it gets there first. */
  function onStrayKey(e) {
    if (!isOpen || isPaletteKey(e) || paletteOpen()) return;
    if (byId("settings-scrim").contains(e.target)) return;
    e.stopPropagation();
    if (e.key === "Tab") V.ui.walkTab(e, byId("settings-scrim"));
    else if (e.key === "Escape") onEscape(e);
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
    const scrim = byId("settings-scrim");
    scrim.addEventListener("keydown", onScrimKey);
    document.addEventListener("keydown", onStrayKey, true);
    // A drag from the wheel or a selection can end out here, and that isn't
    // asking to close: the press has to have started on the scrim too.
    scrim.addEventListener("pointerdown", (e) => { pressedScrim = e.target === scrim; });
    scrim.addEventListener("click", (e) => {
      if (e.target !== scrim || !pressedScrim) return;
      pressedScrim = false;
      // The first click out only closes the colour popover (see below).
      if (!byId("color-popover").hidden) return;
      close();
    });
    byId("settings-nav").addEventListener("click", (e) => {
      const btn = e.target.closest(".settings-nav-item");
      if (btn && !capturing) showSection(btn.dataset.section);
    });
    // It hangs from the button, so it goes when the button moves.
    byId("settings-body").addEventListener("scroll", closeColorPopover);
    window.addEventListener("resize", closeColorPopover);

    byId("hotkey-capture").addEventListener("click", captureKey);

    /* Delegated, because renderAppearance() replaces the buttons on every
       render — a listener bound to each button would be lost with it. A
       failed write re-renders from appliedAppearance, so the grid can never
       be left highlighting a theme that was neither applied nor persisted. */
    const pickAppearance = V.ui.serial(async (btn) => {
      const next = btn.dataset.value;
      if (next === appliedAppearance) return;
      const res = await V.ui.tryApi(V.api().set_appearance(next), "Change theme");
      if (!res) { renderAppearance(); return; }
      appliedAppearance = next;
      renderAppearance();
      V.applyAppearance(next);
      V.ui.toast(`Theme: ${next}`);
    });
    const appearanceGrid = byId("appearance-grid");
    appearanceGrid.addEventListener("click", (e) => {
      const btn = e.target.closest(".thumb");
      if (btn) pickAppearance(btn);
    });
    V.ui.radioKeys(appearanceGrid, ".thumb", pickAppearance);
    // The palette's "Toggle appearance" changes the theme without this view:
    // follow it, or the grid highlights a theme that is no longer applied.
    window.addEventListener("voisr:appearance", () => {
      if (!V.appearancePref) return;
      const pref = V.appearancePref();
      if (pref === appliedAppearance) return;  // our own pick: already shown
      appliedAppearance = pref;
      renderAppearance();
    });

    byId("model-list").addEventListener("click", onModelClick);

    for (const [id, key, label] of [
      ["screen-context-toggle", "use_screen_context", "screen context"],
      ["learn-toggle", "learn_from_edits", "learning"],
      ["auto-add-toggle", "dictionary_auto_add", "auto-add"],
    ]) {
      const box = byId(id);
      if (box) box.addEventListener("change", (e) => saveContextToggle(e, key, label));
    }
    byId("launch-toggle").addEventListener("change", async (e) => {
      const res = await V.ui.tryApi(
        V.api().set_launch_at_login(e.target.checked), "Save launch at login");
      if (!res) { e.target.checked = !e.target.checked; return; }
      V.ui.toast(e.target.checked ? "Voisr will start at login" : "Launch at login off");
    });

    const unloadSelect = byId("unload-select");
    if (unloadSelect) {
      unloadSelect.addEventListener("change", async (e) => {
        const next = e.target.value;
        const res = await V.ui.tryApi(
          V.api().set_config({ model_unload: next }), "Save keep model loaded");
        if (!res) { e.target.value = appliedUnload; return; }  // the stored one
        appliedUnload = next;
        V.ui.toast("Model unload saved");
      });
    }

    byId("color-open").addEventListener("click", openColorPopover);

    /* click-outside / Escape dismissal for the colour popover -- registered
       once here (init, called once), not inside openColorPopover, so repeated
       opens never stack extra listeners. The click listener bails out early
       on any click still inside the popover or on the swatch button that
       opened it, so the very click that opens the popover (which bubbles to
       document right after) can never immediately close it again. Mirrors the
       scrim/Escape pattern in palette.js. */
    document.addEventListener("click", (e) => {
      const pop = byId("color-popover");
      if (pop.hidden) return;
      if (pop.contains(e.target) || e.target.closest("#color-open")) return;
      closeColorPopover();
    });
    document.addEventListener("keydown", onEscape);

    byId("clear-history").addEventListener("click", async () => {
      const btn = byId("clear-history");
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
  V.views.settings = { open, close, isOpen: () => isOpen, captureKey, showKey, showContext,
    showUnload, renderModels, renderAppearance, renderHotkeyStatus, init, render, renderThemes,
    renderColorWheel, initColorWheel, openColorPopover };
})(window.Voisr);
