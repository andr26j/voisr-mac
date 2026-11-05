"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* What each grant costs you when it is missing: the banner leads with the
     consequence, not the name of the setting. */
  const hotkeyBreak = (label) =>
    `Voisr can't see the key until macOS grants ${label}. ` +
    "Until then, holding it does nothing. Click to record still works.";
  const MIC_BREAK = "Nothing can be recorded until macOS grants Microphone.";
  const STALE_BREAK =
    "Voisr is listed and switched on, but macOS still blocks it. This usually " +
    "happens after an update. Reset clears the stale entry; then switch Voisr " +
    "on again in the pane that opens.";
  /* The Accessibility tap was refused, so push-to-talk runs on the Input
     Monitoring tap meanwhile; the next launch tries Accessibility again. */
  const FALLBACK_NOTE =
    "Push-to-talk keeps working through Input Monitoring meanwhile. " +
    "Relaunch Voisr once Accessibility is back on.";
  const OFF_BREAK =
    "Holding the key does nothing right now. Relaunch Voisr to start it again.";

  const HINT_PASTE = "Let go and it lands at your cursor.";
  const HINT_COPY = "Let go and it's copied to your clipboard.";

  const MIC_FIRST_ASK = "asked on your first dictation";

  const PERM_WHY = {
    microphone: "needed to hear you",
    input_monitoring: "needed for push-to-talk",
    accessibility: "needed to paste for you",
  };
  /* The pane that carries the push-to-talk key says what it is for. In active
     mode that is Accessibility, which also pastes; in listen mode it is Input
     Monitoring. A non-hotkey pane keeps its PERM_WHY text. */
  const HOTKEY_WHY = {
    input_monitoring: "needed for push-to-talk",
    accessibility: "needed for push-to-talk and pasting",
  };
  const whyText = (p) => (p.hotkey && HOTKEY_WHY[p.key]) || PERM_WHY[p.key] || "";

  /* A null mic is "not asked yet": macOS shows the prompt on the first
     recording, and Voisr isn't even listed in the Microphone pane until then.
     So it never blocks: holding the key is how the user gets asked. */
  const micPending = (p) => p.key === "microphone" && p.granted === null;
  /* Granted in System Settings, yet macOS still refuses it (the hotkey
     pane's `effective` is false): the stale grant an update leaves behind.
     Only a grant known to be on counts: an unknown or denied pane keeps its
     own wording, so unknown is never dressed up as "switched on". */
  const stale = (p) => p.effective === false && p.granted === true;
  const blocking = (panes) =>
    panes.filter((p) => stale(p) || (p.granted !== true && !micPending(p)));

  const listLabels = (panes) => {
    const names = panes.map((p) => p.label);
    return names.length < 2 ? names.join("")
      : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
  };

  /* ---------- what is true right now ----------

     Everything Home says about whether dictation works is decided here, from
     the permission panes and the listener's state:

       ready     the key is listening and every grant is in
       pending   the same, but macOS hasn't asked for the mic yet
       starting  the tap is coming up or held for a moment: undecided, not broken
       busy      a click-to-record owns the mic, so the key is paused
       unknown   a check failed: never claims Ready, never raises the alarm
       needs     the blue banner: stale, key dead (or mic off), or listener off
       paste     the key works but pasting doesn't: the dark banner stays, and
                 only the status panel asks for the grant

     A pane blocking only the paste (listen mode) leaves the key working, and so
     does the Input Monitoring fallback: neither may say holding it does nothing. */
  function readiness(panes, fallback, listener) {
    const state = listener.state;
    // Off but not stopped: the tap is coming up, hasn't started yet, or is held
    // for a moment by Settings' key capture, which leaves "active on ..." in place.
    const starting = state === "off" && /^(starting|not started|active)/.test(listener.status || "");
    if (!panes) {
      if (state === "off" && !starting) return { kind: "off", checks: null };
      return { kind: starting ? "starting" : "unknown", checks: null };
    }
    const bad = blocking(panes);
    if (bad.some(stale)) {
      return { kind: "stale", lead: bad.find(stale), fallback, panes, checks: panes };
    }
    const killers = bad.filter((p) => p.hotkey || p.key === "microphone");
    if (killers.length) {
      const lead = killers.find((p) => p.granted === false) || killers[0];
      return { kind: lead.key === "microphone" ? "mic" : "key", lead, panes, checks: panes };
    }
    if (state === "off" && !starting) return { kind: "off", panes, checks: panes };
    if (bad.length) return { kind: "paste", lead: bad[0], panes, checks: panes };
    if (starting) return { kind: "starting", checks: null };
    if (state === "busy") return { kind: "busy", checks: null };
    if (state !== "listening") return { kind: "unknown", checks: null };
    if (panes.some(micPending)) return { kind: "pending", checks: null };
    return { kind: "ready", panes, checks: null };
  }

  const NEEDS_BANNER = new Set(["stale", "key", "mic", "off"]);
  const NEEDS_YOU = new Set([...NEEDS_BANNER, "paste"]);
  // #perm-strip's data-state, which is what the strip is announcing
  const stripState = (kind) => (NEEDS_YOU.has(kind) ? "attention" : kind === "pending" ? "pending" : "ok");

  function statusFor(r, listener) {
    switch (r.kind) {
      case "ready": return { label: "Ready", detail: `${listLabels(r.panes)} granted.` };
      case "pending": return { label: "Almost ready",
        detail: "macOS will ask for the microphone the first time you dictate." };
      case "starting": return { label: "Starting…", detail: "The key will work in a moment." };
      case "busy": return { label: "Recording here", detail: "The key is paused until you finish." };
      case "unknown": return { label: "Status unknown", detail: "Couldn't check the key." };
      case "stale": return { label: "Needs you", dot: true,
        detail: r.fallback ? "The key still works through Input Monitoring."
          : r.lead.hotkey ? `Holding the key does nothing until ${r.lead.label} is back.`
          : `Dictations are copied, not pasted, until ${r.lead.label} is back.` };
      case "key": return { label: "Needs you", dot: true,
        detail: `Holding the key does nothing until ${r.lead.label} is back.` };
      case "mic": return { label: "Needs you", dot: true, detail: "Nothing records until Microphone is back." };
      case "paste": return { label: "Needs you", dot: true, action: r.lead,
        detail: r.lead.granted === false
          ? `${r.lead.label} is off, so dictations are copied rather than pasted.`
          : `macOS won't say whether ${r.lead.label} is on, so dictations may be copied rather than pasted.` };
      default: {  // off
        const said = (listener.status || "").trim();
        return { label: "Needs you", dot: true,
                 detail: said ? `${said[0].toUpperCase()}${said.slice(1)}.`.replace(/\.\.$/, ".")
                   : "Push-to-talk isn't running." };
      }
    }
  }

  function openPane(key) {
    return async () => {
      // open_settings_pane answers {"ok": bool}: a false is macOS refusing
      // to open the pane, which used to vanish silently and leave the user
      // clicking a control that appeared to do nothing.
      const res = await V.ui.tryApi(
        V.api().open_settings_pane(key), "Open System Settings");
      if (res && res.ok === false) V.ui.toast("Couldn't open System Settings");
    };
  }

  /* Reset clears Voisr's own TCC entry and opens the pane to grant it again;
     Relaunch quits and reopens, for a grant macOS only reads at launch. */
  function resetButton(pane, cls) {
    return V.ui.el("button", { type: "button", class: cls,
      text: `Reset ${pane.label}`, on: { click: async () => {
        const res = await V.ui.tryApi(V.api().reset_permission(pane.key),
                                      `Reset ${pane.label}`);
        if (res && res.ok) V.ui.toast(`Reset. Switch Voisr on under ${pane.label}`);
        else if (res && res.ok === false) V.ui.toast(`Couldn't reset ${pane.label}`);
      } } });
  }

  function relaunchButton(cls) {
    return V.ui.el("button", { type: "button", class: cls,
      text: "Relaunch Voisr", on: { click: async () => {
        const res = await V.ui.tryApi(V.api().relaunch_app(), "Relaunch");
        if (res && res.ok === false) V.ui.toast(res.error || "Couldn't relaunch");
      } } });
  }

  /* Recording here still works without Input Monitoring or Accessibility;
     without the microphone nothing does, so no second path. Only from idle:
     toggleRecord would stop a recording already running, which is not what
     "Click to record" says. */
  function recordButton(panes) {
    if ((panes || []).some((p) => p.key === "microphone" && p.granted === false)) return null;
    return V.ui.el("button", { type: "button", class: "btn-secondary", text: "Click to record",
      on: { click: () => { if (recState === "idle") toggleRecord(); } } });
  }

  /* The blue banner. A dead key is not a footnote: without its grant the whole
     product does nothing when you hold it, and a banner still saying "hold this
     key to dictate" would be a lie. */
  function needsBanner(r) {
    const el = V.ui.el;
    const lead = r.lead;
    let heading, body, recover = null;
    let actions;
    if (r.kind === "stale") {
      heading = `macOS is still blocking ${lead.label}`;
      body = r.fallback && lead.key === "accessibility" ? `${STALE_BREAK} ${FALLBACK_NOTE}` : STALE_BREAK;
      actions = [resetButton(lead, "btn-signal"),
                 el("button", { type: "button", class: "btn-secondary", text: "Open System Settings",
                                on: { click: openPane(lead.key) } }),
                 recordButton(r.panes)];
      recover = el("p", { class: "banner-recover" },
        el("span", { text: r.fallback ? `${lead.label} back on?` : "Still blocked?" }),
        relaunchButton("text-btn"));
    } else if (r.kind === "off") {
      heading = "The key isn't listening";
      body = OFF_BREAK;
      actions = [relaunchButton("btn-signal"), recordButton(r.panes)];
    } else if (r.kind === "mic") {
      heading = "Voisr can't hear you";
      body = MIC_BREAK;
      actions = [el("button", { type: "button", class: "btn-signal", text: "Open Microphone",
                                on: { click: openPane(lead.key) } })];
    } else {
      heading = "Push-to-talk isn't working";
      body = hotkeyBreak(lead.label);
      actions = [el("button", { type: "button", class: "btn-signal", text: `Open ${lead.label}`,
                                on: { click: openPane(lead.key) } }),
                 recordButton(r.panes)];
      // A denied hotkey grant can still be the stale one: listen mode's
      // preflight reads false while the switch shows ON, and "Open" alone
      // sends the user to a pane that already looks right. Only a known
      // denial of the hotkey's own pane: never unknown, never another pane.
      if (lead.granted === false) {
        recover = el("p", { class: "banner-recover" },
          el("span", { text: "Already switched on?" }),
          resetButton(lead, "text-btn"), relaunchButton("text-btn"));
      }
    }
    return el("section", { class: "banner banner-needs-you", "aria-label": "Permission problem" },
      el("h2", { class: "banner-title", text: heading }),
      el("p", { class: "banner-sub", text: body }),
      el("div", { class: "banner-actions" }, actions),
      recover);
  }

  /* Every pane stays listed, so one trip to System Settings can clear them all. */
  function permChecks(panes) {
    const el = V.ui.el;
    return el("ul", { class: "perm-checks" }, panes.map((p) => {
      const state = stale(p) ? "denied"
        : p.granted === true ? "ok" : p.granted === false ? "denied" : "unknown";
      const mark = state === "ok" ? "✓" : state === "denied" ? "!" : "?";
      const why = stale(p) ? "switched on, not working"
        : state === "ok" ? "granted"
        : micPending(p) ? MIC_FIRST_ASK : whyText(p);
      return el("li", { class: `perm-${state}-row` },
        el("span", { class: "perm-mark", "aria-hidden": "true", text: mark }),
        el("span", { class: "perm-name", text: p.label }),
        el("span", { class: "perm-why", text: why }));
    }));
  }

  /* The last answers from the bridge. The panes survive a failed read: a stale
     banner beside a toast explaining the staleness beats an empty one. */
  let lastPanes = null, lastFallback = false, permsRead = false;
  let listener = { state: "unknown", status: "" }, listenerRead = false;
  let pasteOff = false;

  let permPoll = null;
  // A tick's bridge calls can outlast the 1 s interval on a slow bridge;
  // overlapping ticks would stack calls. Skip a tick until the last settles.
  let pollInFlight = false;

  /* A bridge call that, when `quiet`, swallows its failure instead of
     toasting it: the 1 s poll would otherwise toast twice a second for as
     long as the bridge stayed broken. User-initiated renders keep toasting. */
  async function callApi(call, label, quiet) {
    if (!quiet) return V.ui.tryApi(call(), label);
    try {
      const res = await call();
      return res && res.error ? null : res;
    } catch (e) {
      return null;
    }
  }

  /* Granting happens in System Settings and the tap can come up late, so while
     anything is wrong Home re-checks itself every second. Nothing runs while
     Home or the window is hidden: this app stays resident. */
  function syncPoll(kind) {
    const wanted = NEEDS_YOU.has(kind) || kind === "starting";
    if (wanted && permPoll === null) {
      permPoll = setInterval(() => {
        if (document.getElementById("view-home").hidden) return;
        if (document.visibilityState === "hidden") return;
        if (pollInFlight) return;
        pollInFlight = true;
        Promise.allSettled([
          renderPermissions({ quiet: true }),
          renderListener({ quiet: true }),
        ]).then(() => { pollInFlight = false; });
      }, 1000);
    } else if (!wanted && permPoll !== null) {
      clearInterval(permPoll);
      permPoll = null;
    }
  }

  /* Both are aria-live, and replacing their children makes a screen reader read
     them again: on every window focus, it re-announced what hadn't changed. So
     each re-renders only when what it says actually moved. */
  function renderStatus(r) {
    const el = V.ui.el;
    const box = V.ui.$("#home-status");
    const status = statusFor(r, listener);
    const checks = r.checks ? r.checks.map((p) => [p.key, p.granted, p.effective, p.hotkey]) : null;
    const sig = JSON.stringify([status.label, status.detail, status.action && status.action.key, checks]);
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig;
    box.dataset.state = r.kind;
    const parts = [
      el("span", { class: "status-label" },
        status.dot ? el("span", { class: "status-dot", "aria-hidden": "true" }) : null,
        el("span", { text: status.label })),
      el("span", { class: "status-detail", text: status.detail }),
    ];
    if (status.action) {
      parts.push(el("button", { type: "button", class: "btn-secondary status-action",
                                text: `Open ${status.action.label}`,
                                on: { click: openPane(status.action.key) } }));
    }
    if (r.checks) parts.push(permChecks(r.checks));
    box.replaceChildren(...parts);
  }

  /* The dark banner carries a recording in progress, even from under a blue one:
     "Click to record" there has to show the mic is open. */
  function syncBanners() {
    const strip = V.ui.$("#perm-strip");
    const hero = V.ui.$("#home-hero");
    const showBlue = strip.children.length > 0 && !recordingShown();
    strip.hidden = !showBlue;
    hero.hidden = showBlue;
  }

  function apply() {
    // until the permissions have answered once, a stopped listener isn't news:
    // it stops for a missing grant too, and that gets a better banner
    const r = !permsRead ? { kind: "unknown", checks: null }
      : readiness(lastPanes, lastFallback, listener);
    const strip = V.ui.$("#perm-strip");
    strip.dataset.state = stripState(r.kind);
    const blue = NEEDS_BANNER.has(r.kind);
    const sig = blue
      ? JSON.stringify([r.kind, lastFallback, (r.panes || []).map((p) => [p.key, p.granted, p.effective])])
      : "";
    if (strip.dataset.sig !== sig) {
      strip.dataset.sig = sig;
      strip.replaceChildren(...(blue ? [needsBanner(r)] : []));
    }
    syncBanners();
    // both answers are in, or it would flash "Status unknown" as Home opens
    if ((permsRead && listenerRead) || NEEDS_YOU.has(r.kind)) renderStatus(r);
    const copies = pasteOff || (r.kind === "paste" && r.lead.granted === false);
    V.ui.$("#home-hint").textContent = copies ? HINT_COPY : HINT_PASTE;
    syncPoll(r.kind);
  }

  async function renderPermissions(opts = {}) {
    const res = await callApi(() => V.api().get_permissions(), "Load permissions", opts.quiet);
    if (res && res.panes) {
      lastPanes = res.panes;
      lastFallback = res.fallback === true;
    }
    permsRead = true;
    apply();
  }

  /* Reports the listener as it is, from get_hotkey_status(). */
  async function renderListener(opts = {}) {
    const res = await callApi(() => V.api().get_hotkey_status(), "Load shortcut status", opts.quiet);
    const known = res && ["listening", "busy", "off"].includes(res.state);
    listener = known ? { state: res.state, status: res.status || "" } : { state: "unknown", status: "" };
    listenerRead = true;
    apply();
  }

  // granting a permission happens in System Settings, outside the app;
  // refresh when the user comes back so Home never shows a stale state
  window.addEventListener("focus", () => {
    if (document.getElementById("view-home").hidden) return;
    // Background refresh, so it is deliberately not awaited, but it must
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

  /* Mirrors views/history.js's onDelete exactly: the same undo contract has
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

  /* The banner names the key from hotkey_display(), so the raw config value
     (e.g. "alt_r") never reaches it. The label's symbol stays out of a headline:
     "Right Option (⌥)" reads "Right Option". */
  async function renderPtt() {
    const res = await V.ui.tryApi(V.api().hotkey_display(), "Load shortcut");
    if (!res) return;
    const shown = (res.display || "").replace(/\s*\([^)]*\)\s*$/, "");
    V.ui.$("#ptt-key-name").textContent = shown && shown !== "unset" ? shown : "your key";
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

  /* "41.3K": the panel's numbers are for glancing at; the exact count is on hover. */
  function compactCount(value) {
    const n = Math.round(value);
    if (n < 1000) return n.toLocaleString();
    const short = (x) => (Math.round(x * 10) / 10).toString();
    if (n < 999950) return `${short(n / 1000)}K`;
    return `${short(n / 1000000)}M`;
  }

  const savedText = (s) => (s >= 1 ? V.ui.formatDuration(s) : "0m");

  /* Home is independent panels (greeting, banner, stats, status, feed), so a
     failure in one degrades that panel only and the rest still render:
     `stats.total_words` on a failed get_stats used to throw and take the whole
     pane down with it, leaving a blank screen and no message. Each section is
     guarded on its own null; the try/catch is the backstop for anything that
     still manages to throw. */
  let knownIds = null;  // every history id at the last render (see below)

  /* opts.entering: Home is just being opened (main.js show()), so its
     numbers appear as they are rather than counting. */
  async function render(opts = {}) {
    const $ = V.ui.$;
    try {
      $("#greeting").textContent = greeting();
      // All run concurrently, and all are awaited together so a throw in any of
      // them lands in the catch below.
      const [stats, history, cfg] = await Promise.all([
        V.ui.tryApi(V.api().get_stats(), "Load stats"),
        V.ui.tryApi(V.api().get_history(), "Load history"),
        V.ui.tryApi(V.api().get_config(), "Load settings"),
        renderPermissions(),
        renderPtt(),
        renderListener(),
      ]);

      if (cfg) {
        pasteOff = cfg.paste === false;
        apply();
      }

      if (stats) {
        const animate = !opts.entering;
        countTo($("#stat-saved"), stats.time_saved_seconds || 0, savedText, animate);
        const words = stats.total_words || 0;
        countTo($("#stat-words"), words, compactCount, animate);
        $("#stat-words").title = `${words.toLocaleString()} words`;
        $("#stat-streak").textContent = (stats.streak_days || 0).toLocaleString();
      }

      if (history) renderFeed(history);
    } catch (e) {
      V.ui.toast("Home failed to load");
    }
  }

  /* Keyed by day and id, so a refresh moves only what changed (js/lists.js). The
     whole history decides each row's story: an id still in it just fell out of
     the top five (fades under the card edge) or slid into it (fills from below);
     an id that was never seen is new or restored (lands with the wash); one that
     is gone was deleted. */
  function renderFeed(history) {
    const $ = V.ui.$;
    const recent = history.slice(0, 5);
    const days = new Map();
    for (const item of recent) {
      const day = V.ui.dayOf(item.ts);
      if (!days.has(day)) days.set(day, []);
      days.get(day).push(item);
    }
    const all = new Set(history.map((item) => item.id));
    const known = knownIds;
    knownIds = all;
    V.lists.syncGroups($("#recent-groups"), [...days].map(([day, items]) => ({
      key: day,
      build: () => V.ui.dayGroup(day),
      rows: items.map((item) => ({
        key: item.id, sig: item.id,
        build: () => V.ui.itemRow(item, { onCopy, onDelete }),
      })),
    })), {
      animate: true,
      stillExists: (id) => all.has(id),
      wasKnown: (id) => !!known && known.has(id),
    // Not awaited, so the stats never wait on a row's exit. An animated
    // update can fail after render's try/catch has returned: catch it here.
    }).catch(() => V.ui.toast("Recent dictations failed to update"));
    $("#recent-empty").hidden = recent.length > 0;
    $("#home-view-all").hidden = recent.length === 0;
  }

  /* ---------- record flow ----------

     Capture is the fallback path, not the headline: the key works anywhere on
     the Mac, and this exists for when it can't be used (screen sharing, a
     captured modifier, testing the mic). The banner carries the whole state
     machine: elapsed time while listening, then transcribing, then a done
     state, so nothing else on Home has to move. It never pastes; the text goes
     to the clipboard, and the banner says so. */

  // "starting" covers the await on start_recording: a second click in that
  // window used to start a second recording and strand "Couldn't start."
  let recState = "idle"; // idle | starting | recording | processing
  let shownState = "";   // what the banner shows, which outlasts recState by "done"
  let tickTimer = null;
  let doneTimer = null;
  let elapsed = 0;
  let meter = null;

  const LISTENING_SPEED = 3.2;

  const recordingShown = () => shownState === "recording" || shownState === "processing"
    || shownState === "done";

  /* The wave follows the live mic (js/wave-meter.js), read only while this
     records. Absent in tests that don't load it. */
  function waveMeter() {
    if (!meter && V.waveMeter) {
      const el = V.ui.$("#home-wave");
      if (el) meter = V.waveMeter.create({ el, readLevel: () => V.api().get_level() });
    }
    return meter;
  }

  function waveSpeed(speed) {
    const el = V.ui.$("#home-wave");
    if (V.wave && el) V.wave.setSpeed(el, speed);
  }

  function stopTick() {
    clearInterval(tickTimer);
    tickTimer = null;
  }

  function showElapsed() {
    V.ui.$("#record-time").textContent =
      `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  }

  const LIVE_COPY = {
    recording: ["Listening", "Click to finish. It's copied to your clipboard, not pasted."],
    processing: ["Transcribing", "On this Mac."],
    done: ["Copied to clipboard", "Paste it wherever you need it."],
  };

  /* Both timers are tracked so they can always be cleared: a stale tick or a
     stale reset must never fire against a banner that has since moved on. */
  function setRecordState(state) {
    const $ = V.ui.$;
    shownState = state;
    const hero = $("#home-hero");
    if (state) hero.dataset.state = state;
    else delete hero.dataset.state;
    const copy = LIVE_COPY[state];
    $("#record-title").textContent = copy ? copy[0] : "";
    $("#record-status").textContent = copy ? copy[1] : "";
    $("#record-label").textContent = state === "recording" ? "Finish" : "Click to record";
    const btn = $("#record-btn");
    if (state === "processing") btn.setAttribute("aria-disabled", "true");
    else btn.removeAttribute("aria-disabled");
    // The mic stays open on every view and push-to-talk is dropped meanwhile,
    // so the Home nav item carries it.
    const nav = $("#nav-home");
    if (state === "recording" || state === "processing") nav.dataset.recording = "true";
    else delete nav.dataset.recording;
    syncBanners();
  }

  async function toggleRecord() {
    const $ = V.ui.$;
    const errEl = $("#record-error");

    if (recState === "processing" || recState === "starting") return;

    if (recState === "idle") {
      errEl.textContent = "";
      clearTimeout(doneTimer);
      recState = "starting";
      const res = await V.ui.tryApi(V.api().start_recording(), "Start recording");
      // tryApi owns the detail (and the throw path, which used to be able to
      // strand recState). #record-error keeps its job as the persistent
      // marker in the banner, since a 3.5s toast is easy to miss.
      if (!res) { recState = "idle"; setRecordState(""); errEl.textContent = "Couldn't start."; return; }
      recState = "recording";
      elapsed = 0;
      showElapsed();
      setRecordState("recording");
      waveSpeed(LISTENING_SPEED);
      if (waveMeter()) waveMeter().start();
      stopTick();
      tickTimer = setInterval(() => { elapsed += 1; showElapsed(); }, 1000);
      renderListener().catch(() => {});
      return;
    }

    // recording -> stop
    recState = "processing";
    stopTick();
    setRecordState("processing");
    waveSpeed(1);
    if (waveMeter()) waveMeter().settle();
    // Reset the state before anything can return: a bridge throw here used to
    // leave recState stuck on "processing", which killed the button for the
    // rest of the session.
    const res = await V.ui.tryApi(V.api().stop_recording(), "Transcribe");
    recState = "idle";
    renderListener().catch(() => {});

    if (waveMeter()) waveMeter().idle();
    if (!res) {
      setRecordState("");
      errEl.textContent = "Transcription failed.";
      return;
    }

    // Rare, and "Copied" would send them off to paste whatever was there before.
    if (res.copied === false) {
      setRecordState("");
      errEl.textContent = "Couldn't copy it. Copy it from the list below.";
      render();
      return;
    }

    // The transcript itself lands at the top of the feed a line below, so the
    // banner reports the outcome rather than repeating the text.
    setRecordState("done");
    // unless History couldn't save it: then "Copied" alone would be the only
    // trace, and a wrong one
    if (res.warning) errEl.textContent = "Copied, but not saved to History.";
    doneTimer = setTimeout(() => {
      if (recState !== "idle") return;
      setRecordState("");
      elapsed = 0;
      showElapsed();
    }, 2600);
    render();
  }

  /* The palette's "Start dictation" runs from any view; switching to Home
     first puts the banner, the only thing showing the mic is open, on screen. */
  async function dictateFromAnywhere() {
    V.show("home");
    await toggleRecord();
  }

  function init() {
    V.ui.$("#change-key").addEventListener("click", () => V.show("settings", { section: "key" }));
  }

  /* First run asks for the same grants, and must read them the same way. */
  V.permissions = { stale, micPending, openPane, resetButton, relaunchButton };

  V.views = V.views || {};
  V.views.home = { render, init, toggleRecord, dictateFromAnywhere, renderPermissions,
                   renderListener, compactCount };
})(window.Voisr);
