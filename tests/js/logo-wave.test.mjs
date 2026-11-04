import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { installDom } from "./dom.mjs";

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "voisr", "web");
const SRC = readFileSync(path.join(WEB, "js", "logo-wave.js"), "utf8");
const HTML = readFileSync(path.join(WEB, "index.html"), "utf8");
const MARKUP = HTML.match(/id="brand-wave" d="([^"]+)"/)[1];
const FRAME = 1000 / 60;

/* logo-wave.js with a hand-cranked display. The app stays resident, so these
   count frames: a stopped wave must ask for none. */
function load({ reduced = false, focused = true, visible = "visible", mark = true } = {}) {
  const { window: win, document: doc } = installDom();
  const motion = { matches: reduced, listeners: [], addEventListener(_, fn) { this.listeners.push(fn); } };
  win.matchMedia = () => motion;

  let nextId = 1;
  const frames = new Map();
  win.requestAnimationFrame = (fn) => { const id = nextId++; frames.set(id, fn); return id; };
  win.cancelAnimationFrame = (id) => frames.delete(id);

  doc.visibilityState = visible;
  let hasFocus = focused;
  doc.hasFocus = () => hasFocus;
  const brand = { d: MARKUP, setAttribute(k, v) { assert.equal(k, "d"); this.d = v; this.paints += 1; }, paints: 0 };
  doc.getElementById = (id) => (mark && id === "brand-wave" ? brand : null);

  (0, eval)(SRC);

  let now = 1000;
  const h = {
    V: win.Voisr,
    brand,
    frames,
    run(ms) {
      for (const end = now + ms; now < end;) {
        now += FRAME;
        if (!frames.size) continue;
        const [[id, fn]] = frames;
        frames.delete(id);
        fn(now);
      }
    },
    setVisibility(state) { doc.visibilityState = state; doc.dispatch("visibilitychange", {}); },
    setFocus(value) { hasFocus = value; win.dispatch(value ? "focus" : "blur", {}); },
    setReduced(value) { motion.matches = value; motion.listeners.forEach((fn) => fn()); },
    /* The same changes with the event lost, as WebKit might lose one. */
    quietly: {
      setFocus(value) { hasFocus = value; },
      setVisibility(state) { doc.visibilityState = state; },
      setReduced(value) { motion.matches = value; },
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
  assert.ok(Math.abs(h.clock() - 1) < .06, `clock ${h.clock()}`);
});

test("it paints about 30 times a second, not on every frame of the display", () => {
  const h = load();
  h.run(1000);
  assert.ok(h.brand.paints >= 26 && h.brand.paints <= 31, `${h.brand.paints} paints`);
});

test("hidden, it asks for no frames and resumes from where it was", () => {
  const h = load();
  h.run(1000);
  const held = h.brand.d;
  h.setVisibility("hidden");
  assert.equal(h.frames.size, 0);
  h.run(60000);
  assert.equal(h.brand.d, held);
  h.setVisibility("visible");
  h.run(FRAME * 3);
  assert.equal(h.frames.size, 1);
  assert.ok(distance(h.brand.d, held) < 2, "resumed with a jump");
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
  const [[id, fn]] = h.frames;
  h.frames.delete(id);
  fn(1000 + 3000);
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
