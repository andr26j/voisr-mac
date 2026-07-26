import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

const { V, register } = loadVoisr(["js/ui.js"]);
const { el, formatDuration } = V.ui;

/* The shared toast host. ui.js reaches for it by id every time it toasts,
   so registering one fake node here covers every test in this file. */
const toastHost = makeNode("div");
register("toast", toastHost);
const toastText = () =>
  toastHost.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(" ");

/* ---------- formatDuration ---------- */

test("formatDuration renders sub-minute durations", () => {
  assert.equal(formatDuration(0), "—");
  assert.equal(formatDuration(null), "—");
  assert.equal(formatDuration(undefined), "—");
  assert.equal(formatDuration(45), "45s");
  assert.equal(formatDuration(59), "59s");
  assert.equal(formatDuration(-10), "—", "negative input clamps to zero");
});

test("formatDuration renders whole minutes below an hour", () => {
  assert.equal(formatDuration(60), "1m");
  assert.equal(formatDuration(90), "2m", "rounds to the nearest minute");
  assert.equal(formatDuration(600), "10m");
  assert.equal(formatDuration(3540), "59m");
});

test("formatDuration never prints 60 minutes (shipped-bug regression)", () => {
  // 3599s rounds to 60 minutes. Deriving h and m independently from the raw
  // seconds printed "60m" here and "1h 60m" at 7199 — on the app's headline
  // stat. Minutes must be rounded first, then split.
  assert.equal(formatDuration(3599), "1h 0m");
  assert.equal(formatDuration(3600), "1h 0m");
  assert.equal(formatDuration(7199), "2h 0m");
  assert.equal(formatDuration(7200), "2h 0m");
});

test("formatDuration renders hours and minutes", () => {
  assert.equal(formatDuration(3660), "1h 1m");
  assert.equal(formatDuration(13320), "3h 42m");
  assert.equal(formatDuration(86400), "24h 0m");
});

/* ---------- el() ---------- */

test("el puts text in textContent and never in innerHTML", () => {
  const node = el("p", { text: "<b>not markup</b>" });
  assert.equal(node.tagName, "P");
  assert.equal(node.textContent, "<b>not markup</b>");
  assert.equal(node.innerHTML, "", "text must never reach innerHTML");
});

test("el sets html only via the explicit html prop", () => {
  const node = el("span", { html: "<svg></svg>" });
  assert.equal(node.innerHTML, "<svg></svg>");
  assert.equal(node.textContent, "", "html must not double up as text");
});

test("el maps class to className and everything else through setAttribute", () => {
  const node = el("button", {
    class: "icon-btn danger",
    type: "button",
    "aria-label": "Delete dictation",
  });
  assert.equal(node.className, "icon-btn danger");
  assert.equal(node.getAttribute("type"), "button");
  assert.equal(node.getAttribute("aria-label"), "Delete dictation");
  assert.equal(node.getAttribute("class"), null, "class is not an attribute path");
  assert.equal(node.getAttribute("text"), null, "text is not an attribute path");
});

test("el writes the data prop into dataset, not as attributes", () => {
  const node = el("li", { data: { id: "abc123", key: "microphone" } });
  assert.deepEqual(node.dataset, { id: "abc123", key: "microphone" });
  assert.equal(node.getAttribute("data"), null);
});

test("el registers on: handlers as real listeners", () => {
  let clicks = 0, keys = 0;
  const node = el("button", { on: { click: () => { clicks += 1; }, keydown: () => { keys += 1; } } });

  assert.equal(node.getAttribute("on"), null, "on must not become an attribute");
  node.dispatch("click");
  node.dispatch("click");
  node.dispatch("keydown");
  assert.equal(clicks, 2);
  assert.equal(keys, 1);
});

test("el skips null, undefined and false props and children", () => {
  const node = el("div", { class: "x", title: null, hidden: undefined, disabled: false });
  assert.equal(node.getAttribute("title"), null);
  assert.equal(node.getAttribute("hidden"), null);
  assert.equal(node.getAttribute("disabled"), null);

  const parent = el("ul", null, el("li", { text: "a" }), null, false, undefined, el("li", { text: "b" }));
  assert.equal(parent.children.length, 2);
  assert.deepEqual(parent.children.map((c) => c.textContent), ["a", "b"]);
});

test("el appends children, flattening arrays", () => {
  const parent = el("div", { class: "wrap" },
    "plain text",
    [el("span", { text: "one" }), el("span", { text: "two" })]);
  assert.equal(parent.children.length, 3);
  assert.equal(parent.children[0], "plain text");
  assert.deepEqual(parent.children.slice(1).map((c) => c.textContent), ["one", "two"]);
});

test("el tolerates being called with no props", () => {
  const node = el("div");
  assert.equal(node.tagName, "DIV");
  assert.equal(node.children.length, 0);
});

/* ---------- tryApi ----------

   The one convention for bridge calls. Every Api method returns a dict —
   either a result or {"error": "..."}. Before this existed each view
   invented its own handling (or none), and a Python hiccup blanked a pane
   with no message. Mock timers keep the toast's own dismissal timeout off
   the real event loop. */

