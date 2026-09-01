// Run with: node --test packages/maverick-dashboard/tests/js/third_party_collaboration_portal.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const TOKEN = `tpc1.${"A".repeat(43)}`;

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this.textContent = "";
    this.className = "";
    this.id = "";
    this.type = "";
    this.dateTime = "";
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    this.children = this.children.filter((candidate) => candidate !== child);
    child.parentNode = null;
    return child;
  }

  replaceChildren(...children) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
    children.forEach((child) => this.appendChild(child));
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  }

  focus() {}

  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }

  querySelector() {
    return null;
  }

  querySelectorAll() {
    return [];
  }

  get lastElementChild() {
    return this.children.at(-1) || null;
  }
}

function validContract() {
  return {
    schema: "lightwork.third-party-collaboration-contract.v1",
    invitation_id: "invite-1",
    vendor_ref: "respondent-1",
    assessment_id: "assessment-1",
    notice_ref: "notice-1",
    notice_sha256: "a".repeat(64),
    artifact_policy: {
      allowed_kinds: ["audit_report"],
      maximum_artifacts: 2,
      maximum_total_bytes: 10000,
    },
    questions: [
      {
        question_id: "encryption-at-rest",
        domain: "security",
        prompt_ref: "prompt-encryption-at-rest",
        response_type: "boolean",
        required: true,
        options: [],
      },
    ],
    required_question_ids: ["encryption-at-rest"],
    answered_question_ids: [],
    draft_revision: 0,
    draft_artifacts: [],
    expires_at: 2000086400,
    limitations: [
      "respondent_attestations_are_unverified",
      "artifact_manifests_only_no_upload_bytes",
      "no_network_or_email_delivery",
      "no_model_inference",
    ],
  };
}

function parserContext() {
  const context = {};
  vm.createContext(context);
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-third-party-portal.js"),
    "utf8",
  );
  vm.runInContext(source, context, { filename: "lightwork-third-party-portal.js" });
  return context;
}

function browserContext(contract) {
  const ids = [
    "portal-status",
    "unlock-panel",
    "unlock-form",
    "invitation-credential",
    "unlock-button",
    "workspace",
    "lock-button",
    "vendor-reference",
    "assessment-reference",
    "notice-reference",
    "notice-digest",
    "invitation-expiry",
    "draft-revision",
    "contract-limitations",
    "question-progress",
    "questions",
    "artifact-policy",
    "artifact-manifests",
    "add-artifact-button",
    "respondent-attestation",
    "save-draft-button",
    "finalize-button",
    "receipt-panel",
    "receipt-revision",
    "receipt-assessment-digest",
    "receipt-attestation-digest",
    "receipt-evidence-status",
  ];
  const elements = new Map(ids.map((id) => [id, new FakeElement()]));
  elements.get("unlock-form").tagName = "FORM";
  elements.get("invitation-credential").tagName = "INPUT";
  const requests = [];
  const context = {
    AbortController,
    console,
    crypto: { randomUUID: () => "11111111-2222-4333-8444-555555555555" },
    document: {
      readyState: "complete",
      createDocumentFragment: () => new FakeElement("fragment"),
      createElement: (tagName) => new FakeElement(tagName),
      getElementById: (id) => elements.get(id) || null,
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json; charset=utf-8" },
        json: async () => contract,
      };
    },
    history: { replaceState: () => {} },
    location: {
      pathname: "/api/v1/external/third-party-collaboration/portal_ref_1234567890/portal",
      hash: "",
    },
    setTimeout,
    clearTimeout,
  };
  vm.createContext(context);
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-third-party-portal.js"),
    "utf8",
  );
  vm.runInContext(source, context, { filename: "lightwork-third-party-portal.js" });
  return { context, elements, requests };
}

