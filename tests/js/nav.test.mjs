import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* main.js show(): the sidebar highlight is one element that glides to the
   active item. The fake DOM has no layout, so each item carries the
   offsetTop/offsetHeight a real sidebar would report. */
const VIEWS = ["home", "history", "dictionary", "settings"];

function mountNav() {
  const env = loadVoisr(["js/ui.js", "js/main.js"]);
  const renders = [];
  env.V.views = Object.fromEntries(VIEWS.map((v) => [v, { render: (o) => renders.push([v, o]) }]));

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
  for (const v of VIEWS) {
    const view = makeNode("section");
    view.className = "view";
    env.register(`view-${v}`, view);
    env.document.body.append(view);
  }
  return { ...env, glide, items, liveAt, renders };
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

test("opening Home tells it so, so its stats show as they are", () => {
  const n = mountNav();
  n.V.show("home");
  assert.deepEqual(n.renders.at(-1), ["home", { entering: true }]);
});
