"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const BANNER = "dictionary";
  let filter = "", filterText = "", tab = "all", adding = false, dismissed = false;
  let closeEdit = null;  // closes the row being edited: one at a time
  let editingKey = null;  // that row's word: a render never hides or rebuilds it
  let kept = false;  // a render kept that row only because it is being edited

  const byId = (id) => document.getElementById(id);
  const arrow = () => V.ui.el("span", { class: "pair-arrow", "aria-hidden": "true", text: "→" });

  /* A row reads as a pair, with Edit at its end; Remove lives in the edit, as it
     always did on the row's own bin before the board moved it. An auto-added
     word keeps Remove in plain sight, since nobody chose it. */
  function entryRow(spoken, written, { auto, used }) {
    const el = V.ui.el;
    const row = el("li", { class: "item-row is-plain" });

    function show() {
      row.classList.remove("is-editing");
      row.replaceChildren(
        el("div", { class: "pair" },
          el("span", { class: "pair-from", text: spoken }), arrow(),
          el("span", { class: "pair-to", text: written }),
          auto ? el("span", {
            class: "tag", text: "Auto-added",
            title: "Voisr added this after seeing you make the same correction twice. Remove it and it won't come back.",
          }) : null,
          used ? el("span", { class: "row-note", text: `used ${used}×` }) : null),
        el("div", { class: "row-end" },
          el("button", { type: "button", class: "text-btn", text: "Edit",
                         "aria-label": `Edit ${spoken}`, on: { click: edit } }),
          auto ? el("button", { type: "button", class: "text-btn", text: "Remove",
                                "aria-label": `Remove ${spoken}`,
                                on: { click: () => removeEntry(spoken, written) } }) : null));
    }

    function edit() {
      if (closeEdit) closeEdit();
      if (adding) closeForm();
      const close = () => {
        const mine = closeEdit === close;
        if (mine) { closeEdit = null; editingKey = null; }
        show();
        // a render that came while this was open left the row as it was
        if (row.dataset.stale || (mine && kept)) {
          delete row.dataset.stale;
          kept = false;
          render({ animate: false });
        }
      };
      closeEdit = close;
      editingKey = spoken;
      const input = el("input", { class: "row-input", type: "text", autocomplete: "off",
                                  "aria-label": `Replacement for ${spoken}` });
      input.value = written;

      /* Enter or Save. Clicking away doesn't save: it would race Cancel. */
      async function save() {
        const next = input.value.trim();
        if (!next) { input.focus(); return; }
        // Read fresh rather than reuse a closed-over `cfg` -- another commit
        // (or a remove/undo) may have written to config.json since this row
        // was rendered, and a stale copy here would clobber it.
        const cfg = await V.ui.tryApi(V.api().get_config(), "Load dictionary");
        if (!cfg) return;
        cfg.dictionary = cfg.dictionary || {};
        // unchanged means unchanged from what is stored now, not from what
        // this row showed when it opened
        if (next === (spoken in cfg.dictionary ? cfg.dictionary[spoken] : written)) { close(); return; }
        cfg.dictionary[spoken] = next;
        // A failed write keeps the edit open with what was typed, and tryApi's
        // toast says why: the row must not read as saved.
        if (!await V.ui.tryApi(
          V.api().set_config({ dictionary: cfg.dictionary }), "Save dictionary")) return;
        close();
        V.ui.toast(`Updated: ${spoken} → ${next}`);
        render();
      }

      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); save(); }
        else if (e.key === "Escape") {
          e.preventDefault();
          close();
          const back = row.querySelector(".text-btn");
          if (back) back.focus();
        }
      });
      row.classList.add("is-editing");
      row.replaceChildren(
        el("div", { class: "pair" },
          el("span", { class: "pair-from", text: spoken }), arrow(), input),
        el("div", { class: "row-end" },
          el("button", { type: "button", class: "btn-primary", text: "Save", on: { click: save } }),
          el("button", { type: "button", class: "btn-quiet", text: "Cancel", on: { click: close } }),
          el("button", { type: "button", class: "text-btn", text: "Remove",
                         "aria-label": `Remove ${spoken}`,
                         on: { click: () => removeEntry(spoken, written) } })));
      input.focus();
      if (input.select) input.select();
    }

    show();
    return row;
  }

  async function removeEntry(spoken, written) {
    const cfg = await V.ui.tryApi(V.api().get_config(), "Load dictionary");
    if (!cfg) return;
    cfg.dictionary = cfg.dictionary || {};
    // Undo restores what is stored now, not what the row showed when it was built
    if (spoken in cfg.dictionary) written = cfg.dictionary[spoken];
    delete cfg.dictionary[spoken];
    // Same contract as history's delete: no "Entry removed", and no Undo
    // offer, until the write is confirmed.
    if (!await V.ui.tryApi(
      V.api().set_config({ dictionary: cfg.dictionary }), "Save dictionary")) return;
    render();
    /* Coalesced the same way history's delete is (V.ui.offerUndo): removing
       several entries in a burst used to queue one identical "Entry removed"
       per removal, so the undos surfaced oldest-first and clicking one
       promoted the next into the host instead of showing its result. Its own
       batch, though -- a dictionary entry and a dictation are different
       things and must never end up under one "2 items removed".

       Restoring is still per-entry and still re-reads config each time, so
       the conflict rule below (an edit made after the removal wins) keeps
       working exactly as it did, key by key. */
    V.ui.offerEntryUndo({
      restore: async () => {
        const c = await V.ui.tryApi(V.api().get_config(), "Load dictionary");
        if (!c) return false;
        c.dictionary = c.dictionary || {};
        const current = c.dictionary[spoken];
        if (current !== undefined && current !== written) {
          V.ui.toast(`Kept newer edit: ${spoken} → ${current}`);
          return false;
        }
        if (current === written) return true; // already restored, nothing to do
        c.dictionary[spoken] = written;
        return !!await V.ui.tryApi(
          V.api().set_config({ dictionary: c.dictionary }), "Restore entry");
      },
      refresh: render,
    });
  }

  function suggestionRow(pair) {
    const el = V.ui.el;
    async function act(call, label, done) {
      if (!await V.ui.tryApi(call, label)) return;
      if (done) V.ui.toast(done);
      render();
    }
    return el("li", { class: "item-row is-plain" },
      el("div", { class: "pair" },
        el("span", { class: "pair-from", text: pair.heard }), arrow(),
        el("span", { class: "pair-to", text: pair.written }),
        pair.seen > 1 ? el("span", { class: "row-note", text: `seen ${pair.seen}×` }) : null),
      el("div", { class: "row-end" },
        el("button", {
          type: "button", class: "btn-primary", text: "Accept",
          on: { click: () => act(V.api().accept_suggestion(pair.id), "Accept suggestion",
                                 `Added: ${pair.heard} → ${pair.written}`) },
        }),
        el("button", {
          type: "button", class: "btn-quiet", text: "Dismiss",
          on: { click: () => act(V.api().dismiss_suggestion(pair.id), "Dismiss suggestion") },
        })));
  }

  /* Suggestions are extra: an entry list must still draw if these fail. */
  async function loadSuggestions() {
    try {
      return await V.api().get_suggestions();
    } catch (e) {
      return null;
    }
  }

  function setCount(name, n) {
    byId(`dict-count-${name}`).textContent = n ? String(n) : "";
  }

  /* The banner shows only on the All tab, and the adding form takes its place. */
  function syncBanner() {
    byId("dict-banner").hidden = dismissed || adding || tab !== "all";
  }

  function emptyText(cfg) {
    if (filter) return `No words match “${filterText}”.`;
    if (tab === "suggestions") {
      return cfg.learn_from_edits === false || cfg.use_screen_context === false
        ? "Learning from corrections is off in Settings."
        : "Nothing waiting. Fix a word after dictating and it appears here.";
    }
    if (tab === "auto") return "Nothing added automatically.";
    return dismissed ? "No words yet. Map what it heard to what you meant." : "No words yet.";
  }

  /* One data source, so the same rule as history's render(): on a failed
     load keep the last good list rather than blanking the pane. */
  async function render({ animate } = {}) {
    try {
      await renderInner(animate);
    } catch (e) {
      V.ui.toast("Dictionary failed to load");
    }
  }

  async function renderInner(animate) {
    const [cfg, extra] = await Promise.all([
      V.ui.tryApi(V.api().get_config(), "Load dictionary"), loadSuggestions(),
    ]);
    if (!cfg) return;
    const pending = (extra && extra.pending) || [];
    const auto = (extra && extra.auto_added) || {};
    const used = (extra && extra.used) || {};
    const all = Object.entries(cfg.dictionary || {});
    // the word being edited was removed elsewhere: its row goes, nothing points at it
    if (editingKey !== null && !all.some(([s]) => s === editingKey)) {
      closeEdit = null;
      editingKey = null;
    }
    const isAuto = (s, w) => auto[s.toLowerCase()] === w;

    // everything the All tab shows, so the number matches what's on the page
    setCount("all", all.length + pending.length);
    setCount("suggestions", pending.length);
    setCount("auto", all.filter(([s, w]) => isAuto(s, w)).length);

    const matches = (a, b) => !filter || a.toLowerCase().includes(filter) || b.toLowerCase().includes(filter);
    const shownPending = tab === "auto" ? [] : pending.filter((p) => matches(p.heard, p.written));
    const wanted = ([s, w]) => (tab === "all" || isAuto(s, w)) && matches(s, w);
    const shownWords = tab === "suggestions" ? []
      : all.filter((e) => e[0] === editingKey || wanted(e));
    if (editingKey !== null) kept = !shownWords.every((e) => e[0] !== editingKey || wanted(e));

    dismissed = V.ui.bannerDismissed(cfg, BANNER);
    syncBanner();

    byId("dict-suggestions").hidden = shownPending.length === 0;
    V.lists.sync(byId("dict-suggestion-list"), shownPending.map((p) => ({
      key: p.id, sig: `${p.seen}`, build: () => suggestionRow(p),
    })));

    byId("dict-words").hidden = shownWords.length === 0;
    byId("dict-words-label").textContent = tab === "auto" ? "Auto-added" : "Your words";
    const empty = byId("dict-empty");
    empty.hidden = shownPending.length + shownWords.length > 0;
    empty.textContent = emptyText(cfg);

    // keyed: an add, edit or removal moves only that row (js/lists.js);
    // a filter keystroke applies instantly
    V.lists.sync(byId("dict-list"), shownWords.map(([s, w]) => {
      const facts = { auto: isAuto(s, w), used: used[s.toLowerCase()] || 0 };
      return {
        key: s, sig: `${w}|${facts.auto}|${facts.used}`, build: () => entryRow(s, w, facts),
      };
    }), { animate: animate !== false });
  }

  function openForm() {
    if (closeEdit) closeEdit();
    adding = true;
    syncBanner();
    const form = byId("dict-form");
    const wasOpen = !form.hidden;
    form.hidden = false;
    if (!wasOpen && V.lists.canAnimate(form)) V.lists.enter(form, "fill");
    byId("dict-spoken").focus();
  }

  function closeForm() {
    adding = false;
    byId("dict-form").hidden = true;
    byId("dict-spoken").value = "";
    byId("dict-written").value = "";
    syncBanner();
  }

  async function addEntry(e) {
    e.preventDefault();
    const spoken = byId("dict-spoken").value.trim().toLowerCase();
    const written = byId("dict-written").value.trim();
    if (!spoken || !written) return;
    const cfg = await V.ui.tryApi(V.api().get_config(), "Load dictionary");
    if (!cfg) return;
    const dict = cfg.dictionary || {};
    dict[spoken] = written;
    // Only close the form once the entry is really stored: wiping the two
    // fields on a failed write would throw away what the user just typed.
    if (!await V.ui.tryApi(
      V.api().set_config({ dictionary: dict }), "Save dictionary")) return;
    closeForm();
    V.ui.toast(`Added: ${spoken} → ${written}`);
    render();
  }

  function setTab(next) {
    if (closeEdit) closeEdit();
    tab = next;
    for (const button of byId("dict-tabs").querySelectorAll(".tab")) {
      const on = button.dataset.tab === tab;
      button.classList.toggle("active", on);
      button.setAttribute("aria-pressed", String(on));
    }
    render({ animate: false });
  }

  function openSearch() {
    byId("dict-search").hidden = false;
    byId("dict-search-open").setAttribute("aria-expanded", "true");
    byId("dict-search").focus();
  }

  /* A hidden input must not leave a stale filter in effect: there would be no
     visible control left to clear it. */
  function closeSearch() {
    const search = byId("dict-search");
    search.hidden = true;
    search.value = "";
    filter = "";
    filterText = "";
    byId("dict-search-open").setAttribute("aria-expanded", "false");
    byId("dict-search-open").focus();
    render({ animate: false });
  }

  function init() {
    const search = byId("dict-search");
    search.addEventListener("input", (e) => {
      filterText = e.target.value.trim();
      filter = filterText.toLowerCase();
      render({ animate: false });
    });
    search.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeSearch(); }
    });
    byId("dict-search-open").addEventListener("click", () => {
      if (search.hidden) openSearch(); else closeSearch();
    });
    byId("dict-tabs").addEventListener("click", (e) => {
      const button = e.target.closest(".tab");
      if (button) setTab(button.dataset.tab);
    });
    byId("dict-add").addEventListener("click", openForm);
    byId("dict-banner-add").addEventListener("click", openForm);
    byId("dict-cancel").addEventListener("click", closeForm);
    byId("dict-form").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeForm(); byId("dict-add").focus(); }
    });
    byId("dict-banner-close").addEventListener("click", async () => {
      if (await V.ui.dismissBanner(BANNER, byId("dict-banner"))) {
        dismissed = true;
        render({ animate: false });
      }
    });
  }

  V.views = V.views || {};
  V.views.dictionary = { render, addEntry, init };
})(window.Voisr);
