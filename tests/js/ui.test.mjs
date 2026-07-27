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
  /* SEPARATED IN TIME ON PURPOSE. Raising both at virtual t=0 gives them the
     same 3500ms deadline, so no tick can tell a cancelled timer from a live
     one and the assertions below could never fail -- which is exactly how
     `clearTimeout(toastTimer)` came to be deletable with the whole suite
     still green. One second apart, the dead toast's deadline lands a second
     before its replacement's, in a window this test can actually observe. */
  t.mock.timers.tick(1000);
  ui.toast("Theme: dark");

  assert.equal(toastMsg(), "Theme: dark", "nothing is queued behind a toast with no action");

  // ...and the replaced toast's dismissal timer went with it.
  t.mock.timers.tick(2500);          // t=3500: the FIRST toast's deadline
  assert.equal(toastHost.classList.contains("in"), true,
    "a dead toast's timer must not start a fade on the toast that replaced it");
  t.mock.timers.tick(200);           // t=3700: where its fade would have landed
  assert.equal(toastHost.hidden, false, "and must not hide it either");
  assert.equal(toastMsg(), "Theme: dark");

  // The survivor gets a full window of its own, measured from when it showed.
  t.mock.timers.tick(800);           // t=4500: the SECOND toast's deadline
  t.mock.timers.tick(200);
  assert.equal(toastHost.hidden, true);
});

