import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";

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
