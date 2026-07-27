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
   actually clicked inside the button, not the button itself.

   This used to patch `document.querySelector` locally, for this test only,
   because the shared stub was `() => null` and renderThemes() reaches
   #theme-grid through `V.ui.$`. The harness resolves "#id" itself now, so
   the patch is gone and this runs against exactly the same document every
   other test gets. */
test("closest() reaches main.js:95's guard for a real settings.js-built theme button (Critical regression pin)", async () => {
  const { V, register } = loadVoisr(["js/ui.js", "js/views/settings.js"]);
  const grid = makeNode("div");
  register("theme-grid", grid);
  register("toast", makeNode("div"));

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

/* ---------- node.querySelector ----------

   Element-level querySelector now follows the same rule as closest() and
   document.querySelector: the single ".class"/"#id" subset, and a loud throw
   for anything outside it. It used to return null silently, which meant
   `querySelector` behaved one way on a node and another on the document --
   the same method name, two policies, decided by the receiver. */

test("node.querySelector finds a descendant by class, however the class was set, at any depth", () => {
  const row = makeNode("li");
  const wrap = makeNode("div");
  const txt = makeNode("div");
  txt.className = "txt item-text";        // history.js's real call site shape
  const badge = makeNode("span");
  badge.classList.add("badge");
  row.append(wrap);
  wrap.append(txt, badge);

  assert.equal(row.querySelector(".txt"), txt, "reaches a grandchild, not just direct children");
  assert.equal(row.querySelector(".badge"), badge);
  assert.equal(row.querySelector(".absent"), null);
});

test("node.querySelector finds a descendant by id", () => {
  const wrap = makeNode("div");
  const inner = makeNode("button");
  inner.setAttribute("id", "color-open");   // the way ui.js's el() sets an id
  wrap.append(inner);

  assert.equal(wrap.querySelector("#color-open"), inner);
  assert.equal(wrap.querySelector("#nope"), null);
});

test("node.querySelector searches descendants only -- unlike closest(), it never matches the node itself", () => {
  const n = makeNode("div");
  n.className = "item-row";
  n.id = "self";

  // This is the one place the two must NOT agree: closest() includes the
  // starting element, querySelector does not.
  assert.equal(n.closest(".item-row"), n);
  assert.equal(n.querySelector(".item-row"), null);
  assert.equal(n.querySelector("#self"), null);
});

test("node.querySelector throws loudly on an unsupported selector, exactly like closest() and document.querySelector", () => {
  const n = makeNode("div");
  n.append(makeNode("span"));
  const childless = makeNode("div");

  for (const sel of ["div.foo", "[data-x]", "ul > li", ".a.b", "#a, #b", "a:hover", "span", ""]) {
    assert.throws(() => n.querySelector(sel), /unsupported selector/,
      `node.querySelector("${sel}") must throw, not silently return null`);
    // The selector must be rejected on its own terms, not incidentally by
    // whatever the walk happens to bump into: a childless node would
    // otherwise return null before any matching ever ran, so an unsupported
    // selector would throw or not depending on the shape of the tree.
    assert.throws(() => childless.querySelector(sel), /unsupported selector/,
      `node.querySelector("${sel}") must throw even with nothing to search`);
  }
});

/* ---------- document.querySelector ----------

   `V.ui.$` in ui.js is literally `document.querySelector`, and every view
   (home.js, settings.js, history.js, dictionary.js) reaches every one of its
   elements through it. While the fake's document.querySelector was a flat
   `() => null`, calling any of those functions from Node threw on the first
   dereference (`cv.addEventListener is not a function` for a null
   `$("#color-wheel")`), so whole render paths could only ever be checked in a
   browser. These pin the extension that unlocks them. */

test("document.querySelector resolves a single #id through the same registry as getElementById", () => {
  const { document: doc, register } = installDom();
  const strip = makeNode("div");
  register("perm-strip", strip);

  assert.equal(doc.querySelector("#perm-strip"), strip);
  // The app uses the two interchangeably for the same element -- home.js
  // reads #view-home via getElementById and #perm-strip via V.ui.$ -- so a
  // fake where they can disagree is a fake that lies.
  assert.equal(doc.querySelector("#perm-strip"), doc.getElementById("perm-strip"));
  assert.equal(doc.querySelector("#never-registered"), null,
    "an unknown id is a real null, not a throw -- that is what a browser does");
});

test("document.querySelector finds a single .class anywhere in the document tree, however the class was set", () => {
  const { document: doc } = installDom();
  const row = makeNode("li");
  // Both spellings, for the same reason matchesSimpleSelector checks both:
  // ui.js's el() writes `node.className` directly for its "class" prop and
  // never touches classList, so a class lookup that only reads classList
  // silently no-matches every class the app actually builds.
  const viaClassName = makeNode("div");
  viaClassName.className = "txt item-text";
  const viaClassList = makeNode("span");
  viaClassList.classList.add("badge");
  row.append(viaClassName, viaClassList);
  doc.body.append(row);

  assert.equal(doc.querySelector(".txt"), viaClassName, "matches a descendant several levels down");
  assert.equal(doc.querySelector(".badge"), viaClassList);
  assert.equal(doc.querySelector(".absent"), null);
});

test("document.querySelector can match the document's own roots, which an element's querySelector never does", () => {
  // `document.querySelector(".app")` matches <body class="app"> in a browser
  // -- the document's roots are candidates, not just their descendants. This
  // is the mirror of the descendants-only rule for node.querySelector above.
  const { document: doc } = installDom();
  doc.body.className = "app";
  doc.documentElement.className = "theme-dark";

  assert.equal(doc.querySelector(".app"), doc.body);
  assert.equal(doc.querySelector(".theme-dark"), doc.documentElement);
});

test("document.querySelector does not find a node that was never attached to the document", () => {
  const { document: doc, register } = installDom();
  const orphan = makeNode("div");
  orphan.className = "floating";
  register("floating-thing", orphan);   // reachable by id, still detached

  assert.equal(doc.querySelector(".floating"), null,
    "a real document.querySelector searches the document, not every node ever created");
});

test("document.querySelector throws loudly on a selector shape the app never uses, exactly like closest()", () => {
  const { document: doc } = installDom();
  // Same rule, same reason as closest() above: silently returning null for a
  // shape the fake cannot really evaluate would let a future call site pass
  // its tests here while doing nothing in the real DOM.
  for (const sel of ["div.foo", "[data-x]", "#a .b", ".a.b", "#a, #b", "a:hover", "canvas", ""]) {
    assert.throws(() => doc.querySelector(sel), /unsupported selector/,
      `document.querySelector("${sel}") must throw, not silently return null`);
  }
});

/* ---------- querySelectorAll ----------

   The last place this fake still lied. Both the node and the document version
   answered `[]` for every selector, supported or not -- and `[]` is a
   perfectly plausible "nothing matched", so a Node test of history.js's
   arrow-key navigation, Enter-to-expand or Backspace-to-delete would have
   passed vacuously: selectedItem() returns null every time because
   `rows[cursor]` is always undefined, and main.js's show() hides nothing and
   activates nothing.

   Mutation check for the pair below: put `querySelectorAll() { return []; }`
   back on either makeNode or the document and these fail. */

test("node.querySelectorAll returns every match in document order, not just the first", () => {
  const list = makeNode("ul");
  const a = makeNode("li"), b = makeNode("li"), c = makeNode("li");
  a.className = "item-row";
  b.className = "item-row selected";
  c.classList.add("item-row");            // set the other way, same as findDescendant
  const group = makeNode("div");
  group.append(c);                        // nested a level deeper
  list.append(a, b, group);

  const rows = list.querySelectorAll(".item-row");
  assert.deepEqual(rows, [a, b, c], "document order, parents before descendants");
  assert.equal(rows.length, 3);
  assert.equal(rows[0], list.querySelector(".item-row"),
    "the singular and plural forms must never disagree about the first match");
  assert.deepEqual(list.querySelectorAll(".absent"), [], "a real miss is still an empty list");
});

test("node.querySelectorAll searches descendants only, like its singular twin", () => {
  const n = makeNode("div");
  n.className = "item-row";
  n.id = "self";
  assert.deepEqual(n.querySelectorAll(".item-row"), []);
  assert.deepEqual(n.querySelectorAll("#self"), []);
});

test("node.querySelectorAll throws on an unsupported selector instead of answering an empty list", () => {
  const n = makeNode("div");
  n.append(makeNode("span"));
  const childless = makeNode("div");
  /* "#history-groups .item-row" is history.js's real selector: a fake that
     answered [] for it would let a Backspace-to-delete test pass while
     selectedItem() could never return anything. "[data-view]" is main.js's
     nav binding. Both must be loud. */
  for (const sel of ["#history-groups .item-row", "[data-view]", "div.foo", ".a.b", "li", ""]) {
    assert.throws(() => n.querySelectorAll(sel), /unsupported selector/,
      `node.querySelectorAll("${sel}") must throw, not silently return []`);
    assert.throws(() => childless.querySelectorAll(sel), /unsupported selector/,
      `node.querySelectorAll("${sel}") must throw even with nothing to search`);
  }
});

test("document.querySelectorAll matches the roots and their descendants, and resolves #id through the registry", () => {
  const { document: doc, register } = installDom();
  doc.body.className = "view";
  const a = makeNode("section"), b = makeNode("section");
  a.className = "view";
  b.classList.add("view");
  const wrap = makeNode("div");
  wrap.append(b);
  doc.body.append(a, wrap);

  // main.js's show() walks exactly this: every .view, so it can hide the
  // ones that are not the target.
  assert.deepEqual(doc.querySelectorAll(".view"), [doc.body, a, b],
    "the roots are candidates too, and descendants follow in document order");

  const strip = makeNode("div");
  register("perm-strip", strip);
  assert.deepEqual(doc.querySelectorAll("#perm-strip"), [strip],
    "an id answers through the same registry as getElementById");
  assert.deepEqual(doc.querySelectorAll("#never-registered"), []);
  assert.deepEqual(doc.querySelectorAll(".absent"), []);
});

test("document.querySelectorAll does not find a node that was never attached to the document", () => {
  const { document: doc, register } = installDom();
  const orphan = makeNode("div");
  orphan.className = "floating";
  register("floating-thing", orphan);
  assert.deepEqual(doc.querySelectorAll(".floating"), []);
});

test("document.querySelectorAll throws on an unsupported selector, exactly like document.querySelector", () => {
  const { document: doc } = installDom();
  for (const sel of ["#history-groups .item-row", "[data-view]", "div.foo", "#a, #b", "canvas", ""]) {
    assert.throws(() => doc.querySelectorAll(sel), /unsupported selector/,
      `document.querySelectorAll("${sel}") must throw, not silently return []`);
  }
});

/* ---------- style and geometry, as the colour wheel uses them ---------- */

test("style carries plain properties and custom properties in one object, and removeProperty really removes", () => {
  // settings.js's setColorUi writes both ways against the same element:
  // `swatch.style.background = hex` and `preview.style.setProperty("--mb-color", hex)`.
  const n = makeNode("div");
  n.style.background = "#FF0505";
  n.style.setProperty("--mb-color", "#FF0505");

  assert.equal(n.style.background, "#FF0505");
  assert.equal(n.style.getPropertyValue("--mb-color"), "#FF0505");
  assert.equal(n.style.getPropertyValue("--never-set"), "",
    "an unset property reads as empty string, like a real CSSStyleDeclaration");

  assert.equal(n.style.removeProperty("--mb-color"), "#FF0505", "removeProperty returns the old value");
  assert.equal(n.style.getPropertyValue("--mb-color"), "");
  assert.equal("--mb-color" in n.style, false, "removed means gone, not set to undefined");
});

test("style state is per node, not shared", () => {
  const a = makeNode("div"), b = makeNode("div");
  a.style.setProperty("--mb-color", "#FF0505");
  b.style.background = "transparent";

  assert.equal(b.style.getPropertyValue("--mb-color"), "");
  assert.equal(a.style.background, undefined);
});

test("getBoundingClientRect reports all zeros, which is what a real detached element reports", () => {
  // Deliberate, not a placeholder: every node here is detached, and a browser
  // returns a zero rect for those. settings.js's pick() turns this into "every
  // colour lands dead centre" -- the same thing it would do against an
  // unlaid-out canvas -- so a test that cares about geometry must supply its
  // own rect rather than inherit a flattering fake one.
  const r = makeNode("canvas").getBoundingClientRect();
  assert.deepEqual(
    { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height },
    { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 });
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
