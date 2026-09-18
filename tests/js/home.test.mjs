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
                  "stat-saved", "stat-words", "stat-streak", "delta-saved", "delta-words",
                  "trend-saved", "trend-words", "trend-streak", "recent-list",
                  "recent-empty", "toast", "view-home"];

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
  get_stats: async () => ({
    total_words: 12480, streak_days: 6, time_saved_seconds: 13320,
    daily: Array.from({ length: 14 }, (_, i) => ({ date: `d${i}`, words: i * 10 })),
  }),
  get_history: async () => HISTORY,
};

/* V.api is stubbed directly rather than through js/api.js: api.js's dev
   fallback arms a 300ms setTimeout on load, which every one of these tests
   would then have to mock away for no benefit. Same shape either way --
   `V.api()` returns the bridge object. */
function mountHome(api) {
  const env = loadVoisr(["js/ui.js", "js/views/home.js"]);
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
    { key: "input_monitoring", label: "Input Monitoring", granted: false },
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
