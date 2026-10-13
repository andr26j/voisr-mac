import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* ---------- home.js: render(), driven end to end in Node ----------

   views.test.mjs already covers home.js's pure helpers (trendBars, weekDelta).
   What
   it could not touch was render() itself -- the function that actually puts
   the view on screen -- because every element it writes to is reached through
   `V.ui.$`, i.e. `document.querySelector`, which the fake DOM stubbed as
   `() => null`. render() therefore threw on its first line
   (`$("#greeting").textContent = ...`) in Node, so the panel-isolation
   contract documented above render() ("a failure in one degrades that panel
   only") had never been executed by a test -- only by hand in a browser.

   Now that tests/js/dom.mjs resolves "#id" through the same registry as
   getElementById, these run for real: the production render() writing to
   real fake nodes, with nothing patched per-test. */

const HOME_IDS = ["greeting", "perm-strip", "ptt-bar", "ptt-keycap", "ptt-key-name",
                  "ptt-state", "ptt-state-label",
                  "stat-saved", "stat-words", "stat-streak", "delta-saved", "delta-words",
                  "trend-saved", "trend-words", "trend-streak", "recent-list",
                  "recent-empty", "toast", "view-home",
                  "dock", "record-btn", "record-status", "record-error", "dock-time",
                  "nav-home"];

/* Home polls permissions with a real setInterval while something is blocked.
   Unref'd, a render that ends blocked can't keep the test process alive. */
const realSetInterval = globalThis.setInterval;
globalThis.setInterval = (...args) => {
  const timer = realSetInterval(...args);
  if (timer && typeof timer.unref === "function") timer.unref();
  return timer;
};

const HISTORY = Array.from({ length: 7 }, (_, i) => ({
  id: `h${i}`, ts: 1_750_000_000 + i * 60, text: `dictation ${i}`, words: 10 + i, duration_s: 4,
}));

const OK_API = {
  get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: true },
    { key: "input_monitoring", label: "Input Monitoring", granted: true },
    { key: "accessibility", label: "Accessibility", granted: true },
  ]}),
  hotkey_display: async () => ({ display: "Right Option (⌥)" }),
  get_hotkey_status: async () => ({ status: "active on Right Option (⌥)", state: "listening" }),
  // saved_seconds runs 1.5x the words (40 wpm, no speaking time), so the
  // saved tile's own trend has the same shape the words fixture always had
  get_stats: async () => ({
    total_words: 12480, streak_days: 6, time_saved_seconds: 13320,
    daily: Array.from({ length: 14 }, (_, i) => ({
      date: `d${i}`, words: i * 10, saved_seconds: i * 15 })),
  }),
  get_history: async () => HISTORY,
};

/* V.api is stubbed directly rather than through js/api.js: api.js's dev
   fallback arms a 300ms setTimeout on load, which every one of these tests
   would then have to mock away for no benefit. Same shape either way --
   `V.api()` returns the bridge object. */
function mountHome(api) {
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/views/home.js"]);
  const nodes = {};
  for (const id of HOME_IDS) {
    nodes[id] = makeNode(id === "recent-list" ? "ul" : "div");
    env.register(id, nodes[id]);
  }
  env.V.api = () => ({ ...OK_API, ...api });
  return { ...env, ...nodes };
}

const settle = () => new Promise((r) => setImmediate(r));

