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
  const meter = M.create(7, "classic");
  const { out } = run(meter, 0.02, 500);
  assert.deepEqual(out, [0, 0, 0, 0, 0, 0, 0]);
});

test("classic is centre-weighted with bounded per-bar variance", () => {
  const meter = M.create(7, "classic");
  const { out } = run(meter, 1, 1000);
  assert.ok(out[3] > 0.84 && out[3] <= 1, `centre ${out[3]}`);
  for (const edge of [out[0], out[6]]) assert.ok(edge > 0.52 * 0.84 - 1e-9 && edge < 0.52 * 1.16 + 1e-9, `edge ${edge}`);
  assert.ok(out[3] > out[1] && out[1] > out[0]);
});

test("bars fall slower than they rise", () => {
  const meter = M.create(7, "breathe");
  const rise = run(meter, 1, 100).out[3];
  const m2 = M.create(7, "breathe");
  run(m2, 1, 2000);
  const fall = 1 - run(m2, 0, 100).out[3];
  assert.ok(rise > fall, `rise ${rise} vs fall ${fall}`);
});

test("breathe moves all bars as one", () => {
  const meter = M.create(7, "breathe");
  const { out } = run(meter, 0.7, 300);
  assert.ok(out.every((x) => x === out[0]));
  assert.ok(out[0] > 0);
});

test("neon quantises to four steps", () => {
  const meter = M.create(7, "neon");
  for (const raw of [0.3, 0.55, 0.8, 1]) {
    const { out } = run(meter, raw, 400);
    for (const x of out) assert.ok([0, 0.25, 0.5, 0.75, 1].includes(x), `neon ${x}`);
  }
});

test("ocean: edges lag the centre after a burst", () => {
  const meter = M.create(7, "ocean");
  const { out } = run(meter, 1, 80);
  assert.ok(out[3] / M.weights(7)[3] > out[0] / M.weights(7)[0] + 0.1, `centre ${out[3]} edge ${out[0]}`);
});

test("ember flickers each bar between 72% and 112% of classic", () => {
  let n = 0;
  const rng = () => [0, 0.999][n++ % 2]; // alternate the two extremes
  const meter = M.create(7, "ember", { random: rng });
  const { out } = run(meter, 1, 1500);
  for (const x of out) assert.ok(x >= 0 && x <= 1);
});

test("unknown themes behave as classic", () => {
  const a = run(M.create(7, "nope"), 0.8, 400).out;
  const b = run(M.create(7, "classic"), 0.8, 400).out;
  assert.deepEqual(a, b);
});

test("weights: the pill's seven bars use the thesis table; other counts get a hump", () => {
  assert.deepEqual(M.weights(7), [0.52, 0.7, 0.88, 1, 0.88, 0.7, 0.52]);
  const w = M.weights(21);
  assert.equal(w.length, 21);
  assert.ok(w[10] > w[0] && Math.abs(w[0] - w[20]) < 1e-9);
});

test("reset puts the envelope back at rest", () => {
  const meter = M.create(7, "classic");
  run(meter, 1, 500);
  meter.reset();
  meter.setLevel(0);
  assert.deepEqual(meter.frame(16, 10), [0, 0, 0, 0, 0, 0, 0]);
});

test("paintBar scales a bar and pre-stretches its caps, for the pill and the dock alike", () => {
  const style = {};
  M.paintBar({ style }, 0.5);
  assert.equal(style.transform, "scaleY(0.500)");
  assert.equal(style.borderRadius, "2px / 4.00px", "2 px caps survive a half-height scale");
});

test("atRest: true in silence, false while a voice is in the envelope, true once it has fallen", () => {
  const m = M.create(7, "classic");
  assert.ok(m.atRest(), "a fresh meter is at rest");
  run(m, 0.8, 200);
  assert.ok(!m.atRest());
  const { t } = run(m, 0, 200, 200);
  assert.ok(!m.atRest(), "still falling 200 ms into the pause");
  run(m, 0, 3000, t);
  assert.ok(m.atRest(), "settled after the release");
});
