"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* The sidebar mark's wave, which drifts slowly while Voisr is in front.

     The curve is voisr/logo_wave.py's small one, because the mark is 26 px. At
     clock 0 this draws exactly what index.html already holds, which is the
     logo itself and what reduced motion keeps showing.

     The app stays resident, so it stops whenever the window is hidden or not
     focused, and holds its frame until it can resume. wave.js runs the banner
     under the same rules, but its loop is built around the banner's three
     lines. */
  const POINTS = 44, POWER = 1.25, BREATHE = .2;
  const CYCLES = 1.5, PHASE = -Math.PI / 2;
  const X_START = 10, X_END = 90, MID = 50, AMP = 30;
  const PERIOD = 5;  // seconds per turn
  const FRAME_MS = 1000 / 30;

  const ENVELOPE = Array.from({ length: POINTS + 1 }, (_, i) =>
    Math.pow(Math.sin(Math.PI * (i / POINTS)), POWER));

  /* The wave's path, `clock` seconds in. */
  function pathFor(clock) {
    const turn = (2 * Math.PI * clock) / PERIOD;
    const amp = AMP * (1 + BREATHE * Math.sin(turn));
    let d = "";
    for (let i = 0; i <= POINTS; i++) {
      const u = i / POINTS;
      const y = MID + amp * ENVELOPE[i] * Math.sin(2 * Math.PI * CYCLES * u + PHASE - turn);
      d += `${i ? "L" : "M"}${(X_START + (X_END - X_START) * u).toFixed(2)} ${y.toFixed(2)}`;
    }
    return d;
  }

  V.logoWave = { pathFor };

  const path = document.getElementById("brand-wave");
  if (!path) return;

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let frame = 0, last = 0, clock = 0;

  const canRun = () => !motionQuery.matches && document.visibilityState !== "hidden"
    && document.hasFocus();

  function sync() {
    if (canRun()) {
      if (!frame) {
        last = 0;
        frame = window.requestAnimationFrame(tick);
      }
      return;
    }
    if (frame) window.cancelAnimationFrame(frame);
    frame = 0;
    if (motionQuery.matches && clock) {
      clock = 0;
      path.setAttribute("d", pathFor(clock));
    }
  }

  function tick(now) {
    frame = window.requestAnimationFrame(tick);
    if (!last) { last = now; return; }
    // the allowance keeps a 60 Hz display on every second frame rather than
    // slipping to every third when a frame lands a little early
    if (now - last < FRAME_MS - 2) return;
    // capped, so a stall resumes where it was instead of leaping ahead
    clock += Math.min(now - last, 100) / 1000;
    last = now;
    path.setAttribute("d", pathFor(clock));
  }

  document.addEventListener("visibilitychange", sync);
  window.addEventListener("focus", sync);
  window.addEventListener("blur", sync);
  motionQuery.addEventListener("change", sync);
  sync();
})(window.Voisr);
