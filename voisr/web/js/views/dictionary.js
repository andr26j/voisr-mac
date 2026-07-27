"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const SEARCH_AT = 10;
  let filter = "";

  function entryRow(spoken, written) {
    const value = V.ui.el("span", {
      class: "dict-written", text: written,
      contenteditable: "plaintext-only", role: "textbox",
      "aria-label": `Replacement for ${spoken}`,
    });

    // Enter and blur both funnel through this single commit path (Enter
    // just blurs) so an edit is never saved twice.
    async function commit() {
      const next = value.textContent.trim();
      if (!next || next === written) { value.textContent = written; return; }
      // Read fresh rather than reuse a closed-over `cfg` -- another commit
      // (or a remove/undo) may have written to config.json since this row
      // was rendered, and a stale copy here would clobber it.
      const cfg = await V.ui.tryApi(V.api().get_config(), "Load dictionary");
      if (!cfg) { value.textContent = written; return; }
      cfg.dictionary = cfg.dictionary || {};
      cfg.dictionary[spoken] = next;
      // A failed write must not leave the new text sitting in the row: the
      // user would read that as saved. Snap the cell back to what is
      // actually stored, and let tryApi's toast say why.
      const saved = await V.ui.tryApi(
        V.api().set_config({ dictionary: cfg.dictionary }), "Save dictionary");
      if (!saved) { value.textContent = written; return; }
      V.ui.toast(`Updated: ${spoken} → ${next}`);
      render();
    }

    value.addEventListener("blur", commit);
    value.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); value.blur(); }
      else if (e.key === "Escape") { value.textContent = written; value.blur(); }
    });

    return V.ui.el("li", { class: "item-row" },
      V.ui.el("div", { class: "item-text dict-pair" },
        V.ui.el("span", { class: "dict-spoken", text: spoken }),
        V.ui.el("span", { class: "dict-arrow", html: V.ui.ICONS.arrow }),
        value),
      V.ui.el("button", {
        class: "icon-btn danger", "aria-label": "Remove entry",
        html: V.ui.ICONS.trash,
        on: { click: () => removeEntry(spoken, written) },
      }));
  }

  async function removeEntry(spoken, written) {
    const cfg = await V.ui.tryApi(V.api().get_config(), "Load dictionary");
    if (!cfg) return;
    cfg.dictionary = cfg.dictionary || {};
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

  /* One data source, so the same rule as history's render(): on a failed
     load keep the last good list rather than blanking the pane. */
  async function render() {
    try {
      await renderInner();
    } catch (e) {
      V.ui.toast("Dictionary failed to load");
    }
  }

  async function renderInner() {
    const cfg = await V.ui.tryApi(V.api().get_config(), "Load dictionary");
    if (!cfg) return;
    const all = Object.entries(cfg.dictionary || {});
    const search = document.getElementById("dict-search");
    search.hidden = all.length < SEARCH_AT;
    // A hidden input must not leave a stale filter in effect -- there is no
    // longer any visible control to clear it, so the user would be looking
    // at a filtered list with no way to tell (or undo) why entries are
    // missing. Reset both the module's filter state and the input's own
    // value so a later re-show (the list grows past SEARCH_AT again) starts
    // clean rather than resurrecting whatever was last typed.
    if (search.hidden) { filter = ""; search.value = ""; }
    const shown = all.filter(([s, w]) =>
      !filter || s.toLowerCase().includes(filter) || w.toLowerCase().includes(filter));
    document.getElementById("dict-empty").hidden = shown.length > 0;
    document.getElementById("dict-list")
      .replaceChildren(...shown.map(([s, w]) => entryRow(s, w)));
  }

  async function addEntry(e) {
    const $ = V.ui.$;
    e.preventDefault();
    const spoken = $("#dict-spoken").value.trim().toLowerCase();
    const written = $("#dict-written").value.trim();
    if (!spoken || !written) return;
    const cfg = await V.ui.tryApi(V.api().get_config(), "Load dictionary");
    if (!cfg) return;
    const dict = cfg.dictionary || {};
    dict[spoken] = written;
    // Only clear the form once the entry is really stored — wiping the two
    // fields on a failed write would throw away what the user just typed.
    if (!await V.ui.tryApi(
      V.api().set_config({ dictionary: dict }), "Save dictionary")) return;
    $("#dict-spoken").value = "";
    $("#dict-written").value = "";
    V.ui.toast(`Added: ${spoken} → ${written}`);
    render();
  }

  function initSearch() {
    document.getElementById("dict-search").addEventListener("input", (e) => {
      filter = e.target.value.trim().toLowerCase();
      render();
    });
  }

  V.views = V.views || {};
  V.views.dictionary = { render, addEntry, initSearch };
})(window.Voisr);
