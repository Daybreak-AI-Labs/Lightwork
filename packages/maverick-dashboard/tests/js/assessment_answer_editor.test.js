"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const templates = path.resolve(__dirname, "../../maverick_dashboard/templates");

function fixture() {
  const nodes = {}, calls = [], renders = [];
  class Element {
    constructor(tag = "div") { this.tag = tag; this.children = []; this.listeners = {}; this.value = ""; this.disabled = false; this.style = {}; this.valid = true; }
    set id(id) { this._id = id; nodes[id] = this; }
    get id() { return this._id; }
    appendChild(child) { this.children.push(child); return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    set textContent(value) { this.text = value; this.children = []; }
    get textContent() { return this.text || ""; }
    addEventListener(type, fn) { this.listeners[type] = fn; }
    reportValidity() { return this.valid; }
    focus() { this.focused = true; }
  }
  const get = id => nodes[id] || (nodes[id] = new Element());
  const context = { document: { getElementById: get }, currentId: "record", currentRevision: 1, viewGeneration: 1, answerBusy: false, answerDirty: false, selectedQuestion: "", decidedInDialog: false,
    body: new Element(), statusEl: new Element(), mvConfirm: async () => false,
    fetch: async (url, options) => { calls.push({ url, options }); throw new Error("No response configured"); },
    render: record => renders.push(record), reload: async () => {},
    el: (tag, cls, text = "") => { const element = new Element(tag); element.className = cls; element.textContent = text; return element; } };
  for (const name of ["sendBtn", "approveBtn", "rejectBtn", "acceptBtn", "triggerBtn"]) context[name] = new Element("button");
  vm.createContext(context);
  return { context, get, calls, renders, Element };
}
const record = () => ({ id: "record", revision: 1, status: "pending_review", question_text: { first: { text: "Has this been checked?" }, second: { text: "Is there supporting evidence?" } }, answers: {}, followups: [] });
function editor(rec = record()) {
  const ui = fixture(), source = fs.readFileSync(path.join(templates, "_assessment_review.html"), "utf8");
  vm.runInContext(source.slice(source.indexOf("    function renderAnswerEditor("), source.indexOf("    const RISK_RANK")), ui.context);
  ui.context.renderAnswerEditor(rec);
  ui.form = ui.context.body.children[0].children.find(child => child.tag === "form");
  return ui;
}
const response = data => ({ ok: true, json: async () => data });
const event = () => ({ preventDefault() {} });

test("question changes do not discard an unsaved answer without confirmation", async () => {
  const ui = editor();
  ui.get("arv-answer-value").value = "unknown"; ui.get("arv-answer-value").listeners.input();
  ui.get("arv-answer-note").value = "Need the source document";
  const picker = ui.get("arv-answer-question"); picker.value = "q:second";
  await picker.listeners.change();
  assert.equal(picker.value, "q:first"); assert.equal(ui.get("arv-answer-value").value, "unknown");
  assert.equal(ui.get("arv-answer-note").value, "Need the source document"); assert.equal(picker.disabled, false);
});

test("answer save sends the pinned question and version once and never approves the assessment", async () => {
  const ui = editor(); let finish;
  ui.get("arv-answer-value").value = "yes"; ui.get("arv-answer-note").value = "Verified by respondent";
  ui.context.fetch = async (url, options) => { ui.calls.push({ url, options }); return await new Promise(resolve => { finish = resolve; }); };
  const first = ui.form.listeners.submit(event());
  await ui.form.listeners.submit(event()); assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].url, "/api/v1/assess/sessions/record/answers/first");
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), { answer: "yes", note: "Verified by respondent", expected_revision: 1 });
  finish(response({ ...record(), revision: 2, answers: { first: { answer: "yes" } } })); await first;
  assert.equal(ui.renders.length, 1); assert.equal(ui.renders[0].status, "pending_review");
  assert.equal(ui.context.approveBtn.disabled, false); assert.equal(ui.context.answerBusy, false);
});

test("conflicting or unconfirmed saves preserve the respondent draft and offer a reload", async () => {
  for (const reply of [{ ok: false, status: 409, json: async () => ({ detail: "changed" }) }, response(null), response({ ...record(), id: "other", revision: 2 })]) {
    const ui = editor(); ui.get("arv-answer-value").value = "no"; ui.get("arv-answer-note").value = "My unsaved explanation";
    ui.context.fetch = async () => reply;
    await ui.form.listeners.submit(event());
    assert.equal(ui.renders.length, 0); assert.equal(ui.get("arv-answer-note").value, "My unsaved explanation");
    assert.equal(ui.context.statusEl.children[0].textContent, "Reload latest assessment");
    assert.equal(ui.context.answerBusy, false);
  }
});

test("a delayed answer response cannot repaint a different assessment", async () => {
  const ui = editor(); let finish; ui.get("arv-answer-value").value = "yes";
  ui.context.fetch = async () => await new Promise(resolve => { finish = resolve; });
  const saving = ui.form.listeners.submit(event());
  ui.context.currentId = "another-record"; ui.context.viewGeneration++;
  finish(response({ ...record(), revision: 2, answers: { first: { answer: "yes" } } })); await saving;
  assert.equal(ui.renders.length, 0);
});

