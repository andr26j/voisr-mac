"use strict";
window.Voisr = window.Voisr || {};
(function (V) {
  /* What may be saved is decided in Python (voisr/snippets.py), the same code
     that matches a dictation, so nothing here repeats the rules. */
  const BANNER = "snippets";
  let adding = false, dismissed = false;
  let closeEdit = null;  // closes the row being edited: one at a time

  const byId = (id) => document.getElementById(id);

  /* The banner's place is the adding form's while you add. */
  function syncBanner() {
    byId("snippet-banner").hidden = dismissed || adding;
  }

  function openForm() {
    if (closeEdit) closeEdit();
    adding = true;
    syncBanner();
    const form = byId("snippet-form");
    const wasOpen = !form.hidden;
    form.hidden = false;
    if (!wasOpen && V.lists.canAnimate(form)) V.lists.enter(form, "fill");
    byId("snippet-trigger").focus();
  }

  function closeForm() {
    adding = false;
    byId("snippet-form").hidden = true;
    byId("snippet-trigger").value = "";
    byId("snippet-text").value = "";
    syncBanner();
  }

  /* A trigger -> its text on one line, with any warning under it. Edit opens the
     row into the same fields as adding, because the row may be far below the
     adding form. A warning comes from the app, in its own words. */
  function row(snippet) {
    const el = V.ui.el;
    const node = el("li", { class: "item-row is-plain snippet-row" });

    function show() {
      node.classList.remove("is-editing", "snippet-editing");
      node.replaceChildren(
        el("div", { class: "snippet-main" },
          el("div", { class: "snippet-line" },
            el("span", { class: "snippet-trigger", text: snippet.trigger }),
            el("span", { class: "pair-arrow", "aria-hidden": "true", text: "→" }),
            el("span", { class: "snippet-preview", text: snippet.text })),
          snippet.warning ? el("div", { class: "snippet-warning", text: snippet.warning }) : null),
        el("button", { type: "button", class: "text-btn", text: "Edit",
                       "aria-label": `Edit ${snippet.trigger}`, on: { click: edit } }));
    }

    function edit() {
      if (closeEdit) closeEdit();
      if (adding) closeForm();
      const close = () => {
        if (closeEdit === close) closeEdit = null;
        show();
        // a render that came while this was open left the row as it was
        if (node.dataset.stale) { delete node.dataset.stale; render({ animate: false }); }
      };
      closeEdit = close;
      const trigger = el("input", { id: "snippet-edit-trigger", type: "text", autocomplete: "off" });
      trigger.value = snippet.trigger;
      const text = el("textarea", { id: "snippet-edit-text", rows: "4", spellcheck: "false" });
      text.value = snippet.text;

      async function save(e) {
        e.preventDefault();
        // A refusal keeps the edit open: a long prompt must not be lost to a typo
        // in its trigger. tryApi's toast says why.
        const saved = await V.ui.tryApi(
          V.api().save_snippet(snippet.trigger, trigger.value, text.value), "Save snippet");
        if (!saved) return;
        // nothing changed means no new row is coming, so this one has to close
        close();
        V.ui.toast(`Saved: ${saved.trigger}`);
        render();
      }

      node.classList.add("is-editing", "snippet-editing");
      node.replaceChildren(el("form", {
        class: "snippet-edit",
        on: {
          submit: save,
          keydown: (e) => {
            if (e.key !== "Escape") return;
            e.preventDefault();
            close();
            const back = node.querySelector(".text-btn");
            if (back) back.focus();
          },
        },
      },
        el("div", { class: "field" },
          el("label", { for: "snippet-edit-trigger", text: "When I say" }), trigger),
        el("div", { class: "field" },
          el("label", { for: "snippet-edit-text", text: "Paste this" }), text),
        el("div", { class: "add-form-actions" },
          el("button", { type: "submit", class: "btn-primary", text: "Save" }),
          el("button", { type: "button", class: "btn-quiet", text: "Cancel", on: { click: close } }),
          el("button", { type: "button", class: "text-btn", text: "Delete",
                         "aria-label": `Delete ${snippet.trigger}`,
                         on: { click: () => remove(snippet) } }))));
      text.focus();
    }

    show();
    return node;
  }

  async function remove(snippet) {
    if (!await V.ui.tryApi(V.api().delete_snippet(snippet.trigger), "Delete snippet")) return;
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
    const [snippets, cfg] = await Promise.all([
      V.ui.tryApi(V.api().get_snippets(), "Load snippets"),
      V.ui.tryApi(V.api().get_config(), "Load settings"),
    ]);
    if (cfg) {
      dismissed = V.ui.bannerDismissed(cfg, BANNER);
      syncBanner();
    }
    if (!snippets) return;
    // the snippet being edited was removed elsewhere: its row goes with it
    if (closeEdit && !byId("snippet-list").children.some((n) => n.classList.contains("is-editing")
        && snippets.some((s) => s.trigger === n.dataset.key))) closeEdit = null;
    const empty = byId("snippet-empty");
    empty.hidden = snippets.length > 0;
    // with the banner gone, this is all that says what the page is for
    empty.textContent = dismissed
      ? "No snippets yet. Say a phrase on its own and Voisr pastes your saved text."
      : "No snippets yet.";
    byId("snippet-words").hidden = snippets.length === 0;
    V.lists.sync(byId("snippet-list"), snippets.map((s) => ({
      key: s.trigger, sig: `${s.text}|${s.warning}`, build: () => row(s),
    })), { animate: animate !== false });
  }

  async function save(e) {
    e.preventDefault();
    const trigger = byId("snippet-trigger").value;
    const text = byId("snippet-text").value;
    // A refusal keeps what was typed: a long prompt must not be lost to a typo
    // in its trigger. tryApi's toast says why.
    const saved = await V.ui.tryApi(V.api().save_snippet(null, trigger, text), "Save snippet");
    if (!saved) return;
    closeForm();
    V.ui.toast(`Saved: ${saved.trigger}`);
    render();
  }

  function init() {
    byId("snippet-form").addEventListener("submit", save);
    byId("snippet-add").addEventListener("click", openForm);
    byId("snippet-banner-add").addEventListener("click", openForm);
    byId("snippet-cancel").addEventListener("click", closeForm);
    byId("snippet-form").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeForm(); byId("snippet-add").focus(); }
    });
    byId("snippet-banner-close").addEventListener("click", async () => {
      if (await V.ui.dismissBanner(BANNER, byId("snippet-banner"))) {
        dismissed = true;
        render({ animate: false });
      }
    });
  }

  V.views = V.views || {};
  V.views.snippets = { render, save, init };
})(window.Voisr);
