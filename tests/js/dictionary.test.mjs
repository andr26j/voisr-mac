import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

const IDS = ["dict-search", "dict-search-open", "dict-tabs", "dict-empty", "dict-list", "toast",
             "dict-suggestions", "dict-suggestion-list", "dict-words", "dict-words-label",
             "dict-count-all", "dict-count-suggestions", "dict-count-auto",
             "dict-add", "dict-form", "dict-spoken", "dict-written", "dict-cancel",
             "dict-banner", "dict-banner-add", "dict-banner-close"];

const settle = () => new Promise((r) => setImmediate(r));

function mount(t, { dictionary = {}, state = { pending: [], auto_added: {}, used: {} }, failing = false, cfg = {}, setFails = false } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/dictionary.js"]);
  const calls = [];
  const live = { dictionary: { ...dictionary }, ...cfg };
  env.window.pywebview = { api: {
    get_config: async () => JSON.parse(JSON.stringify(live)),
    set_config: async (u) => {
      if (setFails) return { error: "disk full" };
      calls.push(["set", u]);
      Object.assign(live, u);
      return { ...live };
    },
    get_suggestions: async () => { if (failing) throw new Error("no"); return state; },
    accept_suggestion: async (id) => { calls.push(["accept", id]); return { ok: true }; },
    dismiss_suggestion: async (id) => { calls.push(["dismiss", id]); return { ok: true }; },
  } };
  const nodes = {};
  for (const id of IDS) {
    nodes[id] = makeNode(id === "dict-search" ? "input" : "div");
    env.register(id, nodes[id]);
  }
  nodes["dict-search"].value = "";
  nodes["dict-search"].hidden = true;
  nodes["dict-form"].hidden = true;
  nodes["dict-spoken"].value = "";
  nodes["dict-written"].value = "";
  for (const name of ["all", "suggestions", "auto"]) {
    const tab = makeNode("button");
    tab.className = "tab";
    tab.dataset.tab = name;
    nodes["dict-tabs"].append(tab);
  }
  env.V.views.dictionary.init();
  return { V: env.V, calls, live, ...nodes };
}

const flatten = (node) => [node, ...Array.from(node.children || []).flatMap(flatten)];
const textsOf = (node) => flatten(node).map((n) => n.textContent).filter(Boolean);
const byClass = (node, cls) => flatten(node).filter((n) => String(n.className || "").split(/\s+/).includes(cls));
const buttonText = (row) => flatten(row).filter((n) => n.tagName === "BUTTON").map((b) => b.textContent);
const button = (row, text) => flatten(row).find((n) => n.tagName === "BUTTON" && n.textContent === text);
const toastText = (h) => h.toast.children.map((c) => c.textContent).join("");

const clickTab = async (h, name) => {
  const tab = h["dict-tabs"].children.find((b) => b.dataset.tab === name);
  h["dict-tabs"].dispatch("click", { target: tab });
  await settle();
  return tab;
};

const dictOf = (n) => Object.fromEntries(
  Array.from({ length: n }, (_, i) => [`spoken${i}`, `Written${i}`]));

/* ---------- filtering ---------- */

test("the filter is there however short the list, and appears when asked for", async (t) => {
  const h = mount(t, { dictionary: dictOf(2) });
  await h.V.views.dictionary.render();
  assert.equal(h["dict-search"].hidden, true);
  h["dict-search-open"].dispatch("click");
  assert.equal(h["dict-search"].hidden, false);
  assert.equal(h["dict-search-open"].getAttribute("aria-expanded"), "true");
});

