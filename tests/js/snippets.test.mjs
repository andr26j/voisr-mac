import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

const IDS = ["snippet-form", "snippet-trigger", "snippet-text", "snippet-cancel", "snippet-add",
             "snippet-banner", "snippet-banner-add", "snippet-banner-close",
             "snippet-words", "snippet-list", "snippet-empty", "toast"];

const settle = () => new Promise((r) => setImmediate(r));

function mount(t, { saveResult, cfg = {}, setFails = false } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/snippets.js"]);
  const saves = [], deletes = [];
  let live = [{ trigger: "my email", text: "a@example.com\nline two", warning: null }];
  const config = { ...cfg };
  env.window.pywebview = { api: {
    get_snippets: async () => live.map((s) => ({ ...s })),
    get_config: async () => ({ ...config }),
    set_config: async (u) => {
      if (setFails) return { error: "disk full" };
      Object.assign(config, u);
      return { ...config };
    },
    save_snippet: async (old, trigger, text) => {
      saves.push([old, trigger, text]);
      if (saveResult) return saveResult;
      live = live.filter((s) => s.trigger !== old).concat([{ trigger, text, warning: null }]);
      return { ok: true, trigger, warning: null };
    },
    delete_snippet: async (trigger) => {
      deletes.push(trigger);
      live = live.filter((s) => s.trigger !== trigger);
      return { ok: true };
    },
  } };
  const nodes = {};
  for (const id of IDS) {
    nodes[id] = makeNode(id === "snippet-text" ? "textarea" : "div");
    env.register(id, nodes[id]);
  }
  nodes["snippet-trigger"].value = "";
  nodes["snippet-text"].value = "";
  nodes["snippet-form"].hidden = true;
  env.V.views.snippets.init();
  return { V: env.V, saves, deletes, config, ...nodes };
}

const submit = (h) => h.V.views.snippets.save({ preventDefault() {} });
const flatten = (node) => [node, ...Array.from(node.children || []).flatMap(flatten)];
const byClass = (node, cls) => flatten(node).filter((n) => String(n.className || "").split(/\s+/).includes(cls));
const button = (row, text) => flatten(row).find((n) => n.tagName === "BUTTON" && n.textContent === text);
const buttonText = (row) => flatten(row).filter((n) => n.tagName === "BUTTON").map((b) => b.textContent);
const toastText = (h) => h.toast.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(" ");

test("a saved snippet shows its trigger and text, with no row markup from the text", async (t) => {
  const h = mount(t);
  await h.V.views.snippets.render();
  assert.equal(h["snippet-list"].children.length, 1);
  assert.equal(h["snippet-empty"].hidden, true);
  const row = h["snippet-list"].children[0];
  assert.equal(byClass(row, "snippet-trigger")[0].textContent, "my email");
  assert.equal(byClass(row, "snippet-preview")[0].textContent, "a@example.com\nline two");
  assert.deepEqual(buttonText(row), ["Edit"]);
});

test("the form is closed until asked for, and takes the banner's place", async (t) => {
  const h = mount(t);
  await h.V.views.snippets.render();
  assert.equal(h["snippet-form"].hidden, true);
  assert.equal(h["snippet-banner"].hidden, false);
  h["snippet-add"].dispatch("click");
  assert.equal(h["snippet-form"].hidden, false);
  assert.equal(h["snippet-banner"].hidden, true);
  h["snippet-trigger"].value = "half typed";
  h["snippet-cancel"].dispatch("click");
  assert.equal(h["snippet-form"].hidden, true);
  assert.equal(h["snippet-banner"].hidden, false);
  assert.equal(h["snippet-trigger"].value, "");
  h["snippet-banner-add"].dispatch("click");
  assert.equal(h["snippet-form"].hidden, false, "the banner's own button opens it too");
});