test("home render() fills every panel through V.ui.$ -- greeting, push-to-talk line, stats, trends and the recent list", async () => {
  const h = mountHome({});

  await h.V.views.home.render();

  assert.ok(["Working late", "Good morning", "Good afternoon", "Good evening"]
    .includes(h.greeting.textContent), `unexpected greeting: ${h.greeting.textContent}`);
  // the keycap and the sentence name the same key, both from hotkey_display()
  assert.equal(h["ptt-keycap"].textContent, "Right Option (⌥)");
  assert.equal(h["ptt-key-name"].textContent, "Right Option (⌥)");

  // 13320s -> 222 whole minutes -> 3h 42m (formatDuration rounds to minutes first)
  assert.equal(h["stat-saved"].textContent, "3h 42m");
  assert.equal(h["stat-words"].textContent, (12480).toLocaleString());
  assert.equal(h["stat-streak"].textContent, (6).toLocaleString());

  // every tile carries its own 14-day trend rather than sharing one sparkline
  for (const id of ["trend-saved", "trend-words", "trend-streak"]) {
    assert.equal(h[id].children.length, 14, `${id} did not draw 14 days`);
  }
  const heights = h["trend-saved"].children.map((b) => b.style.getPropertyValue("--h"));
  assert.ok(heights.every((v) => v !== "" && !/NaN|undefined/.test(v)),
    `trend bars have bad heights: ${heights.join()}`);
  // series is i * 10, so the last day is the tallest and the first is empty
  assert.equal(heights[13], "1.000");
  assert.equal(h["trend-saved"].children[0].className, "bar zero");
  assert.equal(h["trend-saved"].children[13].className, "bar today");
  // days 7-13 sum to 700 against days 0-6's 210: +233%
  assert.equal(h["delta-saved"].textContent, "+233% vs last week");
  assert.equal(h["delta-saved"].dataset.dir, "up");
  // day 0 is the only empty one in the i * 10 series, so 13 dots are lit
  assert.equal(h["trend-streak"].children.filter((d) => d.className === "dot on").length, 13);

  // 7 items in history, five on Home.
  assert.equal(h["recent-list"].children.length, 5);
  assert.equal(h["recent-list"].children[0].dataset.id, "h0");
  assert.equal(h["recent-list"].children[0].className, "item-row");
  assert.equal(h["recent-empty"].hidden, true, "the empty-state line hides once there are rows");

  // All three permissions granted -> the quiet one-line state, not three chips.
  assert.equal(h["perm-strip"].dataset.state, "ok");
  assert.equal(h["perm-strip"].children.length, 1);
  assert.equal(h["perm-strip"].children[0].className, "perm-ok");
});

test("home render() reveals the empty-state line when the history empties out", async () => {
  const h = mountHome({ get_history: async () => [] });
  // Start from "already hidden", i.e. the state a previous non-empty render
  // left behind. Asserting against a fresh node instead would pass without
  // render() ever running -- `hidden` is false by default.
  h["recent-empty"].hidden = true;

  await h.V.views.home.render();

  assert.equal(h["recent-list"].children.length, 0);
  assert.equal(h["recent-empty"].hidden, false,
    "with nothing left to list, the empty-state line must come back");
});

test("home render() degrades one panel at a time -- a failed get_stats still leaves the recent list rendered", async (t) => {
  // The panel-isolation contract from the comment above render(): stats used
  // to throw on `stats.total_words` and take the whole pane down, leaving a
  // blank screen. Timers are mocked because tryApi's failure toast arms a
  // 3.5s dismissal that would otherwise hold the process open.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = mountHome({ get_stats: async () => ({ error: "db locked" }) });

  await h.V.views.home.render();
  await settle();

  assert.equal(h["stat-words"].textContent, "",
    "the stats panel stays untouched rather than printing a wrong number");
  assert.equal(h["recent-list"].children.length, 5,
    "the recent list is a separate panel and must still render");
  assert.equal(h["ptt-keycap"].textContent, "Right Option (⌥)", "so must the push-to-talk line");
  assert.equal(h["perm-strip"].dataset.state, "ok", "so must the permission strip");

  const toastText = h.toast.children.map((c) => c.textContent).join("");
  assert.match(toastText, /Load stats failed: db locked/);
  assert.doesNotMatch(toastText, /Home failed to load/,
    "one dead panel is not a whole-view failure");
});

