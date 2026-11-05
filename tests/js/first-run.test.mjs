import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* ---------- first-run.js, driven through its real render paths ----------

   Polls and the level feed run on real setIntervals. Unref'd, a test that ends
   mid-step can't keep the process alive. */
const realSetInterval = globalThis.setInterval;
globalThis.setInterval = (...args) => {
  const timer = realSetInterval(...args);
  if (timer && typeof timer.unref === "function") timer.unref();
  return timer;
};

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

const pane = (key, label, over = {}) => ({ key, label, granted: true, hotkey: false, effective: true, ...over });
const ACC_ON = pane("accessibility", "Accessibility", { hotkey: true });
const ACC_OFF = { ...ACC_ON, granted: false };
const ACC_STALE = { ...ACC_ON, effective: false };
const MIC_ON = pane("microphone", "Microphone");
const MIC_LATER = { ...MIC_ON, granted: null };
const MIC_OFF = { ...MIC_ON, granted: false };

function mount(state = {}) {
  const env = loadVoisr(["js/ui.js", "js/views/home.js", "js/views/first-run.js"]);
  const ids = ["first-run", "first-run-step", "first-run-progress", "toast", "view-home"];
  const nodes = {};
  for (const id of ids) { nodes[id] = makeNode(); env.register(id, nodes[id]); }
  nodes["first-run"].hidden = true;
  nodes["first-run-progress"].append(makeNode("span"), makeNode("span"), makeNode("span"));
  const shell = makeNode();
  shell.className = "shell";
  nodes["view-home"].className = "view";
  env.document.body.append(shell, nodes["view-home"]);
  const calls = [];
  const s = { pending: true, panes: [ACC_OFF, MIC_LATER], fallback: false, level: null,
              display: "Right Option (⌥)", capture: { vk: 63, label: "Fn / Globe" }, history: [], ...state };
  env.V.api = () => ({
    get_first_run: async () => (s.firstRunFails ? { error: "no" } : { pending: s.pending }),
    get_permissions: async () => ({ panes: s.panes, fallback: s.fallback }),
    open_settings_pane: async (key) => { calls.push(["open", key]); return { ok: true }; },
    reset_permission: async (key) => { calls.push(["reset", key]); return { ok: true }; },
    relaunch_app: async () => { calls.push(["relaunch"]); return { ok: true }; },
    hotkey_display: async () => ({ display: s.display }),
    capture_hotkey: async () => { calls.push(["capture"]); return s.capture; },
    get_level: async () => s.level,
    get_history: async () => s.history,
    set_config: async (u) => { calls.push(["save", u]); return s.saveFails ? { error: "no" } : u; },
  });
  const entered = [];
  const start = () => env.V.views.firstRun.start(() => entered.push("app"));
  const box = nodes["first-run-step"];
  const all = (node, out = []) => {
    for (const kid of node.children || []) {
      if (kid && typeof kid === "object") { out.push(kid); all(kid, out); }
    }
    return out;
  };
  const btn = (text) => all(box).find((n) => n.tagName === "BUTTON" && n.textContent === text);
  const heading = () => box.querySelector(".heading-serif").textContent;
  const sub = () => box.querySelector(".first-run-sub").textContent;
  const rows = () => box.querySelectorAll(".first-run-row");
  return { ...env, ...nodes, shell, s, calls, entered, start, box, btn, heading, sub, rows, all };
}

test("nothing to do: it stays closed and says so", async () => {
  const h = mount({ pending: false });
  assert.equal(await h.start(), false);
  assert.equal(h["first-run"].hidden, true);
  assert.equal(h["view-home"].hidden, false);
});

test("a failed check can't stop the app starting", async () => {
  const h = mount({ firstRunFails: true });
  assert.equal(await h.start(), false);
  assert.equal(h["first-run"].hidden, true);
});

test("opening it hides the app behind, which keeps Home's polls quiet", async () => {
  const h = mount();
  assert.equal(await h.start(), true);
  assert.equal(h["first-run"].hidden, false);
  assert.equal(h["view-home"].hidden, true);
  assert.equal(h.entered.length, 0);
});

