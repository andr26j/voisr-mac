import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* dictionary.js's SEARCH_AT threshold -- kept in sync with the module's own
   constant so these tests break loudly (rather than silently mismeasuring)
   if that threshold is ever retuned. */
const SEARCH_AT = 10;

const IDS = ["dict-search", "dict-empty", "dict-list", "toast"];

function mountDictionary(t, dictionary) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/dictionary.js"]);
  let live = { ...dictionary };
  // Only get_config is exercised below -- these tests drive render() and the
  // search input, never addEntry/removeEntry/commit, so set_config is not
  // needed for the mock to be honest.
  env.window.pywebview = { api: {
    get_config: async () => ({ dictionary: { ...live } }),
  } };

  const nodes = {};
  for (const id of IDS) {
    nodes[id] = makeNode(id === "dict-search" ? "input" : "div");
    env.register(id, nodes[id]);
  }
  nodes["dict-search"].value = "";

  env.V.views.dictionary.initSearch();
  return { V: env.V, setDictionary: (d) => { live = { ...d }; }, ...nodes };
}

const dictOf = (n) => Object.fromEntries(
  Array.from({ length: n }, (_, i) => [`spoken${i}`, `Written${i}`]));

test("hiding the filter input (below SEARCH_AT entries) resets the filter and the input's own value", async (t) => {
  const h = await (async () => {
    const m = mountDictionary(t, dictOf(SEARCH_AT));
    await m.V.views.dictionary.render();
    return m;
  })();

  assert.equal(h["dict-search"].hidden, false, "search stays visible at/above the threshold");

  // Type a filter that matches nothing once the list shrinks below the
  // threshold -- this is what makes the regression observable: if `filter`
  // survives the hide, the shrunk list renders as "no matches" instead of
  // "all entries", even though the input that would let the user see or
  // clear the filter is now gone.
  h["dict-search"].value = "zzz-does-not-match-anything";
  h["dict-search"].dispatch("input");
  await h.V.views.dictionary.render();
  assert.equal(h["dict-list"].children.length, 0, "the filter narrowed the list before the shrink");

  // Now the underlying dictionary drops below SEARCH_AT: the input hides.
  h.setDictionary(dictOf(SEARCH_AT - 1));
  await h.V.views.dictionary.render();

  assert.equal(h["dict-search"].hidden, true, "search hides below the threshold");
  assert.equal(h["dict-search"].value, "", "a hidden input must not silently keep stale text");
  assert.equal(h["dict-list"].children.length, SEARCH_AT - 1,
    "the filter must be cleared too -- every entry shows, not zero");
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
  const dictionary = { TradeMate: "TM Group", ...dictOf(SEARCH_AT - 1) };
  const h = mountDictionary(t, dictionary);
  await h.V.views.dictionary.render();
  assert.equal(h["dict-search"].hidden, false);

  h["dict-search"].value = "trademate";
  h["dict-search"].dispatch("input");
  await h.V.views.dictionary.render();

  assert.equal(h["dict-list"].children.length, 1,
    "a lowercase query must still find a mixed-case spoken key");
});

function mountWithSuggestions(t, { dictionary = {}, state, failing = false }) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/dictionary.js"]);
  const calls = [];
  env.window.pywebview = { api: {
    get_config: async () => ({ dictionary: { ...dictionary } }),
    get_suggestions: async () => { if (failing) throw new Error("no"); return state; },
    accept_suggestion: async (id) => { calls.push(["accept", id]); return { ok: true }; },
    dismiss_suggestion: async (id) => { calls.push(["dismiss", id]); return { ok: true }; },
  } };
  const nodes = {};
  for (const id of [...IDS, "dict-suggestions", "dict-suggestion-list"]) {
    nodes[id] = makeNode(id === "dict-search" ? "input" : "div");
    env.register(id, nodes[id]);
  }
  nodes["dict-search"].value = "";
  env.V.views.dictionary.initSearch();
  return { V: env.V, calls, ...nodes };
}

const flatten = (node) => [node, ...Array.from(node.children || []).flatMap(flatten)];
const textsOf = (node) => flatten(node).map((n) => n.textContent).filter(Boolean);
const byClass = (node, cls) => flatten(node).filter((n) => (n.className || "").includes(cls));

test("pending suggestions show with Accept and Dismiss, and the card hides without any", async (t) => {
  const h = mountWithSuggestions(t, { state: {
    pending: [{ id: "p1", heard: "trade mate", written: "TradeMate", seen: 2 }],
    auto_added: {}, used: {},
  } });
  await h.V.views.dictionary.render();
  assert.equal(h["dict-suggestions"].hidden, false);
  const buttons = byClass(h["dict-suggestion-list"], "btn-secondary");
  assert.deepEqual(buttons.map((b) => b.textContent), ["Accept", "Dismiss"]);
});

test("the suggestions card hides when there are none", async (t) => {
  const empty = mountWithSuggestions(t, { state: { pending: [], auto_added: {}, used: {} } });
  await empty.V.views.dictionary.render();
  assert.equal(empty["dict-suggestions"].hidden, true);
});

test("an auto-added entry is marked and keeps its one Remove button; others aren't marked", async (t) => {
  const h = mountWithSuggestions(t, {
    dictionary: { voiser: "Voisr", brunswik: "Brunswick" },
    state: { pending: [], auto_added: { voiser: "Voisr" }, used: { brunswik: 4 } },
  });
  await h.V.views.dictionary.render();
  const rows = Array.from(h["dict-list"].children);
  assert.equal(rows.length, 2);
  const [auto, plain] = rows[0].textContent.includes("voiser") || textsOf(rows[0]).includes("voiser")
    ? rows : [rows[1], rows[0]];
  assert.equal(byClass(auto, "dict-auto").length, 1);
  assert.equal(byClass(plain, "dict-auto").length, 0);
  assert.equal(byClass(auto, "danger").length, 1);
  assert.equal(byClass(plain, "dict-used")[0].textContent, "used 4×");
});

test("an entry the user edited since is no longer marked auto-added", async (t) => {
  const h = mountWithSuggestions(t, {
    dictionary: { voiser: "VoisR" },
    state: { pending: [], auto_added: { voiser: "Voisr" }, used: {} },
  });
  await h.V.views.dictionary.render();
  assert.equal(byClass(h["dict-list"], "dict-auto").length, 0);
});

test("the entry list still draws when suggestions can't be read", async (t) => {
  const h = mountWithSuggestions(t, { dictionary: { voiser: "Voisr" }, failing: true });
  await h.V.views.dictionary.render();
  assert.equal(Array.from(h["dict-list"].children).length, 1);
});
