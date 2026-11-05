"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* The banner's sound wave: three enveloped sines travelling at different
     speeds. A wave's shape changes every frame, which no transform can do, so
     this rewrites the paths on a timer rather than using SVG <animate>, which
     ignores reduced motion and can't change speed without a jump.

     The app stays resident, so nothing runs unless someone can be watching: no
     rAF while the window is hidden or not focused, a banner is off-screen or
     hidden, or under reduced motion. Each of those holds a still frame. After
     V.idle's wait without input it eases to a stop too, unless it's showing the
     mic is open, and eases off again on the next input.

     Put `data-wave` on an element to get a wave. Anything rendered after load
     calls mount(el). setSpeed(el, n) changes the pace without a jump: 1 is
     idle, 3.2 is listening. */
  const WIDTH = 420, MID = 80, POINTS = 80, POWER = 1.25, BREATHE = .2;
  const FRAME_MS = 1000 / 30;
  const LINES = [
    { amp: 50, cycles: 2.5, width: 1.5, opacity: .38, period: 11, offset: 1.7 },
    { amp: 36, cycles: 3.5, width: 1.5, opacity: .6, period: 9, offset: .8 },
    { amp: 58, cycles: 3, width: 2.25, opacity: 1, period: 7, offset: 0 },
  ];
  /* how fast a speed change settles, in seconds: quick enough to answer the
     mic opening, slow enough not to lurch */
  const SPEED_SETTLE = .25;
  const IDLE_SPEED = 1;
  // slower than this is a stop: it takes a little over a second to get here
  const STOPPED = .01;

  const ENVELOPE = Array.from({ length: POINTS + 1 }, (_, i) =>
    Math.pow(Math.sin(Math.PI * (i / POINTS)), POWER));

  /* The path for one line, `clock` seconds in. */
  function pathFor(line, clock) {
    const turn = (2 * Math.PI * clock) / line.period;
    const amp = line.amp * (1 + BREATHE * Math.sin(turn));
    let d = "";
    for (let i = 0; i <= POINTS; i++) {
      const u = i / POINTS;
      const y = MID + amp * ENVELOPE[i] * Math.sin(2 * Math.PI * line.cycles * u + line.offset - turn);
      d += `${i ? "L" : "M"}${(WIDTH * u).toFixed(2)} ${y.toFixed(2)}`;
    }
    return d;
  }

  const waves = new Map();
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let frame = 0, last = 0;

  function draw(wave) {
    wave.paths.forEach((path, i) => path.setAttribute("d", pathFor(LINES[i], wave.clock)));
  }

  const canRun = () => !motionQuery.matches && document.visibilityState !== "hidden"
    && document.hasFocus();

  /* The speed this wave is heading for. Any other pace means the mic is doing
     something, and that stays on show however long it takes. */
  const goal = (wave) => (wave.target === IDLE_SPEED && V.idle.isIdle() ? 0 : wave.target);

  function anyMoving() {
    for (const wave of waves.values()) {
      if (wave.onScreen && (goal(wave) || wave.speed > STOPPED)) return true;
    }
    return false;
  }

  function stop() {
    if (frame) window.cancelAnimationFrame(frame);
    frame = 0;
  }

  function sync() {
    if (canRun() && anyMoving()) {
      if (!frame) {
        last = 0;
        frame = window.requestAnimationFrame(tick);
      }
    } else {
      stop();
    }
  }

  function tick(now) {
    // Checked here as well as on events: if WebKit missed a blur, the wave
    // would otherwise keep painting behind other apps.
    if (!canRun()) {
      frame = 0;
      sync();
      return;
    }
    frame = window.requestAnimationFrame(tick);
    if (!last) { last = now; return; }
    // the small allowance keeps a 60 Hz display on every second frame rather
    // than slipping to every third when a frame lands a little early
    if (now - last < FRAME_MS - 2) return;
    // capped, so a stall resumes where it was instead of leaping ahead
    const dt = Math.min(now - last, 100) / 1000;
    last = now;
    for (const [el, wave] of waves) {
      if (!el.isConnected) { unmount(el); continue; }
      if (!wave.onScreen) continue;
      wave.speed += (goal(wave) - wave.speed) * (1 - Math.exp(-dt / SPEED_SETTLE));
      wave.clock += dt * wave.speed;
      draw(wave);
    }
    if (!anyMoving()) stop();
  }

  const observer = typeof IntersectionObserver === "function"
    ? new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const wave = waves.get(entry.target);
        if (wave) wave.onScreen = entry.isIntersecting;
      }
      sync();
    })
    : null;

  function mount(el) {
    if (waves.has(el)) return;
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${WIDTH} 160`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const paths = LINES.map((line) => {
      const path = document.createElementNS(NS, "path");
      path.setAttribute("stroke-width", line.width);
      path.setAttribute("stroke-opacity", line.opacity);
      svg.append(path);
      return path;
    });
    el.replaceChildren(svg);
    const wave = { paths, clock: 0, speed: 1, target: IDLE_SPEED, onScreen: !observer };
    waves.set(el, wave);
    draw(wave);
    if (observer) observer.observe(el);
    sync();
  }

  function unmount(el) {
    if (observer) observer.unobserve(el);
    waves.delete(el);
    sync();
  }

  function setSpeed(el, speed) {
    const wave = waves.get(el);
    if (!wave) return;
    wave.target = speed;
    V.idle.wake();  // the mic opening or closing is someone at work
    sync();
  }

  document.addEventListener("visibilitychange", sync);
  window.addEventListener("focus", sync);
  window.addEventListener("blur", sync);
  motionQuery.addEventListener("change", sync);
  V.idle.onChange(sync);
  document.querySelectorAll("[data-wave]").forEach(mount);

  V.wave = { mount, unmount, setSpeed, pathFor, LINES };
})(window.Voisr);
