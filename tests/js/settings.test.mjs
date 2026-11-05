import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* The load itself is the assertion here. settings.js used to bind every
   listener at module-parse time -- `document.getElementById("appearance-
   select").addEventListener(...)` ran the instant the file was evaluated,
   which threw outside a browser and kept this whole module out of the Node
   harness. Now that the bindings live in init(), like home/history/
   dictionary/palette, the module parses against the stub DOM and can be
   exercised here at all. If a revert moves the bindings back to parse
   time, this test throws on the very first line of the load. */

test("settings.js loads without touching the DOM at parse time", () => {
  let env;
  assert.doesNotThrow(
    () => { env = loadVoisr(["js/ui.js", "js/views/settings.js"]); },
    "settings.js must not touch the DOM while it parses",
  );

  const settings = env.V.views.settings;
  assert.ok(settings, "V.views.settings was never attached");
  assert.equal(typeof settings.init, "function",
    "settings must export init() so main.js can bind its listeners once");
  assert.equal(typeof settings.render, "function",
    "settings must still export render()");
});

/* ---------- settings.js: init()'s document-level listeners ----------

   H2's hand-off flagged these as untestable: init() binds its arm/disarm
   dwell guard and its colour-popover outside-click/Escape dismissal onto
   `document`, and the old fake DOM's document.addEventListener was a no-op
   with no way to dispatch back into it. H6 extends tests/js/dom.mjs
   (document/window listener recording + dispatch, node.contains/closest,
   parent links) specifically to unlock these. */

const SETTINGS_IDS = ["appearance-grid", "model-list", "launch-toggle", "settings-scrim", "settings-nav",
                       "settings-body", "hotkey-capture", "settings-dialog",
                       "color-open", "color-popover", "clear-history", "toast",
                       "unload-select"];

function mountSettings(t, api, apis = ["setTimeout"]) {
  t.mock.timers.enable({ apis });
  const env = loadVoisr(["js/ui.js", "js/api.js", "js/views/settings.js"]);
  env.window.pywebview = { api: { clear_history: async () => ({ removed: 0 }), ...api } };

  const nodes = {};
  for (const id of SETTINGS_IDS) {
    nodes[id] = makeNode(id === "launch-toggle" ? "input" : "div");
    env.register(id, nodes[id]);
  }
  nodes["color-popover"].hidden = true;

  env.V.views.settings.init();
  return { V: env.V, window: env.window, document: env.document, register: env.register, ...nodes };
}

const settle = () => new Promise((r) => setImmediate(r));

test("clear-history arms on the first click, rejects a confirm at 599ms, then clears once past the 600ms dwell boundary", async (t) => {
  const calls = [];
  // Mock Date too (not just setTimeout): the guard reads Date.now() directly
  // (`Date.now() - clearHistoryArmedAt < 600`), so ticking the virtual clock
  // is what actually moves that comparison -- a real 650ms sleep proved only
  // that *some* delay past 0 confirms, not that 600 is the boundary. This
  // also means the test no longer spends real wall-clock time waiting.
  const h = mountSettings(t, { clear_history: async () => { calls.push(1); return { removed: 3 }; } },
    ["setTimeout", "Date"]);
  const btn = h["clear-history"];

  // Wired by A4: main.js's voisr:new-dictation listener refreshes whichever
  // of Home/History is on screen -- clearing must fire the same event.
  let historyChanged = 0;
  h.window.addEventListener("voisr:new-dictation", () => { historyChanged += 1; });

  btn.dispatch("click");
  await settle();
  assert.equal(btn.dataset.armed, "1", "first click arms");
  assert.match(btn.textContent, /Click again/);
  assert.equal(calls.length, 0, "arming alone must never clear anything");

  t.mock.timers.tick(599);   // one tick short of the 600ms dwell -- pins the lower boundary
  btn.dispatch("click");
  await settle();
  assert.equal(calls.length, 0, "a confirm at 599ms must still be rejected");
  assert.equal(btn.dataset.armed, "1", "still armed -- the too-fast click neither confirmed nor disarmed");

  t.mock.timers.tick(1);   // lands exactly on the 600ms boundary (not 601+) -- pins the upper side too

  btn.dispatch("click");
  await settle();

  assert.equal(calls.length, 1, "a click at exactly the 600ms dwell must clear history");
  assert.equal(btn.dataset.armed, "0", "disarmed once confirmed");
  assert.equal(historyChanged, 1,
    "a successful clear must tell Home/History to refresh (voisr:new-dictation)");
});

test("the clear-history disarm timer fires on its own, so a later single click only re-arms", async (t) => {
  const calls = [];
  const h = mountSettings(t, { clear_history: async () => { calls.push(1); return { removed: 1 }; } });
  const btn = h["clear-history"];

  btn.dispatch("click");
  await settle();
  assert.equal(btn.dataset.armed, "1");

  t.mock.timers.tick(4000);   // the tracked disarm timer
  assert.equal(btn.dataset.armed, "0", "auto-disarmed after the dwell window");
  assert.match(btn.textContent, /^Clear/);

  btn.dispatch("click");
  await settle();

  assert.equal(calls.length, 0, "a single click after auto-disarm must only re-arm, not confirm");
  assert.equal(btn.dataset.armed, "1", "re-armed by this click");
});

/* ---------- settings.js: the colour wheel ----------

   drawColorWheel(), setColorUi() and initColorWheel() all reach their
   elements through `V.ui.$`, so while the fake DOM stubbed
   document.querySelector as `() => null` none of them could be called from
   Node at all -- `const cv = $("#color-wheel")` handed back null and the next
   line threw `cv.addEventListener is not a function`. The pointercancel
   revert below in particular had only ever been checked by hand in a browser.
   With "#id" now resolving through the registry, all three run here. */

const WHEEL_IDS = ["color-wheel", "color-preview", "wheel-thumb", "color-swatch",
                   "color-hex", "color-reset", "color-popover", "toast"];