test("home renderPermissions() replaces the whole panel when a grant is missing, and says what broke", async () => {
  const h = mountHome({ get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: true },
    { key: "input_monitoring", label: "Input Monitoring", granted: false, hotkey: true },
    { key: "accessibility", label: "Accessibility", granted: null },
  ]}) });

  await h.V.views.home.renderPermissions();

  assert.equal(h["perm-strip"].dataset.state, "attention");
  assert.equal(h["perm-strip"].children.length, 1);
  const block = h["perm-strip"].children[0];
  assert.equal(block.className, "perm-block");

  // A dead push-to-talk key is not a footnote: the status line promising it
  // works must be gone, not left sitting above the warning.
  assert.equal(h["ptt-bar"].hidden, true);

  const text = block.children.map((c) => c.textContent).join(" ");
  assert.match(text, /Push-to-talk isn't working/,
    "the heading names what broke, not which setting is missing");
  assert.match(text, /holding it does nothing/);

  // worst first: the known denial leads, not the merely unverified one
  const actions = block.children.find((c) => c.className === "perm-actions");
  assert.deepEqual(actions.children.map((b) => b.textContent),
    ["Open Input Monitoring", "Record from here instead"],
    "a blocked key still offers the dock as a way to keep working");

  // all three stay listed, so one trip to System Settings can clear them all
  const checks = block.children.find((c) => c.className === "perm-checks");
  assert.deepEqual(checks.children.map((li) => li.className),
    ["perm-ok-row", "perm-denied-row", "perm-unknown-row"]);
});

test("home renderPermissions() keeps the push-to-talk line up when only Accessibility is missing", async () => {
  // Without Accessibility the key still records and copies -- only the paste
  // is lost -- so the line above is still telling the truth.
  const h = mountHome({ get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: true },
    { key: "input_monitoring", label: "Input Monitoring", granted: true },
    { key: "accessibility", label: "Accessibility", granted: false },
  ]}) });

  await h.V.views.home.renderPermissions();

  assert.equal(h["perm-strip"].dataset.state, "attention");
  assert.equal(h["ptt-bar"].hidden, false);
  const block = h["perm-strip"].children[0];
  assert.match(block.children.map((c) => c.textContent).join(" "), /copied to your clipboard/);
});

test("home renderPermissions() collapses to one quiet line once every grant is in", async () => {
  const h = mountHome({});
  h["ptt-bar"].hidden = true;   // the state a previous blocked render left behind

  await h.V.views.home.renderPermissions();

  assert.equal(h["perm-strip"].dataset.state, "ok");
  assert.equal(h["perm-strip"].children.length, 1);
  assert.equal(h["perm-strip"].children[0].className, "perm-ok");
  assert.equal(h["ptt-bar"].hidden, false, "the status line must come back");
});

test("home renderPermissions() keeps push-to-talk up on first run, when macOS hasn't asked for the mic yet", async () => {
  // H2 (2026-10-12 audit): the mic prompt only appears on the first
  // recording, so holding the key is exactly how a new user gets past it.
  const h = mountHome({ get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: null },
    { key: "input_monitoring", label: "Input Monitoring", granted: true },
    { key: "accessibility", label: "Accessibility", granted: true },
  ]}) });
  h["ptt-bar"].hidden = true;

  await h.V.views.home.renderPermissions();

  assert.equal(h["ptt-bar"].hidden, false, "the push-to-talk line stays");
  assert.equal(h["perm-strip"].dataset.state, "pending");
  assert.equal(h["perm-strip"].children.length, 1);
  assert.notEqual(h["perm-strip"].children[0].className, "perm-block");
  const hint = h["perm-strip"].children[0].textContent;
  assert.match(hint, /will ask/i);
  assert.match(hint, /first time you dictate/i);
});

test("home renderPermissions() leads with a real denial, not a mic that hasn't been asked yet", async () => {
  const h = mountHome({ get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: null },
    { key: "input_monitoring", label: "Input Monitoring", granted: false, hotkey: true },
    { key: "accessibility", label: "Accessibility", granted: true },
  ]}) });

  await h.V.views.home.renderPermissions();

  const block = h["perm-strip"].children[0];
  assert.equal(block.className, "perm-block");
  const actions = block.children.find((c) => c.className === "perm-actions");
  assert.equal(actions.children[0].textContent, "Open Input Monitoring");
  const checks = block.children.find((c) => c.className === "perm-checks");
  const micWhy = checks.children[0].children.find((c) => c.className === "perm-why");
  assert.match(micWhy.textContent, /first dictation/i,
    "the mic row says it will be asked, not that it is needed");
});

