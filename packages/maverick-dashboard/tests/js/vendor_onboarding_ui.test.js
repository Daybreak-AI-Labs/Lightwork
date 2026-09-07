// Run with: node --test packages/maverick-dashboard/tests/js/vendor_onboarding_ui.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function record(id, overrides = {}) {
  return {
    id, revision: 1, vendor_name: `Vendor ${id}`, document_id: `document-${id}`,
    project: "VEND", purpose: "Support replies", freshness_days: 365,
    stage: "assessment_ready", outcome: null,
    evidence: { status: "available", version: "v1", extracted: { answers: {} } },
    assessment: {
      status: "pending", result: { answered: 1, total: 1, risk_rating: "low" },
      template_snapshot: { questions: [{ id: "training", text: "Customer training?", section: "Data", guidance: "Check terms." }] },
      answers: { training: { answer: "unknown", note: "" } },
    },
    ...overrides,
  };
}

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, async json() { return body; } };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function fixture(records = [], intercept = null, search = "") {
  const document = { elements: new Map(), activeElement: null };
  let now = 2000;
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null;
      this.attributes = new Map(); this.listeners = {}; this.dataset = {};
      this.value = ""; this.disabled = false; this._text = "";
    }
    set id(value) { this._id = value; document.elements.set(value, this); }
    get id() { return this._id; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
    append(child) { child.parentElement = this; this.children.push(child); }
    replaceChildren(...children) { this._text = ""; this.children = []; children.forEach(child => this.append(child)); }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name); }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    focus() { if (!this.disabled) document.activeElement = this; }
    querySelectorAll(selector) {
      const selectors = selector.split(",").map(part => part.trim());
      const all = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
      return all.filter(node => selectors.some(part => {
        if (part === "*") return true;
        if (part === "select[data-question]") return node.tagName === "SELECT" && !!node.dataset.question;
        return node.tagName.toLowerCase() === part;
      }));
    }
  }
  document.createElement = tag => new Element(tag);
  document.getElementById = id => document.elements.get(id) || null;
  const add = (id, tag, parent = null) => {
    const element = document.createElement(tag); element.id = id;
    if (parent) parent.append(element);
    return element;
  };
  const root = add("vendor-onboarding", "section");
  root.dataset.apiBase = "/api/v1/vendor-onboarding";
  const ids = ["message", "case-title", "case-reference", "status", "evidence", "assessment",
    "corrections", "operation", "operation-record", "outcome", "renewal-status", "history", "cases"];
  ids.forEach(id => add(`vo-${id}`, "div", root));
  const intake = add("vo-intake", "form", root);
  ["vendor", "document", "project", "freshness", "renewal"].forEach(id => add(`vo-${id}`, "input", intake));
  add("vo-purpose", "textarea", intake);
  add("vo-create", "button", intake);
  ["rationale", "acceptance-reason"].forEach(id => add(`vo-${id}`, "textarea", root));
  ["refresh", "collect", "assess", "renew", "check-renewal", "review-approve", "review-reject",
    "prepare", "approve", "execute", "reconcile", "accept", "reject"].forEach(id => add(`vo-${id}`, "button", root));
  const calls = [];
  const byId = id => document.getElementById(`vo-${id}`);
  const fetch = async (url, options) => {
    const call = { url, ...options, body: options.body === undefined ? undefined : JSON.parse(options.body) };
    calls.push(call);
    if (intercept) {
      const intercepted = intercept(call);
      if (intercepted !== undefined) return intercepted;
    }
    if (options.method !== "GET") throw new Error(`Unexpected mutation ${url}`);
    if (url.endsWith("/cases")) return response({ cases: records });
    const selected = records.find(row => url.endsWith(`/cases/${encodeURIComponent(row.id)}`));
    return response(selected || { detail: "Not found" }, selected ? 200 : 404);
  };
  vm.runInNewContext(fs.readFileSync(
    path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-vendor-onboarding.js"), "utf8",
  ), { document, fetch, window: {location: {search}}, URLSearchParams, performance: { now: () => now }, setTimeout, clearTimeout, requestAnimationFrame: handler => setTimeout(handler, 0) });
  async function settle() {
    for (let turn = 0; turn < 20; turn += 1) {
      await new Promise(resolve => setImmediate(resolve));
      if (root.getAttribute("aria-busy") === "false") return;
    }
    throw new Error("UI did not settle");
  }
  return {
    root, byId, calls, document, settle,
    advanceTime(milliseconds) { now += milliseconds; },
    caseButton(id) { return byId("cases").children.find(button => button.dataset.caseId === id); },
    async load(id) { this.caseButton(id).listeners.click(); await settle(); },
    async click(id) { byId(id).listeners.click(); await settle(); },
  };
}

