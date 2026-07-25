import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";

const { V } = loadVoisr(["js/ui.js"]);
const { el, formatDuration } = V.ui;

/* ---------- formatDuration ---------- */

test("formatDuration renders sub-minute durations", () => {
  assert.equal(formatDuration(0), "—");
  assert.equal(formatDuration(null), "—");
  assert.equal(formatDuration(undefined), "—");
  assert.equal(formatDuration(45), "45s");
  assert.equal(formatDuration(59), "59s");
  assert.equal(formatDuration(-10), "—", "negative input clamps to zero");
});

test("formatDuration renders whole minutes below an hour", () => {
  assert.equal(formatDuration(60), "1m");
  assert.equal(formatDuration(90), "2m", "rounds to the nearest minute");
  assert.equal(formatDuration(600), "10m");
  assert.equal(formatDuration(3540), "59m");
});

test("formatDuration never prints 60 minutes (shipped-bug regression)", () => {
  // 3599s rounds to 60 minutes. Deriving h and m independently from the raw
  // seconds printed "60m" here and "1h 60m" at 7199 — on the app's headline
  // stat. Minutes must be rounded first, then split.
  assert.equal(formatDuration(3599), "1h 0m");
  assert.equal(formatDuration(3600), "1h 0m");
  assert.equal(formatDuration(7199), "2h 0m");
  assert.equal(formatDuration(7200), "2h 0m");
});

test("formatDuration renders hours and minutes", () => {
  assert.equal(formatDuration(3660), "1h 1m");
  assert.equal(formatDuration(13320), "3h 42m");
  assert.equal(formatDuration(86400), "24h 0m");
});

/* ---------- el() ---------- */

test("el puts text in textContent and never in innerHTML", () => {
  const node = el("p", { text: "<b>not markup</b>" });
  assert.equal(node.tagName, "P");
  assert.equal(node.textContent, "<b>not markup</b>");
  assert.equal(node.innerHTML, "", "text must never reach innerHTML");
});

test("el sets html only via the explicit html prop", () => {
  const node = el("span", { html: "<svg></svg>" });
  assert.equal(node.innerHTML, "<svg></svg>");
  assert.equal(node.textContent, "", "html must not double up as text");
});

test("el maps class to className and everything else through setAttribute", () => {
  const node = el("button", {
    class: "icon-btn danger",
    type: "button",
    "aria-label": "Delete dictation",
  });
  assert.equal(node.className, "icon-btn danger");
  assert.equal(node.getAttribute("type"), "button");
  assert.equal(node.getAttribute("aria-label"), "Delete dictation");
  assert.equal(node.getAttribute("class"), null, "class is not an attribute path");
  assert.equal(node.getAttribute("text"), null, "text is not an attribute path");
});

test("el writes the data prop into dataset, not as attributes", () => {
  const node = el("li", { data: { id: "abc123", key: "microphone" } });
  assert.deepEqual(node.dataset, { id: "abc123", key: "microphone" });
  assert.equal(node.getAttribute("data"), null);
});

test("el registers on: handlers as real listeners", () => {
  let clicks = 0, keys = 0;
  const node = el("button", { on: { click: () => { clicks += 1; }, keydown: () => { keys += 1; } } });

  assert.equal(node.getAttribute("on"), null, "on must not become an attribute");
  node.dispatch("click");
  node.dispatch("click");
  node.dispatch("keydown");
  assert.equal(clicks, 2);
  assert.equal(keys, 1);
});

test("el skips null, undefined and false props and children", () => {
  const node = el("div", { class: "x", title: null, hidden: undefined, disabled: false });
  assert.equal(node.getAttribute("title"), null);
  assert.equal(node.getAttribute("hidden"), null);
  assert.equal(node.getAttribute("disabled"), null);

  const parent = el("ul", null, el("li", { text: "a" }), null, false, undefined, el("li", { text: "b" }));
  assert.equal(parent.children.length, 2);
  assert.deepEqual(parent.children.map((c) => c.textContent), ["a", "b"]);
});

test("el appends children, flattening arrays", () => {
  const parent = el("div", { class: "wrap" },
    "plain text",
    [el("span", { text: "one" }), el("span", { text: "two" })]);
  assert.equal(parent.children.length, 3);
  assert.equal(parent.children[0], "plain text");
  assert.deepEqual(parent.children.slice(1).map((c) => c.textContent), ["one", "two"]);
});

test("el tolerates being called with no props", () => {
  const node = el("div");
  assert.equal(node.tagName, "DIV");
  assert.equal(node.children.length, 0);
});
