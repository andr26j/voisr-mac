"use strict";
window.Voisr = window.Voisr || {};

/* Voice meter: turns the 30 Hz mic level Python pushes (0..1, already in
   dB space, see stream_recorder.level_from_int16) into per-bar heights, once
   per display frame. Pure maths, plus the one style write every scaled bar
   shares (paintBar): the pill (widget.html) and the dashboard dock draw from
   the same numbers the same way, so both recorders move alike.

   Each theme shapes the same real signal instead of replaying its own loop:
     classic  centre-weighted, each bar varies +-16% so the shape is never stamped
     ember    classic plus a per-bar flicker between 72% and 112%, retargeted every 70 ms
     aurora   classic (its hue cycle is CSS)
     ocean    edges lag the centre: +30 ms attack, +90 ms release per step outward
     neon     classic quantised to four steps, like an LED meter
     breathe  every bar moves as one, no weighting or variance
     scanner  classic numbers; the page draws them as brightness, not size */
window.Voisr.meter = (function () {
  const GATE = 0.04;          // room hiss and fans stay at rest
  const ATTACK_MS = 50;       // quick to rise, so syllables land
  const RELEASE_MS = 220;     // slow to fall, so bars don't chatter between them
  const VARIANCE = 0.16;
  const PILL_WEIGHTS = [0.52, 0.7, 0.88, 1, 0.88, 0.7, 0.52];

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
     the 2 px caps stay round at every height */
  function paintBar(el, scale) {
    el.style.transform = `scaleY(${scale.toFixed(3)})`;
    el.style.borderRadius = `2px / ${(2 / scale).toFixed(2)}px`;
  }

  function weights(n) {
    if (n === PILL_WEIGHTS.length) return PILL_WEIGHTS.slice();
    return Array.from({ length: n }, (_, i) => 0.42 + 0.58 * Math.sin(Math.PI * (i + 0.5) / n));
  }

  function create(n, theme, opts) {
    const random = (opts && opts.random) || Math.random;
    const w = weights(n);
    const centre = (n - 1) / 2;
    const env = new Array(n).fill(0);
    // two incommensurate sines per bar: deterministic, never visibly periodic
    const seeds = Array.from({ length: n }, (_, i) => [5 + i * 1.7, 8.3 + i * 2.3, i * 1.3, i * 2.1]);
    const flicker = Array.from({ length: n }, () => ({ value: 1, target: 1, next: 0 }));
    let target = 0;

    function frame(dt, now) {
      const out = new Array(n);
      const t = now / 1000;
      for (let i = 0; i < n; i++) {
        let attack = ATTACK_MS, release = RELEASE_MS;
        if (theme === "ocean") {
          const d = Math.abs(i - centre);
          attack += d * 30; release += d * 90;
        }
        env[i] = step(env[i], target, dt, attack, release);
        let x;
        if (theme === "breathe") {
          x = env[i];
        } else {
          const s = seeds[i];
          const jitter = 0.5 * Math.sin(t * s[0] + s[2]) + 0.5 * Math.sin(t * s[1] + s[3]);
          x = env[i] * w[i] * (1 + VARIANCE * jitter);
        }
        if (theme === "ember") {
          const f = flicker[i];
          if (now >= f.next) { f.target = 0.72 + random() * 0.4; f.next = now + 70; }
          f.value = step(f.value, f.target, dt, 40, 40);
          x *= f.value;
        }
        x = clamp01(x);
        if (theme === "neon") x = Math.round(x * 4) / 4;
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
      reset() { env.fill(0); target = 0; for (const f of flicker) { f.value = 1; f.target = 1; f.next = 0; } },
    };
  }

  return { gate, step, weights, create, paintBar };
})();
