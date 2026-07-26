"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const PERM_HELP = {
    microphone: "Voisr needs the microphone to hear you.",
    input_monitoring: "Without this, the push-to-talk key does nothing.",
    accessibility: "Without this, text is copied but not pasted for you.",
  };

  async function renderPermissions() {
    const strip = V.ui.$("#perm-strip");
    const res = await V.ui.tryApi(V.api().get_permissions(), "Load permissions");
    // Leave whatever the strip last showed rather than wiping it: a stale
    // strip beside a toast explaining the staleness beats an empty one.
    if (!res || !res.panes) return;
    const panes = res.panes;
    const bad = panes.filter((p) => p.granted !== true);

    if (bad.length === 0) {
      // a healthy app should not shout — one quiet line, not three badges
      strip.replaceChildren(V.ui.el("p", {
        class: "perm-ok",
        text: "Microphone, Input Monitoring and Accessibility all granted.",
      }));
      strip.dataset.state = "ok";
      return;
    }

    strip.dataset.state = "attention";
    strip.replaceChildren(...panes.map((p) => {
      const state = p.granted === true ? "ok"
                  : p.granted === false ? "denied" : "unknown";
      const chip = V.ui.el("button", {
        class: `perm-chip perm-${state}`,
        type: "button",
        title: PERM_HELP[p.key] || "",
        data: { key: p.key },
        // open_settings_pane answers {"ok": bool} — a false is macOS
        // refusing to open the pane, which used to vanish silently and
        // leave the user clicking a chip that appeared to do nothing.
        on: { click: async () => {
          const res = await V.ui.tryApi(
            V.api().open_settings_pane(p.key), "Open System Settings");
          if (res && res.ok === false) V.ui.toast("Couldn't open System Settings");
        } },
      }, V.ui.el("span", { class: "perm-dot", "aria-hidden": "true" }),
         V.ui.el("span", { text: p.label }));
      if (state === "ok") chip.disabled = true;
      return chip;
    }));
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

  async function renderHero() {
    const res = await V.ui.tryApi(V.api().hotkey_display(), "Load shortcut");
    if (!res) return;
    V.ui.$("#hero-keycap").textContent = res.display || "unset";
  }

  /* 14 daily word counts -> a polyline across a 240x40 viewBox. A flat run
     (including all-zero) draws along the baseline rather than dividing by 0. */
  function sparklinePath(daily) {
    if (!daily || daily.length < 2) return "";
    const max = Math.max(...daily.map((d) => d.words), 0);
    const stepX = 240 / (daily.length - 1);
    return daily.map((d, i) => {
      const x = (i * stepX).toFixed(1);
      const y = (max === 0 ? 38 : 38 - (d.words / max) * 34).toFixed(1);
      return `${i === 0 ? "M" : "L"}${x} ${y}`;
    }).join(" ");
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
      // renderHero used to be fired bare, which both hid their failures and
      // left a rejection nobody was listening for; joining them to this
      // Promise.all attaches the handler in the same tick they start.
      const [stats, history] = await Promise.all([
        V.ui.tryApi(V.api().get_stats(), "Load stats"),
        V.ui.tryApi(V.api().get_history(), "Load history"),
        renderPermissions(),
        renderHero(),
      ]);

      if (stats) {
        $("#stat-saved").textContent = V.ui.formatDuration(stats.time_saved_seconds);
        $("#stat-words").textContent = (stats.total_words || 0).toLocaleString();
        $("#stat-streak").textContent = (stats.streak_days || 0).toLocaleString();
        $("#spark-path").setAttribute("d", sparklinePath(stats.daily));
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

  /* ---------- record flow ---------- */

  let recState = "idle"; // idle | recording | processing

  async function toggleRecord() {
    const $ = V.ui.$;
    const btn = $("#record-btn");
    const status = $("#record-status");
    const errEl = $("#record-error");
    const wave = $("#hero-wave");

    if (recState === "processing") return;

    if (recState === "idle") {
      errEl.textContent = "";
      $("#record-result").hidden = true;
      const res = await V.ui.tryApi(V.api().start_recording(), "Start recording");
      // tryApi owns the detail (and the throw path, which used to be able to
      // strand recState). #record-error keeps its job as the persistent
      // marker beside the button, since a 3.5s toast is easy to miss.
      if (!res) { errEl.textContent = "Couldn't start recording."; return; }
      recState = "recording";
      wave.dataset.state = "recording";
      btn.setAttribute("aria-label", "Stop recording");
      status.textContent = "Listening… click again to finish";
      return;
    }

    // recording -> stop
    recState = "processing";
    wave.dataset.state = "processing";
    status.textContent = "Transcribing on this Mac…";
    // Reset the button state before anything can return: a bridge throw here
    // used to leave recState stuck on "processing", which killed the record
    // button for the rest of the session.
    const res = await V.ui.tryApi(V.api().stop_recording(), "Transcribe");
    delete wave.dataset.state;
    btn.setAttribute("aria-label", "Start recording");
    recState = "idle";
    status.textContent = "Ready";

    if (!res) { errEl.textContent = "Transcription failed."; return; }
    $("#result-text").textContent = res.text;
    $("#record-result").hidden = false;
    render();
  }

  V.views = V.views || {};
  V.views.home = { render, toggleRecord, renderPermissions, sparklinePath };
})(window.Voisr);