test("initial and refreshed lists describe reads accurately and expose selection", async () => {
  const ui = fixture([record("one"), record("two")]);
  await ui.settle();
  assert.match(ui.byId("message").textContent, /^Requests loaded/);
  assert.equal(ui.byId("rationale").disabled, true);
  assert.equal(ui.byId("acceptance-reason").disabled, true);
  await ui.load("one");
  assert.match(ui.byId("message").textContent, /^Request loaded/);
  assert.equal(ui.caseButton("one").getAttribute("aria-current"), "true");
  assert.equal(ui.caseButton("two").getAttribute("aria-current"), "false");
  ui.byId("rationale").value = "Current review draft";
  await ui.click("refresh");
  assert.match(ui.byId("message").textContent, /^Requests refreshed/);
  assert.equal(ui.byId("rationale").value, "Current review draft");
  assert.equal(ui.calls.every(call => call.method === "GET"), true);
});

test("a linked vendor request reads the exact authorized case without mutating it", async () => {
  const ui = fixture([record("requested"), record("another")], null, "?case=requested");
  await ui.settle();
  assert.match(ui.byId("message").textContent, /Linked request loaded/);
  assert.equal(ui.caseButton("requested").getAttribute("aria-current"), "true");
  assert.equal(ui.calls.length, 2);
  assert.ok(ui.calls.every(call => call.method === "GET"));
});

test("a linked request that is no longer accessible reports failure without selecting another", async () => {
  const ui = fixture([record("another")], null, "?case=missing");
  await ui.settle();
  assert.match(ui.byId("message").textContent, /Not found/);
  assert.equal(ui.byId("review-approve").disabled, true);
  assert.ok(ui.calls.every(call => call.method === "GET"));
});

test("switching cases clears decision drafts and updates current-request context", async () => {
  const ui = fixture([record("one"), record("two")]);
  await ui.settle(); await ui.load("one");
  ui.byId("rationale").value = "Only applies to vendor one";
  ui.byId("acceptance-reason").value = "One was accepted";
  ui.byId("answer-training").value = "no";
  ui.byId("note-training").value = "Only applies to vendor one";
  await ui.load("two");
  assert.equal(ui.byId("rationale").value, "");
  assert.equal(ui.byId("acceptance-reason").value, "");
  assert.equal(ui.byId("answer-training").value, "unknown");
  assert.equal(ui.byId("note-training").value, "");
  assert.match(ui.byId("case-reference").textContent, /Initial request · two/);
  assert.equal(ui.caseButton("two").getAttribute("aria-current"), "true");
  assert.equal(ui.caseButton("one").getAttribute("aria-current"), "false");
});

