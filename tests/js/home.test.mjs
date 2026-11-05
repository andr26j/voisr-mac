import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

/* ---------- home.js: render(), driven end to end in Node ----------

   Every element it writes to is reached through `V.ui.$`, and tests/js/dom.mjs
   resolves "#id" through the same registry as getElementById, so these run for
   real: the production render() writing to real fake nodes, with nothing patched
   per test. That is what lets the panel-isolation contract documented above
   render() ("a failure in one degrades that panel only") be executed at all. */

const HOME_IDS = ["greeting", "perm-strip", "home-hero", "home-hint", "home-status", "ptt-key-name",
                  "stat-saved", "stat-words", "stat-streak",
                  "recent-groups", "recent-empty", "home-view-all", "toast", "view-home",
                  "record-btn", "record-label", "record-title", "record-status", "record-error",
                  "record-time", "home-wave", "change-key", "nav-home"];

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

const LISTEN_PANES = [
  { key: "microphone", label: "Microphone", granted: true },
  { key: "input_monitoring", label: "Input Monitoring", granted: true, hotkey: true },
  { key: "accessibility", label: "Accessibility", granted: true },
];

const OK_API = {
  get_permissions: async () => ({ panes: LISTEN_PANES }),
  hotkey_display: async () => ({ display: "Right Option (⌥)" }),
  get_hotkey_status: async () => ({ status: "active on Right Option (⌥)", state: "listening" }),
  get_stats: async () => ({ total_words: 12480, streak_days: 6, time_saved_seconds: 13320 }),
  get_history: async () => HISTORY,
  get_config: async () => ({ paste: true }),
};

/* V.api is stubbed directly rather than through js/api.js: api.js's dev
   fallback arms a 300ms setTimeout on load, which every one of these tests
   would then have to mock away for no benefit. Same shape either way:
   `V.api()` returns the bridge object. */
function mountHome(api) {
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/views/home.js"]);
  const nodes = {};
  for (const id of HOME_IDS) {
    nodes[id] = makeNode(id === "record-btn" ? "button" : "div");
    env.register(id, nodes[id]);
  }
  env.V.api = () => ({ ...OK_API, ...api });
  return { ...env, ...nodes };
}

const settle = () => new Promise((r) => setImmediate(r));

// a fake container's textContent doesn't include its descendants
const allText = (node) => [node.textContent || "", ...(node.children || []).map(allText)].join(" ");
const flatten = (node) => [node, ...(node.children || []).flatMap(flatten)];
const byClass = (node, cls) => flatten(node).filter((n) => String(n.className || "").split(/\s+/).includes(cls));

const banner = (h) => h["perm-strip"].children[0];
const buttonsIn = (node) => node.children.map((b) => b.textContent);
const actionsOf = (h) => buttonsIn(byClass(banner(h), "banner-actions")[0]);
const recoverOf = (h) => byClass(banner(h), "banner-recover")[0];
const statusLabel = (h) => allText(byClass(h["home-status"], "status-label")[0]).trim();
const statusDetail = (h) => byClass(h["home-status"], "status-detail")[0].textContent;
const feedRows = (h) => byClass(h["recent-groups"], "item-row");

const pane = (key, label, granted, hotkey = false, effective = true) =>
  ({ key, label, granted, hotkey, effective });
const panes = (...list) => ({ get_permissions: async () => ({ panes: list }) });
const MIC_OK = pane("microphone", "Microphone", true);

test("home render() fills every panel through V.ui.$: greeting, banner, stats, status and the feed", async () => {
  const h = mountHome({});

  await h.V.views.home.render();

  assert.ok(["Working late", "Good morning", "Good afternoon", "Good evening"]
    .includes(h.greeting.textContent), `unexpected greeting: ${h.greeting.textContent}`);
  // named from hotkey_display(), without the symbol a headline doesn't need
  assert.equal(h["ptt-key-name"].textContent, "Right Option");
  assert.equal(h["home-hint"].textContent, "Let go and it lands at your cursor.");

  // 13320s -> 222 whole minutes -> 3h 42m (formatDuration rounds to minutes first)
  assert.equal(h["stat-saved"].textContent, "3h 42m");
  assert.equal(h["stat-words"].textContent, "12.5K");
  assert.equal(h["stat-words"].title, `${(12480).toLocaleString()} words`, "the exact count is on hover");
  assert.equal(h["stat-streak"].textContent, (6).toLocaleString());

  // 7 items in history, five on Home.
  assert.equal(feedRows(h).length, 5);
  assert.equal(feedRows(h)[0].dataset.id, "h0");
  assert.equal(h["recent-empty"].hidden, true, "the empty-state line hides once there are rows");
  assert.equal(h["home-view-all"].hidden, false);

  assert.equal(h["perm-strip"].dataset.state, "ok");
  assert.equal(h["perm-strip"].children.length, 0, "nothing to say: no blue banner");
  assert.equal(h["perm-strip"].hidden, true);
  assert.equal(h["home-hero"].hidden, false);
  assert.equal(statusLabel(h), "Ready");
});

test("the feed groups rows by real day, with each row's time beside its text", async () => {
  const DAY = 86400;
  const h = mountHome({ get_history: async () => [
    { id: "a", ts: 1_750_000_000, text: "one", words: 1 },
    { id: "b", ts: 1_750_000_000 - 2 * DAY, text: "two", words: 1 },
  ] });
  await h.V.views.home.render();
  const groups = byClass(h["recent-groups"], "day-group");
  assert.equal(groups.length, 2, "a day apart, so two labels");
  assert.deepEqual(groups.map((g) => byClass(g, "item-row").length), [1, 1]);
  assert.ok(byClass(groups[0], "date-label")[0].textContent);
  assert.ok(byClass(feedRows(h)[0], "row-time")[0].textContent, "the time column");
  assert.equal(byClass(feedRows(h)[0], "item-meta").length, 0, "no word count line");
});