test("step 1 with Accessibility off: Continue waits, and there's a way out", async () => {
  const h = mount();
  await h.start();
  assert.equal(h.heading(), "Two permissions first");
  assert.equal(h.btn("Continue").attributes.disabled, "disabled");
  assert.ok(h.btn("Set up later"));
  assert.equal(h["first-run-progress"].getAttribute("aria-label"), "Step 1 of 3");
  h.btn("Open Settings").click();
  await flush();
  assert.deepEqual(h.calls, [["open", "accessibility"]]);
});

test("step 1 orders the key's pane first and tells the mic to wait", async () => {
  const h = mount({ panes: [MIC_LATER, ACC_OFF] });
  await h.start();
  const [first, second] = h.rows();
  assert.equal(first.querySelector(".first-run-row-name").textContent, "Accessibility");
  assert.equal(first.querySelector(".first-run-row-why").textContent, "For push-to-talk and pasting");
  assert.equal(second.querySelector(".first-run-row-why").textContent, "Asked on your first dictation");
  assert.equal(second.querySelector(".pill").textContent, "Later");
});

test("listen mode has three panes, and the headline can count", async () => {
  const h = mount({ panes: [MIC_LATER, pane("input_monitoring", "Input Monitoring", { hotkey: true, granted: false }),
                            pane("accessibility", "Accessibility", { granted: false })] });
  await h.start();
  assert.equal(h.heading(), "Three permissions first");
  assert.match(h.sub(), /one for the key, one to paste/);
  assert.equal(h.rows().length, 3);
  assert.equal(h.rows()[1].querySelector(".first-run-row-why").textContent, "For pasting");
});

test("Reset shows only when it's off or not working, and always with Relaunch", async () => {
  const off = mount();
  await off.start();
  assert.ok(off.btn("Reset Accessibility"));
  assert.ok(off.btn("Relaunch Voisr"));

  const stale = mount({ panes: [ACC_STALE, MIC_LATER] });
  await stale.start();
  assert.equal(stale.btn("Open Settings"), undefined, "the switch is already on");
  assert.equal(stale.rows()[0].querySelector(".first-run-row-why").textContent,
               "Switched on, but macOS is still blocking it");
  stale.btn("Reset Accessibility").click();
  await flush();
  assert.deepEqual(stale.calls, [["reset", "accessibility"]]);
  assert.ok(stale.btn("Relaunch Voisr"));
  assert.equal(stale.btn("Continue").attributes.disabled, "disabled");
});

test("an unknown grant never counts as on, and offers no Reset", async () => {
  const h = mount({ panes: [{ ...ACC_ON, granted: null }, MIC_LATER] });
  await h.start();
  assert.equal(h.btn("Continue").attributes.disabled, "disabled");
  assert.equal(h.btn("Reset Accessibility"), undefined);
  assert.ok(h.btn("Open Settings"));
});

test("a refused mic on step 1 asks for it too", async () => {
  const h = mount({ panes: [ACC_OFF, MIC_OFF] });
  await h.start();
  const mic = h.rows()[1];
  assert.equal(mic.querySelector(".first-run-row-why").textContent, "Switched off, so nothing can be recorded");
  assert.ok(mic.querySelector(".btn-signal"));
});

test("a refused mic doesn't hold the key back: step 3 deals with it", async () => {
  const h = mount({ panes: [ACC_ON, MIC_OFF] });
  await h.start();
  await flush();
  assert.equal(h.heading(), "Pick the key you'll hold");
});

test("opening with the key's grant on lands on the first unfinished step", async () => {
  const h = mount({ panes: [ACC_ON, MIC_LATER] });
  await h.start();
  await flush();
  assert.equal(h.heading(), "Pick the key you'll hold");
  assert.equal(h["first-run-progress"].getAttribute("aria-label"), "Step 2 of 3");
});

test("the fallback tap carries the key, so it counts as working", async () => {
  const h = mount({ fallback: true, panes: [MIC_LATER, pane("input_monitoring", "Input Monitoring", { hotkey: true }), { ...pane("accessibility", "Accessibility"), effective: false }] });
  await h.start();
  await flush();
  assert.equal(h.heading(), "Pick the key you'll hold");
});

