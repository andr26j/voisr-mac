"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
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

  async function render() {
    const $ = V.ui.$;
    $("#greeting").textContent = greeting();
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

    if (recState === "processing") return;

    if (recState === "idle") {
      errEl.textContent = "";
      $("#record-result").hidden = true;
      const res = await V.api().start_recording();
      if (res.error) { errEl.textContent = res.error; return; }
      recState = "recording";
      btn.classList.add("recording");
      btn.setAttribute("aria-label", "Stop recording");
      status.textContent = "Listening… click again to finish";
      return;
    }

    // recording -> stop
    recState = "processing";
    btn.classList.remove("recording");
    btn.classList.add("processing");
    status.textContent = "Transcribing on this Mac…";
    const res = await V.api().stop_recording();
    btn.classList.remove("processing");
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
  V.views.home = { render, toggleRecord };
})(window.Voisr);
