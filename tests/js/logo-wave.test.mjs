import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { installDom } from "./dom.mjs";

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "voisr", "web");
const SRC = readFileSync(path.join(WEB, "js", "logo-wave.js"), "utf8");
const IDLE_SRC = readFileSync(path.join(WEB, "js", "idle.js"), "utf8");
const HTML = readFileSync(path.join(WEB, "index.html"), "utf8");
const MARKUP = HTML.match(/id="brand-wave" d="([^"]+)"/)[1];
const FRAME = 1000 / 20;

/* logo-wave.js with a hand-cranked clock. The app stays resident, so these
   count timers: a stopped wave must leave none running. */
function load({ reduced = false, focused = true, visible = "visible", mark = true } = {}) {
  const { window: win, document: doc } = installDom();
  const motion = { matches: reduced, listeners: [], addEventListener(_, fn) { this.listeners.push(fn); } };
  win.matchMedia = () => motion;

  let nextId = 1, now = 1000, idleWait = null;
  const frames = new Map();
  win.setInterval = (fn, ms) => {
    assert.equal(ms, FRAME);
    const id = nextId++;
    frames.set(id, fn);
    return id;
  };
  win.clearInterval = (id) => frames.delete(id);
  win.requestAnimationFrame = () => assert.fail("a frame callback wakes the page on every display frame");
  win.performance = { now: () => now };
  win.setTimeout = (fn, ms) => { idleWait = { fn, at: now + ms }; return 1; };

  // the shell's hidden state shows up as the mark having no boxes, and a
  // ResizeObserver is what reports that changing
  const resizeCallbacks = [];
  win.ResizeObserver = class {
    constructor(fn) { resizeCallbacks.push(fn); }
    observe() {}
  };

  doc.visibilityState = visible;
  let hasFocus = focused;
  doc.hasFocus = () => hasFocus;
  const brand = {
    d: MARKUP, paints: 0, shown: true,
    setAttribute(k, v) { assert.equal(k, "d"); this.d = v; this.paints += 1; },
    getClientRects() { return this.shown ? [{}] : []; },
  };
  doc.getElementById = (id) => (mark && id === "brand-wave" ? brand : null);
  const resized = () => resizeCallbacks.forEach((fn) => fn([]));

  (0, eval)(IDLE_SRC);
  (0, eval)(SRC);

  const h = {
    V: win.Voisr,
    brand,
    frames,
    run(ms) {
      for (const end = now + ms; now < end;) {
        now += FRAME;
        for (const fn of [...frames.values()]) fn();
      }
    },
    /* The timer firing late, after a stall. */
    stall(ms) {
      now += ms;
      for (const fn of [...frames.values()]) fn();
    },
    /* No input for as long as idle.js waits. */
    goIdle() {
      now = idleWait.at;
      idleWait.fn();
    },
    input() { win.dispatch("keydown", {}); },
    setVisibility(state) { doc.visibilityState = state; doc.dispatch("visibilitychange", {}); },
    setFocus(value) { hasFocus = value; win.dispatch(value ? "focus" : "blur", {}); },
    setReduced(value) { motion.matches = value; motion.listeners.forEach((fn) => fn()); },
    /* first run hiding the shell, and showing it again */
    hideShell() { brand.shown = false; resized(); },
    showShell() { brand.shown = true; resized(); },
    /* The same changes with the event lost, as WebKit might lose one. */
    quietly: {
      setFocus(value) { hasFocus = value; },
      setVisibility(state) { doc.visibilityState = state; },
      setReduced(value) { motion.matches = value; },
      hideShell() { brand.shown = false; },
    },
    /* Reads the clock back off the screen: the moment whose drawing this is. */
    clock() {
      let best = 0, bestDiff = Infinity;
      for (let c = 0; c < 5; c += .002) {
        const diff = distance(h.V.logoWave.pathFor(c), brand.d);
        if (diff < bestDiff) { bestDiff = diff; best = c; }
      }
      return best;
    },
  };
  return h;
}

function ys(d) {
  return [...d.matchAll(/[ML][\d.-]+ ([\d.-]+)/g)].map((m) => Number(m[1]));
}

function xs(d) {
  return [...d.matchAll(/[ML]([\d.-]+) /g)].map((m) => Number(m[1]));
}

function distance(a, b) {
  const [ya, yb] = [ys(a), ys(b)];
  return Math.max(...ya.map((y, i) => Math.abs(y - yb[i])));
}

test("at rest it draws exactly the logo already in index.html", () => {
  const { V } = load();
  assert.equal(V.logoWave.pathFor(0), MARKUP);
});

test("the ends stay put and the curve stays inside the mark at any moment", () => {
  const { V } = load();
  for (let c = 0; c < 10; c += .37) {
    const d = V.logoWave.pathFor(c);
    const [x, y] = [xs(d), ys(d)];
    assert.equal(x[0], 10);
    assert.equal(x.at(-1), 90);
    assert.ok(Math.abs(y[0] - 50) < .01 && Math.abs(y.at(-1) - 50) < .01);
    for (const v of y) assert.ok(v >= 50 - 36 && v <= 50 + 36, `y ${v}`);
  }
});

test("one idle turn takes five seconds and comes back to the logo", () => {
  const { V } = load();
  assert.notEqual(V.logoWave.pathFor(1.25), MARKUP);
  assert.equal(V.logoWave.pathFor(5), MARKUP);
});

