"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const PERM_HELP = {
    microphone: "Voisr needs the microphone to hear you.",
    input_monitoring: "Without this, the push-to-talk key does nothing.",
    accessibility: "Without this, text is copied but not pasted for you.",
  };

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
        on: { click: () => V.ui.$("#record-btn").focus() },
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

  /* Each tile owns its own 14-day trend, so a chart always belongs to the
     number above it rather than floating between two unrelated ones.
     Returns bar heights as 0..1 fractions of the series maximum; an all-zero
     series stays flat on the floor rather than dividing by zero. */
  function trendBars(daily) {
    if (!daily || !daily.length) return [];
    const max = Math.max(...daily.map((d) => d.words || 0), 0);
    return daily.map((d) => (max === 0 ? 0 : (d.words || 0) / max));
  }

  /* Week-over-week change across the same 14 days the bars draw. Answers
     null rather than a fake 0% when there is no prior week to compare
     against (a fresh install, or a week with nothing in it). */
  function weekDelta(daily) {
    if (!daily || daily.length < 14) return null;
    const sum = (xs) => xs.reduce((t, d) => t + (d.words || 0), 0);
    const prev = sum(daily.slice(-14, -7));
    const curr = sum(daily.slice(-7));
    if (prev === 0) return null;
    return Math.round(((curr - prev) / prev) * 100);
  }

  function renderTrend(sel, daily) {
    const node = V.ui.$(sel);
    const bars = trendBars(daily);
    const last = bars.length - 1;
    node.replaceChildren(...bars.map((h, i) => {
      const bar = V.ui.el("span", {
        class: `bar${h === 0 ? " zero" : ""}${i === last ? " today" : ""}`,
      });
      bar.style.setProperty("--h", h.toFixed(3));
      return bar;
    }));
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
  async function render() {
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
      ]);

      if (stats) {
        $("#stat-saved").textContent = V.ui.formatDuration(stats.time_saved_seconds);
        $("#stat-words").textContent = (stats.total_words || 0).toLocaleString();
        $("#stat-streak").textContent = (stats.streak_days || 0).toLocaleString();

        const pct = weekDelta(stats.daily);
        renderDelta("#delta-saved", pct);
        renderDelta("#delta-words", pct);
        renderTrend("#trend-saved", stats.daily);
        renderTrend("#trend-words", stats.daily);
        renderStreakDots("#trend-streak", stats.daily);
      }

      if (history) {
        const recent = history.slice(0, 5);
        $("#recent-list").replaceChildren(
          ...recent.map((item) => V.ui.itemRow(item, { onCopy, onDelete })));
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

  let recState = "idle"; // idle | recording | processing
  let tickTimer = null;
  let doneTimer = null;
  let elapsed = 0;

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
  }

  async function toggleRecord() {
    const $ = V.ui.$;
    const btn = $("#record-btn");
    const errEl = $("#record-error");

    if (recState === "processing") return;

    if (recState === "idle") {
      errEl.textContent = "";
      clearTimeout(doneTimer);
      const res = await V.ui.tryApi(V.api().start_recording(), "Start recording");
      // tryApi owns the detail (and the throw path, which used to be able to
      // strand recState). #record-error keeps its job as the persistent
      // marker beside the button, since a 3.5s toast is easy to miss.
      if (!res) { errEl.textContent = "Couldn't start."; return; }
      recState = "recording";
      elapsed = 0;
      showElapsed();
      setDockState("recording", "Listening — click again to finish");
      btn.setAttribute("aria-label", "Stop recording");
      stopTick();
      tickTimer = setInterval(() => { elapsed += 1; showElapsed(); }, 1000);
      return;
    }

    // recording -> stop
    recState = "processing";
    stopTick();
    setDockState("processing", "Transcribing on this Mac…");
    // Reset the button state before anything can return: a bridge throw here
    // used to leave recState stuck on "processing", which killed the record
    // button for the rest of the session.
    const res = await V.ui.tryApi(V.api().stop_recording(), "Transcribe");
    btn.setAttribute("aria-label", "Start recording");
    recState = "idle";

    if (!res) {
      setDockState(null, "Click to record here instead");
      errEl.textContent = "Transcription failed.";
      return;
    }

    // The transcript itself lands at the top of Recent activity a line below,
    // so the dock reports the outcome rather than repeating the text.
    setDockState("done", "Copied to clipboard");
    doneTimer = setTimeout(() => {
      if (recState !== "idle") return;
      setDockState(null, "Click to record here instead");
      elapsed = 0;
      showElapsed();
    }, 2600);
    render();
  }

  V.views = V.views || {};
  V.views.home = { render, toggleRecord, renderPermissions, trendBars, weekDelta };
})(window.Voisr);
