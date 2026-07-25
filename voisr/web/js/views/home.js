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
    const { panes } = await V.api().get_permissions();
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
        on: { click: () => V.api().open_settings_pane(p.key) },
      }, V.ui.el("span", { class: "perm-dot", "aria-hidden": "true" }),
         V.ui.el("span", { text: p.label }));
      if (state === "ok") chip.disabled = true;
      return chip;
    }));
  }

  // granting a permission happens in System Settings, outside the app;
  // refresh when the user comes back so the strip never shows a stale state
  window.addEventListener("focus", () => {
    if (!document.getElementById("view-home").hidden) renderPermissions();
  });

  async function onCopy(item) {
    await V.api().copy_text(item.text);
    V.ui.toast("Copied to clipboard");
  }

  async function onDelete(item) {
    await V.api().delete_history_item(item.id);
    await render();
    V.ui.toast("Dictation deleted");
  }

  function greeting() {
    const h = new Date().getHours();
    if (h < 5) return "Working late";
    if (h < 12) return "Good morning";
    if (h < 17) return "Good afternoon";
    return "Good evening";
  }

  async function renderHero() {
    const { display } = await V.api().hotkey_display();
    V.ui.$("#hero-keycap").textContent = display || "unset";
  }

  async function render() {
    const $ = V.ui.$;
    $("#greeting").textContent = greeting();
    renderPermissions();
    renderHero();
    const [stats, history] = await Promise.all([V.api().get_stats(), V.api().get_history()]);
    $("#stat-words").textContent = stats.total_words.toLocaleString();
    $("#stat-wpm").textContent = stats.avg_wpm.toLocaleString();
    $("#stat-streak").textContent = stats.streak_days.toLocaleString();

    const recent = history.slice(0, 5);
    $("#recent-list").replaceChildren(...recent.map((item) => V.ui.itemRow(item, { onCopy, onDelete })));
    $("#recent-empty").hidden = recent.length > 0;
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
      const res = await V.api().start_recording();
      if (res.error) { errEl.textContent = res.error; return; }
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
    const res = await V.api().stop_recording();
    delete wave.dataset.state;
    btn.setAttribute("aria-label", "Start recording");
    recState = "idle";

    if (res.error) {
      status.textContent = "Click to start dictating";
      $("#record-error").textContent = res.error;
      return;
    }
    status.textContent = "Click to start dictating";
    $("#result-text").textContent = res.text;
    $("#record-result").hidden = false;
    render();
  }

  V.views = V.views || {};
  V.views.home = { render, toggleRecord, renderPermissions, renderHero };
})(window.Voisr);
