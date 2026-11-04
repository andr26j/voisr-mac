import { test } from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";

const { V } = loadVoisr(["js/wave-meter.js"]);

function fakePath() {
  const attrs = {};
  return { attrs,
    setAttribute(k, v) { attrs[k] = v; },
    removeAttribute(k) { delete attrs[k]; } };
}

/* A wave of three paths and hand-cranked timers: `poll()` fires the 30 Hz read,
   `frame(t)` fires one display frame. */
function rig({ levels = [], reduced = false, watching = () => true } = {}) {
  const paths = [fakePath(), fakePath(), fakePath()];
  const el = { dataset: {}, style: {}, querySelectorAll: () => paths };
  const timers = { poll: null, polls: 0, stopped: 0, frame: null, frames: 0 };
  const reads = [];
  const answers = [...levels];
  const meter = V.waveMeter.create({
    el,
    readLevel: () => { reads.push(1); return Promise.resolve(answers.length ? answers.shift() : 0.5); },
    every: (fn, ms) => { timers.poll = fn; timers.ms = ms; timers.polls += 1; return timers.polls; },
    stopEvery: () => { timers.poll = null; timers.stopped += 1; },
    raf: (fn) => { timers.frame = fn; timers.frames += 1; return timers.frames; },
    isReduced: () => reduced,
    isWatching: watching,
    now: () => 0,
  });
  const settle = () => new Promise((r) => setImmediate(r));
  return {
    el, meter, timers, reads, paths,
    async poll() { timers.poll(); await settle(); },
    frame(t) { const fn = timers.frame; timers.frame = null; fn(t); },
    run(from, count = 300) {
      let t = from;
      for (let i = 0; i < count && timers.frame; i++) { t += 16; this.frame(t); }
      return t;
    },
  };
}

/* matrix(1 0 0 k 0 dy): k is the wave's height, and dy keeps its centre line put */
function heightOf(path) {
  const t = path.attrs.transform;
  if (t === undefined) return 1;
  const [, k, dy] = /^matrix\(1 0 0 ([\d.]+) 0 ([\d.-]+)\)$/.exec(t);
  assert.ok(Math.abs(Number(dy) - 80 * (1 - Number(k))) < 0.05, `centre line moved: ${t}`);
  return Number(k);
}

test("start polls the level at 30 Hz and the wave drops to its quiet height", () => {
  const r = rig();
  r.meter.start();
  assert.equal(r.el.dataset.meter, "live");
  assert.ok(Math.abs(r.timers.ms - 1000 / 30) < 1e-9);
  r.run(0);
  assert.equal(heightOf(r.paths[0]), 0.3);
});

test("the level drives every path's height, and silence below the gate doesn't", async () => {
  const r = rig({ levels: Array(60).fill(1) });
  r.meter.start();
  let t = 0;
  await r.poll();
  for (let i = 0; i < 40; i++) { t += 16; if (r.timers.frame) r.frame(t); if (i % 2 === 0) await r.poll(); }
  for (const path of r.paths) assert.ok(heightOf(path) > 0.9, `height ${heightOf(path)}`);

  const hiss = rig({ levels: Array(60).fill(0.03) });
  hiss.meter.start();
  await hiss.poll();
  hiss.run(0);
  assert.equal(heightOf(hiss.paths[0]), 0.3);
});

test("a recorder with no meter stops polling and leaves the wave at full height", async () => {
  const r = rig({ levels: [null] });
  r.meter.start();
  r.frame(16);
  await r.poll();
  assert.equal(r.el.dataset.meter, "loop");
  assert.equal(r.timers.poll, null);
  r.run(16);
  for (const path of r.paths) assert.equal(path.attrs.transform, undefined);
});

test("only one level read is in flight at a time", async () => {
  const r = rig();
  r.meter.start();
  r.timers.poll(); r.timers.poll(); r.timers.poll();  // three ticks, one read
  await new Promise((res) => setImmediate(res));
  assert.equal(r.reads.length, 1);
  r.timers.poll();
  await new Promise((res) => setImmediate(res));
  assert.equal(r.reads.length, 2);
});

test("nothing is read while the window is hidden or not focused", async () => {
  let watching = false;
  const r = rig({ watching: () => watching });
  r.meter.start();
  await r.poll();
  assert.equal(r.reads.length, 0);
  watching = true;
  await r.poll();
  assert.equal(r.reads.length, 1);
});

test("settle stops the feed and lowers the wave for transcribing", async () => {
  const r = rig();
  r.meter.start();
  await r.poll();
  r.meter.settle();
  assert.equal(r.timers.poll, null);
  r.run(0);
  assert.equal(heightOf(r.paths[0]), 0.45);
});

test("idle brings the wave back to full height and takes the transform off", () => {
  const r = rig();
  r.meter.start();
  r.run(0);
  r.meter.idle();
  r.run(2000);
  for (const path of r.paths) assert.equal(path.attrs.transform, undefined);
  assert.equal(r.el.dataset.meter, undefined);
  assert.equal(r.timers.frame, null, "and it has stopped asking for frames");
});

test("an answer from a finished recording is ignored", async () => {
  let release;
  const paths = [fakePath()];
  const el = { dataset: {}, style: {}, querySelectorAll: () => paths };
  let poll = null;
  const meter = V.waveMeter.create({
    el,
    readLevel: () => new Promise((r) => { release = r; }),
    every: (fn) => { poll = fn; return 1; }, stopEvery: () => { poll = null; },
    raf: () => 1,
    isReduced: () => false, isWatching: () => true, now: () => 0,
  });
  meter.start();
  poll();
  await Promise.resolve(); await Promise.resolve();  // the read is now in flight
  meter.settle();
  release(null);  // the old session's read lands late, saying "no meter"
  await new Promise((r) => setImmediate(r));
  assert.equal(el.dataset.meter, "live");  // not flipped to the loop
});

test("reduced motion leaves the wave's shape alone and shows the level as brightness", async () => {
  const r = rig({ levels: Array(6).fill(0.5), reduced: true });
  r.meter.start();
  await r.poll();
  const t = r.run(0);
  assert.equal(r.paths[0].attrs.transform, undefined);
  const opacity = Number(r.el.style.opacity);
  assert.ok(opacity > 0.7 && opacity < 0.8, `opacity ${r.el.style.opacity}`);
  r.meter.idle();
  r.run(t);
  assert.equal(r.el.style.opacity, "");
});

test("a settled meter parks the frame loop until a level moves it again", async () => {
  const r = rig({ levels: [...Array(10).fill(0.8), ...Array(200).fill(0)] });
  r.meter.start();
  let t = 0;
  for (let i = 0; i < 10; i++) { await r.poll(); t += 16; if (r.timers.frame) r.frame(t); }
  // fall silent for three seconds: far longer than the 220 ms release
  for (let i = 0; i < 190; i++) { await r.poll(); t += 16; if (r.timers.frame) r.frame(t); }
  assert.equal(r.timers.frame, null, "a settled wave requests no more frames");
  assert.equal(heightOf(r.paths[0]), 0.3, "and it stands at its quiet height");

  await r.poll();                      // another silent reading: nothing to do
  assert.equal(r.timers.frame, null);
});
