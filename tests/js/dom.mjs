// Minimal DOM stand-in for node:test. No dependencies. Supports exactly what
// the Voisr web layer touches: el()'s construction surface, classList,
// dataset, hidden, listeners, and an id registry for getElementById.
/* Depth-first search for the only selector shape a node is ever asked for by
   the app: a single class, e.g. `row.querySelector(".txt")` in
   views/history.js. Anything else returns null, exactly as before.

   NOTE -- policy discrepancy vs. closest() below: querySelector silently
   returns null for an unsupported selector, while matchesSimpleSelector's
   closest() throws loudly for the same situation. Pre-existing, predates H6,
   and left as-is here: querySelector's only real call site
   (row.querySelector(".txt") in views/history.js) is always a plain class
   already, so there is no live call site whose behaviour depends on picking
   one policy over the other today. Flagging it here rather than changing it,
   since unifying the two would mean deciding which direction is "right" for
   a call site that doesn't yet exist -- an app-behaviour question, not a
   harness-fidelity one, and out of scope for H6. */
function findByClass(children, sel) {
  if (!sel.startsWith(".") || /[\s>#[:]/.test(sel.slice(1))) return null;
  const want = sel.slice(1);
  for (const kid of children) {
    if (!kid || typeof kid !== "object") continue;   // text children are strings
    const classes = String(kid.className || "").split(/\s+/);
    if (classes.includes(want) || kid.classList?.contains(want)) return kid;
    const deep = findByClass(kid.children || [], sel);
    if (deep) return deep;
  }
  return null;
}

/* Selector support for closest(): exactly the two shapes the app's real call
   sites use --

     main.js:95            e.target.closest(".theme-opt")
     settings.js:282        e.target.closest("#color-open")
     history.js:240         e.target.closest("#history-export")

   i.e. a single class or a single id, nothing compound, no combinators, no
   attribute selectors. A fake that silently matched (or silently returned
   null for) some *other* selector shape would let a future call site pass
   its own tests while doing nothing in the real DOM -- a fake that lies is
   worse than no fake. So anything outside this subset throws instead of
   returning null, exactly the way an unsupported selector would surface as
   a loud, obvious test failure rather than a quietly-wrong dismissal. */
function matchesSimpleSelector(node, sel) {
  const s = String(sel);
  if (/^\.[\w-]+$/.test(s)) {
    // Match el()'s real output: ui.js's el() sets `node.className = v`
    // directly for the "class" prop and never touches classList, so a
    // class check that only reads classList silently no-matches every
    // class the app actually builds (e.g. main.js:95's
    // `.closest(".theme-opt")` against a button from settings.js's
    // renderThemes()). Check both, exactly like findByClass above already
    // does -- the real DOM has no such distinction between the two.
    const cls = s.slice(1);
    return String(node.className || "").split(/\s+/).includes(cls)
        || !!(node.classList && node.classList.contains(cls));
  }
  if (/^#[\w-]+$/.test(s)) return node.id === s.slice(1);
  throw new Error(
    `closest(): unsupported selector "${sel}" -- the fake DOM only supports ` +
    `a single ".class" or "#id", matching the app's real call sites`);
}

export function makeNode(tag = "div") {
  const node = {
    tagName: String(tag).toUpperCase(),
    id: "",
    parent: null,
    children: [], className: "", textContent: "",
    dataset: {}, attributes: {}, style: {}, hidden: false, disabled: false,
    _innerHTML: "", _listeners: {},
    classList: {
      _set: new Set(),
      add(...cs) { cs.forEach((c) => this._set.add(c)); },
      remove(...cs) { cs.forEach((c) => this._set.delete(c)); },
      toggle(c, force) {
        const on = force ?? !this._set.has(c);
        on ? this._set.add(c) : this._set.delete(c);
        return on;
      },
      contains(c) { return this._set.has(c); },
    },
    // Real DOM reflects the "id" attribute onto `.id` (and vice versa) --
    // ui.js's el() sets id via setAttribute (never `node.id = ...` directly),
    // so without this sync `node.id` stays "" and closest("#id") silently
    // no-matches every id the app actually sets this way. Sync at the
    // source rather than special-casing closest()/matchesSimpleSelector.
    setAttribute(k, v) {
      node.attributes[k] = String(v);
      if (k === "id") node.id = String(v);
    },
    getAttribute(k) { return k in node.attributes ? node.attributes[k] : null; },
    addEventListener(type, fn) { (node._listeners[type] ??= []).push(fn); },
    removeEventListener(type, fn) {
      const arr = node._listeners[type];
      if (!arr) return;
      const i = arr.indexOf(fn);
      if (i !== -1) arr.splice(i, 1);
    },
    // .slice() so a handler that removes itself (or another handler of the
    // same type) mid-dispatch can't skip or double-fire a sibling listener.
    dispatch(type, ev = {}) {
      (node._listeners[type] || []).slice().forEach((f) => f({ target: node, ...ev }));
    },
    // Detach a kid from its previous parent's children array before
    // adopting it here -- the real DOM *moves* a node on append, it doesn't
    // duplicate it. Without this, oldParent.contains(kid) stays true (kid
    // is still spliced into oldParent.children) even though kid.parent now
    // points elsewhere.
    _adopt(k) {
      if (k && typeof k === "object" && k.parent && k.parent !== node) {
        const siblings = k.parent.children;
        const i = siblings.indexOf(k);
        if (i !== -1) siblings.splice(i, 1);
      }
      node.children.push(k);
      if (k && typeof k === "object") k.parent = node;
    },
    append(...kids) {
      kids.flat().forEach((k) => node._adopt(k));
    },
    appendChild(k) {
      node._adopt(k);
      return k;
    },
    // Detaches every outgoing child (parent = null) before adopting the new
    // ones, so a node that got replaced out of the tree can never satisfy a
    // later contains()/closest() through a stale parent link.
    replaceChildren(...kids) {
      for (const kid of node.children) {
        if (kid && typeof kid === "object" && kid.parent === node) kid.parent = null;
      }
      node.children = kids.flat();
      for (const kid of node.children) {
        if (kid && typeof kid === "object") kid.parent = node;
      }
    },
    querySelector(sel) { return findByClass(node.children, String(sel)); },
    querySelectorAll() { return []; },
    focus() {}, blur() {}, click() { node.dispatch("click"); },
    scrollIntoView() {},
    // True for the node itself (matching real Node.contains), then walks up
    // `other`'s parent chain looking for `node` -- i.e. "is `other` this node
    // or one of its descendants", never an ancestor or unrelated node.
    contains(other) {
      let cur = other;
      while (cur) {
        if (cur === node) return true;
        cur = (cur && typeof cur === "object") ? cur.parent : null;
      }
      return false;
    },
    // Walks up from the node itself (matching real Element.closest, which
    // includes the starting element) through `.parent` links, matching the
    // subset of selector syntax described at matchesSimpleSelector above.
    closest(sel) {
      let cur = node;
      while (cur) {
        if (matchesSimpleSelector(cur, sel)) return cur;
        cur = cur.parent || null;
      }
      return null;
    },
    get innerHTML() { return node._innerHTML; },
    set innerHTML(v) { node._innerHTML = String(v); },
  };
  return node;
}

/* Shared by document and window: both are real EventTargets in a browser, so
   both need to record listeners and be able to dispatch to them, mirroring
   the per-node dispatch() above. Kept as a factory rather than duplicated
   inline so the two stay identical -- a settings.js/history.js document
   listener and a main.js window listener must behave the same way here. */
function makeEventTarget() {
  const listeners = {};
  return {
    addEventListener(type, fn) { (listeners[type] ??= []).push(fn); },
    removeEventListener(type, fn) {
      const arr = listeners[type];
      if (!arr) return;
      const i = arr.indexOf(fn);
      if (i !== -1) arr.splice(i, 1);
    },
    // Test-facing: `document.dispatch("click", { target: someNode })`,
    // `window.dispatch("keydown", { key: "Escape" })` -- direct equivalent of
    // makeNode's dispatch(), for driving a listener straight from a test
    // without needing a real Event object.
    dispatch(type, ev = {}) {
      (listeners[type] || []).slice().forEach((f) => f({ target: null, ...ev }));
    },
    // App-facing: `window.dispatchEvent(new Event("voisr:new-dictation"))`
    // (settings.js, after a successful clear-history). A real Event's
    // `.type`/`.target` are accessor properties, not own-enumerable ones, so
    // it cannot be spread the way a plain test `ev` object can -- route it
    // through dispatch() by type only.
    dispatchEvent(event) {
      this.dispatch(event && event.type, {});
      return true;
    },
  };
}

export function installDom() {
  const byId = new Map();
  const doc = {
    createElement: (t) => makeNode(t),
    createTextNode: (t) => String(t),
    getElementById: (id) => byId.get(id) ?? null,
    ...makeEventTarget(),
    querySelector: () => null, querySelectorAll: () => [],
    documentElement: makeNode("html"), body: makeNode("body"),
    activeElement: null,
  };
  const win = {
    ...makeEventTarget(),
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    Voisr: {},
  };
  globalThis.window = win;
  globalThis.document = doc;
  return {
    window: win, document: doc,
    // Mirrors the real DOM: an element's `.id` reflects the id it was
    // registered/looked-up under, which is what closest("#id") matches on.
    register: (id, n) => { byId.set(id, n); if (n && typeof n === "object") n.id = id; },
  };
}
