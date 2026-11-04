"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* What may be saved is decided in Python (voisr/snippets.py), the same code
     that matches a dictation, so nothing here repeats the rules. */
  let editing = null; // the trigger being edited, or null when adding

  function setNote(text) {
    document.getElementById("snippet-note").textContent = text || "";
  }

  function resetForm() {
    editing = null;
    document.getElementById("snippet-trigger").value = "";
    document.getElementById("snippet-text").value = "";
    document.getElementById("snippet-save").textContent = "Add";
    document.getElementById("snippet-cancel").hidden = true;
  }

  function startEdit(snippet) {
    editing = snippet.trigger;
    document.getElementById("snippet-trigger").value = snippet.trigger;
    document.getElementById("snippet-text").value = snippet.text;
    document.getElementById("snippet-save").textContent = "Save";
    document.getElementById("snippet-cancel").hidden = false;
    setNote("");
    document.getElementById("snippet-text").focus();
  }

  function row(snippet) {
    return V.ui.el("li", { class: "item-row snippet-row" },
      V.ui.el("div", { class: "item-text" },
        V.ui.el("div", { class: "snippet-trigger", text: snippet.trigger }),
        V.ui.el("div", { class: "snippet-preview", text: snippet.text }),
        snippet.warning ? V.ui.el("div", { class: "snippet-warning", text: snippet.warning }) : null),
      V.ui.el("button", {
        type: "button", class: "btn-secondary", text: "Edit",
        "aria-label": `Edit ${snippet.trigger}`,
        on: { click: () => startEdit(snippet) },
      }),
      V.ui.el("button", {
        class: "icon-btn danger", "aria-label": `Delete ${snippet.trigger}`,
        html: V.ui.ICONS.trash,
        on: { click: () => remove(snippet) },
      }));
  }

  async function remove(snippet) {
    if (!await V.ui.tryApi(V.api().delete_snippet(snippet.trigger), "Delete snippet")) return;
    if (editing === snippet.trigger) resetForm();
    render();
    // a long prompt is the user's work: same undo as a dictionary entry
    V.ui.offerEntryUndo({
      restore: async () => {
        const now = await V.ui.tryApi(V.api().get_snippets(), "Load snippets");
        if (!now) return false;
        if (now.some((s) => s.trigger === snippet.trigger && s.text === snippet.text)) return true;
        return !!await V.ui.tryApi(
          V.api().save_snippet(null, snippet.trigger, snippet.text), "Restore snippet");
      },
      refresh: render,
    });
  }

  /* Same rule as the other lists: a failed load keeps the last good one. */
  async function render({ animate } = {}) {
    const snippets = await V.ui.tryApi(V.api().get_snippets(), "Load snippets");
    if (!snippets) return;
    document.getElementById("snippet-empty").hidden = snippets.length > 0;
    V.lists.sync(document.getElementById("snippet-list"), snippets.map((s) => ({
      key: s.trigger, sig: `${s.text}|${s.warning}`, build: () => row(s),
    })), { animate: animate !== false });
  }

  async function save(e) {
    e.preventDefault();
    const trigger = document.getElementById("snippet-trigger").value;
    const text = document.getElementById("snippet-text").value;
    // A refusal keeps what was typed: a long prompt must not be lost to a typo
    // in its trigger. tryApi's toast says why.
    const saved = await V.ui.tryApi(V.api().save_snippet(editing, trigger, text), "Save snippet");
    if (!saved) return;
    resetForm();
    setNote(saved.warning);
    V.ui.toast(`Saved: ${saved.trigger}`);
    render();
  }

  function init() {
    document.getElementById("snippet-form").addEventListener("submit", save);
    document.getElementById("snippet-cancel").addEventListener("click", () => {
      resetForm();
      setNote("");
    });
  }

  V.views = V.views || {};
  V.views.snippets = { render, save, init };
})(window.Voisr);