test("closing the filter resets it and the input's own value", async (t) => {
  const h = mount(t, { dictionary: dictOf(3) });
  await h.V.views.dictionary.render();
  h["dict-search-open"].dispatch("click");

  // this is what makes the regression observable: if `filter` survives the
  // close, the list renders as "no matches" with no visible way to clear it
  h["dict-search"].value = "zzz-does-not-match-anything";
  h["dict-search"].dispatch("input");
  await settle();
  assert.equal(h["dict-list"].children.length, 0, "the filter narrowed the list");
  assert.equal(h["dict-empty"].hidden, false);
  assert.equal(h["dict-empty"].textContent, "No words match “zzz-does-not-match-anything”.");

  h["dict-search"].dispatch("keydown", { key: "Escape", preventDefault() {} });
  await settle();

  assert.equal(h["dict-search"].hidden, true);
  assert.equal(h["dict-search"].value, "", "a hidden input must not silently keep stale text");
  assert.equal(h["dict-list"].children.length, 3, "every entry shows again");
  assert.equal(h["dict-empty"].hidden, true);
});

test("filter matching case-folds the spoken key the same way the written value already is", async (t) => {
  // Deliberately mixed-case spoken key -- addEntry() always lowercases on
  // the way in, but the match itself must not depend on that: a hand-edited
  // config.json (or any future write path) could still hand it a
  // non-lowercase key, and the match has to be correct on its own. The
  // written value is chosen so it does NOT itself contain "trademate" --
  // otherwise the written-value half of the OR would match regardless of
  // how the spoken key is compared, and the test would pass even with the
  // spoken-key side left unfixed.
  const h = mount(t, { dictionary: { TradeMate: "TM Group", ...dictOf(3) } });
  await h.V.views.dictionary.render();
  h["dict-search"].value = "trademate";
  h["dict-search"].dispatch("input");
  await settle();
  assert.equal(h["dict-list"].children.length, 1,
    "a lowercase query must still find a mixed-case spoken key");
});

test("the filter covers suggestions too", async (t) => {
  const h = mount(t, { dictionary: { voiser: "Voisr" }, state: {
    pending: [{ id: "p1", heard: "whisper cpp", written: "whisper.cpp", seen: 1 }], auto_added: {}, used: {},
  } });
  await h.V.views.dictionary.render();
  h["dict-search"].value = "whisper";
  h["dict-search"].dispatch("input");
  await settle();
  assert.equal(h["dict-suggestions"].hidden, false);
  assert.equal(h["dict-words"].hidden, true);
  assert.equal(h["dict-empty"].hidden, true);
});

/* ---------- tabs and counts ---------- */

const MIXED = {
  dictionary: { voiser: "Voisr", brunswik: "Brunswick", sam: "Sam" },
  state: {
    pending: [{ id: "p1", heard: "whisper cpp", written: "whisper.cpp", seen: 1 }],
    auto_added: { sam: "Sam" }, used: {},
  },
};

test("All counts everything it shows, including the suggestion", async (t) => {
  const h = mount(t, MIXED);
  await h.V.views.dictionary.render();
  assert.equal(h["dict-count-all"].textContent, "4");
  assert.equal(h["dict-count-suggestions"].textContent, "1");
  assert.equal(h["dict-count-auto"].textContent, "1");
});

test("a word the user has since edited doesn't count as auto-added", async (t) => {
  const h = mount(t, {
    dictionary: { voiser: "VoisR" },
    state: { pending: [], auto_added: { voiser: "Voisr" }, used: {} },
  });
  await h.V.views.dictionary.render();
  assert.equal(h["dict-count-auto"].textContent, "", "the same rule as the row's badge");
  assert.equal(byClass(h["dict-list"], "tag").length, 0);
});

test("a tab with nothing in it shows no number", async (t) => {
  const h = mount(t, { dictionary: { voiser: "Voisr" } });
  await h.V.views.dictionary.render();
  assert.equal(h["dict-count-all"].textContent, "1");
  assert.equal(h["dict-count-suggestions"].textContent, "");
  assert.equal(h["dict-count-auto"].textContent, "");
});