function mountColorWheel(t, api) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/views/settings.js"]);
  const nodes = {};
  for (const id of WHEEL_IDS) {
    nodes[id] = makeNode(id === "color-wheel" ? "canvas" : "div");
    env.register(id, nodes[id]);
  }
  // A real 200x200 box at the origin, so pick()'s client-coordinate maths has
  // something to work against. makeNode's default is an all-zero rect (what a
  // detached element really reports), which would collapse every pick to the
  // wheel's centre.
  nodes["color-wheel"].getBoundingClientRect = () =>
    ({ left: 0, top: 0, right: 200, bottom: 200, width: 200, height: 200 });
  const calls = [];
  env.V.api = () => ({
    get_config: async () => ({ pill_theme: "ocean", pill_color: "#00FF00" }),
    set_config: async (u) => { calls.push(u); return { ok: true }; },
    ...api,
  });
  return { ...env, ...nodes, calls };
}

/* Pointer coordinates used throughout: (200, 100) is due east of the wheel's
   centre (100, 100), i.e. hue 0 at very nearly full saturation --
   r = min(hypot(100, 0), WHEEL_R - 2) = 98, s = 0.98 -- which hsvToHex maps
   to #FF0505. Pinning the literal hex pins the geometry and the HSV maths
   together; a rect or radius-clamp regression moves it. */
const PICKED = "#FF0505";
const pointerAt = (x, y) => ({ clientX: x, clientY: y, pointerId: 1 });

test("a drag on the colour wheel paints a live preview without saving anything", async (t) => {
  const h = mountColorWheel(t, {});
  h.V.views.settings.initColorWheel();

  h["color-wheel"].dispatch("pointerdown", pointerAt(200, 100));

  assert.equal(h["color-hex"].textContent, PICKED);
  assert.equal(h["color-swatch"].style.background, PICKED);
  assert.equal(h["color-preview"].style.getPropertyValue("--mb-color"), PICKED);
  assert.equal(h["wheel-thumb"].hidden, false, "the thumb shows where on the wheel we are");
  assert.equal(h.calls.length, 0, "a drag in progress must not write config -- that happens on release");

  // pick() runs BEFORE setPointerCapture precisely so a capture failure cannot
  // swallow it; makeNode has no setPointerCapture, so this dispatch also
  // exercises that ordering -- the throw is caught and the pick still landed.
});

test("a cancelled colour drag reverts the preview to what is actually stored, and saves nothing", async (t) => {
  // H6's pointercancel fix, until now verified only in a browser. An
  // interrupted gesture (capture lost, system gesture takes over) never
  // reaches pointerup, so nothing was ever confirmed to config -- leaving the
  // half-dragged colour on screen would be a lie about what the pill will do.
  const h = mountColorWheel(t, {});
  h.V.views.settings.initColorWheel();

  h["color-wheel"].dispatch("pointerdown", pointerAt(200, 100));
  assert.equal(h["color-hex"].textContent, PICKED, "sanity: the drag painted its preview");

  h["color-wheel"].dispatch("pointercancel", pointerAt(200, 100));
  await settle();

  assert.equal(h["color-hex"].textContent, "#00FF00",
    "the cancelled colour must be replaced by the stored one, not left on screen");
  assert.equal(h["color-swatch"].style.background, "#00FF00");
  assert.equal(h["color-preview"].dataset.theme, "ocean",
    "the revert goes through renderColorWheel, which repaints the theme too");
  assert.equal(h.calls.length, 0, "a cancelled gesture must never write config");
});

test("a cancelled drag that never started is ignored, so a stray pointercancel cannot repaint the wheel", async (t) => {
  const h = mountColorWheel(t, {});
  h.V.views.settings.initColorWheel();
  h["color-hex"].textContent = "untouched";

  h["color-wheel"].dispatch("pointercancel", pointerAt(200, 100));
  await settle();

  assert.equal(h["color-hex"].textContent, "untouched",
    "the `if (!dragging) return` guard must hold -- no drag, no revert");
});

