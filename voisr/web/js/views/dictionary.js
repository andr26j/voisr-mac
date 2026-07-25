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
      const cfg = await V.api().get_config();
      cfg.dictionary[spoken] = next;
      await V.api().set_config({ dictionary: cfg.dictionary });
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
    const cfg = await V.api().get_config();
    delete cfg.dictionary[spoken];
    await V.api().set_config({ dictionary: cfg.dictionary });
    render();
    V.ui.toast("Entry removed", {
      actionLabel: "Undo",
      onAction: async () => {
        const c = await V.api().get_config();
        c.dictionary[spoken] = written;
        await V.api().set_config({ dictionary: c.dictionary });
        render();
      },
    });
  }

  async function render() {
    const cfg = await V.api().get_config();
    const all = Object.entries(cfg.dictionary || {});
    const search = document.getElementById("dict-search");
    search.hidden = all.length < SEARCH_AT;
    const shown = all.filter(([s, w]) =>
      !filter || s.includes(filter) || w.toLowerCase().includes(filter));
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
    const cfg = await V.api().get_config();
    const dict = cfg.dictionary || {};
    dict[spoken] = written;
    await V.api().set_config({ dictionary: dict });
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
