import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { installDom } from "./dom.mjs";

const SRC = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)),
  "..", "..", "voisr", "web", "js", "idle.js"), "utf8");
const WAIT = 30000;

function load() {
  const { window: win, document: doc } = installDom();
  let now = 5000;
  const pending = [];
  win.performance = { now: () => now };
  win.setTimeout = (fn, ms) => { pending.push({ fn, at: now + ms }); return pending.length; };
  (0, eval)(SRC);
  return {
    idle: win.Voisr.idle,
    win,
    doc,
    pending,
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = pending.findIndex((t) => t.at <= end);
        if (due < 0) break;
        const [t] = pending.splice(due, 1);
        now = t.at;
        t.fn();
      }
      now = end;
    },
  };
}

test("it isn't idle until the wait has passed with no input", () => {
  const h = load();
  h.advance(WAIT - 1);
  assert.equal(h.idle.isIdle(), false);
  h.advance(1);
  assert.equal(h.idle.isIdle(), true);
});

for (const type of ["pointerdown", "keydown", "wheel", "focus"]) {
  test(`${type} puts the wait back to the start and ends idle`, () => {
    const h = load();
    h.advance(WAIT - 1);
    h.win.dispatch(type, {});
    h.advance(WAIT - 1);
    assert.equal(h.idle.isIdle(), false);
    h.advance(1);
    assert.equal(h.idle.isIdle(), true);
    h.win.dispatch(type, {});
    assert.equal(h.idle.isIdle(), false);
  });
}

test("however much input arrives, one timer runs, and none once idle", () => {
  const h = load();
  for (let i = 0; i < 500; i++) { h.advance(10); h.win.dispatch("pointermove", { screenX: i, screenY: 0 }); }
  assert.equal(h.pending.length, 1);
  h.advance(WAIT);
  assert.equal(h.idle.isIdle(), true);
  assert.equal(h.pending.length, 0);
});

test("listeners hear each change once, and not repeated input", () => {
  const h = load();
  let calls = 0;
  h.idle.onChange(() => { calls += 1; });
  h.win.dispatch("keydown", {});
  assert.equal(calls, 0);
  h.advance(WAIT);
  assert.equal(calls, 1);
  h.win.dispatch("keydown", {});
  h.win.dispatch("keydown", {});
  assert.equal(calls, 2);
});

test("the window being shown again ends idle, and being hidden doesn't", () => {
  const h = load();
  h.advance(WAIT);
  h.doc.visibilityState = "hidden";
  h.doc.dispatch("visibilitychange", {});
  assert.equal(h.idle.isIdle(), true);
  h.doc.visibilityState = "visible";
  h.doc.dispatch("visibilitychange", {});
  assert.equal(h.idle.isIdle(), false);
});

test("a pointer that really moved counts, and one WebKit only reports again doesn't", () => {
  const h = load();
  h.win.dispatch("pointermove", { screenX: 10, screenY: 20 });
  h.advance(WAIT - 1);
  h.win.dispatch("pointermove", { screenX: 10, screenY: 20 });
  h.advance(1);
  assert.equal(h.idle.isIdle(), true, "same place, so no input");
  h.win.dispatch("pointermove", { screenX: 10, screenY: 20 });
  assert.equal(h.idle.isIdle(), true);
  h.win.dispatch("pointermove", { screenX: 11, screenY: 20 });
  assert.equal(h.idle.isIdle(), false);
});
