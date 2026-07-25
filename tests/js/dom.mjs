// Minimal DOM stand-in for node:test. No dependencies. Supports exactly what
// the Voisr web layer touches: el()'s construction surface, classList,
// dataset, hidden, listeners, and an id registry for getElementById.
/* Depth-first search for the only selector shape a node is ever asked for by
   the app: a single class, e.g. `row.querySelector(".txt")` in
   views/history.js. Anything else returns null, exactly as before. */
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

export function makeNode(tag = "div") {
  const node = {
    tagName: String(tag).toUpperCase(),
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
    setAttribute(k, v) { node.attributes[k] = String(v); },
    getAttribute(k) { return k in node.attributes ? node.attributes[k] : null; },
    addEventListener(type, fn) { (node._listeners[type] ??= []).push(fn); },
    removeEventListener() {},
    dispatch(type, ev = {}) { (node._listeners[type] || []).forEach((f) => f({ target: node, ...ev })); },
    append(...kids) { kids.flat().forEach((k) => node.children.push(k)); },
    appendChild(k) { node.children.push(k); return k; },
    replaceChildren(...kids) { node.children = kids.flat(); },
    querySelector(sel) { return findByClass(node.children, String(sel)); },
    querySelectorAll() { return []; },
    focus() {}, blur() {}, click() { node.dispatch("click"); },
    scrollIntoView() {},
    get innerHTML() { return node._innerHTML; },
    set innerHTML(v) { node._innerHTML = String(v); },
  };
  return node;
}

export function installDom() {
  const byId = new Map();
  const doc = {
    createElement: (t) => makeNode(t),
    createTextNode: (t) => String(t),
    getElementById: (id) => byId.get(id) ?? null,
    addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    documentElement: makeNode("html"), body: makeNode("body"),
    activeElement: null,
  };
  const win = {
    addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    Voisr: {},
  };
  globalThis.window = win;
  globalThis.document = doc;
  return { window: win, document: doc, register: (id, n) => byId.set(id, n) };
}