test("releasing a colour drag is what actually saves it, and confirms with the hex", async (t) => {
  const h = mountColorWheel(t, {});
  h.V.views.settings.initColorWheel();

  h["color-wheel"].dispatch("pointerdown", pointerAt(200, 100));
  h["color-wheel"].dispatch("pointerup", {});
  await settle();

  assert.deepEqual(h.calls, [{ pill_color: PICKED }], "release is what writes config");
  assert.equal(h["color-hex"].textContent, PICKED, "and the saved colour stays on screen");
  assert.match(h.toast.children.map((c) => c.textContent).join(""), /Bar color: #FF0505/);
});

test("a save that fails repaints the wheel from what is really stored, rather than leaving its colour up", async (t) => {
  // Same rule the pointercancel revert follows: pick()'s live preview is only
  // trustworthy once it is confirmed written to config.
  const h = mountColorWheel(t, { set_config: async () => ({ error: "disk full" }) });
  h.V.views.settings.initColorWheel();

  h["color-wheel"].dispatch("pointerdown", pointerAt(200, 100));
  h["color-wheel"].dispatch("pointerup", {});
  await settle();

  assert.equal(h["color-hex"].textContent, "#00FF00",
    "a save that failed must not leave its colour showing as if it took");
  assert.equal(h["color-swatch"].style.background, "#00FF00");
  assert.match(h.toast.children.map((c) => c.textContent).join(""),
    /Save bar color failed: disk full/);
});

test("the colour reset clears the custom colour back to the theme default", async (t) => {
  const h = mountColorWheel(t, {});
  h.V.views.settings.initColorWheel();
  h["color-wheel"].dispatch("pointerdown", pointerAt(200, 100));
  assert.equal(h["wheel-thumb"].hidden, false, "sanity: a colour is showing");

  h["color-reset"].dispatch("click");
  await settle();

  assert.deepEqual(h.calls, [{ pill_color: null }]);
  assert.equal(h["color-hex"].textContent, "Theme default");
  assert.equal(h["color-swatch"].style.background, undefined,
    "the swatch goes back to the stylesheet's own colour");
  assert.equal(h["color-preview"].style.getPropertyValue("--mb-color"), "",
    "the custom-colour variable is removed, not set to an empty-looking value");
  assert.equal(h["wheel-thumb"].hidden, true, "no colour, nothing to point at");
});

test("opening the popover for the first time paints an HSV disc: white at the centre, saturated at the rim, transparent outside", async (t) => {
  // drawColorWheel() is deliberately not exported, so this drives it the way
  // the app does -- openColorPopover() draws the canvas and binds the wheel
  // on first open only. That first-open path is itself only reachable here
  // now: it calls both drawColorWheel() and initColorWheel(), each of which
  // starts with a `V.ui.$` lookup.
  const h = mountColorWheel(t, {});
  h["color-popover"].hidden = true;
  // Just enough canvas to run the pixel loop -- createImageData/putImageData
  // is the whole surface drawColorWheel touches.
  let painted = null;
  h["color-wheel"].getContext = () => ({
    createImageData: (w, hgt) => ({ width: w, height: hgt, data: new Uint8ClampedArray(w * hgt * 4) }),
    putImageData: (img) => { painted = img; },
  });

  h.V.views.settings.openColorPopover();

  assert.equal(h["color-popover"].hidden, false, "the popover opened");
  assert.ok(painted, "the wheel was never handed back to the canvas");
  const px = (x, y) => Array.from(painted.data.slice((y * 200 + x) * 4, (y * 200 + x) * 4 + 4));

  assert.deepEqual(px(100, 100), [255, 255, 255, 255], "s=0 at the centre is white, fully opaque");
  const [r, g, b, a] = px(198, 100);   // due east, r=98 -> hue 0, near-full saturation
  assert.equal(a, 255);
  assert.ok(r === 255 && g < 20 && b < 20, `expected saturated red at the rim, got ${[r, g, b]}`);
  assert.equal(px(0, 0)[3], 0, "a corner is outside the disc and must be fully transparent");
});

test("outside click and Escape dismiss the colour popover; a click inside it or on its opener does not", async (t) => {
  const h = mountSettings(t, {});
  const pop = h["color-popover"], colorOpen = h["color-open"];

  // Set the open state directly rather than clicking #color-open: that path
  // draws the 200x200 wheel canvas on first open, which the fake DOM has no
  // reason to model -- the dismissal logic below only ever reads
  // pop.hidden, so this is a faithful way to reach "already open".
  pop.hidden = false;
  h.document.dispatch("click", { target: makeNode("div") });
  assert.equal(pop.hidden, true, "a click outside the popover and its opener closes it");

  pop.hidden = false;
  h.document.dispatch("click", { target: colorOpen });
  assert.equal(pop.hidden, false,
    "a click on the button that opened it must not immediately re-close it (it bubbles right after opening)");

  pop.hidden = false;
  const insideChild = makeNode("button");
  pop.append(insideChild);
  h.document.dispatch("click", { target: insideChild });
  assert.equal(pop.hidden, false, "a click inside the popover itself does not close it");

  h.document.dispatch("keydown", { key: "a" });
  assert.equal(pop.hidden, false, "a non-Escape key does nothing");

  h.document.dispatch("keydown", { key: "Escape" });
  assert.equal(pop.hidden, true, "Escape closes the open popover");
});

/* ---------- appearance thumbnails ----------

   The grid replaces its own buttons on every render, so the click listener
   is delegated onto the grid; a per-button listener would be thrown away
   with the buttons. These drive it the way dom.mjs intends -- dispatching
   on the grid with the button as the event target. */

function mountAppearance(t, api) {
  const env = mountSettings(t, {
    get_config: async () => ({ appearance: "system", pill_theme: "classic" }),
    set_appearance: async (mode) => ({ ok: true, appearance: mode }),
    ...api,
  });
  // main.js owns applyAppearance; settings.js only calls it
  const applied = [];
  env.V.applyAppearance = (mode) => applied.push(mode);
  env.V.views.settings.renderAppearance();
  return { ...env, applied };
}

const labelsOf = (grid) => grid.children.map((b) => b.dataset.value);
const activeOf = (grid) =>
  (grid.children.find((b) => b.className.includes("active")) || {}).dataset?.value;

test("the appearance grid offers all three modes as pictures, with the stored one marked", async (t) => {
  const s = mountAppearance(t, {});

  assert.deepEqual(labelsOf(s["appearance-grid"]), ["system", "light", "dark"]);
  // each is a picture of the theme, not a text row
  assert.ok(s["appearance-grid"].children.every((b) =>
    b.children.some((c) => c.className === "thumb-art")));
  assert.equal(activeOf(s["appearance-grid"]), "system");
  // M11 (2026-10-12 audit): one-of-three is a radio group, not toggles
  assert.equal(s["appearance-grid"].children[0].getAttribute("role"), "radio");
  assert.equal(s["appearance-grid"].children[0].getAttribute("aria-checked"), "true");
  assert.equal(s["appearance-grid"].children[2].getAttribute("aria-checked"), "false");
  // roving tabindex: Tab lands on the checked option only
  assert.deepEqual(s["appearance-grid"].children.map((b) => b.getAttribute("tabindex")),
    ["0", "-1", "-1"]);
});

test("picking a theme saves it, applies it, and moves the highlight", async (t) => {
  const saved = [];
  const s = mountAppearance(t, {
    set_appearance: async (mode) => { saved.push(mode); return { ok: true }; },
  });

  const dark = s["appearance-grid"].children[2];
  s["appearance-grid"].dispatch("click", { target: dark });
  await settle();

  assert.deepEqual(saved, ["dark"]);
  assert.deepEqual(s.applied, ["dark"]);
  assert.equal(activeOf(s["appearance-grid"]), "dark");
});

test("a failed save leaves the grid on the theme that is actually applied", async (t) => {
  // The <select> this replaced had the same contract: a write that fails
  // must never leave the control showing a theme that was neither applied
  // nor persisted.
  const s = mountAppearance(t, { set_appearance: async () => ({ error: "read-only config" }) });

  s["appearance-grid"].dispatch("click", { target: s["appearance-grid"].children[1] });
  await settle();

  assert.equal(activeOf(s["appearance-grid"]), "system",
    "the highlight snaps back to what is really stored");
  assert.deepEqual(s.applied, [], "nothing was applied");
});

test("clicking the already-active theme is a no-op, not a redundant write", async (t) => {
  const saved = [];
  const s = mountAppearance(t, {
    set_appearance: async (mode) => { saved.push(mode); return { ok: true }; },
  });

  s["appearance-grid"].dispatch("click", { target: s["appearance-grid"].children[0] });
  await settle();

  assert.deepEqual(saved, []);
});

test("a click on the grid's own padding does nothing", async (t) => {
  const saved = [];
  const s = mountAppearance(t, {
    set_appearance: async (mode) => { saved.push(mode); return { ok: true }; },
  });

  s["appearance-grid"].dispatch("click", { target: s["appearance-grid"] });
  await settle();

  assert.deepEqual(saved, []);
});


/* ---------- M11 (2026-10-12 audit): keyboard and stale-state fixes ---------- */

test("picking a theme keeps the same buttons, so keyboard focus stays on the choice", async (t) => {
  // replaceChildren on every render dropped focus to <body> after a pick
  const s = mountAppearance(t, {});
  const before = [...s["appearance-grid"].children];
  s["appearance-grid"].dispatch("click", { target: before[2] });
  await settle();
  assert.ok(s["appearance-grid"].children.every((b, i) => b === before[i]),
    "the grid was rebuilt instead of updated");
  assert.equal(activeOf(s["appearance-grid"]), "dark");
  assert.equal(before[2].getAttribute("tabindex"), "0");
});

test("arrow keys move the theme choice like any radio group", async (t) => {
  const saved = [];
  const s = mountAppearance(t, {
    set_appearance: async (mode) => { saved.push(mode); return { ok: true }; },
  });
  const [system, light, dark] = s["appearance-grid"].children;
  s["appearance-grid"].dispatch("keydown", { key: "ArrowRight", target: system, preventDefault() {} });
  await settle();
  s["appearance-grid"].dispatch("keydown", { key: "ArrowLeft", target: light, preventDefault() {} });
  await settle();
  s["appearance-grid"].dispatch("keydown", { key: "ArrowLeft", target: system, preventDefault() {} });
  await settle();
  assert.deepEqual(saved, ["light", "system", "dark"], "wraps from first to last");
  assert.equal(activeOf(s["appearance-grid"]), "dark");
  void dark;
});

test("the theme picker follows an appearance change made elsewhere (the palette)", async (t) => {
  const s = mountAppearance(t, {});
  s.V.appearancePref = () => "dark";
  globalThis.window.dispatch("voisr:appearance");
  assert.equal(activeOf(s["appearance-grid"]), "dark",
    "the highlighted option must be the one actually applied");
});

test("the push-to-talk status carries its state, so only a dead key asks for attention", async (t) => {
  // L8 (2026-10-12 audit): "push-to-talk not running" used to look like "active"
  const s = mountSettings(t, {});
  const node = makeNode("p");
  s.V.ui.$ = ((orig) => (sel) => (sel === "#hotkey-status" ? node : orig(sel)))(s.V.ui.$);
  s.V.views.settings.renderHotkeyStatus({ status: "active on F13", state: "listening" });
  assert.equal(node.dataset.state, "listening");
  s.V.views.settings.renderHotkeyStatus({ status: "push-to-talk not running", state: "off" });
  assert.equal(node.dataset.state, "off");
  assert.equal(node.textContent, "push-to-talk not running");
});

test("fast theme picks are saved in the order they were made", async (t) => {
  // overlapping set_appearance calls let the slower one win
  const saved = [];
  const pending = [];
  const s = mountAppearance(t, {
    set_appearance: (mode) => new Promise((r) => pending.push(() => { saved.push(mode); r({ ok: true }); })),
  });
  const [system, light] = s["appearance-grid"].children;
  s["appearance-grid"].dispatch("keydown", { key: "ArrowRight", target: system, preventDefault() {} });
  s["appearance-grid"].dispatch("keydown", { key: "ArrowRight", target: light, preventDefault() {} });
  await settle();
  assert.equal(pending.length, 1, "the second pick waits for the first");
  pending.shift()();
  await settle(); await settle();
  assert.equal(pending.length, 1);
  pending.shift()();
  await settle(); await settle();
  assert.deepEqual(saved, ["light", "dark"]);
  assert.deepEqual(s.applied, ["light", "dark"]);
  assert.equal(activeOf(s["appearance-grid"]), "dark");
});

test("the appearance event doesn't re-render a picker that already shows it", async (t) => {
  const s = mountAppearance(t, {});
  const btn = s["appearance-grid"].children[0];
  btn.setAttribute("tabindex", "sentinel");
  s.V.appearancePref = () => "system";
  globalThis.window.dispatch("voisr:appearance");
  assert.equal(btn.getAttribute("tabindex"), "sentinel");
});

/* ---------- settings.js: the speech model list ---------- */

const ROW = (over) => ({
  id: "x", label: "X", description: "desc", bytes: 487614201, installed: false,
  active: false, custom: false, warning: null, blocked: null, benchmark: null, ...over,
});

function buttons(node, out = []) {
  for (const k of node.children || []) {
    if (!k || typeof k !== "object") continue;
    if (k.tagName === "BUTTON" || k.tag === "button") out.push(k);
    buttons(k, out);
  }
  return out;
}

function textOf(node) {
  return [node.textContent || "", ...(node.children || []).map((k) =>
    typeof k === "string" ? k : textOf(k))].join(" ");
}

test("model list shows size and state, and Download only where it applies", (t) => {
  const s = mountSettings(t, {});
  s.V.views.settings.renderModels({
    models: [
      ROW({ id: "a", label: "Active", installed: true, active: true }),
      ROW({ id: "b", label: "Spare", installed: true }),
      ROW({ id: "c", label: "Remote" }),
      ROW({ id: "d", label: "Mine", installed: true, custom: true }),
    ],
    system: { chip: "Apple M1", memory_gb: 8 }, download: { state: "idle" },
  });
  const rows = s["model-list"].children;
  assert.equal(rows.length, 4);
  const acts = rows.map((r) => buttons(r).map((b) => b.dataset.act));
  assert.deepEqual(acts, [[], ["use", "delete"], ["download"], ["use", "delete"]]);
  assert.match(textOf(rows[0]), /465 MB/);
  assert.match(textOf(rows[0]), /In use/);
  assert.match(textOf(rows[1]), /Installed/);
  assert.doesNotMatch(textOf(rows[2]), /Installed/, "Download already says it isn't");
  assert.match(textOf(rows[3]), /Custom/);
  assert.notEqual(rows[0].children[0].innerHTML, "", "the model in use carries the tick");
  assert.equal(rows[1].children[0].innerHTML, "");
});

test("model list shows warnings, disables blocked downloads, and no invented figures", (t) => {
  const s = mountSettings(t, {});
  s.V.views.settings.renderModels({
    models: [
      ROW({ id: "w", warning: "Wants about 12 GB" }),
      ROW({ id: "i", blocked: "Needs an Apple Silicon Mac." }),
      ROW({ id: "m", benchmark: { wer: 0.082, median_s: 1.25 } }),
    ],
    system: {}, download: { state: "idle" },
  });
  const [w, i, m] = s["model-list"].children;
  assert.match(textOf(w), /Wants about 12 GB/);
  assert.match(textOf(i), /Apple Silicon/);
  assert.ok(buttons(i)[0].getAttribute("disabled") !== null);
  assert.ok(!/Measured/.test(textOf(w)), "no figure without a measurement");
  assert.equal(buttons(w)[0].getAttribute("disabled"), null,
    "a memory warning only warns: the download stays allowed");
  assert.match(textOf(m), /Measured: 8\.2% word errors, median 1\.3 s/);
});

test("a download in progress disables other Download buttons", (t) => {
  const s = mountSettings(t, { get_download_progress: async () => ({ state: "idle" }) });
  s.V.views.settings.renderModels({
    models: [ROW({ id: "a" }), ROW({ id: "b" })],
    system: {}, download: { state: "downloading", id: "a", done: 100, total: 200 },
  });
  const [a, b] = s["model-list"].children;
  assert.match(textOf(a), /50%/);
  assert.equal(buttons(a).length, 0, "the percent stands where the button was");
  assert.ok(buttons(b)[0].getAttribute("disabled") !== null);
});

test("download progress says how far, in the row, and keeps going on each poll", async (t) => {
  const MB = 1024 * 1024;
  let answer = { state: "downloading", id: "a", done: 300 * MB, total: 600 * MB };
  const s = mountSettings(t, {
    get_download_progress: async () => answer,
    get_config: async () => ({}), get_hotkey_status: async () => null,
    get_models: async () => null, hotkey_display: async () => null,
    get_pill_themes: async () => [],
  });
  s.V.views.settings.renderModels({
    models: [ROW({ id: "a" })], system: {},
    download: { state: "downloading", id: "a", done: 60 * MB, total: 600 * MB },
  });
  const row = s["model-list"].children[0];
  assert.match(textOf(row), /10%/);
  assert.match(textOf(row), /60 MB of 600 MB/);

  // $ finds these by class only inside the document, so put the row there
  s.document.body.append(s["model-list"]);
  t.mock.timers.tick(1000);
  await settle();
  assert.match(textOf(row), /50%/);
  assert.match(textOf(row), /300 MB of 600 MB/);
  const fill = (function find(n) {
    return n.className === "model-bar-fill" ? n
      : (n.children || []).map((k) => (k && typeof k === "object" ? find(k) : null)).find(Boolean);
  })(row);
  assert.equal(fill.style.transform, "scaleX(0.5)");

  answer = { state: "idle" };   // stop the poll from outliving the test
  t.mock.timers.tick(1000);
  await settle();
});

test("a download outcome that arrives before the first render is reported once", (t) => {
  const toasts = [];
  const s = mountSettings(t, {});
  s.V.ui.toast = (m) => toasts.push(m);
  const view = { models: [ROW({ id: "a" })], system: {},
    download: { state: "error", error: "Checksum mismatch", seq: 3, id: "a" } };
  s.V.views.settings.renderModels(view);
  s.V.views.settings.renderModels(view);
  assert.deepEqual(toasts, ["Checksum mismatch"]);
  s.V.views.settings.renderModels({ ...view, download: { state: "done", seq: 4, id: "a" } });
  assert.deepEqual(toasts, ["Checksum mismatch", "Model downloaded"]);
});

test("deleting a Custom model needs a second click after a real pause; a registry model does not", async (t) => {
  const calls = [];
  const s = mountSettings(t, {
    delete_model: async (id) => { calls.push(id); return { ok: true }; },
    get_models: async () => ({ models: [], system: {}, download: { state: "idle" } }),
    get_config: async () => ({}), get_hotkey_status: async () => null,
    hotkey_display: async () => ({}),
  }, ["setTimeout", "Date"]);
  s.V.views.settings.renderModels({
    models: [ROW({ id: "mine", installed: true, custom: true }),
             ROW({ id: "reg", installed: true })],
    system: {}, download: { state: "idle" } });
  const [mine, reg] = s["model-list"].children;
  const del = (row) => buttons(row).find((b) => b.dataset.act === "delete");
  const click = (b) => s["model-list"].dispatch("click", { target: b });

  await click(del(reg));
  assert.deepEqual(calls, ["reg"]);

  await click(del(mine));                       // arms
  assert.deepEqual(calls, ["reg"]);
  await click(del(mine));                       // too soon: ignored
  assert.deepEqual(calls, ["reg"]);
  t.mock.timers.tick(700);
  await click(del(mine));                       // confirmed
  assert.deepEqual(calls, ["reg", "mine"]);
});

test("a failed 'keep model loaded' save restores the stored choice, not the HTML default", async (t) => {
  const s = mountSettings(t, {
    set_config: async () => { throw new Error("bridge down"); },
  }, ["setTimeout"]);
  s["unload-select"].value = "min5";           // what index.html ships
  s.V.views.settings.showUnload("min10");      // what Settings render() found saved
  assert.equal(s["unload-select"].value, "min10");

  s["unload-select"].value = "never";          // the user picks another
  await s["unload-select"].dispatch("change", { target: s["unload-select"] });
  await settle();
  assert.equal(s["unload-select"].value, "min10");
});

test("a successful 'keep model loaded' save becomes the one a later failure restores", async (t) => {
  let fail = false;
  const s = mountSettings(t, {
    set_config: async () => { if (fail) throw new Error("x"); return {}; },
  }, ["setTimeout"]);
  s.V.views.settings.showUnload("min10");
  s["unload-select"].value = "hour1";
  await s["unload-select"].dispatch("change", { target: s["unload-select"] });
  await settle();
  fail = true;
  s["unload-select"].value = "never";
  await s["unload-select"].dispatch("change", { target: s["unload-select"] });
  await settle();
  assert.equal(s["unload-select"].value, "hour1");
});

test("context switches show their dependency: off leaves nothing claiming to be on", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/api.js", "js/views/settings.js"]);
  const saved = [];
  env.window.pywebview = { api: {
    clear_history: async () => ({ removed: 0 }),
    set_config: async (u) => { saved.push(u); return { use_screen_context: false }; },
  } };
  const nodes = {};
  for (const id of [...SETTINGS_IDS, "screen-context-toggle", "learn-toggle", "auto-add-toggle"]) {
    nodes[id] = makeNode(id.endsWith("toggle") ? "input" : "div");
    env.register(id, nodes[id]);
  }
  nodes["color-popover"].hidden = true;
  env.V.views.settings.init();
  const show = env.V.views.settings.showContext;
  const state = () => ["screen-context-toggle", "learn-toggle", "auto-add-toggle"].map(
    (id) => [nodes[id].checked, !!nodes[id].disabled]);

  show({});   // defaults: all on
  assert.deepEqual(state(), [[true, false], [true, false], [true, false]]);
  show({ learn_from_edits: false });
  assert.deepEqual(state(), [[true, false], [false, false], [false, true]]);
  show({ use_screen_context: false });
  assert.deepEqual(state(), [[false, false], [false, true], [false, true]]);
  show({ dictionary_auto_add: false });
  assert.deepEqual(state(), [[true, false], [true, false], [false, false]]);

  nodes["screen-context-toggle"].checked = false;
  nodes["screen-context-toggle"].dispatch("change", { target: nodes["screen-context-toggle"] });
  await settle();
  assert.deepEqual(saved, [{ use_screen_context: false }]);
  assert.deepEqual(state(), [[false, false], [false, true], [false, true]]);
});

