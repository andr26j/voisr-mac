"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* What each grant costs you when it is missing — the block leads with the
     consequence, not the name of the setting. */
  const PERM_BREAKS = {
    microphone: "Voisr can't hear you at all until macOS grants Microphone.",
    input_monitoring: "Voisr can't see the key until macOS grants Input Monitoring. " +
                      "Until then, holding it does nothing — everything else still works.",
    accessibility: "Voisr can't paste for you until macOS grants Accessibility. " +
                   "Dictations are copied to your clipboard instead.",
  };

  const MIC_FIRST_ASK = "asked on your first dictation";

  const PERM_WHY = {
    microphone: "needed to hear you",
    input_monitoring: "needed for push-to-talk",
    accessibility: "needed to paste for you",
  };

  /* A null mic is "not asked yet": macOS shows the prompt on the first
     recording, and Voisr isn't even listed in the Microphone pane until then.
     So it never blocks — holding the key is how the user gets asked. */
  const micPending = (p) => p.key === "microphone" && p.granted === null;
  const blocking = (panes) => panes.filter((p) => p.granted !== true && !micPending(p));

  function openPane(key) {
    return async () => {
      // open_settings_pane answers {"ok": bool} — a false is macOS refusing
      // to open the pane, which used to vanish silently and leave the user
      // clicking a control that appeared to do nothing.
      const res = await V.ui.tryApi(
        V.api().open_settings_pane(key), "Open System Settings");
      if (res && res.ok === false) V.ui.toast("Couldn't open System Settings");
    };
  }

  /* The blocking state. A missing grant is not a footnote: without Input
     Monitoring the entire product does nothing when you hold the key, and a
     chip above a hero still saying "hold this key to dictate" was a lie. */
  function permBlock(panes) {
    const bad = blocking(panes);
    // worst first: a false is a known denial, a null is merely unverified
    const lead = bad.find((p) => p.granted === false) || bad[0];

    const actions = V.ui.el("div", { class: "perm-actions" },
      V.ui.el("button", {
        type: "button", class: "btn-primary", text: `Open ${lead.label}`,
        on: { click: openPane(lead.key) },
      }));
    // Recording from the dock still works without Input Monitoring or
    // Accessibility; without the microphone nothing does, so no second path.
    if (lead.key !== "microphone") {
      actions.append(V.ui.el("button", {
        type: "button", class: "btn-secondary", text: "Record from here instead",
        // starts the dock recording: a bare .focus() shows no ring after a
        // mouse click, so the button looked like it did nothing
        // (only from idle: toggleRecord would stop a dock recording already
        // running, which is not what "Record" says)
        on: { click: () => {
          V.ui.$("#record-btn").focus();
          if (recState === "idle") toggleRecord();
        } },
      }));
    }

    const checks = V.ui.el("ul", { class: "perm-checks" },
      ...panes.map((p) => {
        const state = p.granted === true ? "ok" : p.granted === false ? "denied" : "unknown";
        const mark = state === "ok" ? "\u2713" : state === "denied" ? "!" : "?";
        const why = state === "ok" ? "granted"
          : micPending(p) ? MIC_FIRST_ASK : PERM_WHY[p.key] || "";
        return V.ui.el("li", { class: `perm-${state}-row` },
          V.ui.el("span", { class: "perm-mark", "aria-hidden": "true", text: mark }),
          V.ui.el("span", { text: p.label }),
          V.ui.el("span", { class: "perm-why", text: why }));
      }));

    return V.ui.el("div", { class: "perm-block" },
      V.ui.el("div", { class: "perm-glyph", html: V.ui.ICONS.keyboard }),
      V.ui.el("h2", { text: lead.key === "input_monitoring"
                        ? "Push-to-talk isn't working" : `${lead.label} is switched off` }),
      V.ui.el("p", { text: PERM_BREAKS[lead.key] || "" }),
      actions, checks);
  }

  async function renderPermissions() {
    const strip = V.ui.$("#perm-strip");
    const ptt = V.ui.$("#ptt-bar");
    const res = await V.ui.tryApi(V.api().get_permissions(), "Load permissions");
    // Leave whatever the strip last showed rather than wiping it: a stale
    // strip beside a toast explaining the staleness beats an empty one.
    if (!res || !res.panes) return;
    const panes = res.panes;
    // #perm-strip is aria-live, and replacing its children makes a screen
    // reader read it again: on every window focus, it re-announced a strip
    // that hadn't changed. Only re-render when a grant actually moved.
    const sig = JSON.stringify(panes.map((p) => [p.key, p.granted]));
    if (strip.dataset.sig === sig) return;
    strip.dataset.sig = sig;
    const bad = blocking(panes);

    if (bad.length === 0 && panes.some(micPending)) {
      // first run: nothing is wrong yet, the first dictation will ask
      strip.replaceChildren(V.ui.el("p", {
        class: "perm-ok",
        text: "macOS will ask for the microphone the first time you dictate — " +
              "hold the key and allow it.",
      }));
      strip.dataset.state = "pending";
      if (ptt) ptt.hidden = false;
      return;
    }

    if (bad.length === 0) {
      // a healthy app should not shout — one quiet line, not three badges
      strip.replaceChildren(V.ui.el("p", {
        class: "perm-ok",
        text: "Microphone, Input Monitoring and Accessibility all granted.",
      }));
      strip.dataset.state = "ok";
      if (ptt) ptt.hidden = false;
      return;
    }

    strip.dataset.state = "attention";
    strip.replaceChildren(permBlock(panes));
    // the status line promises a key that does not currently work
    if (ptt) ptt.hidden = bad.some((p) => p.key !== "accessibility");
  }

  // granting a permission happens in System Settings, outside the app;
  // refresh when the user comes back so the strip never shows a stale state
  window.addEventListener("focus", () => {
    if (document.getElementById("view-home").hidden) return;
    // Background refresh, so it is deliberately not awaited — but it must
    // still be caught. Bridge failures already toast inside
    // renderPermissions; this catch only covers a genuine throw, which
    // would otherwise surface as an unhandled rejection nobody sees.
    renderPermissions().catch(() => V.ui.toast("Permissions failed to refresh"));
    renderListener().catch(() => {});
  });

  async function onCopy(item) {
    if (!await V.ui.tryApi(V.api().copy_text(item.text), "Copy")) return;
    V.ui.toast("Copied to clipboard");
  }

  /* Mirrors views/history.js's onDelete exactly — the same undo contract has
     to hold on both surfaces the user can delete from. */
  async function onDelete(item) {
    if (!await V.ui.tryApi(V.api().delete_history_item(item.id), "Delete dictation")) return;
    await render();
    V.ui.offerDictationUndo({
      restore: () => V.ui.tryApi(V.api().restore_history_item(item), "Restore dictation"),
      refresh: render,
    });
  }

  function greeting() {
    const h = new Date().getHours();
    if (h < 5) return "Working late";
    if (h < 12) return "Good morning";
    if (h < 17) return "Good afternoon";
    return "Good evening";
  }

  /* The keycap and the sentence name the same key, so both are written from
     hotkey_display() — the raw config value (e.g. "alt_r") must never reach
     either one. */
  async function renderPtt() {
    const res = await V.ui.tryApi(V.api().hotkey_display(), "Load shortcut");
    if (!res) return;
    const display = res.display || "unset";
    V.ui.$("#ptt-keycap").textContent = display;
    V.ui.$("#ptt-key-name").textContent = display;
  }

  /* The dot beside the push-to-talk line reports the listener as it is,
     from get_hotkey_status().state. It used to be static HTML that pulsed
     green through a stopped listener and a dock recording alike. */
  const LISTENER_LABELS = {
    listening: "Listening for the key",
    busy: "Paused while you record here",
    off: "Not listening — see Settings",
    unknown: "Listener status unknown",
  };

  async function renderListener() {
    const res = await V.ui.tryApi(V.api().get_hotkey_status(), "Load shortcut status");
    const state = res && LISTENER_LABELS[res.state] ? res.state : "unknown";
    V.ui.$("#ptt-state").dataset.state = state;
    V.ui.$("#ptt-state-label").textContent = LISTENER_LABELS[state];
  }

  /* Each tile owns its own 14-day trend, so a chart always belongs to the
     number above it rather than floating between two unrelated ones.
     Returns bar heights as 0..1 fractions of the series maximum; an all-zero
     series stays flat on the floor rather than dividing by zero. */
  function trendBars(daily, key = "words") {
    if (!daily || !daily.length) return [];
    const max = Math.max(...daily.map((d) => d[key] || 0), 0);
    return daily.map((d) => (max === 0 ? 0 : (d[key] || 0) / max));
  }

  /* Week-over-week change across the same 14 days the bars draw. Answers
     null rather than a fake 0% when there is no prior week to compare
     against (a fresh install, or a week with nothing in it). */
  function weekDelta(daily, key = "words") {
    if (!daily || daily.length < 14) return null;
    const sum = (xs) => xs.reduce((t, d) => t + (d[key] || 0), 0);
    const prev = sum(daily.slice(-14, -7));
    const curr = sum(daily.slice(-7));
    if (prev === 0) return null;
    return Math.round(((curr - prev) / prev) * 100);
  }

  /* Bars scale from the floor (views.css) rather than resize, so a refresh
     grows them in place: the nodes are kept whenever the day count holds,
     or there would be nothing to transition from. A scaled bar ignores
     min-height, so the 2 px stub every day keeps is a floor on --h: 2 px
     of the 34 px .trend. */
  const BAR_FLOOR = 2 / 34;

  function renderTrend(sel, daily, key) {
    const node = V.ui.$(sel);
    const bars = trendBars(daily, key);
    const last = bars.length - 1;
    if (node.children.length !== bars.length) {
      node.replaceChildren(...bars.map(() => V.ui.el("span")));
    }
    bars.forEach((h, i) => {
      const bar = node.children[i];
      bar.className = `bar${h === 0 ? " zero" : ""}${i === last ? " today" : ""}`;
      bar.style.setProperty("--h", Math.max(h, BAR_FLOOR).toFixed(3));
    });
  }

  /* ---------- stats count up ----------

     A stat that changes while Home is on screen tweens from the old value
     to the new over 600 ms on --ease. Opening Home shows the numbers as
     they are, never a count from zero, and so does reduced motion. A new
     value mid-count carries on from whatever is on screen. */
  const COUNT_MS = 600;

  /* cubic-bezier(.2, .8, .2, 1), the --ease token: solve x(t) = p by
     Newton's method (x' never reaches zero on this curve), answer y(t). */
  const ease = (() => {
    const [x1, y1, x2, y2] = [0.2, 0.8, 0.2, 1];
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    return (p) => {
      let t = p;
      for (let i = 0; i < 8; i++) {
        const err = ((ax * t + bx) * t + cx) * t - p;
        if (Math.abs(err) < 1e-6) break;
        t -= err / ((3 * ax * t + 2 * bx) * t + cx);
      }
      t = Math.min(1, Math.max(0, t));
      return ((ay * t + by) * t + cy) * t;
    };
  })();

  const counters = new Map();  // node -> { value } on screen, one per count

  function countTo(node, to, format, animate) {
    const prev = counters.get(node);
    const from = prev ? prev.value : to;
    const state = { value: from };
    counters.set(node, state);
    const canTween = animate && from !== to && typeof window.requestAnimationFrame === "function"
      && !(V.lists && V.lists.reduced());
    if (!canTween) {
      state.value = to;
      node.textContent = format(to);
      return;
    }
    let start = null;
    const frame = (now) => {
      if (counters.get(node) !== state) return;  // a newer count took over
      if (start === null) start = now;
      const p = Math.min(1, (now - start) / COUNT_MS);
      state.value = p === 1 ? to : from + (to - from) * ease(p);
      node.textContent = format(state.value);
      if (p < 1) window.requestAnimationFrame(frame);
    };
    window.requestAnimationFrame(frame);
  }

  function renderStreakDots(sel, daily) {
    V.ui.$(sel).replaceChildren(...(daily || []).map((d) =>
      V.ui.el("span", { class: `dot${(d.words || 0) > 0 ? " on" : ""}` })));
  }

  function renderDelta(sel, pct) {
    const node = V.ui.$(sel);
    if (pct === null) { node.textContent = ""; delete node.dataset.dir; return; }
    node.textContent = `${pct >= 0 ? "+" : ""}${pct}% vs last week`;
    node.dataset.dir = pct >= 0 ? "up" : "down";
  }

  /* Home is four independent panels (greeting, permissions, hero shortcut,
     stats + recent list), so a failure in one degrades that panel only and
     the rest still render — `stats.total_words` on a failed get_stats used
     to throw and take the whole pane down with it, leaving a blank screen
     and no message. Each section is guarded on its own null; the try/catch
     is the backstop for anything that still manages to throw. */
  let knownIds = null;  // every history id at the last render (see below)

  /* opts.entering: Home is just being opened (main.js show()), so its
     numbers appear as they are rather than counting. */
  async function render(opts = {}) {
    const $ = V.ui.$;
    try {
      $("#greeting").textContent = greeting();
      // All four run concurrently, and all four are awaited together so a
      // throw in any of them lands in the catch below. renderPermissions and
      // renderPtt used to be fired bare, which both hid their failures and
      // left a rejection nobody was listening for; joining them to this
      // Promise.all attaches the handler in the same tick they start.
      const [stats, history] = await Promise.all([
        V.ui.tryApi(V.api().get_stats(), "Load stats"),
        V.ui.tryApi(V.api().get_history(), "Load history"),
        renderPermissions(),
        renderPtt(),
        renderListener(),
      ]);

      if (stats) {
        const animate = !opts.entering;
        countTo($("#stat-saved"), stats.time_saved_seconds || 0,
                (s) => V.ui.formatDuration(s), animate);
        countTo($("#stat-words"), stats.total_words || 0,
                (n) => Math.round(n).toLocaleString(), animate);
        $("#stat-streak").textContent = (stats.streak_days || 0).toLocaleString();

        // each tile compares and draws its own series
        renderDelta("#delta-saved", weekDelta(stats.daily, "saved_seconds"));
        renderDelta("#delta-words", weekDelta(stats.daily, "words"));
        renderTrend("#trend-saved", stats.daily, "saved_seconds");
        renderTrend("#trend-words", stats.daily, "words");
        renderStreakDots("#trend-streak", stats.daily);
      }

      if (history) {
        const recent = history.slice(0, 5);
        /* Keyed, so a refresh moves only what changed (js/lists.js). The
           whole history decides each row's story: an id still in it just
           fell out of the top five (fades under the card edge) or slid into
           it (fills from below); an id that was never seen is new or
           restored (lands with the wash); one that is gone was deleted. */
        const all = new Set(history.map((item) => item.id));
        const known = knownIds;
        knownIds = all;
        V.lists.sync($("#recent-list"), recent.map((item) => ({
          key: item.id, sig: item.id,
          build: () => V.ui.itemRow(item, { onCopy, onDelete }),
        })), {
          stillExists: (id) => all.has(id),
          wasKnown: (id) => !!known && known.has(id),
        });
        $("#recent-empty").hidden = recent.length > 0;
      }
    } catch (e) {
      V.ui.toast("Home failed to load");
    }
  }

  /* ---------- record flow ----------

     Capture is the fallback path, not the headline: the key works anywhere
     on the Mac, and this dock exists for when it can't be used (screen
     sharing, a captured modifier, testing the mic). The dock carries the
     whole state machine — elapsed time while listening, then transcribing,
     then a done state — so nothing else on Home has to move. */

  // "starting" covers the await on start_recording: a second click in that
  // window used to start a second recording and strand "Couldn't start."
  let recState = "idle"; // idle | starting | recording | processing
  let tickTimer = null;
  let doneTimer = null;
  let elapsed = 0;
  let dockMeter = null;

  /* The dock's bars follow the live mic level (js/dock-meter.js), polled
     only while this dock records. Absent in tests that don't load it. */
  function wave() {
    if (!dockMeter && V.dockMeter) {
      const el = V.ui.$("#dock-wave");
      if (el) dockMeter = V.dockMeter.create({ wave: el, readLevel: () => V.api().get_level() });
    }
    return dockMeter;
  }

  function stopTick() {
    clearInterval(tickTimer);
    tickTimer = null;
  }

  function showElapsed() {
    V.ui.$("#dock-time").textContent =
      `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  }

  /* Both timers are tracked so they can always be cleared: a stale tick or a
     stale reset must never fire against a dock that has since moved on. */
  function setDockState(state, hint) {
    const dock = V.ui.$("#dock");
    if (state) dock.dataset.state = state;
    else delete dock.dataset.state;
    V.ui.$("#record-status").textContent = hint;
    // The dock lives on Home, but the mic stays open on every view and
    // push-to-talk is dropped meanwhile, so the Home nav item carries it.
    const nav = V.ui.$("#nav-home");
    if (state === "recording" || state === "processing") nav.dataset.recording = "true";
    else delete nav.dataset.recording;
  }

  async function toggleRecord() {
    const $ = V.ui.$;
    const btn = $("#record-btn");
    const errEl = $("#record-error");

    if (recState === "processing" || recState === "starting") return;

    if (recState === "idle") {
      errEl.textContent = "";
      clearTimeout(doneTimer);
      recState = "starting";
      const res = await V.ui.tryApi(V.api().start_recording(), "Start recording");
      // tryApi owns the detail (and the throw path, which used to be able to
      // strand recState). #record-error keeps its job as the persistent
      // marker beside the button, since a 3.5s toast is easy to miss.
      if (!res) { recState = "idle"; errEl.textContent = "Couldn't start."; return; }
      recState = "recording";
      elapsed = 0;
      showElapsed();
      setDockState("recording", "Listening — click again to finish");
      if (wave()) wave().start();
      btn.setAttribute("aria-label", "Stop recording");
      stopTick();
      tickTimer = setInterval(() => { elapsed += 1; showElapsed(); }, 1000);
      renderListener().catch(() => {});
      return;
    }

    // recording -> stop
    recState = "processing";
    stopTick();
    setDockState("processing", "Transcribing on this Mac…");
    if (wave()) wave().settle();
    // Reset the button state before anything can return: a bridge throw here
    // used to leave recState stuck on "processing", which killed the record
    // button for the rest of the session.
    const res = await V.ui.tryApi(V.api().stop_recording(), "Transcribe");
    btn.setAttribute("aria-label", "Start recording");
    recState = "idle";
    renderListener().catch(() => {});

    if (wave()) wave().idle();
    if (!res) {
      setDockState(null, "Click to record here instead");
      errEl.textContent = "Transcription failed.";
      return;
    }

    // The transcript itself lands at the top of Recent activity a line below,
    // so the dock reports the outcome rather than repeating the text.
    setDockState("done", "Copied to clipboard");
    // the transcript lands in Recent activity, unless History couldn't save
    // it: then "Copied" alone was the only trace, and a wrong one
    if (res.warning) errEl.textContent = "Copied, but not saved to History.";
    doneTimer = setTimeout(() => {
      if (recState !== "idle") return;
      setDockState(null, "Click to record here instead");
      elapsed = 0;
      showElapsed();
    }, 2600);
    render();
  }

  /* The palette's "Start dictation" runs from any view; switching to Home
     first puts the dock — the only thing showing the mic is open — on
     screen. */
  async function dictateFromAnywhere() {
    V.show("home");
    await toggleRecord();
  }

  V.views = V.views || {};
  V.views.home = { render, toggleRecord, dictateFromAnywhere, renderPermissions,
                   renderListener, trendBars, weekDelta };
})(window.Voisr);