test("a snippet's row says so, because it only holds the phrase", async () => {
  const h = mountHome({ get_history: async () => [
    { id: "a", ts: 1_750_000_000, text: "my email", words: 2, snippet: true },
    { id: "b", ts: 1_750_000_000, text: "a sentence", words: 2 },
  ] });
  await h.V.views.home.render();
  assert.equal(byClass(feedRows(h)[0], "tag")[0].textContent, "Snippet");
  assert.equal(byClass(feedRows(h)[1], "tag").length, 0);
});

test("the empty feed says so, with nothing to view", async () => {
  const h = mountHome({ get_history: async () => [] });
  // Start from "already hidden", i.e. the state a previous non-empty render
  // left behind. Asserting against a fresh node instead would pass without
  // render() ever running: `hidden` is false by default.
  h["recent-empty"].hidden = true;

  await h.V.views.home.render();

  assert.equal(feedRows(h).length, 0);
  assert.equal(h["recent-empty"].hidden, false);
  assert.equal(h["home-view-all"].hidden, true);
});

test("home render() degrades one panel at a time: a failed get_stats still leaves the feed rendered", async (t) => {
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
  assert.equal(feedRows(h).length, 5, "the feed is a separate panel and must still render");
  assert.equal(h["ptt-key-name"].textContent, "Right Option", "so must the banner");
  assert.equal(statusLabel(h), "Ready", "so must the status");

  const toastText = h.toast.children.map((c) => c.textContent).join("");
  assert.match(toastText, /Load stats failed: db locked/);
  assert.doesNotMatch(toastText, /Home failed to load/,
    "one dead panel is not a whole-view failure");
});

test("large word counts are compact; small ones are exact", () => {
  const { compactCount } = mountHome({}).V.views.home;
  assert.equal(compactCount(0), "0");
  assert.equal(compactCount(999), "999");
  assert.equal(compactCount(1000), "1K");
  assert.equal(compactCount(41300), "41.3K");
  assert.equal(compactCount(999949), "999.9K");
  assert.equal(compactCount(999950), "1M", "never 1000K");
  assert.equal(compactCount(1234567), "1.2M");
});

test("no time saved yet reads 0m, not a dash beside the word saved", async () => {
  const h = mountHome({ get_stats: async () => ({ total_words: 0, streak_days: 0, time_saved_seconds: 0 }) });
  await h.V.views.home.render();
  assert.equal(h["stat-saved"].textContent, "0m");
  assert.equal(h["stat-words"].textContent, "0");
});

for (const [display, shown] of [
  ["Left Option (⌥)", "Left Option"], ["Fn / Globe", "Fn / Globe"], ["F13", "F13"], ["unset", "your key"],
]) {
  test(`the banner names ${display} as "${shown}"`, async () => {
    const h = mountHome({ hotkey_display: async () => ({ display }) });
    await h.V.views.home.render();
    assert.equal(h["ptt-key-name"].textContent, shown);
  });
}

test("Change key opens Settings at the key's section", () => {
  const h = mountHome({});
  const shown = [];
  h.V.show = (...args) => { shown.push(args); };
  h.V.views.home.init();
  h["change-key"].dispatch("click");
  assert.deepEqual(shown, [["settings", { section: "key" }]]);
});

/* ---------- how "Ready" maps onto the real states ---------- */

test("Ready lists the panes this mode really needs", async () => {
  const active = mountHome(panes(MIC_OK, pane("accessibility", "Accessibility", true, true)));
  await active.V.views.home.render();
  assert.equal(statusDetail(active), "Microphone and Accessibility granted.");

  const listen = mountHome({});
  await listen.V.views.home.render();
  assert.equal(statusDetail(listen), "Microphone, Input Monitoring and Accessibility granted.");
});

test("a mic macOS hasn't asked about yet is Almost ready, and nothing is blue", async () => {
  // H2 (2026-10-12 audit): the mic prompt only appears on the first
  // recording, so holding the key is exactly how a new user gets past it.
  const h = mountHome(panes(pane("microphone", "Microphone", null),
                            pane("accessibility", "Accessibility", true, true)));
  await h.V.views.home.render();
  assert.equal(statusLabel(h), "Almost ready");
  assert.match(statusDetail(h), /will ask for the microphone the first time you dictate/);
  assert.equal(h["perm-strip"].dataset.state, "pending");
  assert.equal(h["perm-strip"].children.length, 0);
  assert.equal(h["home-hero"].hidden, false, "holding the key is how they get asked");
  assert.equal(byClass(h["home-status"], "status-dot").length, 0);
});

test("a tap still starting is undecided, not broken", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let status = { status: "starting push-to-talk on Right Option…", state: "off" };
  const h = mountHome({ get_hotkey_status: async () => status });
  await h.V.views.home.render();
  assert.equal(statusLabel(h), "Starting…");
  assert.equal(h["home-hero"].hidden, false);
  assert.equal(h["perm-strip"].children.length, 0, "no alarm while it comes up");

  status = { status: "active on Right Option", state: "listening" };
  t.mock.timers.tick(1000);
  await settle();
  assert.equal(statusLabel(h), "Ready", "and Home notices without a click");
});

