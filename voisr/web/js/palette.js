"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  const commands = [];
  let filtered = [], cursor = 0, lastFocus = null;

  function register(cmd) { commands.push(cmd); }

  /* Subsequence match, ranked by how early the first character lands — a
     query of "sh" reaches "Search history" before "Settings". */
  function match(query, items) {
    const q = (query || "").trim().toLowerCase();
    if (!q) return items.slice();
    const scored = [];
    for (const item of items) {
      const hay = item.title.toLowerCase();
      let qi = 0, first = -1;
      for (let i = 0; i < hay.length && qi < q.length; i++) {
        if (hay[i] === q[qi]) { if (qi === 0) first = i; qi++; }
      }
      if (qi === q.length) scored.push({ item, first });
    }
    scored.sort((a, b) => a.first - b.first);
    return scored.map((s) => s.item);
  }

  function draw() {
    const list = document.getElementById("palette-list");
    list.replaceChildren(...filtered.map((cmd, i) =>
      V.ui.el("li", {
        class: "palette-item" + (i === cursor ? " active" : ""),
        role: "option", "aria-selected": i === cursor,
        on: { click: () => run(cmd) },
      },
      V.ui.el("span", { text: cmd.title }),
      cmd.hint ? V.ui.el("span", { class: "palette-hint", text: cmd.hint }) : null)));
  }

  function open() {
    lastFocus = document.activeElement;
    const scrim = document.getElementById("palette");
    const input = document.getElementById("palette-input");
    scrim.hidden = false;
    input.value = "";
    filtered = commands.slice();
    cursor = 0;
    draw();
    input.focus();
  }

  function close() {
    document.getElementById("palette").hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    lastFocus = null;
  }

  async function run(cmd) {
    const query = document.getElementById("palette-input").value.trim();
    close();
    try {
      await cmd.run(query);
    } catch (err) {
      V.ui.toast(`${cmd.title}: ${err.message}`);
    }
  }

  function init() {
    const input = document.getElementById("palette-input");

    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.getElementById("palette").hidden ? open() : close();
      }
    });

    document.getElementById("palette").addEventListener("click", (e) => {
      if (e.target.id === "palette") close();   // click the scrim to dismiss
    });

    input.addEventListener("input", () => {
      filtered = match(input.value, commands);
      cursor = 0;
      draw();
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown") {
        e.preventDefault(); cursor = Math.min(cursor + 1, filtered.length - 1); draw();
      } else if (e.key === "ArrowUp") {
        e.preventDefault(); cursor = Math.max(cursor - 1, 0); draw();
      } else if (e.key === "Enter" && filtered[cursor]) {
        e.preventDefault(); run(filtered[cursor]);
      }
    });

    register({ id: "dictate", title: "Start dictation", hint: "record",
               run: () => V.views.home.toggleRecord() });
    /* Drives the History search box through a real input event rather than
       calling into that view directly — so this command works both before
       and after Task 16 adds the search field. */
    register({ id: "search", title: "Search history", hint: "⌘F",
               run: (q) => {
                 V.show("history");
                 const input = document.getElementById("history-search");
                 if (!input || !q) return;
                 input.value = q;
                 input.dispatchEvent(new Event("input", { bubbles: true }));
               } });
    register({ id: "home", title: "Go to Home", run: () => V.show("home") });
    register({ id: "history", title: "Go to History", run: () => V.show("history") });
    register({ id: "dictionary", title: "Go to Dictionary", run: () => V.show("dictionary") });
    register({ id: "settings", title: "Go to Settings", run: () => V.show("settings") });
    register({ id: "hotkey", title: "Change push-to-talk key",
               run: async () => {
                 V.show("settings");
                 const res = await V.api().capture_hotkey();
                 if (res.error) {
                   V.ui.toast(res.error);
                 } else {
                   V.ui.toast(`Push-to-talk key set to ${res.label}`);
                 }
               } });
    register({ id: "appearance", title: "Toggle appearance", hint: "light / dark",
               run: async () => {
                 const cfg = await V.api().get_config();
                 if (cfg.error) {
                   V.ui.toast(cfg.error);
                   return;
                 }
                 const next = cfg.appearance === "dark" ? "light" : "dark";
                 const res = await V.api().set_appearance(next);
                 if (res.error) {
                   V.ui.toast(res.error);
                 } else {
                   V.applyAppearance(next);
                   V.ui.toast(`Appearance: ${next}`);
                 }
               } });
    register({ id: "copy-last", title: "Copy last dictation",
               run: async () => {
                 const items = await V.api().get_history();
                 if (items.error) {
                   V.ui.toast(items.error);
                   return;
                 }
                 if (!items.length) return V.ui.toast("Nothing to copy yet");
                 const res = await V.api().copy_text(items[0].text);
                 if (res.error) {
                   V.ui.toast(res.error);
                 } else {
                   V.ui.toast("Copied to clipboard");
                 }
               } });
  }

  V.palette = { open, close, register, match, init };
})(window.Voisr);
