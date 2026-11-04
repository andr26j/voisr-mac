"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  V.api = () => window.pywebview.api;

  /* Dev fallback: when opened in a plain browser (no pywebview bridge),
     run against an in-memory mock so the UI can be previewed and tested. */
  setTimeout(() => {
    if (window.pywebview) return;
    const now = Date.now() / 1000;
    // Preview only: the same sample as the design's boards.
    const HOUR = 3600, startOfToday = new Date().setHours(0, 0, 0, 0) / 1000;
    const today = (hours) => startOfToday + hours * HOUR;
    const yesterday = (hours) => startOfToday - 24 * HOUR + hours * HOUR;
    let mockHistory = [
      { id: "a1", ts: Math.min(today(16.2), now - 120), text: "Running ten minutes late. The 96 has stopped in Brunswick East for reasons nobody has explained.", words: 15, duration_s: 6 },
      { id: "a2", ts: Math.min(today(14.78), now - 3600), text: "Can you send me the updated quote by Friday? I'll sign it off once Sam has checked the measurements.", words: 18, duration_s: 8 },
      { id: "a3", ts: Math.min(today(11.05), now - 7200), text: "Move the permission check into the first run, so the reset only shows up when something is actually wrong.", words: 19, duration_s: 8 },
      { id: "a4", ts: Math.min(today(9.43), now - 10800), text: "my email", words: 2, duration_s: 1, snippet: true },
      { id: "a5", ts: yesterday(17.52), text: "The drawings are in the shared folder. Page three has the only change that matters.", words: 15, duration_s: 6 },
      { id: "a6", ts: yesterday(13.13), text: "Tell them Tuesday works, but not before ten.", words: 8, duration_s: 3 },
    ];
    let mockCfg = { hotkey: "alt_r", paste: true, show_widget: true, device: ":default", pill_theme: "classic", pill_color: null, dictionary: { trademake: "TradeMate", "trade mate": "TradeMate", voicer: "Voisr", voiser: "Voisr", "voice are": "Voisr", sam: "Sam" }, model: "small.en", launch_at_login: false, model_unload: "min5", use_screen_context: true, learn_from_edits: true, dictionary_auto_add: true, dismissed_banners: [] };
    // Preview only, to show the page's states. The rules for what may be saved
    // and what gets a warning are in voisr/snippets.py and are not repeated here.
    // Save a snippet called "email" to see the warning.
    const SAMPLE_WARNING = "Sample warning: the real ones come from the app.";
    let mockSnippets = { "my email": "you@example.com", "sign off": "Thanks, Sam", "my address": "12 Example Street, Brunswick East VIC 3057" };
    let mockSuggestions = [{ id: "s1", heard: "whisper cpp", written: "whisper.cpp", seen: 1 }];
    let recording = false;
    // mirrors voisr/ui.py's HOTKEY_OPTIONS, to resolve a human label for
    // hotkey_display() below.
    const HOTKEY_OPTIONS = [
      { value: "alt_r", label: "Right Option (⌥)" }, { value: "alt_l", label: "Left Option (⌥)" },
      { value: "cmd_r", label: "Right Command (⌘)" }, { value: "ctrl_r", label: "Right Control (⌃)" },
      { value: "shift_r", label: "Right Shift (⇧)" }, { value: "f13", label: "F13" }, { value: "f14", label: "F14" },
      { value: "f15", label: "F15" }, { value: "f16", label: "F16" }, { value: "f17", label: "F17" },
      { value: "f18", label: "F18" }, { value: "f19", label: "F19" },
    ];
    window.pywebview = { api: {
      get_permissions: async () => ({ panes: [
        { key: "microphone", label: "Microphone", granted: true,
          settings_url: "x-apple.systempreferences:", hotkey: false, effective: true },
        { key: "accessibility", label: "Accessibility", granted: true,
          settings_url: "x-apple.systempreferences:", hotkey: true, effective: true },
      ]}),
      reset_permission: async () => ({ ok: true }),
      relaunch_app: async () => ({ ok: false, error: "dev mode" }),
      open_settings_pane: async () => ({ ok: true }),
      get_history: async () => [...mockHistory].sort((a, b) => b.ts - a.ts),
      get_stats: async () => ({
        total_words: 41300, dictations: mockHistory.length, avg_wpm: 132,
        streak_days: 12, total_seconds: 5400, time_saved_seconds: 11520,
        daily: Array.from({ length: 14 }, (_, i) => ({
          date: `2026-07-${String(12 + i).padStart(2, "0")}`,
          words: [0, 120, 340, 90, 0, 610, 480, 220, 0, 130, 700, 540, 310, 260][i],
          saved_seconds: [0, 140, 400, 95, 0, 760, 560, 250, 0, 150, 820, 610, 350, 300][i],
        })),
      }),
      get_config: async () => JSON.parse(JSON.stringify(mockCfg)),
      set_config: async (u) => Object.assign(mockCfg, u),
      get_snippets: async () => Object.entries(mockSnippets).map(([trigger, text]) => ({
        trigger, text, warning: trigger === "email" ? SAMPLE_WARNING : null,
      })),
      save_snippet: async (old, trigger, text) => {
        if (trigger !== old && trigger in mockSnippets) return { error: `“${trigger}” already uses that phrase` };
        delete mockSnippets[old];
        mockSnippets[trigger] = text;
        return { ok: true, trigger, warning: trigger === "email" ? SAMPLE_WARNING : null };
      },
      delete_snippet: async (trigger) => { delete mockSnippets[trigger]; return { ok: true }; },
      // mirrors Api.get_suggestions() and friends in voisr/ui.py
      get_suggestions: async () => ({ pending: [...mockSuggestions],
        auto_added: { sam: "Sam" }, used: {} }),
      accept_suggestion: async (id) => {
        const hit = mockSuggestions.find((p) => p.id === id);
        if (hit) mockCfg.dictionary[hit.heard] = hit.written;
        mockSuggestions = mockSuggestions.filter((p) => p.id !== id);
        return { ok: true };
      },
      dismiss_suggestion: async (id) => {
        mockSuggestions = mockSuggestions.filter((p) => p.id !== id);
        return { ok: true };
      },
      // mirrors Api.take_config_notice() in voisr/ui.py: {} unless a corrupt
      // config.json was moved aside at load.
      take_config_notice: async () => ({}),
      // mirrors Api.set_appearance() in voisr/ui.py, used by the command
      // palette's "Toggle appearance" command.
      set_appearance: async (mode) => {
        if (!["system", "light", "dark"].includes(mode)) return { error: `unknown appearance: ${mode}` };
        mockCfg.appearance = mode;
        return { ok: true, appearance: mode };
      },
      delete_history_item: async (id) => { mockHistory = mockHistory.filter((i) => i.id !== id); return { ok: true }; },
      restore_history_item: async (item) => { mockHistory.push(item); return { ok: true }; },
      // mirrors Api.clear_history() in voisr/ui.py: a single atomic truncate,
      // never a per-row delete_history_item loop.
      clear_history: async () => {
        const removed = mockHistory.length;
        mockHistory = [];
        return { ok: true, removed };
      },
      // mirrors Api.get_models() in voisr/ui.py; one model is left
      // uninstalled here so the mock exercises the download path.
      get_models: async () => ({
        models: [
          { id: "base.en", label: "Base (English)", description: "The smallest of the four. English only.", bytes: 147964211, installed: false, active: false, custom: false, min_memory_gb: 4, apple_silicon_only: false, license: "MIT", blocked: null, warning: null, benchmark: null },
          { id: "small.en", label: "Small (English)", description: "A step up in size from Base. English only. The default.", bytes: 487614201, installed: true, active: true, custom: false, min_memory_gb: 4, apple_silicon_only: false, license: "MIT", blocked: null, warning: null, benchmark: null },
        ],
        active: mockCfg.model || "small.en",
        system: { chip: "Preview", memory_gb: 8, apple_silicon: true },
        download: { state: "idle" },
      }),
      download_model: async () => ({ error: "Downloads are disabled in the browser preview." }),
      delete_model: async () => ({ error: "Not available in the browser preview." }),
      get_download_progress: async () => ({ state: "idle" }),
      // mirrors Api.set_launch_at_login() in voisr/ui.py.
      set_launch_at_login: async (enabled) => {
        mockCfg.launch_at_login = !!enabled;
        return { ok: true, enabled: !!enabled };
      },
      export_history: async (fmt) => {
        const stamp = new Date().toISOString().slice(0, 10);
        let text;
        if (fmt === "json") text = JSON.stringify(mockHistory, null, 2);
        else if (fmt === "md") text = mockHistory.map((i) => `- ${i.text}`).join("\n");
        else if (fmt === "txt") text = mockHistory.map((i) => i.text).join("\n");
        else return { error: `unsupported format: ${fmt}` };
        return { text, suggested_name: `voisr-history-${stamp}.${fmt}` };
      },
      save_text_file: async (text, suggestedName) => ({ ok: true, path: `/tmp/${suggestedName}` }),
      copy_text: async () => ({ ok: true }),
      capture_hotkey: async () => {
        await new Promise((r) => setTimeout(r, 1200));
        mockCfg.hotkey_vk = 63; mockCfg.hotkey_label = "Fn / Globe";
        return { vk: 63, label: "Fn / Globe" };
      },
      // mirrors Api.hotkey_display() in voisr/ui.py: captured label, else
      // the matching HOTKEY_OPTIONS label, else the raw value, else "unset".
      hotkey_display: async () => {
        if (mockCfg.hotkey_label) return { display: mockCfg.hotkey_label };
        const opt = HOTKEY_OPTIONS.find((o) => o.value === mockCfg.hotkey);
        if (opt) return { display: opt.label };
        return { display: mockCfg.hotkey || "unset" };
      },
      // mirrors Api.get_hotkey_status(): state drives Home's status panel
      get_hotkey_status: async () => ({ status: `active on ${mockCfg.hotkey}`,
                                        state: recording ? "busy" : "listening" }),
      get_pill_themes: async () => [
        { value: "classic", label: "Signature" }, { value: "quiet", label: "Quiet" },
      ],
      start_recording: async () => { recording = true; return { recording: true }; },
      // mirrors Api.get_level(): a simulated voice (syllables and pauses)
      // while recording, null otherwise
      get_level: async () => {
        if (!recording) return null;
        const t = Date.now() / 1000;
        const syllable = Math.max(0, Math.sin(t * 9.5)) * (Math.sin(t * 1.3) > -0.6 ? 1 : 0);
        return 0.03 + 0.85 * syllable * (0.75 + 0.25 * Math.sin(t * 3.1));
      },
      stop_recording: async () => {
        if (!recording) return { error: "not recording" };
        recording = false;
        await new Promise((r) => setTimeout(r, 900));
        const item = { id: Math.random().toString(36).slice(2), ts: Date.now() / 1000, text: "This is a mock dictation rendered in browser preview.", words: 9, duration_s: 4 };
        mockHistory.push(item);
        return { text: item.text, item, copied: true };
      },
    }};
    // main.js registers its init() on this event; dispatching it here
    // (instead of calling init() directly) keeps this file decoupled
    // from the view/main modules that load after it.
    window.dispatchEvent(new Event("pywebviewready"));
  }, 300);
})(window.Voisr);