for (const status of ["not started", "active on Right Option"]) {
  test(`a listener that's off with status "${status}" is only held up, so no alarm`, async (t) => {
    // the page can load before the tap starts, and Settings' key capture stops it
    // for up to 8 s with "active on" still in place
    t.mock.timers.enable({ apis: ["setInterval"] });
    const h = mountHome({ get_hotkey_status: async () => ({ status, state: "off" }) });
    await h.V.views.home.render();
    assert.equal(statusLabel(h), "Starting…");
    assert.equal(h["perm-strip"].children.length, 0);
    assert.equal(h["home-hero"].hidden, false);
  });
}

test("busy says the key is paused", async () => {
  const h = mountHome({ get_hotkey_status: async () => ({ status: "x", state: "busy" }) });
  await h.V.views.home.render();
  assert.equal(statusLabel(h), "Recording here");
  assert.match(statusDetail(h), /paused/);
});

test("a listener that's off with everything granted says so, and offers a way out", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const calls = [];
  const h = mountHome({
    get_hotkey_status: async () => ({ status: "push-to-talk not running: boom", state: "off" }),
    relaunch_app: async () => { calls.push("relaunch"); return { ok: true }; },
  });
  await h.V.views.home.render();
  assert.equal(h["perm-strip"].dataset.state, "attention");
  assert.equal(h["home-hero"].hidden, true, "Hold the key would be a lie");
  assert.match(allText(banner(h)), /The key isn't listening/);
  assert.deepEqual(actionsOf(h), ["Relaunch Voisr", "Click to record"]);
  assert.equal(statusLabel(h), "Needs you");
  assert.equal(statusDetail(h), "Push-to-talk not running: boom.");
  byClass(banner(h), "banner-actions")[0].children[0].dispatch("click");
  await settle();
  assert.deepEqual(calls, ["relaunch"]);
});

test("a stopped listener doesn't raise the alarm before the permissions have answered", async () => {
  // it stops for a missing grant too, and that gets a better banner
  const h = mountHome({ get_hotkey_status: async () => ({ status: "waiting", state: "off" }) });
  await h.V.views.home.renderListener();
  assert.equal(h["perm-strip"].children.length, 0);
});

test("home render() does not claim it is ready when the status call fails", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = mountHome({ get_hotkey_status: async () => ({ error: "bridge gone" }) });
  await h.V.views.home.render();
  assert.equal(statusLabel(h), "Status unknown");
  assert.equal(h["home-hero"].hidden, false, "and it doesn't raise the alarm either");
  assert.equal(byClass(h["home-status"], "status-dot").length, 0);
});

test("a failed permissions read never reads as Ready", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = mountHome({ get_permissions: async () => ({ error: "bridge gone" }) });
  await h.V.views.home.render();
  assert.equal(statusLabel(h), "Status unknown");
});

test("the status doesn't flash unknown before both checks have answered", async () => {
  const h = mountHome({});
  await h.V.views.home.renderPermissions();
  assert.equal(h["home-status"].children.length, 0);
});

/* ---------- needs you ---------- */

test("a missing hotkey grant takes the banner's place and says what broke", async () => {
  const h = mountHome(panes(MIC_OK,
    pane("input_monitoring", "Input Monitoring", false, true),
    pane("accessibility", "Accessibility", null)));

  await h.V.views.home.renderPermissions();

  assert.equal(h["perm-strip"].dataset.state, "attention");
  assert.equal(h["perm-strip"].hidden, false);
  assert.equal(banner(h).className, "banner banner-needs-you");
  // A dead push-to-talk key is not a footnote: the banner promising it works
  // must be gone, not left sitting above the warning.
  assert.equal(h["home-hero"].hidden, true);

  const text = allText(banner(h));
  assert.match(text, /Push-to-talk isn't working/,
    "the heading names what broke, not which setting is missing");
  assert.match(text, /holding it does nothing/);
  assert.match(text, /Click to record still works/);

  // worst first: the known denial leads, not the merely unverified one
  assert.deepEqual(actionsOf(h), ["Open Input Monitoring", "Click to record"],
    "a blocked key still offers another way to keep working");
});

test("every pane stays listed in the status panel, so one trip to System Settings can clear them all", async () => {
  const h = mountHome(panes(MIC_OK,
    pane("input_monitoring", "Input Monitoring", false, true),
    pane("accessibility", "Accessibility", null)));
  await h.V.views.home.render();
  const checks = byClass(h["home-status"], "perm-checks")[0];
  assert.deepEqual(checks.children.map((li) => li.className),
    ["perm-ok-row", "perm-denied-row", "perm-unknown-row"]);
  assert.equal(statusLabel(h), "Needs you");
  assert.equal(byClass(h["home-status"], "status-dot").length, 1);
  assert.equal(statusDetail(h), "Holding the key does nothing until Input Monitoring is back.");
});

test("a real denial leads, not a mic that hasn't been asked yet", async () => {
  const h = mountHome(panes(pane("microphone", "Microphone", null),
    pane("input_monitoring", "Input Monitoring", false, true),
    pane("accessibility", "Accessibility", true)));

  await h.V.views.home.render();

  assert.equal(actionsOf(h)[0], "Open Input Monitoring");
  const micRow = byClass(h["home-status"], "perm-checks")[0].children[0];
  assert.match(byClass(micRow, "perm-why")[0].textContent, /first dictation/i,
    "the mic row says it will be asked, not that it is needed");
});

