// Run with: node --test packages/maverick-dashboard/tests/js/no_code_grc_studio_client.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function client() {
  const window = {};
  const context = { TextDecoder, TextEncoder, console, window };
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(
      path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-no-code-grc-studio.js"),
      "utf8",
    ),
    context,
  );
  return window.LightworkNoCodeGRCStudioClient;
}

function plain(value) { return JSON.parse(JSON.stringify(value)); }

test("draft builder emits only closed lifecycle fields", () => {
  const api = client();
  const built = plain(api.buildBuilderPayload({
    mode: "create",
    studio_id: "enterprise-grc",
    version: "0",
    operation_id: "create-v1",
    blueprint_json: JSON.stringify({ schema: "lightwork.no-code-grc-blueprint.v1", studio_id: "enterprise-grc" }),
    tenant_id: "attacker",
    actor_ref: "attacker",
    storage_path: "C:/attacker",
  }));
  assert.deepEqual(Object.keys(built.payload).sort(), ["blueprint", "expected_latest_version", "operation_id"]);
  assert.equal(built.payload.expected_latest_version, 0);
  assert.equal("tenant_id" in built.payload, false);
});

test("replacement requires exact revision and digest", () => {
  const api = client();
  const built = plain(api.buildBuilderPayload({
    mode: "replace",
    studio_id: "enterprise-grc",
    version: "2",
    expected_revision: "4",
    expected_integrity_sha256: "a".repeat(64),
    operation_id: "replace-v2",
    blueprint_json: JSON.stringify({ schema: "lightwork.no-code-grc-blueprint.v1", studio_id: "enterprise-grc" }),
  }));
  assert.equal(built.version, 2);
  assert.equal(built.payload.expected_revision, 4);
  assert.equal(built.payload.expected_integrity_sha256, "a".repeat(64));
});

test("visual composer emits the exact closed blueprint root", () => {
  const api = client();
  const state = plain(api.emptyComposerState());
  state.objects.push({
    object_id: "risk-record",
    kind: "risk",
    label_ref: "risk-label",
    description_ref: null,
    fields: [{
      field_id: "severity",
      label_ref: "severity-label",
      field_type: "single_choice",
      required: true,
      sensitivity: "internal",
      options: ["low", "high"],
      reference_kind: null,
      default: "low",
    }],
  });
  const blueprint = plain(api.buildBlueprintFromComposer({
    studio_id: "enterprise-grc",
    name_ref: "enterprise-name",
    description_ref: "",
    tenant_id: "attacker",
    provider_url: "https://attacker.invalid",
  }, state));
  assert.deepEqual(Object.keys(blueprint).sort(), [
    "collector_bindings",
    "description_ref",
    "forms",
    "name_ref",
    "objects",
    "report_views",
    "rules",
    "schema",
    "studio_id",
  ]);
  assert.equal(blueprint.description_ref, null);
  assert.equal("tenant_id" in blueprint, false);
  assert.throws(() => api.opaqueList("safe,https://attacker.invalid", "Value", 10, 1), /invalid/);
});

test("advanced composer load rejects an open root schema", () => {
  const api = client();
  const closed = {
    schema: "lightwork.no-code-grc-blueprint.v1",
    studio_id: "enterprise-grc",
    name_ref: "enterprise-name",
    description_ref: null,
    objects: [],
    forms: [],
    rules: [],
    collector_bindings: [],
    report_views: [],
  };
  assert.equal(plain(api.loadComposerState(closed)).metadata.studio_id, "enterprise-grc");
  assert.throws(() => api.loadComposerState({ ...closed, eval: "process" }), /exact closed/);
});

test("lifecycle and rollback builders enforce exact CAS", () => {
  const api = client();
  const publish = plain(api.buildLifecyclePayload({
    action: "publish",
    studio_id: "enterprise-grc",
    version: "3",
    expected_revision: "5",
    expected_integrity_sha256: "b".repeat(64),
    expected_active_version: "2",
    expected_active_blueprint_sha256: "c".repeat(64),
    expected_pointer_revision: "7",
    operation_id: "publish-v3",
  }));
  assert.equal(publish.payload.expected_pointer_revision, 7);
  const rollback = plain(api.buildRollbackPayload({
    confirmation: true,
    studio_id: "enterprise-grc",
    target_version: "1",
    expected_target_blueprint_sha256: "d".repeat(64),
    expected_active_version: "3",
    expected_active_blueprint_sha256: "e".repeat(64),
    expected_pointer_revision: "8",
    rationale_ref: "production-regression",
    operation_id: "rollback-v1",
  }));
  assert.equal(rollback.payload.target_version, 1);
  assert.throws(() => api.buildRollbackPayload({ confirmation: false }), /confirmation/);
});

test("projection payload cannot carry target authority or provider material", () => {
  const api = client();
  const payload = plain(api.buildProjectionPayload({
    studio_id: "enterprise-grc",
    projection_kind: "collector",
    definition_id: "cloud-binding",
    target_id: "collector-registry",
    target_revision: "4",
    expected_active_version: "3",
    expected_blueprint_sha256: "f".repeat(64),
    target_authority_sha256: "0".repeat(64),
    provider_url: "https://attacker.invalid",
    credential: "secret",
  }));
  assert.deepEqual(Object.keys(payload).sort(), [
    "definition_id",
    "expected_active_version",
    "expected_blueprint_sha256",
    "projection_kind",
    "studio_id",
    "target_id",
    "target_revision",
  ]);
  const apply = plain(api.buildProjectionApplyPayload({
    studio_id: "enterprise-grc",
    projection_kind: "collector",
    definition_id: "cloud-binding",
    target_id: "collector-registry",
    target_revision: "4",
    expected_active_version: "3",
    expected_blueprint_sha256: "f".repeat(64),
    operation_id: "apply-cloud-binding",
    target_authority_sha256: "0".repeat(64),
    provider_url: "https://attacker.invalid",
  }));
  assert.deepEqual(Object.keys(apply).sort(), Object.keys(payload).concat("operation_id").sort());
});

test("declared and streaming oversized responses are cancelled", async () => {
  const api = client();
  let cancelled = false;
  const declared = {
    headers: { get: () => String(api.MAX_RESPONSE_BYTES + 1) },
    body: { cancel: async () => { cancelled = true; } },
  };
  await assert.rejects(api.readBoundedJson(declared), /byte bound/);
  assert.equal(cancelled, true);

  let reads = 0;
  let streamCancelled = false;
  const response = {
    headers: { get: () => null },
    ok: true,
    status: 200,
    body: {
      getReader() {
        return {
          async read() {
            reads += 1;
            if (reads === 1) { return { done: false, value: new Uint8Array(api.MAX_RESPONSE_BYTES) }; }
            return { done: false, value: new Uint8Array(1) };
          },
          async cancel() { streamCancelled = true; },
        };
      },
    },
  };
  await assert.rejects(api.readBoundedJson(response), /byte bound/);
  assert.equal(streamCancelled, true);
});
