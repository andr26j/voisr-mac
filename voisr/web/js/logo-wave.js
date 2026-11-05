"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* The sidebar mark's wave, which drifts slowly while Voisr is in front.

     The curve is voisr/logo_wave.py's small one, because the mark is 26 px. At
     clock 0 this draws exactly what index.html already holds, which is the
     logo itself and what reduced motion keeps showing.

     The app stays resident, so it stops whenever the window is hidden or not
     focused, and holds its frame until it can resume. After V.idle's wait
     without input it eases to a stop as well. wave.js runs the banner under the
     same rules, but its loop is built around the banner's three lines.

     This runs on a timer because a frame callback wakes the page on every frame
     of the display, drawn or not: for the same 20 drawings a second that cost
     about 17% of a core against 13%. */
  const POINTS = 44, POWER = 1.25, BREATHE = .2;
  const CYCLES = 1.5, PHASE = -Math.PI / 2;
  const X_START = 10, X_END = 90, MID = 50, AMP = 30;
  const PERIOD = 5;  // seconds per turn
  // Under half a pixel of movement a frame at this size, so more would cost
  // work without looking any smoother.
  const FRAME_MS = 1000 / 20;
  const SPEED_SETTLE = .25;  // seconds, the same as the banner's wave
  // slower than this is a stop: it takes a little over a second to get here
  const STOPPED = .01;

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
  let timer = 0, last = 0, clock = 0, speed = 1;

  const canRun = () => !motionQuery.matches && document.visibilityState !== "hidden"
    && document.hasFocus();

  const goal = () => (V.idle.isIdle() ? 0 : 1);

  function stop() {
    if (timer) window.clearInterval(timer);
    timer = 0;
  }

  function sync() {
    if (canRun() && (goal() || speed > STOPPED)) {
      if (!timer) {
        last = window.performance.now();
        timer = window.setInterval(tick, FRAME_MS);
      }
      return;
    }
    stop();
    if (motionQuery.matches && clock) {
      clock = 0;
      path.setAttribute("d", pathFor(clock));
    }
  }

  function tick() {
    // Checked here as well as on events: if WebKit missed a blur, the wave
    // would otherwise keep painting behind other apps.
    if (!canRun()) {
      sync();
      return;
    }
    const now = window.performance.now();
    // capped, so a stall resumes where it was instead of leaping ahead
    const dt = Math.min(now - last, 100) / 1000;
    last = now;
    speed += (goal() - speed) * (1 - Math.exp(-dt / SPEED_SETTLE));
    clock += dt * speed;
    path.setAttribute("d", pathFor(clock));
    if (!goal() && speed <= STOPPED) stop();
  }

  document.addEventListener("visibilitychange", sync);
  window.addEventListener("focus", sync);
  window.addEventListener("blur", sync);
  motionQuery.addEventListener("change", sync);
  V.idle.onChange(sync);
  sync();
})(window.Voisr);
