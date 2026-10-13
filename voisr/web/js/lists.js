"use strict";
window.Voisr = window.Voisr || {};

/* Keyed list updates with motion. Lists used to be rebuilt with
   replaceChildren on every refresh, so every row faded in again after each
   dictation. Rows are now matched by key and survivors keep their node (and
   with it focus, the expanded state, and any animation in flight). Only
   what changed moves:

     deleted   a row whose item is gone fades out (160 ms, 8 px left), first
     moved     survivors slide to their new place (240 ms FLIP)
     landed    a new or restored row rises in (220 ms from -6 px) with a
               1.6 s accent wash, so you see where it went
     fill      a row that was already in the data but out of view (the next
               dictation sliding into Home's top five) rises in from +6 px
     pushed    a row still in the data but past the end of the view fades
               under the list's edge while the height holds still

   The first fill of an empty list keeps the old 20 ms stagger instead.
   Reduced motion: no sliding; entrances and exits are 120 ms fades, and
   the wash stays (it is colour, not movement). Without element.animate
   (the Node test DOM) every update is applied instantly. */
window.Voisr.lists = (function () {
  const ENTER_MS = 220, MOVE_MS = 240, LEAVE_MS = 160, FADE_MS = 120, STAGGER_MS = 400;
  const EASE = "cubic-bezier(.2, .8, .2, 1)";
  const EASE_IN = "cubic-bezier(.4, 0, 1, 1)";

  function reduced() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function canAnimate(el) {
    return !!el && typeof el.animate === "function";
  }

  const keyOf = (n) => (n && n.dataset ? n.dataset.key : undefined);
  const keyed = (list) => Array.from(list.children).filter((n) => keyOf(n) !== undefined);
  const finished = (anim) => (anim && anim.finished ? anim.finished.catch(() => {}) : Promise.resolve());

  /* Replays the 20 ms row stagger (base.css) on the rows already there. */
  function stagger(list) {
    list.classList.remove("is-staggering");
    void list.offsetWidth;
    list.classList.add("is-staggering");
    clearTimeout(list._staggerTimer);
    list._staggerTimer = setTimeout(() => list.classList.remove("is-staggering"), STAGGER_MS);
  }

  /* Keyed in-place update, no motion. entries: [{ key, sig, build }] in the
     wanted order; a survivor whose sig changed is rebuilt. Rows that are no
     longer wanted are removed, unless opts.stillExists(key) says they only
     fell out of view: those are returned as `pushed`, still in the DOM. */
  function apply(list, entries, opts = {}) {
    const current = new Map(keyed(list).map((n) => [keyOf(n), n]));
    const want = new Set(entries.map((e) => e.key));
    const pushed = [];
    for (const [k, n] of current) {
      if (want.has(k)) continue;
      if (opts.stillExists && opts.stillExists(k)) pushed.push(n);
      else n.remove();
    }
    const entered = [];
    entries.forEach((e, i) => {
      let node = current.get(e.key);
      let kind = null;
      if (node && node.dataset.sig !== String(e.sig)) {
        node.remove();
        node = null;
        kind = "landed";  // changed in place: wash it so the edit is visible
      }
      if (!node) {
        node = e.build();
        node.dataset.key = e.key;
        node.dataset.sig = String(e.sig);
        kind = kind || (opts.wasKnown && opts.wasKnown(e.key) ? "fill" : "landed");
        entered.push([node, kind]);
      }
      const at = list.children[i];
      if (at !== node) list.insertBefore(node, at || null);
    });
    return { entered, pushed };
  }

  function measure(elements) {
    const tops = new Map();
    for (const el of elements) {
      if (el && typeof el.getBoundingClientRect === "function") tops.set(el, el.getBoundingClientRect().top);
    }
    return tops;
  }

  function play(tops) {
    for (const [el, top] of tops) {
      if (!canAnimate(el) || el.isConnected === false) continue;
      const dy = top - el.getBoundingClientRect().top;
      if (Math.abs(dy) > 0.5) {
        el.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }],
                   { duration: MOVE_MS, easing: EASE });
      }
    }
  }

  function wash(node) {
    node.classList.remove("is-landed");
    void node.offsetWidth;
    node.classList.add("is-landed");
    // only the wash's own end: the row-in stagger, or any animation in the
    // row bubbling up, used to strip it early
    if (!node._washEnd) {
      node._washEnd = (e) => { if (e.animationName === "wash") node.classList.remove("is-landed"); };
      node.addEventListener("animationend", node._washEnd);
    }
  }

  function enter(node, kind) {
    if (kind === "landed") wash(node);
    if (!canAnimate(node)) return;
    if (reduced()) {
      node.animate([{ opacity: 0 }, { opacity: 1 }], { duration: FADE_MS });
      return;
    }
    const from = kind === "fill" ? 6 : -6;
    node.animate([{ opacity: 0, transform: `translateY(${from}px)` }, { opacity: 1, transform: "none" }],
                 { duration: ENTER_MS, easing: EASE });
  }

  /* Fades nodes out (deleted rows); resolves once they have left. */
  function leave(nodes) {
    const still = reduced();
    return Promise.all(nodes.map((n) => {
      n.classList.add("is-leaving");  // out of reach: History's keyboard skips it
      n.style.pointerEvents = "none";
      if (!canAnimate(n)) return Promise.resolve();
      const frames = still
        ? [{ opacity: 1 }, { opacity: 0 }]
        : [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(-8px)" }];
      return finished(n.animate(frames, { duration: still ? FADE_MS : LEAVE_MS, easing: EASE_IN, fill: "forwards" }));
    }));
  }

  /* Rows pushed past the end: fade under the edge, then go. Each gives up
     its key first, so a later update that wants that item back builds a
     fresh row instead of reusing one that is about to remove itself. */
  function fadeAway(nodes) {
    for (const n of nodes) {
      delete n.dataset.key;
      n.classList.add("is-leaving");
      n.style.pointerEvents = "none";
      const anim = canAnimate(n) ? n.animate([{ opacity: 1 }, { opacity: 0 }], { duration: LEAVE_MS, fill: "forwards" }) : null;
      finished(anim).then(() => n.remove());
    }
  }

  /* Runs `task` now when `owner` is idle (so an instant update stays
     synchronous), otherwise after whatever is still animating there. A
     task returns a promise only while it animates; until that settles the
     owner is busy. */
  function serial(owner, task) {
    const run = () => {
      owner._listPending = (owner._listPending || 0) + 1;
      const done = () => { owner._listPending -= 1; };
      let result;
      try { result = task(); } catch (e) { done(); throw e; }
      if (result && typeof result.then === "function") return result.finally(done);
      done();
      return Promise.resolve();
    };
    if (!owner._listPending) {
      owner._listTail = run();
    } else {
      owner._listTail = owner._listTail.then(run, run);
    }
    return owner._listTail;
  }

  /* A flat list, choreographed. Updates to one list run one after another,
     so a refresh that lands mid-animation waits its turn. */
  function sync(list, entries, opts = {}) {
    return serial(list, () => syncNow(list, entries, opts));
  }

  function syncNow(list, entries, opts) {
    const hadRows = keyed(list).length > 0;
    const motion = opts.animate !== false && hadRows && canAnimate(list);
    if (!motion) {
      const { pushed } = apply(list, entries, opts);
      pushed.forEach((n) => n.remove());
      if (!hadRows && entries.length && opts.animate !== false) stagger(list);
      return undefined;
    }
    return choreograph(list, entries, opts);
  }

  async function choreograph(list, entries, opts) {
    const want = new Set(entries.map((e) => e.key));
    const out = keyed(list).filter((n) => !want.has(keyOf(n)));
    const deleted = out.filter((n) => !(opts.stillExists && opts.stillExists(keyOf(n))));
    if (deleted.length) await leave(deleted);

    const before = reduced() ? new Map() : measure(keyed(list).filter((n) => !deleted.includes(n)));
    const hold = out.length > deleted.length && typeof list.offsetHeight === "number";
    if (hold) {
      list.style.height = `${list.offsetHeight}px`;
      list.style.overflow = "clip";
    }
    const { entered, pushed } = apply(list, entries, opts);
    play(before);
    for (const [node, kind] of entered) enter(node, kind);
    fadeAway(pushed);
    if (hold) {
      setTimeout(() => { list.style.height = ""; list.style.overflow = ""; }, MOVE_MS + 20);
    }
  }

  return { sync, serial, apply, measure, play, enter, wash, leave, fadeAway, stagger, reduced, canAnimate };
})();