test("a mic the user switched off offers no way to record", async () => {
  const h = mountHome(panes(pane("microphone", "Microphone", false),
    pane("input_monitoring", "Input Monitoring", true, true),
    pane("accessibility", "Accessibility", true)));

  await h.V.views.home.render();

  assert.match(allText(banner(h)), /Voisr can't hear you/);
  assert.deepEqual(actionsOf(h), ["Open Microphone"]);
  assert.equal(h["home-hero"].hidden, true);
  assert.equal(statusDetail(h), "Nothing records until Microphone is back.");
});

test("with the key dead and the mic off too, there's still no Click to record", async () => {
  const h = mountHome(panes(pane("microphone", "Microphone", false),
    pane("accessibility", "Accessibility", null, true)));
  await h.V.views.home.render();
  assert.ok(!actionsOf(h).includes("Click to record"));
});

test("a grant that only blocks pasting leaves the dark banner up, and only the panel asks", async (t) => {
  // Without Accessibility in listen mode the key still records and copies, so
  // the banner is still telling the truth once its hint says where the text goes.
  t.mock.timers.enable({ apis: ["setInterval"] });
  const opened = [];
  const h = mountHome({ ...panes(MIC_OK,
      pane("input_monitoring", "Input Monitoring", true, true),
      pane("accessibility", "Accessibility", false)),
    open_settings_pane: async (key) => { opened.push(key); return { ok: true }; } });

  await h.V.views.home.render();

  assert.equal(h["perm-strip"].dataset.state, "attention");
  assert.equal(h["perm-strip"].children.length, 0);
  assert.equal(h["home-hero"].hidden, false);
  assert.equal(h["home-hint"].textContent, "Let go and it's copied to your clipboard.");
  assert.equal(statusLabel(h), "Needs you");
  assert.match(statusDetail(h), /copied rather than pasted/);
  assert.doesNotMatch(statusDetail(h), /does nothing/);
  const open = byClass(h["home-status"], "status-action")[0];
  assert.equal(open.textContent, "Open Accessibility");
  open.dispatch("click");
  await settle();
  assert.deepEqual(opened, ["accessibility"]);
});

test("with auto-paste off, the banner says it copies", async () => {
  const h = mountHome({ get_config: async () => ({ paste: false }) });
  await h.V.views.home.render();
  assert.equal(h["home-hint"].textContent, "Let go and it's copied to your clipboard.");
  assert.equal(statusLabel(h), "Ready", "that's a choice, not a problem");
});

test("a focus refresh with no change leaves banner and status alone, so they aren't re-announced", async () => {
  const h = mountHome(panes(MIC_OK, pane("accessibility", "Accessibility", false, true)));
  await h.V.views.home.render();
  const [first, label] = [banner(h), h["home-status"].children[0]];
  await h.V.views.home.renderPermissions();
  await h.V.views.home.renderListener();
  assert.equal(banner(h), first, "aria-live re-reads the strip whenever its children are replaced");
  assert.equal(h["home-status"].children[0], label);
});

test("a permission change still re-renders the banner", async () => {
  let granted = true;
  const h = mountHome({ get_permissions: async () => ({ panes: [
    MIC_OK, pane("input_monitoring", "Input Monitoring", granted, true),
  ]}) });
  await h.V.views.home.render();
  assert.equal(h["perm-strip"].children.length, 0);
  granted = false;
  await h.V.views.home.renderPermissions();
  assert.equal(banner(h).className, "banner banner-needs-you");
  granted = true;
  await h.V.views.home.renderPermissions();
  assert.equal(h["perm-strip"].children.length, 0, "and it goes once fixed");
  assert.equal(h["home-hero"].hidden, false, "the banner must come back");
});

/* ---------- the record flow, in the banner ---------- */

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

/* The one-second tick is a real setInterval: a test that ends with the banner
   recording keeps Node alive, so every recording test stops it. */
async function stopRecording(h) {
  if (h["home-hero"].dataset.state === "recording") await h.V.views.home.toggleRecord();
}

test("recording tells you where the text goes, and marks the Home nav item until it stops", async () => {
  const { api } = recordingApi();
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  await settle();
  try {
    assert.equal(h["home-hero"].dataset.state, "recording");
    assert.equal(h["record-title"].textContent, "Listening");
    assert.equal(h["record-time"].textContent, "0:00");
    assert.match(h["record-status"].textContent, /Click to finish/);
    assert.match(h["record-status"].textContent, /clipboard, not pasted/);
    assert.equal(h["record-label"].textContent, "Finish");
    assert.equal(h["nav-home"].dataset.recording, "true",
      "visible from every view, since push-to-talk is dropped meanwhile");
  } catch (e) {
    await stopRecording(h);
    throw e;
  }

  await h.V.views.home.toggleRecord();
  await settle();
  assert.equal(h["nav-home"].dataset.recording, undefined);
  assert.equal(h["home-hero"].dataset.state, "done");
  assert.equal(h["record-title"].textContent, "Copied to clipboard");
  assert.equal(h["record-label"].textContent, "Click to record");
});

test("the panel says the key is paused while recording here, and listening again after", async () => {
  const { api } = recordingApi();
  const h = mountHome(api);
  await h.V.views.home.render();
  await h.V.views.home.toggleRecord();
  await settle();
  try {
    assert.equal(statusLabel(h), "Recording here");
  } finally {
    await stopRecording(h);
  }
  await settle();
  assert.equal(statusLabel(h), "Ready");
});

test("while transcribing the banner says so and the button is dead", async () => {
  let finish;
  const { api } = recordingApi({ stop_recording: () => new Promise((r) => { finish = () => r({ text: "Hi" }); }) });
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  const stopping = h.V.views.home.toggleRecord();
  await settle();
  assert.equal(h["home-hero"].dataset.state, "processing");
  assert.equal(h["record-title"].textContent, "Transcribing");
  assert.equal(h["record-btn"].getAttribute("aria-disabled"), "true");
  finish();
  await stopping;
  assert.equal(h["record-btn"].getAttribute("aria-disabled"), null);
});

test("the done state clears itself", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const { api } = recordingApi();
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  await h.V.views.home.toggleRecord();
  assert.equal(h["home-hero"].dataset.state, "done");
  t.mock.timers.tick(2600);
  assert.equal(h["home-hero"].dataset.state, undefined);
});