test("refreshing or reselecting an unchanged case preserves corrections and submits them for approval", async () => {
  let submitted;
  const ui = fixture([record("one")], call => {
    if (!call.url.endsWith("/review")) return undefined;
    submitted = call.body;
    return response(record("one", { revision: 2 }));
  });
  await ui.settle(); await ui.load("one");
  const answer = ui.byId("answer-training");
  const note = ui.byId("note-training");
  answer.value = "no";
  note.value = "Vendor terms reviewed";
  ui.byId("rationale").value = "Approve with corrected answer";
  ui.advanceTime(3000);
  await ui.click("refresh");
  await ui.load("one");
  ui.advanceTime(2000);
  assert.equal(ui.byId("answer-training"), answer);
  assert.equal(ui.byId("note-training"), note);
  assert.equal(answer.value, "no");
  assert.equal(note.value, "Vendor terms reviewed");
  assert.equal(ui.byId("rationale").value, "Approve with corrected answer");
  await ui.click("review-approve");
  assert.equal(submitted.expected_revision, 1);
  assert.equal(submitted.decision, "approved");
  assert.equal(submitted.rationale, "Approve with corrected answer");
  assert.equal(submitted.review_seconds, 5);
  assert.deepEqual(submitted.corrections, { training: { answer: "no", note: "Vendor terms reviewed" } });
  assert.equal(ui.byId("rationale").value, "");
});

test("reordered assessment object keys preserve the same review draft", async () => {
  const records = [record("one")];
  const ui = fixture(records);
  await ui.settle(); await ui.load("one");
  ui.byId("answer-training").value = "no";
  ui.byId("note-training").value = "Draft correction note";
  ui.byId("rationale").value = "Draft review reason";
  records[0] = record("one", { assessment: {
    answers: { training: { note: "", answer: "unknown" } },
    template_snapshot: { questions: [{ guidance: "Check terms.", section: "Data", text: "Customer training?", id: "training" }] },
    result: { risk_rating: "low", total: 1, answered: 1 },
    status: "pending",
  } });
  await ui.click("refresh");
  assert.equal(ui.byId("answer-training").value, "no");
  assert.equal(ui.byId("note-training").value, "Draft correction note");
  assert.equal(ui.byId("rationale").value, "Draft review reason");
  assert.match(ui.byId("message").textContent, /^Requests refreshed/);
});

for (const change of ["revision", "assessment", "assessment answer"]) {
  test(`refreshing a changed ${change} resets stale drafts and requires a new decision reason`, async () => {
    const records = [record("one")];
    const ui = fixture(records);
    await ui.settle(); await ui.load("one");
    ui.byId("answer-training").value = "no";
    ui.byId("note-training").value = "Review of old evidence";
    ui.byId("rationale").value = "Approval of the old version";
    ui.byId("acceptance-reason").value = "Acceptance of the old version";
    if (change === "revision") records[0] = record("one", { revision: 2 });
    else if (change === "assessment") {
      records[0] = record("one");
      records[0].assessment = { ...records[0].assessment, id: "replacement-assessment" };
    } else {
      records[0] = record("one");
      records[0].assessment.answers.training = { answer: "yes", note: "Updated source" };
    }
    await ui.click("refresh");
    assert.equal(ui.byId("answer-training").value, records[0].assessment.answers.training.answer);
    assert.equal(ui.byId("note-training").value, records[0].assessment.answers.training.note);
    assert.equal(ui.byId("rationale").value, "");
    assert.equal(ui.byId("acceptance-reason").value, "");
    assert.match(ui.byId("message").textContent, /changed.*draft.*reset/i);
    await ui.click("review-approve");
    assert.match(ui.byId("message").textContent, /Enter a review rationale/);
    assert.equal(ui.calls.some(call => call.method === "POST"), false);
  });
}

test("refresh preserves acceptance text only for the same case version", async () => {
  const records = [record("one", { operation: { state: "verified" } })];
  const ui = fixture(records);
  await ui.settle(); await ui.load("one");
  ui.byId("acceptance-reason").value = "Verified current external result";
  await ui.click("refresh");
  assert.equal(ui.byId("acceptance-reason").value, "Verified current external result");
  records[0] = record("one", { revision: 2, operation: { state: "verified" } });
  await ui.click("refresh");
  assert.equal(ui.byId("acceptance-reason").value, "");
  await ui.click("accept");
  assert.match(ui.byId("message").textContent, /Enter an acceptance or rejection reason/);
  assert.equal(ui.calls.some(call => call.method === "POST"), false);
});