test("saving sends the text exactly, line breaks and outer spaces included", async (t) => {
  const h = mount(t);
  h["snippet-add"].dispatch("click");
  h["snippet-trigger"].value = "review prompt";
  h["snippet-text"].value = "  Please:\n\n- review\n";
  await submit(h);
  assert.deepEqual(h.saves, [[null, "review prompt", "  Please:\n\n- review\n"]]);
  assert.equal(h["snippet-trigger"].value, "", "cleared once it is really saved");
  assert.equal(h["snippet-text"].value, "");
  assert.equal(h["snippet-form"].hidden, true, "and the form closes");
});

test("a refused save keeps the form and what was typed, and says why", async (t) => {
  const h = mount(t, { saveResult: { error: "“my email” already uses that phrase" } });
  h["snippet-add"].dispatch("click");
  h["snippet-trigger"].value = "My Email.";
  h["snippet-text"].value = "a very long prompt";
  await submit(h);
  assert.equal(h["snippet-form"].hidden, false);
  assert.equal(h["snippet-trigger"].value, "My Email.");
  assert.equal(h["snippet-text"].value, "a very long prompt");
  assert.match(toastText(h), /already uses that phrase/);
});

test("a very short trigger's warning shows under its row once saved, in the app's own words", async (t) => {
  const warning = "Very short, so it may be misheard, said by accident, or paste as plain words.";
  const h = mount(t);
  await h.V.views.snippets.render();
  assert.equal(byClass(h["snippet-list"], "snippet-warning").length, 0);
  window.pywebview.api.get_snippets = async () => [{ trigger: "email", text: "x", warning }];
  await h.V.views.snippets.render();
  assert.equal(byClass(h["snippet-list"], "snippet-warning")[0].textContent, warning);
});

/* ---------- inline edit ---------- */

async function editing(t, opts) {
  const h = mount(t, opts);
  await h.V.views.snippets.render();
  const row = h["snippet-list"].children[0];
  button(row, "Edit").dispatch("click");
  const fields = flatten(row).filter((n) => n.tagName === "INPUT" || n.tagName === "TEXTAREA");
  return { h, row, trigger: fields[0], text: fields[1], form: flatten(row).find((n) => n.tagName === "FORM") };
}

test("Edit opens the row into both fields, with Save, Cancel and Delete", async (t) => {
  const { row, trigger, text } = await editing(t);
  assert.equal(trigger.value, "my email");
  assert.equal(text.value, "a@example.com\nline two");
  assert.deepEqual(buttonText(row), ["Save", "Cancel", "Delete"]);
});

test("saving an edit replaces the old trigger", async (t) => {
  const { h, trigger, form } = await editing(t);
  trigger.value = "work email";
  form.dispatch("submit", { preventDefault() {} });
  await settle();
  assert.deepEqual(h.saves[0].slice(0, 2), ["my email", "work email"]);
  assert.match(toastText(h), /Saved: work email/);
});

test("saving an edit with nothing changed still closes it, as no new row is coming", async (t) => {
  const { h, row, form } = await editing(t, { saveResult: { ok: true, trigger: "my email", warning: null } });
  form.dispatch("submit", { preventDefault() {} });
  await settle();
  assert.equal(h["snippet-list"].children[0], row, "the same row, kept");
  assert.deepEqual(buttonText(row), ["Edit"]);
});

test("a delete that fails leaves the edit open, and Cancel still works", async (t) => {
  const { h, row } = await editing(t);
  window.pywebview.api.delete_snippet = async () => ({ error: "disk full" });
  button(row, "Delete").dispatch("click");
  await settle();
  assert.deepEqual(buttonText(row), ["Save", "Cancel", "Delete"]);
  assert.match(toastText(h), /disk full/);
  button(row, "Cancel").dispatch("click");
  assert.deepEqual(buttonText(row), ["Edit"]);
});

