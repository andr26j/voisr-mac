import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* ui.js first: both views call V.ui.el / V.ui.$ at module scope. */
const { V } = loadVoisr(["js/ui.js", "js/views/home.js", "js/views/history.js"]);

/* ---------- home: sparklinePath ---------- */

const sparklinePath = V.views.home.sparklinePath;

const daily = (...words) => words.map((w, i) => ({ day: `d${i}`, words: w }));

test("sparklinePath draws a NaN-free polyline for a mixed 14-day series", () => {
  const series = daily(0, 120, 45, 900, 30, 0, 610, 210, 75, 1000, 5, 480, 260, 90);
  const path = sparklinePath(series);

  assert.ok(path.startsWith("M"), `path should start with a moveto, got: ${path}`);
  assert.ok(!/NaN|Infinity|undefined/.test(path), `path has bad numbers: ${path}`);

  const segs = path.split(" L").length;   // "M.. .. L.. .. L.. .."
  assert.equal(segs, 14, "one point per day");
  assert.equal((path.match(/M/g) || []).length, 1, "exactly one moveto");

  // x spans the full 240-wide viewBox, left to right.
  const xs = path.split(/[ML]/).filter(Boolean).map((p) => parseFloat(p.trim()));
  assert.equal(xs[0], 0);
  assert.equal(parseFloat(path.split("L").pop().trim().split(" ")[0]), 240);

  // The tallest day (1000 words) sits at the top of the band: 38 - 34 = 4.
  const ys = path.split(/[ML]/).filter(Boolean)
    .map((p) => parseFloat(p.trim().split(" ")[1]));
  assert.equal(Math.min(...ys), 4);
  assert.equal(ys[0], 38, "a zero day rests on the baseline");
});

test("sparklinePath keeps an all-zero series flat on the baseline (no divide-by-zero)", () => {
  const path = sparklinePath(daily(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0));
  assert.ok(!/NaN/.test(path), `all-zero series produced NaN: ${path}`);
  const ys = path.split(/[ML]/).filter(Boolean)
    .map((p) => parseFloat(p.trim().split(" ")[1]));
  assert.deepEqual([...new Set(ys)], [38], "every point on the baseline");
});

test("sparklinePath returns an empty path for fewer than two points", () => {
  assert.equal(sparklinePath([]), "");
  assert.equal(sparklinePath(daily(42)), "");
  assert.equal(sparklinePath(null), "");
  assert.equal(sparklinePath(undefined), "");
});

/* ---------- history: matchesQuery ---------- */

const { matchesQuery, highlighted } = V.views.history;

test("matchesQuery matches a multi-word query with its spaces intact", () => {
  // Regression: a mid-typing trim used to eat the spaces, making it
  // impossible to search for more than a single word.
  assert.equal(matchesQuery("Brunswick site visit next week", "site visit"), true);
  assert.equal(matchesQuery("Brunswick site visit next week", "visit next"), true);
  assert.equal(matchesQuery("Brunswick sitevisit next week", "site visit"), false);
});

test("matchesQuery is case-insensitive in both directions", () => {
  assert.equal(matchesQuery("Brunswick Site Visit", "site visit"), true);
  assert.equal(matchesQuery("brunswick site visit", "SITE VISIT"), true);
});

test("matchesQuery trims the needle but not the haystack's inner spaces", () => {
  assert.equal(matchesQuery("Brunswick site visit", "  site visit  "), true);
  assert.equal(matchesQuery("Brunswick site visit", "site  visit"), false);
});

test("matchesQuery treats an empty or blank query as matching everything", () => {
  assert.equal(matchesQuery("anything at all", ""), true);
  assert.equal(matchesQuery("anything at all", "   "), true);
  assert.equal(matchesQuery("anything at all", null), true);
  assert.equal(matchesQuery("anything at all", undefined), true);
});

