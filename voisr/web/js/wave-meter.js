"use strict";
window.Voisr = window.Voisr || {};

/* Makes Home's banner wave answer the mic while a click-to-record dictation
   runs. js/wave.js keeps drawing the wave's shape; this only sets its height,
   from the same 30 Hz mic level as the pill (V.api().get_level()), with one read
   in flight at most.

   The height is a transform on each path, not on the element: a transform from
   outside the SVG would squash the strokes as well, since non-scaling-stroke
   only sees the ones inside it.

   A first answer of null means the recorder has no meter (the ffmpeg fallback):
   the wave then keeps its full height and just drifts. Under reduced motion the
   wave holds still, so the level shows as brightness. Nothing is read while the
   window is hidden or not focused, which is when wave.js stops too, or while the
   wave itself can't be seen. Timers are injectable so Node can drive it frame by
   frame. */
window.Voisr.waveMeter = (function () {
  // the pill's gate and envelope (js/meter.js), so both recorders move alike
  const { gate, ATTACK_MS, RELEASE_MS } = window.Voisr.meter;
  const POLL_MS = 1000 / 30;
  const SETTLE_MS = 180;
  const QUIET = 0.3;          // the wave's height in silence, of its full height
  const TRANSCRIBING = 0.45;  // ...and while the words come back
  const MID = 80;             // the wave's centre line, in wave.js's 160-unit box
  const NEAR = 0.002;

  function create(opts) {
    const el = opts.el;
    const readLevel = opts.readLevel;
    const raf = opts.raf || ((fn) => window.requestAnimationFrame(fn));
    const every = opts.every || ((fn, ms) => setInterval(fn, ms));
    const stopEvery = opts.stopEvery || ((id) => clearInterval(id));
    const isReduced = opts.isReduced ||
      (() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    // no boxes: another view is showing, or the banner is too narrow for a wave
    const isWatching = opts.isWatching ||
      (() => document.visibilityState !== "hidden" && document.hasFocus() && el.getClientRects().length > 0);
    const now = opts.now || (() => performance.now());

    let poll = 0, frameId = 0, last = 0, inFlight = false, session = 0;
    let scale = 1, target = 1, rise = SETTLE_MS, fall = SETTLE_MS;

    function paint() {
      const paths = Array.from(el.querySelectorAll("path"));
      if (isReduced()) {
        for (const path of paths) path.removeAttribute("transform");
        el.style.opacity = scale === 1 ? "" : (0.35 + 0.65 * scale).toFixed(3);
        return;
      }
      el.style.opacity = "";
      for (const path of paths) {
        if (scale === 1) path.removeAttribute("transform");
        else path.setAttribute("transform", `matrix(1 0 0 ${scale.toFixed(3)} 0 ${(MID * (1 - scale)).toFixed(2)})`);
      }
    }

    function frame(t) {
      frameId = 0;
      const dt = Math.min(64, t - last);
      last = t;
      const tau = target > scale ? rise : fall;
      scale += (target - scale) * (1 - Math.exp(-dt / tau));
      if (Math.abs(target - scale) < NEAR) scale = target;
      paint();
      // parked once it arrives; the next reading starts frames again
      if (scale !== target) frameId = raf(frame);
    }

    function aim(next, up, down) {
      target = next;
      rise = up;
      fall = down;
      if (scale === target) return;
      if (!frameId) { last = now(); frameId = raf(frame); }
    }

    function stopPolling() {
      if (poll) stopEvery(poll);
      poll = 0;
      session += 1;  // any read still in flight now belongs to the past
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
            stopPolling();
            el.dataset.meter = "loop";
            aim(1, SETTLE_MS, SETTLE_MS);
            return;
          }
          aim(QUIET + (1 - QUIET) * gate(level), ATTACK_MS, RELEASE_MS);
        }, () => { inFlight = false; });  // a failed read is skipped, not fatal
    }

    function start() {
      stopPolling();
      const my = session;
      el.dataset.meter = "live";
      aim(QUIET, SETTLE_MS, SETTLE_MS);
      poll = every(() => {
        if (isWatching()) read(my);
        else aim(QUIET, SETTLE_MS, SETTLE_MS);
      }, POLL_MS);
    }

    function settle() {
      stopPolling();
      aim(TRANSCRIBING, SETTLE_MS, SETTLE_MS);
    }

    function idle() {
      stopPolling();
      delete el.dataset.meter;
      aim(1, SETTLE_MS, SETTLE_MS);
    }

    return { start, settle, idle };
  }

  return { create };
})();
