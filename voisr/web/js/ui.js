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

  const TOAST_QUEUE_MAX = 3;

  let toastTimer = null;
  let toastFadeTimer = null;
  let toastQueue = [];
  // Bumped by every toast() call, shown or queued. The action-click handler
  // samples it around onAction() so it can tell whether the action put up its
  // own message; see the comment there.
  let toastSeq = 0;
  // True from the moment an actionable toast (Undo, etc.) is shown until it
  // is either clicked or fully dismissed. `toast()` calls `replaceChildren`
  // on the shared host, which would otherwise tear a pending action's button
  // out of the DOM before the user gets a chance to click it (History, Home
  // and Dictionary all rely on this for undo-delete). So an ordinary toast
  // raised while an action is live waits its turn instead of clobbering it.
  // A *second removal* is the one thing that does not wait: it upgrades the
  // live undo in place rather than queueing another one -- see offerUndo.
  let toastActionLive = false;
  /* The live toast's one-shot resolve notification (opts.onResolve), or null.
     Taken-and-nulled by resolveLiveToast(), which every exit path calls --
     click, expiry/fade, replacement by a newer toast, and eviction from the
     queue. So an owner that holds state on the toast's behalf (the undo
     batches below) learns exactly once that its toast is off screen, and no
     path can notify twice. */
  let toastResolve = null;

  function hasAction(opts) {
    return !!(opts && opts.onAction && opts.actionLabel);
  }

  function resolveLiveToast() {
    const fn = toastResolve;
    toastResolve = null;
    if (fn) fn();
  }

  function toast(msg, opts) {
    toastSeq += 1;
    if (toastActionLive) {
      queueToast(msg, opts);
      return;
    }
    showToast(msg, opts);
  }

  function queueToast(msg, opts) {
    toastQueue.push([msg, opts]);
    /* Cap the backlog. Queued toasts drain one at a time at 3.5s each, so an
       unbounded queue turns a burst of failures (several tryApi calls losing
       the bridge in one render) into a minutes-long parade of stale messages
       long after the user has moved on. The newest message is the most
       relevant, so the oldest goes -- but only among toasts that carry no
       action.

       DO NOT "simplify" this back to a plain shift-the-oldest. Read this
       first:

         - Deleting is a hard delete on the Python side. The queued Undo's
           closure is the only handle on the deleted rows, and evicting an
           entry calls its onResolve, which empties the batch. Dropping a
           queued undo is therefore silent, permanent data loss with no
           message to the user.
         - Chatter alone gets you there. Three plain toasts need not be three
           user actions: home.js's "Permissions failed to refresh" and every
           tryApi failure toast fire on their own, so one flaky-bridge moment
           emits several inside a five-second window with nobody clicking
           anything.

       This is NOT the unbounded exemption the H4 review rejected. What made
       that one unacceptable was that N deletes queued N undos, so the exempt
       population grew without limit. Coalescing fixed that at the source:
       offerUndo gives a batch at most one queue slot (it upgrades the live
       toast in place, or rewrites its own queued entry in place), and there
       are exactly two batches, one of which owns the live toast whenever
       anything is queueing at all. So at most one undo is ever queued.

       The exemption also does not lengthen the backlog: a skipped entry is
       still *counted* against TOAST_QUEUE_MAX, so the queue only exceeds the
       cap when every entry in it is an action toast -- and at most one can
       be. The loop terminates for the same reason: it stops as soon as there
       is no droppable entry left.

       An evicted entry is resolved exactly once and then gone from the
       queue, so it can never be shown or resolved again. */
    while (toastQueue.length > TOAST_QUEUE_MAX) {
      const i = toastQueue.findIndex(([, o]) => !hasAction(o));
      if (i === -1) break;
      const [, dropped] = toastQueue.splice(i, 1)[0];
      if (dropped && dropped.onResolve) dropped.onResolve();
    }
  }

  function advanceToastQueue() {
    if (toastQueue.length) {
      const [msg, opts] = toastQueue.shift();
      showToast(msg, opts);
    }
  }

  /* `upgrade` is set only by offerUndo when it re-renders the toast that is
     already on screen for the same batch. That is a continuation of the live
     toast, not a resolution of it, so the resolve notification is suppressed
     (the batch must survive) while the dismissal timer below is restarted
     from scratch (the window is measured from the most recent delete). */
  function showToast(msg, opts, upgrade) {
    const { actionLabel, onAction, ms } = opts || {};
    const withAction = hasAction(opts);
    const host = document.getElementById("toast");
    clearTimeout(toastTimer);
    clearTimeout(toastFadeTimer);
    if (!upgrade) resolveLiveToast();
    toastActionLive = false;
    toastResolve = (opts && opts.onResolve) || null;
    host.replaceChildren(el("span", { text: msg }));
    if (withAction) {
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
            /* Draining the queue is how this action hands the host on -- but
               only if the action didn't claim the host itself. A callback
               that toasts synchronously (say "Restored") has already taken
               over, and draining on top of it would replace a confirmation
               the user just earned with a stale queued message. Whatever it
               put up drains the queue when its own window ends, so nothing is
               stranded either way. Comparing the counter rather than deferring
               keeps this ordering exact -- no timer, no delay.

               KNOWN RESIDUE, cross-entity only. The real callers are async
               (runUndo awaits restore + refresh), so the counter is unchanged
               here and the drain runs. If what it promotes is the *other*
               batch's queued undo, the user's click reads as "nothing
               happened": the host immediately shows "Dictation deleted" with
               a live Undo, and the confirmation this click earned
               ("Restored 2 entries") queues behind it for the promoted
               undo's full 5s window. Clicking again then undoes a different
               entity.

               Exact reachability: remove a dictionary entry, switch views
               within 5s, delete a dictation (its undo queues behind the live
               entry undo -- different batch, so no upgrade), then click Undo.
               The two views cannot be on screen at once, so it needs a
               deliberate cross-view sequence inside one 5s window.

               Not fixed, deliberately. The obvious fix -- skip action toasts
               when draining on click -- breaks the invariant that keeps this
               queue provably drainable (host idle => queue empty; every
               advance happens at a fade-end or here), and would strand a
               queued undo, unshown and unresolved, whenever the clicked
               action toasts nothing. An unresolved batch is worse than this:
               a delete minutes later coalesces onto stale entries. Since the
               cap no longer evicts queued undos, this residue costs ordering
               and clarity, not data. */
            const seq = toastSeq;
            onAction();
            resolveLiveToast();
            if (toastSeq === seq) advanceToastQueue();
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
        resolveLiveToast();
        advanceToastQueue();
      }, 200);
    }, ms || (withAction ? 5000 : 3500));
  }

  /* ---------- coalescing undo (shared by home, history and dictionary) ----

     Removal is a hard delete on the Python side; the only recovery handle is
     the closure the live toast holds. Queueing one undo per removal made that
     handle unusable in a burst: FIFO surfaced them oldest-first (the mistake
     you want back is the one that appears last), every one of them read the
     same text with no row identity, and clicking one promoted the *next* undo
     into the host so the confirmation was buried -- indistinguishable from
     "my click didn't register", and clicking again undid a different row.

     Instead there is one live undo, always current. A removal that lands
     while an undo is live upgrades it in place:

        1 -> "Dictation deleted"    Undo restores that one
        2 -> "2 dictations deleted" Undo restores both
        N -> "N dictations deleted" Undo restores all N

     Consequence, deliberate: a batch occupies at most one toast slot, so the
     number of undos in flight is bounded by the number of batches (two)
     rather than by the number of deletes. That bound is what makes the cap's
     skip-actions rule above safe; it is not a claim that an undo is never
     queued. One *can* be -- see the cross-entity note at the click drain. */

  const undoBatches = new Map();

  /* The last failure message tryApi put on screen. runUndo reads it so that a
     batch where *nothing* came back can report its count and the reason in
     one line, instead of replacing the reason with a bare count (or, as
     before, saying nothing at all and letting one reason stand for N lost
     rows). Cleared at the start of every undo run, so a stale reason from
     minutes ago can never be attributed to this one. */
  let lastApiFailure = null;

  function batchFor(key) {
    let batch = undoBatches.get(key);
    if (!batch) {
      batch = { entries: [], spec: null };
      /* One stable function per batch, used for two things: it is the
         resolve notification, and its identity is how offerUndo recognises
         its own toast among whatever else is on screen. */
      batch.resolve = () => { batch.entries = []; };
      undoBatches.set(key, batch);
    }
    return batch;
  }

  /* entry: { restore: () => Promise<truthy on success>, refresh: () => Promise }
     spec:  { message(n) -> string, done(ok, n) -> string|null } */
  function offerUndo(key, spec, entry) {
    const batch = batchFor(key);
    batch.spec = spec;
    batch.entries.push(entry);
    const msg = spec.message(batch.entries.length);
    const opts = {
      actionLabel: "Undo",
      onAction: () => runUndo(batch),
      onResolve: batch.resolve,
    };
    if (toastActionLive && toastResolve === batch.resolve) {
      toastSeq += 1;
      showToast(msg, opts, true);   // upgrade in place, timer restarted
      return;
    }
    /* Only reachable when a *different* batch's undo owns the host (removing
       a dictionary entry within seconds of deleting a dictation, i.e. after
       switching views). This batch's toast is then waiting in the queue --
       update it in place rather than adding a second one, so the queue still
       never holds two undos for the same batch. */
    const queued = toastQueue.find(([, o]) => o && o.onResolve === batch.resolve);
    if (queued) {
      toastSeq += 1;
      queued[0] = msg;
      queued[1] = opts;
      return;
    }
    toast(msg, opts);
  }

  async function runUndo(batch) {
    /* Take the entries synchronously. The click handler resolves the toast
       immediately after calling this, and resolving empties the batch. */
    const entries = batch.entries;
    const spec = batch.spec;
    batch.entries = [];
    if (!entries.length) return;

    lastApiFailure = null;
    let ok = 0;
    const refreshes = [];
    for (const entry of entries) {
      if (await entry.restore()) ok += 1;
      if (entry.refresh && !refreshes.includes(entry.refresh)) refreshes.push(entry.refresh);
    }
    // Capture before the refreshes run: a refresh can fail too, and its
    // reason is not why the restore did not come back.
    const reason = lastApiFailure;
    // Re-render either way: on a partial or total failure the list must show
    // what is really there, not what we hoped.
    for (const refresh of refreshes) await refresh();

    /* Never claim more than actually came back. `done` returns null when
       there is nothing honest left to add -- with a single row nothing
       restored, tryApi's own failure toast is the whole story and must not be
       replaced. */
    const msg = spec.done(ok, entries.length, reason);
    if (msg) toast(msg);
  }

  /* Nothing came back at all. At n === 1, tryApi's own toast already says
     everything true and stays the last word (null = say nothing). At n > 1 it
     names one reason while N rows are gone, which under-reports -- so restate
     it with the count rather than in place of it. */
  function noneRestored(n, reason, noun) {
    if (n === 1) return null;
    return reason ? `${reason} (0 of ${n} restored)` : `No ${noun} restored (0 of ${n})`;
  }

  const DICTATION_UNDO = {
    message: (n) => (n === 1 ? "Dictation deleted" : `${n} dictations deleted`),
    done: (ok, n, reason) => (ok === n ? (n === 1 ? "Restored" : `Restored ${n} dictations`)
                    : ok ? `Restored ${ok} of ${n} dictations`
                    : noneRestored(n, reason, "dictations")),
  };

  const ENTRY_UNDO = {
    message: (n) => (n === 1 ? "Entry removed" : `${n} entries removed`),
    // A single clean restore stays silent, as it always has: the row
    // reappearing in the list is the confirmation.
    done: (ok, n, reason) => (ok === n ? (n === 1 ? null : `Restored ${n} entries`)
                    : ok ? `Restored ${ok} of ${n} entries`
                    : noneRestored(n, reason, "entries")),
  };

  /* home.js and history.js render the same rows and must offer the same undo,
     so they share one batch ("dictation") and one set of wordings. */
  const offerDictationUndo = (entry) => offerUndo("dictation", DICTATION_UNDO, entry);
  const offerEntryUndo = (entry) => offerUndo("entry", ENTRY_UNDO, entry);

  /* Single convention for bridge calls: a thrown bridge error or an {error}
     response becomes a toast, and callers get null back. A view must never
     blank silently because Python hiccuped. */
  async function tryApi(promise, label) {
    // Show the reason and remember it, so runUndo can report a whole batch
    // that failed with its count without losing the reason.
    const fail = (m) => { lastApiFailure = m; toast(m); };
    let res;
    try { res = await promise; }
    catch (e) { fail(`${label} failed: ${e && e.message ? e.message : e}`); return null; }
    if (res && typeof res === "object" && res.error) {
      fail(`${label} failed: ${res.error}`);
      return null;
    }
    return res;
  }

  /* Build a node without string templating. Text goes through textContent and
     attributes through setAttribute, so nothing user-supplied is ever parsed
     as markup — which retired the escape-then-interpolate-into-an-attribute
     pattern this file used to need (the `escapeHtml` helper it required is
     gone, along with its last call site).

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

  V.ui = { el, $, toast, tryApi, timeOf, dayOf, formatDuration, ICONS, itemRow,
           offerDictationUndo, offerEntryUndo };
})(window.Voisr);