/* ---------- the pop-up ---------- */

const SECTION_NAMES = ["dictation", "models", "key", "appearance", "privacy"];

function mountPopup(t, api = {}) {
  const h = mountSettings(t, {
    get_config: async () => ({}), get_hotkey_status: async () => null,
    get_models: async () => null, hotkey_display: async () => ({ display: "Right Option (⌥)" }),
    get_pill_themes: async () => [],
    ...api,
  }, ["setTimeout", "Date"]);
  const rest = {};
  for (const id of ["paste-toggle", "widget-toggle", "theme-grid", "color-preview", "color-swatch",
                    "color-hex", "wheel-thumb", "key-control", "hotkey-current", "hotkey-cap",
                    "hotkey-status"]) {
    rest[id] = makeNode("div");
    h.register(id, rest[id]);
  }
  const sections = {}, navItems = {};
  for (const name of SECTION_NAMES) {
    const section = makeNode("section");
    section.className = "settings-section";
    section.dataset.section = name;
    const item = makeNode("button");
    item.className = "settings-nav-item";
    item.dataset.section = name;
    h["settings-body"].append(section);
    h["settings-nav"].append(item);
    sections[name] = section;
    navItems[name] = item;
  }
  h.document.body.append(h["settings-body"], h["settings-nav"]);
  return { ...h, ...rest, sections, navItems, popup: h.V.views.settings };
}

