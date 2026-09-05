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
  const messages = [], cleaned = [], watched = [], calls = [], loaded = [], painted = [], history = [];
  const buttons = new Map(["fd-run", "fd-run-real", "fd-draft"].map((id) => [id, {
    disabled: false, attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
  }]));
  const context = {
    fetch(...args) { calls.push(args); return fetchImpl(...args); },
    rootFlow: { id: "flow", name: "Flow", start: "step", version: 1, revision: "draft-1", published: false,
      nodes: [{ id: "step", kind: "action", tool: "lookup", label: "Lookup" }] },
    idInput: { value: "flow" },
    flowInputs: [], saveInFlight: false, publishInFlight: false, runInFlight: false,
    applyInFlight: false, draftGeneration: 0, pendingDraft: null,
    snapshot: "unsaved draft", cleanDraft: null, description: { value: "First draft" },
    sample: { value: "", focus() { this.focused = true; } }, testData: { open: false },
    proposals: { step: { to_kind: "agent" } },
    runState: {}, runCursor: null, lastRunId: null,
    commitRawFields() { return []; },
    toApiFlow() { return { ...context.rootFlow }; },
    FDCore: require("../../maverick_dashboard/templates/flow_designer_core.js"),
    draftSnapshot() { return context.snapshot; },
    draftIsDirty() { return context.cleanDraft !== null && context.cleanDraft !== context.snapshot; },
    syncSaveButtons() {},
    markClean(snapshot) { cleaned.push(snapshot); context.cleanDraft = snapshot; },
    canonicalizeSavedFlow() {},
    say(message, error = false) { messages.push({ message, error }); },
    watchRun(id) { watched.push(id); },
    mvConfirm() { return Promise.resolve(true); },
    pushHistory() { history.push(context.snapshot); },
    fromApiFlow(flow) { painted.push(flow); context.rootFlow = flow; context.snapshot = JSON.stringify(flow); },
    load(...args) { loaded.push(args); },
    loadSuggestions() {}, loadSchema() {},
    document: { getElementById(id) {
      if (id === "fd-sample") return context.sample;
      if (id === "fd-test-data") return context.testData;
      if (id === "fd-legend") return { classList: { add() {} } };
      if (id === "fd-nl") return context.description;
      return buttons.get(id);
    } },
  };
  context.window = context;
  context.flow = context.rootFlow;
  const source = fs.readFileSync(path.resolve(__dirname,
    "../../maverick_dashboard/templates/flow_designer.js"), "utf8");
  const start = source.indexOf("  function apiFetch(");
  const end = source.indexOf("  function runUrl(", start);
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(source.slice(start, end), context, { filename: "flow_designer.js" });
  for (const [first, next] of [["draft", "load"], ["applyProposal", "showHistory"]]) {
    const pattern = new RegExp("  (?:async )?function " + first + "\\(");
    const begin = source.search(pattern);
    const finish = source.indexOf("  function " + next + "(", begin);
    assert.ok(begin >= 0 && finish > begin);
    vm.runInNewContext(source.slice(begin, finish), context, { filename: "flow_designer.js" });
  }
  function useRealLoad() {
    const begin = source.indexOf("  function load(");
    const finish = source.indexOf("  function loadSchema(", begin);
    vm.runInNewContext(source.slice(begin, finish), context, { filename: "flow_designer.js" });
  }
  function visibleProposal(node) {
    const begin = source.indexOf("    var prop =", source.indexOf("// self-rewrite proposal for this node"));
    const end = source.indexOf(";", begin) + 1;
    assert.ok(begin >= 0 && end > begin);
    return vm.runInNewContext(source.slice(begin, end) + " prop;", { ...context, n: node });
  }
  return { context, messages, cleaned, watched, calls, buttons, loaded, painted, history, useRealLoad, visibleProposal };
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

