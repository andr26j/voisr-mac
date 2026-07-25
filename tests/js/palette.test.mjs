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
  const items = [{ id: "search", title: "Search history" }];
  assert.deepEqual(ids("yhs", items), [], "reversed order must not match");
  assert.deepEqual(ids("sh", items), ["search"]);
});

test("match drops items that do not contain the whole query", () => {
  assert.deepEqual(ids("zzz"), []);
  assert.deepEqual(ids("dictationz"), [], "one unmatched character is enough to exclude");
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
