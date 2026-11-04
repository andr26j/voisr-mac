import { test } from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* The fake DOM has no layout and no Web Animations: these give a list just
   enough of both to watch the choreography. A row's top is its index x 50. */
function animated(node, log) {
  node.animate = (frames, opts) => {
    log.push({ node, frames, opts });
    return { finished: new Promise((r) => setTimeout(r, opts.duration)) };
  };
  node.getBoundingClientRect = () => {
    const i = node.parent ? node.parent.children.indexOf(node) : -1;
    return { top: i * 50 };
  };
  return node;
}

function setup({ reduced = false } = {}) {
  const env = loadVoisr(["js/lists.js"]);
  env.window.matchMedia = () => ({ matches: reduced, addEventListener() {} });
  const log = [];
  const list = animated(makeNode("ul"), log);
  list.offsetHeight = 250;
  const row = (key) => animated(Object.assign(makeNode("li"), { label: key }), log);
  const entries = (keys, sig = () => 1) => keys.map((k) => ({ key: k, sig: sig(k), build: () => row(k) }));
  const keys = () => list.children.map((n) => n.dataset.key);
  return { L: env.V.lists, list, log, entries, keys };
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const moves = (log) => log.filter((a) => String(a.frames[0].transform).startsWith("translateY") && a.frames.length === 2 && !("opacity" in a.frames[0]));

test("a first fill builds every row in order and plays the stagger, nothing else", async () => {
  const t = setup();
  await t.L.sync(t.list, t.entries(["a", "b", "c"]));
  assert.deepEqual(t.keys(), ["a", "b", "c"]);
  assert.ok(t.list.classList.contains("is-staggering"));
  assert.equal(t.log.length, 0);
});

test("survivors keep their node; only the new row is built", async () => {
  const t = setup();
  await t.L.sync(t.list, t.entries(["a", "b", "c"]));
  const [a, b] = t.list.children;
  await t.L.sync(t.list, t.entries(["n", "a", "b", "c"]));
  assert.deepEqual(t.keys(), ["n", "a", "b", "c"]);
  assert.equal(t.list.children[1], a);
  assert.equal(t.list.children[2], b);
});

test("a landing dictation rises in with the wash; the rows below slide down 240 ms", async () => {
  const t = setup();
  await t.L.sync(t.list, t.entries(["a", "b", "c"]));
  await t.L.sync(t.list, t.entries(["n", "a", "b", "c"]));
  const n = t.list.children[0];
  assert.ok(n.classList.contains("is-landed"));
  const rise = t.log.find((x) => x.node === n);
  assert.deepEqual(rise.frames[0], { opacity: 0, transform: "translateY(-6px)" });
  assert.equal(rise.opts.duration, 220);
  const slides = moves(t.log);
  assert.equal(slides.length, 3);
  assert.ok(slides.every((s) => s.frames[0].transform === "translateY(-50px)" && s.opts.duration === 240));
});

test("a row pushed out of a capped list fades under the edge while the height holds", async () => {
  const t = setup();
  const all = new Set(["a", "b", "c", "d"]);
  await t.L.sync(t.list, t.entries(["a", "b", "c"]));
  all.add("n");
  await t.L.sync(t.list, t.entries(["n", "a", "b"]), { stillExists: (k) => all.has(k), wasKnown: (k) => k !== "n" });
  assert.equal(t.list.style.height, "250px");
  assert.equal(t.list.children.length, 4);  // c is still fading
  await wait(300);
  assert.deepEqual(t.keys(), ["n", "a", "b"]);
  assert.equal(t.list.style.height, "");
});

test("a deleted row leaves first, then its neighbours close the gap and the next row fills in", async () => {
  const t = setup();
  await t.L.sync(t.list, t.entries(["a", "b", "c"]));
  const b = t.list.children[1];
  await t.L.sync(t.list, t.entries(["a", "c", "d"]), { stillExists: () => false, wasKnown: (k) => k === "d" });
  const leaveAt = t.log.findIndex((x) => x.node === b);
  assert.deepEqual(t.log[leaveAt].frames[1], { opacity: 0, transform: "translateX(-8px)" });
  assert.equal(t.log[leaveAt].opts.duration, 160);
  const slideAt = t.log.findIndex((x, i) => i > leaveAt && x.node === t.list.children[1]);
  assert.ok(slideAt > leaveAt, "neighbours move only after the row has left");
  const d = t.list.children[2];
  assert.equal(d.dataset.key, "d");
  assert.ok(!d.classList.contains("is-landed"), "a fill is not new: no wash");
  assert.deepEqual(t.log.find((x) => x.node === d).frames[0], { opacity: 0, transform: "translateY(6px)" });
});

test("a changed row is rebuilt in place and washed", async () => {
  const t = setup();
  let v = 1;
  await t.L.sync(t.list, t.entries(["a", "b"], () => v));
  const old = t.list.children[1];
  v = 2;
  await t.L.sync(t.list, [{ key: "a", sig: 1, build: () => null }, ...t.entries(["b"], () => v)]);
  assert.notEqual(t.list.children[1], old);
  assert.ok(t.list.children[1].classList.contains("is-landed"));
});

test("animate: false applies the change instantly", async () => {
  const t = setup();
  await t.L.sync(t.list, t.entries(["a", "b", "c"]));
  await t.L.sync(t.list, t.entries(["c", "x"]), { animate: false, stillExists: () => true });
  assert.deepEqual(t.keys(), ["c", "x"]);
  assert.equal(t.log.length, 0);
});

test("reduced motion: no sliding, 120 ms fades in and out", async () => {
  const t = setup({ reduced: true });
  await t.L.sync(t.list, t.entries(["a", "b", "c"]));
  const b = t.list.children[1];
  await t.L.sync(t.list, t.entries(["n", "a", "c"]), { stillExists: () => false });
  assert.equal(moves(t.log).length, 0);
  const out = t.log.find((x) => x.node === b);
  assert.deepEqual(out.frames, [{ opacity: 1 }, { opacity: 0 }]);
  assert.equal(out.opts.duration, 120);
  const n = t.list.children[0];
  assert.deepEqual(t.log.find((x) => x.node === n).frames, [{ opacity: 0 }, { opacity: 1 }]);
  assert.ok(n.classList.contains("is-landed"), "the wash is colour, not movement: it stays");
});

test("updates to one list run in order, the later one wins", async () => {
  const t = setup();
  await t.L.sync(t.list, t.entries(["a", "b", "c"]));
  const first = t.L.sync(t.list, t.entries(["a", "c"]), { stillExists: () => false });
  const second = t.L.sync(t.list, t.entries(["z", "a", "c"]), { stillExists: () => false });
  await Promise.all([first, second]);
  assert.deepEqual(t.keys(), ["z", "a", "c"]);
});

test("a row pushed out and wanted back mid-fade comes back as a new row, not the fading one", async () => {
  const t = setup();
  await t.L.sync(t.list, t.entries(["a", "b", "c", "d", "e"]));
  const exists = { stillExists: () => true, wasKnown: () => true };
  const fading = t.list.children[4];
  t.L.sync(t.list, t.entries(["n", "a", "b", "c", "d"]), exists);   // e fades under the edge
  await t.L.sync(t.list, t.entries(["a", "b", "c", "d", "e"]), exists);  // and is wanted straight back
  await wait(250);
  assert.deepEqual(t.keys(), ["a", "b", "c", "d", "e"], "e is still listed once its old node's fade ends");
  assert.ok(!t.list.children.includes(fading), "the fading node finished leaving");
});

test("the wash ends on its own animationend, not on the stagger's or a child's", () => {
  const t = setup();
  const row = makeNode("li");
  t.L.wash(row);
  row.dispatch("animationend", { animationName: "row-in" });
  assert.ok(row.classList.contains("is-landed"), "the row-in stagger ending must not cut the wash short");
  row.dispatch("animationend", { animationName: "wash" });
  assert.ok(!row.classList.contains("is-landed"));
  t.L.wash(row);
  row.dispatch("animationend", { animationName: "wash" });
  assert.equal(row._listeners.animationend.length, 1, "washing a row again adds no second listener");
});

/* ---------- day groups (History and Home) ---------- */

function groupsSetup({ reduced = false } = {}) {
  const env = loadVoisr(["js/lists.js"]);
  env.window.matchMedia = () => ({ matches: reduced, addEventListener() {} });
  const log = [];
  const container = animated(makeNode("div"), log);
  const buildGroup = () => {
    const group = animated(makeNode("div"), log);
    group.className = "day-group";
    const label = animated(makeNode("div"), log);
    label.className = "date-row";
    const list = animated(makeNode("ul"), log);
    list.className = "item-list";
    list.offsetHeight = 200;
    group.append(label, list);
    return group;
  };
  const row = (key) => animated(Object.assign(makeNode("li"), { label: key }), log);
  const groups = (spec) => Object.entries(spec).map(([day, keys]) => ({
    key: day, build: buildGroup,
    rows: keys.map((k) => ({ key: k, sig: 1, build: () => row(k) })),
  }));
  const shape = () => Object.fromEntries(container.children.map((g) =>
    [g.dataset.key, g.querySelector(".item-list").children.map((r) => r.dataset.key)]));
  return { L: env.V.lists, container, log, groups, shape };
}

const finishTimers = (t) => t.mock.timers.tick(5000);

test("syncGroups fills an empty container without motion, one group a day", async () => {
  const s = groupsSetup();
  await s.L.syncGroups(s.container, s.groups({ Today: ["a", "b"], Yesterday: ["c"] }), { animate: true });
  assert.deepEqual(s.shape(), { Today: ["a", "b"], Yesterday: ["c"] });
  assert.equal(s.log.length, 0, "a first fill staggers instead");
});

test("syncGroups keeps the rows it can, so a refresh moves only what changed", async () => {
  const s = groupsSetup();
  await s.L.syncGroups(s.container, s.groups({ Today: ["a", "b"] }), {});
  const a = s.container.children[0].querySelector(".item-list").children[0];
  await s.L.syncGroups(s.container, s.groups({ Today: ["new", "a", "b"] }), {});
  assert.deepEqual(s.shape(), { Today: ["new", "a", "b"] });
  assert.equal(s.container.children[0].querySelector(".item-list").children[1], a);
});

test("a day that empties leaves as a whole; a deleted row leaves on its own", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = groupsSetup();
  await s.L.syncGroups(s.container, s.groups({ Today: ["a", "b"], Yesterday: ["c"] }), {});
  const gone = s.container.children[1];
  const update = s.L.syncGroups(s.container, s.groups({ Today: ["b"] }),
    { animate: true, stillExists: () => false });
  await new Promise((r) => setImmediate(r));
  assert.ok(gone.classList.contains("is-leaving"));
  assert.ok(s.log.some((e) => e.node === gone), "the group fades as one piece");
  finishTimers(t);
  await update;
  assert.deepEqual(s.shape(), { Today: ["b"] });
});

