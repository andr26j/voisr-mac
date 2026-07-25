import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";

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