test("a refused edit stays open with what was typed", async (t) => {
  const { h, row, trigger, form } = await editing(t, { saveResult: { error: "that phrase is taken" } });
  trigger.value = "taken";
  form.dispatch("submit", { preventDefault() {} });
  await settle();
  assert.equal(flatten(row).find((n) => n.tagName === "INPUT").value, "taken");
  assert.match(toastText(h), /that phrase is taken/);
});

test("Cancel and Escape leave an edit without saving", async (t) => {
  const { h, row, form } = await editing(t);
  form.dispatch("keydown", { key: "Escape", preventDefault() {} });
  assert.deepEqual(buttonText(row), ["Edit"]);
  button(row, "Edit").dispatch("click");
  button(row, "Cancel").dispatch("click");
  assert.deepEqual(buttonText(row), ["Edit"]);
  assert.equal(h.saves.length, 0);
});

test("a save after cancelling an edit adds, not replaces", async (t) => {
  const { h, row } = await editing(t);
  button(row, "Cancel").dispatch("click");
  h["snippet-add"].dispatch("click");
  h["snippet-trigger"].value = "new one";
  h["snippet-text"].value = "x";
  await submit(h);
  assert.equal(h.saves[0][0], null);
});

test("adding closes an open edit", async (t) => {
  const { h, row } = await editing(t);
  h["snippet-add"].dispatch("click");
  assert.deepEqual(buttonText(row), ["Edit"]);
});

test("delete in the edit removes the row, offers Undo and shows the empty message", async (t) => {
  const { h, row } = await editing(t);
  button(row, "Delete").dispatch("click");
  await settle();
  await h.V.views.snippets.render();
  assert.deepEqual(h.deletes, ["my email"]);
  assert.equal(h["snippet-list"].children.length, 0);
  assert.equal(h["snippet-empty"].hidden, false);
  assert.equal(h["snippet-empty"].textContent, "No snippets yet.");
  assert.equal(h["snippet-words"].hidden, true);
  assert.match(toastText(h), /removed/i);
});

/* ---------- the banner ---------- */

test("a dismissed banner stays gone, and the empty line then says what the page is for", async (t) => {
  const h = mount(t, { cfg: { dismissed_banners: ["snippets"] } });
  window.pywebview.api.get_snippets = async () => [];
  await h.V.views.snippets.render();
  assert.equal(h["snippet-banner"].hidden, true);
  assert.match(h["snippet-empty"].textContent, /Say a phrase on its own/);
});

test("dismissing the banner saves that", async (t) => {
  const h = mount(t, { cfg: { dismissed_banners: ["dictionary"] } });
  await h.V.views.snippets.render();
  h["snippet-banner-close"].dispatch("click");
  await settle();
  assert.deepEqual(h.config.dismissed_banners, ["dictionary", "snippets"]);
  assert.equal(h["snippet-banner"].hidden, true);
});

test("a dismissal that couldn't be saved leaves the banner up", async (t) => {
  const h = mount(t, { setFails: true });
  await h.V.views.snippets.render();
  h["snippet-banner-close"].dispatch("click");
  await settle();
  assert.equal(h["snippet-banner"].hidden, false);
  assert.match(toastText(h), /disk full/);
});

test("the list still draws if the config can't be read", async (t) => {
  const h = mount(t);
  window.pywebview.api.get_config = async () => ({ error: "bad" });
  await h.V.views.snippets.render();
  assert.equal(h["snippet-list"].children.length, 1);
});

/* ---------- history rows ---------- */

test("a history row for a snippet is tagged, and others aren't", () => {
  const { V } = loadVoisr(["js/ui.js"]);
  const handlers = { onCopy() {}, onDelete() {} };
  const row = (item) => V.ui.itemRow({ id: "a", ts: 1, words: 2, text: "my email", ...item }, handlers);
  assert.equal(byClass(row({ snippet: true }), "tag")[0].textContent, "Snippet");
  assert.equal(byClass(row({}), "tag").length, 0);
  assert.ok(byClass(row({}), "row-time")[0].textContent, "every row has its time");
});
