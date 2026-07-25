"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  async function onRemove(spoken) {
    const cfg = await V.api().get_config();
    delete cfg.dictionary[spoken];
    await V.api().set_config({ dictionary: cfg.dictionary });
    V.ui.toast("Entry removed");
    render();
  }

  async function render() {
    const $ = V.ui.el;
    const cfg = await V.api().get_config();
    const entries = Object.entries(cfg.dictionary || {});
    $("#dict-empty").hidden = entries.length > 0;
    $("#dict-list").replaceChildren(
      ...entries.map(([spoken, written]) => V.ui.dictRow(spoken, written, onRemove)));
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