test("home renderPermissions() still blocks on a mic the user switched off", async () => {
  const h = mountHome({ get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: false },
    { key: "input_monitoring", label: "Input Monitoring", granted: true },
    { key: "accessibility", label: "Accessibility", granted: true },
  ]}) });

  await h.V.views.home.renderPermissions();

  assert.equal(h["perm-strip"].children[0].className, "perm-block");
  assert.equal(h["ptt-bar"].hidden, true);
});


/* ---------- M6-M9 (2026-10-12 audit): Home tells the truth ---------- */

for (const [state, label] of [
  ["listening", /Listening for the key/],
  ["busy", /Paused while you record here/],
  ["off", /Not listening/],
]) {
  test(`home render() shows the listener as ${state}, from get_hotkey_status`, async () => {
    const h = mountHome({ get_hotkey_status: async () => ({ status: "x", state }) });
    await h.V.views.home.render();
    assert.equal(h["ptt-state"].dataset.state, state);
    assert.match(h["ptt-state-label"].textContent, label);
  });
}

test("home render() does not claim it is listening when the status call fails", async () => {
  const h = mountHome({ get_hotkey_status: async () => ({ error: "bridge gone" }) });
  await h.V.views.home.render();
  assert.equal(h["ptt-state"].dataset.state, "unknown");
  assert.doesNotMatch(h["ptt-state-label"].textContent, /Listening/);
});

function recordingApi(extra = {}) {
  let recording = false;
  const calls = { start: 0, stop: 0 };
  const api = {
    get_hotkey_status: async () => ({ status: "x", state: recording ? "busy" : "listening" }),
    start_recording: async () => { calls.start += 1; recording = true; return { recording: true }; },
    stop_recording: async () => { calls.stop += 1; recording = false; return { text: "Hi" }; },
    ...extra,
  };
  return { api, calls };
}

/* The dock's one-second tick is a real setInterval: a test that ends with
   the dock recording keeps Node alive, so every recording test stops it. */
async function stopDock(h) {
  if (h.dock.dataset.state === "recording") await h.V.views.home.toggleRecord();
}

test("a dock recording marks the Home nav item and pauses the listener dot until it stops", async () => {
  const { api } = recordingApi();
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  await settle();
  try {
    assert.equal(h["nav-home"].dataset.recording, "true",
      "visible from every view, since push-to-talk is dropped meanwhile");
    assert.equal(h["ptt-state"].dataset.state, "busy");
  } catch (e) {
    await stopDock(h);
    throw e;
  }

  await h.V.views.home.toggleRecord();
  await settle();
  assert.equal(h["nav-home"].dataset.recording, undefined);
  assert.equal(h["ptt-state"].dataset.state, "listening");
});

test("a double-click on the dock mic starts one recording and shows no error", async () => {
  const pending = [];
  const { api, calls } = recordingApi();
  const slowStart = api.start_recording;
  api.start_recording = () => new Promise((r) => { pending.push(() => r(slowStart())); });
  const h = mountHome(api);

  const first = h.V.views.home.toggleRecord();
  const second = h.V.views.home.toggleRecord();   // lands while the first is in flight
  pending.forEach((go) => go());
  await Promise.all([first, second]);

  try {
    assert.equal(calls.start, 1);
    assert.equal(calls.stop, 0, "the second click must not stop what the first started");
    assert.equal(h["record-error"].textContent, "");
    assert.equal(h.dock.dataset.state, "recording");
  } finally {
    await stopDock(h);
  }
});

test("a failed start returns the dock to idle so the next click can try again", async () => {
  const { api, calls } = recordingApi();
  api.start_recording = async () => { calls.start += 1; return { error: "already recording" }; };
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  assert.equal(h["record-error"].textContent, "Couldn't start.");
  await h.V.views.home.toggleRecord();
  assert.equal(calls.start, 2);
});