test("tryApi turns a thrown bridge error into a toast and returns null", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  toastHost.replaceChildren();

  const out = await V.ui.tryApi(Promise.reject(new Error("boom")), "Load stats");

  assert.equal(out, null, "callers must get null back, never a rejection");
  assert.match(toastText(), /Load stats failed: boom/);
});

test("tryApi turns an {error} response into a toast and returns null", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  toastHost.replaceChildren();

  const out = await V.ui.tryApi(Promise.resolve({ error: "nope" }), "X");

  assert.equal(out, null);
  assert.match(toastText(), /X failed: nope/);
});

test("tryApi passes a good result straight through and never toasts", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  toastHost.replaceChildren();

  const value = { ok: true };
  const out = await V.ui.tryApi(Promise.resolve(value), "X");

  assert.equal(out, value, "the result object itself is handed back");
  assert.equal(toastText(), "", "a success must be silent");
  assert.equal(toastHost.children.length, 0);
});

test("tryApi tolerates a non-Error rejection and a non-object result", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });

  toastHost.replaceChildren();
  assert.equal(await V.ui.tryApi(Promise.reject("plain string"), "Save"), null);
  assert.match(toastText(), /Save failed: plain string/);

  // Api methods that legitimately return an array (get_history) or a
  // falsy-but-valid value must not be mistaken for failures.
  toastHost.replaceChildren();
  const list = [1, 2, 3];
  assert.equal(await V.ui.tryApi(Promise.resolve(list), "Load history"), list);
  assert.equal(toastText(), "", "an array result is not an error");
});

/* ---------- the toast queue ----------

   There is one shared #toast host and `toast()` calls replaceChildren on it,
   so a newer toast would tear a pending Undo button out of the DOM before the
   user could click it. Undo is the only safety net on delete (confirm() is
   gone), so losing one silently is data-loss-adjacent. Hence: while an action
   toast is live, incoming toasts queue instead of clobbering it.

   Each test below reloads ui.js so it starts from a clean queue — the module
   state (queue, live flag, timers) is private, and node:test discards a test's
   fake timers when it ends, which would otherwise strand `toastActionLive`
   true for every following test. The same host node is re-registered each
   time so the tests above keep resolving #toast against the newest env. */

function freshUi(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js"]);
  env.register("toast", toastHost);
  toastHost.replaceChildren();
  toastHost.hidden = false;
  toastHost.classList.remove("in");
  return env.V.ui;
}

/* Toast text alone, without the action button's label. */
const toastMsg = () =>
  toastHost.children
    .filter((c) => !(c && c.className === "toast-action"))
    .map((c) => (typeof c === "string" ? c : c.textContent))
    .join(" ");
const toastBtn = () => toastHost.children.find((c) => c && c.className === "toast-action") || null;
/* An action toast's full window: 5000ms display + the 200ms fade-out, at the
   end of which the host hides and the queue drains. */
const endOfActionToast = (t) => { t.mock.timers.tick(5000); t.mock.timers.tick(200); };
const endOfPlainToast = (t) => { t.mock.timers.tick(3500); t.mock.timers.tick(200); };

test("a plain toast requested behind a live action waits its turn", (t) => {
  const ui = freshUi(t);

  ui.toast("Dictation deleted", { actionLabel: "Undo", onAction: () => {} });
  ui.toast("Copied to clipboard");

  assert.equal(toastMsg(), "Dictation deleted", "the action toast must not be replaced");
  assert.ok(toastBtn(), "the Undo button is still in the DOM and clickable");
  assert.equal(toastBtn().textContent, "Undo");

  endOfActionToast(t);
  assert.equal(toastMsg(), "Copied to clipboard", "the queued toast appears once the window ends");
  assert.equal(toastBtn(), null);
});

test("a second action toast waits for the first one's window, then shows its own", (t) => {
  const ui = freshUi(t);
  let secondUndone = false;

  ui.toast("Deleted A", { actionLabel: "Undo", onAction: () => {} });
  ui.toast("Deleted B", { actionLabel: "Undo", onAction: () => { secondUndone = true; } });

  assert.equal(toastMsg(), "Deleted A", "the first undo survives its full window");

  endOfActionToast(t);
  assert.equal(toastMsg(), "Deleted B");
  assert.ok(toastBtn(), "the second undo is offered with a working button");
  toastBtn().dispatch("click");
  assert.equal(secondUndone, true);
});

test("a plain toast is replaced immediately by a newer plain toast", (t) => {
  const ui = freshUi(t);

  ui.toast("Copied to clipboard");
  assert.equal(toastMsg(), "Copied to clipboard");
  ui.toast("Theme: dark");

  assert.equal(toastMsg(), "Theme: dark", "nothing is queued behind a toast with no action");
  // and the replaced toast's timer went with it: the survivor gets a full
  // window rather than being cut short by its predecessor's expiry.
  t.mock.timers.tick(3499);
  assert.equal(toastHost.hidden, false);
});