test("matchesQuery rejects text that does not contain the query", () => {
  assert.equal(matchesQuery("Brunswick site visit", "Altona"), false);
  assert.equal(matchesQuery("", "site"), false);
});

/* ---------- history: highlighted ---------- */

const textOf = (seg) => (typeof seg === "string" ? seg : seg.textContent);
const marks = (segs) => segs.filter((s) => typeof s !== "string");

test("highlighted splits text into segments that reassemble to the original", () => {
  const segs = highlighted("a site visit", "site");
  assert.equal(segs.map(textOf).join(""), "a site visit");

  const ms = marks(segs);
  assert.equal(ms.length, 1);
  assert.equal(ms[0].tagName, "MARK");
  assert.equal(ms[0].textContent, "site");
});

test("highlighted marks every occurrence, preserving the surrounding text", () => {
  const segs = highlighted("site by site by site", "site");
  assert.equal(segs.map(textOf).join(""), "site by site by site");
  assert.equal(marks(segs).length, 3);
  assert.deepEqual(marks(segs).map((m) => m.textContent), ["site", "site", "site"]);
});

test("highlighted keeps the source casing inside the mark", () => {
  const segs = highlighted("Visit the SITE today", "site");
  assert.equal(segs.map(textOf).join(""), "Visit the SITE today");
  assert.deepEqual(marks(segs).map((m) => m.textContent), ["SITE"]);
});

test("highlighted returns the text untouched for a blank query", () => {
  assert.deepEqual(highlighted("a site visit", ""), ["a site visit"]);
  assert.deepEqual(highlighted("a site visit", "   "), ["a site visit"]);
  assert.deepEqual(highlighted("a site visit", null), ["a site visit"]);
});

test("highlighted handles a multi-word query as one span", () => {
  const segs = highlighted("Brunswick site visit next week", "site visit");
  assert.equal(segs.map(textOf).join(""), "Brunswick site visit next week");
  assert.deepEqual(marks(segs).map((m) => m.textContent), ["site visit"]);
});

test("highlighted never parses the query as markup", () => {
  const segs = highlighted('before <img src=x onerror=1> after', "<img");
  assert.equal(segs.map(textOf).join(""), "before <img src=x onerror=1> after");

  const ms = marks(segs);
  assert.equal(ms.length, 1);
  assert.equal(ms[0].textContent, "<img", "the query must land as literal text");
  assert.equal(ms[0].innerHTML, "", "nothing may reach innerHTML");
});

/* ---------- history: the real search input path ----------

   Everything above tests the hoisted pure seams. The space-eating bug the
   comments in views/history.js describe shipped in the *input path* --
   the `input` listener, the debounce and applyQuery -- which those pure
   tests never touch. These mount the module against fake nodes and drive it
   the way a typist does: set input.value, fire `input`, let the debounce
   expire, then look at what actually rendered.                            */

const HISTORY = [
  { id: "a1", ts: 1752000000, text: "Hello team, please send the invoice by Friday.", words: 8, duration_s: 5 },
  { id: "a2", ts: 1752000100, text: "Draft a follow-up about the Brunswick site visit next week.", words: 10, duration_s: 6 },
  { id: "a3", ts: 1752000200, text: "Remember to book the trailer for Saturday morning.", words: 8, duration_s: 4 },
];

const NODE_IDS = ["history-search", "history-empty", "history-more",
                  "history-groups", "history-export"];

/* Mock timers are enabled BEFORE the scripts load so every setTimeout they
   register -- history.js's search debounce and api.js's dev-fallback -- is
   the mock's. t.mock restores them when the test ends. */
async function mountHistory(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/api.js", "js/views/history.js"]);
  // js/api.js resolves the bridge lazily as `window.pywebview.api`, so
  // stubbing the property is enough -- no need to replace V.api itself.
  env.window.pywebview = { api: { get_history: async () => HISTORY.map((i) => ({ ...i })) } };

  const nodes = {};
  for (const id of NODE_IDS) {
    nodes[id] = makeNode(id === "history-search" ? "input" : "div");
    env.register(id, nodes[id]);
  }
  nodes["history-search"].value = "";

  await env.V.views.history.render();   // the one genuine bridge round-trip
  env.V.views.history.initKeys();       // binds the `input` listener
  return { V: env.V, ...nodes };
}