test("a pending refresh protects draft controls and prevents duplicate refresh or approval", async () => {
  const pending = deferred();
  let refreshing = false;
  const ui = fixture([record("one")], call => refreshing && call.url.endsWith("/cases/one") ? pending.promise : undefined);
  await ui.settle(); await ui.load("one");
  ui.byId("answer-training").value = "no";
  ui.byId("note-training").value = "Unsaved review note";
  ui.byId("rationale").value = "Unsaved reason";
  refreshing = true;
  ui.byId("refresh").listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.root.getAttribute("aria-busy"), "true");
  for (const id of ["refresh", "answer-training", "note-training", "rationale", "review-approve"])
    assert.equal(ui.byId(id).disabled, true, id);
  const requests = ui.calls.length;
  ui.byId("refresh").listeners.click();
  ui.byId("review-approve").listeners.click();
  assert.equal(ui.calls.length, requests);
  pending.resolve(response(record("one")));
  await ui.settle();
  assert.equal(ui.root.getAttribute("aria-busy"), "false");
  assert.equal(ui.byId("answer-training").disabled, false);
  assert.equal(ui.byId("answer-training").value, "no");
  assert.equal(ui.byId("note-training").value, "Unsaved review note");
  assert.equal(ui.byId("rationale").value, "Unsaved reason");
});

test("a failed refresh leaves review drafts intact and reports the error", async () => {
  let failRefresh = false;
  const ui = fixture([record("one")], call => failRefresh && call.url.endsWith("/cases/one")
    ? response({ detail: "Case refresh temporarily unavailable" }, 503) : undefined);
  await ui.settle(); await ui.load("one");
  ui.byId("answer-training").value = "no";
  ui.byId("note-training").value = "Unsaved note";
  ui.byId("rationale").value = "Unsaved reason";
  failRefresh = true;
  await ui.click("refresh");
  assert.equal(ui.byId("answer-training").value, "no");
  assert.equal(ui.byId("note-training").value, "Unsaved note");
  assert.equal(ui.byId("rationale").value, "Unsaved reason");
  assert.match(ui.byId("message").textContent, /Case refresh temporarily unavailable/);
  assert.equal(ui.byId("message").getAttribute("role"), "alert");
  assert.equal(ui.byId("refresh").disabled, false);
});

test("stages gate human review, execution, reconciliation and acceptance separately", async () => {
  const ui = fixture([
    record("review"),
    record("approved", { operation: { state: "approved" } }),
    record("uncertain", { operation: { state: "reconciliation_required" } }),
    record("verified", { operation: { state: "verified" } }),
    record("accepted", { operation: { state: "verified" }, outcome: { accepted: true } }),
  ]);
  await ui.settle(); await ui.load("review");
  assert.equal(ui.byId("rationale").disabled, false);
  assert.equal(ui.byId("corrections").querySelectorAll("select")[0].disabled, false);
  assert.equal(ui.byId("execute").disabled, true);
  await ui.load("approved");
  assert.equal(ui.byId("execute").disabled, false);
  assert.equal(ui.byId("accept").disabled, true);
  await ui.load("uncertain");
  assert.equal(ui.byId("reconcile").disabled, false);
  assert.equal(ui.byId("rationale").disabled, true);
  assert.equal(ui.byId("corrections").querySelectorAll("select")[0].disabled, true);
  await ui.load("verified");
  assert.equal(ui.byId("acceptance-reason").disabled, false);
  assert.equal(ui.byId("review-approve").disabled, true);
  await ui.load("accepted");
  assert.equal(ui.byId("acceptance-reason").disabled, true);
  assert.equal(ui.byId("renew").disabled, false);
});

test("a pending request disables edits and prevents a duplicate action", async () => {
  const pending = deferred();
  const ui = fixture([record("one")], call => call.url.endsWith("/collect-evidence") ? pending.promise : undefined);
  await ui.settle(); await ui.load("one");
  ui.byId("collect").listeners.click();
  assert.equal(ui.root.getAttribute("aria-busy"), "true");
  assert.equal(ui.byId("vendor").disabled, true);
  assert.equal(ui.byId("rationale").disabled, true);
  assert.equal(ui.byId("refresh").disabled, true);
  assert.equal(ui.caseButton("one").disabled, true);
  ui.byId("collect").listeners.click();
  assert.equal(ui.calls.filter(call => call.method === "POST").length, 1);
  pending.resolve(response(record("one", { revision: 2 })));
  await ui.settle();
  assert.equal(ui.byId("vendor").disabled, false);
  assert.equal(ui.byId("rationale").disabled, false);
  assert.match(ui.byId("message").textContent, /^Saved/);
});

