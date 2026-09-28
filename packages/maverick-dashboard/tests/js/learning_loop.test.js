"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const source = fs.readFileSync(
  path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-learning-loop.js"), "utf8");

function page(admin) {
  return `<div id="llp-root" data-admin="${admin ? 1 : 0}">
<p id="llp-ref-state"></p><table><tbody id="llp-ref-rows"></tbody></table><ul id="llp-in-force"></ul>
${admin ? `<details id="llp-propose-box"><form id="llp-propose">
<select name="target"><option value="prompt">Standing instructions</option></select>
<input name="title"><textarea name="failure"></textarea><textarea name="change"></textarea><textarea name="rationale"></textarea>
<button type="submit">Suggest this change</button></form></details>` : ""}
<p id="llp-ref-msg"></p>
<select id="llp-model"></select>
<table><tbody id="llp-eff-rows"></tbody></table><ul id="llp-conf"></ul>
<button id="llp-preview-btn">Show</button><div id="llp-preview"></div>
<ol id="llp-log"></ol><button id="llp-holdout-btn">Check</button><div id="llp-holdout"></div>
${admin ? `<select id="llp-scope"><option value="main">Main</option><option value="all">All</option></select>
<button id="llp-run">Run</button><button id="llp-quality-btn">Quality</button>` : ""}
<div id="llp-run-result"></div><p id="llp-harness-msg"></p></div>`;
}

const PENDING = { id: "0123456789abcdef", status: "pending", target: "Standing instructions", title: "Check quote approval",
  failure: "Emailed too early", change: "Confirm the quote first", rationale: "Three blocked tasks",
  approval: "Approved", can_apply: true, can_revert: false, proposed_at: 1700000000,
  technical: { reference: "0123456789abcdef", approval_number: 7, fingerprint: "abcdef0123456789" } };
const APPLIED = Object.assign({}, PENDING, { id: "fedcba9876543210", status: "applied", can_apply: false, can_revert: true, applied_at: 1700000100 });

function defaults() {
  return {
    "/refinements": { enabled: true, require_approval: true, proposals: [PENDING, APPLIED],
      in_force: [{ target: "Standing instructions", title: "Check quote approval", change: "Confirm the quote first", applied_at: 1700000100 }] },
    "/models": { enabled: true, models: [{ value: "claude-x", label: "Claude X" }] },
    "/efficacy": { lessons: [{ text: "always cite", department: "", went_well: 3, did_not: 1, rate: 0.75, recent_rate: null }] },
    "/conflicts": { pairs: [{ model: "claude-x", first: "always cite", second: "never cite" }] },
    "/log": { events: [{ when: 1700000000, what: "Learned a new lesson", model: "claude-x", lesson: "always cite" }] },
    "/preview": { enabled: true, weaknesses: [{ pattern: "forgot totals", times: 4, kind: "wrong answer", would_add: "check totals" }], scanned: 9, eligible: 5 },
    "/holdout": { configured: true, verified: true, events: 12, checks_used: 3, technical: { ledger: "L1", latest_fingerprint: "ab" } },
    "/run": { reports: [{ patterns_found: 2, ideas_tried: 1, passed_testing: 1, adopted: 1, retired: 0,
      paused_for_calibration: false, promotion_gate_off: false, new_lessons: ["check totals"] }] },
  };
}

async function fixture({ admin = true, answers = {}, confirm = true } = {}) {
  const dom = new JSDOM(page(admin), { url: "https://lightwork.example/learned", runScripts: "outside-only" });
  const { window } = dom;
  const calls = [];
  const table = Object.assign(defaults(), answers);
  window.mvErrorText = (failure, action) => `Could not ${action}.`;
  window.mvHuman = (v) => String(v).replace(/_/g, " ");
  window.mvWhen = () => "14 Nov 2023";
  window.mvConfirm = async (message) => { calls.push({ confirm: message }); return confirm; };
  window.fetch = async (url, init = {}) => {
    const method = init.method || "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(init.body) : null });
    const key = Object.keys(table).sort((a, b) => b.length - a.length)
      .find((k) => url.replace("/api/v1/learning-loop", "").split("?")[0] === k);
    let answer = table[key];
    if (method === "POST" && !key) answer = {};
    const status = answer && answer.__status ? answer.__status : 200;
    return new Response(JSON.stringify(answer && answer.__body ? answer.__body : answer || {}),
      { status, headers: { "Content-Type": "application/json" } });
  };
  const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((done) => setImmediate(done)); };
  window.eval(source);
  await settle();
  return { window, doc: window.document, calls, settle };
}

