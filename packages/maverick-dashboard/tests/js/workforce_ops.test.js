"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const read = (name) => fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/static/" + name), "utf8");
const jobs = read("lightwork-ops-jobs.js");
const workforce = read("lightwork-workforce-ops.js");

function partial(name) {
  return fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/templates/", name), "utf8")
    .replace(/\{#[\s\S]*?#\}/g, "").replace(/<script[\s\S]*?<\/script>/g, "");
}

function defaults(admin) {
  return {
    "/api/v1/value-proof/access": { admin: admin, audit: admin },
    "/api/v1/workforce-ops/inventory": { suites: [{ value: "finance", label: "Finance" }], specialists: 40, departments: 3,
      builders: 1, can_change_things_while_drafting: 1, sign_off_enforced: 30, sign_off_only_asked: 2, extra_refusals: 5,
      with_deliverable: 20, levels_enabled: true, independence: { observe: 1, suggest: 30, request: 8, auto: 1 }, still_supervised: 12,
      flagged: [{ specialist: "Invoice drafter", can_reach: ["send_payment"] }] },
    "/api/v1/workforce-ops/test-suite": { cases: 4, specialists: 3, problems: 0, technical: [],
      list: [{ specialist: "Payroll", task: "Run payroll", expects: ["refuses"] }] },
    "/api/v1/workforce-ops/test-suite/specialists": { specialists: [{ value: "finance_ap", label: "Accounts payable" }] },
    "/api/v1/workforce-ops/test-suite/customer": { live_enabled: true, cases: [{
      id: "a".repeat(32), specialist: "finance_ap", revision: 1, task: "Could these invoices be duplicates?",
      includes: ["duplicate"], excludes: ["payment sent"], refusal: false, citation: false
    }], runs: [] },
    "/api/v1/workforce-ops/test-suite/customer/run": { job: { id: "abcdef0123456789", state: "done", can_stop: false,
      result: { run: { state: "done", passed: 1, total: 1 } } } },
    "/api/v1/workforce-ops/setup-check": { job: { id: "0123456789abcdef", state: "done", can_stop: false, result: {  // pragma: allowlist secret
      specialists: 40, must_fix: 1, could_improve: 2, more: 0, findings: [{ specialist: "Invoice drafter",
        must_fix: ["It has no limit on how risky its actions may be."], could_improve: [], technical: ["max_risk is unset"] }] } } },
    "/api/v1/workforce-ops/upcoming": { days: 30, tasks: [
      { task: 7, title: "File the return", department: "finance_vat", due: 1700000000, overdue: true, questions: 1 },
      { task: 8, title: "Renew the contract", department: "", due: 1700500000, overdue: false, questions: 0 }] },
  };
}

async function fixture(markup, { admin = true, inventory = {}, responses = {} } = {}) {
  const dom = new JSDOM(markup, { url: "https://lightwork.example/workforce", runScripts: "outside-only" });
  const { window } = dom;
  const calls = [];
  const table = defaults(admin);
  Object.assign(table["/api/v1/workforce-ops/inventory"], inventory);
  Object.assign(table, responses);
  window.mvErrorText = (failure, action) => `Could not ${action}.`;
  window.mvPlainDetail = () => "";
  window.mvHuman = (v) => String(v).replace(/_/g, " ");
  window.mvWhen = () => "14 Nov 2023";
  window.mvToast = (m) => calls.push({ toast: m });
  window.mvConfirm = async (m) => { calls.push({ confirm: m }); return true; };
  window.fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", body: init.body });
    const path = url.split("?")[0];
    const answer = table[path] || (path.startsWith("/api/v1/workforce-ops/test-suite/customer/run/")
      ? table["/api/v1/workforce-ops/test-suite/customer/run/*"] : null);
    return new Response(JSON.stringify(answer || {}), { status: answer ? 200 : 404, headers: { "Content-Type": "application/json" } });
  };
  const settle = async () => { for (let i = 0; i < 60; i++) await new Promise((done) => setImmediate(done)); };
  window.eval(jobs);
  window.eval(workforce);
  await settle();
  return { window, doc: window.document, calls, settle };
}

test("the inventory reads plainly and flags specialists to review", async (t) => {
  const ui = await fixture(partial("_workforce_ops.html"));
  t.after(() => ui.window.close());
  const inv = ui.doc.getElementById("wo-inv").textContent;
  assert.match(inv, /40 specialists in 3 departments/);
  assert.match(inv, /1 specialist that should only draft can reach tools that change things/);
  assert.match(inv, /Invoice drafter.*send payment/);
  assert.equal(ui.doc.getElementById("wo-inv-suite").options.length, 2);
  assert.equal(ui.doc.getElementById("wo-lint-run").closest("section").hidden, false);
  assert.match(ui.doc.getElementById("wo-suite").textContent, /4 tests across 3 specialists\. Every test is complete\./);
});

test("the inventory lists the independence split only when the levels are on", async (t) => {
  const on = await fixture(partial("_workforce_ops.html"));
  t.after(() => on.window.close());
  assert.match(on.doc.getElementById("wo-inv").textContent, /Independence: Watch only 1, Suggest 30, Ask before acting 8, Act on their own 1\. 12 specialists still supervised/);

  const off = await fixture(partial("_workforce_ops.html"), { inventory: { levels_enabled: false } });
  t.after(() => off.window.close());
  const text = off.doc.getElementById("wo-inv").textContent;
  assert.match(text, /Independence levels are turned off, so every specialist follows the same approval rules\./);
  assert.doesNotMatch(text, /Suggest 30|Ask before acting 8|still supervised/);
});

test("the setup check runs as a job and lists what to fix", async (t) => {
  const ui = await fixture(partial("_workforce_ops.html"));
  t.after(() => ui.window.close());
  ui.doc.getElementById("wo-lint-run").click();
  await ui.settle();
  const out = ui.doc.getElementById("wo-lint-out").textContent;
  assert.match(out, /Checked 40 specialists: 1 problem to fix and 2 things that could be better/);
  assert.match(out, /It has no limit on how risky its actions may be/);
});

test("non-admins do not see the download or the setup check", async (t) => {
  const ui = await fixture(partial("_workforce_ops.html"), { admin: false });
  t.after(() => ui.window.close());
  assert.equal(ui.doc.getElementById("wo-inv-download").hidden, true);
  assert.equal(ui.doc.getElementById("wo-lint-run").closest("section").hidden, true);
  assert.equal(ui.doc.getElementById("wo-test-form").hidden, true);
});

test("customer can edit and run a saved specialist answer check from Workforce", async (t) => {
  const ui = await fixture(partial("_workforce_ops.html"));
  t.after(() => ui.window.close());
  const select = ui.doc.getElementById("wo-test-specialist");
  select.value = "finance_ap";
  select.dispatchEvent(new ui.window.Event("change"));
  await ui.settle();
  assert.match(ui.doc.getElementById("wo-tests-list").textContent, /Could these invoices be duplicates/);
  assert.equal(ui.doc.getElementById("wo-test-form").hidden, false);
  ui.doc.querySelector("#wo-tests-list button").click();
  assert.equal(ui.doc.getElementById("wo-test-task").value, "Could these invoices be duplicates?");
  ui.doc.getElementById("wo-test-form").dispatchEvent(new ui.window.Event("submit", {cancelable: true}));
  await ui.settle();
  const save = ui.calls.find((c) => c.url === "/api/v1/workforce-ops/test-suite/customer" && c.method === "POST");
  assert.ok(save);
  assert.equal(JSON.parse(save.body).expected_revision, 1);
  ui.doc.getElementById("wo-test-run").click();
  await ui.settle();
  const run = ui.calls.find((c) => c.url === "/api/v1/workforce-ops/test-suite/customer/run" && c.method === "POST");
  assert.match(JSON.parse(run.body).operation_id, /^[a-f0-9]{32}$/);
  assert.deepEqual(JSON.parse(run.body).case_ids, ["a".repeat(32)]);
  assert.deepEqual(JSON.parse(run.body).case_revisions, {["a".repeat(32)]: 1});
  assert.match(ui.doc.getElementById("wo-test-run-out").textContent, /1 of 1 answers passed/);
});

test("a larger saved set lets a person choose a bounded subset", async (t) => {
  const cases = Array.from({length: 6}, (_, i) => ({id: String(i).repeat(32), specialist: "finance_ap",
    revision: 1, task: "Check invoice example " + i, includes: ["duplicate"], excludes: [],
    refusal: false, citation: false}));
  const ui = await fixture(partial("_workforce_ops.html"), {responses: {
    "/api/v1/workforce-ops/test-suite/customer": {live_enabled: true, cases, runs: []}
  }});
  t.after(() => ui.window.close());
  const select = ui.doc.getElementById("wo-test-specialist");
  select.value = "finance_ap";
  select.dispatchEvent(new ui.window.Event("change"));
  await ui.settle();
  const boxes = ui.doc.querySelectorAll(".wo-test-choice");
  assert.equal(ui.doc.getElementById("wo-test-run").disabled, true);
  boxes[1].click(); boxes[3].click();
  assert.equal(ui.doc.getElementById("wo-test-run").disabled, false);
  ui.doc.getElementById("wo-test-run").click();
  await ui.settle();
  const run = ui.calls.find((c) => c.url === "/api/v1/workforce-ops/test-suite/customer/run" && c.method === "POST");
  assert.deepEqual(JSON.parse(run.body).case_ids, [cases[1].id, cases[3].id]);
  assert.deepEqual(JSON.parse(run.body).case_revisions, {[cases[1].id]: 1, [cases[3].id]: 1});
});

test("an uncertain paid check reads its original result without posting another request", async (t) => {
  const ui = await fixture(partial("_workforce_ops.html"), {responses: {
    "/api/v1/workforce-ops/test-suite/customer/run": {job: {id: "unknown-1", state: "done", can_stop: false,
      result: {run: {state: "unknown", passed: 0, total: 1, message: "Provider result uncertain."}}}},
    "/api/v1/workforce-ops/test-suite/customer/run/*": {run: {state: "unknown", passed: 0, total: 1,
      message: "Original result still uncertain."}}
  }});
  t.after(() => ui.window.close());
  const select = ui.doc.getElementById("wo-test-specialist");
  select.value = "finance_ap";
  select.dispatchEvent(new ui.window.Event("change"));
  await ui.settle();
  ui.doc.getElementById("wo-test-run").click();
  await ui.settle();
  ui.doc.getElementById("wo-test-run").click();
  await ui.settle();
  assert.equal(ui.calls.filter((c) => c.url === "/api/v1/workforce-ops/test-suite/customer/run" && c.method === "POST").length, 1);
  assert.ok(ui.calls.some((c) => c.url && c.url.startsWith("/api/v1/workforce-ops/test-suite/customer/run/") && c.method === "GET"));
  assert.match(ui.doc.getElementById("wo-test-run-status").textContent, /Original result still uncertain/);
});

test("tasks coming due list overdue first with links and waiting questions", async (t) => {
  const ui = await fixture(partial("_upcoming_tasks.html"));
  t.after(() => ui.window.close());
  const text = ui.doc.getElementById("wo-up").textContent;
  assert.match(text, /File the returnOverdue: 14 Nov 2023finance vat1 question/);
  assert.equal(ui.doc.querySelector("#wo-up a").getAttribute("href"), "/chat/goal/7");
  ui.doc.getElementById("wo-up-days").value = "7";
  ui.doc.getElementById("wo-up-days").dispatchEvent(new ui.window.Event("change"));
  await ui.settle();
  assert.ok(ui.calls.some((c) => c.url === "/api/v1/workforce-ops/upcoming?days=7"));
});

test("nothing coming due uses the shared empty state and offers a longer period", async (t) => {
  const markup = '<div id="mv-toasts"></div><div id="mv-toasts-alert"></div>' + partial("_upcoming_tasks.html");
  const dom = new JSDOM(markup, { url: "https://lightwork.example/goals", runScripts: "outside-only" });
  t.after(() => dom.window.close());
  const { window } = dom;
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.fetch = async () => new Response(JSON.stringify({ days: 30, tasks: [] }),
    { status: 200, headers: { "Content-Type": "application/json" } });
  window.eval(read("lightwork-ui.js"));
  window.eval(jobs);
  window.eval(workforce);
  const settle = async () => { for (let i = 0; i < 60; i++) await new Promise((done) => setImmediate(done)); };
  await settle();
  const empty = window.document.querySelector("#wo-up .mv-empty.mv-empty--compact");
  assert.ok(empty, "the shared empty state is shown");
  assert.equal(empty.querySelector(".mv-empty__title").textContent, "Nothing is due in the next 30 days");
  assert.match(empty.querySelector(".mv-empty__body").textContent, /Choose a longer period above/);
  window.document.getElementById("wo-up-days").value = "90";
  window.document.getElementById("wo-up-days").dispatchEvent(new window.Event("change"));
  await settle();
  assert.doesNotMatch(window.document.getElementById("wo-up").textContent, /longer period/);
});
