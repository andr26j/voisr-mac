"use strict";
window.Voisr = window.Voisr || {};

/* Voice meter: turns the 30 Hz mic level Python pushes (0..1, already in
   dB space, see stream_recorder.level_from_int16) into per-bar heights, once
   per display frame. Pure maths, plus the one style write every scaled bar
   shares (paintBar): the pill (widget.html) and the dashboard dock draw from
   the same numbers the same way, so both recorders move alike.

   createPill: the pill's 17 bars take the design's silhouette, and each keeps
   its own rhythm so a voice looks like speech rather than one shape swelling.
   create: any count, a plain centre-weighted hump that varies by +-16%. */
window.Voisr.meter = (function () {
  const GATE = 0.04;          // room hiss and fans stay at rest
  const ATTACK_MS = 50;       // quick to rise, so syllables land
  const RELEASE_MS = 220;     // slow to fall, so bars don't chatter between them
  const VARIANCE = 0.16;
  const PILL_BASE = 0.65;
  const PILL_VARIANCE = 0.7;
  /* [seconds per cycle, offset in seconds], bar by bar. The live meter uses
     them to vary a real voice; the unmetered fallback plays them as they are. */
  const PILL_SPEAK = [
    [0.9, 0], [0.7, 0.15], [1.1, 0.05], [0.8, 0.3], [1.0, 0.1], [0.75, 0.25],
    [1.15, 0.2], [0.85, 0.05], [0.95, 0.35], [0.7, 0.18], [1.05, 0.28], [0.8, 0.08],
    [0.9, 0.32], [0.72, 0.12], [1.1, 0.22], [0.86, 0.02], [0.98, 0.26],
  ];

  function clamp01(x) { return x > 0 ? (x < 1 ? x : 1) : 0; }

  function gate(raw) {
    if (!(raw > GATE)) return 0;  // also catches NaN
    return clamp01((raw - GATE) / (1 - GATE));
  }

  /* One-pole envelope: moves `env` toward `target` with a time constant of
     `attack` ms when rising and `release` ms when falling. */
  function step(env, target, dt, attack, release) {
    const tau = target > env ? attack : release;
    return env + (target - env) * (1 - Math.exp(-dt / tau));
  }

  /* scaleY squashes the rounded ends; pre-stretch the vertical radius so
     the caps stay round at every height */
  function paintBar(el, scale, radius = 2) {
    el.style.transform = `scaleY(${scale.toFixed(3)})`;
    el.style.borderRadius = `${radius}px / ${(radius / scale).toFixed(2)}px`;
  }

  /* 0..1 across the bars, low at the edges: the shape behind every height the
     pill draws */
  function hump(n) {
    return Array.from({ length: n }, (_, i) => Math.sin(Math.PI * (i + 0.5) / n) ** 0.9);
  }

  function weights(n) {
    return Array.from({ length: n }, (_, i) => 0.42 + 0.58 * Math.sin(Math.PI * (i + 0.5) / n));
  }

  function pillWeights() {
    return hump(PILL_SPEAK.length).map((s) => (4 + 22 * s) / 26);  // 6.6-26 px of 26
  }

  /* seeds: two incommensurate sines per bar, so the motion is deterministic
     and never visibly periodic */
  function build(w, seeds, base, variance) {
    const n = w.length;
    const env = new Array(n).fill(0);
    let target = 0;

    function frame(dt, now) {
      const out = new Array(n);
      const t = now / 1000;
      for (let i = 0; i < n; i++) {
        env[i] = step(env[i], target, dt, ATTACK_MS, RELEASE_MS);
        const s = seeds[i];
        const jitter = 0.5 * Math.sin(t * s[0] + s[2]) + 0.5 * Math.sin(t * s[1] + s[3]);
        let x = clamp01(env[i] * w[i] * (base + variance * jitter));
        if (x < 1e-4) x = 0;  // settle exactly, so silence is a still frame
        out[i] = x;
      }
      return out;
    }

    return {
      setLevel(raw) { target = gate(raw); },
      frame,
      /* silent and settled: every frame from here paints rest, so a
         painter can stop asking for frames until the next setLevel */
      atRest: () => target === 0 && env.every((e) => e < 1e-4),
      reset() { env.fill(0); target = 0; },
    };
  }

  function create(n) {
    const seeds = Array.from({ length: n }, (_, i) => [5 + i * 1.7, 8.3 + i * 2.3, i * 1.3, i * 2.1]);
    return build(weights(n), seeds, 1, VARIANCE);
  }

  /* The pill's bars sit lower on average and swing further, so they rarely
     all reach their full height at once. */
  function createPill() {
    const seeds = PILL_SPEAK.map(([period, offset]) => {
      const speed = 2 * Math.PI / period;
      return [speed, speed * 2.3, speed * offset, speed * offset * 1.7];
    });
    return build(pillWeights(), seeds, PILL_BASE, PILL_VARIANCE);
  }

  return { gate, step, hump, weights, pillWeights, create, createPill, paintBar, ATTACK_MS, RELEASE_MS, PILL_SPEAK };
})();