test("suggested changes list in plain words with apply and undo for admins", async (t) => {
  const ui = await fixture();
  t.after(() => ui.window.close());
  const rows = ui.doc.getElementById("llp-ref-rows");
  assert.match(rows.textContent, /Check quote approval/);
  assert.match(rows.textContent, /Emailed too early/);
  assert.match(rows.textContent, /In use since 14 Nov 2023/);
  const labels = Array.from(rows.querySelectorAll("button")).map((b) => b.textContent);
  assert.deepEqual(labels, ["Apply", "Undo"]);
  // The reference number only appears inside the collapsed technical details.
  const tech = rows.querySelector("details");
  assert.equal(tech.querySelector("summary").textContent, "Technical details");
  assert.match(ui.doc.getElementById("llp-in-force").textContent, /Confirm the quote first/);
});

test("viewers get no buttons", async (t) => {
  const ui = await fixture({ admin: false });
  t.after(() => ui.window.close());
  assert.equal(ui.doc.querySelectorAll("#llp-ref-rows button").length, 0);
});

test("apply confirms, then calls the apply route", async (t) => {
  const ui = await fixture();
  t.after(() => ui.window.close());
  ui.doc.querySelector("#llp-ref-rows button").click();
  await ui.settle();
  assert.ok(ui.calls.some((c) => c.confirm && /restore point/.test(c.confirm)));
  assert.ok(ui.calls.some((c) => c.method === "POST" && c.url === "/api/v1/learning-loop/refinements/0123456789abcdef/apply"));  // pragma: allowlist secret
  assert.match(ui.doc.getElementById("llp-ref-msg").textContent, /Change applied/);
});

test("declining the undo confirmation sends nothing", async (t) => {
  const ui = await fixture({ confirm: false });
  t.after(() => ui.window.close());
  ui.doc.querySelectorAll("#llp-ref-rows button")[1].click();
  await ui.settle();
  assert.equal(ui.calls.filter((c) => c.method === "POST").length, 0);
});

test("a refusal shows the server's plain sentence", async (t) => {
  const ui = await fixture({ answers: { "/refinements/0123456789abcdef/apply": { __status: 409,  // pragma: allowlist secret
    __body: { detail: "This change is still waiting for approval." } } } });
  t.after(() => ui.window.close());
  ui.doc.querySelector("#llp-ref-rows button").click();
  await ui.settle();
  assert.equal(ui.doc.getElementById("llp-ref-msg").textContent, "This change is still waiting for approval.");
});

test("suggesting a change sends the form fields", async (t) => {
  const ui = await fixture({ answers: { "/refinements_post": {} } });
  t.after(() => ui.window.close());
  const form = ui.doc.getElementById("llp-propose");
  form.querySelector("[name=title]").value = "Check totals";
  form.querySelector("[name=failure]").value = "Wrong totals";
  form.querySelector("[name=change]").value = "Add the totals twice";
  form.querySelector("[name=rationale]").value = "Totals were wrong";
  form.dispatchEvent(new ui.window.Event("submit", { cancelable: true }));
  await ui.settle();
  const sent = ui.calls.find((c) => c.method === "POST");
  assert.equal(sent.url, "/api/v1/learning-loop/refinements");
  assert.deepEqual(sent.body, { target: "prompt", title: "Check totals", failure: "Wrong totals",
    change: "Add the totals twice", rationale: "Totals were wrong" });
});

test("read-outs render lesson results, clashes and the activity timeline", async (t) => {
  const ui = await fixture();
  t.after(() => ui.window.close());
  assert.equal(ui.doc.getElementById("llp-model").value, "claude-x");
  assert.match(ui.doc.getElementById("llp-eff-rows").textContent, /always cite.*3.*1.*75%/);
  assert.match(ui.doc.getElementById("llp-conf").textContent, /may contradict/);
  assert.match(ui.doc.getElementById("llp-log").textContent, /14 Nov 2023: Learned a new lesson/);
});

test("preview and hold-out check explain results in sentences", async (t) => {
  const ui = await fixture();
  t.after(() => ui.window.close());
  ui.doc.getElementById("llp-preview-btn").click();
  ui.doc.getElementById("llp-holdout-btn").click();
  await ui.settle();
  assert.match(ui.doc.getElementById("llp-preview").textContent, /Seen 4 times: forgot totals/);
  assert.match(ui.doc.getElementById("llp-preview").textContent, /Nothing was changed/);
  assert.match(ui.doc.getElementById("llp-holdout").textContent, /complete and untouched: 12 entries/);
});

test("an auditor-only read-out explains who can see it", async (t) => {
  const ui = await fixture({ admin: false, answers: { "/log": { __status: 403, __body: {} } } });
  t.after(() => ui.window.close());
  assert.equal(ui.doc.getElementById("llp-log").textContent, "Only administrators and auditors can see the learning activity.");
});

test("run now confirms and summarises the pass", async (t) => {
  const ui = await fixture();
  t.after(() => ui.window.close());
  ui.doc.getElementById("llp-run").click();
  await ui.settle();
  const run = ui.calls.find((c) => c.method === "POST");
  assert.deepEqual(run.body, { scope: "main" });
  assert.match(ui.doc.getElementById("llp-run-result").textContent, /1 new lesson adopted/);
});