test("the grant arriving on a poll unlocks Continue and draws the tick", async () => {
  const h = mount();
  await h.start();
  h.s.panes = [ACC_ON, MIC_LATER];
  await h.window.dispatch("focus");
  await flush();
  const row = h.rows()[0];
  assert.match(row.querySelector(".first-run-on").innerHTML, /first-run-tick is-new/);
  assert.equal(h.btn("Continue").attributes.disabled, undefined);
  assert.equal(h.btn("Set up later"), undefined);
  assert.equal(h.btn("Reset Accessibility"), undefined);
});

test("a poll that changes nothing doesn't redraw, so focus and screen readers stay put", async () => {
  const h = mount();
  await h.start();
  const before = h.btn("Continue");
  await h.window.dispatch("focus");
  await flush();
  assert.equal(h.btn("Continue"), before);
});

test("Set up later saves it as done and enters the app", async () => {
  const h = mount();
  await h.start();
  h.btn("Set up later").click();
  await flush();
  assert.deepEqual(h.calls, [["save", { first_run_done: true }]]);
  assert.deepEqual(h.entered, ["app"]);
  assert.equal(h.shell.hidden, false);
  assert.ok(h["first-run"].classList.contains("is-leaving"));
});

test("a failed save still lets them in: it just shows again next launch", async () => {
  const h = mount({ saveFails: true });
  await h.start();
  h.btn("Set up later").click();
  await flush();
  assert.deepEqual(h.entered, ["app"]);
});

test("keyParts: the raw label becomes a symbol and a name", () => {
  const { V } = mount();
  const { keyParts } = V.views.firstRun;
  assert.deepEqual(keyParts("Right Option (⌥)"), { cap: "⌥", name: "Right Option" });
  assert.deepEqual(keyParts("Fn / Globe"), { cap: "fn", name: "Fn / Globe" });
  assert.deepEqual(keyParts("Caps Lock"), { cap: "⇪", name: "Caps Lock" });
  assert.deepEqual(keyParts("F13"), { cap: "F13", name: "F13", word: false });
  assert.deepEqual(keyParts("Space"), { cap: "Space", name: "Space", word: true });
  assert.deepEqual(keyParts("unset"), { cap: "unset", name: "unset", word: true });
});

const toastText = (h) => h.all(h.toast).map((n) => n.textContent).filter(Boolean)
  .concat((h.toast.children || []).filter((c) => typeof c === "string"));

async function atKey(state) {
  const h = mount({ panes: [ACC_ON, MIC_LATER], ...state });
  await h.start();
  await flush();
  return h;
}

test("step 2 shows the real key and changes it through capture_hotkey", async () => {
  const h = await atKey();
  assert.equal(h.box.querySelector(".first-run-keycap").textContent, "⌥");
  assert.equal(h.box.querySelector(".first-run-key-name").textContent, "Right Option");
  h.s.display = "Fn / Globe";
  h.btn("Change…").click();
  assert.equal(h.box.querySelector(".first-run-key-name").textContent, "Right Option", "drawn before the bridge answers");
  await flush();
  assert.deepEqual(h.calls, [["capture"]]);
  assert.equal(h.box.querySelector(".first-run-keycap").textContent, "fn");
});

test("step 2 waits while it listens for a key", async () => {
  const h = await atKey();
  let answer;
  h.V.api = ((real) => () => ({ ...real(), capture_hotkey: () => new Promise((r) => { answer = r; }) }))(h.V.api);
  h.btn("Change…").click();
  await flush();
  assert.equal(h.box.querySelector(".first-run-key-name").textContent, "Press a key…");
  assert.equal(h.btn("Change…").attributes.disabled, "disabled");
  assert.equal(h.btn("Continue").attributes.disabled, "disabled");
  answer({ cancelled: true });
  await flush();
  assert.equal(h.box.querySelector(".first-run-key-name").textContent, "Right Option");
  assert.equal(h.btn("Continue").attributes.disabled, undefined);
});

test("the key press that was captured can't also press a button", async () => {
  const h = await atKey();
  h.btn("Change…").click();
  await flush();
  h.btn("Continue").click();   // Return reaching the page just after Python answered
  await flush();
  assert.equal(h.heading(), "Pick the key you'll hold");
  h.btn("Change…").click();
  await flush();
  assert.equal(h.calls.filter(([name]) => name === "capture").length, 1);
});