test("clicking the action drains the queue immediately", (t) => {
  const ui = freshUi(t);

  ui.toast("Dictation deleted", { actionLabel: "Undo", onAction: () => {} });
  ui.toast("Copied to clipboard");
  ui.toast("Theme: dark");

  toastBtn().dispatch("click");
  assert.equal(toastMsg(), "Copied to clipboard", "the queue drains on click, not only on expiry");
  endOfPlainToast(t);
  assert.equal(toastMsg(), "Theme: dark");
  endOfPlainToast(t);
  assert.equal(toastHost.hidden, true, "an empty queue leaves the host hidden");
});

test("the queue keeps only the newest three plain toasts", (t) => {
  const ui = freshUi(t);

  ui.toast("Dictation deleted", { actionLabel: "Undo", onAction: () => {} });
  for (const m of ["one", "two", "three", "four", "five"]) ui.toast(m);

  // Five queued, cap of three, oldest dropped: "one" and "two" never show.
  const seen = [];
  endOfActionToast(t);
  seen.push(toastMsg());
  endOfPlainToast(t);
  seen.push(toastMsg());
  endOfPlainToast(t);
  seen.push(toastMsg());

  assert.deepEqual(seen, ["three", "four", "five"], "newest three, in order");
  endOfPlainToast(t);
  assert.equal(toastHost.hidden, true, "nothing else was queued");
});

test("the cap never evicts a queued undo", (t) => {
  const ui = freshUi(t);
  let undone = false;

  ui.toast("Deleted A", { actionLabel: "Undo", onAction: () => {} });
  ui.toast("chatter 1");
  // A second delete while A's undo is up: this one MUST survive the cap. It
  // is the only recovery path for a row the user just deleted, which is the
  // whole reason the queue exists.
  ui.toast("Deleted B", { actionLabel: "Undo", onAction: () => { undone = true; } });
  for (const m of ["chatter 2", "chatter 3", "chatter 4", "chatter 5"]) ui.toast(m);

  endOfActionToast(t);
  assert.equal(toastMsg(), "Deleted B", "the queued undo kept its place at the head");
  assert.ok(toastBtn(), "and it still has a live button");
  toastBtn().dispatch("click");
  assert.equal(undone, true);

  // Three survivors total: the undo plus the newest two chatter messages.
  assert.equal(toastMsg(), "chatter 4");
  endOfPlainToast(t);
  assert.equal(toastMsg(), "chatter 5");
  endOfPlainToast(t);
  assert.equal(toastHost.hidden, true);
});

test("action toasts are never dropped even when they alone exceed the cap", (t) => {
  const ui = freshUi(t);

  ui.toast("Deleted A", { actionLabel: "Undo", onAction: () => {} });
  for (const m of ["Deleted B", "Deleted C", "Deleted D", "Deleted E"]) {
    ui.toast(m, { actionLabel: "Undo", onAction: () => {} });
  }

  const seen = [];
  for (let i = 0; i < 4; i += 1) { endOfActionToast(t); seen.push(toastMsg()); }
  assert.deepEqual(seen, ["Deleted B", "Deleted C", "Deleted D", "Deleted E"],
    "four queued undos all survive a cap of three");
});

/* GAP 2: a confirmation raised by onAction() must not be clobbered by the
   drain that follows it. The click handler used to run onAction() and then
   advanceToastQueue() unconditionally, so a callback that toasted
   synchronously had its message replaced by a stale queued one in the same
   tick. Not reachable through today's three callers (history, home and
   dictionary all await before toasting — see the async test below), but the
   ordering is wrong at the callback boundary and one sync caller would lose
   a confirmation. */
test("a confirmation toasted synchronously by onAction is not clobbered by the drain", (t) => {
  const ui = freshUi(t);

  ui.toast("Dictation deleted", {
    actionLabel: "Undo",
    onAction: () => ui.toast("Restored"),
  });
  ui.toast("Copied to clipboard");

  toastBtn().dispatch("click");
  assert.equal(toastMsg(), "Restored", "the action owns the host once it has toasted");

  // The queue is not stranded: the confirmation drains it when it expires.
  endOfPlainToast(t);
  assert.equal(toastMsg(), "Copied to clipboard");
});

test("an async onAction still ends up showing its confirmation last", async (t) => {
  // Characterisation of the real callers: onAction returns a pending promise,
  // so the drain runs first and the queued toast is what "Restored" replaces
  // a microtask later. The confirmation wins, which is what matters; the fix
  // above deliberately does not defer the drain across an arbitrary promise,
  // since a callback that never settles would strand the queue for good.
  const ui = freshUi(t);

  ui.toast("Dictation deleted", {
    actionLabel: "Undo",
    onAction: async () => { await Promise.resolve(); ui.toast("Restored"); },
  });
  ui.toast("Copied to clipboard");

  toastBtn().dispatch("click");
  assert.equal(toastMsg(), "Copied to clipboard", "drained synchronously, before the await lands");
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(toastMsg(), "Restored", "the confirmation is what the user is left looking at");
});
