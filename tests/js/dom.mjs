// Minimal DOM stand-in for node:test. No dependencies. Supports exactly what
// the Voisr web layer touches: el()'s construction surface, classList,
// dataset, hidden, listeners, and an id registry for getElementById.
/* Depth-first descendant search, shared by node.querySelector and
   document.querySelector's class branch. Matching goes through
   matchesSimpleSelector, so every selector-taking method in this file --
   closest(), node.querySelector, document.querySelector -- accepts exactly
   the same subset and matches it exactly the same way. The selector is
   validated by the caller (see checkSelector), so this walk never has to
   decide what an unsupported one means; by the time it runs, there isn't one.

   Descendants only, never the starting node: that is the one place these
   methods legitimately differ, and it mirrors the real DOM, where closest()
   includes the element it was called on and querySelector does not. */
function findDescendant(children, sel) {
  for (const kid of children) {
    if (!kid || typeof kid !== "object") continue;   // text children are strings
    if (matchesSimpleSelector(kid, sel)) return kid;
    const deep = findDescendant(kid.children || [], sel);
    if (deep) return deep;
  }
  return null;
}

/* The all-matches counterpart, shared by node.querySelectorAll and
   document.querySelectorAll's class branch, exactly as findDescendant is
   shared by the singular pair. Same walk, same matcher, same validated
   subset -- so `querySelectorAll(sel)[0]` and `querySelector(sel)` can never
   disagree about what matches or in what order.

   Document order: a node is pushed before its own descendants are walked,
   which is what a real NodeList gives you. history.js indexes the result by
   cursor position (`rows[cursor]`), so the order is load-bearing, not
   cosmetic. */
function collectDescendants(children, sel, out) {
  for (const kid of children) {
    if (!kid || typeof kid !== "object") continue;   // text children are strings
    if (matchesSimpleSelector(kid, sel)) out.push(kid);
    collectDescendants(kid.children || [], sel, out);
  }
  return out;
}

/* The supported selector subset, shared by every selector-taking method here
   -- closest(), node.querySelector, document.querySelector and both
   querySelectorAll's -- so none of them can drift apart from the others.
   Exactly the two shapes the app's real call sites use --

     main.js:95            e.target.closest(".theme-opt")
     settings.js:282        e.target.closest("#color-open")
     history.js:240         e.target.closest("#history-export")
     history.js             row.querySelector(".txt")
     ui.js:6               document.querySelector(sel), i.e. V.ui.$ -- always
                           a single "#id" at every call site across home.js,
                           settings.js, history.js and dictionary.js
     main.js:13,14         document.querySelectorAll(".view") / (".nav-item")

   i.e. a single class or a single id, nothing compound, no combinators, no
   attribute selectors. The app does have three querySelectorAll call sites
   outside this subset -- history.js:87,98,251 ("#history-groups .item-row")
   and main.js:76 ("[data-view]") -- and they throw here rather than being
   guessed at, for exactly the reason that follows.

   A fake that silently matched (or silently returned null for) some *other*
   selector shape would let a future call site pass its own tests while doing
   nothing in the real DOM -- a fake that lies is worse than no fake. So anything outside this subset throws instead of
   returning null, exactly the way an unsupported selector would surface as
   a loud, obvious test failure rather than a quietly-wrong dismissal. */
const CLASS_SEL = /^\.[\w-]+$/;
const ID_SEL = /^#[\w-]+$/;

function unsupportedSelector(fn, sel) {
  return new Error(
    `${fn}: unsupported selector "${sel}" -- the fake DOM only supports ` +
    `a single ".class" or "#id", matching the app's real call sites`);
}

/* Validate up front, before any walking. A search that only rejected a bad
   selector when it happened to test it against a node would throw or not
   depending on the shape of the tree -- an empty node would accept anything
   and answer null. */
function checkSelector(fn, sel) {
  const s = String(sel);
  if (!CLASS_SEL.test(s) && !ID_SEL.test(s)) throw unsupportedSelector(fn, sel);
  return s;
}

