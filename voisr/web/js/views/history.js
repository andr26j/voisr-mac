"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const PAGE = 200;
  const SEARCH_DEBOUNCE_MS = 120;
  let query = "", shown = PAGE, items = [], flat = [];
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

  /* The keyboard selection lives on the rows on screen, never on an index
     into `flat`: rows are kept across refreshes (js/lists.js), and a deleted
     row (or a whole day) stays in the DOM while it fades out, by which time
     `flat` is already the new list. Rows on their way out can't be reached. */
  function liveRows() {
    return Array.from(document.getElementById("history-groups").querySelectorAll(".item-row"))
      .filter((r) => !r.closest(".is-leaving"));
  }

  function selectedRow() {
    return liveRows().find((r) => r.classList.contains("selected")) || null;
  }

  function clearSelection() {
    for (const r of document.getElementById("history-groups").querySelectorAll(".item-row")) {
      r.classList.remove("selected");
    }
  }

  function moveCursor(delta) {
    const rows = liveRows();
    if (!rows.length) return;
    const at = rows.findIndex((r) => r.classList.contains("selected"));
    const next = Math.max(0, Math.min(at + delta, rows.length - 1));
    rows.forEach((r, i) => r.classList.toggle("selected", i === next));
    rows[next].scrollIntoView({ block: "nearest" });
  }

  /* The single source of truth for "is there a selected item right now":
     the highlighted row, resolved to its item by id. A row whose item is no
     longer listed answers null, so Backspace can only ever delete the row
     the user can see highlighted. */
  function selectedItem() {
    const row = selectedRow();
    if (!row) return null;
    return flat.find((item) => item.id === row.dataset.key) || null;
  }

  /* Pure re-render from the already-loaded `items` array -- no bridge call.
     This is what a search keystroke drives (via applyQuery); it must never
     hit the API, or every keystroke would re-read and re-sort the entire
     JSONL in Python for a filter that's cheap to do in JS. */
  let knownIds = null;      // every loaded id at the last data render

  /* Rebuilds from the already-loaded `items` array. Keyed (js/lists.js):
     day groups by label, rows by id, so a refresh moves only what changed.
     `animate` is set by render() -- a real data change, and "Load more"
     too, which refetches through render(): its new rows rise in as fill.
     A search keystroke applies instantly. Runs serialised, so a refresh
     that lands mid-animation waits its turn. */
  function renderList({ animate = false } = {}) {
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
    const known = knownIds;
    knownIds = new Set(items.map((item) => item.id));
    const ids = knownIds;
    const q = query;

    // `flat` was just rebuilt (delete / Load more / search / background
    // refresh can all land here), so drop the keyboard selection: the user
    // arrows again to re-select, and selectedItem() refuses to act until
    // they do. Kept rows would otherwise stay highlighted.
    clearSelection();

    return V.lists.serial(document.getElementById("history-groups"),
                          () => syncGroups(groups, { animate, known, ids, q }));
  }

  function buildRow(item, q) {
    const row = V.ui.itemRow(item, { onCopy: onCopy, onDelete: onDelete });
    row.querySelector(".txt").replaceChildren(...highlighted(item.text, q));
    row.addEventListener("click", () => row.classList.toggle("expanded"));
    return row;
  }

  /* Returns a promise only when it animates (see V.lists.serial). */
  function syncGroups(groups, opts) {
    const container = document.getElementById("history-groups");
    const wasEmpty = container.children.length === 0;
    const motion = opts.animate && !wasEmpty && V.lists.canAnimate(container);
    if (!motion) {
      applyGroups(container, groups, opts);
      if (wasEmpty && opts.animate) {
        for (const group of Array.from(container.children)) V.lists.stagger(group.querySelector(".item-list"));
      }
      return undefined;
    }
    return animateGroups(container, groups, opts);
  }

  async function animateGroups(container, groups, opts) {
    const L = V.lists;
    const { ids } = opts;
    const listOf = (group) => group.querySelector(".item-list");

    // deleted rows leave first; a day that empties leaves as a whole
    const leaving = [];
    for (const group of Array.from(container.children)) {
      if (!groups.has(group.dataset.key)) { leaving.push(group); continue; }
      for (const row of Array.from(listOf(group).children)) {
        if (!ids.has(row.dataset.key)) leaving.push(row);
      }
    }
    if (leaving.length) await L.leave(leaving);

    const before = !L.reduced()
      ? L.measure(Array.from(container.children).flatMap((group) =>
          [group.querySelector(".day-label"), ...Array.from(listOf(group).children)]))
      : new Map();

    const { fresh, enteredRows } = applyGroups(container, groups, opts);
    L.play(before);
    // a new day rises in as one piece; its rows only take the wash
    for (const group of fresh) L.enter(group, "group");
    for (const [row, kind, inFreshGroup] of enteredRows) {
      if (inFreshGroup) { if (kind === "landed") L.wash(row); }
      else L.enter(row, kind);
    }
  }

  /* Keyed, motion-free: day groups by label, rows by id. */
  function applyGroups(container, groups, { known, q }) {
    const L = V.lists;
    const listOf = (group) => group.querySelector(".item-list");
    const { entered: newGroups } = L.apply(container, [...groups.keys()].map((day) => ({
      key: day, sig: day,
      build: () => V.ui.el("div", { class: "day-group" },
        V.ui.el("div", { class: "day-label", text: day }),
        V.ui.el("div", { class: "card" }, V.ui.el("ul", { class: "item-list" }))),
    })));
    const fresh = new Set(newGroups.map(([group]) => group));
    const enteredRows = [];
    for (const group of Array.from(container.children)) {
      const rows = groups.get(group.dataset.key) || [];
      const { entered } = L.apply(listOf(group), rows.map((item) => ({
        key: item.id, sig: q, build: () => buildRow(item, q),
      })), { wasKnown: (id) => !!known && known.has(id) });
      for (const [row, kind] of entered) enteredRows.push([row, kind, fresh.has(group)]);
    }
    return { fresh, enteredRows };
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
      await renderList({ animate: true });
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
        if (!selectedItem()) return;
        e.preventDefault();
        selectedRow().classList.toggle("expanded");
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "c") {
        const item = selectedItem();
        if (!item) return;
        e.preventDefault(); onCopy(item);
      } else if (e.key === "Backspace") {
        // Deliberately re-derive the item from the live DOM selection
        // instead of trusting an index into `flat` — see selectedItem()'s
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
