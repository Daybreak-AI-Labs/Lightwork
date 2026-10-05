"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const {shell} = require('./_shell');

const read = (name) => fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/static/" + name), "utf8");
const jobs = read("lightwork-ops-jobs.js");
const learning = read("lightwork-learning-ops.js");
const learned = read("lightwork-learned-ops.js");
const templates = path.resolve(__dirname, "../../maverick_dashboard/templates/");

// The partials' own markup, with the Jinja admin blocks kept (the page is
// rendered for an administrator in these tests).
function partial(name) {
  return fs.readFileSync(path.join(templates, name), "utf8")
    .replace(/\{#[\s\S]*?#\}/g, "")
    .replace(/\{%[^%]*%\}/g, "")
    .replace(/\{\{[^}]*\}\}/g, "1")
    .replace(/<script[\s\S]*?<\/script>/g, "");
}

const JOB = (result) => ({ job: { id: "0123456789abcdef", state: "done", can_stop: false, result } });  // pragma: allowlist secret

function defaults() {
  return {
    "/api/v1/learning-ops/self-modify": { state: "blocked", blockers: ["Add at least two practice tests it must pass."],
      level: "starter", areas: 2, results: 0, spending_limit: 4, technical: { areas: ["docs/**"] } },
    "/api/v1/learning-ops/self-modify/results": { results: [] },
    "/api/v1/training-ops/model-improvement": { on: false, catalog: { as_of: "2026-06-01", candidates: 7 },
      task_sets: [{ value: "invoice_triage", label: "Invoice triage", ready: false, practice_groups: 3,
        kept_aside_groups: 2, why_not: ["Needs more independent practice task groups."] }],
      choices: { roles: [{ value: "coder", label: "Coder" }], memory_gib: [16, 32], context_tokens: [16384],
        concurrency: [1], splits: [{ value: "holdout", label: "Kept-aside questions" }] } },
    "/api/v1/training-ops/reward-models": { models: [], waiting_outlines: 5 },
    "/api/v1/training-ops/codec": { shorthand: { codes: 3, on: false, phrases: ["check the totals"] },
      token_codes: { codes: 0, on: false }, exact_count_available: false },
    "/api/v1/learning-ops/donations": { sharing: true, with_text: false, waiting: 2, sizes: [10, 12], can_clear: true },
    "/api/v1/learning-ops/jobs": { jobs: [{ id: "a", label: "Training the reward model", state: "done", started_at: 1700000000 }] },
    "/api/v1/learning-ops/flywheel/turn": JOB({ acted: true, tasks: 12, avoid: ["send_unchecked"], habits: ["check_totals"],
      ideas: [{ instead_of: "retry", try: "ask the owner" }], counts: { avoid: 1, habits: 1, ideas: 1 } }),
    "/api/v1/learning-ops/factory/preview": { corrections: [{ guidance: "Add a deny list.", seen_in: 3, area: "provisioning" }] },
    "/api/v1/learning-ops/self-modify/run": JOB({ ran: false, tested: 0, spent: 0, rounds: [], problem: "Turn this experiment on first." }),
    "/api/v1/learning-ops/donations/clear": { cleared: 2 },
    "/api/v1/learning-ops/self-harness/plan": { enabled: true, test_cases_set_up: true, careful_testing: true, spending_limit: 5,
      checker: "Checker", models: [{ value: "claude-x", label: "Claude X" }, { value: "worker-y", label: "Worker Y" }],
      hold_out: { set_up: true, exists: false },
      places: { test_cases: "All settings, Memory & learning, Self-improving instructions, “Test cases for improvements”",
        hold_out: "All settings, Memory & learning, Self-improving instructions, “Record of unseen-case checks”",
        self_coaching: "“Self-harness” under Learning systems on the Learning page" } },
    "/api/v1/learning-ops/self-harness/clash-check": JOB({ pairs: [{ model: "claude-x", first: "cite", second: "never cite" }], spent: 0.02 }),
    "/api/v1/learning-ops/self-harness/hold-out/set-up": { created: true, record_number: "ledger-42" },
    "/api/v1/learning-ops/self-harness/test-cases/import/preview": { replace: false, added: 1, total_now: 1, total_after: 2,
      keys: [{ key: "claude-x", label: "Claude X", now: 1, after: 2 }] },
    "/api/v1/learning-ops/self-harness/test-cases/import": { cases: 1, replaced: false },
  };
}

async function fixture(markup, scripts, { answers = {}, confirm = true } = {}) {
  const dom = new JSDOM(markup, { url: "https://lightwork.example/learning", runScripts: "outside-only" });
  const { window } = dom;
  shell(window);
  const calls = [];
  const table = Object.assign(defaults(), answers);
  window.mvErrorText = (failure, action) => `Could not ${action}.`;
  window.mvPlainDetail = (d) => (typeof d === "string" && /^[A-Z].*\.$/.test(d) ? d : "");
  window.mvHuman = (v) => String(v).replace(/_/g, " ");
  window.mvWhen = () => "14 Nov 2023";
  window.mvToast = (message) => calls.push({ toast: message });
  window.mvConfirm = async (message) => { calls.push({ confirm: message }); return confirm; };
  window.fetch = async (url, init = {}) => {
    const method = init.method || "GET";
    let body = init.body;
    if (typeof body === "string") { try { body = JSON.parse(body); } catch (e) { /* raw */ } }
    calls.push({ url, method, body });
    const answer = table[url.split("?")[0]];
    const status = answer && answer.__status ? answer.__status : (answer ? 200 : 404);
    return new Response(JSON.stringify(answer || { detail: "Missing." }), { status, headers: { "Content-Type": "application/json" } });
  };
  const settle = async () => { for (let i = 0; i < 60; i++) await new Promise((done) => setImmediate(done)); };
  window.eval(jobs);
  scripts.forEach((s) => window.eval(s));
  await settle();
  return { window, doc: window.document, calls, settle };
}

const LEARNING = () => partial("_learning_ops.html");
const LEARNED = () => `<p id="llp-cost"></p>${partial("_learned_ops.html")}`;

test("learning page loads each system's status in plain words", async (t) => {
  const ui = await fixture(LEARNING(), [learning]);
  t.after(() => ui.window.close());
  const text = (id) => ui.doc.getElementById(id).textContent;
  assert.match(text("lo-sm-state"), /Add at least two practice tests/);
  assert.match(text("lo-sm-state"), /can spend up to \$4\.00/);
  assert.match(text("lo-mi-state"), /Invoice triage.*Not yet: Needs more independent practice task groups/);
  assert.match(text("lo-rm-list"), /5 task outlines waiting/);
  assert.match(text("lo-cd-state"), /check the totals/);
  assert.match(text("lo-dn-state"), /2 outlines waiting, not yet sent/);
  assert.match(text("lo-jobs"), /Training the reward model.*Finished/);
  assert.equal(ui.doc.getElementById("lo-cd-exact").options.length, 1, "no exact count offered when not allowed");
});

test("turning the flywheel asks, runs a job and lists what was learned", async (t) => {
  const ui = await fixture(LEARNING(), [learning]);
  t.after(() => ui.window.close());
  ui.doc.getElementById("lo-fw-run").click();
  await ui.settle();
  assert.match(ui.calls.find((c) => c.confirm).confirm, /whole installation/);
  assert.match(ui.doc.getElementById("lo-fw-out").textContent, /Looked at 12 tasks/);
  assert.match(ui.doc.getElementById("lo-fw-out").textContent, /Instead of “retry”, try “ask the owner”/);
});

test("practice rounds show the spending limit in the confirmation and the kernel's refusal", async (t) => {
  const ui = await fixture(LEARNING(), [learning]);
  t.after(() => ui.window.close());
  ui.doc.getElementById("lo-sm-objective").value = "make summaries faster";
  ui.doc.getElementById("lo-sm-form").dispatchEvent(new ui.window.Event("submit", { cancelable: true }));
  await ui.settle();
  assert.match(ui.calls.find((c) => c.confirm).confirm, /Nothing is installed\. It can spend up to \$4\.00/);
  const sent = ui.calls.find((c) => c.method === "POST");
  assert.deepEqual(sent.body, { objective: "make summaries faster", cycles: 1, confirm: true });
  assert.match(ui.doc.getElementById("lo-sm-out").textContent, /Turn this experiment on first/);
});

test("declining a confirmation sends nothing", async (t) => {
  const ui = await fixture(LEARNING(), [learning], { confirm: false });
  t.after(() => ui.window.close());
  ui.doc.getElementById("lo-fw-run").click();
  ui.doc.querySelector("#lo-dn-state button").click();
  await ui.settle();
  assert.equal(ui.calls.filter((c) => c.method === "POST").length, 0);
});

test("deleting waiting outlines confirms the consequence", async (t) => {
  const ui = await fixture(LEARNING(), [learning]);
  t.after(() => ui.window.close());
  ui.doc.querySelector("#lo-dn-state button").click();
  await ui.settle();
  assert.match(ui.calls.find((c) => c.confirm).confirm, /without being sent and cannot be recovered/);
  assert.deepEqual(ui.calls.find((c) => c.method === "POST").body, { confirm: true });
  assert.match(ui.doc.getElementById("lo-dn-status").textContent, /Deleted 2 outlines/);
});

test("the agent-building preview lists guidance in a table", async (t) => {
  const ui = await fixture(LEARNING(), [learning]);
  t.after(() => ui.window.close());
  ui.doc.getElementById("lo-fa-preview").click();
  await ui.settle();
  assert.match(ui.doc.getElementById("lo-fa-out").textContent, /Add a deny list\.3 agents/);
});

test("learned page tools: clash check, hold-out set-up and a previewed import", async (t) => {
  const ui = await fixture(LEARNED(), [learned]);
  t.after(() => ui.window.close());
  assert.equal(ui.doc.getElementById("lpo-transfer-source").options.length, 2);
  ui.doc.getElementById("lpo-clash-run").click();
  await ui.settle();
  assert.match(ui.doc.getElementById("lpo-clash-out").textContent, /1 pair of lessons may contradict/);

  ui.doc.querySelector("#lpo-holdout button").click();
  await ui.settle();
  assert.match(ui.calls.filter((c) => c.confirm).pop().confirm, /can only be done once/);
  assert.match(ui.doc.getElementById("lpo-holdout").textContent, /ledger-42/);

  const input = ui.doc.getElementById("lpo-import-file");
  const file = new ui.window.File(['{"claude-x": []}'], "cases.json", { type: "application/json" });
  Object.defineProperty(input, "files", { value: [file] });
  ui.doc.getElementById("lpo-import-check").click();
  await ui.settle();
  assert.match(ui.doc.getElementById("lpo-cases-status").textContent, /add 1 new test case/);
  assert.equal(ui.calls.filter((c) => (c.url || "").startsWith("/api/v1/learning-ops/self-harness/test-cases/import?")).length, 0);
  ui.doc.querySelector("#lpo-cases-out button").click();
  await ui.settle();
  const imported = ui.calls.find((c) => (c.url || "").startsWith("/api/v1/learning-ops/self-harness/test-cases/import?"));
  assert.match(imported.url, /confirm=true&replace=false/);
  assert.match(ui.doc.getElementById("lpo-cases-status").textContent, /Added 1 test case/);
});

test("the learned page names the exact setting to fill in first", async (t) => {
  const plan = defaults()["/api/v1/learning-ops/self-harness/plan"];
  const answers = { "/api/v1/learning-ops/self-harness/plan": Object.assign({}, plan,
    { test_cases_set_up: false, hold_out: { set_up: false, exists: false } }) };
  const ui = await fixture(LEARNED(), [learned], { answers });
  t.after(() => ui.window.close());
  assert.match(ui.doc.getElementById("lpo-plan").textContent,
    /choose where to keep them in All settings, Memory & learning, Self-improving instructions, “Test cases for improvements”/);
  assert.match(ui.doc.getElementById("lpo-holdout").textContent, /“Record of unseen-case checks”/);
  assert.equal(ui.doc.querySelector("#lpo-holdout a").getAttribute("href"), "/settings/all");
});
