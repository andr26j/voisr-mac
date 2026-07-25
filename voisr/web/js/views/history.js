"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  async function onCopy(item) {
    await V.api().copy_text(item.text);
    V.ui.toast("Copied to clipboard");
  }

  async function onDelete(item) {
    await V.api().delete_history_item(item.id);
    await render();
    V.ui.toast("Dictation deleted");
  }

  async function render() {
    const $ = V.ui.$;
    const items = await V.api().get_history();
    const container = $("#history-groups");
    $("#history-empty").hidden = items.length > 0;

    const groups = new Map();
    for (const item of items) {
      const day = V.ui.dayOf(item.ts);
      if (!groups.has(day)) groups.set(day, []);
      groups.get(day).push(item);
    }

    container.replaceChildren(...[...groups.entries()].map(([day, rows]) => {
      const list = document.createElement("ul");
      list.className = "item-list";
      list.replaceChildren(...rows.map((item) => V.ui.itemRow(item, { onCopy, onDelete })));

      const card = document.createElement("div");
      card.className = "card";
      card.append(list);

      const label = document.createElement("div");
      label.className = "day-label";
      label.textContent = day;

      const group = document.createElement("div");
      group.className = "day-group";
      group.append(label, card);
      return group;
    }));
  }

  V.views = V.views || {};
  V.views.history = { render };
})(window.Voisr);