test("Tab is walked by hand inside the pop-up, past what can't take focus, and wraps", async (t) => {
  const h = mountPopup(t);
  const scrim = h["settings-scrim"];
  const button = (over) => {
    const n = makeNode("button");
    n.focus = () => { h.document.activeElement = n; };
    n.getClientRects = () => [{}];
    return Object.assign(n, over);
  };
  const first = button();
  const disabled = button({ disabled: true });
  const hidden = button({ getClientRects: () => [] });
  const roving = button();
  roving.setAttribute("tabindex", "-1");
  const last = button();
  scrim.querySelectorAll = () => [first, disabled, hidden, roving, last];
  const tab = (over = {}) => {
    const ev = { key: "Tab", target: scrim, stopPropagation() {}, preventDefault() { ev.prevented = true; }, ...over };
    scrim.dispatch("keydown", ev);
    return ev;
  };

  h.document.activeElement = null;
  assert.equal(tab().prevented, true);
  assert.equal(h.document.activeElement, first);
  tab();
  assert.equal(h.document.activeElement, last);
  tab();
  assert.equal(h.document.activeElement, first, "wraps");
  tab({ shiftKey: true });
  assert.equal(h.document.activeElement, last, "and backwards");
});

const pressEscape = (target) => {
  const ev = { key: "Escape", target, stopPropagation() { ev.stopped = true; } };
  return ev;
};

