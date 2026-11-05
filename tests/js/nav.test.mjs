import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* main.js show(): the sidebar highlight is one element that glides to the
   active item. The fake DOM has no layout, so each item carries the
   offsetTop/offsetHeight a real sidebar would report. */
const VIEWS = ["home", "history", "dictionary", "settings"];
const PAGES = VIEWS.filter((v) => v !== "settings");

function mountNav() {
  const env = loadVoisr(["js/ui.js", "js/main.js"]);
  const renders = [];
  const opened = [];
  let closes = 0;
  env.V.views = Object.fromEntries(VIEWS.map((v) => [v, { render: (o) => renders.push([v, o]) }]));
  env.V.views.settings = {
    open: (o) => opened.push(o),
    close: () => {
      closes += 1;
      env.window.dispatch("voisr:settings-closed", {});
    },
  };

  const nav = makeNode("nav");
  const glide = makeNode("span");
  glide.className = "nav-glide";
  env.register("nav-glide", glide);
  nav.append(glide);
  // records where the glide stood when it was switched on
  const liveAt = [];
  const add = glide.classList.add.bind(glide.classList);
  glide.classList.add = (...cs) => {
    if (cs.includes("is-live")) liveAt.push(glide.style.transform);
    add(...cs);
  };

  const items = VIEWS.map((v, i) => {
    const b = makeNode("button");
    b.className = "nav-item";
    b.dataset.view = v;
    Object.assign(b, { offsetTop: i * 46, offsetHeight: 44 });
    nav.append(b);
    return b;
  });
  env.document.body.append(nav);
  const pages = {};
  for (const v of PAGES) {
    const view = makeNode("section");
    pages[v] = view;
    view.className = "view";
    env.register(`view-${v}`, view);
    env.document.body.append(view);
  }
  return { ...env, glide, items, liveAt, renders, opened, pages, closes: () => closes };
}

test("the first paint places the highlight under Home without a glide", () => {
  const n = mountNav();
  n.V.show("home");
  assert.equal(n.glide.style.transform, "translateY(0px)");
  assert.equal(n.glide.style.height, "44px");
  assert.deepEqual(n.liveAt, ["translateY(0px)"],
    "the transition only switches on once the highlight already stands under Home");
});

test("switching views moves the one highlight to the new item", () => {
  const n = mountNav();
  n.V.show("home");
  n.V.show("dictionary");
  assert.equal(n.glide.style.transform, "translateY(92px)");
  assert.ok(n.glide.classList.contains("is-live"));
  assert.ok(n.items[2].classList.contains("active"), "the item keeps .active for its text colour");
  assert.ok(!n.items[0].classList.contains("active"));
});

test("a window resize keeps the highlight under an item that moved", () => {
  const n = mountNav();
  n.V.show("settings");
  n.items[3].offsetTop = 500;  // Settings is bottom-anchored
  n.window.dispatch("resize", {});
  assert.equal(n.glide.style.transform, "translateY(500px)");
  assert.ok(n.glide.classList.contains("is-live"), "the next switch still glides");
  assert.equal(n.liveAt.at(-1), "translateY(500px)", "placed before the transition came back");
});

test("a resize before the first view is placed does nothing", () => {
  const n = mountNav();
  n.window.dispatch("resize", {});
  assert.equal(n.glide.style.transform, undefined);
});

test("opening Home tells it so, so its stats show as they are", () => {
  const n = mountNav();
  n.V.show("home");
  assert.deepEqual(n.renders.at(-1), ["home", { entering: true }]);
});

test("Settings opens over the page, leaving the page as it was", () => {
  const n = mountNav();
  n.V.show("history");
  const rendersBefore = n.renders.length;
  n.V.show("settings", { section: "key" });
  assert.deepEqual(n.opened, [{ section: "key" }]);
  assert.equal(n.pages.history.hidden, false, "the page stays behind it");
  assert.equal(n.renders.length, rendersBefore, "and isn't rendered again");
  assert.ok(n.items[3].classList.contains("active"), "the sidebar shows where you are");
  assert.equal(n.glide.style.transform, "translateY(138px)");
});

test("closing Settings puts the sidebar back on the page behind it", () => {
  const n = mountNav();
  n.V.show("history");
  n.V.show("settings");
  n.V.views.settings.close();
  assert.ok(n.items[1].classList.contains("active"));
  assert.ok(!n.items[3].classList.contains("active"));
  assert.equal(n.glide.style.transform, "translateY(46px)");
});

test("closing Settings refreshes Home and Dictionary, which show what it changes", () => {
  for (const page of ["home", "dictionary"]) {
    const n = mountNav();
    n.V.show(page);
    n.renders.length = 0;
    n.V.show("settings");
    n.V.views.settings.close();
    assert.deepEqual(n.renders.map((r) => r[0]), [page]);
  }
});

test("closing Settings leaves History alone, so its selected row survives", () => {
  const n = mountNav();
  n.V.show("history");
  n.renders.length = 0;
  n.V.show("settings");
  n.V.views.settings.close();
  assert.deepEqual(n.renders, []);
});

test("the palette going to a page closes Settings on the way", () => {
  const n = mountNav();
  n.V.show("home");
  n.V.show("settings");
  const before = n.closes();
  n.renders.length = 0;
  n.V.show("history");
  assert.equal(n.closes(), before + 1);
  assert.deepEqual(n.renders.map((r) => r[0]), ["history"], "Home is being left, so it isn't redrawn");
  assert.equal(n.pages.history.hidden, false);
  assert.equal(n.pages.home.hidden, true);
});