/* Depth-first collect of rendered nodes carrying a class. */
function byClass(node, cls, out = []) {
  for (const kid of node.children || []) {
    if (!kid || typeof kid !== "object") continue;
    if (String(kid.className || "").split(/\s+/).includes(cls)) out.push(kid);
    byClass(kid, cls, out);
  }
  return out;
}

const rowsIn = (groups) => byClass(groups, "item-row");
const rowText = (row) => byClass(row, "txt")[0].children
  .map((seg) => (typeof seg === "string" ? seg : seg.textContent)).join("");
const rowMarks = (row) => byClass(row, "txt")[0].children
  .filter((seg) => typeof seg !== "string").map((m) => m.textContent);

test("typing a multi-word query filters the list without touching input.value", async (t) => {
  const h = await mountHistory(t);
  const search = h["history-search"], groups = h["history-groups"];

  assert.equal(rowsIn(groups).length, 3, "all three items render before any query");

  search.value = "site visit";
  search.dispatch("input");

  // Debounced: nothing may have happened yet on the keystroke itself.
  assert.equal(rowsIn(groups).length, 3, "the input handler must not re-render synchronously");

  t.mock.timers.tick(500);   // past SEARCH_DEBOUNCE_MS

  const rows = rowsIn(groups);
  assert.equal(rows.length, 1, "only the Brunswick site visit item survives the filter");
  assert.equal(rowText(rows[0]), HISTORY[1].text, "the row reassembles to the original text");
  assert.deepEqual(rowMarks(rows[0]), ["site visit"],
    "the whole multi-word phrase is highlighted as one span");
  assert.equal(h["history-empty"].hidden, true, "a match means the empty state stays hidden");

  // The regression itself: the old code trimmed on the way in and wrote the
  // result back to the field, eating the space as the user typed it.
  assert.equal(search.value, "site visit", "the input's value must survive untouched");
});

test("a mid-typing trailing space is neither eaten nor allowed to break the match", async (t) => {
  const h = await mountHistory(t);
  const search = h["history-search"], groups = h["history-groups"];

  // "site visit " is what the field holds the instant before the next word --
  // exactly the state a write-back of the trimmed value used to destroy.
  search.value = "site visit ";
  search.dispatch("input");
  t.mock.timers.tick(500);

  assert.equal(search.value, "site visit ", "the trailing space must still be there");
  assert.equal(rowsIn(groups).length, 1, "trimming at match time still finds the item");
});

test("a query that matches nothing empties the list and shows the empty state", async (t) => {
  const h = await mountHistory(t);
  const search = h["history-search"], groups = h["history-groups"];

  search.value = "Altona";
  search.dispatch("input");
  t.mock.timers.tick(500);

  assert.equal(rowsIn(groups).length, 0);
  assert.equal(h["history-empty"].hidden, false, "the empty state shows when nothing matches");
  assert.equal(h["history-more"].hidden, true, "nothing to page through either");
  assert.equal(search.value, "Altona");
});

test("only the last keystroke of a fast burst is applied", async (t) => {
  const h = await mountHistory(t);
  const search = h["history-search"], groups = h["history-groups"];

  for (const v of ["s", "si", "site", "site ", "site v", "site visit"]) {
    search.value = v;
    search.dispatch("input");
    t.mock.timers.tick(10);   // well inside the debounce window
    assert.equal(rowsIn(groups).length, 3, `"${v}" must not have rendered yet`);
  }

  t.mock.timers.tick(500);
  assert.equal(rowsIn(groups).length, 1, "the burst collapses into a single render");
  assert.equal(search.value, "site visit");
});