test("each tab shows only what it says, and marks itself", async (t) => {
  const h = mount(t, MIXED);
  await h.V.views.dictionary.render();
  assert.equal(h["dict-list"].children.length, 3);
  assert.equal(h["dict-suggestions"].hidden, false);

  const suggestions = await clickTab(h, "suggestions");
  assert.equal(suggestions.getAttribute("aria-pressed"), "true");
  assert.ok(suggestions.classList.contains("active"));
  assert.equal(h["dict-suggestions"].hidden, false);
  assert.equal(h["dict-words"].hidden, true);

  await clickTab(h, "auto");
  assert.equal(h["dict-suggestions"].hidden, true);
  assert.equal(h["dict-words"].hidden, false);
  assert.equal(h["dict-list"].children.length, 1);
  assert.equal(h["dict-words-label"].textContent, "Auto-added");
  assert.equal(suggestions.getAttribute("aria-pressed"), "false");

  await clickTab(h, "all");
  assert.equal(h["dict-words-label"].textContent, "Your words");
});

/* ---------- empty ---------- */

test("each tab has its own empty line", async (t) => {
  const h = mount(t);
  await h.V.views.dictionary.render();
  assert.equal(h["dict-empty"].hidden, false);
  assert.equal(h["dict-empty"].textContent, "No words yet.");
  assert.equal(h["dict-words"].hidden, true);

  await clickTab(h, "suggestions");
  assert.match(h["dict-empty"].textContent, /Nothing waiting/);
  await clickTab(h, "auto");
  assert.equal(h["dict-empty"].textContent, "Nothing added automatically.");
});

for (const off of [{ learn_from_edits: false }, { use_screen_context: false }]) {
  test(`the Suggestions tab says learning is off with ${JSON.stringify(off)}`, async (t) => {
    const h = mount(t, { cfg: off });
    await h.V.views.dictionary.render();
    await clickTab(h, "suggestions");
    assert.equal(h["dict-empty"].textContent, "Learning from corrections is off in Settings.");
  });
}

/* ---------- the banner ---------- */

test("the banner shows on All only", async (t) => {
  const h = mount(t, { dictionary: { voiser: "Voisr" } });
  await h.V.views.dictionary.render();
  assert.equal(h["dict-banner"].hidden, false);
  await clickTab(h, "auto");
  assert.equal(h["dict-banner"].hidden, true);
  await clickTab(h, "all");
  assert.equal(h["dict-banner"].hidden, false);
});

test("a dismissed banner stays hidden on every tab", async (t) => {
  const gone = mount(t, { cfg: { dismissed_banners: ["dictionary"] } });
  await gone.V.views.dictionary.render();
  assert.equal(gone["dict-banner"].hidden, true);
});

test("dismissing it saves that, keeping any other page's dismissal", async (t) => {
  const h = mount(t, { cfg: { dismissed_banners: ["snippets"] } });
  await h.V.views.dictionary.render();
  h["dict-banner-close"].dispatch("click");
  await settle();
  assert.deepEqual(h.live.dismissed_banners, ["snippets", "dictionary"]);
  assert.equal(h["dict-banner"].hidden, true);
  await h.V.views.dictionary.render();
  assert.equal(h["dict-banner"].hidden, true, "and it stays gone");
});

test("a dismissal that couldn't be saved leaves the banner up and says why", async (t) => {
  const h = mount(t, { setFails: true });
  await h.V.views.dictionary.render();
  h["dict-banner-close"].dispatch("click");
  await settle();
  assert.equal(h["dict-banner"].hidden, false);
  assert.match(toastText(h), /disk full/);
});

test("with the banner dismissed, the empty line says what the page is for", async (t) => {
  const h = mount(t, { cfg: { dismissed_banners: ["dictionary"] } });
  await h.V.views.dictionary.render();
  assert.equal(h["dict-empty"].textContent, "No words yet. Map what it heard to what you meant.");
});

/* ---------- adding ---------- */

test("Add new swaps the banner for the form, and Cancel swaps them back", async (t) => {
  const h = mount(t);
  await h.V.views.dictionary.render();
  h["dict-add"].dispatch("click");
  assert.equal(h["dict-form"].hidden, false);
  assert.equal(h["dict-banner"].hidden, true);
  h["dict-spoken"].value = "half typed";
  h["dict-cancel"].dispatch("click");
  assert.equal(h["dict-form"].hidden, true);
  assert.equal(h["dict-banner"].hidden, false);
  assert.equal(h["dict-spoken"].value, "");
});

