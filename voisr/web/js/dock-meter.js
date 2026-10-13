"use strict";
window.Voisr = window.Voisr || {};

/* The Home dock's waveform, driven by the same live mic level as the pill.

   While the dock records it polls V.api().get_level() at 30 Hz (one read in
   flight at most) and draws js/meter.js's numbers once per display frame.
   A first answer of null means the recorder has no meter (the ffmpeg
   fallback): the dock then keeps the decorative loop it always had.

   Bars are 18 px tall and scaled, 3 px at rest; transcribing eases them to
   half height for the CSS shimmer, and idle eases them back. Timers are
   injectable so Node can drive it frame by frame. */
window.Voisr.dockMeter = (function () {
  const POLL_MS = 1000 / 30;
  const REST = 3 / 18;
  const SETTLE = "transform 180ms var(--ease)";

  function create(opts) {
    const wave = opts.wave;
    const readLevel = opts.readLevel;
    const raf = opts.raf || ((fn) => window.requestAnimationFrame(fn));
    const caf = opts.caf || ((id) => window.cancelAnimationFrame(id));
    const every = opts.every || ((fn, ms) => setInterval(fn, ms));
    const stopEvery = opts.stopEvery || ((id) => clearInterval(id));
    const isReduced = opts.isReduced ||
      (() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const now = opts.now || (() => performance.now());

    const bars = Array.from(wave.children);
    bars.forEach((b, i) => b.style.setProperty("--i", i));
    const meter = window.Voisr.meter.create(bars.length, "classic");

    let poll = 0, frameId = 0, last = 0, inFlight = false, session = 0;

    const paint = window.Voisr.meter.paintBar;

    function stopAll() {
      if (poll) stopEvery(poll);
      if (frameId) caf(frameId);
      poll = 0;
      frameId = 0;
      session += 1;  // any read still in flight now belongs to the past
    }

    function frame(t) {
      frameId = 0;
      const dt = Math.min(64, t - last);
      last = t;
      const still = isReduced();
      meter.frame(dt, t).forEach((x, i) => {
        const b = bars[i];
        if (still) {
          paint(b, 0.5);
          b.style.opacity = (0.35 + 0.65 * x).toFixed(3);
        } else {
          paint(b, REST + (1 - REST) * x);
        }
      });
      // parked in silence; the next reading's read() starts frames again
      if (!meter.atRest()) frameId = raf(frame);
    }

    function read(my) {
      if (inFlight) return;
      inFlight = true;
      Promise.resolve()
        .then(readLevel)
        .then((level) => {
          inFlight = false;
          if (my !== session) return;
          if (level === null || level === undefined) {
            stopAll();
            wave.dataset.meter = "loop";
            for (const b of bars) {
              b.style.transition = ""; b.style.transform = "";
              b.style.borderRadius = ""; b.style.opacity = "";
            }
            return;
          }
          meter.setLevel(level);
          if (!frameId) { last = now(); frameId = raf(frame); }
        }, () => { inFlight = false; });  // a failed read is skipped, not fatal
    }

    function start() {
      stopAll();
      const my = session;
      meter.reset();
      wave.dataset.meter = "live";
      for (const b of bars) { b.style.transition = "none"; b.style.opacity = ""; paint(b, REST); }
      poll = every(() => read(my), POLL_MS);
    }

    function ease(scale) {
      stopAll();
      wave.dataset.meter = "live";
      const transition = isReduced() ? "none" : SETTLE;
      for (const b of bars) { b.style.transition = transition; b.style.opacity = ""; paint(b, scale); }
    }

    return {
      start,
      settle: () => ease(0.5),
      idle: () => ease(REST),
    };
  }

  return { create };
})();