test("opening shows the pop-up on the section asked for, and reopens where it was left", async (t) => {
  const h = mountPopup(t);
  const scrim = h["settings-scrim"];
  scrim.hidden = true;

  h.popup.open({ section: "key" });
  assert.equal(scrim.hidden, false);
  assert.ok(scrim.classList.contains("is-open"));
  assert.equal(h.sections.key.hidden, false);
  assert.equal(h.sections.dictation.hidden, true);
  assert.ok(h.navItems.key.classList.contains("active"));
  assert.equal(h.navItems.key.getAttribute("aria-current"), "page");

  h.popup.close();
  h.popup.open();
  assert.equal(h.sections.key.hidden, false, "no section named: the one it was left on");

  h.popup.open({ section: "nonsense" });
  assert.equal(h.sections.key.hidden, false, "an unknown name can't open onto a blank pane");
  h.popup.open({ section: "privacy" });
  assert.equal(h.sections.privacy.hidden, false, "asking while it's open just moves section");
  assert.equal(h.sections.key.hidden, true);
});

test("the side menu switches section and starts it from the top", async (t) => {
  const h = mountPopup(t);
  h.popup.open();
  h["settings-body"].scrollTop = 200;
  h["settings-nav"].dispatch("click", { target: h.navItems.models });
  assert.equal(h.sections.models.hidden, false);
  assert.equal(h.sections.dictation.hidden, true);
  assert.equal(h["settings-body"].scrollTop, 0);
  assert.equal(h.navItems.dictation.getAttribute("aria-current"), null);
});

