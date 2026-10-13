import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* ui.js first: both views call V.ui.el / V.ui.$ at module scope. */
const { V } = loadVoisr(["js/ui.js", "js/lists.js", "js/views/home.js", "js/views/history.js"]);

/* ---------- home: trendBars + weekDelta ---------- */

const trendBars = V.views.home.trendBars;
const weekDelta = V.views.home.weekDelta;

const daily = (...words) => words.map((w, i) => ({ date: `d${i}`, words: w }));

test("trendBars scales every day against the tallest one in the series", () => {
  const bars = trendBars(daily(0, 50, 100, 25, 0, 75, 100, 10, 0, 40, 100, 60, 30, 20));
  assert.equal(bars.length, 14);
  assert.ok(bars.every((b) => b >= 0 && b <= 1), `out of range: ${bars.join()}`);
  assert.ok(bars.every((b) => Number.isFinite(b)), `non-finite: ${bars.join()}`);
  assert.equal(bars[2], 1, "the tallest day is the full height");
  assert.equal(bars[1], 0.5);
  assert.equal(bars[0], 0);
});

test("trendBars keeps an all-zero series flat instead of dividing by zero", () => {
  const bars = trendBars(daily(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0));
  assert.equal(bars.length, 14);
  assert.ok(bars.every((b) => b === 0), `expected a flat floor, got ${bars.join()}`);
});

test("trendBars treats a missing word count as zero rather than NaN", () => {
  const bars = trendBars([{ date: "d0" }, { date: "d1", words: 10 }]);
  assert.deepEqual(bars, [0, 1]);
});

test("trendBars returns nothing for an empty or absent series", () => {
  assert.deepEqual(trendBars([]), []);
  assert.deepEqual(trendBars(null), []);
  assert.deepEqual(trendBars(undefined), []);
});

test("weekDelta compares the last seven days against the seven before them", () => {
  // 70 in the first week, 140 in the second
  assert.equal(weekDelta(daily(10, 10, 10, 10, 10, 10, 10, 20, 20, 20, 20, 20, 20, 20)), 100);
  assert.equal(weekDelta(daily(20, 20, 20, 20, 20, 20, 20, 10, 10, 10, 10, 10, 10, 10)), -50);
});

