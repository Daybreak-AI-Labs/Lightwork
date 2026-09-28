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
      with_deliverable: 20, independence: { observe: 1, suggest: 30, request: 8, auto: 1 }, still_supervised: 12,
      flagged: [{ specialist: "Invoice drafter", can_reach: ["send_payment"] }] },
    "/api/v1/workforce-ops/test-suite": { cases: 4, specialists: 3, problems: 0, technical: [],
      list: [{ specialist: "Payroll", task: "Run payroll", expects: ["refuses"] }] },
    "/api/v1/workforce-ops/setup-check": { job: { id: "0123456789abcdef", state: "done", can_stop: false, result: {  // pragma: allowlist secret
      specialists: 40, must_fix: 1, could_improve: 2, more: 0, findings: [{ specialist: "Invoice drafter",
        must_fix: ["It has no limit on how risky its actions may be."], could_improve: [], technical: ["max_risk is unset"] }] } } },
    "/api/v1/workforce-ops/upcoming": { days: 30, tasks: [
      { task: 7, title: "File the return", department: "finance_vat", due: 1700000000, overdue: true, questions: 1 },
      { task: 8, title: "Renew the contract", department: "", due: 1700500000, overdue: false, questions: 0 }] },
  };
}

async function fixture(markup, { admin = true } = {}) {
  const dom = new JSDOM(markup, { url: "https://lightwork.example/workforce", runScripts: "outside-only" });
  const { window } = dom;
  const calls = [];
  const table = defaults(admin);
  window.mvErrorText = (failure, action) => `Could not ${action}.`;
  window.mvPlainDetail = () => "";
  window.mvHuman = (v) => String(v).replace(/_/g, " ");
  window.mvWhen = () => "14 Nov 2023";
  window.mvToast = (m) => calls.push({ toast: m });
  window.mvConfirm = async (m) => { calls.push({ confirm: m }); return true; };
  window.fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET" });
    const answer = table[url.split("?")[0]];
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
