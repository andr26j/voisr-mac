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

const SETTINGS_IDS = ["appearance-select", "model-select", "launch-toggle",
                       "color-open", "color-popover", "clear-history", "toast"];

function mountSettings(t, api, apis = ["setTimeout"]) {
  t.mock.timers.enable({ apis });
  const env = loadVoisr(["js/ui.js", "js/api.js", "js/views/settings.js"]);
  env.window.pywebview = { api: { clear_history: async () => ({ removed: 0 }), ...api } };

  const nodes = {};
  for (const id of SETTINGS_IDS) {
    nodes[id] = makeNode(id === "launch-toggle" ? "input"
      : id === "appearance-select" || id === "model-select" ? "select" : "div");
    env.register(id, nodes[id]);
  }
  nodes["color-popover"].hidden = true;

  env.V.views.settings.init();
  return { V: env.V, window: env.window, document: env.document, ...nodes };
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
  assert.equal(h["color-preview"].style.getPropertyValue("--mb-glow"), "rgba(255, 5, 5, .75)");
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
  assert.equal(h["color-hex"].textContent, "theme default");
  assert.equal(h["color-swatch"].style.background, "transparent");
  assert.equal(h["color-preview"].style.getPropertyValue("--mb-color"), "",
    "the custom-colour variable is removed, not set to an empty-looking value");
  assert.equal("customColor" in h["color-preview"].dataset, false);
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