test("it drifts while Voisr is in front", () => {
  const h = load();
  h.run(1000);
  assert.ok(Math.abs(h.clock() - 1) < .08, `clock ${h.clock()}`);
});

test("it paints 20 times a second", () => {
  const h = load();
  h.run(1000);
  assert.equal(h.brand.paints, 20);
});

test("hidden, it asks for no frames and resumes from where it was", () => {
  const h = load();
  h.run(1000);
  const held = h.brand.d;
  h.setVisibility("hidden");
  assert.equal(h.frames.size, 0);
  // not a whole number of five-second turns, or a leap would land back on
  // the same shape and look like no jump at all
  h.run(61300);
  assert.equal(h.brand.d, held);
  h.setVisibility("visible");
  h.run(FRAME);
  assert.equal(h.frames.size, 1);
  // one frame's step is at most about 2.3 units
  assert.ok(distance(h.brand.d, held) < 3, "resumed with a jump");
});

test("it stops without focus", () => {
  const h = load();
  h.setFocus(false);
  assert.equal(h.frames.size, 0);
  h.setFocus(true);
  assert.equal(h.frames.size, 1);
});

test("a lost blur or visibility event still stops it within a frame", () => {
  for (const lose of [(h) => h.quietly.setFocus(false), (h) => h.quietly.setVisibility("hidden")]) {
    const h = load();
    h.run(200);
    const held = h.brand.d;
    lose(h);
    h.run(FRAME);
    assert.equal(h.frames.size, 0);
    h.run(2000);
    assert.equal(h.brand.d, held);
  }
});

test("it starts again on the next event after a lost one", () => {
  const h = load();
  h.run(200);
  h.quietly.setFocus(false);
  h.run(FRAME);
  assert.equal(h.frames.size, 0);
  h.setFocus(true);
  assert.equal(h.frames.size, 1);
});

test("with the shell hidden in first run it stops, and asks for nothing", () => {
  const h = load();
  h.run(200);
  h.hideShell();
  assert.equal(h.frames.size, 0);
  const held = h.brand.d, paints = h.brand.paints;
  h.run(5000);
  assert.equal(h.brand.d, held);
  assert.equal(h.brand.paints, paints);
});

test("a lost hide still stops it within a frame", () => {
  const h = load();
  h.run(200);
  h.quietly.hideShell();
  h.run(FRAME);
  assert.equal(h.frames.size, 0);
});

test("input during first run doesn't start it, but the shell showing again does, with no other event", () => {
  const h = load();
  h.hideShell();
  h.input();
  h.run(1000);
  assert.equal(h.frames.size, 0, "hidden, input alone must not start it");
  h.showShell();
  assert.equal(h.frames.size, 1);
  const held = h.brand.d;
  h.run(1000);
  assert.ok(h.brand.d !== held && h.brand.paints > 0, "the wave moves again");
});

test("a lost reduced-motion event still goes back to the logo", () => {
  const h = load();
  h.run(200);
  h.quietly.setReduced(true);
  h.run(FRAME);
  assert.equal(h.frames.size, 0);
  assert.equal(h.brand.d, MARKUP);
});

test("only one frame is ever pending", () => {
  const h = load();
  h.setFocus(true);
  h.setVisibility("visible");
  assert.equal(h.frames.size, 1);
});

test("a stall resumes where it was instead of leaping ahead", () => {
  const h = load();
  h.run(FRAME * 4);
  const before = h.clock();
  h.stall(3000);
  const moved = h.clock() - before;
  assert.ok(moved <= .11, `moved ${moved}`);
});

test("reduced motion from the start: no frames and the logo untouched", () => {
  const h = load({ reduced: true });
  assert.equal(h.frames.size, 0);
  assert.equal(h.brand.d, MARKUP);
  assert.equal(h.brand.paints, 0);
});

test("turning reduced motion on stops it on the logo, and off starts it again", () => {
  const h = load();
  h.run(1000);
  assert.notEqual(h.brand.d, MARKUP);
  h.setReduced(true);
  assert.equal(h.frames.size, 0);
  assert.equal(h.brand.d, MARKUP);
  h.setReduced(false);
  assert.equal(h.frames.size, 1);
});

test("a page without the mark loads quietly", () => {
  const h = load({ mark: false });
  assert.equal(h.frames.size, 0);
  assert.equal(typeof h.V.logoWave.pathFor, "function");
});

test("left alone, it eases to a stop within a couple of seconds and leaves no timer", () => {
  const h = load();
  h.run(1000);
  h.goIdle();
  assert.equal(h.frames.size, 1, "it eases rather than freezing");
  const held = h.brand.d;
  h.run(FRAME);
  assert.notEqual(h.brand.d, held);
  h.run(2000);
  assert.equal(h.frames.size, 0);
  const paints = h.brand.paints;
  h.run(60000);
  assert.equal(h.brand.paints, paints);
});

test("input after a stop starts it again without a jump", () => {
  const h = load();
  h.run(1000);
  h.goIdle();
  h.run(2000);
  const held = h.brand.d;
  h.input();
  assert.equal(h.frames.size, 1);
  h.run(FRAME);
  assert.ok(distance(h.brand.d, held) < 1, "a stopped wave picks up slowly");
});

test("a page without the mark doesn't mind going idle", () => {
  const h = load({ mark: false });
  h.goIdle();
  h.input();
  assert.equal(h.frames.size, 0);
});