test("step 2: Esc says nothing, a timeout says why", async () => {
  const cancelled = await atKey({ capture: { cancelled: true } });
  cancelled.btn("Change…").click();
  await flush();
  assert.deepEqual(toastText(cancelled), []);

  const timedOut = await atKey({ capture: { error: "no key detected" } });
  timedOut.btn("Change…").click();
  await flush();
  // tryApi says what failed, and either way the step is usable again
  assert.match(toastText(timedOut).join(" "), /no key detected/);
  assert.equal(timedOut.btn("Continue").attributes.disabled, undefined);
});

async function atDictation(state) {
  const h = await atKey(state);
  h.btn("Continue").click();
  await flush();
  return h;
}

test("step 3 warns about the mic only if macOS hasn't asked, and offers Skip", async () => {
  const later = await atDictation();
  assert.equal(later.heading(), "Hold your key and say something");
  assert.match(later.sub(), /macOS will ask about the microphone first; allow it/);
  assert.ok(later.btn("Skip"));
  assert.equal(later["first-run-progress"].getAttribute("aria-label"), "Step 3 of 3");

  const on = await atDictation({ panes: [ACC_ON, MIC_ON] });
  assert.equal(on.sub(), "Anything. Only your Mac is listening.");
});

test("step 3 has no drawn pill: the real one floats at the bottom of the screen", async () => {
  const h = await atDictation();
  assert.equal(h.box.querySelector(".pill-preview"), null);
  assert.ok(h.box.querySelector(".first-run-wave"));
});

test("a refused mic swaps the step for the way to fix it, and back", async () => {
  const h = await atDictation();
  h.s.panes = [ACC_ON, MIC_OFF];
  h.window.dispatch("focus");
  await flush();
  assert.equal(h.heading(), "Voisr can't hear you");
  assert.equal(h.box.querySelector(".first-run-wave"), null);
  assert.ok(h.btn("Skip"));
  h.btn("Open Settings").click();
  await flush();
  assert.deepEqual(h.calls.at(-1), ["open", "microphone"]);
  h.s.panes = [ACC_ON, MIC_ON];
  h.window.dispatch("focus");
  await flush();
  assert.equal(h.heading(), "Hold your key and say something");
});

test("the key dying at step 3 goes back to the step that fixes it", async () => {
  const h = await atDictation();
  h.s.panes = [ACC_OFF, MIC_ON];
  h.window.dispatch("focus");
  await flush();
  assert.equal(h.heading(), "Two permissions first");
});

test("a dictation landing finishes the step by itself, and shows the newest", async () => {
  const h = await atDictation({
    history: [{ id: "old", ts: 1, text: "older", words: 1 },
              { id: "new", ts: 9, text: "Testing, testing. Is this thing writing down everything I say?", words: 10 }],
  });
  h.window.dispatch("voisr:new-dictation");
  await flush();
  assert.equal(h.heading(), "That's it");
  assert.equal(h.sub(), "10 words. None of them left this Mac.");
  assert.equal(h.box.querySelector(".first-run-said").textContent,
               "Testing, testing. Is this thing writing down everything I say?");
  h.btn("Open Voisr").click();
  await flush();
  assert.deepEqual(h.calls.at(-1), ["save", { first_run_done: true }]);
  assert.deepEqual(h.entered, ["app"]);
});

test("one word reads as one word", async () => {
  const h = await atDictation({ history: [{ id: "a", ts: 1, text: "Testing.", words: 1 }] });
  h.window.dispatch("voisr:new-dictation");
  await flush();
  assert.equal(h.sub(), "1 word. It didn't leave this Mac.");
});

test("a dictation before step 3 doesn't finish anything", async () => {
  const h = await atKey({ history: [{ id: "a", ts: 1, text: "stray", words: 1 }] });
  h.window.dispatch("voisr:new-dictation");
  await flush();
  assert.equal(h.heading(), "Pick the key you'll hold");
});

test("Skip goes to the app without a dictation", async () => {
  const h = await atDictation();
  h.btn("Skip").click();
  await flush();
  assert.deepEqual(h.calls.at(-1), ["save", { first_run_done: true }]);
  assert.deepEqual(h.entered, ["app"]);
});

test("finishing twice only enters once", async () => {
  const h = await atDictation();
  const skip = h.btn("Skip");
  skip.click();
  skip.click();
  await flush();
  assert.deepEqual(h.entered, ["app"]);
});
