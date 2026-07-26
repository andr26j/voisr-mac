import test from "node:test";
import assert from "node:assert/strict";
import { makeNode, installDom } from "./dom.mjs";
import { loadVoisr } from "./load.mjs";

/* Direct fidelity tests for the H6 fake-DOM extension. The app-level tests
   (settings.test.mjs's popover-dismissal test in particular) exercise
   contains()/closest()/dispatch() indirectly; these pin the contract itself,
   including edge cases nothing else happens to touch -- self-containment,
   ancestors NOT counting as contained, detachment on replaceChildren, and
   the "throw rather than silently return null" rule for an unsupported
   closest() selector. A fake that lies here is worse than no fake: it would
   make a real-DOM bug pass in Node and only show up in the browser. */

/* ---------- contains ---------- */

test("contains() is true for the node itself, matching real Node.contains", () => {
  const n = makeNode("div");
  assert.equal(n.contains(n), true);
});

test("contains() is true for a descendant reached via append(), false for an unrelated node", () => {
  const parent = makeNode("div");
  const child = makeNode("span");
  const grandchild = makeNode("b");
  parent.append(child);
  child.append(grandchild);

  assert.equal(parent.contains(child), true, "direct child");
  assert.equal(parent.contains(grandchild), true, "grandchild, multiple levels down");
  assert.equal(parent.contains(makeNode("div")), false, "an unrelated node is not contained");
});

test("contains() does not treat an ancestor as contained by its descendant", () => {
  const parent = makeNode("div");
  const child = makeNode("span");
  parent.append(child);

  // The relationship is one-directional: the parent contains the child, not
  // the reverse. Getting this backwards would make every outside-click test
  // pass for the wrong reason.
  assert.equal(child.contains(parent), false);
});

test("appendChild also links parent, and replaceChildren detaches outgoing children", () => {
  const parent = makeNode("ul");
  const a = makeNode("li"), b = makeNode("li"), c = makeNode("li");
  parent.appendChild(a);
  assert.equal(parent.contains(a), true);

  parent.replaceChildren(b, c);
  assert.equal(parent.contains(a), false, "a is no longer in the tree");
  assert.equal(a.parent, null, "a's parent link must be cleared, not left dangling");
  assert.equal(parent.contains(b), true);
  assert.equal(parent.contains(c), true);
});

test("append() and appendChild() detach a node from its previous parent before adopting it, like the real DOM", () => {
  const oldParent = makeNode("div");
  const newParent = makeNode("div");
  const kid = makeNode("span");
  oldParent.append(kid);
  assert.equal(oldParent.contains(kid), true, "sanity: kid starts under oldParent");

  newParent.append(kid);
  assert.equal(kid.parent, newParent, "kid's parent link points at the new parent");
  assert.equal(newParent.contains(kid), true);
  assert.equal(oldParent.contains(kid), false,
    "the real DOM *moves* a node -- oldParent must not still contain it");
  assert.equal(oldParent.children.includes(kid), false,
    "kid must be spliced out of oldParent.children, not just have a stale parent link");

  // appendChild() must behave identically to append() here.
  const kid2 = makeNode("b");
  oldParent.appendChild(kid2);
  newParent.appendChild(kid2);
  assert.equal(oldParent.children.includes(kid2), false);
  assert.equal(newParent.contains(kid2), true);
});

/* ---------- closest ---------- */

test("closest() matches the starting node itself before walking up, by class or id", () => {
  // Fixtures built the way the app actually builds nodes, via V.ui.el() --
  // NOT via classList.add()/direct `.id =` assignment, which is not how
  // ui.js's el() sets either one. el()'s "class" prop sets `node.className`
  // directly (never touches classList); its "id" prop routes through
  // setAttribute. A fixture shaped any other way can pass while the fake
  // still no-matches every class/id the app actually produces (main.js:95's
  // `.closest(".theme-opt")` against a settings.js renderThemes() button).
  const { V } = loadVoisr(["js/ui.js"]);
  const n = V.ui.el("button", { class: "theme-opt active" });
  assert.equal(n.closest(".theme-opt"), n);

  const withId = V.ui.el("div", { id: "color-open" });
  assert.equal(withId.closest("#color-open"), withId);
});

/* This is the regression pin for the Critical itself: a button built by the
   real production code (settings.js's renderThemes(), not a hand-rolled
   fixture) run through the exact literal guard at main.js:95-96 --
   `const btn = e.target.closest(".theme-opt"); if (!btn) return;`. Before
   the matchesSimpleSelector fix, this failed exactly the way the review's
   probe output showed: closest() no-matched a real app-built ".theme-opt"
   button because renderThemes() sets classes via `node.className = v`
   (ui.js's el()), never `classList`. The click target here is deliberately
   a *descendant* of the button (the label span), mirroring "child.closest
   -> null" from the report -- #theme-grid's listener fires on whatever was
   actually clicked inside the button, not the button itself. main.js's own
   init() can't be driven here (it binds through `V.ui.$`, i.e.
   `document.querySelector`, which is a pre-existing `() => null` stub --
   flagged separately, out of H6's scope), so this test patches
   `document.querySelector` locally, for this test only, to resolve `#id`
   the way a real browser would -- just enough to let the real renderThemes()
   run and build a real button, without touching the shared harness or app
   code. */
