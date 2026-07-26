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
  let toastFadeTimer = null;
  let toastQueue = [];
  // True from the moment an actionable toast (Undo, etc.) is shown until it
  // is either clicked or fully dismissed. `toast()` calls `replaceChildren`
  // on the shared host, which would otherwise tear a pending action's button
  // out of the DOM before the user gets a chance to click it (History and
  // Dictionary both rely on this for undo-delete). Chosen fix: queue rather
  // than drop -- a toast requested while an action is live waits its turn
  // instead of clobbering it, so no undo opportunity is ever silently lost.
  let toastActionLive = false;

  function toast(msg, opts) {
    if (toastActionLive) {
      toastQueue.push([msg, opts]);
      return;
    }
    showToast(msg, opts);
  }

  function advanceToastQueue() {
    if (toastQueue.length) {
      const [msg, opts] = toastQueue.shift();
      showToast(msg, opts);
    }
  }

  function showToast(msg, opts) {
    const { actionLabel, onAction, ms } = opts || {};
    const host = document.getElementById("toast");
    clearTimeout(toastTimer);
    clearTimeout(toastFadeTimer);
    toastActionLive = false;
    host.replaceChildren(el("span", { text: msg }));
    if (onAction && actionLabel) {
      toastActionLive = true;
      host.append(el("button", {
        class: "toast-action", type: "button", text: actionLabel,
        on: {
          click: () => {
            clearTimeout(toastTimer);
            clearTimeout(toastFadeTimer);
            toastActionLive = false;
            host.classList.remove("in");
            host.hidden = true;
            onAction();
            advanceToastQueue();
          },
        },
      }));
    }
    host.hidden = false;
    host.classList.add("in");
    toastTimer = setTimeout(() => {
      host.classList.remove("in");
      toastFadeTimer = setTimeout(() => {
        host.hidden = true;
        toastActionLive = false;
        advanceToastQueue();
      }, 200);
    }, ms || (onAction ? 5000 : 3500));
  }

  /* Single convention for bridge calls: a thrown bridge error or an {error}
     response becomes a toast, and callers get null back. A view must never
     blank silently because Python hiccuped. */
  async function tryApi(promise, label) {
    let res;
    try { res = await promise; }
    catch (e) { toast(`${label} failed: ${e && e.message ? e.message : e}`); return null; }
    if (res && typeof res === "object" && res.error) {
      toast(`${label} failed: ${res.error}`);
      return null;
    }
    return res;
  }

  /* Build a node without string templating. Text goes through textContent and
     attributes through setAttribute, so nothing user-supplied is ever parsed
     as markup — which retires the escapeHtml-into-attribute pattern.

     NOTE on naming: this is exposed as `V.ui.el`. The querySelector shorthand
     (formerly also called `el`) has been renamed back to its original name,
     `V.ui.$`, so the two no longer collide. See call sites across main.js,
     views/home.js, views/history.js, views/dictionary.js and
     views/settings.js, all of which now do `const $ = V.ui.$;`. */
  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;   // trusted constants only
      else if (k === "on") for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
      else if (k === "data") for (const [d, dv] of Object.entries(v)) node.dataset[d] = dv;
      else node.setAttribute(k, v);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child);
    }
    return node;
  }

  function formatDuration(seconds) {
    const s = Math.max(0, Math.round(seconds || 0));
    if (s < 60) return s === 0 ? "—" : `${s}s`;
    // round to whole minutes FIRST, then split -- deriving h and m from the raw
    // seconds independently lets m round up to 60 and print "1h 60m"
    const totalM = Math.round(s / 60);
    const h = Math.floor(totalM / 60), m = totalM % 60;
    return h ? `${h}h ${m}m` : `${m}m`;
  }

  /* ---------- shared item rows (used by home + history) ---------- */

  function itemRow(item, { onCopy, onDelete }) {
    return el("li", { class: "item-row", data: { id: item.id } },
      el("div", { class: "item-text" },
        el("div", { class: "txt", text: item.text }),
        el("div", { class: "item-meta", text: `${timeOf(item.ts)} · ${item.words} words` })),
      el("button", { class: "icon-btn", "aria-label": "Copy text",
                     html: ICONS.copy, on: { click: (e) => { e.stopPropagation(); onCopy(item); } } }),
      el("button", { class: "icon-btn danger", "aria-label": "Delete dictation",
                     html: ICONS.trash, on: { click: (e) => { e.stopPropagation(); onDelete(item); } } }));
  }

  V.ui = { el, $, toast, tryApi, escapeHtml, timeOf, dayOf, formatDuration, ICONS, itemRow };
})(window.Voisr);