test("a new day rises in as one piece, its rows taking only the wash", async () => {
  const s = groupsSetup();
  await s.L.syncGroups(s.container, s.groups({ Yesterday: ["a"] }), {});
  await s.L.syncGroups(s.container, s.groups({ Today: ["n"], Yesterday: ["a"] }), { animate: true });
  const today = s.container.children[0];
  assert.ok(s.log.some((e) => e.node === today), "the group enters");
  const row = today.querySelector(".item-list").children[0];
  assert.ok(row.classList.contains("is-landed"), "the new row washes");
  assert.ok(!s.log.some((e) => e.node === row), "and doesn't animate inside a moving group");
});

test("a row pushed past the end stays to fade while its card's height holds", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const s = groupsSetup();
  await s.L.syncGroups(s.container, s.groups({ Today: ["a", "b"] }), {});
  const list = s.container.children[0].querySelector(".item-list");
  const pushed = list.children[1];
  await s.L.syncGroups(s.container, s.groups({ Today: ["n", "a"] }),
    { animate: true, stillExists: (k) => k === "b", wasKnown: () => false });
  assert.ok(pushed.classList.contains("is-leaving"));
  assert.equal(pushed.parent, list, "still in the DOM while it fades");
  assert.equal(pushed.dataset.key, undefined, "but a later update can't reuse it");
  assert.equal(list.style.height, "200px");
  finishTimers(t);
  await new Promise((r) => setImmediate(r));
  assert.equal(list.style.height, "");
  assert.ok(!list.children.includes(pushed));
});

test("reduced motion moves nothing across groups", async () => {
  const s = groupsSetup({ reduced: true });
  await s.L.syncGroups(s.container, s.groups({ Today: ["a"] }), {});
  await s.L.syncGroups(s.container, s.groups({ Today: ["n", "a"] }), { animate: true });
  assert.ok(s.log.every((e) => !e.frames.some((f) => /translate/.test(f.transform || ""))));
});

test("closeUp hides a node at once where nothing can animate", async () => {
  const { V } = loadVoisr(["js/lists.js"]);
  const node = makeNode("section");
  await V.lists.closeUp(node);
  assert.equal(node.hidden, true);
});
