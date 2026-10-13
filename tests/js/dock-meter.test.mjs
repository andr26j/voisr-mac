import { test } from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";

const { V } = loadVoisr(["js/meter.js", "js/dock-meter.js"]);

function fakeStyle() {
  const s = { setProperty(k, v) { s[k] = String(v); } };
  return s;
}

/* A dock with 21 bars and hand-cranked timers: `poll()` fires the 30 Hz
   read, `frame(t)` fires one display frame. */
function rig({ levels = [], reduced = false } = {}) {
  const wave = { dataset: {}, children: Array.from({ length: 21 }, () => ({ style: fakeStyle() })) };
  const timers = { poll: null, polls: 0, stopped: 0, frame: null, frames: 0, cancelled: 0 };
  const reads = [];
  const answers = [...levels];
  const dock = V.dockMeter.create({
    wave,
    readLevel: () => { reads.push(1); return Promise.resolve(answers.length ? answers.shift() : 0.5); },
    every: (fn, ms) => { timers.poll = fn; timers.ms = ms; timers.polls += 1; return timers.polls; },
    stopEvery: () => { timers.poll = null; timers.stopped += 1; },
    raf: (fn) => { timers.frame = fn; timers.frames += 1; return timers.frames; },
    caf: () => { timers.frame = null; timers.cancelled += 1; },
    isReduced: () => reduced,
    now: () => 0,
  });
  const settle = () => new Promise((r) => setImmediate(r));
  return {
    wave, dock, timers, reads, bars: wave.children,
    async poll() { timers.poll(); await settle(); },
    frame(t) { const fn = timers.frame; timers.frame = null; fn(t); },
  };
}

const scaleOf = (bar) => Number(/scaleY\(([\d.]+)\)/.exec(bar.style.transform)[1]);

test("start rests every bar at 3 px of 18 and polls the level at 30 Hz", () => {
  const r = rig();
  r.dock.start();
  assert.equal(r.wave.dataset.meter, "live");
  assert.ok(Math.abs(r.timers.ms - 1000 / 30) < 1e-9);
  for (const b of r.bars) assert.ok(Math.abs(scaleOf(b) - 3 / 18) < 1e-3);
  assert.equal(r.bars[5].style["--i"], "5");  // shimmer stagger index
});

test("levels drive the bars, centre-weighted", async () => {
  const r = rig({ levels: Array(60).fill(1) });
  r.dock.start();
  let t = 0;
  await r.poll();
  for (let i = 0; i < 40; i++) { t += 16; r.frame(t); if (i % 2 === 0) await r.poll(); }
  const mid = scaleOf(r.bars[10]), edge = scaleOf(r.bars[0]);
  assert.ok(mid > 0.8, `centre ${mid}`);
  assert.ok(edge < mid, `edge ${edge} vs centre ${mid}`);
});

test("a recorder with no meter falls back to the loop and stops polling", async () => {
  const r = rig({ levels: [null] });
  r.dock.start();
  await r.poll();
  assert.equal(r.wave.dataset.meter, "loop");
  assert.equal(r.timers.poll, null);
  for (const b of r.bars) assert.equal(b.style.transform, "");
});

test("only one level read is in flight at a time", async () => {
  const r = rig();
  r.dock.start();
  r.timers.poll(); r.timers.poll(); r.timers.poll();  // three ticks, one read
  await new Promise((res) => setImmediate(res));
  assert.equal(r.reads.length, 1);
  r.timers.poll();
  await new Promise((res) => setImmediate(res));
  assert.equal(r.reads.length, 2);
});

test("settle stops the feed and eases the bars to half height for the shimmer", async () => {
  const r = rig();
  r.dock.start();
  await r.poll();
  r.dock.settle();
  assert.equal(r.timers.poll, null);
  assert.equal(r.timers.frame, null);
  for (const b of r.bars) {
    assert.equal(scaleOf(b), 0.5);
    assert.equal(b.style.transition, "transform 180ms var(--ease)");
  }
});

test("idle eases the bars back to rest", () => {
  const r = rig();
  r.dock.start();
  r.dock.settle();
  r.dock.idle();
  for (const b of r.bars) assert.ok(Math.abs(scaleOf(b) - 3 / 18) < 1e-3);
  assert.equal(r.wave.dataset.meter, "live");
});

test("an answer from a finished recording is ignored", async () => {
  let release;
  const wave = { dataset: {}, children: Array.from({ length: 21 }, () => ({ style: fakeStyle() })) };
  let poll = null, frames = 0;
  const dock = V.dockMeter.create({
    wave,
    readLevel: () => new Promise((r) => { release = r; }),
    every: (fn) => { poll = fn; return 1; }, stopEvery: () => { poll = null; },
    raf: () => { frames += 1; return frames; }, caf: () => {},
    isReduced: () => false, now: () => 0,
  });
  dock.start();
  poll();
  await Promise.resolve(); await Promise.resolve();  // the read is now in flight
  dock.settle();
  release(null);  // the old session's read lands late, saying "no meter"
  await new Promise((r) => setImmediate(r));
  assert.equal(wave.dataset.meter, "live");  // not flipped to the loop
  assert.equal(frames, 0);
});

test("reduced motion holds the bars at half height and shows the level as brightness", async () => {
  const r = rig({ levels: [1, 1, 1, 1, 1, 1], reduced: true });
  r.dock.start();
  let t = 0;
  await r.poll();
  for (let i = 0; i < 20; i++) { t += 16; r.frame(t); }
  assert.equal(scaleOf(r.bars[10]), 0.5);
  assert.ok(Number(r.bars[10].style.opacity) > 0.8);
  r.dock.settle();
  assert.equal(r.bars[0].style.transition, "none");
});

test("silence parks the frame loop until a level rises again", async () => {
  const r = rig({ levels: [...Array(10).fill(0.8), ...Array(200).fill(0)] });
  r.dock.start();
  let t = 0;
  for (let i = 0; i < 10; i++) { await r.poll(); t += 16; r.frame(t); }
  // fall silent for three seconds: far longer than the 220 ms release
  for (let i = 0; i < 190 && r.timers.frame; i++) { await r.poll(); t += 16; if (r.timers.frame) r.frame(t); }
  assert.equal(r.timers.frame, null, "a settled meter requests no more frames");
  for (const b of r.bars) assert.ok(Math.abs(scaleOf(b) - 3 / 18) < 1e-3, "and its bars stand at rest");

  await r.poll();                      // another silent reading: one frame, then parked again
  t += 16; r.frame(t);
  assert.equal(r.timers.frame, null);
});
