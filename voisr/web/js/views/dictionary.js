"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  async function render() {
    const $ = V.ui.el;
    const cfg = await V.api().get_config();
    const entries = Object.entries(cfg.dictionary || {});
    $("#dict-empty").hidden = entries.length > 0;
    $("#dict-list").innerHTML = entries.map(([spoken, written]) => `
    <li class="item-row" data-spoken="${V.ui.escapeHtml(spoken)}">
      <div class="item-text" style="display:flex;align-items:center;gap:10px;">
        <span class="dict-spoken">${V.ui.escapeHtml(spoken)}</span>
        ${V.ui.ICONS.arrow}
        <span class="dict-written">${V.ui.escapeHtml(written)}</span>
      </div>
      <button class="icon-btn danger act-remove" aria-label="Remove entry">${V.ui.ICONS.trash}</button>
    </li>`).join("");

    $("#dict-list").querySelectorAll(".act-remove").forEach((b) => b.addEventListener("click", async (e) => {
      const spoken = e.currentTarget.closest(".item-row").dataset.spoken;
      const cfg2 = await V.api().get_config();
      delete cfg2.dictionary[spoken];
      await V.api().set_config({ dictionary: cfg2.dictionary });
      V.ui.toast("Entry removed");
      render();
    }));
  }

  async function addEntry(e) {
    const $ = V.ui.el;
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

  V.views = V.views || {};
  V.views.dictionary = { render, addEntry };
})(window.Voisr);