test("a double-click on record starts one recording and shows no error", async () => {
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
    assert.equal(h["home-hero"].dataset.state, "recording");
  } finally {
    await stopRecording(h);
  }
});

test("a failed start returns to idle with a line that stays until the next try", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { api, calls } = recordingApi();
  api.start_recording = async () => { calls.start += 1; return { error: "already recording" }; };
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  assert.equal(h["record-error"].textContent, "Couldn't start.");
  assert.equal(h["home-hero"].dataset.state, undefined);
  await h.V.views.home.toggleRecord();
  assert.equal(calls.start, 2);
});

test("a failed transcription says so", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { api } = recordingApi({ stop_recording: async () => ({ error: "whisper fell over" }) });
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  await h.V.views.home.toggleRecord();
  assert.equal(h["record-error"].textContent, "Transcription failed.");
  assert.equal(h["home-hero"].dataset.state, undefined);
});

test("a dictation whose History write failed says so instead of a bare 'Copied'", async () => {
  const { api } = recordingApi({
    stop_recording: async () => ({ text: "Hi", copied: true, warning: "history failed: disk full" }),
  });
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  await h.V.views.home.toggleRecord();
  assert.match(h["record-error"].textContent, /History/);
});

test("a clipboard that failed is never reported as Copied", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { api } = recordingApi({ stop_recording: async () => ({ text: "Hi", copied: false }) });
  const h = mountHome(api);
  await h.V.views.home.toggleRecord();
  await h.V.views.home.toggleRecord();
  assert.equal(h["home-hero"].dataset.state, undefined);
  assert.match(h["record-error"].textContent, /Couldn't copy/);
});

test("the wave speeds up while listening and slows again for transcribing", async () => {
  const { api } = recordingApi();
  const h = mountHome(api);
  const speeds = [];
  h.V.wave = { setSpeed: (el, n) => { speeds.push([el, n]); } };
  await h.V.views.home.toggleRecord();
  await h.V.views.home.toggleRecord();
  assert.deepEqual(speeds.map(([, n]) => n), [3.2, 1]);
  assert.ok(speeds.every(([el]) => el === h["home-wave"]));
});

test("dictateFromAnywhere switches to Home before recording, so the banner is in view", async () => {
  const { api, calls } = recordingApi();
  const h = mountHome(api);
  const shown = [];
  h.V.show = (view) => { shown.push(view); };
  try {
    await h.V.views.home.dictateFromAnywhere();
    assert.deepEqual(shown, ["home"]);
    assert.equal(calls.start, 1);
  } finally {
    await stopRecording(h);
  }
});

const DEAD_KEY = panes(MIC_OK, pane("input_monitoring", "Input Monitoring", false, true),
                       pane("accessibility", "Accessibility", true));

function clickToRecord(h) {
  const button = byClass(banner(h), "banner-actions")[0].children
    .find((b) => b.textContent === "Click to record");
  button.dispatch("click", { target: button });
}

/* A recording's outcome under a blue banner: the dark banner holding
   #record-error is hidden, so the blue one has to say it. */
const blueError = (h) => byClass(banner(h), "banner-error")[0];

const OUTCOMES = [
  ["a failed start", { start_recording: async () => { throw new Error("no mic"); } }, 1, "Couldn't start."],
  ["a failed transcription", { stop_recording: async () => { throw new Error("nope"); } }, 2, "Transcription failed."],
  ["a failed copy", { stop_recording: async () => ({ text: "Hi", copied: false }) }, 2,
   "Couldn't copy it. Copy it from the list below."],
];

for (const [what, extra, clicks, message] of OUTCOMES) {
  test(`${what} stays on screen with a blue banner up`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
    const { api } = recordingApi(extra);
    const h = mountHome({ ...api, ...DEAD_KEY });
    await h.V.views.home.renderPermissions();
    assert.equal(blueError(h).textContent, "");

    for (let i = 0; i < clicks; i++) await h.V.views.home.toggleRecord();
    await settle();

    assert.equal(h["home-hero"].hidden, true, "the dark banner has gone back under the blue one");
    assert.equal(h["perm-strip"].hidden, false);
    assert.equal(blueError(h).textContent, message);
  });
}

test("'not saved to History' outlasts the done state under a blue banner", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const { api } = recordingApi({ stop_recording: async () => ({ text: "Hi", warning: "disk full" }) });
  const h = mountHome({ ...api, ...DEAD_KEY });
  await h.V.views.home.renderPermissions();
  await h.V.views.home.toggleRecord();
  await h.V.views.home.toggleRecord();
  assert.equal(h["home-hero"].dataset.state, "done");
  assert.equal(h["record-error"].textContent, "Copied, but not saved to History.");

  t.mock.timers.tick(2600);
  await settle();

  assert.equal(h["home-hero"].hidden, true);
  assert.equal(blueError(h).textContent, "Copied, but not saved to History.");
});

