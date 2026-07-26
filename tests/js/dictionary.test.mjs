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
  const env = loadVoisr(["js/ui.js", "js/api.js", "js/views/dictionary.js"]);
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
