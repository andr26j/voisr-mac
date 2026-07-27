import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { loadVoisr } from "./load.mjs";

const PALETTE_SRC = readFileSync(path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..", "..", "voisr", "web", "js", "palette.js"), "utf8");

/* palette.js reaches for V.ui.el when it draws, so ui.js loads first. */
const { V } = loadVoisr(["js/ui.js", "js/palette.js"]);
const { match } = V.palette;

/* The real command set, minus the run handlers the matcher never looks at. */
const COMMANDS = [
  { id: "dictate", title: "Start dictation" },
  { id: "search", title: "Search history" },
  { id: "home", title: "Go to Home" },
  { id: "history", title: "Go to History" },
  { id: "dictionary", title: "Go to Dictionary" },
  { id: "settings", title: "Go to Settings" },
  { id: "hotkey", title: "Change push-to-talk key" },
  { id: "appearance", title: "Toggle appearance" },
  { id: "copy-last", title: "Copy last dictation" },
];

const ids = (q, items = COMMANDS) => match(q, items).map((c) => c.id);

/* The fixture above is a hand-copy, so it can drift away from the real
   command list without anything failing. Pin it to the source: a renamed
   command now breaks this test instead of quietly leaving the ranking
   assertions exercising titles the app no longer has. */
test("the fixture titles still exist verbatim in palette.js", () => {
  for (const { id, title } of COMMANDS) {
    assert.ok(PALETTE_SRC.includes(`"${title}"`),
      `palette.js no longer contains the title "${title}" (command ${id}) -- ` +
      "update this fixture to match the real command list.");
  }
});

test("match returns every item for an empty query", () => {
  assert.deepEqual(ids(""), COMMANDS.map((c) => c.id));
  assert.deepEqual(ids("   "), COMMANDS.map((c) => c.id));
  assert.deepEqual(ids(null), COMMANDS.map((c) => c.id));
  assert.deepEqual(ids(undefined), COMMANDS.map((c) => c.id));
});

test("match returns a copy, not the caller's array", () => {
  const out = match("", COMMANDS);
  out.push({ id: "intruder", title: "Intruder" });
  assert.equal(COMMANDS.length, 9, "the source list must not be mutated");
});

test("match is a subsequence matcher, not a substring matcher", () => {
  // "shy" appears in "Search history" only as scattered characters.
  assert.deepEqual(ids("shy", [{ id: "search", title: "Search history" }]), ["search"]);
  // ...and "ptk" only as scattered characters in "Change push-to-talk key".
  assert.deepEqual(ids("ptk", [{ id: "hotkey", title: "Change push-to-talk key" }]), ["hotkey"]);
});

test("match requires the query's characters in order", () => {
  /* THE NEGATIVE CASE MUST NOT USE THE SEARCH COMMAND. `match` pins search as
     a fallback for any non-empty query, so `ids("yhs", [{id:"search"}])`
     answers ["search"] whether the matcher respects order or not -- replacing
     the ordered-subsequence walk with a plain "the title contains every
     character of q" test left the whole suite green. `hotkey` is an ordinary
     command with nothing to pin it, so an out-of-order query really does have
     to come back empty. */
  const hotkey = [{ id: "hotkey", title: "Change push-to-talk key" }];
  // "kep" is "Change push-to-talk key" backwards-ish: every one of those
  // characters is in the title, but not in this order.
  assert.deepEqual(ids("yek", hotkey), [],
    "reversed order must not match, and there is nothing to pin here");
  assert.deepEqual(ids("cpk", hotkey), ["hotkey"], "the same characters in order do match");

  // The search command still answers for its own out-of-order query, but only
  // because it is pinned -- not because "yhs" matched anything.
  const items = [{ id: "search", title: "Search history" }];
  assert.deepEqual(ids("yhs", items), ["search"], "reversed order doesn't match, search pinned as fallback");
  assert.deepEqual(ids("sh", items), ["search"]);
});

test("match drops items that do not contain the whole query", () => {
  // Queries that match nothing still pin the search command as a fallback.
  assert.deepEqual(ids("zzz"), ["search"], "no matches, search pinned as fallback");
  assert.deepEqual(ids("dictationz"), ["search"], "one unmatched character is enough to exclude, search pinned");
  assert.ok(!ids("appearance").includes("home"));
});

test("match ranks an earlier first-character hit above a later one", () => {
  // "sh": "Search history" hits its 's' at index 0; "Change push-to-talk key"
  // not until "push". The earlier hit must come first.
  const items = [
    { id: "hotkey", title: "Change push-to-talk key" },
    { id: "search", title: "Search history" },
  ];
  assert.deepEqual(ids("sh", items), ["search", "hotkey"]);
  // ...and the input order must not decide it.
  assert.deepEqual(ids("sh", [...items].reverse()), ["search", "hotkey"]);
});

test("match ignores query case and surrounding whitespace", () => {
  assert.deepEqual(ids("SEARCH HISTORY"), ids("search history"));
  assert.deepEqual(ids("  sh  "), ids("sh"));
  assert.ok(ids("SH").includes("search"));
});

test("match keeps the real command set usefully filtered", () => {
  const out = ids("dict");
  assert.ok(out.includes("dictate"), "Start dictation");
  assert.ok(out.includes("dictionary"), "Go to Dictionary");
  assert.ok(!out.includes("home"), "Go to Home has no 'd'");
});

test("match pins the search command as a fallback for arbitrary queries", () => {
  // A query with no matching commands should still offer search.
  const result = ids("brunswick");
  assert.ok(result.includes("search"), "search command should be in results");
  assert.equal(result[result.length - 1], "search", "search should be last when it's a fallback");
});

test("match does not pin the search command when the query is empty", () => {
  // Empty query returns all commands unchanged.
  const result = ids("");
  const expectedOrder = COMMANDS.map((c) => c.id);
  assert.deepEqual(result, expectedOrder, "empty query should return all commands in original order");
});

test("match preserves search command's natural ranking when it matches normally", () => {
  // "sh" matches "Search history" at the 's' (index 0) and "push-to-talk" much later.
  // So "Search history" should rank first by its normal scoring, not pinned at the end.
  const result = ids("sh");
  assert.equal(result[0], "search", "search should rank first when it matches early");
  // Verify "hotkey" (Change push-to-talk key) comes after.
  assert.ok(result.indexOf("search") < result.indexOf("hotkey"),
    "search should come before hotkey in the ranking");
});

test("match ensures search command appears exactly once in all result sets", () => {
  // Verify no duplicates across multiple queries.
  for (const query of ["", "sh", "brunswick", "go", "toggle"]) {
    const result = ids(query);
    const searchCount = result.filter((id) => id === "search").length;
    assert.equal(searchCount, 1,
      `search should appear exactly once for query "${query}", got ${searchCount}`);
  }
});

test("match preserves order of matched commands before adding pinned search", () => {
  // Query that matches multiple commands: "go" should match "Go to Home", "Go to History", "Go to Dictionary".
  const result = ids("go");
  // These should appear in their earliest-first-match order, not affected by search pinning.
  assert.ok(
    result.indexOf("home") < result.indexOf("history") &&
    result.indexOf("history") < result.indexOf("dictionary"),
    "go-prefixed commands should maintain their matched order"
  );
  /* ...and the half this test's name promised but never checked: "Search
     history" has no 'g', so it cannot match "go" naturally and is pinned as
     the fallback. It must land AFTER every naturally-matched command.
     "brunswick" above cannot prove this -- nothing matches there, so the
     pinned command is the only element and it is trivially last either way.
     Here four commands match first, so this is the assertion that actually
     distinguishes appending the fallback from prepending it. */
  assert.equal(result[result.length - 1], "search",
    "the pinned search fallback must come after every naturally-matched command");
});