test("renewal copies intake values without claiming a saved request and focuses an enabled field", async () => {
  const ui = fixture([record("accepted", { outcome: { accepted: true }, operation: { state: "verified" } })]);
  await ui.settle(); await ui.load("accepted");
  const before = ui.calls.length;
  await ui.click("renew");
  assert.equal(ui.calls.length, before);
  assert.equal(ui.byId("renewal").value, "accepted");
  assert.equal(ui.byId("document").value, "document-accepted");
  assert.match(ui.byId("message").textContent, /Renewal details copied.*Create request/);
  assert.equal(ui.byId("document").disabled, false);
  assert.equal(ui.document.activeElement, ui.byId("document"));
});

test("renewal readiness reports a check without implying customer acceptance was saved", async () => {
  const ui = fixture([record("accepted", { outcome: { accepted: true } })], call =>
    call.url.endsWith("/check-renewal") ? response({ classification: "human_fallback", reason: "Evidence changed" }) : undefined);
  await ui.settle(); await ui.load("accepted");
  await ui.click("check-renewal");
  assert.match(ui.byId("message").textContent, /^Renewal readiness checked/);
  assert.match(ui.byId("renewal-status").textContent, /human fallback.*Evidence changed/);
});

test("missing cost remains unmeasured while an explicit zero remains zero", async () => {
  const ui = fixture([record("missing"), record("zero", { metrics: { execution_cost_usd: 0 } })]);
  await ui.settle(); await ui.load("missing");
  assert.match(ui.byId("outcome").textContent, /Execution cost: unmeasured/);
  assert.doesNotMatch(ui.byId("outcome").textContent, /Execution cost: \$0/);
  await ui.load("zero");
  assert.match(ui.byId("outcome").textContent, /Execution cost: \$0/);
});

test("invalid server response shapes surface actionable errors and leave actions unavailable", async () => {
  for (const [body, status] of [[null, 200], [null, 503], [[], 200], ["unavailable", 503]]) {
    const ui = fixture([], () => response(body, status));
    await ui.settle();
    assert.equal(ui.byId("message").getAttribute("role"), "alert");
    assert.match(ui.byId("message").textContent, /Server response unavailable/);
    assert.equal(ui.byId("execute").disabled, true);
    assert.equal(ui.byId("refresh").disabled, false);
  }
});

test("incomplete case responses cannot select a new case or enable its actions", async () => {
  const ui = fixture([record("broken")], call => call.url.endsWith("/cases/broken") ? response({ id: "broken" }) : undefined);
  await ui.settle(); await ui.load("broken");
  assert.match(ui.byId("message").textContent, /Case response is incomplete/);
  assert.equal(ui.byId("collect").disabled, true);
  assert.equal(ui.caseButton("broken").getAttribute("aria-current"), "false");
});

test("review mutations carry the selected revision and the actual corrected answer", async () => {
  let submitted;
  const ui = fixture([record("one")], call => {
    if (!call.url.endsWith("/review")) return undefined;
    submitted = call.body;
    return response(record("one", { revision: 2 }));
  });
  await ui.settle(); await ui.load("one");
  ui.byId("rationale").value = "Confirm restricted use";
  ui.byId("answer-training").value = "no";
  ui.byId("note-training").value = "Vendor terms reviewed";
  await ui.click("review-approve");
  assert.equal(submitted.expected_revision, 1);
  assert.equal(submitted.decision, "approved");
  assert.deepEqual(submitted.corrections, { training: { answer: "no", note: "Vendor terms reviewed" } });
});