test("the banner's own button opens the same form", async (t) => {
  const h = mount(t);
  await h.V.views.dictionary.render();
  h["dict-banner-add"].dispatch("click");
  assert.equal(h["dict-form"].hidden, false);
});

test("adding stores the pair and closes the form", async (t) => {
  const h = mount(t);
  await h.V.views.dictionary.render();
  h["dict-add"].dispatch("click");
  h["dict-spoken"].value = " TradeMake ";
  h["dict-written"].value = " TradeMate ";
  await h.V.views.dictionary.addEntry({ preventDefault() {} });
  assert.deepEqual(h.live.dictionary, { trademake: "TradeMate" });
  assert.equal(h["dict-form"].hidden, true);
  assert.equal(h["dict-spoken"].value, "");
});

test("a failed add keeps the form and what was typed", async (t) => {
  const h = mount(t, { setFails: true });
  await h.V.views.dictionary.render();
  h["dict-add"].dispatch("click");
  h["dict-spoken"].value = "trademake";
  h["dict-written"].value = "TradeMate";
  await h.V.views.dictionary.addEntry({ preventDefault() {} });
  assert.equal(h["dict-form"].hidden, false);
  assert.equal(h["dict-written"].value, "TradeMate");
});

/* ---------- rows and inline edit ---------- */