test("closing hides it only once the fade is done, and says so straight away", async (t) => {
  const h = mountPopup(t);
  let closed = 0;
  h.window.addEventListener("voisr:settings-closed", () => { closed += 1; });
  const scrim = h["settings-scrim"];
  h.popup.open();
  h.popup.close();
  assert.equal(closed, 1);
  assert.ok(!scrim.classList.contains("is-open"));
  assert.equal(scrim.hidden, false, "still fading");
  t.mock.timers.tick(140);
  assert.equal(scrim.hidden, true);
  h.popup.close();
  assert.equal(closed, 1, "closing what's already closed says nothing");
});

test("reopening mid-close carries on, rather than vanishing when the old timer fires", async (t) => {
  const h = mountPopup(t);
  h.popup.open();
  h.popup.close();
  t.mock.timers.tick(100);
  h.popup.open();
  t.mock.timers.tick(100);
  assert.equal(h["settings-scrim"].hidden, false);
  assert.equal(h.popup.isOpen(), true);
});

test("focus goes into the pop-up and comes back to what opened it", async (t) => {
  const h = mountPopup(t);
  const opener = makeNode("button");
  let onOpener = 0, onItem = 0;
  opener.focus = () => { onOpener += 1; };
  h.navItems.dictation.focus = () => { onItem += 1; };
  h.document.activeElement = opener;
  h.popup.open();
  assert.equal(onItem, 1);
  h.popup.close();
  assert.equal(onOpener, 1);
});

test("Esc peels one layer at a time: the colour popover, then the pop-up", async (t) => {
  const h = mountPopup(t);
  h.popup.open();
  h["color-popover"].hidden = false;
  h["settings-scrim"].dispatch("keydown", pressEscape(h["settings-scrim"]));
  assert.equal(h["color-popover"].hidden, true);
  assert.equal(h.popup.isOpen(), true, "the popover took it");
  h["settings-scrim"].dispatch("keydown", pressEscape(h["settings-scrim"]));
  assert.equal(h.popup.isOpen(), false);
});

test("Esc the palette has already handled doesn't close Settings as well", async (t) => {
  const h = mountPopup(t);
  h.popup.open();
  h.document.dispatch("keydown", { key: "Escape", defaultPrevented: true,
                                   target: h["settings-scrim"], stopPropagation() {} });
  assert.equal(h.popup.isOpen(), true);
});

test("keys stop at the pop-up, so History can't delete a row nobody can see", async (t) => {
  const h = mountPopup(t);
  h.popup.open();
  const backspace = { key: "Backspace", target: h["settings-scrim"],
                      stopPropagation() { this.stopped = true; } };
  h["settings-scrim"]._listeners.keydown[0](backspace);
  assert.equal(backspace.stopped, true);
  const commandK = { key: "k", metaKey: true, target: h["settings-scrim"],
                     stopPropagation() { this.stopped = true; } };
  h["settings-scrim"]._listeners.keydown[0](commandK);
  assert.equal(commandK.stopped, undefined, "the palette opens from anywhere");
});

test("a key with focus lost to the page is stopped on its way down, and Esc still works", async (t) => {
  const h = mountPopup(t);
  const body = makeNode("body");
  let stopped = 0;
  const stray = (key) => ({ key, target: body, stopPropagation() { stopped += 1; } });
  h.document.dispatch("keydown", stray("x"));
  assert.equal(stopped, 0, "closed: nothing to guard");
  h.popup.open();
  h.document.dispatch("keydown", stray("Backspace"));
  assert.equal(stopped, 1);
  h.document.dispatch("keydown", stray("Escape"));
  assert.equal(h.popup.isOpen(), false);
});

test("a click closes it only if the press started on the scrim too", async (t) => {
  const h = mountPopup(t);
  const scrim = h["settings-scrim"];
  h.popup.open();

  scrim.dispatch("pointerdown", { target: h["color-popover"] });  // a wheel drag let go outside
  scrim.dispatch("click", { target: scrim });
  assert.equal(h.popup.isOpen(), true);

  h["color-popover"].hidden = false;
  scrim.dispatch("pointerdown", { target: scrim });
  scrim.dispatch("click", { target: scrim });
  assert.equal(h.popup.isOpen(), true, "the first click out belongs to the open popover");

  h["color-popover"].hidden = true;
  scrim.dispatch("pointerdown", { target: scrim });
  scrim.dispatch("click", { target: scrim });
  assert.equal(h.popup.isOpen(), false);
});

test("closing or changing section disarms Clear, so a later click only arms it", async (t) => {
  const h = mountPopup(t);
  const clear = h["clear-history"];
  h.popup.open();
  clear.dispatch("click");
  await settle();
  assert.equal(clear.dataset.armed, "1");
  h["settings-nav"].dispatch("click", { target: h.navItems.models });
  assert.equal(clear.dataset.armed, "0");
  assert.match(clear.textContent, /^Clear/);

  clear.dispatch("click");
  await settle();
  assert.equal(clear.dataset.armed, "1");
  h.popup.close();
  assert.equal(clear.dataset.armed, "0");
});

/* ---------- capturing the key ---------- */

function mountCapture(t, res) {
  let release;
  const h = mountPopup(t, {
    capture_hotkey: () => new Promise((r) => { release = () => r(res); }),
  });
  const toasts = [];
  h.V.ui.toast = (m) => toasts.push(m);
  return { ...h, toasts, finish: () => release() };
}

test("the key's row asks for a key while the app listens, then shows the new one", async (t) => {
  const h = mountCapture(t, { vk: 61, label: "Right Option (⌥)" });
  h.popup.open({ section: "key" });
  await settle();
  const btn = h["hotkey-capture"];

  const done = h.popup.captureKey();
  assert.equal(btn.disabled, true);
  assert.equal(h["hotkey-current"].textContent, "Press a key…");
  assert.ok(h["key-control"].classList.contains("is-listening"));
  assert.equal(h["hotkey-cap"].hidden, true);

  h.finish();
  await done;
  await settle();
  assert.equal(btn.disabled, false);
  assert.ok(!h["key-control"].classList.contains("is-listening"));
  assert.equal(h["hotkey-current"].textContent, "Right Option");
  assert.equal(h["hotkey-cap"].textContent, "⌥");
  assert.equal(h["hotkey-cap"].hidden, false);
  assert.deepEqual(h.toasts, ["Push-to-talk key set to Right Option (⌥)"]);
});