test("contract parser accepts only the exact server-owned surface", () => {
  const { LightworkThirdPartyPortal: portal } = parserContext();
  const normalized = portal.validateContract(validContract());
  assert.equal(normalized.schema, "lightwork.third-party-collaboration-contract.v1");
  assert.deepEqual(Array.from(normalized.required_question_ids), ["encryption-at-rest"]);

  assert.throws(
    () => portal.validateContract({ ...validContract(), tenant_id: "tenant-a" }),
    /closed contract/,
  );
  assert.throws(
    () => portal.validateContract({ ...validContract(), provider_id: "provider-a" }),
    /closed contract/,
  );
  assert.throws(
    () => portal.validateContract({ ...validContract(), risk_score: 99 }),
    /closed contract/,
  );
  assert.throws(
    () => portal.validateContract({ ...validContract(), limitations: [] }),
    /limitations/,
  );
});

test("contract parser enforces question visibility and digest-only evidence bounds", () => {
  const { LightworkThirdPartyPortal: portal } = parserContext();
  assert.throws(
    () => portal.validateContract({
      ...validContract(),
      answered_question_ids: ["server-hidden-question"],
    }),
    /outside the visible contract/,
  );
  assert.throws(
    () => portal.validateContract({
      ...validContract(),
      questions: [{ ...validContract().questions[0], response_type: "free_text" }],
    }),
    /unsupported/,
  );
  assert.throws(
    () => portal.validateContract({
      ...validContract(),
      draft_artifacts: [{
        artifact_id: "report-1",
        kind: "audit_report",
        content_sha256: "not-a-digest",
        size_bytes: 100,
        media_type_ref: "application.pdf",
      }],
    }),
    /digest/,
  );
  assert.throws(
    () => portal.validateContract({
      ...validContract(),
      artifact_policy: {
        allowed_kinds: [],
        maximum_artifacts: 1,
        maximum_total_bytes: 100,
      },
    }),
    /inconsistent/,
  );
});

test("receipt parsers reject added scoring and identity claims", () => {
  const { LightworkThirdPartyPortal: portal } = parserContext();
  const draft = {
    invitation_id: "invite-1",
    revision: 1,
    draft_sha256: "b".repeat(64),
    active_question_ids: ["encryption-at-rest"],
    required_question_ids: ["encryption-at-rest"],
    artifact_count: 0,
    idempotent_replay: false,
  };
  assert.equal(portal.validateDraftReceipt(draft).revision, 1);
  assert.throws(() => portal.validateDraftReceipt({ ...draft, score: 100 }), /closed contract/);

  const finalReceipt = {
    schema: "lightwork.third-party-collaboration-receipt.v1",
    invitation_id: "invite-1",
    assessment_id: "assessment-1",
    assessment_revision: 2,
    assessment_integrity_sha256: "c".repeat(64),
    question_ids: ["encryption-at-rest"],
    attestation_sha256: "d".repeat(64),
    evidence_status: "respondent_attestation_unverified",
    idempotent_replay: false,
  };
  assert.equal(portal.validateFinalReceipt(finalReceipt).evidence_status,
    "respondent_attestation_unverified");
  assert.throws(
    () => portal.validateFinalReceipt({ ...finalReceipt, verified_identity: true }),
    /closed contract/,
  );
});

test("programmatic invitation flow sends the credential only in Authorization memory", async () => {
  const contract = { ...validContract(), questions: [], required_question_ids: [] };
  const { context, elements, requests } = browserContext(contract);
  elements.get("invitation-credential").value = TOKEN;

  const opened = await context.LightworkThirdPartyPortal.unlock(TOKEN);

  assert.equal(opened, true);
  assert.equal(elements.get("invitation-credential").value, "");
  assert.equal(requests.length, 1);
  assert.equal(
    requests[0].url,
    "/api/v1/external/third-party-collaboration/portal_ref_1234567890/contract",
  );
  assert.equal(requests[0].options.headers.Authorization, `ThirdPartyInvitation ${TOKEN}`);
  assert.equal(requests[0].options.credentials, "omit");
  assert.equal(requests[0].options.cache, "no-store");
  assert.equal(requests[0].options.redirect, "error");
  assert.equal(requests[0].options.referrerPolicy, "no-referrer");
  assert.equal(requests[0].url.includes(TOKEN), false);
  assert.equal(JSON.stringify(requests[0].options).includes("tenant"), false);
  assert.equal(JSON.stringify(requests[0].options).includes("provider"), false);
});
