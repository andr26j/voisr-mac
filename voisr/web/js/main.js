"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* ---------- navigation ---------- */

  function show(view) {
    document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== `view-${view}`; });
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    if (view === "home") V.views.home.render();
    if (view === "history") V.views.history.render();
    if (view === "dictionary") V.views.dictionary.render();
    if (view === "settings") V.views.settings.render();
  }

  /* ---------- init ---------- */

  function init() {
    const $ = V.ui.el;
    document.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => show(b.dataset.view)));
    $("#record-btn").addEventListener("click", V.views.home.toggleRecord);
    $("#dict-form").addEventListener("submit", V.views.dictionary.addEntry);
    $("#paste-toggle").addEventListener("change", async (e) => {
      await V.api().set_config({ paste: e.target.checked });
      V.ui.toast(e.target.checked ? "Auto-paste enabled" : "Auto-paste disabled");
    });
    $("#widget-toggle").addEventListener("change", async (e) => {
      await V.api().set_config({ show_widget: e.target.checked });
      V.ui.toast(e.target.checked ? "Recording widget on" : "Recording widget off");
    });
    $("#theme-grid").addEventListener("click", async (e) => {
      const btn = e.target.closest(".theme-opt");
      if (!btn) return;
      await V.api().set_config({ pill_theme: btn.dataset.value });
      V.ui.toast(`Pill theme: ${btn.querySelector(".theme-name").textContent}`);
      V.views.settings.renderThemes();
      V.views.settings.renderColorWheel();
    });
    // pushed from Python after each dictation (hotkey ones land while the
    // dashboard is open) — refresh whichever view is on screen
    window.addEventListener("voisr:new-dictation", () => {
      if (!$("#view-home").hidden) V.views.home.render();
      if (!$("#view-history").hidden) V.views.history.render();
    });
    $("#hotkey-capture").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = "Press any key…";
      $("#hotkey-current").textContent = "…";
      try {
        const res = await V.api().capture_hotkey();
        if (res.error) {
          V.ui.toast(res.error);
        } else {
          V.ui.toast(`Push-to-talk key set to ${res.label}`);
        }
      } finally {
        btn.disabled = false;
        btn.textContent = "Change…";
        V.views.settings.render();
      }
    });
    V.views.settings.initColorWheel();
    show("home");
  }

  window.addEventListener("pywebviewready", init);

  V.show = show;
})(window.Voisr);
