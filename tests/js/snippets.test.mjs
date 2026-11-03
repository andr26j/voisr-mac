import test from "node:test";
import assert from "node:assert/strict";
import { loadVoisr } from "./load.mjs";
import { makeNode } from "./dom.mjs";

const IDS = ["snippet-form", "snippet-trigger", "snippet-text", "snippet-save", "snippet-cancel",
             "snippet-note", "snippet-list", "snippet-empty", "toast"];

function mount(t, { saveResult } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = loadVoisr(["js/ui.js", "js/lists.js", "js/api.js", "js/views/snippets.js"]);
  const saves = [], deletes = [];
  let live = [{ trigger: "my email", text: "a@example.com\nline two", warning: null }];
  env.window.pywebview = { api: {
    get_snippets: async () => live.map((s) => ({ ...s })),
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
  env.V.views.snippets.init();
  return { V: env.V, saves, deletes, ...nodes };
}

const submit = (h) => h.V.views.snippets.save({ preventDefault() {} });
const texts = (node) => node.children.map((c) => c.textContent);

test("a saved snippet shows its trigger and text, with no row markup from the text", async (t) => {
  const h = mount(t);
  await h.V.views.snippets.render();
  assert.equal(h["snippet-list"].children.length, 1);
  assert.equal(h["snippet-empty"].hidden, true);
  const body = h["snippet-list"].children[0].children[0];
  assert.deepEqual(texts(body), ["my email", "a@example.com\nline two"]);
});

test("saving sends the text exactly, line breaks and outer spaces included", async (t) => {
  const h = mount(t);
  h["snippet-trigger"].value = "review prompt";
  h["snippet-text"].value = "  Please:\n\n- review\n";
  await submit(h);
  assert.deepEqual(h.saves, [[null, "review prompt", "  Please:\n\n- review\n"]]);
  assert.equal(h["snippet-trigger"].value, "", "cleared once it is really saved");
  assert.equal(h["snippet-text"].value, "");
});

test("a refused save keeps what was typed and says why", async (t) => {
  const h = mount(t, { saveResult: { error: "“my email” already uses that phrase" } });
  h["snippet-trigger"].value = "My Email.";
  h["snippet-text"].value = "a very long prompt";
  await submit(h);
  assert.equal(h["snippet-trigger"].value, "My Email.");
  assert.equal(h["snippet-text"].value, "a very long prompt");
  assert.match(h.toast.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(" "), /already uses that phrase/);
});

test("a very short trigger saves and the warning stays on the page", async (t) => {
  const warning = "Very short, so it may be misheard or said by accident.";
  const h = mount(t, { saveResult: { ok: true, trigger: "email", warning } });
  h["snippet-trigger"].value = "email";
  h["snippet-text"].value = "x";
  await submit(h);
  assert.equal(h["snippet-note"].textContent, warning);
});

test("a row's warning is shown on the row", async (t) => {
  const h = mount(t);
  window.pywebview.api.get_snippets = async () => [{ trigger: "email", text: "x", warning: "Very short" }];
  await h.V.views.snippets.render();
  assert.equal(texts(h["snippet-list"].children[0].children[0])[2], "Very short");
});

test("editing fills the form and saves over the old trigger", async (t) => {
  const h = mount(t);
  await h.V.views.snippets.render();
  const edit = h["snippet-list"].children[0].children[1];
  edit.dispatch("click");
  assert.equal(h["snippet-trigger"].value, "my email");
  assert.equal(h["snippet-text"].value, "a@example.com\nline two");
  assert.equal(h["snippet-save"].textContent, "Save");
  assert.equal(h["snippet-cancel"].hidden, false);
  h["snippet-trigger"].value = "work email";
  await submit(h);
  assert.deepEqual(h.saves[0].slice(0, 2), ["my email", "work email"]);
  assert.equal(h["snippet-save"].textContent, "Add");
});

test("cancel leaves an edit without saving", async (t) => {
  const h = mount(t);
  await h.V.views.snippets.render();
  h["snippet-list"].children[0].children[1].dispatch("click");
  h["snippet-cancel"].dispatch("click");
  assert.equal(h["snippet-trigger"].value, "");
  h["snippet-trigger"].value = "new one";
  h["snippet-text"].value = "x";
  await submit(h);
  assert.equal(h.saves[0][0], null, "a save after cancel adds, not replaces");
});

test("delete removes the row and shows the empty message", async (t) => {
  const h = mount(t);
  await h.V.views.snippets.render();
  h["snippet-list"].children[0].children[2].dispatch("click");
  await new Promise((r) => setImmediate(r));
  await h.V.views.snippets.render();
  assert.deepEqual(h.deletes, ["my email"]);
  assert.equal(h["snippet-list"].children.length, 0);
  assert.equal(h["snippet-empty"].hidden, false);
});

test("a history row for a snippet is labelled, others count words", () => {
  const { V } = loadVoisr(["js/ui.js"]);
  const handlers = { onCopy() {}, onDelete() {} };
  const label = (item) => V.ui.itemRow({ id: "a", ts: 1, words: 2, text: "my email", ...item }, handlers)
    .children[0].children[1].textContent;
  assert.match(label({ snippet: true }), /· Snippet$/);
  assert.match(label({}), /· 2 words$/);
});
