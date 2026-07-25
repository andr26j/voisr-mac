"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  async function render() {
    const $ = V.ui.el;
    const items = await V.api().get_history();
    const container = $("#history-groups");
    $("#history-empty").hidden = items.length > 0;

    const groups = new Map();
    for (const item of items) {
      const day = V.ui.dayOf(item.ts);
      if (!groups.has(day)) groups.set(day, []);
      groups.get(day).push(item);
    }

    container.innerHTML = [...groups.entries()].map(([day, rows]) => `
    <div class="day-group">
      <div class="day-label">${day}</div>
      <div class="card"><ul class="item-list">${rows.map(V.ui.itemRow).join("")}</ul></div>
    </div>`).join("");
    V.ui.bindRows(container);
  }

  V.views = V.views || {};
  V.views.history = { render };
})(window.Voisr);