test("dictateFromAnywhere switches to Home before recording, so the dock is in view", async () => {
  const { api, calls } = recordingApi();
  const h = mountHome(api);
  const shown = [];
  h.V.show = (view) => { shown.push(view); };
  try {
    await h.V.views.home.dictateFromAnywhere();
    assert.deepEqual(shown, ["home"]);
    assert.equal(calls.start, 1);
  } finally {
    await stopDock(h);
  }
});

test("the Time saved tile draws and compares its own series, not the word counts", async () => {
  // words climb while time saved falls (slower speech): each tile must say so
  const daily = Array.from({ length: 14 }, (_, i) => ({
    date: `d${i}`, words: 10 + i * 10, saved_seconds: 300 - i * 20 }));
  const h = mountHome({ get_stats: async () => ({
    total_words: 1000, streak_days: 1, time_saved_seconds: 600, daily }) });
  await h.V.views.home.render();
  assert.equal(h["delta-words"].dataset.dir, "up");
  assert.equal(h["delta-saved"].dataset.dir, "down");
  assert.equal(h["trend-saved"].children[0].style.getPropertyValue("--h"), "1.000",
    "the first day saved the most time, so it is the tallest bar");
});


test("'Record from here instead' starts a dock recording rather than only focusing the button", async () => {
  // M11: .focus() after a mouse click shows no ring, so the button looked dead
  const { api, calls } = recordingApi();
  const h = mountHome({ ...api, get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: true },
    { key: "input_monitoring", label: "Input Monitoring", granted: false, hotkey: true },
    { key: "accessibility", label: "Accessibility", granted: true },
  ]}) });
  await h.V.views.home.renderPermissions();
  const actions = h["perm-strip"].children[0].children.find((c) => c.className === "perm-actions");
  const fallback = actions.children.find((b) => b.textContent === "Record from here instead");
  try {
    fallback.dispatch("click", { target: fallback });
    await settle();
    assert.equal(calls.start, 1);
    assert.equal(h.dock.dataset.state, "recording");
  } finally {
    await stopDock(h);
  }
});

/* ---------- L8 (2026-10-12 audit) ---------- */

test("a focus refresh with no permission change leaves the strip alone, so it isn't re-announced", async () => {
  const h = mountHome({});
  await h.V.views.home.renderPermissions();
  const first = h["perm-strip"].children[0];
  await h.V.views.home.renderPermissions();
  assert.equal(h["perm-strip"].children[0], first,
    "aria-live re-reads the strip whenever its children are replaced");
});

test("a permission change still re-renders the strip", async () => {
  let granted = true;
  const h = mountHome({ get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: true },
    { key: "input_monitoring", label: "Input Monitoring", granted },
    { key: "accessibility", label: "Accessibility", granted: true },
  ]}) });
  await h.V.views.home.renderPermissions();
  granted = false;
  await h.V.views.home.renderPermissions();
  assert.equal(h["perm-strip"].children[0].className, "perm-block");
});

test("a dock dictation whose History write failed says so instead of a bare 'Copied'", async () => {
  const { api } = recordingApi({
    stop_recording: async () => ({ text: "Hi", copied: true, warning: "history failed: disk full" }),
  });
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  await h.V.views.home.toggleRecord();
  assert.match(h["record-error"].textContent, /History/);
});

test("'Record from here instead' never stops a dock recording already running", async () => {
  const { api, calls } = recordingApi();
  const h = mountHome({ ...api, get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: true },
    { key: "input_monitoring", label: "Input Monitoring", granted: false, hotkey: true },
    { key: "accessibility", label: "Accessibility", granted: true },
  ]}) });
  await h.V.views.home.renderPermissions();
  await h.V.views.home.toggleRecord();          // recording from the dock
  const actions = h["perm-strip"].children[0].children.find((c) => c.className === "perm-actions");
  const fallback = actions.children.find((b) => b.textContent === "Record from here instead");
  try {
    fallback.dispatch("click", { target: fallback });
    await settle();
    assert.equal(calls.stop, 0);
    assert.equal(h.dock.dataset.state, "recording");
  } finally {
    await stopDock(h);
  }
});

/* ---------- step 5: stats count up, trend bars grow ----------

   The fake window has no requestAnimationFrame, so every update is instant
   unless a test installs these: frames queue up and the test runs them with
   the timestamps it wants. */
