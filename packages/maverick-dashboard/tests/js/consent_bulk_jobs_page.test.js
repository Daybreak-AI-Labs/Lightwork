// Run with: node --test packages/maverick-dashboard/tests/js/consent_bulk_jobs_page.test.js
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "../../maverick_dashboard");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");
const flush = async () => { for (let i = 0; i < 60; i++) { await new Promise(setImmediate); await new Promise((r) => setTimeout(r, 2)); } };

function reply(status, value) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let done = false;
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: (name) => (name.toLowerCase() === "content-length" ? String(bytes.byteLength) : null) },
    body: { getReader: () => ({ async read() { if (done) return { done: true }; done = true; return { done: false, value: bytes }; }, async cancel() {} }) },
  };
}

const JOBS = [{ job_id: "job-1", operation: "export_receipts", state: "queued", revision: 4, total_items: 2, processed_items: 0, failed_items: 0, cancel_requested: false, created_at: 1800000000 }];

async function page(operations) {
  const html = read("templates/consent_bulk_jobs.html").replace(/\{%[\s\S]*?%\}/g, "").replace(/\{\{[\s\S]*?\}\}/g, "");
  const dom = new JSDOM(html, { url: "https://lightwork.test/consent/bulk-jobs", runScripts: "outside-only" });
  const w = dom.window, d = w.document, calls = [], confirms = [];
  Object.defineProperty(w, "crypto", { value: crypto.webcrypto });
  w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder;
  const shell = d.getElementById("consent-bulk-jobs-shell");
  Object.assign(shell.dataset, { apiBase: "/api/v1/consent/bulk-jobs", canAudit: "true", canAdmin: "true" });
  d.querySelectorAll("button,fieldset").forEach((n) => { n.disabled = false; });
  w.mvConfirm = async (message) => { confirms.push(message); return true; };
  w.mvErrorText = (_f, action) => "Could not " + action + ".";
  w.fetch = async (url, init = {}) => {
    const method = init.method || "GET", body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url, method, body });
    if (url.endsWith("/bulk-jobs/status")) return reply(200, { enabled: true, ready: true, operations: operations || { export_receipts: true, edit_preferences: true, delete_subject: true, change_identifier_type: false, reactivate: false, clear_expiration: false } });
    if (url.startsWith("/api/v1/consent/bulk-jobs?")) return reply(200, { jobs: JOBS });
    if (url.endsWith("/credits/detail")) return reply(200, { available: 7, reserved: 1, consumed: 2, revision: 9 });
    if (url.startsWith("/api/v1/consent/profiles")) return reply(200, { profiles: [{ profile_id: "customer-1", identity_kind: "known", revision: 3 }, { profile_id: "customer-2", identity_kind: "anonymous", revision: 5 }] });
    if (url.startsWith("/api/v1/consent/policies")) return reply(200, { policies: [{ policy_id: "main", version: 2, purposes: { analytics: "consent", marketing: "consent" } }] });
    return reply(201, { job_id: "job-2", revision: 1 });
  };
  w.eval(read("static/lightwork-consent-workbench.js"));
  w.eval(read("static/lightwork-consent-bulk-jobs.js"));
  d.dispatchEvent(new w.Event("DOMContentLoaded"));
  await flush();
  const button = (label) => [...d.querySelectorAll("#cbj-job-list button")].find((b) => b.textContent === label);
  return { dom, w, d, calls, confirms, button, posts: () => calls.filter((c) => c.method === "POST") };
}

test("jobs load as rows and kinds of change that are not set up are disabled", async () => {
  const ui = await page();
  try {
    assert.equal(ui.d.getElementById("cbj-operations").disabled, false);
    assert.match(ui.d.getElementById("cbj-job-list").textContent, /Export consent records.*2.*0 of 2 done.*Waiting/);
    assert.match(ui.d.getElementById("cbj-credit-detail").textContent, /7 bulk changes can still be started/);
    assert.match(ui.d.getElementById("cbj-readiness-detail").textContent, /not set up yet: change how people are identified/);
    assert.equal(ui.d.querySelector("input[name=operation][value=reactivate]").disabled, true);
  } finally { ui.dom.window.close(); }
});

test("an export is built from ticked people, not typed items", async () => {
  const ui = await page();
  try {
    ui.d.getElementById("cbj-new").click();
    await flush();
    ui.d.querySelectorAll("#cbj-people input[name=person]").forEach((c) => { c.checked = true; c.dispatchEvent(new ui.w.Event("change")); });
    ui.d.getElementById("cbj-create").elements.receipt_limit.value = "50";
    ui.d.getElementById("cbj-create").dispatchEvent(new ui.w.Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    const sent = ui.posts().find((c) => c.url.endsWith("/bulk-jobs/exports"));
    assert.deepEqual(sent.body.items, [
      { item_id: "item-1", profile_id: "customer-1", receipt_limit: 50 },
      { item_id: "item-2", profile_id: "customer-2", receipt_limit: 50 },
    ]);
    assert.ok(sent.body.idempotency_key);
  } finally { ui.dom.window.close(); }
});

test("changing choices confirms and carries each person's version and the chosen policy", async () => {
  const ui = await page();
  try {
    ui.d.getElementById("cbj-new").click();
    await flush();
    const form = ui.d.getElementById("cbj-create");
    const radio = form.querySelector("input[name=operation][value=edit_preferences]");
    radio.checked = true; radio.dispatchEvent(new ui.w.Event("change"));
    const person = ui.d.querySelector("#cbj-people input[value=customer-2]");
    person.checked = true; person.dispatchEvent(new ui.w.Event("change"));
    form.elements.purpose_id.value = "marketing";
    form.elements.action.value = "deny";
    form.dispatchEvent(new ui.w.Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    assert.match(ui.confirms[0], /Change choices for 1 record\?/);
    const sent = ui.posts().find((c) => c.url.endsWith("/bulk-jobs/actions"));
    const item = sent.body.items[0];
    assert.equal(sent.body.operation, "edit_preferences");
    assert.equal(item.profile_id, "customer-2");
    assert.equal(item.expected_revision, 5);
    assert.equal(item.policy_id, "main");
    assert.equal(item.policy_version, 2);
    assert.equal(item.choices[0].purpose_id, "marketing");
    assert.equal(item.choices[0].action, "deny");
    assert.equal(item.context.asserted_source, "operator");
  } finally { ui.dom.window.close(); }
});

test("choosing nobody explains what to do instead of sending", async () => {
  const ui = await page();
  try {
    ui.d.getElementById("cbj-new").click();
    await flush();
    ui.d.getElementById("cbj-create").dispatchEvent(new ui.w.Event("submit", { bubbles: true, cancelable: true }));
    await flush();
    assert.equal(ui.posts().length, 0);
    assert.equal(ui.d.getElementById("cbj-operation-status").textContent, "Choose at least one person.");
  } finally { ui.dom.window.close(); }
});

test("cancelling a job confirms and carries its version", async () => {
  const ui = await page();
  try {
    ui.button("Cancel").click();
    await flush();
    assert.match(ui.confirms[0], /Changes already made are not undone/);
    const sent = ui.posts().find((c) => c.url.endsWith("/job-1/cancel"));
    assert.equal(sent.body.expected_revision, 4);
  } finally { ui.dom.window.close(); }
});
