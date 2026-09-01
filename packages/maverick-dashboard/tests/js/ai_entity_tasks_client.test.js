// Run with: node --test packages/maverick-dashboard/tests/js/ai_entity_tasks_client.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function client() {
  const window = {};
  const context = { Date, TextDecoder, TextEncoder, console, window };
  vm.createContext(context);
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-ai-entity-tasks.js"),
    "utf8",
  );
  vm.runInContext(source, context, { filename: "lightwork-ai-entity-tasks.js" });
  return window.LightworkAIEntityTasksClient;
}

function headers(contentLength = null) {
  return {
    get(name) {
      return name.toLowerCase() === "content-length" ? contentLength : null;
    },
  };
}

function streamingResponse(chunks, { contentLength = null, ok = true, status = 200 } = {}) {
  let reads = 0;
  let cancelled = false;
  const reader = {
    async read() {
      const value = chunks[reads];
      reads += 1;
      return value === undefined ? { done: true } : { done: false, value };
    },
    async cancel() { cancelled = true; },
  };
  const body = {
    getReader() { return reader; },
    async cancel() { cancelled = true; },
  };
  return {
    response: { headers: headers(contentLength), body, ok, status },
    state: () => ({ reads, cancelled }),
  };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("declared oversized task response is rejected before body allocation", async () => {
  const api = client();
  const fixture = streamingResponse([new Uint8Array([123])], {
    contentLength: String(api.MAX_RESPONSE_BYTES + 1),
  });

  await assert.rejects(api.readBoundedJson(fixture.response), (error) => {
    assert.equal(error.code, "response_too_large");
    return true;
  });
  assert.deepEqual(fixture.state(), { reads: 0, cancelled: true });
});

test("streaming task response cancels as soon as it crosses 64 KiB", async () => {
  const api = client();
  const fixture = streamingResponse([
    new Uint8Array(api.MAX_RESPONSE_BYTES),
    new Uint8Array([1]),
    new TextEncoder().encode('{"never":"read"}'),
  ]);

  await assert.rejects(api.readBoundedJson(fixture.response), (error) => {
    assert.equal(error.code, "response_too_large");
    return true;
  });
  assert.deepEqual(fixture.state(), { reads: 2, cancelled: true });
});

test("bounded task JSON parses and invalid JSON is never echoed", async () => {
  const api = client();
  const valid = streamingResponse([new TextEncoder().encode('{"ready":true}')]);
  assert.equal((await api.parseResponse(valid.response)).ready, true);

  const secret = "private-task-response-content";
  const invalid = streamingResponse([new TextEncoder().encode(secret)]);
  await assert.rejects(api.parseResponse(invalid.response), (error) => {
    assert.equal(error.code, "response_invalid_json");
    assert.equal(error.message.includes(secret), false);
    assert.equal(error.status, 200);
    return true;
  });
});

test("create payload contains only closed task fields and a future due date", () => {
  const api = client();
  const due = new Date(Date.now() + 3_600_000).toISOString();
  const payload = plain(api.buildCreatePayload({
    entity_id: "inventory:asset-7",
    entity_revision: "8",
    kind: "remediation",
    title: "Review runtime owner",
    description: "Confirm the assigned control owner.",
    assignee_ref: "team:risk",
    collaborator_refs: "team:privacy, team:risk,team:privacy",
    priority: "high",
    due_at: due,
    idempotency_key: "create:asset-7:8",
    tenant_id: "caller-tenant",
    actor_ref: "caller-actor",
    entity_authority_sha256: "f".repeat(64),
  }));

  assert.equal(payload.entity_id, "inventory:asset-7");
  assert.equal(payload.entity_revision, 8);
  assert.deepEqual(payload.collaborator_refs, ["team:privacy", "team:risk"]);
  assert.equal(payload.due_at > Date.now() / 1000, true);
  assert.equal("tenant_id" in payload, false);
  assert.equal("actor_ref" in payload, false);
  assert.equal("entity_authority_sha256" in payload, false);
});

test("update payload expresses explicit clear operations without identity injection", () => {
  const api = client();
  const payload = plain(api.buildUpdatePayload({
    expected_revision: "4",
    idempotency_key: "update:task-4:clear",
    status: "blocked",
    priority: "critical",
    assignee_action: "clear",
    assignee_ref: "ignored:assignee",
    collaborator_action: "clear",
    collaborator_refs: "ignored:collaborator",
    due_action: "clear",
    due_at: "",
    tenant_id: "caller-tenant",
    principal_ref: "caller-principal",
  }));

  assert.deepEqual(payload, {
    expected_revision: 4,
    idempotency_key: "update:task-4:clear",
    status: "blocked",
    priority: "critical",
    assignee_ref: null,
    collaborator_refs: [],
    due_at: null,
  });
  assert.equal("tenant_id" in payload, false);
  assert.equal("principal_ref" in payload, false);
});

test("empty update and malformed collaborator references fail before transport", () => {
  const api = client();
  assert.throws(() => api.buildUpdatePayload({
    expected_revision: "1",
    idempotency_key: "empty:update",
    status: "",
    priority: "",
    assignee_action: "keep",
    collaborator_action: "keep",
    due_action: "keep",
  }), /Select at least one/);
  assert.throws(() => api.splitRefs("good-ref,not allowed"), /valid reference/);
  assert.throws(() => api.splitRefs(
    Array.from({ length: 65 }, (_, index) => "person:" + index).join(","),
  ), /64 collaborators/);
});
