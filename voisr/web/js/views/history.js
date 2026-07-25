"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const PAGE = 200;
  const SEARCH_DEBOUNCE_MS = 120;
  let query = "", shown = PAGE, items = [], flat = [], cursor = -1;
  let searchDebounceTimer = null;

  async function onCopy(item) {
    await V.api().copy_text(item.text);
    V.ui.toast("Copied to clipboard");
  }

  async function onDelete(item) {
    await V.api().delete_history_item(item.id);
    await render();
    V.ui.toast("Dictation deleted", {
      actionLabel: "Undo",
      onAction: async () => {
        await V.api().restore_history_item(item);
        await render();
        V.ui.toast("Restored");
      },
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

  /* Programmatic entry point (e.g. an external caller jumping to History
     with a preset query). Unlike onSearchInput, this DOES drive the input
     field's value (no keystroke is in flight to fight with) and applies
     immediately rather than waiting out the debounce. */
  function setQuery(q) {
    clearTimeout(searchDebounceTimer);
    const input = document.getElementById("history-search");
    if (input) input.value = q || "";
    applyQuery(q);
  }

  function matches(item) {
    const q = query.trim();
    return !q || item.text.toLowerCase().includes(q.toLowerCase());
  }

  /* Highlight without markup injection: split on the match and build nodes. */
  function highlighted(text) {
    const q = query.trim();
    if (!q) return [text];
    const lower = text.toLowerCase(), qLower = q.toLowerCase();
    const out = [];
    let at = 0, i;
    while ((i = lower.indexOf(qLower, at)) !== -1) {
      if (i > at) out.push(text.slice(at, i));
      out.push(V.ui.el("mark", { text: text.slice(i, i + q.length) }));
      at = i + q.length;
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
              txt.replaceChildren(...highlighted(item.text));
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
  async function render() {
    items = await V.api().get_history();
    renderList();
  }

  function initKeys() {
    document.getElementById("history-search")
      .addEventListener("input", (e) => onSearchInput(e.target.value));

    document.getElementById("history-more").addEventListener("click", () => {
      shown += PAGE;
      render();
    });

    document.getElementById("history-export").addEventListener("click", async () => {
      const out = await V.api().export_history("txt");
      if (out.error) return V.ui.toast(out.error);
      const saved = await V.api().save_text_file(out.text, out.suggested_name);
      if (saved.error) return V.ui.toast(saved.error);
      if (saved.ok) V.ui.toast("History exported");
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
  V.views.history = { render, setQuery, initKeys };
})(window.Voisr);