test("weekDelta answers null rather than a fabricated percentage", () => {
  // nothing last week is not a 0% or an infinite change -- there is no
  // comparison to draw, and the tile shows no delta at all
  assert.equal(weekDelta(daily(0, 0, 0, 0, 0, 0, 0, 10, 10, 10, 10, 10, 10, 10)), null);
  // a fresh install has no fortnight of history yet
  assert.equal(weekDelta(daily(10, 20, 30)), null);
  assert.equal(weekDelta([]), null);
  assert.equal(weekDelta(null), null);
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
                  "history-groups", "history-export", "export-popover",
                  "export-fmt-txt", "export-fmt-md", "export-fmt-json"];

/* Mock timers are enabled BEFORE the scripts load so every setTimeout they
   register -- history.js's search debounce and api.js's dev-fallback -- is
   the mock's. t.mock restores them when the test ends. */
async function mountHistory(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/history.js"]);
  // js/api.js resolves the bridge lazily as `window.pywebview.api`, so
  // stubbing the property is enough -- no need to replace V.api itself.
  env.window.pywebview = { api: { get_history: async () => HISTORY.map((i) => ({ ...i })) } };

  const nodes = {};
  for (const id of NODE_IDS) {
    nodes[id] = makeNode(id === "history-search" ? "input" : "div");
    env.register(id, nodes[id]);
  }
  nodes["history-search"].value = "";
  // makeNode() defaults `hidden: false`; index.html ships the export
  // popover with the `hidden` attribute, i.e. closed until opened.
  nodes["export-popover"].hidden = true;

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

/* ---------- history: undo must never claim a restore that failed ----------

   Undo is the only safety net left on delete (the native confirm() was
   removed). `restore_history_item` can return {"error": ...} — a read-only
   history file, a full disk — and the old code toasted "Restored"
   unconditionally, telling the user their dictation was back when it was
   gone for good. */

const UNDO_IDS = [...NODE_IDS, "toast"];

async function mountHistoryWithToast(t, api) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/history.js"]);
  let live = HISTORY.map((i) => ({ ...i }));
  const restoreCalls = [];
  env.window.pywebview = { api: {
    get_history: async () => live.map((i) => ({ ...i })),
    delete_history_item: async (id) => { live = live.filter((i) => i.id !== id); return { ok: true }; },
    /* Mirrors Api.restore_history_item: idempotent by id, preserves ts, so
       the row really comes back and a row count can be asserted after undo. */
    restore_history_item: async (item) => {
      restoreCalls.push(item.id);
      if (!live.some((i) => i.id === item.id)) {
        live.push({ ...item });
        live.sort((a, b) => a.ts - b.ts);
      }
      return { ok: true };
    },
    ...api,
  } };

  const nodes = {};
  for (const id of UNDO_IDS) {
    nodes[id] = makeNode(id === "history-search" ? "input" : "div");
    env.register(id, nodes[id]);
  }
  nodes["history-search"].value = "";
  // makeNode() defaults `hidden: false`; index.html ships the export
  // popover with the `hidden` attribute, i.e. closed until opened.
  nodes["export-popover"].hidden = true;

  await env.V.views.history.render();
  return { V: env.V, restoreCalls, ...nodes };
}

/* Lets every already-resolved promise in the click -> api -> render chain
   settle: microtasks drain fully before a macrotask runs. */
const settle = () => new Promise((r) => setImmediate(r));

const toastTextOf = (host) =>
  host.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(" ");
const toastActionOf = (host) =>
  host.children.find((c) => c && typeof c === "object" && String(c.className).includes("toast-action"));

test("undo surfaces a failed restore instead of toasting Restored", async (t) => {
  const h = await mountHistoryWithToast(t, {
    restore_history_item: async () => ({ error: "disk full" }),
  });
  const toast = h["toast"];

  const row = rowsIn(h["history-groups"])[0];
  byClass(row, "danger")[0].dispatch("click", { stopPropagation() {} });
  await settle();

  assert.match(toastTextOf(toast), /Dictation deleted/, "delete still confirms");
  const undo = toastActionOf(toast);
  assert.ok(undo, "the delete toast offers an Undo action");

  undo.dispatch("click");
  await settle();

  const text = toastTextOf(toast);
  assert.match(text, /Restore dictation failed: disk full/,
    "the real reason the restore failed must reach the user");
  assert.doesNotMatch(text, /Restored/,
    "a failed restore must never be reported as a success");
});

test("undo still confirms Restored when the restore actually succeeds", async (t) => {
  const h = await mountHistoryWithToast(t, {});
  const toast = h["toast"];

  const row = rowsIn(h["history-groups"])[0];
  byClass(row, "danger")[0].dispatch("click", { stopPropagation() {} });
  await settle();

  toastActionOf(toast).dispatch("click");
  await settle();

  assert.match(toastTextOf(toast), /Restored/, "the happy path is unchanged");
});

/* H4b: two deletes in the same window are one undo, and it restores both.
   Driven through the real view — real rows, real delete/restore bridge calls —
   because the count on screen is half of what this guarantees. */
test("two deletes in a row coalesce into one undo that restores both rows", async (t) => {
  const h = await mountHistoryWithToast(t, {});
  const toast = h["toast"], groups = h["history-groups"];

  const deleteFirstRow = () =>
    byClass(rowsIn(groups)[0], "danger")[0].dispatch("click", { stopPropagation() {} });

  deleteFirstRow();
  await settle();
  assert.match(toastTextOf(toast), /Dictation deleted/, "a single delete still reads in the singular");

  deleteFirstRow();
  await settle();

  assert.equal(rowsIn(groups).length, 1, "two rows are gone");
  assert.match(toastTextOf(toast), /2 dictations deleted/, "one always-current toast, not two");
  const undos = toast.children.filter(
    (c) => c && typeof c === "object" && String(c.className).includes("toast-action"));
  assert.equal(undos.length, 1, "exactly one Undo button");

  undos[0].dispatch("click");
  await settle();

  assert.deepEqual([...h.restoreCalls].sort(), ["a1", "a2"],
    "both deletes were restored, not just the first");
  assert.equal(rowsIn(groups).length, 3, "the list is whole again");
  assert.match(toastTextOf(toast), /Restored 2 dictations/);
  assert.doesNotMatch(toastTextOf(toast), /deleted/,
    "the confirmation replaces the undo — it is not buried behind another delete toast");
});

test("a failed delete does not offer an Undo for something still present", async (t) => {
  const h = await mountHistoryWithToast(t, {
    delete_history_item: async () => ({ error: "read-only history" }),
  });
  const toast = h["toast"];

  const row = rowsIn(h["history-groups"])[0];
  byClass(row, "danger")[0].dispatch("click", { stopPropagation() {} });
  await settle();

  const text = toastTextOf(toast);
  assert.match(text, /Delete dictation failed: read-only history/);
  assert.doesNotMatch(text, /Dictation deleted/, "nothing was deleted, so say nothing was");
  assert.equal(toastActionOf(toast), undefined, "no Undo for a delete that never happened");
  assert.equal(rowsIn(h["history-groups"]).length, 3, "the row is still there");
});

test("history render surfaces a bridge failure rather than blanking the pane", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/history.js"]);
  let calls = 0;
  env.window.pywebview = { api: { get_history: async () => {
    calls++;
    // First render succeeds so there is something on screen to preserve;
    // only the second (a refresh) fails -- otherwise this test can't tell
    // "never blanked" from "never had anything to blank".
    if (calls === 1) return HISTORY.map((i) => ({ ...i }));
    return { error: "history unreadable" };
  } } };

  const nodes = {};
  for (const id of UNDO_IDS) {
    nodes[id] = makeNode(id === "history-search" ? "input" : "div");
    env.register(id, nodes[id]);
  }

  await env.V.views.history.render();
  assert.equal(rowsIn(nodes["history-groups"]).length, 3,
    "first render establishes the rows this test defends");

  await env.V.views.history.render();

  assert.match(toastTextOf(nodes["toast"]), /Load history failed: history unreadable/);
  assert.equal(rowsIn(nodes["history-groups"]).length, 3,
    "the existing rows must survive a failed refresh, not be blanked");
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

/* ---------- history: export format picker ----------

   export_history("txt") was hardcoded, so .md and .json were unreachable
   from the UI even though Api.export_history already supported them. These
   drive the popover the same way a click would: open it off the Export
   button, then click one of the three format buttons, and check that the
   chosen fmt reaches export_history and its {text, suggested_name} reaches
   save_text_file untouched. Reuses mountHistoryWithToast (already wires a
   `toast` node and lets the api be overridden) but -- unlike the undo tests,
   whose click handlers come from itemRow -- the export button and its
   popover buttons are bound by initKeys(), so each test binds it explicitly,
   exactly like the very first search test above. */

for (const fmt of ["txt", "md", "json"]) {
  test(`export format "${fmt}" reaches export_history and save_text_file with its own fmt`, async (t) => {
    const exportCalls = [], saveCalls = [];
    const h = await mountHistoryWithToast(t, {
      export_history: async (f) => {
        exportCalls.push(f);
        return { text: `serialised-${f}`, suggested_name: `voisr-history.${f}` };
      },
      save_text_file: async (text, name) => {
        saveCalls.push([text, name]);
        return { ok: true, path: `/tmp/${name}` };
      },
    });
    h.V.views.history.initKeys();

    h["history-export"].dispatch("click");
    assert.equal(h["export-popover"].hidden, false, `popover opens for ${fmt}`);

    h[`export-fmt-${fmt}`].dispatch("click");
    await settle();

    assert.deepEqual(exportCalls, [fmt], `export_history must be called with "${fmt}", not a hardcoded format`);
    assert.deepEqual(saveCalls, [[`serialised-${fmt}`, `voisr-history.${fmt}`]],
      `save_text_file must receive export_history's own text/suggested_name for ${fmt}`);
    assert.equal(h["export-popover"].hidden, true, "popover closes once a format is chosen");
  });
}

test("a cancelled save panel stays silent -- no toast, no false success", async (t) => {
  const h = await mountHistoryWithToast(t, {
    export_history: async (fmt) => ({ text: "x", suggested_name: `a.${fmt}` }),
    save_text_file: async () => ({ ok: false, path: null }),
  });
  h.V.views.history.initKeys();

  h["history-export"].dispatch("click");
  h["export-fmt-txt"].dispatch("click");
  await settle();

  assert.equal(toastTextOf(h["toast"]).trim(), "",
    "saved.ok === false means the user cancelled the save panel -- stay silent");
});

test("an export_history error toasts and never reaches save_text_file", async (t) => {
  const saveCalls = [];
  const h = await mountHistoryWithToast(t, {
    export_history: async () => ({ error: "history unreadable" }),
    save_text_file: async (...args) => { saveCalls.push(args); return { ok: true }; },
  });
  h.V.views.history.initKeys();

  h["history-export"].dispatch("click");
  h["export-fmt-md"].dispatch("click");
  await settle();

  assert.match(toastTextOf(h["toast"]), /Export history failed: history unreadable/);
  assert.equal(saveCalls.length, 0, "a failed export must never reach save_text_file");
});

test("a save_text_file error toasts and does not claim the export succeeded", async (t) => {
  const h = await mountHistoryWithToast(t, {
    export_history: async (fmt) => ({ text: "x", suggested_name: `a.${fmt}` }),
    save_text_file: async () => ({ error: "disk full" }),
  });
  h.V.views.history.initKeys();

  h["history-export"].dispatch("click");
  h["export-fmt-json"].dispatch("click");
  await settle();

  const text = toastTextOf(h["toast"]);
  assert.match(text, /Save file failed: disk full/);
  assert.doesNotMatch(text, /History exported/,
    "a failed save must never be reported as an export that happened");
});

/* ---------- history: the keyboard selection under keyed rows ----------

   Rows now survive a refresh (js/lists.js) and a deleted row stays in the
   DOM while it fades out. The selection has to follow the rows on screen,
   not an index into `flat`, which is already the new list mid-animation. */

const keyOf = (row) => row.dataset.key;

async function mountHistoryKeys(t, { animate = false } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/history.js"]);
  let live = HISTORY.map((i) => ({ ...i }));
  const deleted = [];
  env.window.pywebview = { api: {
    get_history: async () => live.map((i) => ({ ...i })),
    delete_history_item: async (id) => {
      deleted.push(id);
      live = live.filter((i) => i.id !== id);
      return { ok: true };
    },
  } };
  // Web Animations stand-in: an animation finishes when its duration has
  // passed on the mocked clock, so a test can stop mid-fade
  const withMotion = (n) => {
    n.animate = (frames, opts) => ({
      finished: new Promise((r) => setTimeout(r, opts.duration)) });
    n.getBoundingClientRect = () => ({ top: n.parent ? n.parent.children.indexOf(n) * 50 : 0 });
    return n;
  };
  if (animate) {
    const make = env.document.createElement;
    env.document.createElement = (tag) => withMotion(make(tag));
  }
  const nodes = {};
  for (const id of [...UNDO_IDS, "view-history"]) {
    nodes[id] = makeNode(id === "history-search" ? "input" : "div");
    if (animate) withMotion(nodes[id]);
    env.register(id, nodes[id]);
  }
  nodes["history-search"].value = "";
  nodes["export-popover"].hidden = true;
  await env.V.views.history.render();
  env.V.views.history.initKeys();
  const press = (key) => env.document.dispatch("keydown", {
    key, target: { tagName: "BODY" }, preventDefault() {} });
  const selected = () => rowsIn(nodes["history-groups"]).filter((r) => r.classList.contains("selected"));
  return { V: env.V, deleted, press, selected, ...nodes };
}

test("a refresh clears the keyboard selection along with the cursor", async (t) => {
  const h = await mountHistoryKeys(t);
  h.press("ArrowDown");
  assert.equal(h.selected().length, 1);

  await h.V.views.history.render();   // a dictation landed elsewhere

  assert.equal(h.selected().length, 0,
    "a kept row must not stay highlighted when Enter and Backspace would ignore it");
});

test("mid-fade, Backspace deletes the row that is highlighted, never its neighbour", async (t) => {
  const h = await mountHistoryKeys(t, { animate: true });
  const groups = h["history-groups"];
  const first = rowsIn(groups)[0];
  h.press("ArrowDown");
  h.press("Backspace");
  await settle();                      // the first row is now fading out
  assert.ok(rowsIn(groups).includes(first), "still in the DOM while it fades");

  h.press("ArrowDown");
  h.press("ArrowDown");
  const [mark] = h.selected();
  assert.ok(mark, "a row is highlighted");
  assert.notEqual(mark, first, "a fading row can't be selected");
  h.press("Backspace");
  await settle();
  assert.equal(h.deleted[1], keyOf(mark), "the second delete hit the highlighted row");
});