test("an older toast's fade timer cannot hide the toast that replaced it", (t) => {
  /* The fade-out is a SECOND timer, and it is the one that shipped untracked:
     toast A's display window ends, its 200ms fade is scheduled, toast B
     replaces A inside those 200ms, and A's fade then hides B -- Undo button
     and all. That is the defect this project already fixed once; nothing
     covered it, so `clearTimeout(toastFadeTimer)` was deletable with the
     suite green.

     Deliberately disjoint from the test above: there the first toast's
     display timer is what must be cancelled, here it has already fired and
     only the fade is outstanding. So deleting either clearTimeout on its own
     fails exactly one of the two. */
  const ui = freshUi(t);

  ui.toast("Copied to clipboard");
  t.mock.timers.tick(3500);          // A's window ends; its fade is now pending
  assert.equal(toastHost.hidden, false, "A is fading, not yet hidden");

  ui.toast("Dictation deleted", { actionLabel: "Undo", onAction: () => {} });
  t.mock.timers.tick(200);           // t=3700: where A's fade lands

  assert.equal(toastHost.hidden, false,
    "the replaced toast's fade must not hide its replacement");
  assert.equal(toastMsg(), "Dictation deleted");
  assert.ok(toastBtn(),
    "and must not take a live Undo off screen -- delete is a hard delete");

  // The new toast still owns a full action window of its own, measured from
  // t=3500 when it showed -- so its own deadline is t=8500, fade at t=8700.
  t.mock.timers.tick(4800);
  t.mock.timers.tick(200);
  assert.equal(toastHost.hidden, true);
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

/* ---------- the coalescing undo ----------

   REPLACES two tests that pinned the previous model:

     - "the cap never evicts a queued undo"
     - "action toasts are never dropped even when they alone exceed the cap"

   Both pinned a cap *exemption* for action toasts, which existed because a
   dropped Undo is permanent data loss. The exemption was the wrong fix: it
   let undos pile up FIFO, so the mistake you most want back surfaced last,
   every one of them read the same text with no row identity, and clicking one
   promoted the *next* undo into the host instead of the confirmation.

   The replacement is coalescing: one live undo, always current. Their
   replacements below pin that instead —
   "a burst of deletes leaves exactly one live undo, never a queue of them"
   is the direct counterpart of the second test (N deletes, one toast rather
   than N).

   The first test's counterpart is a PAIR, and it was originally shipped as
   one, which is how the cap regained the ability to destroy a dictation:
   "chatter cannot evict the live undo" plus "chatter cannot evict a QUEUED
   undo either". Coalescing bounds how many undos can be in flight (one per
   batch, two batches) but does NOT stop one being queued — a dictation
   delete while a dictionary undo is live queues, as "a dictionary removal
   keeps its own batch" demonstrates. So the cap keeps a *bounded* exemption:
   it skips over action toasts and drops plain ones. "the queue keeps only the
   newest three plain toasts" above and "the cap still drops plain toasts when
   an undo is queued among them" below pin that the exemption did not turn
   into a licence to grow. */

/* Lets the async undo chain (restore -> refresh -> confirmation toast)
   settle: microtasks drain fully before a macrotask runs. */
const settle = () => new Promise((r) => setImmediate(r));

/* What the user can actually read: a hidden host shows nothing at all,
   whatever stale children it is still holding. */
const shown = () => (toastHost.hidden ? "" : toastMsg());

/* A stand-in for one delete. `restore` is what history.js/home.js inject —
   here it just records the id and reports success or failure. */
function deleter(ui, log, ok = () => true) {
  return (id) => ui.offerDictationUndo({
    restore: async () => { log.push(id); return ok(id); },
    refresh: async () => { log.push(`refresh:${id}`); },
  });
}

test("a second delete upgrades the live undo in place instead of queueing", (t) => {
  const ui = freshUi(t);
  const log = [];
  const del = deleter(ui, log);

  del("a1");
  assert.equal(toastMsg(), "Dictation deleted", "one delete reads in the singular");

  del("a2");
  assert.equal(toastMsg(), "2 dictations deleted",
    "the second delete rewrites the live toast rather than lining up behind it");
  assert.equal(toastHost.children.filter((c) => c && c.className === "toast-action").length, 1,
    "exactly one Undo button, not one per delete");

  del("a3");
  assert.equal(toastMsg(), "3 dictations deleted");

  // Nothing is waiting behind it: the window ends and the host goes empty.
  endOfActionToast(t);
  assert.equal(toastHost.hidden, true, "no queued undos were left over");
});

test("the coalesced Undo restores every delete in the batch", async (t) => {
  const ui = freshUi(t);
  const log = [];
  const del = deleter(ui, log);

  del("a1");
  del("a2");
  toastBtn().dispatch("click");
  await settle();

  assert.deepEqual(log.filter((l) => !l.startsWith("refresh")), ["a1", "a2"],
    "both rows were restored, not just the one the toast was first raised for");
  assert.equal(toastMsg(), "Restored 2 dictations");
});

test("clicking Undo shows the result, never another delete toast (H4 regression)", async (t) => {
  /* The bug that blocked H4. With one queued undo per delete, clicking the
     first drained the *second* into the host: the toast still read
     "Dictation deleted" with an Undo button, which reads as "my click didn't
     register" — and clicking again undid a different row. */
  const ui = freshUi(t);
  const log = [];
  const del = deleter(ui, log);

  del("a1");
  del("a2");
  toastBtn().dispatch("click");

  assert.equal(shown(), "",
    "the undo is dismissed on click and no second 'Dictation deleted' takes its place");

  await settle();
  assert.equal(shown(), "Restored 2 dictations", "the confirmation is what the user sees");
  assert.equal(toastBtn(), null, "and it carries no further Undo");
});

test("upgrading the undo restarts its dismissal window", (t) => {
  const ui = freshUi(t);
  const log = [];
  const del = deleter(ui, log);

  del("a1");
  t.mock.timers.tick(4000);          // 1s left on the first delete's window
  del("a2");
  assert.equal(toastMsg(), "2 dictations deleted");

  t.mock.timers.tick(1500);          // t = 5.5s: past the FIRST delete's 5s
  assert.equal(toastHost.hidden, false, "the window is measured from the most recent delete");
  assert.ok(toastBtn(), "the Undo is still clickable");

  endOfActionToast(t);               // t = 9.2s: the second delete's window ends
  assert.equal(toastHost.hidden, true);
});

test("the batch clears when the undo expires, so a later delete starts at one", (t) => {
  const ui = freshUi(t);
  const log = [];
  const del = deleter(ui, log);

  del("a1");
  endOfActionToast(t);
  assert.equal(toastHost.hidden, true);

  del("a2");
  assert.equal(toastMsg(), "Dictation deleted",
    "a delete after the window must not resurrect the long-gone batch");

  toastBtn().dispatch("click");
  assert.deepEqual(log.filter((l) => !l.startsWith("refresh")), ["a2"],
    "and its Undo restores only the new delete");
});

test("a partial restore reports what actually came back, not full success", async (t) => {
  const ui = freshUi(t);
  const log = [];
  // The middle restore fails — tryApi answers null on an {error} response.
  const del = deleter(ui, log, (id) => (id === "a2" ? null : true));

  del("a1"); del("a2"); del("a3");
  assert.equal(toastMsg(), "3 dictations deleted");

  toastBtn().dispatch("click");
  await settle();

  assert.equal(toastMsg(), "Restored 2 of 3 dictations");
});

test("a single restore that fails outright leaves the failure message as the last word", async (t) => {
  const ui = freshUi(t);
  ui.offerDictationUndo({
    restore: async () => ui.tryApi(Promise.resolve({ error: "disk full" }), "Restore dictation"),
    refresh: async () => {},
  });

  toastBtn().dispatch("click");
  await settle();

  assert.equal(toastMsg(), "Restore dictation failed: disk full",
    "with one row, tryApi's reason is the whole story and nothing may be layered over it");
});

test("an all-fail batch reports how many were lost without discarding the reason", async (t) => {
  /* Three deletes, every restore fails. Staying silent here left the user
     reading one singular failure ("Restore dictation failed: gone 3") while
     three rows were gone -- accurate about one row, silent about the other
     two. The count is now carried, and the reason survives with it. */
  const ui = freshUi(t);
  const del = (id) => ui.offerDictationUndo({
    restore: async () => ui.tryApi(Promise.resolve({ error: `gone ${id}` }), "Restore dictation"),
    refresh: async () => {},
  });

  del("a1"); del("a2"); del("a3");
  toastBtn().dispatch("click");
  await settle();

  assert.equal(toastMsg(), "Restore dictation failed: gone a3 (0 of 3 restored)");
});

test("a burst of deletes leaves exactly one live undo, never a queue of them", async (t) => {
  const ui = freshUi(t);
  const log = [];
  const del = deleter(ui, log);

  for (const id of ["a1", "a2", "a3", "a4", "a5"]) del(id);

  assert.equal(toastMsg(), "5 dictations deleted", "five deletes, one always-current toast");
  assert.ok(toastBtn());

  toastBtn().dispatch("click");
  await settle();
  assert.deepEqual(log.filter((l) => !l.startsWith("refresh")), ["a1", "a2", "a3", "a4", "a5"],
    "and it restores all five");
  assert.equal(shown(), "Restored 5 dictations");
});

/* NAME MATTERS HERE. This was called "chatter can no longer evict an undo,
   because an undo is never queued" -- which asserted in its title something
   the code does not guarantee, three lines above a test that *demonstrates* a
   queued undo ("a dictionary removal keeps its own batch"). That mis-framing
   is exactly why the cap shipped able to evict one. What this test actually
   proves is narrower: chatter cannot dislodge the *live* undo. The queued
   case is the separate test below it. */
test("chatter cannot evict the live undo", (t) => {
  const ui = freshUi(t);
  const log = [];
  const del = deleter(ui, log);

  del("a1");
  for (const m of ["chatter 1", "chatter 2", "chatter 3", "chatter 4", "chatter 5"]) ui.toast(m);
  del("a2");

  // The second delete reaches the live undo over the top of five queued
  // messages -- under the old model it joined the back of that queue and
  // depended on a cap exemption to survive at all.
  assert.equal(toastMsg(), "2 dictations deleted");
  assert.ok(toastBtn(), "the undo is on screen and clickable the whole time");

  toastBtn().dispatch("click");
  // The chatter is still capped to three and still drains oldest-first.
  assert.equal(toastMsg(), "chatter 3");
  endOfPlainToast(t);
  assert.equal(toastMsg(), "chatter 4");
  endOfPlainToast(t);
  assert.equal(toastMsg(), "chatter 5");
  endOfPlainToast(t);
  assert.equal(toastHost.hidden, true);
});

test("chatter cannot evict a QUEUED undo either (permanent-data-loss regression)", async (t) => {
  /* The counterpart the rename above exposes as missing, and the sequence a
     plain shift-the-oldest cap loses outright:

       offerEntryUndo()      -> "Entry removed" LIVE
       offerDictationUndo()  -> QUEUED (different batch, so no upgrade)
       three plain toasts    -> the cap shifts the OLDEST, i.e. the dictation
                                undo, firing its onResolve and emptying the
                                batch

     Delete is a hard delete; that closure was the only handle on the row.
     Three plain toasts are not three user actions -- tryApi failures and
     home.js's permission-refresh toast fire on their own, so one flaky-bridge
     moment reaches this with nobody clicking anything.

     Mutation check: replace the findIndex/splice loop in queueToast with
     `toastQueue.shift()` and this test fails on the "Dictation deleted"
     assertion. */
  const ui = freshUi(t);
  const log = [];

  ui.offerEntryUndo({ restore: async () => { log.push("e1"); return true; }, refresh: async () => {} });
  assert.equal(toastMsg(), "Entry removed", "the entry undo owns the host");

  ui.offerDictationUndo({
    restore: async () => { log.push("d1"); return true; },
    refresh: async () => { log.push("refresh:d1"); },
  });
  assert.equal(toastMsg(), "Entry removed", "the dictation undo is queued, not shown");

  for (const m of ["Copied to clipboard 1", "Copied to clipboard 2", "Copied to clipboard 3"]) ui.toast(m);

  // The live entry undo runs its full window first...
  endOfActionToast(t);

  // ...and the dictation undo must still be there, not evicted by the chatter.
  assert.equal(toastMsg(), "Dictation deleted",
    "the queued undo survives the cap -- evicting it destroys the row for good");
  assert.ok(toastBtn(), "and it still carries a working Undo");

  toastBtn().dispatch("click");
  await settle();
  assert.deepEqual(log, ["d1", "refresh:d1"], "the row is really restored, and the list re-rendered");
  assert.equal(toastMsg(), "Restored", "and the click's own confirmation is what is left on screen");

  // Chatter is what the cap ate: the undo occupies a slot but is skipped
  // over, so the OLDEST plain toast went instead. The newest still drains.
  endOfPlainToast(t);
  assert.equal(toastMsg(), "Copied to clipboard 3");
  endOfPlainToast(t);
  assert.equal(toastHost.hidden, true);
});

test("a second delete behind someone else's live undo updates the QUEUED undo in place", async (t) => {
  /* offerUndo has two coalescing branches and only one of them was covered.
     The live branch ("a second delete upgrades the live undo in place") is
     reached when this batch owns the host. This is the other one: a
     *different* batch owns the host, so this batch's undo is sitting in the
     queue, and a second delete must rewrite that queued entry rather than
     add a second one.

     That branch is what makes "at most one undo is ever queued" true -- and
     that claim is the entire justification for the cap's skip-actions
     exemption (see the comment at ui.js's queueToast). Without it, two
     deletes behind a live entry undo queue TWO undos for one batch: the
     first click empties batch.entries, and the second then drains in as a
     live-looking Undo button that restores nothing. This project has already
     shipped a Critical from asserting the wrong invariant here, so the real
     one gets a real test.

     Mutation check: delete the `const queued = ... if (queued) {...}` block
     from offerUndo and this test fails on the first assertion below. */
  const ui = freshUi(t);
  const log = [];

  // A dictionary removal owns the host, so the dictation undo has to queue.
  ui.offerEntryUndo({ restore: async () => { log.push("e1"); return true; }, refresh: async () => {} });
  ui.offerDictationUndo({ restore: async () => { log.push("d1"); return true; }, refresh: async () => {} });
  ui.offerDictationUndo({ restore: async () => { log.push("d2"); return true; }, refresh: async () => {} });
  assert.equal(toastMsg(), "Entry removed", "the live entry undo is untouched");

  endOfActionToast(t);               // the entry undo's window ends, queue drains

  assert.equal(toastMsg(), "2 dictations deleted",
    "the second delete rewrote the queued undo instead of lining up behind it");

  toastBtn().dispatch("click");
  await settle();
  assert.deepEqual(log, ["d1", "d2"],
    "and the one Undo restores the whole batch, not just the first row");
  assert.equal(toastMsg(), "Restored 2 dictations");

  endOfPlainToast(t);
  assert.equal(toastHost.hidden, true,
    "no second dictation undo was queued -- a dead Undo button never drains in");
});

test("the cap still drops plain toasts when an undo is queued among them", (t) => {
  /* The exemption is bounded, not a licence to grow. The queued undo is
     skipped over, but it is still *counted* against TOAST_QUEUE_MAX, so five
     plain toasts behind it leave two rather than three: the backlog can never
     get longer because an undo is in it. */
  const ui = freshUi(t);

  ui.offerEntryUndo({ restore: async () => true, refresh: async () => {} });
  ui.offerDictationUndo({ restore: async () => true, refresh: async () => {} });
  for (const m of ["one", "two", "three", "four", "five"]) ui.toast(m);

  endOfActionToast(t);
  assert.equal(toastMsg(), "Dictation deleted", "the undo is never the one dropped");

  endOfActionToast(t);
  assert.equal(toastMsg(), "four", "the oldest plain toasts went");
  endOfPlainToast(t);
  assert.equal(toastMsg(), "five");
  endOfPlainToast(t);
  assert.equal(toastHost.hidden, true, "three slots, one held by the undo, so two chatter survived");
});

test("a dictionary removal keeps its own batch, separate from dictations", (t) => {
  const ui = freshUi(t);
  const log = [];

  ui.offerEntryUndo({ restore: async () => { log.push("e1"); return true; }, refresh: async () => {} });
  assert.equal(toastMsg(), "Entry removed");

  ui.offerEntryUndo({ restore: async () => { log.push("e2"); return true; }, refresh: async () => {} });
  assert.equal(toastMsg(), "2 entries removed", "entries coalesce the same way dictations do");

  // A dictation delete must not be swept into the entry batch -- different
  // things, so it waits its turn rather than upgrading someone else's undo.
  ui.offerDictationUndo({ restore: async () => { log.push("a1"); return true; }, refresh: async () => {} });
  assert.equal(toastMsg(), "2 entries removed", "the live entry undo is not hijacked");

  endOfActionToast(t);
  assert.equal(toastMsg(), "Dictation deleted");
  assert.ok(toastBtn());
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