test("invalid advanced test data is revealed and focused before a test can run", () => {
  const ui = fixture(async () => response({}));
  ui.context.sample.value = "{ incomplete";
  ui.context.run(true);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.context.testData.open, true);
  assert.equal(ui.context.sample.focused, true);
  assert.equal(ui.messages.at(-1).error, true);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// The API offers applicable action→agent proposals. Agent→action suggestions
// require reviewed parameter bindings and are intentionally not applicable yet.
const proposal = { to_kind: "agent" };
function applied(overrides = {}) {
  return { id: "flow", version: 3, node_id: "step", from_kind: "action", to_kind: "agent", ...overrides };
}
function drafted(name) {
  return { flow: { id: "flow", name, start: "step", nodes: [{ id: "step", kind: "setvar" }] }, notes: [] };
}

test("Apply & track awaits its confirmed save and suppresses duplicate clicks until apply completes", async () => {
  const saveResult = deferred(), applyResult = deferred();
  const ui = fixture((url) => url.endsWith("/apply") ? applyResult.promise : saveResult.promise);
  const proposalNode = ui.context.rootFlow.nodes[0];
  const button = { disabled: false };
  const operation = ui.context.applyProposal(proposalNode, proposal, button);
  ui.context.applyProposal(proposalNode, proposal, button);
  assert.equal(ui.calls.length, 1);
  assert.equal(button.disabled, true);
  saveResult.resolve(response(saved()));
  await turn();
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.calls[1][0], "/api/v1/flows/flow/apply");
  assert.deepEqual(JSON.parse(ui.calls[1][1].body), {
    node_id: "step", to_kind: "agent", tool: "lookup", brief: "Lookup", version: 2, revision: "draft-2",
  });
  ui.context.applyProposal(proposalNode, proposal, button);
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.loaded.length, 0);
  applyResult.resolve(response(applied()));
  await operation;
  assert.equal(ui.context.proposals.step, undefined);
  assert.equal(ui.loaded[0][0], "flow");
  assert.match(ui.messages.at(-1).message, /Applied.*v3/);
  assert.equal(button.disabled, true, "the consumed suggestion cannot be applied a second time");
  assert.equal(ui.context.applyInFlight, false);
});

test("Apply sends nothing after an unconfirmed save", async () => {
  const ui = fixture(async () => response({}));
  const proposalNode = ui.context.rootFlow.nodes[0];
  await ui.context.applyProposal(proposalNode, proposal);
  await turn();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.loaded.length, 0);
  assert.ok(ui.context.proposals.step);
  assert.equal(ui.messages.at(-1).error, true);
});

for (const receipt of [null, {}, applied({ id: "other" }), applied({ node_id: "other" }),
  applied({ from_kind: "agent" }), applied({ to_kind: "action" }), applied({ version: 2 })]) {
  test(`Apply rejects an incomplete or mismatched receipt: ${JSON.stringify(receipt)}`, async () => {
    const ui = fixture(async (url) => response(url.endsWith("/apply") ? receipt : saved()));
    const proposalNode = ui.context.rootFlow.nodes[0];
    await ui.context.applyProposal(proposalNode, proposal);
    await turn();
    assert.equal(ui.calls.length, 2);
    assert.equal(ui.loaded.length, 0);
    assert.ok(ui.context.proposals.step);
    assert.equal(ui.messages.at(-1).error, true);
    assert.match(ui.messages.at(-1).message, /could not be confirmed/);
    assert.equal(ui.context.applyInFlight, false);
  });
}

test("a proposal requiring reviewed parameter bindings sends no save or apply request", async () => {
  const ui = fixture(async () => response(saved()));
  await ui.context.applyProposal({ id: "step", kind: "agent" }, {
    to_kind: "action", inferred_tool: "lookup", applicable: false,
  });
  assert.equal(ui.calls.length, 0);
});

test("a failed apply restores its control and shows the server's conflict message", async () => {
  const ui = fixture(async (url) => url.endsWith("/apply")
    ? response({ detail: "Flow changed; reload before applying." }, { ok: false }) : response(saved()));
  const proposalNode = ui.context.rootFlow.nodes[0];
  const button = { disabled: false };
  await ui.context.applyProposal(proposalNode, proposal, button);
  assert.equal(ui.calls.length, 2);
  assert.equal(button.disabled, false);
  assert.ok(ui.context.proposals.step);
  assert.equal(ui.messages.at(-1).message, "Flow changed; reload before applying.");
});

test("manual save and publish cannot interleave with an in-flight apply", async () => {
  const pending = deferred();
  const ui = fixture(async (url) => url.endsWith("/apply") ? pending.promise : response(saved()));
  const proposalNode = ui.context.rootFlow.nodes[0];
  const operation = ui.context.applyProposal(proposalNode, proposal);
  await turn();
  assert.equal(await ui.context.save(), false);
  await ui.context.publishFlow();
  assert.equal(ui.calls.length, 2);
  pending.resolve(response(applied()));
  await operation;
});

test("Apply preserves canvas edits made while save is pending and does not apply an outdated proposal", async () => {
  const pending = deferred();
  const ui = fixture(() => pending.promise);
  const proposalNode = ui.context.rootFlow.nodes[0];
  const operation = ui.context.applyProposal(proposalNode, proposal);
  ui.context.snapshot = "newer canvas edits";
  pending.resolve(response(saved()));
  await operation;
  await turn();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.loaded.length, 0);
  assert.equal(ui.context.snapshot, "newer canvas edits");
  assert.match(ui.messages.at(-1).message, /changed/);
});

test("Apply keeps newer canvas edits instead of reloading over them after a confirmed receipt", async () => {
  const pending = deferred();
  const ui = fixture(async (url) => url.endsWith("/apply") ? pending.promise : response(saved()));
  const proposalNode = ui.context.rootFlow.nodes[0];
  const operation = ui.context.applyProposal(proposalNode, proposal);
  await turn();
  ui.context.snapshot = "newer canvas edits";
  pending.resolve(response(applied()));
  await operation;
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.loaded.length, 0);
  assert.equal(ui.context.snapshot, "newer canvas edits");
  assert.match(ui.messages.at(-1).message, /kept|preserved/);
});