/* ---------- Tab on the main views ---------- */

function mountTab() {
  const env = loadVoisr(["js/ui.js", "js/main.js"]);
  const parts = {};
  for (const [name, sel] of [["shell", "shell"], ["palette", "palette-scrim"], ["scrim", "settings-scrim"]]) {
    const n = makeNode("div");
    n.className = sel;
    parts[name] = n;
    env.document.body.append(n);
  }
  env.register("palette", parts.palette);
  env.register("settings-scrim", parts.scrim);
  parts.palette.hidden = true;
  parts.scrim.hidden = true;

  const control = (tag = "button", over = {}) => {
    const n = makeNode(tag);
    n.focus = () => { env.document.activeElement = n; };
    n.getClientRects = () => [{}];
    return Object.assign(n, over);
  };
  const tab = (over = {}) => {
    const ev = { key: "Tab", target: env.document.body, preventDefault() { ev.prevented = true; }, ...over };
    env.document.dispatch("keydown", ev);
    return ev;
  };
  return { ...env, ...parts, control, tab };
}

/* Fakes the document-order query the fake DOM's selector subset can't parse. */
const inOrder = (t, nodes) => { t.document.body.querySelectorAll = () => nodes; };

test("from the body, Tab starts at the first control and Shift-Tab at the last", () => {
  const t = mountTab();
  const first = t.control(), last = t.control();
  inOrder(t, [first, last]);

  t.document.activeElement = t.document.body;   // where first run leaves it
  assert.equal(t.tab().prevented, true);
  assert.equal(t.document.activeElement, first);

  t.document.activeElement = t.document.body;
  t.tab({ shiftKey: true });
  assert.equal(t.document.activeElement, last);
});

test("Tab and Shift-Tab step through in order and wrap", () => {
  const t = mountTab();
  const nav = t.control(), search = t.control("input"), text = t.control("textarea"), row = t.control();
  inOrder(t, [nav, search, text, row]);
  t.document.activeElement = null;

  const seen = [];
  for (let i = 0; i < 5; i++) { t.tab(); seen.push(t.document.activeElement); }
  assert.deepEqual(seen, [nav, search, text, row, nav]);
  t.tab({ shiftKey: true });
  assert.equal(t.document.activeElement, row, "back round the other way");
});

test("the walker asks for textareas, which the Snippets form has", () => {
  const t = mountTab();
  let asked = "";
  t.document.body.querySelectorAll = (sel) => { asked = sel; return []; };
  t.tab();
  assert.ok(asked.split(",").map((s) => s.trim()).includes("textarea"), asked);
});

test("Tab goes past a control that is visibility: hidden, where focus() would fail", () => {
  const t = mountTab();
  const record = t.control();
  const changeKey = t.control();
  changeKey.style.visibility = "hidden";   // Home's Change key while recording
  changeKey.focus = () => assert.fail("focus() on it does nothing, and Tab would be stuck");
  const viewAll = t.control();
  inOrder(t, [record, changeKey, viewAll]);

  t.document.activeElement = record;
  t.tab();
  assert.equal(t.document.activeElement, viewAll);
  t.tab({ shiftKey: true });
  assert.equal(t.document.activeElement, record);
});

test("the walker leaves Tab alone while first run, the palette or Settings is up", () => {
  for (const up of ["shell", "palette", "scrim"]) {
    const t = mountTab();
    inOrder(t, [t.control()]);
    if (up === "shell") t.shell.hidden = true;   // first run
    else if (up === "palette") t.palette.hidden = false;
    else { t.scrim.hidden = false; t.scrim.classList.add("is-open"); }
    t.document.activeElement = null;
    const ev = t.tab();
    assert.equal(ev.prevented, undefined, up);
    assert.equal(t.document.activeElement, null, up);
  }
});

test("the walker leaves other keys, ⌃Tab and a Tab already handled alone", () => {
  const t = mountTab();
  inOrder(t, [t.control()]);
  t.document.activeElement = null;
  for (const over of [{ key: "Enter" }, { ctrlKey: true }, { metaKey: true }, { altKey: true },
                      { defaultPrevented: true }]) {
    assert.equal(t.tab(over).prevented, undefined, JSON.stringify(over));
  }
  assert.equal(t.document.activeElement, null);
});

test("while Settings fades out, Tab walks the page and not its leaving buttons", () => {
  const t = mountTab();
  const pageButton = t.control(), leaving = t.control();
  t.scrim.hidden = false;                      // closing: no is-open, not hidden yet
  t.shell.querySelectorAll = () => [pageButton];
  inOrder(t, [pageButton, leaving]);
  t.document.activeElement = pageButton;
  const ev = t.tab();
  assert.equal(ev.prevented, true, "WebKit's own Tab would leave the web view");
  assert.equal(t.document.activeElement, pageButton, "the only control on the page, so it wraps to itself");
  t.tab({ shiftKey: true });
  assert.equal(t.document.activeElement, pageButton);
});

test("Tab selects what's in a field it lands on, as WebKit's own does", () => {
  const t = mountTab();
  let selected = 0;
  const field = t.control("input", { select: () => { selected += 1; } });
  const button = t.control();
  inOrder(t, [button, field]);
  t.document.activeElement = button;
  t.tab();
  assert.equal(t.document.activeElement, field);
  assert.equal(selected, 1);
  t.tab();                                     // a button has no select(), and that's fine
  assert.equal(t.document.activeElement, button);
});