test("the message survives the blue banner being rebuilt, and clears when the next recording starts", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let current = DEAD_KEY.get_permissions;
  const { api } = recordingApi({ stop_recording: async () => ({ text: "Hi", copied: false }),
                                 get_permissions: () => current() });
  const h = mountHome(api);
  await h.V.views.home.renderPermissions();
  await h.V.views.home.toggleRecord();
  await h.V.views.home.toggleRecord();
  await settle();
  const before = banner(h);

  // a different pane is now to blame, so apply() builds a new banner
  current = panes(MIC_OK, pane("input_monitoring", "Input Monitoring", true, true),
                  pane("accessibility", "Accessibility", true, false, false)).get_permissions;
  await h.V.views.home.renderPermissions();
  assert.notEqual(banner(h), before, "the banner really was rebuilt");
  assert.match(blueError(h).textContent, /Couldn't copy/);

  // a fixed grant takes the blue banner away: the dark one still says it
  current = panes(MIC_OK, pane("input_monitoring", "Input Monitoring", true, true),
                  pane("accessibility", "Accessibility", true)).get_permissions;
  await h.V.views.home.renderPermissions();
  assert.equal(h["perm-strip"].hidden, true);
  assert.match(h["record-error"].textContent, /Couldn't copy/);

  // and back to blue, then the next recording clears both
  current = DEAD_KEY.get_permissions;
  await h.V.views.home.renderPermissions();
  assert.match(blueError(h).textContent, /Couldn't copy/);
  await h.V.views.home.toggleRecord();
  assert.equal(h["record-error"].textContent, "");
  assert.equal(blueError(h).textContent, "");
  await stopRecording(h);
});

test("'Click to record' under a blue banner brings the recording up in the dark one, then goes back", async (t) => {
  // M11: the button has to start something: focus alone looked like it did nothing
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const { api, calls } = recordingApi();
  const h = mountHome({ ...api, ...DEAD_KEY });
  await h.V.views.home.renderPermissions();
  assert.equal(h["home-hero"].hidden, true);
  clickToRecord(h);
  await settle();
  assert.equal(calls.start, 1);
  assert.equal(h["home-hero"].dataset.state, "recording");
  assert.equal(h["home-hero"].hidden, false, "the mic is open: show it");
  assert.equal(h["perm-strip"].hidden, true);

  await h.V.views.home.toggleRecord();
  await settle();
  assert.equal(h["home-hero"].hidden, false, "the result shows there too");
  t.mock.timers.tick(2600);
  assert.equal(h["home-hero"].hidden, true, "and the warning comes back");
  assert.equal(h["perm-strip"].hidden, false);
});

test("'Click to record' never stops a recording already running", async () => {
  const { api, calls } = recordingApi();
  const h = mountHome({ ...api, ...DEAD_KEY });
  await h.V.views.home.renderPermissions();
  await h.V.views.home.toggleRecord();          // recording from the banner
  try {
    clickToRecord(h);
    await settle();
    assert.equal(calls.stop, 0);
    assert.equal(h["home-hero"].dataset.state, "recording");
  } finally {
    await stopRecording(h);
  }
});

/* ---------- stats count up ----------

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

const statsWith = (over) => async () => ({ ...(await OK_API.get_stats()), ...over });
// exact, so a tween can be read: below 1000 the panel doesn't round
const smallWords = (n) => statsWith({ total_words: n });
const num = (s) => Number(String(s).replace(/\D/g, ""));

test("opening Home shows the stats as they are: no count from zero", async () => {
  const h = mountHome({});
  const f = frames(h);
  await h.V.views.home.render({ entering: true });
  assert.equal(h["stat-words"].textContent, "12.5K");
  assert.equal(h["stat-saved"].textContent, "3h 42m");
  assert.equal(f.pending, 0, "nothing to tween on open");
});

test("a stat that changes while Home is on screen counts up over 600 ms", async () => {
  const api = { get_stats: smallWords(100) };
  const h = mountHome(api);
  const f = frames(h);
  await h.V.views.home.render({ entering: true });

  api.get_stats = statsWith({ total_words: 900, time_saved_seconds: 13920 });
  await h.V.views.home.render();
  assert.equal(num(h["stat-words"].textContent), 100, "starts from the old value");

  f.run(1000);                       // first frame: the clock starts here
  assert.equal(num(h["stat-words"].textContent), 100);
  f.run(1300);                       // halfway in time, well past halfway in value (ease-out)
  const mid = num(h["stat-words"].textContent);
  assert.ok(mid > 500 && mid < 900, `halfway frame showed ${mid}`);
  f.run(1600);
  assert.equal(h["stat-words"].textContent, "900");
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
  const api = { get_stats: smallWords(100) };
  const h = mountHome(api);
  const f = frames(h);
  await h.V.views.home.render({ entering: true });
  api.get_stats = smallWords(500);
  await h.V.views.home.render();
  f.run(0);
  f.run(300);
  const shown = num(h["stat-words"].textContent);

  api.get_stats = smallWords(900);
  await h.V.views.home.render();
  f.run(500);
  assert.equal(num(h["stat-words"].textContent), shown, "the second count starts where the first stood");
  f.run(1100);
  assert.equal(h["stat-words"].textContent, "900");
});

test("reduced motion jumps a changed stat straight to its value", async () => {
  const api = { get_stats: smallWords(100) };
  const h = mountHome(api);
  const f = frames(h);
  h.window.matchMedia = (q) => ({ matches: /reduce/.test(q), addEventListener() {} });
  await h.V.views.home.render({ entering: true });
  api.get_stats = smallWords(900);
  await h.V.views.home.render();
  assert.equal(h["stat-words"].textContent, "900");
  assert.equal(f.pending, 0);
});

test("a feed refresh that fails mid-animation says so instead of an unhandled rejection", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const api = {};
  const h = mountHome(api);
  h["recent-groups"].animate = () => ({ finished: Promise.resolve() });  // lists.js animates it
  await h.V.views.home.render({ entering: true });

  api.get_history = async () => [{ id: "new", ts: 1_750_000_000, text: "fresh", words: 1, duration_s: 1 }, ...HISTORY];
  h.V.ui.itemRow = () => { throw new Error("bad row"); };
  await h.V.views.home.render();
  await settle();

  const toastText = h.toast.children.map((c) => c.textContent).join("");
  assert.match(toastText, /Recent dictations failed to update/);
});

/* ---------- a granted-but-refused hotkey grant, Reset/Relaunch, polling ---------- */

const STALE_API = panes(MIC_OK, pane("accessibility", "Accessibility", true, true, false));

test("a granted-but-not-effective hotkey pane blocks with Reset and Relaunch", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const h = mountHome(STALE_API);
  await h.V.views.home.render();
  assert.equal(h["perm-strip"].dataset.state, "attention");
  const text = allText(banner(h));
  assert.match(text, /macOS is still blocking Accessibility/);
  assert.deepEqual(actionsOf(h), ["Reset Accessibility", "Open System Settings", "Click to record"]);
  assert.match(allText(recoverOf(h)), /Relaunch Voisr/);
  assert.equal(h["home-hero"].hidden, true, "the key doesn't work, so don't promise it");
  assert.equal(statusDetail(h), "Holding the key does nothing until Accessibility is back.");
  const row = byClass(h["home-status"], "perm-checks")[0].children[1];
  assert.equal(byClass(row, "perm-why")[0].textContent, "switched on, not working");
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
  for (let i = 0; i < 3; i++) {
    t.mock.timers.tick(1000);
    await settle();   // each tick's calls finish before the next (see in-flight guard)
  }
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
  const reset = byClass(banner(h), "banner-actions")[0].children[0];
  const relaunch = byClass(recoverOf(h), "text-btn")[0];
  const toastText = () => h.toast.children.map((c) => c.textContent).join("|");
  reset.dispatch("click", { target: reset });
  await settle();
  assert.match(toastText(), /Switch Voisr on under Accessibility/);
  relaunch.dispatch("click", { target: relaunch });
  await settle();
  assert.match(toastText(), /no bundle/);
  assert.deepEqual(calls, [["reset", "accessibility"], ["relaunch"]]);
});

const hotkeyPane = (granted, effective) => ({ panes: [
  pane("microphone", "Microphone", true),
  pane("accessibility", "Accessibility", granted, true, effective),
]});

for (const [granted, label] of [[null, "unknown"], [false, "denied"]]) {
  test(`a ${label} hotkey pane with effective:false is not called "switched on, not working"`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
    const h = mountHome({ get_permissions: async () => hotkeyPane(granted, false) });
    await h.V.views.home.render();
    assert.equal(h["perm-strip"].dataset.state, "attention");
    const text = allText(banner(h));
    assert.doesNotMatch(text, /still blocking/);
    assert.doesNotMatch(allText(h["home-status"]), /switched on, not working/);
    assert.match(text, /Open Accessibility/);
    // only a known denial offers the "already switched on?" reset (R15)
    if (granted === null) assert.doesNotMatch(text, /Reset /);
    else assert.match(text, /Reset Accessibility/);
  });
}

