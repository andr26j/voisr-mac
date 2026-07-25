"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  V.api = () => window.pywebview.api;

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
    let mockCfg = { hotkey: "alt_r", paste: true, show_widget: true, device: ":0", pill_theme: "classic", pill_color: null, dictionary: { trademake: "TradeMate", "trade mate": "TradeMate" } };
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
        { value: "alt_r", label: "Right Option (⌥)" }, { value: "alt_l", label: "Left Option (⌥)" },
        { value: "cmd_r", label: "Right Command (⌘)" }, { value: "ctrl_r", label: "Right Control (⌃)" },
        { value: "shift_r", label: "Right Shift (⇧)" }, { value: "f13", label: "F13" }, { value: "f14", label: "F14" },
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
    // main.js registers its init() on this event; dispatching it here
    // (instead of calling init() directly) keeps this file decoupled
    // from the view/main modules that load after it.
    window.dispatchEvent(new Event("pywebviewready"));
  }, 300);
})(window.Voisr);