function frames(h) {
  const queue = [];
  h.window.requestAnimationFrame = (fn) => queue.push(fn);
  return {
    run(ts) { queue.splice(0).forEach((fn) => fn(ts)); },
    get pending() { return queue.length; },
  };
}

const num = (s) => Number(String(s).replace(/\D/g, ""));
const statsWith = (over) => async () => ({ ...(await OK_API.get_stats()), ...over });

test("opening Home shows the stats as they are -- no count from zero", async () => {
  const h = mountHome({});
  const f = frames(h);
  await h.V.views.home.render({ entering: true });
  assert.equal(h["stat-words"].textContent, (12480).toLocaleString());
  assert.equal(h["stat-saved"].textContent, "3h 42m");
  assert.equal(f.pending, 0, "nothing to tween on open");
});

test("a stat that changes while Home is on screen counts up over 600 ms", async () => {
  const api = {};
  const h = mountHome(api);
  const f = frames(h);
  await h.V.views.home.render({ entering: true });

  api.get_stats = statsWith({ total_words: 13000, time_saved_seconds: 13920 });
  await h.V.views.home.render();
  assert.equal(num(h["stat-words"].textContent), 12480, "starts from the old value");

  f.run(1000);                       // first frame: the clock starts here
  assert.equal(num(h["stat-words"].textContent), 12480);
  f.run(1300);                       // halfway in time, well past halfway in value (ease-out)
  const mid = num(h["stat-words"].textContent);
  assert.ok(mid > 12740 && mid < 13000, `halfway frame showed ${mid}`);
  f.run(1600);
  assert.equal(h["stat-words"].textContent, (13000).toLocaleString());
  assert.equal(h["stat-saved"].textContent, "3h 52m", "time saved tweens its seconds and lands formatted");
  assert.equal(f.pending, 0, "the tween stops once it lands");
});

test("an unchanged stat schedules no frames", async () => {
  const h = mountHome({});
  const f = frames(h);
  await h.V.views.home.render({ entering: true });
  await h.V.views.home.render();
  assert.equal(f.pending, 0);
});

test("a new value mid-count carries on from what is on screen, never jumping back", async () => {
  const api = {};
  const h = mountHome(api);
  const f = frames(h);
  await h.V.views.home.render({ entering: true });
  api.get_stats = statsWith({ total_words: 13000 });
  await h.V.views.home.render();
  f.run(0);
  f.run(300);
  const shown = num(h["stat-words"].textContent);

  api.get_stats = statsWith({ total_words: 14000 });
  await h.V.views.home.render();
  f.run(500);
  assert.equal(num(h["stat-words"].textContent), shown, "the second count starts where the first stood");
  f.run(1100);
  assert.equal(h["stat-words"].textContent, (14000).toLocaleString());
});

test("reduced motion jumps a changed stat straight to its value", async () => {
  const api = {};
  const h = mountHome(api);
  const f = frames(h);
  h.window.matchMedia = (q) => ({ matches: /reduce/.test(q), addEventListener() {} });
  await h.V.views.home.render({ entering: true });
  api.get_stats = statsWith({ total_words: 13000 });
  await h.V.views.home.render();
  assert.equal(h["stat-words"].textContent, (13000).toLocaleString());
  assert.equal(f.pending, 0);
});

test("a refresh with the same number of days keeps the trend bars, so they can grow", async () => {
  const api = {};
  const h = mountHome(api);
  await h.V.views.home.render({ entering: true });
  const before = [...h["trend-words"].children];

  // today doubles: every other day halves against the new maximum
  api.get_stats = statsWith({ daily: Array.from({ length: 14 }, (_, i) => ({
    date: `d${i}`, words: i === 13 ? 260 : i * 10, saved_seconds: i * 15 })) });
  await h.V.views.home.render();

  const after = h["trend-words"].children;
  assert.equal(after.length, 14);
  assert.ok(after.every((bar, i) => bar === before[i]), "the same nodes, restyled in place");
  assert.equal(after[13].style.getPropertyValue("--h"), "1.000");
  assert.equal(after[12].style.getPropertyValue("--h"), "0.462");
  assert.equal(after[13].className, "bar today");
});