test("polling stops once permissions flip to all granted", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let fixed = false;
  let calls = 0;
  const h = mountHome({ get_permissions: async () => {
    calls += 1;
    return hotkeyPane(true, fixed);
  } });
  await h.V.views.home.render();
  fixed = true;
  t.mock.timers.tick(1000);
  await settle();
  assert.equal(h["perm-strip"].dataset.state, "ok");
  const after = calls;
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(calls, after, "no get_permissions calls once nothing is blocked");
});

test("repeated renders while blocked don't stack poll intervals", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let calls = 0;
  const h = mountHome({ get_permissions: async () => {
    calls += 1;
    return hotkeyPane(true, false);
  } });
  await h.V.views.home.render();
  await h.V.views.home.render();
  await h.V.views.home.renderPermissions();
  const before = calls;
  t.mock.timers.tick(1000);
  await settle();
  assert.equal(calls - before, 1, "one tick, one poll");
});

test("the poll tick does nothing while Home is hidden", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let calls = 0;
  const h = mountHome({ get_permissions: async () => {
    calls += 1;
    return hotkeyPane(true, false);
  } });
  await h.V.views.home.render();
  h["view-home"].hidden = true;
  const before = calls;
  t.mock.timers.tick(3000);
  await settle();
  assert.equal(calls, before);
});

test("the poll tick does nothing while the window is hidden: the app stays resident", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let calls = 0;
  const h = mountHome({ get_permissions: async () => {
    calls += 1;
    return hotkeyPane(true, false);
  } });
  await h.V.views.home.render();
  h.document.visibilityState = "hidden";
  const before = calls;
  t.mock.timers.tick(3000);
  await settle();
  assert.equal(calls, before);
});

