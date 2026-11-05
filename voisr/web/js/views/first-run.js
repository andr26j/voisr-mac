"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* Three steps over the whole window: permissions, key, first dictation. It
     talks to the same bridge calls as Home and Settings, reads a grant with
     Home's rules (V.permissions), and never traps anyone: every step that can
     get stuck has a way out to Home, whose banner takes over from there. */

  const POLL_MS = 1000;
  const LEVEL_MS = 200;       // only "is the mic open?", not the shape of a voice
  const LISTENING_SPEED = 3.2;  // wave.js's listening pace
  const LEAVE_MS = 240;       // --slow, the fade into Home
  const AFTER_CAPTURE_MS = 300;
  const tickSvg = (cls) => `<svg class="${cls}" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 4.8"/></svg>`;

  /* What's on screen. `step` is 1, 2, 3 or "done". */
  let step = null, enterApp = null, leaving = false;
  let panes = null, fallback = false;
  let poll = 0, pollInFlight = false, levelPoll = 0, levelInFlight = false;
  let listening = false, capturing = false, captureEndedAt = -Infinity, waveEl = null;
  let drawn = "";             // what the current step last drew, so polls stay quiet
  const granted = new Set();  // grants already shown as on: only a new one draws its tick

  const el = (...args) => V.ui.el(...args);
  const $ = (sel) => V.ui.$(sel);

  /* ---------- what's true ---------- */

  /* The key works on the fallback tap even with Accessibility stale, and an
     unknown grant never counts as on. */
  function keyWorks() {
    if (!panes) return false;
    return fallback || panes.filter((p) => p.hotkey)
      .every((p) => p.granted === true && !V.permissions.stale(p));
  }

  const mic = () => (panes || []).find((p) => p.key === "microphone");
  const micRefused = () => { const m = mic(); return !!m && m.granted === false; };
  const micPending = () => { const m = mic(); return !!m && V.permissions.micPending(m); };

  /* Fails quietly on a poll: the last answer stays on screen, and a toast a
     second would help nobody. The first read of a step says what went wrong. */
  async function readPanes(quiet) {
    let res = null;
    if (quiet) {
      try { res = await V.api().get_permissions(); } catch (e) { res = null; }
    } else {
      res = await V.ui.tryApi(V.api().get_permissions(), "Check permissions");
    }
    if (res && res.panes) { panes = res.panes; fallback = res.fallback === true; }
  }

  /* ---------- the frame ---------- */

  function setProgress() {
    const at = step === "done" ? 3 : step;
    const bar = $("#first-run-progress");
    bar.setAttribute("aria-label", `Step ${at} of 3`);
    Array.from(bar.children).forEach((seg, i) => seg.classList.toggle("is-on", i < at));
  }

  /* A new step replays the entrance from frame 0 and moves focus to its
     headline, so a screen reader starts there. A poll changing a step's content
     only swaps the slots, in place. */
  function paint(head, sub, body, foot, entering) {
    const box = $("#first-run-step");
    const hadFocus = entering || (document.activeElement && box.contains(document.activeElement));
    box.replaceChildren(
      el("header", { class: "first-run-head" },
        el("h1", { class: "heading-serif", tabindex: "-1", text: head }),
        el("p", { class: "first-run-sub", text: sub })),
      el("div", { class: "first-run-body" }, body),
      el("div", { class: "first-run-foot" }, foot));
    if (entering) {
      box.classList.remove("is-entering");
      void box.offsetWidth;  // force reflow, so the entrance restarts
      box.classList.add("is-entering");
    } else {
      box.classList.remove("is-entering");  // or the new children would play it
    }
    if (hadFocus) {
      const main = box.querySelector(".btn-primary");
      const target = !entering && main && !main.disabled ? main : box.querySelector(".heading-serif");
      if (target && target.focus) target.focus();
    }
  }

  function button(cls, text, onClick, attrs) {
    return el("button", { type: "button", class: cls, text, on: { click: onClick }, ...attrs });
  }

  function goTo(next) {
    step = next;
    drawn = "";
    capturing = false;
    setListening(false);
    if (waveEl && V.wave) V.wave.unmount(waveEl);
    waveEl = null;
    if (next) setProgress();  // leaving: the bar stays as it was while it fades
    window.removeEventListener("voisr:new-dictation", onDictation);
    if (next === 3) window.addEventListener("voisr:new-dictation", onDictation);
    const live = next === 1 || next === 3;
    if (live && !poll) poll = setInterval(tick, POLL_MS);
    if (!live && poll) { clearInterval(poll); poll = 0; }
    if (next === 3 && !levelPoll) levelPoll = setInterval(tickLevel, LEVEL_MS);
    if (next !== 3 && levelPoll) { clearInterval(levelPoll); levelPoll = 0; }
    return draw(true);
  }

  function draw(entering) {
    if (step === 1) return drawPermissions(entering);
    if (step === 2) return drawKey(entering);
    if (step === 3) return drawDictation(entering);
    return undefined;
  }

  /* Granting happens in System Settings, outside the app, so steps that wait on
     it keep looking. It carries on while another app is in front, because that's
     where the switch is; only a hidden window stops it. */
  async function tick() {
    if (pollInFlight || document.visibilityState === "hidden") return;
    pollInFlight = true;
    try {
      await readPanes(true);
    } finally {
      pollInFlight = false;
    }
    if (step === 3 && !keyWorks()) { goTo(1); return; }
    draw(false);
  }

  /* ---------- step 1: permissions ---------- */

  const WHY = {
    microphone: "To hear you",
    input_monitoring: "For push-to-talk",
    accessibility: "For pasting",
  };
  const whyFor = (p) => (p.key === "accessibility" && p.hotkey
    ? "For push-to-talk and pasting" : WHY[p.key] || "");

  function openButton(p) {
    return button("btn-signal", "Open Settings", V.permissions.openPane(p.key));
  }

  function permissionRow(p) {
    const P = V.permissions;
    let why = whyFor(p), control;
    if (P.stale(p)) {
      why = fallback ? "The key still works through Input Monitoring"
        : "Switched on, but macOS is still blocking it";
      control = P.resetButton(p, "btn-signal");
    } else if (p.granted === true) {
      const cls = granted.has(p.key) ? "first-run-tick" : "first-run-tick is-new";
      control = el("span", { class: "first-run-on", html: `${tickSvg(cls)}On` });
      granted.add(p.key);
    } else if (P.micPending(p)) {
      why = "Asked on your first dictation";
      control = el("span", { class: "pill", text: "Later" });
    } else if (p.key === "microphone") {
      why = "Switched off, so nothing can be recorded";
      control = openButton(p);
    } else {
      control = openButton(p);
    }
    return el("div", { class: "first-run-row" },
      el("div", { class: "first-run-row-text" },
        el("span", { class: "first-run-row-name", text: p.label }),
        el("span", { class: "first-run-row-why", text: why })),
      control);
  }

  /* Home's rule: a denied key pane can still be the stale grant, because listen
     mode's check reads false while the switch shows on. Never for an unknown. */
  function recoverLine() {
    const P = V.permissions;
    const stale = (panes || []).find(P.stale);
    if (stale) {
      return el("p", { class: "first-run-recover" },
        el("span", { text: fallback ? `${stale.label} back on?` : "Still blocked?" }),
        P.relaunchButton("text-btn"));
    }
    const denied = (panes || []).find((p) => p.hotkey && p.granted === false);
    if (!denied) return null;
    return el("p", { class: "first-run-recover" },
      el("span", { text: "Already on and nothing happens?" }),
      P.resetButton(denied, "text-btn"), P.relaunchButton("text-btn"));
  }

  function drawPermissions(entering) {
    const shown = panes || [];
    const sig = JSON.stringify([fallback, shown.map((p) => [p.key, p.granted, p.effective])]);
    if (!entering && sig === drawn) return;
    const three = shown.length > 2;
    const ok = keyWorks();
    // the key's own pane first, the mic last: the board's order
    const rank = (p) => (p.hotkey ? 0 : p.key === "microphone" ? 2 : 1);
    const rows = [...shown].sort((a, b) => rank(a) - rank(b)).map(permissionRow);
    paint(three ? "Three permissions first" : "Two permissions first",
      `${three ? "One to hear you, one for the key, one to paste." : "One to hear you, one for the key and pasting."} Everything stays on this Mac.`,
      el("div", { class: "first-run-card", "aria-live": "polite" }, rows),
      [button("btn-primary btn-full", "Continue", () => goTo(2), { disabled: ok ? null : "disabled" }),
       recoverLine(),
       ok ? null : button("text-btn", "Set up later", finish)],
      entering);
    drawn = sig;
  }

  /* ---------- step 2: key ---------- */

  const SYMBOLS = [["Option", "⌥"], ["Command", "⌘"], ["Shift", "⇧"], ["Control", "⌃"],
                   ["Caps Lock", "⇪"], ["Fn", "fn"]];

  /* "Right Option (⌥)" gives ⌥ on the cap and "Right Option" under it. A key with
     no symbol wears its own name. */
  function keyParts(display) {
    const named = /^(.*\S)\s*\(([^)]+)\)$/.exec(display);
    if (named) return { cap: named[2], name: named[1] };
    const hit = SYMBOLS.find(([word]) => display.includes(word));
    if (hit) return { cap: hit[1], name: display };
    return { cap: display, name: display, word: display.length > 3 };
  }

  /* The same key press can reach the page just after Python has answered, as a
     second event the capture never saw (Settings has the same guard). Return
     mustn't then press Change again, or Continue. */
  const justCaptured = () => Date.now() - captureEndedAt < AFTER_CAPTURE_MS;

  async function changeKey() {
    if (capturing || justCaptured()) return;
    capturing = true;
    await drawKey(false);
    // tryApi says why a timeout failed; Esc cancelling it needs no comment
    await V.ui.tryApi(V.api().capture_hotkey(), "Change key");
    captureEndedAt = Date.now();
    if (step !== 2) return;
    capturing = false;
    await drawKey(false);
    const change = $("#first-run-step").querySelector(".btn-secondary");
    if (change && change.focus) change.focus();
  }

  async function drawKey(entering) {
    const res = await V.ui.tryApi(V.api().hotkey_display(), "Load shortcut");
    if (step !== 2) return;
    const parts = keyParts((res && res.display) || "unset");
    const on = capturing ? " is-listening" : "";
    paint("Pick the key you'll hold",
      "Hold it in any app and talk. Let go and the text lands where you were typing.",
      [el("kbd", { class: `first-run-keycap${parts.word ? " is-word" : ""}${on}`, text: parts.cap }),
       el("span", { class: `first-run-key-name${on}`, text: capturing ? "Press a key…" : parts.name }),
       button("btn-secondary", "Change…", changeKey, { disabled: capturing ? "disabled" : null })],
      [button("btn-primary btn-full", "Continue", () => { if (!justCaptured()) goTo(3); },
              { disabled: capturing ? "disabled" : null })],
      entering);
  }

  /* ---------- step 3: first dictation ---------- */

  function setListening(on) {
    if (on === listening) return;
    listening = on;
    if (waveEl && V.wave) V.wave.setSpeed(waveEl, on ? LISTENING_SPEED : 1);
  }

  /* A level means a recorder is metering, so the mic is open. The ffmpeg
     fallback has no meter: there the wave only drifts, and the pill still shows
     what's happening. Not read without focus, which is when wave.js stops: this
     step can sit behind other apps for as long as the app stays resident. */
  async function tickLevel() {
    if (levelInFlight || !waveEl || !document.hasFocus()) return;
    levelInFlight = true;
    try {
      const level = await V.api().get_level();
      if (step === 3) setListening(level !== null && level !== undefined);
    } catch (e) {
      // the wave just keeps drifting
    } finally {
      levelInFlight = false;
    }
  }

  function drawDictation(entering) {
    const sig = micRefused() ? "refused" : micPending() ? "pending" : "ready";
    if (!entering && sig === drawn) return;
    drawn = sig;
    if (waveEl && V.wave) V.wave.unmount(waveEl);
    waveEl = null;
    if (sig === "refused") {
      paint("Voisr can't hear you", "Nothing can be recorded until macOS grants Microphone.", null,
        [button("btn-signal btn-full", "Open Settings", V.permissions.openPane("microphone")),
         button("text-btn", "Skip", finish)], entering);
      return;
    }
    const wave = el("div", { class: "first-run-wave" });
    paint("Hold your key and say something",
      sig === "pending"
        ? "Anything. Only your Mac is listening. macOS will ask about the microphone first; allow it, then hold the key again."
        : "Anything. Only your Mac is listening.",
      wave, [button("btn-secondary", "Skip", finish)], entering);
    waveEl = wave;
    if (V.wave) {
      V.wave.mount(wave);
      if (listening) V.wave.setSpeed(wave, LISTENING_SPEED);
    }
  }

  /* The real pill says when a clip was too short or nothing was heard, and those
     leave no row, so this only ever hears about a dictation that landed. */
  async function onDictation() {
    if (step !== 3) return;
    const rows = await V.ui.tryApi(V.api().get_history(), "Load your dictation");
    if (step !== 3) return;
    const newest = (rows || []).reduce((a, b) => (!a || b.ts > a.ts ? b : a), null);
    showDone(newest);
  }

  function showDone(item) {
    goTo("done");
    const words = item && item.words;
    const line = words === 1 ? "1 word. It didn't leave this Mac."
      : words ? `${words} words. None of them left this Mac.`
      : "None of it left this Mac.";
    paint("That's it", line,
      item ? el("p", { class: "first-run-said", text: item.text }) : null,
      [button("btn-primary btn-full", "Open Voisr", finish)], true);
    const open = $("#first-run-step").querySelector(".btn-primary");
    if (open && open.focus) open.focus();
  }

  /* ---------- the keyboard ----------

     First run hides the whole app, so a button Tab can't reach is a dead end.
     On the document, because a redrawn step can leave focus on the body. */
  function onKey(e) {
    if (e.key === "Tab" && step) V.ui.walkTab(e, $("#first-run"));
  }

  /* ---------- in and out ---------- */

  async function finish() {
    if (leaving) return;
    leaving = true;
    goTo(null);
    // a failed save only means it shows again next launch
    await V.ui.tryApi(V.api().set_config({ first_run_done: true }), "Save setup");
    const root = $("#first-run"), box = $("#first-run-step");
    $(".shell").hidden = false;
    enterApp();
    root.classList.add("is-leaving");
    setTimeout(() => {
      root.hidden = true;
      root.classList.remove("is-leaving");
      box.replaceChildren();
      leaving = false;
    }, LEAVE_MS);
  }

  /* Opens first run if it's still to do, and says whether it did. Home's views
     stay hidden meanwhile, which also keeps their polls and waves quiet. If it
     can't be asked, the app has to start anyway. Reopening lands on the first
     step that isn't finished: the key's grant is the only one that's known. */
  async function start(onDone) {
    const res = await V.ui.tryApi(V.api().get_first_run(), "Check first run");
    if (!res || !res.pending) return false;
    enterApp = onDone;
    document.querySelectorAll(".view").forEach((v) => { v.hidden = true; });
    $(".shell").hidden = true;
    $("#first-run").hidden = false;
    try {
      await readPanes(false);
      for (const p of panes || []) if (p.granted === true) granted.add(p.key);
      await goTo(keyWorks() ? 2 : 1);
    } catch (e) {
      // the app has to start anyway, and first run gets another go next launch
      goTo(null);
      $("#first-run").hidden = true;
      $(".shell").hidden = false;
      return false;
    }
    window.addEventListener("focus", () => {
      if (step === 1 || step === 3) tick();
    });
    document.addEventListener("keydown", onKey);
    return true;
  }

  V.views = V.views || {};
  V.views.firstRun = { start, keyParts };
})(window.Voisr);