test("unsaved questionnaire changes cannot be mistaken for the answers being approved", async () => {
  const ui = editor(), source = fs.readFileSync(path.join(templates, "_assessment_review.html"), "utf8");
  vm.runInContext(source.slice(source.indexOf("    async function decide("), source.indexOf("    approveBtn.addEventListener(")), ui.context);
  ui.get("arv-answer-value").value = "yes"; ui.get("arv-answer-value").listeners.input();
  await ui.context.decide("approved");
  assert.equal(ui.calls.length, 0); assert.match(ui.context.statusEl.textContent, /Save or discard/);
});

test("a follow-up answer uses the follow-up endpoint and retains its open review status", async () => {
  const rec = { ...record(), question_text: {}, status: "needs_more", followups: [{ id: "fu-1", question: "Which document?", answer: "" }] };
  const ui = editor(rec); ui.get("arv-answer-note").value = "The dated inspection report";
  ui.context.fetch = async (url, options) => { ui.calls.push({ url, options }); return response({ ...rec, revision: 2, status: "pending_review", followups: [{ id: "fu-1", answer: "The dated inspection report" }] }); };
  await ui.form.listeners.submit(event());
  assert.equal(ui.calls[0].url, "/api/v1/assess/sessions/record/followups/fu-1/answer");
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), { answer: "The dated inspection report", expected_revision: 1 });
  assert.equal(ui.renders[0].status, "pending_review");
});

test("intake quotes a single subject safely and opens only a verified created assessment", async () => {
  const ui = fixture(), opened = [];
  ui.context.window = { mvAssessmentReview: { open: id => opened.push(id) } };
  const source = fs.readFileSync(path.join(templates, "_workspace_worklist.html"), "utf8");
  vm.runInContext(source.slice(source.indexOf("  (function assessmentIntake()"), source.lastIndexOf("  </script>")), ui.context);
  ui.get("pw-start-type").value = "pia"; ui.get("pw-start-subject").value = 'Acme, "North"\nOffice';
  ui.context.fetch = async (url, options) => { ui.calls.push({ url, options }); return response({ created_count: 1, error_count: 0, created: [{ id: "new-record", subject: 'Acme, "North"\nOffice', type: "pia" }] }); };
  await ui.get("pw-start-form").listeners.submit(event());
  assert.equal(JSON.parse(ui.calls[0].options.body).csv, 'subject,type\n"Acme, ""North""\nOffice","pia"');
  assert.deepEqual(opened, ["new-record"]); assert.equal(ui.get("pw-start-subject").value, "");
});

test("risk summary distinguishes empty, unresolved and complete questionnaires without claiming zero risks are controlled", () => {
  const source = fs.readFileSync(path.join(templates, "_assessment_review.html"), "utf8");
  for (const state of ["empty", "unknown", "complete"]) {
    const ui = fixture();
    ui.context.currentQuestions = {}; ui.context.riskEl = ui.get("arv-risk");
    const begin = source.indexOf("    function riskClass(");
    const end = source.indexOf("      renderAnswerEditor(rec);");
    vm.runInContext(source.slice(begin, end) + "\n    }", ui.context);
    const rec = { ...record(), result: { answered: state === "complete" ? 2 : 0, total: 2,
      inherent_risk: "minimal", residual_risk: "minimal", risks_in_scope: 0, controls_in_place: 0 },
      answers: state === "empty" ? {} : { first: { answer: state === "unknown" ? "unknown" : "yes" } } };
    ui.context.render(rec);
    const childrenText = ui.context.body.children.flatMap(element => [element.textContent, ...element.children]).join(" ");
    if (state === "complete") {
      assert.equal(ui.context.riskEl.textContent, "Risk after safeguards: Minimal");
      assert.ok(childrenText.includes("in-scope risk areas controlled"));
    } else {
      assert.match(ui.context.riskEl.textContent, state === "empty" ? /not yet assessed/ : /provisional/);
      assert.equal(ui.context.riskEl.className, "badge warn");
      assert.match(childrenText, /0 of 2 questions resolved/);
      assert.equal(childrenText.includes("in-scope risk areas controlled"), false);
      assert.equal(ui.context.body.children[1].className, "arv__incomplete");
    }
  }
});

test("approving an incomplete questionnaire requires explicit acknowledgment and keeps canceled work unchanged", async () => {
  const ui = fixture(), confirmations = [], source = fs.readFileSync(path.join(templates, "_assessment_review.html"), "utf8");
  ui.context.currentCompletion = { answered: 0, total: 2, incomplete: true };
  ui.context.mvConfirm = async message => { confirmations.push(message); return false; };
  vm.runInContext(source.slice(source.indexOf("    async function decide("), source.indexOf("    approveBtn.addEventListener(")), ui.context);
  await ui.context.decide("approved");
  assert.match(confirmations[0], /0 of 2.*resolved/); assert.equal(ui.calls.length, 0);
  assert.equal(ui.context.approveBtn.disabled, false); assert.equal(ui.context.rejectBtn.disabled, false);
});

test("incomplete approval acknowledgment cannot transfer to a different assessment opened while it is pending", async () => {
  const ui = fixture(), source = fs.readFileSync(path.join(templates, "_assessment_review.html"), "utf8");
  ui.context.currentCompletion = { answered: 0, total: 2, incomplete: true };
  ui.context.mvConfirm = async () => { ui.context.currentId = "another-record"; ui.context.viewGeneration++; return true; };
  vm.runInContext(source.slice(source.indexOf("    async function decide("), source.indexOf("    approveBtn.addEventListener(")), ui.context);
  await ui.context.decide("approved"); assert.equal(ui.calls.length, 0);
});
