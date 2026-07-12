"use strict";

/* ---------- helpers ---------- */

const $ = (sel) => document.querySelector(sel);

const ICONS = {
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>',
  arrow: '<svg class="dict-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>',
};

function escapeHtml(s) {
  // must also cover quotes: the result is interpolated into attribute values
  return String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function timeOf(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function dayOf(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}

let toastTimer = null;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

const api = () => window.pywebview.api;

/* ---------- navigation ---------- */

function show(view) {
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "home") renderHome();
  if (view === "history") renderHistory();
  if (view === "dictionary") renderDictionary();
  if (view === "settings") renderSettings();
}

/* ---------- home ---------- */

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Working late";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

async function renderHome() {
  $("#greeting").textContent = greeting();
  const [stats, history] = await Promise.all([api().get_stats(), api().get_history()]);
  $("#stat-words").textContent = stats.total_words.toLocaleString();
  $("#stat-wpm").textContent = stats.avg_wpm.toLocaleString();
  $("#stat-streak").textContent = stats.streak_days.toLocaleString();

  const recent = history.slice(0, 5);
  $("#recent-list").innerHTML = recent.map(itemRowHtml).join("");
  $("#recent-empty").hidden = recent.length > 0;
  bindItemRows($("#recent-list"));
}

function itemRowHtml(item) {
  return `<li class="item-row" data-id="${item.id}" data-text="${escapeHtml(item.text)}">
    <div class="item-text">
      <div class="txt">${escapeHtml(item.text)}</div>
      <div class="item-meta">${timeOf(item.ts)} · ${item.words} words</div>
    </div>
    <button class="icon-btn act-copy" aria-label="Copy text">${ICONS.copy}</button>
    <button class="icon-btn danger act-delete" aria-label="Delete dictation">${ICONS.trash}</button>
  </li>`;
}

function bindItemRows(root) {
  root.querySelectorAll(".act-copy").forEach((b) => b.addEventListener("click", async (e) => {
    const row = e.currentTarget.closest(".item-row");
    await api().copy_text(row.dataset.text);
    toast("Copied to clipboard");
  }));
  root.querySelectorAll(".act-delete").forEach((b) => b.addEventListener("click", async (e) => {
    const row = e.currentTarget.closest(".item-row");
    if (!confirm("Delete this dictation? This can't be undone.")) return;
    await api().delete_history_item(row.dataset.id);
    toast("Dictation deleted");
    renderHome();
    if (!$("#view-history").hidden) renderHistory();
  }));
}

/* ---------- record flow ---------- */

let recState = "idle"; // idle | recording | processing

async function toggleRecord() {
  const btn = $("#record-btn");
  const status = $("#record-status");
  const errEl = $("#record-error");

  if (recState === "processing") return;

  if (recState === "idle") {
    errEl.textContent = "";
    $("#record-result").hidden = true;
    const res = await api().start_recording();
    if (res.error) { errEl.textContent = res.error; return; }
    recState = "recording";
    btn.classList.add("recording");
    btn.setAttribute("aria-label", "Stop recording");
    status.textContent = "Listening… click again to finish";
    return;
  }

  // recording -> stop
  recState = "processing";
  btn.classList.remove("recording");
  btn.classList.add("processing");
  status.textContent = "Transcribing on this Mac…";
  const res = await api().stop_recording();
  btn.classList.remove("processing");
  btn.setAttribute("aria-label", "Start recording");
  recState = "idle";

  if (res.error) {
    status.textContent = "Click to start dictating";
    $("#record-error").textContent = res.error;
    return;
  }
  status.textContent = "Click to start dictating";
  $("#result-text").textContent = res.text;
  $("#record-result").hidden = false;
  renderHome();
}

/* ---------- history ---------- */

async function renderHistory() {
  const items = await api().get_history();
  const container = $("#history-groups");
  $("#history-empty").hidden = items.length > 0;

  const groups = new Map();
  for (const item of items) {
    const day = dayOf(item.ts);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(item);
  }

  container.innerHTML = [...groups.entries()].map(([day, rows]) => `
    <div class="day-group">
      <div class="day-label">${day}</div>
      <div class="card"><ul class="item-list">${rows.map(itemRowHtml).join("")}</ul></div>
    </div>`).join("");
  bindItemRows(container);
}

/* ---------- dictionary ---------- */

async function renderDictionary() {
  const cfg = await api().get_config();
  const entries = Object.entries(cfg.dictionary || {});
  $("#dict-empty").hidden = entries.length > 0;
  $("#dict-list").innerHTML = entries.map(([spoken, written]) => `
    <li class="item-row" data-spoken="${escapeHtml(spoken)}">
      <div class="item-text" style="display:flex;align-items:center;gap:10px;">
        <span class="dict-spoken">${escapeHtml(spoken)}</span>
        ${ICONS.arrow}
        <span class="dict-written">${escapeHtml(written)}</span>
      </div>
      <button class="icon-btn danger act-remove" aria-label="Remove entry">${ICONS.trash}</button>
    </li>`).join("");

  $("#dict-list").querySelectorAll(".act-remove").forEach((b) => b.addEventListener("click", async (e) => {
    const spoken = e.currentTarget.closest(".item-row").dataset.spoken;
    const cfg2 = await api().get_config();
    delete cfg2.dictionary[spoken];
    await api().set_config({ dictionary: cfg2.dictionary });
    toast("Entry removed");
    renderDictionary();
  }));
}

async function addDictEntry(e) {
  e.preventDefault();
  const spoken = $("#dict-spoken").value.trim().toLowerCase();
  const written = $("#dict-written").value.trim();
  if (!spoken || !written) return;
  const cfg = await api().get_config();
  const dict = cfg.dictionary || {};
  dict[spoken] = written;
  await api().set_config({ dictionary: dict });
  $("#dict-spoken").value = "";
  $("#dict-written").value = "";
  toast(`Added: ${spoken} → ${written}`);
  renderDictionary();
}

/* ---------- settings ---------- */

function miniPillHtml(value) {
  return `<div class="mini-pill" data-theme="${escapeHtml(value)}">${'<span class="mb"></span>'.repeat(7)}</div>`;
}

async function renderThemes() {
  const [cfg, themes] = await Promise.all([api().get_config(), api().get_pill_themes()]);
  const active = cfg.pill_theme || "classic";
  $("#theme-grid").innerHTML = themes.map((t) => `
    <button type="button" class="theme-opt${t.value === active ? " active" : ""}"
            data-value="${escapeHtml(t.value)}" aria-pressed="${t.value === active}">
      ${miniPillHtml(t.value)}
      <span class="theme-name">${escapeHtml(t.label)}</span>
    </button>`).join("");
}

async function renderSettings() {
  const [cfg, status] = await Promise.all([
    api().get_config(), api().get_hotkey_status(),
  ]);
  $("#paste-toggle").checked = !!cfg.paste;
  $("#widget-toggle").checked = cfg.show_widget !== false;
  $("#hotkey-current").textContent = cfg.hotkey_label || cfg.hotkey || "alt_r";
  $("#hotkey-status").textContent = status.status;
  renderThemes();
}

/* ---------- init ---------- */

function init() {
  document.querySelectorAll("[data-view]").forEach((b) =>
    b.addEventListener("click", () => show(b.dataset.view)));
  $("#record-btn").addEventListener("click", toggleRecord);
  $("#dict-form").addEventListener("submit", addDictEntry);
  $("#paste-toggle").addEventListener("change", async (e) => {
    await api().set_config({ paste: e.target.checked });
    toast(e.target.checked ? "Auto-paste enabled" : "Auto-paste disabled");
  });
  $("#widget-toggle").addEventListener("change", async (e) => {
    await api().set_config({ show_widget: e.target.checked });
    toast(e.target.checked ? "Recording widget on" : "Recording widget off");
  });
  $("#theme-grid").addEventListener("click", async (e) => {
    const btn = e.target.closest(".theme-opt");
    if (!btn) return;
    await api().set_config({ pill_theme: btn.dataset.value });
    toast(`Pill theme: ${btn.querySelector(".theme-name").textContent}`);
    renderThemes();
  });
  // pushed from Python after each dictation (hotkey ones land while the
  // dashboard is open) — refresh whichever view is on screen
  window.addEventListener("voisr:new-dictation", () => {
    if (!$("#view-home").hidden) renderHome();
    if (!$("#view-history").hidden) renderHistory();
  });
  $("#hotkey-capture").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = "Press any key…";
    $("#hotkey-current").textContent = "…";
    try {
      const res = await api().capture_hotkey();
      if (res.error) {
        toast(res.error);
      } else {
        toast(`Push-to-talk key set to ${res.label}`);
      }
    } finally {
      btn.disabled = false;
      btn.textContent = "Change…";
      renderSettings();
    }
  });
  show("home");
}

