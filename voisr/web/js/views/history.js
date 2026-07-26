"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const PAGE = 200;
  const SEARCH_DEBOUNCE_MS = 120;
  let query = "", shown = PAGE, items = [], flat = [], cursor = -1;
  let searchDebounceTimer = null;

  async function onCopy(item) {
    if (!await V.ui.tryApi(V.api().copy_text(item.text), "Copy")) return;
    V.ui.toast("Copied to clipboard");
  }

  async function onDelete(item) {
    // Nothing may claim success before the bridge confirms it. A failed
    // delete must not print "Dictation deleted" and must not offer an Undo
    // for a row that never went anywhere.
    if (!await V.ui.tryApi(V.api().delete_history_item(item.id), "Delete dictation")) return;
    await render();
    /* One live undo, always current: deleting again inside the window
       upgrades this toast to "N dictations deleted" and its Undo restores the
       whole batch. The wording, the batching and the honest partial-failure
       reporting all live in V.ui.offerDictationUndo, which home.js shares --
       both surfaces render the same rows and must behave identically. */
    V.ui.offerDictationUndo({
      restore: () => V.ui.tryApi(V.api().restore_history_item(item), "Restore dictation"),
      refresh: render,
    });
  }

  /* Internal query update shared by both entry points below. Trimming only
     happens for matching (see matches()/highlighted()), never here -- a
     mid-typing trim is what let a leading/trailing space vanish and mangle
     multi-word queries like "site visit". Filters the already-loaded
     `items` array -- no bridge round-trip, so this is safe to call on
     every keystroke (see onSearchInput's debounce below). */
  function applyQuery(q) {
    query = q || "";
    shown = PAGE;
    cursor = -1;
    renderList();
  }

  /* User-typing path: the `input` event handler. Must NOT write back to
     `input.value` -- doing so while the user is mid-keystroke is what
     caused a trimmed value to clobber spaces as they were typed. Debounced
     so a fast typist doesn't force a full re-render (rebuild + restagger
     the DOM) on every single keystroke. */
  function onSearchInput(q) {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => applyQuery(q), SEARCH_DEBOUNCE_MS);
  }

  /* Pure: takes the needle as a parameter rather than reading module state,
     so the search semantics are directly testable (tests/js/views.test.mjs).
     The trim lives HERE, at match time only -- never on the way in from the
     input field, which is what used to eat the spaces in "site visit". */
  function matchesQuery(text, q) {
    const needle = (q || "").trim();
    return !needle || text.toLowerCase().includes(needle.toLowerCase());
  }

  function matches(item) {
    return matchesQuery(item.text, query);
  }

  /* Highlight without markup injection: split on the match and build nodes.
     Pure for the same reason as matchesQuery above. */
  function highlighted(text, q) {
    const needle = (q || "").trim();
    if (!needle) return [text];
    const lower = text.toLowerCase(), qLower = needle.toLowerCase();
    const out = [];
    let at = 0, i;
    while ((i = lower.indexOf(qLower, at)) !== -1) {
      if (i > at) out.push(text.slice(at, i));
      out.push(V.ui.el("mark", { text: text.slice(i, i + needle.length) }));
      at = i + needle.length;
    }
    if (at < text.length) out.push(text.slice(at));
    return out;
  }

  function moveCursor(delta) {
    if (!flat.length) return;
    cursor = Math.max(0, Math.min(cursor + delta, flat.length - 1));
    const rows = document.querySelectorAll("#history-groups .item-row");
    rows.forEach((r, i) => r.classList.toggle("selected", i === cursor));
    if (rows[cursor]) rows[cursor].scrollIntoView({ block: "nearest" });
  }

  /* The single source of truth for "is there a selected item right now".
     Never trust `cursor`/`flat` alone — always confirm a row actually
     carries `.selected` in the live DOM before acting on it. This is what
     keeps a stale cursor from ever deleting a row the user didn't pick. */
  function selectedItem() {
    if (cursor < 0 || cursor >= flat.length) return null;
    const rows = document.querySelectorAll("#history-groups .item-row");
    const row = rows[cursor];
    if (!row || !row.classList.contains("selected")) return null;
    return flat[cursor];
  }

  /* Pure re-render from the already-loaded `items` array -- no bridge call.
     This is what a search keystroke drives (via applyQuery); it must never
     hit the API, or every keystroke would re-read and re-sort the entire
     JSONL in Python for a filter that's cheap to do in JS. */
  function renderList() {
    const visible = items.filter(matches);
    flat = visible.slice(0, shown);
    document.getElementById("history-empty").hidden = visible.length > 0;
    document.getElementById("history-more").hidden = visible.length <= shown;

    const groups = new Map();
    for (const item of flat) {
      const day = V.ui.dayOf(item.ts);
      if (!groups.has(day)) groups.set(day, []);
      groups.get(day).push(item);
    }

    const container = document.getElementById("history-groups");
    container.replaceChildren(...[...groups.entries()].map(([day, rows]) =>
      V.ui.el("div", { class: "day-group" },
        V.ui.el("div", { class: "day-label", text: day }),
        V.ui.el("div", { class: "card" },
          V.ui.el("ul", { class: "item-list" },
            ...rows.map((item) => {
              const row = V.ui.itemRow(item, {
                onCopy: onCopy, onDelete: onDelete,
              });
              const txt = row.querySelector(".txt");
              txt.replaceChildren(...highlighted(item.text, query));
              row.addEventListener("click", () => row.classList.toggle("expanded"));
              return row;
            }))))));

    // `flat` was just rebuilt (delete / Load more / search / background
    // refresh can all land here) and row indices may have shifted, so any
    // previous `cursor` position is no longer trustworthy. Clear it rather
    // than try to remap it — a stale index reapplied to new rows is exactly
    // how a Backspace can delete something the user never selected. The
    // user must arrow again to re-select, and `selectedItem()` above
    // refuses to act on anything until they do.
    cursor = -1;
  }

  /* Genuine data-changing entry point: refetches from Python then rebuilds.
     Used on view entry, delete/undo, "Load more" and the
     voisr:new-dictation push event -- anywhere the underlying data (not
     just the filter) may have changed. */
  /* History has exactly one data source, so there is no degraded view to
     fall back to: on a failed load keep the last good list on screen (the
     toast already says why it is stale) rather than replacing it with an
     empty pane. The try/catch is the backstop for anything tryApi cannot
     see — a DOM mistake in renderList, say — so a throw can never leave the
     view blank and silent. */
  async function render() {
    try {
      const loaded = await V.ui.tryApi(V.api().get_history(), "Load history");
      if (!loaded) return;
      items = loaded;
      renderList();
    } catch (e) {
      V.ui.toast("History failed to load");
    }
  }

  /* ---------- export format popover ----------

     export_history(fmt) has always supported "txt"/"md"/"json"; only the
     UI hardcoded "txt". The three format buttons live as static markup in
     index.html (mirroring the Settings colour popover), so no dynamic DOM
     construction is needed here -- open()/close() only flip `hidden`. */

  function openExportPopover() {
    document.getElementById("export-popover").hidden = false;
    document.getElementById("history-export").setAttribute("aria-expanded", "true");
  }

  function closeExportPopover() {
    document.getElementById("export-popover").hidden = true;
    document.getElementById("history-export").setAttribute("aria-expanded", "false");
  }

  async function doExport(fmt) {
    // Close immediately on the click that chose a format -- the picker's
    // job is done the instant a format is picked, whether the export
    // itself goes on to succeed, fail, or get cancelled at the save panel.
    closeExportPopover();
    const out = await V.ui.tryApi(V.api().export_history(fmt), "Export history");
    if (!out) return;
    const saved = await V.ui.tryApi(
      V.api().save_text_file(out.text, out.suggested_name), "Save file");
    // `saved.ok` false is a user cancelling the save panel, not a failure:
    // stay silent rather than claim an export that did not happen.
    if (saved && saved.ok) V.ui.toast("History exported");
  }

  function initKeys() {
    document.getElementById("history-search")
      .addEventListener("input", (e) => onSearchInput(e.target.value));

    document.getElementById("history-more").addEventListener("click", () => {
      shown += PAGE;
      render();
    });

    document.getElementById("history-export").addEventListener("click", () => {
      const pop = document.getElementById("export-popover");
      if (pop.hidden) openExportPopover(); else closeExportPopover();
    });

    for (const fmt of ["txt", "md", "json"]) {
      document.getElementById(`export-fmt-${fmt}`)
        .addEventListener("click", () => doExport(fmt));
    }

    /* click-outside / Escape dismissal for the export popover -- registered
       once here (initKeys, called once from main.js), not inside
       openExportPopover, so repeated opens never stack extra listeners.
       Mirrors settings.js's colour-popover dismissal: the click listener
       bails out early on any click still inside the popover or on the
       Export button that opened it, so the very click that opens the
       popover (which bubbles to document right after) can never
       immediately close it again. */
    document.addEventListener("click", (e) => {
      const pop = document.getElementById("export-popover");
      if (pop.hidden) return;
      if (pop.contains(e.target) || e.target.closest("#history-export")) return;
      closeExportPopover();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!document.getElementById("export-popover").hidden) closeExportPopover();
    });

    document.addEventListener("keydown", (e) => {
      if (document.getElementById("view-history").hidden) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") {
        e.preventDefault();
        document.getElementById("history-search").focus();
        return;
      }
      if (e.target.tagName === "INPUT") return;
      if (e.key === "ArrowDown") { e.preventDefault(); moveCursor(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); moveCursor(-1); }
      else if (e.key === "Enter") {
        const item = selectedItem();
        if (!item) return;
        e.preventDefault();
        document.querySelectorAll("#history-groups .item-row")[cursor]
          .classList.toggle("expanded");
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "c") {
        const item = selectedItem();
        if (!item) return;
        e.preventDefault(); onCopy(item);
      } else if (e.key === "Backspace") {
        // Deliberately re-derive the item from the live DOM selection
        // instead of trusting `flat[cursor]` — see selectedItem()'s
        // comment. If nothing is visibly selected, Backspace is a no-op.
        const item = selectedItem();
        if (!item) return;
        e.preventDefault(); onDelete(item);
      }
    });
  }

  V.views = V.views || {};
  V.views.history = { render, initKeys, matchesQuery, highlighted };
})(window.Voisr);