test("a plain row offers Edit; an auto-added row is marked and offers Remove too", async (t) => {
  const h = mount(t, {
    dictionary: { voiser: "Voisr", brunswik: "Brunswick" },
    state: { pending: [], auto_added: { voiser: "Voisr" }, used: { brunswik: 4 } },
  });
  await h.V.views.dictionary.render();
  const rows = Array.from(h["dict-list"].children);
  assert.equal(rows.length, 2);
  const auto = rows.find((r) => textsOf(r).includes("voiser"));
  const plain = rows.find((r) => textsOf(r).includes("brunswik"));
  assert.equal(byClass(auto, "tag")[0].textContent, "Auto-added");
  assert.match(byClass(auto, "tag")[0].getAttribute("title"), /won't come back/);
  assert.equal(byClass(plain, "tag").length, 0);
  assert.deepEqual(buttonText(auto), ["Edit", "Remove"]);
  assert.deepEqual(buttonText(plain), ["Edit"]);
  assert.equal(byClass(plain, "row-note")[0].textContent, "used 4×");
});

async function editing(t, opts = { dictionary: { voiser: "Voisr" } }) {
  const h = mount(t, opts);
  await h.V.views.dictionary.render();
  const row = h["dict-list"].children[0];
  button(row, "Edit").dispatch("click");
  return { h, row, input: byClass(row, "row-input")[0] };
}

test("Edit turns the word into a field, with Save, Cancel and Remove", async (t) => {
  const { row, input } = await editing(t);
  assert.equal(input.value, "Voisr");
  assert.deepEqual(buttonText(row), ["Save", "Cancel", "Remove"]);
  assert.ok(textsOf(row).includes("voiser"), "what it heard stays fixed");
});

test("Enter saves; a fresh config is what gets written", async (t) => {
  const { h, input } = await editing(t);
  input.value = "VoisR";
  input.dispatch("keydown", { key: "Enter", preventDefault() {} });
  await settle();
  assert.deepEqual(h.live.dictionary, { voiser: "VoisR" });
  assert.match(toastText(h), /Updated: voiser → VoisR/);
});

test("Escape and Cancel leave the word as it was, and clicking away saves nothing", async (t) => {
  const { h, row, input } = await editing(t);
  input.value = "something else";
  input.dispatch("blur");
  await settle();
  assert.equal(h.calls.length, 0, "blur would race Cancel");
  input.dispatch("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(byClass(row, "row-input").length, 0);
  assert.deepEqual(buttonText(row), ["Edit"]);
  assert.equal(h.calls.length, 0);

  button(row, "Edit").dispatch("click");
  button(row, "Cancel").dispatch("click");
  assert.deepEqual(buttonText(row), ["Edit"]);
});

test("saving nothing changed just closes; saving nothing at all stays open", async (t) => {
  const { h, row, input } = await editing(t);
  input.value = "   ";
  button(row, "Save").dispatch("click");
  await settle();
  assert.equal(byClass(row, "row-input").length, 1);
  input.value = "Voisr";
  button(row, "Save").dispatch("click");
  await settle();
  assert.equal(byClass(row, "row-input").length, 0);
  assert.equal(h.calls.length, 0);
});

test("a failed save keeps the edit open with what was typed", async (t) => {
  const { h, row, input } = await editing(t, { dictionary: { voiser: "Voisr" }, setFails: true });
  input.value = "VoisR";
  button(row, "Save").dispatch("click");
  await settle();
  assert.equal(byClass(row, "row-input")[0].value, "VoisR");
  assert.match(toastText(h), /disk full/);
});

test("a removal that fails leaves the edit open, and Cancel still works", async (t) => {
  const { h, row } = await editing(t, { dictionary: { voiser: "Voisr" }, setFails: true });
  button(row, "Remove").dispatch("click");
  await settle();
  assert.deepEqual(buttonText(row), ["Save", "Cancel", "Remove"]);
  assert.match(toastText(h), /disk full/);
  button(row, "Cancel").dispatch("click");
  assert.deepEqual(buttonText(row), ["Edit"]);
});

test("one row edits at a time", async (t) => {
  const h = mount(t, { dictionary: { voiser: "Voisr", brunswik: "Brunswick" } });
  await h.V.views.dictionary.render();
  const [first, second] = h["dict-list"].children;
  button(first, "Edit").dispatch("click");
  button(second, "Edit").dispatch("click");
  assert.equal(byClass(first, "row-input").length, 0);
  assert.equal(byClass(second, "row-input").length, 1);
});

test("editing a row closes the adding form, and the other way round", async (t) => {
  const h = mount(t, { dictionary: { voiser: "Voisr" } });
  await h.V.views.dictionary.render();
  const row = h["dict-list"].children[0];
  h["dict-add"].dispatch("click");
  button(row, "Edit").dispatch("click");
  assert.equal(h["dict-form"].hidden, true);
  h["dict-add"].dispatch("click");
  assert.deepEqual(buttonText(row), ["Edit"]);
});

test("Remove in the edit removes the word and offers Undo", async (t) => {
  const { h, row } = await editing(t);
  button(row, "Remove").dispatch("click");
  await settle();
  assert.deepEqual(h.live.dictionary, {});
  assert.match(toastText(h), /removed/i);
});

/* ---------- suggestions ---------- */

test("pending suggestions show with Accept and Dismiss, and the section hides without any", async (t) => {
  const h = mount(t, { state: {
    pending: [{ id: "p1", heard: "trade mate", written: "TradeMate", seen: 2 }],
    auto_added: {}, used: {},
  } });
  await h.V.views.dictionary.render();
  assert.equal(h["dict-suggestions"].hidden, false);
  const row = h["dict-suggestion-list"].children[0];
  assert.deepEqual(buttonText(row), ["Accept", "Dismiss"]);
  assert.equal(byClass(row, "row-note")[0].textContent, "seen 2×");

  button(row, "Accept").dispatch("click");
  await settle();
  assert.deepEqual(h.calls, [["accept", "p1"]]);
});

test("the suggestions section hides when there are none", async (t) => {
  const empty = mount(t);
  await empty.V.views.dictionary.render();
  assert.equal(empty["dict-suggestions"].hidden, true);
});

test("the entry list still draws when suggestions can't be read", async (t) => {
  const h = mount(t, { dictionary: { voiser: "Voisr" }, failing: true });
  await h.V.views.dictionary.render();
  assert.equal(Array.from(h["dict-list"].children).length, 1);
});