test("a failing bridge doesn't toast every second from the poll", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let broken = false;
  const h = mountHome({
    get_permissions: async () => {
      if (broken) throw new Error("bridge down");
      return hotkeyPane(true, false);
    },
    get_hotkey_status: async () => {
      if (broken) throw new Error("bridge down");
      return { state: "listening" };
    },
  });
  await h.V.views.home.render();
  broken = true;
  t.mock.timers.tick(3000);
  await settle();
  assert.equal(h.toast.children.map((c) => c.textContent).join(""), "",
    "the poll is quiet; user-initiated renders still toast");
  await h.V.views.home.renderPermissions();
  assert.match(h.toast.children.map((c) => c.textContent).join(""), /bridge down/);
});

test("Reset without an error string still says it couldn't", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const h = mountHome({ ...STALE_API, reset_permission: async () => ({ ok: false }) });
  await h.V.views.home.render();
  const reset = byClass(banner(h), "banner-actions")[0].children[0];
  reset.dispatch("click", { target: reset });
  await settle();
  assert.match(h.toast.children.map((c) => c.textContent).join(""), /Couldn't reset Accessibility/);
});

const LISTEN_DENIED_HOTKEY = panes(MIC_OK,
  pane("input_monitoring", "Input Monitoring", false, true),
  pane("accessibility", "Accessibility", true));

test("a denied hotkey pane also offers Reset and Relaunch for a switch that only looks off", async (t) => {
  // listen mode's preflight can read false while the switch shows ON: Open
  // alone sent the user to a pane that already looked right, a dead end
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const calls = [];
  const h = mountHome({ ...LISTEN_DENIED_HOTKEY,
    reset_permission: async (key) => { calls.push(["reset", key]); return { ok: true }; },
    relaunch_app: async () => { calls.push(["relaunch"]); return { ok: true }; },
  });
  await h.V.views.home.render();
  assert.deepEqual(actionsOf(h), ["Open Input Monitoring", "Click to record"]);
  assert.ok(recoverOf(h), "a recovery row under the main actions");
  assert.match(allText(recoverOf(h)), /Already switched on\?/);
  const buttons = byClass(recoverOf(h), "text-btn");
  assert.deepEqual(buttons.map((b) => b.textContent), ["Reset Input Monitoring", "Relaunch Voisr"]);
  buttons[0].dispatch("click", { target: buttons[0] });
  await settle();
  buttons[1].dispatch("click", { target: buttons[1] });
  await settle();
  assert.deepEqual(calls, [["reset", "input_monitoring"], ["relaunch"]]);
  assert.match(h.toast.children.map((c) => c.textContent).join("|"),
               /Switch Voisr on under Input Monitoring/);
});

test("an unknown (null) hotkey pane gets no Reset", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const h = mountHome(panes(MIC_OK,
    pane("input_monitoring", "Input Monitoring", null, true),
    pane("accessibility", "Accessibility", true)));
  await h.V.views.home.render();
  const text = allText(banner(h));
  assert.match(text, /Open Input Monitoring/);
  assert.doesNotMatch(text, /Reset /);
  assert.equal(recoverOf(h), undefined);
});

test("a denied hotkey accessibility pane says it is needed for push-to-talk and pasting", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const h = mountHome({ get_permissions: async () => hotkeyPane(false, true) });
  await h.V.views.home.render();
  const accessRow = byClass(h["home-status"], "perm-checks")[0].children[1];
  assert.match(accessRow.className, /perm-denied-row/);
  assert.equal(byClass(accessRow, "perm-why")[0].textContent, "needed for push-to-talk and pasting");
});

test("a poll tick is skipped while the previous tick's calls are still pending", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let calls = 0;
  let hang = false;
  const h = mountHome({ get_permissions: () => {
    calls += 1;
    return hang ? new Promise(() => {}) : LISTEN_DENIED_HOTKEY.get_permissions();
  } });
  await h.V.views.home.render();
  hang = true;
  const before = calls;
  t.mock.timers.tick(1000);
  await settle();
  assert.equal(calls, before + 1, "the first tick polls");
  for (let i = 0; i < 4; i++) {
    t.mock.timers.tick(1000);
    await settle();
  }
  assert.equal(calls, before + 1, "a slow bridge never gets a second overlapping poll");
});

/* ---------- Listen-only fallback: the key works, Accessibility is stale ---------- */

const FALLBACK_PANES = [
  pane("microphone", "Microphone", true),
  pane("input_monitoring", "Input Monitoring", true, true),
  pane("accessibility", "Accessibility", true, false, false),
];

test("on the fallback, a stale Accessibility says the key still works, never that it does nothing", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const h = mountHome({ get_permissions: async () => ({ panes: FALLBACK_PANES, fallback: true }) });
  await h.V.views.home.render();
  const text = allText(banner(h));
  assert.equal(h["perm-strip"].dataset.state, "attention");
  assert.match(text, /macOS is still blocking Accessibility/);
  assert.match(text, /keeps working through Input Monitoring/);
  assert.match(text, /Reset Accessibility/);
  assert.match(allText(recoverOf(h)), /Accessibility back on\?/);
  assert.match(allText(recoverOf(h)), /Relaunch Voisr/);
  assert.equal(statusLabel(h), "Needs you");
  assert.equal(statusDetail(h), "The key still works through Input Monitoring.");
  assert.doesNotMatch(allText(h["home-status"]), /does nothing/);
});

test("without the fallback flag, the stale banner has no fallback sentence", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const h = mountHome({ get_permissions: async () => ({ panes: FALLBACK_PANES }) });
  await h.V.views.home.render();
  assert.doesNotMatch(allText(banner(h)), /Input Monitoring meanwhile/);
  assert.match(allText(recoverOf(h)), /Still blocked\?/);
  assert.doesNotMatch(statusDetail(h), /does nothing/, "a non-hotkey pane doesn't kill the key");
});