window.addEventListener("pywebviewready", init);

/* Dev fallback: when opened in a plain browser (no pywebview bridge),
   run against an in-memory mock so the UI can be previewed and tested. */
setTimeout(() => {
  if (window.pywebview) return;
  const now = Date.now() / 1000;
  let mockHistory = [
    { id: "a1", ts: now - 300, text: "Hello team, please send the invoice to TradeMate by Friday.", words: 10, duration_s: 5 },
    { id: "a2", ts: now - 7200, text: "Draft a follow-up about the Brunswick site visit next week.", words: 10, duration_s: 6 },
    { id: "a3", ts: now - 90000, text: "Remember to book the trailer for Saturday morning.", words: 8, duration_s: 4 },
  ];
  let mockCfg = { hotkey: "alt_r", paste: true, show_widget: true, device: ":0", pill_theme: "classic", dictionary: { trademake: "TradeMate", "trade mate": "TradeMate" } };
  let recording = false;
  window.pywebview = { api: {
    get_history: async () => [...mockHistory].sort((a, b) => b.ts - a.ts),
    get_stats: async () => ({ total_words: 12480, dictations: mockHistory.length, avg_wpm: 132, streak_days: 6 }),
    get_config: async () => JSON.parse(JSON.stringify(mockCfg)),
    set_config: async (u) => Object.assign(mockCfg, u),
    delete_history_item: async (id) => { mockHistory = mockHistory.filter((i) => i.id !== id); return { ok: true }; },
    copy_text: async () => ({ ok: true }),
    capture_hotkey: async () => {
      await new Promise((r) => setTimeout(r, 1200));
      mockCfg.hotkey_vk = 63; mockCfg.hotkey_label = "Fn / Globe";
      return { vk: 63, label: "Fn / Globe" };
    },
    get_hotkey_options: async () => [
      { value: "alt_r", label: "Right Option (\u2325)" }, { value: "alt_l", label: "Left Option (\u2325)" },
      { value: "cmd_r", label: "Right Command (\u2318)" }, { value: "ctrl_r", label: "Right Control (\u2303)" },
      { value: "shift_r", label: "Right Shift (\u21e7)" }, { value: "f13", label: "F13" }, { value: "f14", label: "F14" },
    ],
    get_hotkey_status: async () => ({ status: `active on ${mockCfg.hotkey}` }),
    get_pill_themes: async () => [
      { value: "classic", label: "Classic" }, { value: "ember", label: "Ember" },
      { value: "aurora", label: "Aurora" }, { value: "ocean", label: "Ocean" },
      { value: "neon", label: "Neon EQ" }, { value: "breathe", label: "Breathe" },
      { value: "scanner", label: "Scanner" },
    ],
    start_recording: async () => { recording = true; return { recording: true }; },
    stop_recording: async () => {
      if (!recording) return { error: "not recording" };
      recording = false;
      await new Promise((r) => setTimeout(r, 900));
      const item = { id: Math.random().toString(36).slice(2), ts: Date.now() / 1000, text: "This is a mock dictation rendered in browser preview.", words: 9, duration_s: 4 };
      mockHistory.push(item);
      return { text: item.text, item, copied: true };
    },
  }};
  init();
}, 300);
