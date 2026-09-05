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
      path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-assessment-evidence-autopopulation.js"),
      "utf8",
    ),
    context,
  );
  return window.LightworkAssessmentEvidenceClient;
}

function response(chunks, contentLength = null) {
  let index = 0;
  let cancelled = false;
  const reader = {
    async read() {
      const value = chunks[index++];
      return value === undefined ? {done: true} : {done: false, value};
    },
    async cancel() { cancelled = true; },
  };
  return {
    value: {
      ok: true,
      status: 200,
      headers: {get: () => contentLength},
      body: {getReader: () => reader},
    },
    state: () => ({index, cancelled}),
  };
}

function plain(value) { return JSON.parse(JSON.stringify(value)); }

test("confidence displays as a percentage without inventing missing or invalid certainty", () => {
  const api = client();
  assert.equal(api.confidenceLabel(10000), "100%");
  assert.equal(api.confidenceLabel(9250), "92.5%");
  assert.equal(api.confidenceLabel(125), "1.25%");
  assert.equal(api.confidenceLabel(0), "0%");
  for (const value of [undefined, null, "9250", -1, 10001, NaN])
    assert.equal(api.confidenceLabel(value), "Not reported");
});

test("bounded response rejects declared and streamed overflow", async () => {
  const api = client();
  const declared = response([], String(api.MAX_RESPONSE_BYTES + 1));
  await assert.rejects(api.boundedResponseText(declared.value), /1 MiB/);
  const streamed = response([
    new Uint8Array(api.MAX_RESPONSE_BYTES),
    new Uint8Array([1]),
  ]);
  await assert.rejects(api.boundedResponseText(streamed.value), /1 MiB/);
  assert.deepEqual(streamed.state(), {index: 2, cancelled: true});
});

test("create payload accepts only assessment evidence selectors", () => {
  const payload = plain(client().buildCreatePayload({
    assessment_id: "assessment-one",
    analysis_ids: "analysis-one\nanalysis-two\nanalysis-one",
    idempotency_key: "create-one",
    tenant_id: "attacker",
    actor_ref: "attacker",
    artifact_bytes: "secret",
  }));
  assert.deepEqual(payload, {
    assessment_id: "assessment-one",
    analysis_ids: ["analysis-one", "analysis-two"],
    idempotency_key: "create-one",
  });
});

test("review approval requires an explicit question selection", () => {
  const api = client();
  assert.throws(() => api.buildReviewPayload({
    decision: "approve",
    selected_question_ids: [],
    rationale: "reviewed",
    expected_revision: "1",
    idempotency_key: "review-one",
  }), /Select at least one/);
  const rejected = plain(api.buildReviewPayload({
    decision: "reject",
    selected_question_ids: [],
    rationale: "insufficient evidence",
    expected_revision: "1",
    idempotency_key: "reject-one",
  }));
  assert.equal(rejected.decision, "reject");
  assert.deepEqual(rejected.selected_question_ids, []);
});

test("apply builder enforces explicit confirmation and closed CAS fields", () => {
  const api = client();
  assert.throws(() => api.buildApplyPayload({
    expected_revision: "2",
    expected_assessment_revision: "4",
    idempotency_key: "apply-one",
    confirm: false,
  }), /Confirm/);
  const payload = plain(api.buildApplyPayload({
    expected_revision: "2",
    expected_assessment_revision: "4",
    idempotency_key: "apply-one",
    confirm: true,
    answers: {pia_security: "no"},
    tenant_id: "attacker",
  }));
  assert.deepEqual(payload, {
    expected_revision: 2,
    expected_assessment_revision: 4,
    idempotency_key: "apply-one",
  });
});