test("closest() reaches main.js:95's guard for a real settings.js-built theme button (Critical regression pin)", async () => {
  const { V, document, register } = loadVoisr(["js/ui.js", "js/views/settings.js"]);
  const grid = makeNode("div");
  register("theme-grid", grid);
  register("toast", makeNode("div"));
  document.querySelector = (sel) =>
    sel.startsWith("#") ? document.getElementById(sel.slice(1)) : null;

  V.api = () => ({
    get_config: async () => ({ pill_theme: "sunset" }),
    get_pill_themes: async () => [{ value: "sunset", label: "Sunset" }],
  });

  await V.views.settings.renderThemes();

  const btn = grid.children[0];
  assert.ok(btn, "renderThemes() must have appended a theme button to #theme-grid");
  assert.equal(btn.className, "theme-opt active");

  const labelSpan = btn.children.find((k) => k && k.className === "theme-name");
  assert.ok(labelSpan, "expected the theme-name span renderThemes() builds inside the button");

  const guardResult = labelSpan.closest(".theme-opt"); // main.js:95's literal call
  assert.equal(guardResult, btn,
    "main.js:95's guard must resolve to the button, not early-exit on null");
});

test("closest() walks up through parent links past several levels", () => {
  const button = makeNode("button");
  button.id = "color-open";
  const wrap = makeNode("div");
  const icon = makeNode("span");
  button.append(wrap);
  wrap.append(icon);

  assert.equal(icon.closest("#color-open"), button, "matches an ancestor, not just self/parent");
});

test("closest() returns null when nothing in the chain matches", () => {
  const leaf = makeNode("span");
  const root = makeNode("div");
  root.append(leaf);
  assert.equal(leaf.closest("#nope"), null);
  assert.equal(leaf.closest(".nope"), null);
});

test("closest() throws loudly on a selector shape the app never actually uses", () => {
  const n = makeNode("div");
  // Real call sites (main.js, settings.js, history.js) only ever pass a
  // single ".class" or "#id". Anything else must fail loudly rather than
  // silently returning null, which would make a future call site that needs
  // real selector support look tested when it is not.
  for (const sel of ["div.foo", "[data-x]", "ul > li", ".a.b", "#a, #b", "a:hover", ""]) {
    assert.throws(() => n.closest(sel), /unsupported selector/,
      `closest("${sel}") must throw, not silently return null`);
  }
});

/* ---------- document / window event targets ---------- */

test("document.addEventListener records handlers and dispatch() invokes every one, in order", () => {
  const { document: doc } = installDom();
  const seen = [];
  doc.addEventListener("click", () => seen.push("first"));
  doc.addEventListener("click", () => seen.push("second"));
  doc.addEventListener("keydown", () => seen.push("keydown"));

  doc.dispatch("click", { target: "x" });
  assert.deepEqual(seen, ["first", "second"]);
});

test("document.dispatch hands each listener the event object, target included", () => {
  const { document: doc } = installDom();
  let received = null;
  doc.addEventListener("keydown", (e) => { received = e; });
  doc.dispatch("keydown", { key: "Escape" });
  assert.equal(received.key, "Escape");
});

test("removeEventListener actually stops a handler from firing", () => {
  const { document: doc } = installDom();
  let calls = 0;
  const fn = () => { calls += 1; };
  doc.addEventListener("click", fn);
  doc.dispatch("click");
  assert.equal(calls, 1);

  doc.removeEventListener("click", fn);
  doc.dispatch("click");
  assert.equal(calls, 1, "the removed handler must not fire again");
});

test("window.dispatchEvent(new Event(type)) reaches window listeners by type", () => {
  const { window: win } = installDom();
  let fired = 0;
  win.addEventListener("voisr:new-dictation", () => { fired += 1; });

  win.dispatchEvent(new Event("voisr:new-dictation"));
  assert.equal(fired, 1);

  // A different event type must not cross-fire the listener.
  win.dispatchEvent(new Event("something-else"));
  assert.equal(fired, 1);
});

test("node.dispatch and document/window listeners are independent registries", () => {
  const { document: doc, window: win } = installDom();
  const node = makeNode("div");
  let nodeClicks = 0, docClicks = 0, winClicks = 0;
  node.addEventListener("click", () => { nodeClicks += 1; });
  doc.addEventListener("click", () => { docClicks += 1; });
  win.addEventListener("click", () => { winClicks += 1; });

  node.dispatch("click");
  assert.equal(nodeClicks, 1);
  assert.equal(docClicks, 0);
  assert.equal(winClicks, 0);
});

/* ---------- pre-existing invariants that must survive this change ---------- */

test("textContent and innerHTML remain independent, per node", () => {
  const a = makeNode("span");
  const b = makeNode("span");
  a.textContent = "plain text";
  b.innerHTML = "<b>markup</b>";

  assert.equal(a.textContent, "plain text");
  assert.equal(a.innerHTML, "", "setting textContent must not leak into innerHTML");
  assert.equal(b.innerHTML, "<b>markup</b>");
  assert.equal(b.textContent, "", "setting innerHTML must not leak into textContent");
});

test("classList state is per node, not shared", () => {
  const a = makeNode("div");
  const b = makeNode("div");
  a.classList.add("armed");
  assert.equal(a.classList.contains("armed"), true);
  assert.equal(b.classList.contains("armed"), false);
});