test("an empty day keeps its 2 px stub now that bars scale instead of resize", async () => {
  const h = mountHome({});
  await h.V.views.home.render({ entering: true });
  const zero = h["trend-words"].children[0];
  assert.equal(zero.className, "bar zero");
  // scaleY ignores min-height, so the floor is 2 px of the 34 px trend
  assert.equal(zero.style.getPropertyValue("--h"), (2 / 34).toFixed(3));
});

test("a recent-list refresh that fails mid-animation says so instead of an unhandled rejection", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const api = {};
  const h = mountHome(api);
  h["recent-list"].animate = () => ({ finished: Promise.resolve() });  // lists.js animates it
  await h.V.views.home.render({ entering: true });

  api.get_history = async () => [{ id: "new", ts: 1_760_000_000, text: "fresh", words: 1, duration_s: 1 }, ...HISTORY];
  h.V.ui.itemRow = () => { throw new Error("bad row"); };
  await h.V.views.home.render();
  await settle();

  const toastText = h.toast.children.map((c) => c.textContent).join("");
  assert.match(toastText, /Recent dictations failed to update/);
});

/* ---------- Task 7: a granted-but-refused hotkey grant, Reset/Relaunch, polling ---------- */

// a fake container's textContent doesn't include its descendants
const allText = (node) => [node.textContent || "", ...(node.children || []).map(allText)].join(" ");

const STALE_API = {
  get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: true, hotkey: false, effective: true },
    { key: "accessibility", label: "Accessibility", granted: true, hotkey: true, effective: false },
  ]}),
};

test("a granted-but-not-effective hotkey pane blocks with Reset and Relaunch", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const h = mountHome(STALE_API);
  await h.V.views.home.render();
  assert.equal(h["perm-strip"].dataset.state, "attention");
  const text = allText(h["perm-strip"]);
  assert.match(text, /isn't honouring/);
  assert.match(text, /Reset Accessibility/);
  assert.match(text, /Relaunch Voisr/);
  assert.equal(h["ptt-bar"].hidden, true, "the key doesn't work, so don't promise it");
});

test("active mode: two granted panes read as all granted", async () => {
  const h = mountHome({ get_permissions: async () => ({ panes: [
    { key: "microphone", label: "Microphone", granted: true, hotkey: false, effective: true },
    { key: "accessibility", label: "Accessibility", granted: true, hotkey: true, effective: true },
  ]}) });
  await h.V.views.home.render();
  assert.equal(h["perm-strip"].dataset.state, "ok");
  assert.equal(h["perm-strip"].children[0].textContent,
               "Microphone and Accessibility granted.");
});

test("while attention is needed, permissions re-render every second", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let calls = 0;
  const h = mountHome({ get_permissions: async () => {
    calls += 1;
    return (await STALE_API.get_permissions());
  } });
  await h.V.views.home.render();
  const before = calls;
  t.mock.timers.tick(3000);
  await settle();
  assert.ok(calls >= before + 3, `expected ≥3 polls, got ${calls - before}`);
});

test("Reset and Relaunch call the bridge, and surface a failure as a toast", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const calls = [];
  const h = mountHome({ ...STALE_API,
    reset_permission: async (key) => { calls.push(["reset", key]); return { ok: true }; },
    relaunch_app: async () => { calls.push(["relaunch"]); return { ok: false, error: "no bundle" }; },
  });
  await h.V.views.home.render();
  const actions = h["perm-strip"].children[0].children.find((c) => c.className === "perm-actions");
  const [reset, relaunch] = actions.children;
  const toastText = () => h.toast.children.map((c) => c.textContent).join("|");
  reset.dispatch("click", { target: reset });
  await settle();
  assert.match(toastText(), /switch Voisr on under Accessibility/);
  relaunch.dispatch("click", { target: relaunch });
  await settle();
  assert.match(toastText(), /no bundle/);
  assert.deepEqual(calls, [["reset", "accessibility"], ["relaunch"]]);
});