test("a nested node with the same ID cannot display or apply the root node's suggestion", async () => {
  const ui = fixture(async (url) => response(url.endsWith("/apply") ? applied() : saved()));
  const rootNode = ui.context.rootFlow.nodes[0];
  assert.equal(ui.visibleProposal(rootNode), ui.context.proposals.step);
  const innerNode = { id: "step", kind: "action", tool: "inner_lookup", label: "Inner lookup" };
  const body = { id: "body", start: "step", nodes: [innerNode] };
  ui.context.rootFlow.nodes.push({ id: "loop", kind: "foreach", items: "items", body });
  ui.context.flow = body;
  const visible = ui.visibleProposal(innerNode);
  await ui.context.applyProposal(innerNode, proposal);
  assert.equal(ui.calls.length, 0, "a scoped duplicate ID must not mutate the root node");
  assert.equal(visible, null);
  assert.match(ui.messages.at(-1).message, /main flow/);
});

test("Apply rejects a stale node object even when its ID matches a current root node", async () => {
  const ui = fixture(async (url) => response(url.endsWith("/apply") ? applied() : saved()));
  const staleNode = { ...ui.context.rootFlow.nodes[0], tool: "outdated_lookup" };
  await ui.context.applyProposal(staleNode, proposal);
  assert.equal(ui.calls.length, 0);
  assert.ok(ui.context.proposals.step);
  assert.match(ui.messages.at(-1).message, /main flow/);
});

test("the post-apply refresh cannot overwrite edits made during its GET request", async () => {
  const pending = deferred();
  const ui = fixture(() => pending.promise);
  ui.useRealLoad();
  ui.context.load("flow", "unsaved draft");
  ui.context.snapshot = "edits during reload";
  pending.resolve(response(drafted("Applied flow").flow));
  await turn();
  assert.equal(ui.painted.length, 0);
  assert.equal(ui.context.snapshot, "edits during reload");
  assert.match(ui.messages.at(-1).message, /kept|preserved/);
});

test("only the latest draft response can replace the canvas or its feedback", async () => {
  const pending = [deferred(), deferred()];
  let requests = 0;
  const ui = fixture(() => pending[requests++].promise);
  ui.context.draft();
  ui.context.description.value = "Second draft";
  ui.context.draft();
  assert.equal(ui.calls.length, 2);
  pending[1].resolve(response(drafted("Second draft")));
  await turn();
  const latestMessage = ui.messages.at(-1);
  pending[0].resolve(response(drafted("First draft")));
  await turn();
  assert.deepEqual(ui.painted.map((flow) => flow.name), ["Second draft"]);
  assert.equal(ui.history.length, 1);
  assert.deepEqual(ui.messages.at(-1), latestMessage);
});

test("duplicate identical draft submissions make one model request", async () => {
  const pending = deferred();
  const ui = fixture(() => pending.promise);
  ui.context.draft();
  ui.context.draft();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.buttons.get("fd-draft").attributes["aria-busy"], "true");
  pending.resolve(response(drafted("First draft")));
  await turn();
  assert.equal(ui.painted.length, 1);
  assert.equal(ui.buttons.get("fd-draft").attributes["aria-busy"], undefined);
});

test("drafting preserves canvas edits made after the request started", async () => {
  const pending = deferred();
  const ui = fixture(() => pending.promise);
  ui.context.draft();
  ui.context.snapshot = "user changed the canvas";
  pending.resolve(response(drafted("First draft")));
  await turn();
  assert.equal(ui.painted.length, 0);
  assert.equal(ui.history.length, 0);
  assert.equal(ui.context.snapshot, "user changed the canvas");
  assert.match(ui.messages.at(-1).message, /changed/);
});

test("a superseded draft failure cannot replace the latest success message", async () => {
  const pending = [deferred(), deferred()];
  let requests = 0;
  const ui = fixture(() => pending[requests++].promise);
  ui.context.draft();
  ui.context.description.value = "Second draft";
  ui.context.draft();
  pending[1].resolve(response(drafted("Second draft")));
  await turn();
  const latestMessage = ui.messages.at(-1);
  pending[0].reject(new Error("old network failure"));
  await turn();
  assert.deepEqual(ui.messages.at(-1), latestMessage);
});

for (const receipt of [null, {}, { flow: {} }, { flow: { nodes: null } }]) {
  test(`an incomplete draft receipt leaves the existing canvas intact: ${JSON.stringify(receipt)}`, async () => {
    const ui = fixture(async () => response(receipt));
    ui.context.draft();
    await turn();
    assert.equal(ui.painted.length, 0);
    assert.equal(ui.history.length, 0);
    assert.equal(ui.messages.at(-1).error, true);
  });
}
