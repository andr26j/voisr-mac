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

// Captured before any test enables mock timers, so this always reaches the
// real, unmocked setTimeout -- used only to wait out a genuine real-time
// dwell below. App code itself keeps using the (per-test-mocked) global
// setTimeout, so its own disarm timer stays frozen until a test ticks it.
const realSetTimeout = globalThis.setTimeout;
const realSleep = (ms) => new Promise((resolve) => realSetTimeout(resolve, ms));

const SETTINGS_IDS = ["appearance-select", "model-select", "launch-toggle",
                       "color-open", "color-popover", "clear-history", "toast"];

function mountSettings(t, api) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
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

test("clear-history arms on the first click, rejects a too-fast confirm, then clears after a genuine dwell", async (t) => {
  const calls = [];
  const h = mountSettings(t, { clear_history: async () => { calls.push(1); return { removed: 3 }; } });
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

  // Lands well under the 600ms dwell -- a double-click or a stuck key.
  btn.dispatch("click");
  await settle();
  assert.equal(calls.length, 0, "a too-fast confirm must be rejected");
  assert.equal(btn.dataset.armed, "1", "still armed -- the fast click neither confirmed nor disarmed");

  await realSleep(650);   // a genuine dwell, measured on the real clock like Date.now() is

  btn.dispatch("click");
  await settle();

  assert.equal(calls.length, 1, "a click after a genuine dwell must clear history");
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
