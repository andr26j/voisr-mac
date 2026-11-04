import { test } from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";

const { V } = loadVoisr(["js/meter.js"]);
const M = V.meter;

/* Run a meter for `ms` at 60 fps against a fixed raw level. */
function run(meter, raw, ms, t0 = 0) {
  meter.setLevel(raw);
  let out, t = t0;
  for (let i = 0; i < Math.round(ms / 16); i++) { t += 16; out = meter.frame(16, t); }
  return { out, t };
}

test("gate: room hiss stays at rest, the rest of the range rescales to 0..1", () => {
  assert.equal(M.gate(0), 0);
  assert.equal(M.gate(0.03), 0);
  assert.equal(M.gate(0.04), 0);
  assert.equal(M.gate(1), 1);
  assert.ok(Math.abs(M.gate(0.52) - 0.5) < 1e-9);
  assert.equal(M.gate(7), 1);
  assert.equal(M.gate(NaN), 0);
});

test("step: a 50 ms attack covers 63% in 50 ms, a 220 ms release only 20%", () => {
  const up = M.step(0, 1, 50, 50, 220);
  assert.ok(Math.abs(up - (1 - Math.exp(-1))) < 1e-9);
  const down = M.step(1, 0, 50, 50, 220);
  assert.ok(Math.abs(down - Math.exp(-50 / 220)) < 1e-9);
});

test("silence leaves every bar at rest", () => {
  const meter = M.createPill();
  const { out } = run(meter, 0.02, 500);
  assert.deepEqual(out, new Array(17).fill(0));
});

test("hump: low at the edges, full in the middle, symmetric", () => {
  const h = M.hump(17);
  assert.equal(h.length, 17);
  assert.equal(h[8], 1);
  for (let i = 0; i < 8; i++) {
    assert.ok(h[i] < h[i + 1]);
    assert.ok(Math.abs(h[i] - h[16 - i]) < 1e-9);
  }
});

test("weights: the pill's 17 bars take the design's 6.6-26 px silhouette; create gets a plain hump at any count", () => {
  const w = M.pillWeights();
  assert.equal(w.length, 17);
  assert.equal(M.PILL_SPEAK.length, 17);
  assert.equal(w[8], 1);
  assert.ok(Math.abs(w[0] * 26 - 6.6) < 0.05, `edge ${w[0] * 26} px`);
  const dock = M.weights(21);
  assert.equal(dock.length, 21);
  assert.ok(dock[10] > dock[0] && Math.abs(dock[0] - dock[20]) < 1e-9);
  assert.ok(M.weights(17)[0] > w[0], "17 bars elsewhere don't turn into the pill");
});

test("a voice moves each pill bar on its own rhythm, inside its share of the silhouette", () => {
  const meter = M.createPill();
  meter.setLevel(1);
  const w = M.pillWeights();
  const seen = w.map(() => [Infinity, -Infinity]);
  let t = 0, frame;
  for (let i = 0; i < 400; i++) {
    t += 16;
    frame = meter.frame(16, t);
    if (t < 500) continue;  // past the attack
    frame.forEach((x, b) => {
      assert.ok(x >= 0 && x <= 1, `bar ${b}: ${x}`);
      assert.ok(x <= w[b] * 1.35 + 1e-9, `bar ${b} ${x} past ${w[b] * 1.35}`);
      seen[b] = [Math.min(seen[b][0], x), Math.max(seen[b][1], x)];
    });
  }
  // the centre swings by most of its height rather than holding a stamped shape
  assert.ok(seen[8][1] - seen[8][0] > 0.5, `centre range ${seen[8]}`);
  // neighbours don't move as one
  assert.ok(frame.some((x, b) => b < 8 && Math.abs(x / w[b] - frame[b + 1] / w[b + 1]) > 0.05));
});

test("other bar counts stay within 16% of their hump", () => {
  const meter = M.create(21);
  const { out } = run(meter, 1, 1000);
  const w = M.weights(21);
  out.forEach((x, b) => {
    assert.ok(x >= w[b] * 0.84 - 1e-9 && x <= Math.min(1, w[b] * 1.16) + 1e-9, `bar ${b}: ${x}`);
  });
});

test("bars fall slower than they rise", () => {
  const rise = run(M.create(21), 1, 100).out[10];
  const m2 = M.create(21);
  run(m2, 1, 2000);
  const fall = 1 - run(m2, 0, 100).out[10];
  assert.ok(rise > fall, `rise ${rise} vs fall ${fall}`);
});

test("reset puts the envelope back at rest", () => {
  const meter = M.createPill();
  run(meter, 1, 500);
  meter.reset();
  meter.setLevel(0);
  assert.deepEqual(meter.frame(16, 10), new Array(17).fill(0));
});

test("paintBar scales a bar and pre-stretches its caps, for the pill and the dock alike", () => {
  const style = {};
  M.paintBar({ style }, 0.5);
  assert.equal(style.transform, "scaleY(0.500)");
  assert.equal(style.borderRadius, "2px / 4.00px", "2 px caps survive a half-height scale");
  M.paintBar({ style }, 0.5, 1.25);
  assert.equal(style.borderRadius, "1.25px / 2.50px", "and so do the pill's narrower ones");
});

test("atRest: true in silence, false while a voice is in the envelope, true once it has fallen", () => {
  const m = M.createPill();
  assert.ok(m.atRest(), "a fresh meter is at rest");
  run(m, 0.8, 200);
  assert.ok(!m.atRest());
  const { t } = run(m, 0, 200, 200);
  assert.ok(!m.atRest(), "still falling 200 ms into the pause");
  run(m, 0, 3000, t);
  assert.ok(m.atRest(), "settled after the release");
});
