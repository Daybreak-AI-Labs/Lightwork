// Run with: node --test packages/maverick-dashboard/tests/js/third_party_contracts_client.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function client() {
  const window = {};
  const context = { Date, Set, TextDecoder, TextEncoder, URLSearchParams, console, window };
  vm.createContext(context);
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-third-party-contracts.js"),
    "utf8",
  );
  vm.runInContext(source, context, { filename: "lightwork-third-party-contracts.js" });
  return window.LightworkThirdPartyContractsClient;
}

function headers(contentLength = null) {
  return {
    get(name) { return name.toLowerCase() === "content-length" ? contentLength : null; },
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

function plain(value) { return JSON.parse(JSON.stringify(value)); }

function commonCreate() {
  return {
    record_kind: "contract",
    record_id: "contract-7",
    vendor_id: "vendor-1",
    vendor_revision: "4",
    title_ref: "contract-title",
    owner_ref: "owner-risk",
    contract_status: "draft",
    agreement_type: "subscription",
    agreement_ref: "agreement-vault-ref",
    effective_at: "2033-05-18T03:33:20Z",
    expires_at: "2034-05-18T03:33:20Z",
    renewal_mode: "automatic",
    notice_period_days: "45",
    governing_law_ref: "law-ref",
    parent_contract_id: "",
    engagement_status: "planned",
    engagement_type: "software",
    service_category_ref: "ai-service",
    start_at: "",
    end_at: "",
    operational_criticality: "high",
    data_access: "confidential",
    hosting_model: "vendor_cloud",
    linked_contract_ids: "",
    business_owner_ref: "business-owner",
    field_schema_json: JSON.stringify([
      {
        field_id: "annual_cost", label_ref: "label-cost", field_type: "decimal",
        required: true, sensitivity: "confidential", options: [],
      },
      {
        field_id: "regions", label_ref: "label-regions", field_type: "multi_choice",
        required: true, sensitivity: "internal", options: ["us", "ca"],
      },
    ]),
    field_values_json: JSON.stringify({ annual_cost: 60000, regions: ["us", "ca"] }),
    formulas_json: JSON.stringify([
      {
        formula_id: "cost_per_region", operator: "divide", round_digits: 2,
        operands: [
          { source: "field", field_id: "annual_cost", value: null },
          { source: "field", field_id: "regions", value: null },
        ],
      },
    ]),
    idempotency_key: "create-contract-7",
    tenant_id: "attacker-tenant",
    actor_ref: "attacker",
    vendor_type: "attacker-controlled",
    vendor_authority_sha256: "f".repeat(64),
    storage_path: "C:/attacker",
  };
}

test("declared oversized lifecycle response is rejected before allocation", async () => {
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

test("streaming lifecycle response cancels immediately after 64 KiB", async () => {
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

test("invalid server JSON is rejected without echoing its content", async () => {
  const api = client();
  const secret = "private-contract-response";
  const fixture = streamingResponse([new TextEncoder().encode(secret)]);
  await assert.rejects(api.parseResponse(fixture.response), (error) => {
    assert.equal(error.code, "response_invalid_json");
    assert.equal(error.message.includes(secret), false);
    return true;
  });
});

test("contract create payload is closed and preserves safe schema formulas", () => {
  const api = client();
  const built = plain(api.buildCreatePayload(commonCreate()));
  assert.equal(built.kind, "contract");
  assert.equal(built.payload.record_id, "contract-7");
  assert.equal(built.payload.vendor_revision, 4);
  assert.equal(built.payload.lifecycle.agreement_type, "subscription");
  assert.equal(built.payload.lifecycle.notice_period_days, 45);
  assert.equal(built.payload.field_schema[0].sensitivity, "confidential");
  assert.equal(built.payload.formulas[0].operator, "divide");
  for (const forbidden of [
    "tenant_id", "actor_ref", "vendor_type", "vendor_authority_sha256",
    "storage_path", "recovery_path", "provider", "adapter",
  ]) {
    assert.equal(forbidden in built.payload, false);
  }
});

test("engagement payload contains only exact engagement lifecycle fields", () => {
  const api = client();
  const values = commonCreate();
  values.record_kind = "engagement";
  values.record_id = "engagement-7";
  values.engagement_status = "planned";
  values.linked_contract_ids = "contract-7,contract-7,contract-8";
  values.idempotency_key = "create-engagement-7";
  const built = plain(api.buildCreatePayload(values));
  assert.equal(built.kind, "engagement");
  assert.deepEqual(built.payload.lifecycle.linked_contract_ids, ["contract-7", "contract-8"]);
  assert.equal(built.payload.lifecycle.data_access, "confidential");
  assert.equal("agreement_ref" in built.payload.lifecycle, false);
});

test("update payload sends only explicitly selected closed fields", () => {
  const api = client();
  const built = plain(api.buildUpdatePayload({
    record_kind: "contract",
    expected_revision: "5",
    idempotency_key: "update-contract-7",
    include_title_ref: false,
    update_title_ref: "ignored-title",
    include_owner_ref: true,
    update_owner_ref: "owner-legal",
    include_status: true,
    update_status: "active",
    include_lifecycle: false,
    lifecycle_json: "{}",
    include_field_schema: false,
    update_field_schema_json: "[]",
    include_field_values: true,
    update_field_values_json: '{"annual_cost":75000,"regions":["us"]}',
    include_formulas: false,
    update_formulas_json: "[]",
    tenant_id: "attacker",
    principal_ref: "attacker",
  }));
  assert.deepEqual(built, {
    kind: "contract",
    payload: {
      expected_revision: 5,
      idempotency_key: "update-contract-7",
      owner_ref: "owner-legal",
      status: "active",
      field_values: { annual_cost: 75000, regions: ["us"] },
    },
  });
  assert.equal("tenant_id" in built.payload, false);
  assert.equal("principal_ref" in built.payload, false);
});

test("schema and formula validators reject extra or executable material", () => {
  const api = client();
  assert.throws(() => api.validateFieldSchema(JSON.stringify([
    {
      field_id: "risk", label_ref: "risk-label", field_type: "integer",
      required: true, sensitivity: "internal", options: [], prompt: "licensed content",
    },
  ])), /unsupported field/);
  assert.throws(() => api.validateFormulas(JSON.stringify([
    {
      formula_id: "risk", operator: "add", operands: [
        { source: "literal", field_id: null, value: 1 },
      ], round_digits: 0, expression: "eval(secret)",
    },
  ])), /unsupported field/);
  assert.throws(() => api.buildUpdatePayload({
    record_kind: "contract", expected_revision: "1", idempotency_key: "empty-update",
    include_title_ref: false, include_owner_ref: false, include_status: false,
    include_lifecycle: false, include_field_schema: false,
    include_field_values: false, include_formulas: false,
  }), /Select at least one/);
  const invalidCreate = commonCreate();
  invalidCreate.agreement_type = "caller_defined";
  assert.throws(() => api.buildCreatePayload(invalidCreate), /Agreement type is unsupported/);
  assert.throws(() => api.validateLifecycle("contract", { agreement_type: "subscription" }), /missing agreement_ref/);
});