test("while it listens the pop-up stays put, and Esc belongs to the capture", async (t) => {
  const h = mountCapture(t, { cancelled: true });
  h.popup.open({ section: "key" });
  const scrim = h["settings-scrim"];
  const done = h.popup.captureKey();

  scrim.dispatch("keydown", pressEscape(scrim));
  assert.equal(h.popup.isOpen(), true, "Esc doesn't close it");
  scrim.dispatch("pointerdown", { target: scrim });
  scrim.dispatch("click", { target: scrim });
  assert.equal(h.popup.isOpen(), true, "nor does a click outside");
  h["settings-nav"].dispatch("click", { target: h.navItems.models });
  assert.equal(h.sections.key.hidden, false, "the row asking for a key stays on screen");

  h.finish();
  await done;
  assert.deepEqual(h.toasts, [], "cancelling isn't news");
});

test("the Esc that cancelled a capture can't reach the page late and close the pop-up", async (t) => {
  const h = mountCapture(t, { cancelled: true });
  h.popup.open({ section: "key" });
  const scrim = h["settings-scrim"];
  const done = h.popup.captureKey();
  h.finish();
  await done;

  scrim.dispatch("keydown", pressEscape(scrim));
  assert.equal(h.popup.isOpen(), true, "this is the same key press, arriving late");
  t.mock.timers.tick(300);
  scrim.dispatch("keydown", pressEscape(scrim));
  assert.equal(h.popup.isOpen(), false, "a later Esc is a new one");
});

test("no key in time says so, and the row comes back", async (t) => {
  const h = mountCapture(t, { error: "no key detected — click Change and press a key" });
  h.popup.open({ section: "key" });
  const done = h.popup.captureKey();
  h.finish();
  await done;
  await settle();
  assert.deepEqual(h.toasts, ["no key detected — click Change and press a key"]);
  assert.equal(h["hotkey-current"].textContent, "Right Option");
  assert.equal(h["hotkey-capture"].disabled, false);
});

test("a key without a symbol is just its name, with no blank keycap", (t) => {
  const h = mountCapture(t, {});
  h.popup.showKey("F13");
  assert.equal(h["hotkey-current"].textContent, "F13");
  assert.equal(h["hotkey-cap"].hidden, true);
  h.popup.showKey("Fn / Globe");
  assert.equal(h["hotkey-current"].textContent, "Fn / Globe");
  assert.equal(h["hotkey-cap"].hidden, true);
  h.popup.showKey("Left Command (⌘)");
  assert.equal(h["hotkey-current"].textContent, "Left Command");
  assert.equal(h["hotkey-cap"].textContent, "⌘");
});

/* ---------- pill pictures ---------- */

test("the pill picker's two pictures look like the two real pills", async (t) => {
  const h = mountSettings(t, {
    get_config: async () => ({ pill_theme: "quiet" }),
    get_pill_themes: async () => [{ value: "classic", label: "Signature" }, { value: "quiet", label: "Quiet" }],
  });
  const grid = makeNode("div");
  const preview = (b) => b.children[0].children[0];
  h.document.querySelector = ((orig) => (x) => (x === "#theme-grid" ? grid : orig(x)))(h.document.querySelector);
  await h.V.views.settings.renderThemes();
  const [signature, quiet] = grid.children;
  assert.deepEqual(preview(signature).children.map((n) => n.className),
    Array(15).fill("pp-bar"), "bars only, with no dot");
  assert.deepEqual(preview(quiet).children.map((n) => n.className), ["pp-live"],
    "the live dot only");
  assert.equal(activeOf(grid), "quiet");
});

test("on Quiet the popover previews the dots a colour changes, not the live dot it leaves alone", async (t) => {
  const h = mountColorWheel(t, { get_config: async () => ({ pill_theme: "quiet", pill_color: "#00FF00" }) });
  await h.V.views.settings.renderColorWheel();
  assert.deepEqual(h["color-preview"].children.map((n) => n.className), ["pp-dot", "pp-dot", "pp-dot"]);
  assert.equal(h["color-preview"].style.getPropertyValue("--mb-color"), "#00FF00");
});

test("a tap still starting isn't shown as needing you, and is looked at again until it decides", async (t) => {
  let answer = { status: "active on F13", state: "listening", health: "active" };
  const h = mountPopup(t, { get_hotkey_status: async () => answer });
  h.popup.open({ section: "key" });
  await settle();
  const node = h["hotkey-status"];

  h.popup.renderHotkeyStatus({ status: "starting push-to-talk on F13…", state: "off", health: "starting" });
  assert.equal(node.dataset.state, "busy");

  t.mock.timers.tick(1000);
  await settle();
  assert.equal(node.textContent, "active on F13");
  assert.equal(node.dataset.state, "listening");
});

test("re-rendering the model list disarms a custom Delete, so its plain new button can't delete at once", async (t) => {
  const calls = [];
  const s = mountSettings(t, {
    delete_model: async (id) => { calls.push(id); return { ok: true }; },
    get_models: async () => null, get_config: async () => null,
    get_hotkey_status: async () => null, hotkey_display: async () => null,
  }, ["setTimeout", "Date"]);
  const view = { models: [ROW({ id: "mine", installed: true, custom: true })], system: {},
                 download: { state: "idle" } };
  const del = () => buttons(s["model-list"].children[0]).find((b) => b.dataset.act === "delete");
  const click = (b) => s["model-list"]._listeners.click[0]({ target: b });

  s.V.views.settings.renderModels(view);
  await click(del());                          // arms
  s.V.views.settings.renderModels(view);       // a download finishing, say
  t.mock.timers.tick(700);
  await click(del());
  assert.deepEqual(calls, [], "this click only arms again");
  assert.match(del().textContent, /Click again/);
});
