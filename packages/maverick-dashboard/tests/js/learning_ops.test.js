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
const flywheel = read("lightwork-learning-flywheel.js");
const holdout = read("lightwork-learning-holdout-setup.js");
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

const CONTEXT = "c".repeat(64);
const OPERATION = "0123456789abcdef0123456789abcdef";  // pragma: allowlist secret
const CYCLE = { context: CONTEXT, operation: OPERATION, recorded: true, confirmed: true,
  message: "The original cycle task is recorded. Read its current progress; nothing was repeated.",
  result: { message: "Looked at 12 tasks.", counts: { avoid: 1, habits: 1, ideas: 1 },
    stores: [{ Information: "Safeguards", "Recorded state": "Saved", Items: 1 }] } };
const JOB = (result) => ({ job: { id: "0123456789abcdef", kind: "learning_task", state: "done", can_stop: false, result } });  // pragma: allowlist secret

function defaults() {
  return {
    "/api/v1/learning-ops/self-modify": { context: CONTEXT, revision: "r".repeat(64), model: "Coder model",
      state: "blocked", blockers: ["Add at least two practice tests it must pass."],
      level: "starter", areas: 2, results: 0, spending_limit: 4, technical: { areas: ["docs/**"] } },
    // Practice starts from a complete review of the exact objective (a sealed request).
    "/api/v1/learning-ops/self-modify/review": { token: "sealed:practice", job_id: "0123456789abcdef",  // pragma: allowlist secret
      review: { objective: "make summaries faster", cycles: 1, model: "Coder model", spending_limit: 4 } },
    "/api/v1/learning-ops/self-modify/results": { results: [] },
    "/api/v1/training-ops/model-improvement": { context: CONTEXT, on: false, catalog: { as_of: "2026-06-01", candidates: 7 },
      task_sets: [{ value: "invoice_triage", label: "Invoice triage", ready: false, practice_groups: 3,
        kept_aside_groups: 2, why_not: ["Needs more independent practice task groups."] }],
      choices: { roles: [{ value: "coder", label: "Coder" }], memory_gib: [16, 32], context_tokens: [16384],
        concurrency: [1], splits: [{ value: "holdout", label: "Kept-aside questions" }] } },
    "/api/v1/training-ops/reward-models": { models: [], waiting_outlines: 5 },
    "/api/v1/training-ops/codec": { shorthand: { codes: 3, on: false, phrases: ["check the totals"] },
      token_codes: { codes: 0, on: false }, exact_count_available: false },
    "/api/v1/learning-ops/donations": { sharing: true, with_text: false, waiting: 2, sizes: [10, 12], can_clear: true },
    "/api/v1/learning-ops/jobs": { jobs: [{ id: "a", label: "Training the reward model", state: "done", started_at: 1700000000 }] },
    // The improvement cycle: current work, a full review, then the reviewed start.
    "/api/v1/learning-ops/flywheel": { context: CONTEXT, revision: "rev-1", ready: true, tasks: 12, requests: [] },
    "/api/v1/learning-ops/flywheel/review": { context: CONTEXT, operation: OPERATION, token: "sealed:cycle",
      rows: [{ label: "Currently permitted recorded work", after: "12 recorded runs" },
        { label: "Cost", after: "No AI provider calls or charges." }] },
    "/api/v1/learning-ops/flywheel/turn": CYCLE,
    ["/api/v1/learning-ops/flywheel/original/" + OPERATION]: CYCLE,
    "/api/v1/learning-ops/factory/preview": { corrections: [{ guidance: "Add a deny list.", seen_in: 3, area: "provisioning" }] },
    "/api/v1/learning-ops/self-modify/run": JOB({ ran: false, tested: 0, spent: 0, rounds: [], problem: "Turn this experiment on first." }),
    "/api/v1/learning-ops/donations/clear": { cleared: 2 },
    "/api/v1/learning-ops/self-harness/plan": { enabled: true, test_cases_set_up: true, careful_testing: true, spending_limit: 5,
      checker: "Checker", models: [{ value: "claude-x", label: "Claude X" }, { value: "worker-y", label: "Worker Y" }],
      hold_out: { set_up: true, exists: false },
      places: { test_cases: "All settings, Memory & learning, Self-improving instructions, “Test cases for improvements”",
        hold_out: "All settings, Memory & learning, Self-improving instructions, “Record of unseen-case checks”",
        self_coaching: "“Self-harness” under Learning systems on the Learning page" } },
    // The meaning check: current originals, a full review, then the reviewed start.
    "/api/v1/learning-ops/self-harness/clash-check": (method) => method === "GET"
      ? { context: CONTEXT, requests: [], notice: "Original checks are kept for your account." }
      : { context: CONTEXT, operation: OPERATION, state: "done", message: "Original result read.", notice: "Judgments may be wrong.",
        checked: 1, unknown: 0, flagged: 1, wording_flags: 0, spent: 0.02,
        pairs: [{ area: "Claude X", first: "cite", second: "never cite", basis: "These ask for opposite things." }] },
    "/api/v1/learning-ops/self-harness/clash-check/review": { context: CONTEXT, operation: OPERATION, token: "sealed:check",
      changes: [{ label: "Lessons to compare", after: ["cite", "never cite"] }, { label: "Spending limit", after: "$5.00" }] },
    // The one-time hold-out record (its own reviewed setup).
    "/api/v1/learning-holdout-setup": { context: CONTEXT, revision: "rev-h", configured: true, ready: false,
      message: "The record location is chosen. Initialize the record once." },
    "/api/v1/learning-holdout-setup/review": { context: CONTEXT, operation: OPERATION, token: "sealed:holdout",
      changes: [{ label: "Change", after: "Initialize the hold-out record once; it is never replaced." }] },
    "/api/v1/learning-holdout-setup/save": { context: CONTEXT, operation: OPERATION, message: "The record was initialized.", current: "Ready." },
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
    let answer = table[url.split("?")[0]];
    if (typeof answer === "function") answer = answer(method, body);
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

test("an improvement cycle is reviewed in full, started once and its outcome read back", async (t) => {
  const ui = await fixture(LEARNING(), [learning, flywheel]);
  t.after(() => ui.window.close());
  const $ = (id) => ui.doc.getElementById(id);
  assert.match($("lo-fw-status").textContent, /12 permitted recorded runs/);
  $("lo-fw-run").click();
  await ui.settle();
  assert.equal($("lo-fw-review").hidden, false);
  assert.match($("lo-fw-review-rows").textContent, /Currently permitted recorded work.*12 recorded runs/s);
  assert.equal($("lo-fw-apply").disabled, true, "nothing starts before the review is ticked");
  assert.equal(ui.calls.filter((c) => c.url.endsWith("/flywheel/turn")).length, 0);
  $("lo-fw-reviewed").checked = true;
  $("lo-fw-reviewed").dispatchEvent(new ui.window.Event("change"));
  $("lo-fw-apply").click();
  await ui.settle();
  const turns = ui.calls.filter((c) => c.url.endsWith("/flywheel/turn"));
  assert.equal(turns.length, 1);
  assert.deepEqual(turns[0].body, { token: "sealed:cycle", confirmed: true });
  assert.match($("lo-fw-out").textContent, /Looked at 12 tasks/);
  assert.match($("lo-fw-out").textContent, /1 safeguards, 1 habits and 1 suggestions/);
  assert.match($("lo-fw-out").textContent, /never repeats the cycle/);
  assert.ok(ui.calls.some((c) => c.url.endsWith("/flywheel/original/" + OPERATION)), "the original is read back");
});

test("practice rounds show the spending limit in the confirmation and the kernel's refusal", async (t) => {
  const ui = await fixture(LEARNING(), [learning]);
  t.after(() => ui.window.close());
  ui.doc.getElementById("lo-sm-objective").value = "make summaries faster";
  ui.doc.getElementById("lo-sm-form").dispatchEvent(new ui.window.Event("submit", { cancelable: true }));
  await ui.settle();
  assert.match(ui.calls.find((c) => c.confirm).confirm, /Nothing is installed\. It can spend up to \$4\.00/);
  const exact = { objective: "make summaries faster", cycles: 1, context: CONTEXT, revision: "r".repeat(64) };
  const posts = ui.calls.filter((c) => c.method === "POST");
  assert.deepEqual(posts.map((c) => c.url), ["/api/v1/learning-ops/self-modify/review", "/api/v1/learning-ops/self-modify/run"]);
  assert.deepEqual(posts[0].body, exact);
  // The run carries the reviewed objective and its sealed review, only after the confirmation.
  assert.deepEqual(posts[1].body, { ...exact, token: "sealed:practice", confirm: true });
  assert.ok(ui.calls.findIndex((c) => c.confirm) < ui.calls.indexOf(posts[1]));
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

test("agent-building suggestions are chosen one at a time before any comparison", async (t) => {
  // Improve how new agents are built: current suggestions, one chosen with the
  // agents it was reported for, and nothing compared without consent.
  const catalog = { context: CONTEXT, revision: "rev-f", model: "Claude X", enabled: true, requests: [],
    notice: "Suggestions come from recorded corrections.",
    corrections: [{ key: "k1", guidance: "Add a deny list.", why: "Seen in 3 agents.", supported: true, packs: ["invoice_clerk", "ap_bot"] },
      { key: "k2", guidance: "Rarely seen idea.", why: "Seen in 1 agent.", supported: false, packs: [] }] };
  const ui = await fixture(LEARNING(), [learning], { answers: { "/api/v1/learning-ops/factory/catalog": catalog } });
  t.after(() => ui.window.close());
  const $ = (id) => ui.doc.getElementById(id);
  $("lo-fa-preview").click();
  await ui.settle();
  assert.ok(ui.calls.some((c) => (c.url || "").startsWith("/api/v1/learning-ops/factory/catalog?min_support=")));
  assert.match($("lo-fa-status").textContent, /Suggestions come from recorded corrections\..*Claude X/);
  const choices = Array.from($("lo-fa-suggestion").options);
  assert.deepEqual(choices.map((o) => o.value), ["", "k1", "k2"]);
  assert.equal(choices[2].disabled, true, "an unsupported suggestion cannot be chosen");
  $("lo-fa-suggestion").value = "k1";
  $("lo-fa-suggestion").dispatchEvent(new ui.window.Event("change"));
  assert.equal($("lo-fa-suggestion-help").textContent, "Add a deny list. Seen in 3 agents.");
  assert.deepEqual(Array.from($("lo-fa-requests").querySelectorAll("label")).map((l) => l.textContent.trim()), ["invoice clerk", "ap bot"]);
  assert.equal($("lo-fa-compare").disabled, true, "no comparison without consent");
  $("lo-fa-consent").checked = true;
  $("lo-fa-consent").dispatchEvent(new ui.window.Event("input", { bubbles: true }));
  assert.equal($("lo-fa-compare").disabled, false);
  assert.equal(ui.calls.some((c) => c.method === "POST"), false, "choosing sends nothing");
});

test("learned page tools: a reviewed meaning check and a previewed import", async (t) => {
  const ui = await fixture(LEARNED(), [learned]);
  t.after(() => ui.window.close());
  // Lesson transfers moved to their own reviewed page (learning_transfers);
  // this panel no longer offers the old unreviewed transfer picker.
  assert.equal(ui.doc.getElementById("lpo-transfer-source"), null);
  ui.doc.getElementById("lpo-clash-run").click();
  await ui.settle();
  const review = ui.doc.getElementById("lpo-clash-review");
  assert.match(review.textContent, /Review this exact meaning check.*never cite.*Spending limit\$5\.00/s);
  const start = Array.from(review.querySelectorAll("button")).find((b) => /Start reviewed meaning check/.test(b.textContent));
  assert.equal(start.disabled, true, "no AI call before the review is ticked");
  const tick = review.querySelector('input[type="checkbox"]');
  tick.checked = true;
  tick.dispatchEvent(new ui.window.Event("change"));
  start.click();
  await ui.settle();
  const started = ui.calls.filter((c) => c.method === "POST" && c.url === "/api/v1/learning-ops/self-harness/clash-check");
  assert.equal(started.length, 1);
  assert.deepEqual(started[0].body, { context: CONTEXT, token: "sealed:check", confirmed: true });
  assert.match(ui.doc.getElementById("lpo-clash-out").textContent, /1 possible conflicts flagged by AI/);
  assert.match(ui.doc.getElementById("lpo-clash-out").textContent, /These ask for opposite things\./);

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
  // Test cases are set up in the Learning questions section on this page.
  assert.match(ui.doc.getElementById("lpo-plan").textContent,
    /Test cases are not set up yet\. Use Learning questions below to choose Lightwork’s managed location\./);
  const raw = (name) => fs.readFileSync(path.join(templates, name), "utf8");
  assert.match(raw("_learned_ops.html"), /\{% include "_learning_test_cases\.html" %\}/);
  assert.match(raw("_learning_test_cases.html"), /<h3 id="learning-case-title">Learning questions<\/h3>/);
});

test("the hold-out record is initialized once, only after its review is ticked", async (t) => {
  const ui = await fixture(partial("_learning_holdout_setup.html"), [holdout]);
  t.after(() => ui.window.close());
  const $ = (id) => ui.doc.getElementById("learning-holdout-" + id);
  assert.match($("status").textContent, /Initialize the record once/);
  assert.equal($("initialize").hidden, false);
  $("initialize").click();
  await ui.settle();
  assert.match($("review").textContent, /Review one-time initialization.*never replaced/s);
  const apply = Array.from($("review").querySelectorAll("button")).find((b) => /Initialize reviewed record/.test(b.textContent));
  assert.equal(apply.disabled, true);
  const tick = $("review").querySelector('input[type="checkbox"]');
  tick.checked = true;
  tick.dispatchEvent(new ui.window.Event("change"));
  apply.click();
  await ui.settle();
  const saves = ui.calls.filter((c) => c.url === "/api/v1/learning-holdout-setup/save");
  assert.equal(saves.length, 1);
  assert.deepEqual(saves[0].body, { token: "sealed:holdout", confirmed: true });
  assert.match($("result").textContent, /The record was initialized\./);
});
