import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { installDom } from "./dom.mjs";

const SRC = readFileSync(path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..", "..", "voisr", "web", "js", "wave.js"), "utf8");

/* wave.js with a hand-cranked clock. The app must do no work unless a banner
   is on screen in a visible window, so these count animation frames rather
   than looking at pixels. */
function loadWave({ reduced = false } = {}) {
  const { window: win, document: doc } = installDom();
  const motion = { matches: reduced, listeners: [], addEventListener(_, fn) { this.listeners.push(fn); } };
  win.matchMedia = () => motion;

  let nextId = 1;
  const frames = new Map();
  win.requestAnimationFrame = (fn) => { const id = nextId++; frames.set(id, fn); return id; };
  win.cancelAnimationFrame = (id) => frames.delete(id);

  doc.visibilityState = "visible";
  let focused = true;
  doc.hasFocus = () => focused;
  doc.createElementNS = () => ({
    attrs: {},
    children: [],
    setAttribute(k, v) { this.attrs[k] = v; },
    append(c) { this.children.push(c); },
  });
  doc.querySelectorAll = () => [];

  const observers = [];
  globalThis.IntersectionObserver = class {
    constructor(cb) { this.cb = cb; this.watched = new Set(); observers.push(this); }
    observe(el) { this.watched.add(el); }
    unobserve(el) { this.watched.delete(el); }
  };

  (0, eval)(SRC);

  const banner = () => ({
    isConnected: true,
    replaceChildren(svg) { this.svg = svg; },
    get paths() { return this.svg.children; },
  });
  return {
    wave: win.Voisr.wave,
    banner,
    frames,
    /* Runs the pending frame, as the display would at `now` ms. */
    runFrame(now) {
      const [[id, fn]] = frames;
      frames.delete(id);
      fn(now);
    },
    setOnScreen(el, isIntersecting) {
      observers[0].cb([{ target: el, isIntersecting }]);
    },
    setVisibility(state) {
      doc.visibilityState = state;
      doc.dispatch("visibilitychange", {});
    },
    setFocus(value) {
      focused = value;
      win.dispatch(value ? "focus" : "blur", {});
    },
    setReduced(value) {
      motion.matches = value;
      motion.listeners.forEach((fn) => fn());
    },
  };
}

test("the wave follows the design's formula", () => {
  const { wave } = loadWave();
  const line = wave.LINES[2];
  const coords = wave.pathFor(line, 0).slice(1).split("L").map((p) => p.split(" ").map(Number));
  assert.equal(coords.length, 81);
  // the envelope pins both ends to the midline
  assert.deepEqual(coords[0], [0, 80]);
  assert.equal(coords[80][0], 420);
  assert.ok(Math.abs(coords[80][1] - 80) < .01);
  // at a quarter of the way along, y = 80 + 58 * sin(pi / 4)^1.25 * sin(2 pi * 3 * .25)
  const expected = 80 + 58 * Math.pow(Math.sin(Math.PI / 4), 1.25) * Math.sin(1.5 * Math.PI);
  assert.ok(Math.abs(coords[20][1] - expected) < .01);
});

test("a banner shows a wave even before it moves", () => {
  const { wave, banner } = loadWave();
  const el = banner();
  wave.mount(el);
  assert.equal(el.paths.length, 3);
  el.paths.forEach((p, i) => assert.equal(p.attrs.d, wave.pathFor(wave.LINES[i], 0)));
});

test("nothing runs until a banner is on screen", () => {
  const { wave, banner, frames, setOnScreen } = loadWave();
  const el = banner();
  wave.mount(el);
  assert.equal(frames.size, 0, "mounted but not yet known to be visible");
  setOnScreen(el, true);
  assert.equal(frames.size, 1);
  setOnScreen(el, false);
  assert.equal(frames.size, 0, "a dismissed or hidden banner must stop the loop");
});

test("a hidden window stops the loop, and showing it restarts it", () => {
  const { wave, banner, frames, setOnScreen, setVisibility } = loadWave();
  const el = banner();
  wave.mount(el);
  setOnScreen(el, true);
  setVisibility("hidden");
  assert.equal(frames.size, 0);
  setVisibility("visible");
  assert.equal(frames.size, 1);
});

test("a window that isn't focused stops the loop, and focus restarts it", () => {
  const { wave, banner, frames, setOnScreen, setFocus } = loadWave();
  const el = banner();
  wave.mount(el);
  setOnScreen(el, true);
  setFocus(false);
  assert.equal(frames.size, 0);
  setFocus(true);
  assert.equal(frames.size, 1);
});

test("reduced motion holds a still frame and never schedules one", () => {
  const { wave, banner, frames, setOnScreen } = loadWave({ reduced: true });
  const el = banner();
  wave.mount(el);
  setOnScreen(el, true);
  assert.equal(frames.size, 0);
  assert.equal(el.paths[0].attrs.d, wave.pathFor(wave.LINES[0], 0));
});

test("turning reduced motion on stops the wave where it is", () => {
  const { wave, banner, frames, runFrame, setOnScreen, setReduced } = loadWave();
  const el = banner();
  wave.mount(el);
  setOnScreen(el, true);
  runFrame(1000);
  runFrame(1040);
  const held = el.paths[0].attrs.d;
  assert.notEqual(held, wave.pathFor(wave.LINES[0], 0), "it had moved");
  setReduced(true);
  assert.equal(frames.size, 0);
  assert.equal(el.paths[0].attrs.d, held);
});

test("frames between the 30 fps ticks draw nothing", () => {
  const { wave, banner, runFrame, setOnScreen } = loadWave();
  const el = banner();
  wave.mount(el);
  setOnScreen(el, true);
  runFrame(1000);
  const before = el.paths[0].attrs.d;
  runFrame(1016);
  assert.equal(el.paths[0].attrs.d, before, "a 60 Hz frame in between");
  runFrame(1033);
  assert.notEqual(el.paths[0].attrs.d, before, "every second frame");
});

test("a stall resumes where it was instead of leaping ahead", () => {
  const { wave, banner, runFrame, setOnScreen } = loadWave();
  const el = banner();
  wave.mount(el);
  setOnScreen(el, true);
  runFrame(1000);
  runFrame(6000);
  assert.equal(el.paths[2].attrs.d, wave.pathFor(wave.LINES[2], .1));
});

test("a speed change eases in rather than jumping the phase", () => {
  const { wave, banner, runFrame, setOnScreen } = loadWave();
  const el = banner();
  wave.mount(el);
  setOnScreen(el, true);
  runFrame(1000);
  wave.setSpeed(el, 3.2);
  runFrame(1040);
  const speed = 1 + 2.2 * (1 - Math.exp(-.04 / .25));
  assert.equal(el.paths[2].attrs.d, wave.pathFor(wave.LINES[2], .04 * speed));
  assert.ok(speed < 1.5, "well short of 3.2 after one frame");
});

test("a banner removed from the page stops costing anything", () => {
  const { wave, banner, frames, runFrame, setOnScreen } = loadWave();
  const el = banner();
  wave.mount(el);
  setOnScreen(el, true);
  runFrame(1000);
  el.isConnected = false;
  runFrame(1040);
  assert.equal(frames.size, 0);
});

test("mounting the same element twice doesn't stack waves", () => {
  const { wave, banner } = loadWave();
  const el = banner();
  wave.mount(el);
  const svg = el.svg;
  wave.mount(el);
  assert.equal(el.svg, svg);
});
