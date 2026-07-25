"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* ---------- helpers ---------- */

  const $ = (sel) => document.querySelector(sel);

  const ICONS = {
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>',
    arrow: '<svg class="dict-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>',
  };

  function escapeHtml(s) {
    // must also cover quotes: the result is interpolated into attribute values
    return String(s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function timeOf(ts) {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function dayOf(ts) {
    const d = new Date(ts * 1000);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  }

  let toastTimer = null;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
  }

  /* ---------- shared item rows (used by home + history) ---------- */

  function itemRow(item) {
    return `<li class="item-row" data-id="${item.id}" data-text="${escapeHtml(item.text)}">
    <div class="item-text">
      <div class="txt">${escapeHtml(item.text)}</div>
      <div class="item-meta">${timeOf(item.ts)} · ${item.words} words</div>
    </div>
    <button class="icon-btn act-copy" aria-label="Copy text">${ICONS.copy}</button>
    <button class="icon-btn danger act-delete" aria-label="Delete dictation">${ICONS.trash}</button>
  </li>`;
  }

  function bindRows(root) {
    root.querySelectorAll(".act-copy").forEach((b) => b.addEventListener("click", async (e) => {
      const row = e.currentTarget.closest(".item-row");
      await V.api().copy_text(row.dataset.text);
      toast("Copied to clipboard");
    }));
    root.querySelectorAll(".act-delete").forEach((b) => b.addEventListener("click", async (e) => {
      const row = e.currentTarget.closest(".item-row");
      if (!confirm("Delete this dictation? This can't be undone.")) return;
      await V.api().delete_history_item(row.dataset.id);
      toast("Dictation deleted");
      V.views.home.render();
      if (!$("#view-history").hidden) V.views.history.render();
    }));
  }

  V.ui = { el: $, toast, escapeHtml, timeOf, dayOf, ICONS, itemRow, bindRows };
})(window.Voisr);