function matchesSimpleSelector(node, sel) {
  const s = String(sel);
  if (CLASS_SEL.test(s)) {
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
  if (ID_SEL.test(s)) return node.id === s.slice(1);
  throw unsupportedSelector("closest()", sel);
}

export function makeNode(tag = "div") {
  const node = {
    tagName: String(tag).toUpperCase(),
    id: "",
    parent: null,
    children: [], className: "", textContent: "",
    dataset: {}, attributes: {}, hidden: false, disabled: false,
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
    /* The app writes style two ways and reads it back through neither: plain
       properties (`swatch.style.background = hex`, `thumb.style.left = ...`)
       and custom properties (settings.js's setColorUi does
       `preview.style.setProperty("--mb-color", hex)` and removeProperty for
       the reset). A bare `{}` covers only the first, so setColorUi threw on
       the second and could not be reached from Node at all. Both spellings
       land in the same object, exactly as a real CSSStyleDeclaration lets you
       reach a longhand either way -- so a test can assert on
       `style.background` and `style.getPropertyValue("--mb-color")` alike. */
    style: (() => {
      const style = {
        setProperty(k, v) { style[k] = String(v); },
        // Real removeProperty returns the old value, and "" for an unset one.
        removeProperty(k) {
          const prev = k in style ? style[k] : "";
          delete style[k];
          return prev;
        },
        getPropertyValue(k) { return k in style ? style[k] : ""; },
      };
      return style;
    })(),
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
    querySelector(sel) {
      return findDescendant(node.children, checkSelector("node.querySelector()", sel));
    },
    /* Was `() => []` for every selector, supported or not -- the last place
       this fake still lied. `[]` is a perfectly plausible "nothing matched",
       so a future test of history.js's arrow-key navigation or Backspace-to-
       delete would have passed while selectedItem() returned null every time
       and moveCursor() toggled nothing. Same subset and the same loud throw
       as querySelector/closest, for the reason spelled out at CLASS_SEL.

       Returns a real Array, which covers everything the app asks of a
       NodeList: forEach (main.js:13,14,76), indexing (history.js:251) and
       .length. */
    querySelectorAll(sel) {
      return collectDescendants(
        node.children, checkSelector("node.querySelectorAll()", sel), []);
    },
    focus() {}, blur() {}, click() { node.dispatch("click"); },
    scrollIntoView() {},
    /* An all-zero rect is not a placeholder -- it is what a real browser
       reports for an element that is detached or display:none, which every
       node here is. settings.js's pick() reads it to turn client coordinates
       into a hue/saturation, so a test that cares about wheel geometry must
       assign a real rect (see mountColorWheel in settings.test.mjs);
       otherwise every pick lands dead centre, exactly as it would in a
       browser against an unlaid-out canvas. */
    getBoundingClientRect() {
      return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
    },
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
    /* `V.ui.$` (ui.js:6) *is* this function, and it is how every view reaches
       every element it touches. While this was a flat `() => null`, calling
       any of those functions from Node threw on the first dereference of the
       null it handed back (`cv.addEventListener is not a function` for
       `$("#color-wheel")` in settings.js's initColorWheel), so render paths
       like home.js's render() and the colour wheel could only be checked in a
       browser.

       Supports the same subset as closest() -- a single "#id" or ".class" --
       and throws on anything else for the same reason, spelled out at
       CLASS_SEL above. An id resolves through the byId registry, i.e. exactly
       what getElementById returns: the app reads the same element both ways
       (home.js uses getElementById for #view-home and V.ui.$ for everything
       else), so the two must never be able to disagree. A class searches the
       document tree only -- a node that was register()ed but never appended
       is not in the document, and a real querySelector would not find it
       either. */
    querySelector(sel) {
      const s = checkSelector("document.querySelector()", sel);
      if (ID_SEL.test(s)) return byId.get(s.slice(1)) ?? null;
      // Both roots: the real documentElement contains body, but here they
      // are two independent fake trees, so neither can be assumed to reach
      // the other. Unlike an element's querySelector, the document's roots
      // are themselves candidates -- `document.querySelector(".x")` matches
      // <html class="x"> in a browser.
      for (const root of [doc.documentElement, doc.body]) {
        if (!root) continue;
        if (matchesSimpleSelector(root, s)) return root;
        const hit = findDescendant(root.children, s);
        if (hit) return hit;
      }
      return null;
    },
    /* Mirrors document.querySelector above, all-matches: an "#id" resolves
       through the same byId registry (so it answers 0 or 1 element, exactly
       as a real document does with unique ids), and a ".class" walks both
       roots with the roots themselves as candidates.

       NOTE for whoever tests the remaining call sites. Three of the six live
       ones use shapes outside the supported subset and so throw here rather
       than answer: history.js:87,98,251 ("#history-groups .item-row", a
       descendant combinator) and main.js:76 ("[data-view]", an attribute
       selector). That is deliberate and is the whole policy of this file --
       a loud throw is the honest answer for a selector the fake cannot
       really evaluate, and it is what a test of Backspace-to-delete or the
       nav binding should hit. Widening the grammar is the fix if and when
       one of those is written; quietly answering [] is not. main.js's
       show() (".view", ".nav-item") is inside the subset and works today. */
    querySelectorAll(sel) {
      const s = checkSelector("document.querySelectorAll()", sel);
      if (ID_SEL.test(s)) {
        const hit = byId.get(s.slice(1));
        return hit ? [hit] : [];
      }
      const out = [];
      for (const root of [doc.documentElement, doc.body]) {
        if (!root) continue;
        if (matchesSimpleSelector(root, s)) out.push(root);
        collectDescendants(root.children, s, out);
      }
      return out;
    },
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
