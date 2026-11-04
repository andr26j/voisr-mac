import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

/* Runs widget.html's real inline script and js/meter.js against a small stub
   DOM and a fake clock, so the pill's state machine is tested as written. No
   CSS runs here: test_widget.py pins the rules a state depends on, and how it
   looks can only be checked in WebKit. */
const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "voisr", "web");
const html = readFileSync(path.join(WEB, "widget.html"), "utf8");
const pageScript = html.slice(html.indexOf("<script>\n") + 9, html.lastIndexOf("</script>"));
const meterScript = readFileSync(path.join(WEB, "js", "meter.js"), "utf8");

function makeEl() {
  const classes = new Set();
  const props = {};
  return {
    dataset: {}, children: [], scrollWidth: 0, offsetWidth: 100, textContent: "",
    style: {
      setProperty(k, v) { props[k] = String(v); },
      removeProperty(k) { delete props[k]; },
      props,
    },
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
    appendChild(c) { this.children.push(c); return c; },
    querySelectorAll() { return []; },
  };
}

function boot({ reduced = false } = {}) {
  let now = 0, seq = 1;
  const timers = new Map(), frames = new Map();
  const els = { pill: makeEl(), dot: makeEl(), bars: makeEl(), "pill-label": makeEl() };
  els.pill.dataset.state = "idle";
  els.pill.dataset.meter = "live";
  const qdots = [makeEl(), makeEl(), makeEl()];
  els.pill.querySelectorAll = () => qdots;
  const page = {
    document: { getElementById: (id) => els[id], createElement: makeEl, addEventListener() {}, visibilityState: "visible" },
    location: { search: "" },
    URLSearchParams,
    performance: { now: () => now },
    matchMedia: () => ({ matches: reduced }),
    setTimeout: (fn, ms) => { const id = seq++; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimeout: (id) => timers.delete(id),
    setInterval: () => { throw new Error("only the browser QA path may use setInterval"); },
    requestAnimationFrame: (fn) => { const id = seq++; frames.set(id, fn); return id; },
    cancelAnimationFrame: (id) => frames.delete(id),
  };
  page.window = page;
  vm.createContext(page);
  vm.runInContext(meterScript, page);
  vm.runInContext(pageScript, page);
  return {
    page, pill: els.pill, label: els["pill-label"], timers, frames,
    bars: els.bars.children,
    state: () => els.pill.dataset.state,
    meter: () => els.pill.dataset.meter,
    advance(ms) {
      const end = now + ms;
      while (now < end) {
        now += 16;
        for (const [id, t] of [...timers]) if (t.at <= now) { timers.delete(id); t.fn(); }
        const due = [...frames.values()];
        frames.clear();
        for (const fn of due) fn(now);
      }
    },
  };
}

test("a re-press during the exit keeps recording, and the old hide can't idle it", () => {
  const w = boot();
  w.page.setState("recording");
  w.page.setLevel(0.8);
  w.advance(200);
  w.page.hide();
  w.advance(100);          // inside the exit: the settle timer is still pending
  w.page.setState("recording");
  w.page.setLevel(0.8);
  w.advance(400);
  assert.equal(w.state(), "recording");
  assert.ok(w.pill.classList.contains("is-shown"));
  assert.ok(!w.pill.classList.contains("is-leaving"));
  assert.equal(w.frames.size, 1);
});

test("hiding straight from recording stops the frames and the meter's timer at once", () => {
  const w = boot();
  w.page.setState("recording");      // the fallback's grace timer is pending
  w.page.hide();
  assert.equal(w.timers.size, 1, "only the settle timer is left");

  const voiced = boot();
  voiced.page.setState("recording");
  voiced.page.setLevel(0.8);
  voiced.advance(100);
  assert.equal(voiced.frames.size, 1);
  voiced.page.hide();
  assert.equal(voiced.frames.size, 0);
});

test("a hidden pill goes idle after its exit and leaves nothing running", () => {
  const w = boot();
  w.page.setState("recording");
  w.page.setLevel(0.8);
  w.advance(200);
  w.page.hide();
  w.page.setLevel(0.9);              // a push already on its way
  w.advance(100);
  assert.equal(w.state(), "recording", "the exit plays out first");
  w.advance(200);
  assert.equal(w.state(), "idle");
  assert.equal(w.frames.size, 0);
  assert.equal(w.timers.size, 0);
});

test("hiding twice settles once", () => {
  const w = boot();
  w.page.hide();
  w.page.hide();
  assert.equal(w.timers.size, 1);
  w.advance(300);
  assert.equal(w.state(), "idle");
  assert.equal(w.timers.size, 0);
});

test("no level within the grace period plays the loop; a late one returns to live", () => {
  const w = boot();
  w.page.setState("recording");
  w.advance(100);
  assert.equal(w.meter(), "live");
  w.advance(100);
  assert.equal(w.meter(), "loop");
  assert.ok(w.bars.every((b) => !b.style.transform), "the loop's keyframes own the bars");
  w.page.setLevel(0);
  assert.equal(w.meter(), "live");
  assert.equal(w.frames.size, 0, "silence has nothing to draw");
});

test("a level in time never starts the loop", () => {
  const w = boot();
  w.page.setState("recording");
  w.page.setLevel(0.5);
  w.advance(400);
  assert.equal(w.meter(), "live");
});

test("leaving the loop puts the meter back, for the next state and the next recording", () => {
  const processing = boot();
  processing.page.setState("recording");
  processing.advance(200);
  processing.page.setState("processing");
  assert.equal(processing.meter(), "live");

  const again = boot();
  again.page.setState("recording");
  again.advance(200);
  again.page.setState("recording");
  assert.equal(again.meter(), "live");
});

test("settled silence parks the frames, a voice starts them, and they park again", () => {
  const w = boot();
  w.page.setState("recording");
  w.page.setLevel(0.02);             // under the gate
  w.advance(100);
  assert.equal(w.frames.size, 0);
  w.page.setLevel(0.8);
  w.advance(50);
  assert.equal(w.frames.size, 1);
  w.page.setLevel(0);
  w.advance(5000);
  assert.equal(w.frames.size, 0);
});

test("Quiet draws no frames and no loop: it has no bars", () => {
  const w = boot();
  w.page.setTheme("quiet");
  w.page.setState("recording");
  w.page.setLevel(0.8);
  assert.equal(w.frames.size, 0, "not even one frame per push");
  w.advance(200);
  assert.equal(w.frames.size, 0);
  assert.equal(w.meter(), "live");
});

test("a label sizes Quiet's capsule, between its own width and Signature's", () => {
  const w = boot();
  w.page.setTheme("quiet");
  w.label.scrollWidth = 109;         // "Copied — press ⌘V" as WebKit measures it
  w.page.setLabel("Copied — press ⌘V");
  assert.equal(w.pill.style.props["--label-w"], "140px");
  w.label.scrollWidth = 400;
  w.page.setLabel("a very long notice");
  assert.equal(w.pill.style.props["--label-w"], "196px");
  w.label.scrollWidth = 0;
  w.page.setLabel("");
  assert.equal(w.pill.style.props["--label-w"], "52px");
});

test("the label reaches the page as text", () => {
  const w = boot();
  w.page.setLabel("<b>Tareq</b>");
  assert.equal(w.label.textContent, "<b>Tareq</b>");
});

test("done folds the bars, and the next recording restores them", () => {
  const w = boot();
  w.page.setState("recording");
  w.page.setLevel(0.8);
  w.advance(100);
  w.page.setState("processing");
  w.page.setState("done");
  assert.match(w.bars[0].style.transform, /translateX\(44px\)/);
  assert.equal(w.bars[0].style.opacity, "0");
  w.page.setState("recording");
  assert.doesNotMatch(w.bars[0].style.transform, /translateX/);
  assert.equal(w.bars[0].style.opacity, "");
  assert.match(w.bars[0].style.transform, /^scaleY\(/);
});

test("reduced motion: the shape holds, brightness carries the level, nothing folds", () => {
  const w = boot({ reduced: true });
  w.page.setState("recording");
  w.page.setLevel(0.02);
  const centre = w.bars[8];
  const held = centre.style.transform;
  assert.equal(centre.style.opacity, ".35", "silence is dim from the first frame");
  w.page.setLevel(0.9);
  w.advance(200);
  assert.equal(centre.style.transform, held);
  assert.ok(Number(centre.style.opacity) > 0.35);
  w.page.setState("done");
  assert.doesNotMatch(centre.style.transform, /translateX/);
});

test("setTheme and setColor take only what they recognise", () => {
  const w = boot();
  w.page.setTheme("quiet");
  assert.equal(w.pill.dataset.theme, "quiet");
  w.page.setTheme("ember");
  assert.ok(!("theme" in w.pill.dataset), "no attribute means Signature");
  w.page.setColor("#112233");
  assert.equal(w.pill.style.props["--bar-color"], "#112233");
  for (const bad of ['#12345"); alert(1); ("', "#112233\n", "red", null]) {
    w.page.setColor(bad);
    assert.ok(!("--bar-color" in w.pill.style.props), String(bad));
  }
});
