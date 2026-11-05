"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* ---------- navigation ---------- */

  // tracks whichever section is mid view-in animation, so a rapid switch
  // (or re-selecting the same view) can clean it up instead of leaving it
  // stuck with a stale "entering" class or an orphaned listener
  let enteringTarget = null;
  let enteringController = null;
  let currentView = null;   // the page, which stays behind the Settings pop-up
  let litView = null;       // what the sidebar highlights: the page, or Settings

  /* One highlight glides behind the sidebar to the active item (180 ms,
     base.css) instead of each item painting its own. The first placement
     happens with the transition off, so the first paint doesn't glide in;
     reduced motion snaps in CSS. */
  function glideTo(view) {
    const glide = document.getElementById("nav-glide");
    const item = Array.from(document.querySelectorAll(".nav-item"))
      .find((b) => b.dataset.view === view);
    if (!glide || !item) return;
    glide.style.height = `${item.offsetHeight}px`;
    glide.style.transform = `translateY(${item.offsetTop}px)`;
    if (!glide.classList.contains("is-live")) {
      void glide.offsetWidth;  // commit the first position before transitions switch on
      glide.classList.add("is-live");
    }
  }

  /* Settings sits at the bottom of the sidebar, so its offset changes with the
     window's height. Drop .is-live so the highlight tracks the resize instead of
     trailing it: glideTo switches the transition back on after placing it. */
  window.addEventListener("resize", () => {
    if (!litView) return;
    const glide = document.getElementById("nav-glide");
    if (glide) glide.classList.remove("is-live");
    glideTo(litView);
  });

  function highlight(view) {
    litView = view;
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    glideTo(view);
  }

  function show(view) {
    currentView = view;
    document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== `view-${view}`; });
    highlight(view);
    const target = document.getElementById(`view-${view}`);

    // .main is the one scroll container for every view, so its offset
    // carries across a switch: leave Home scrolled to the bottom, open
    // History, and it opens scrolled past its own content — a blank pane.
    const main = document.querySelector(".main");
    if (main) main.scrollTop = 0;

    if (enteringController) {
      enteringController.abort();
      if (enteringTarget) enteringTarget.classList.remove("entering");
    }

    // restart the CSS animation from frame 0, same technique as kick() in
    // widget.html: drop the class, force a reflow so the drop takes effect,
    // then re-add it. This replays the animation (even for the same view
    // re-selected) and, critically, lets it run to completion instead of
    // being cancelled mid-flight by an immediate class removal.
    target.classList.remove("entering");
    void target.offsetWidth; // force reflow
    target.classList.add("entering");

    const controller = new AbortController();
    enteringTarget = target;
    enteringController = controller;
    const finishEntering = () => {
      target.classList.remove("entering");
      if (enteringController === controller) { enteringController = null; enteringTarget = null; }
    };
    target.addEventListener("animationend", finishEntering, { once: true, signal: controller.signal });
    target.addEventListener("animationcancel", finishEntering, { once: true, signal: controller.signal });

    // opening a page replays the row stagger on what is already there
    if (V.lists) target.querySelectorAll(".item-list").forEach((list) => V.lists.stagger(list));

    if (view === "home") V.views.home.render({ entering: true });
    if (view === "history") V.views.history.render();
    if (view === "dictionary") V.views.dictionary.render();
    if (view === "snippets") V.views.snippets.render();
  }

  /* Settings is a pop-up over the page rather than a page of its own, so it
     never goes through show(): the page behind stays exactly as it was.
     opts.section opens it on one section (settings.js's SECTIONS). A page
     switch, which only the palette can ask for while it's up, closes it. */
  function go(view, opts) {
    const settings = V.views.settings;
    if (view === "settings") {
      highlight("settings");
      settings.open(opts);
      return;
    }
    currentView = null;  // the page being left has nothing to refresh
    settings.close();
    show(view);
  }

  /* A setting can change what the page behind says. Home learns the key's name
     and the auto-paste line only when it renders, and Dictionary's suggestions
     depend on the learning switches. History has nothing that depends on them,
     and rendering it would lose the row you had selected. */
  window.addEventListener("voisr:settings-closed", () => {
    if (currentView) highlight(currentView);
    if (currentView === "home") V.views.home.render();
    if (currentView === "dictionary") V.views.dictionary.render();
  });

  /* ---------- the keyboard ----------

     The pages have no walker of their own, so this one covers the whole window,
     Undo in the toast included. On the document, because focus can be on the
     body: first run leaves it there, and so does a redrawn list. While Settings
     is up its own handlers stop Tab before it gets here, except when the palette
     is over it; first run hides the shell and walks its own steps. Once Settings
     is closing its handlers have stopped, but its buttons are there until the
     fade ends, so that moment walks the page alone. */
  function onTab(e) {
    if (e.key !== "Tab" || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    const $ = V.ui.$;
    const scrim = $("#settings-scrim");
    if ($(".shell").hidden || !$("#palette").hidden || scrim.classList.contains("is-open")) return;
    V.ui.walkTab(e, scrim.hidden ? document.body : $(".shell"));
  }
  document.addEventListener("keydown", onTab);

  /* ---------- appearance ---------- */

  const DARK_QUERY = window.matchMedia("(prefers-color-scheme: dark)");
  let appearancePref = "system";

  function resolveAppearance() {
    const mode = appearancePref === "system"
      ? (DARK_QUERY.matches ? "dark" : "light")
      : appearancePref;
    document.documentElement.dataset.appearance = mode;
    return mode;
  }

  /* pref is "system" | "light" | "dark"; the attribute is always concrete, so
     tokens.css needs one block per theme instead of restating both. */
  V.applyAppearance = function (pref) {
    appearancePref = pref || "system";
    const mode = resolveAppearance();
    // Settings' picker listens: the palette applies a theme without it.
    window.dispatchEvent(new Event("voisr:appearance"));
    return mode;
  };
  V.appearancePref = () => appearancePref;

  // keep following macOS when the user has not pinned a theme
  DARK_QUERY.addEventListener("change", () => {
    if (appearancePref === "system") resolveAppearance();
  });

  /* ---------- init ---------- */

  async function init() {
    const $ = V.ui.$;
    document.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => go(b.dataset.view)));
    $("#record-btn").addEventListener("click", V.views.home.toggleRecord);
    $("#dict-form").addEventListener("submit", V.views.dictionary.addEntry);
    $("#paste-toggle").addEventListener("change", async (e) => {
      // Same contract as settings.js's launch-toggle: a failed write must
      // never leave the checkbox claiming a state the app isn't actually in.
      const res = await V.ui.tryApi(
        V.api().set_config({ paste: e.target.checked }), "Save auto-paste");
      if (!res) { e.target.checked = !e.target.checked; return; }
      V.ui.toast(e.target.checked ? "Auto-paste enabled" : "Auto-paste disabled");
    });
    $("#widget-toggle").addEventListener("change", async (e) => {
      const res = await V.ui.tryApi(
        V.api().set_config({ show_widget: e.target.checked }), "Save recording widget");
      if (!res) { e.target.checked = !e.target.checked; return; }
      V.ui.toast(e.target.checked ? "Recording widget on" : "Recording widget off");
    });
    const pickPillTheme = V.ui.serial(async (btn) => {
      const res = await V.ui.tryApi(
        V.api().set_config({ pill_theme: btn.dataset.value }), "Save pill theme");
      // Re-render regardless: on success this reflects the newly active
      // theme, on failure it puts the "active" pill back on what is really
      // stored -- the grid's equivalent of reverting a toggle.
      V.views.settings.renderThemes();
      V.views.settings.renderColorWheel();
      if (!res) return;
      V.ui.toast(`Pill theme: ${btn.querySelector(".theme-name").textContent}`);
    });
    $("#theme-grid").addEventListener("click", (e) => {
      const btn = e.target.closest(".theme-opt");
      if (btn) pickPillTheme(btn);
    });
    V.ui.radioKeys($("#theme-grid"), ".theme-opt", pickPillTheme);
    // pushed from Python after each dictation (hotkey ones land while the
    // dashboard is open) — refresh whichever view is on screen
    window.addEventListener("voisr:new-dictation", () => {
      if (!$("#view-home").hidden) V.views.home.render();
      if (!$("#view-history").hidden) V.views.history.render();
    });
    // pushed from Python when a correction was learned; added is what went
    // into the dictionary unasked, which the pill has also said if it could
    window.addEventListener("voisr:dictionary-changed", (e) => {
      for (const { written } of (e.detail && e.detail.added) || []) {
        V.ui.toast(`Added ${written} to Dictionary`);
      }
      if (!$("#view-dictionary").hidden) V.views.dictionary.render();
    });
    V.views.home.init();
    V.views.history.initKeys();
    V.views.dictionary.init();
    V.views.snippets.init();
    V.views.settings.init();

    // The app still has to start even if this read fails, so fall back to
    // "system" rather than block boot -- tryApi's toast is enough to say why
    // appearance may not match what was saved.
    const cfg = await V.ui.tryApi(V.api().get_config(), "Load settings");
    V.applyAppearance((cfg && cfg.appearance) || "system");

    // A config.json that couldn't be parsed was moved aside at load; say so
    // once, or the user just sees their dictionary gone.
    const notice = await V.ui.tryApi(V.api().take_config_notice(), "Check settings file");
    if (notice && notice.notice) V.ui.toast(notice.notice);

    // The palette waits too: first run has nothing it could jump to.
    const enterApp = () => { V.palette.init(); show("home"); };
    if (!await V.views.firstRun.start(enterApp)) enterApp();
  }

  window.addEventListener("pywebviewready", init);

  V.show = go;
})(window.Voisr);
