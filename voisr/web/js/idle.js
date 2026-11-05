"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* Whether anyone has used the window lately. The waves cost about 20% of a
     core while they move, whatever their size, and a dashboard left in front
     has nobody watching, so they settle after this long without input. */
  const AFTER_MS = 30000;
  // each of these only notes the time, so a moving pointer costs next to
  // nothing, and one timer runs however much input arrives
  const INPUTS = ["pointerdown", "keydown", "wheel"];

  const listeners = [];
  let idle = false, lastInput = 0, timer = 0, pointerX = null, pointerY = null;

  function check() {
    timer = 0;
    const left = AFTER_MS - (window.performance.now() - lastInput);
    if (left > 0) {
      timer = window.setTimeout(check, left);
      return;
    }
    idle = true;
    listeners.forEach((fn) => fn());
  }

  function wake() {
    lastInput = window.performance.now();
    if (!timer) timer = window.setTimeout(check, AFTER_MS);
    if (idle) {
      idle = false;
      listeners.forEach((fn) => fn());
    }
  }

  const options = { capture: true, passive: true };
  INPUTS.forEach((type) => window.addEventListener(type, wake, options));
  // Only a pointer that really moved counts: WebKit can send moves with the
  // pointer still when what's under it changes, and the waves mustn't be able
  // to keep themselves awake that way. None showed up when this was measured.
  window.addEventListener("pointermove", (e) => {
    if (e.screenX === pointerX && e.screenY === pointerY) return;
    pointerX = e.screenX;
    pointerY = e.screenY;
    wake();
  }, options);
  // coming back to the window counts, even with the pointer still
  window.addEventListener("focus", wake);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "hidden") wake();
  });
  wake();

  V.idle = {
    isIdle: () => idle,
    onChange: (fn) => { listeners.push(fn); },
    wake,
  };
})(window.Voisr);
