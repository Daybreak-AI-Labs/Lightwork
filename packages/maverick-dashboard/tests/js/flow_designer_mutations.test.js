// Run with: node --test packages/maverick-dashboard/tests/js/flow_designer_mutations.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function response(body, { ok = true, invalid = false } = {}) {
  return { ok, status: ok ? 200 : 409, async json() {
    if (invalid) throw new SyntaxError("invalid JSON");
    return body;
  } };
}

function saved(overrides = {}) {
  return { id: "flow", version: 2, revision: "draft-2", published: false,
    published_version: null, published_revision: "", ...overrides };
}

function fixture(fetchImpl) {
  const messages = [], cleaned = [], watched = [], calls = [];
  const buttons = new Map(["fd-run", "fd-run-real"].map((id) => [id, {
    disabled: false, attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
  }]));
  const context = {
    fetch(...args) { calls.push(args); return fetchImpl(...args); },
    rootFlow: { id: "flow", name: "Flow", version: 1, revision: "draft-1", published: false },
    idInput: { value: "flow" },
    flowInputs: [], saveInFlight: false, publishInFlight: false, runInFlight: false,
    runState: {}, runCursor: null, lastRunId: null,
    commitRawFields() { return []; },
    toApiFlow() { return { ...context.rootFlow, nodes: [{ id: "step", kind: "setvar" }] }; },
    FDCore: { validateFlow() { return []; } },
    draftSnapshot() { return "unsaved draft"; },
    syncSaveButtons() {},
    markClean(snapshot) { cleaned.push(snapshot); },
    canonicalizeSavedFlow() {},
    say(message, error = false) { messages.push({ message, error }); },
    watchRun(id) { watched.push(id); },
    mvConfirm() { return Promise.resolve(true); },
    document: { getElementById(id) {
      if (id === "fd-sample") return { value: "" };
      if (id === "fd-legend") return { classList: { add() {} } };
      return buttons.get(id);
    } },
  };
  context.window = context;
  const source = fs.readFileSync(path.resolve(__dirname,
    "../../maverick_dashboard/templates/flow_designer.js"), "utf8");
  const start = source.indexOf("  function apiFetch(");
  const end = source.indexOf("  function runUrl(", start);
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(source.slice(start, end), context, { filename: "flow_designer.js" });
  return { context, messages, cleaned, watched, calls, buttons };
}

async function turn() { await new Promise((resolve) => setImmediate(resolve)); }

for (const bad of [response({}, { invalid: true }), response({}), response(null), response(saved({ id: "another-flow" }))]) {
  test(`save preserves the dirty draft when its receipt is invalid: ${JSON.stringify(bad)}`, async () => {
    const ui = fixture(async () => bad);
    assert.equal(await ui.context.save(), false);
    assert.equal(ui.cleaned.length, 0);
    assert.equal(ui.context.rootFlow.version, 1);
    assert.equal(ui.context.rootFlow.revision, "draft-1");
    assert.equal(ui.messages.at(-1).error, true);
    assert.equal(ui.context.saveInFlight, false);
  });
}

test("a valid save records the returned version and marks the submitted draft clean", async () => {
  const ui = fixture(async () => response(saved()));
  assert.equal(await ui.context.save(), true);
  assert.equal(ui.context.rootFlow.version, 2);
  assert.equal(ui.context.rootFlow.revision, "draft-2");
  assert.deepEqual(ui.cleaned, ["unsaved draft"]);
});

test("publish sends nothing after an unconfirmed save", async () => {
  const ui = fixture(async () => response({}));
  await ui.context.publishFlow();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.context.rootFlow.published, false);
});

for (const publication of [{}, { id: "flow", published: true, published_version: 1, published_revision: "release" }]) {
  test(`publication requires the exact saved version receipt: ${JSON.stringify(publication)}`, async () => {
    let count = 0;
    const ui = fixture(async () => response(++count === 1 ? saved() : publication));
    await ui.context.publishFlow();
    assert.equal(ui.context.rootFlow.published, false);
    assert.equal(ui.messages.at(-1).error, true);
    assert.equal(ui.context.publishInFlight, false);
  });
}

test("a confirmed publication records the release only after its matching save", async () => {
  let count = 0;
  const ui = fixture(async () => response(++count === 1 ? saved() : {
    id: "flow", published: true, published_version: 2, published_revision: "release-2",
  }));
  await ui.context.publishFlow();
  assert.equal(ui.context.rootFlow.published, true);
  assert.equal(ui.context.rootFlow.published_revision, "release-2");
  assert.deepEqual(JSON.parse(ui.calls[1][1].body), { version: 2, revision: "draft-2" });
});

for (const result of [{}, { run_id: "run", dry_run: false, status: "queued" }, null]) {
  test(`a dry run never claims success for a missing or mismatched receipt: ${JSON.stringify(result)}`, async () => {
    const ui = fixture(async () => response(result));
    ui.context.run(true);
    await turn();
    assert.equal(ui.watched.length, 0);
    assert.equal(ui.context.lastRunId, null);
    assert.equal(ui.messages.at(-1).error, true);
  });
}

test("a duplicate run click queues one request and restores controls after response", async () => {
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const ui = fixture(() => pending);
  ui.context.run(true);
  ui.context.run(true);
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.buttons.get("fd-run").disabled, true);
  resolve(response({ run_id: "run", dry_run: true, status: "queued" }));
  await turn();
  assert.deepEqual(ui.watched, ["run"]);
  assert.equal(ui.buttons.get("fd-run").disabled, false);
});

test("a failed run request restores controls and preserves a useful server error", async () => {
  const ui = fixture(async () => response({ detail: "Flow changed; reload it." }, { ok: false }));
  ui.context.run(true);
  await turn();
  assert.equal(ui.messages.at(-1).message, "Flow changed; reload it.");
  assert.equal(ui.messages.at(-1).error, true);
  assert.equal(ui.buttons.get("fd-run").disabled, false);
});
