"use strict";

// Added for the companion preview. Execution is deferred at the user's request.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const CONTEXT = "employee-workspace-context";
const script = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-employee-companion.js"), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const policy = overrides => ({owner: "user:employee@example.test", revision: 3, enabled: true,
  allowed_sources: ["own_tasks"], allowed_tools: ["task_status"], retention_days: 0, ...overrides});
const snapshot = overrides => ({context: CONTEXT, available: true, policy: policy(), history: [], tasks: [], ...overrides});
const reply = overrides => ({context: CONTEXT, policy: policy(), history: [],
  message: {role: "assistant", content: "One task is running.", sources: []}, ...overrides});
const adminSnapshot = overrides => ({context: CONTEXT, available: true,
  employees: [{principal: "user:employee@example.test", label: "Employee One"}],
  policies: [policy()], events: [], ...overrides});
const response = (payload, status = 200) => ({ok: status >= 200 && status < 300, status,
  text: async () => typeof payload === "string" ? payload : JSON.stringify(payload)});

async function setup(queue, admin = false) {
  const filename = admin ? "employee_companion_admin.html" : "employee_companion.html";
  const template = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/templates", filename), "utf8")
    .replace(/{%[\s\S]*?%}/g, "")
    .replace(/{{ companion_context \| default\(''\) }}/g, CONTEXT)
    .replace(/{{[\s\S]*?}}/g, "");
  const dom = new JSDOM(template, {url: "https://lightwork.test/companion" + (admin ? "/admin" : ""), runScripts: "outside-only"});
  const calls = [];
  dom.window.fetch = async (url, options) => {
    calls.push({url, options});
    const next = queue.shift();
    if (next instanceof Error) throw next;
    if (typeof next === "function") return next(url, options);
    if (next === undefined) throw new Error("Unexpected request");
    return response(next);
  };
  dom.window.eval(script);
  await tick();
  return {dom, window: dom.window, document: dom.window.document, calls,
    el: id => dom.window.document.getElementById(id)};
}
function submit(ui, id) {
  ui.el(id).dispatchEvent(new ui.window.Event("submit", {bubbles: true, cancelable: true}));
}

test("unsaved conversations show both turns safely and bind requests to the rendered account", async t => {
  const ui = await setup([snapshot(), reply({message: {role: "assistant", content: "<img src=x onerror=alert(1)>", sources: [
    {goal_id: 8, href: "javascript:alert(1)", status: "active", checked_at: 1700000000},
    {goal_id: 9, href: "/chat/goal/9", status: "active", checked_at: 1700000000}
  ]}})]);
  t.after(() => ui.dom.window.close());
  ui.el("ec-message").value = "What is running?";
  submit(ui, "ec-message-form");
  await tick();
  assert.match(ui.el("ec-messages").textContent, /What is running\?/);
  assert.match(ui.el("ec-messages").textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(ui.el("ec-messages").querySelector("img"), null);
  assert.equal(ui.el("ec-messages").querySelectorAll("a").length, 1);
  assert.equal(ui.el("ec-messages").querySelector("a").getAttribute("href"), "/chat/goal/9");
  assert.equal(ui.calls[0].options.headers["X-Lightwork-Companion-Context"], CONTEXT);
  assert.equal(ui.calls[1].options.headers["X-Lightwork-Companion-Context"], CONTEXT);
  assert.equal(ui.calls[1].options.credentials, "same-origin");
  assert.deepEqual(JSON.parse(ui.calls[1].options.body), {message: "What is running?", expected_revision: 3});
  assert.equal(ui.el("ec-message").value, "");
  assert.equal(ui.window.localStorage.length, 0);
  assert.equal(ui.el("ec-message-form").closest('[data-draft-recovery="off"]'), ui.el("ec-root"));
});

test("retained answers display only their newly authorized source links", async t => {
  const saved = policy({retention_days: 7});
  const ui = await setup([snapshot({policy: saved}), reply({policy: saved, history: [
    {role: "user", content: "What is running?"}, {role: "assistant", content: "One task is running.", sources: []}
  ], message: {role: "assistant", content: "One task is running.", sources: [
    {goal_id: 2, href: "/chat/goal/2", status: "active", checked_at: 1700000000}
  ]}})]);
  t.after(() => ui.dom.window.close());
  ui.el("ec-message").value = "What is running?";
  submit(ui, "ec-message-form");
  await tick();
  assert.equal(ui.el("ec-messages").querySelector("a").getAttribute("href"), "/chat/goal/2");
});

test("lost access clears private messages and drafts even when denial is an HTML response", async t => {
  const ui = await setup([snapshot({history: [{role: "user", content: "Private question"}]}),
    () => response("<h1>Sign in again</h1>", 401)]);
  t.after(() => ui.dom.window.close());
  ui.el("ec-message").value = "Unsent private question";
  ui.el("ec-refresh").click();
  await tick();
  assert.equal(ui.el("ec-messages").textContent, "");
  assert.equal(ui.el("ec-message").value, "");
  assert.equal(ui.el("ec-message").disabled, true);
  assert.equal(ui.el("ec-refresh").disabled, true);
});

test("changed workspace is terminal and never displays the other workspace response", async t => {
  const ui = await setup([snapshot(), () => response({detail: {code: "companion_context_changed"}}, 409)]);
  t.after(() => ui.dom.window.close());
  ui.el("ec-message").value = "What is running?";
  submit(ui, "ec-message-form");
  await tick();
  assert.equal(ui.el("ec-message").value, "");
  assert.equal(ui.el("ec-send").disabled, true);
  assert.equal(ui.el("ec-refresh").disabled, true);
  assert.match(ui.el("ec-status").textContent, /account, workspace or access changed/);
});

test("failed requests preserve questions and suppress duplicate submissions", async t => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const ui = await setup([snapshot(), () => pending]);
  t.after(() => ui.dom.window.close());
  ui.el("ec-message").value = "What is running?";
  submit(ui, "ec-message-form");
  submit(ui, "ec-message-form");
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.el("ec-send").disabled, true);
  release(response({detail: "Temporary outage"}, 503));
  await tick();
  assert.equal(ui.el("ec-message").value, "What is running?");
  assert.equal(ui.el("ec-send").disabled, false);
  assert.match(ui.el("ec-status").textContent, /could not confirm/);
});

test("clearing history is possible when disabled and removes transient page messages", async t => {
  const ui = await setup([snapshot({available: false, policy: policy({enabled: false}),
    history: [{role: "user", content: "Previous question"}]}), {context: CONTEXT, history: []}]);
  t.after(() => ui.dom.window.close());
  assert.equal(ui.el("ec-send").disabled, true);
  assert.equal(ui.el("ec-clear").disabled, false);
  ui.el("ec-clear").click();
  assert.equal(ui.document.activeElement, ui.el("ec-clear-no"));
  ui.el("ec-clear-yes").click();
  await tick();
  assert.doesNotMatch(ui.el("ec-messages").textContent, /Previous question/);
  assert.equal(ui.calls[1].options.method, "DELETE");
  assert.equal(ui.calls[1].options.headers["X-Lightwork-Companion-Context"], CONTEXT);
  assert.equal(ui.document.activeElement, ui.el("ec-clear"));
});

test("admin CAS conflict preserves choices and requires explicit reload", async t => {
  const ui = await setup([adminSnapshot(), () => response({detail: "Policy changed"}, 409), adminSnapshot({policies: [policy({revision: 4})]})], true);
  t.after(() => ui.dom.window.close());
  ui.el("ec-owner").value = "user:employee@example.test";
  ui.el("ec-owner").dispatchEvent(new ui.window.Event("change"));
  ui.el("ec-retention-days").value = "7";
  submit(ui, "ec-policy-form");
  await tick();
  assert.equal(ui.el("ec-retention-days").value, "7");
  assert.equal(ui.el("ec-save").disabled, true);
  assert.equal(JSON.parse(ui.calls[1].options.body).expected_revision, 3);
  submit(ui, "ec-policy-form");
  assert.equal(ui.calls.length, 2);
  ui.el("ec-refresh").click();
  await tick();
  assert.equal(ui.el("ec-revision").value, "4");
  assert.equal(ui.el("ec-retention-days").value, "0");
  assert.equal(ui.el("ec-save").disabled, false);
});

test("admin activity only renders approved metadata fields", async t => {
  const ui = await setup([adminSnapshot({events: [{owner: "user:employee@example.test",
    actor: "user:employee@example.test", action: "reply", time: 1700000000,
    message: "Private employee question", response: "Private companion answer", title: "Private task title"}]})], true);
  t.after(() => ui.dom.window.close());
  assert.match(ui.el("ec-events").textContent, /Companion question answered/);
  assert.doesNotMatch(ui.el("ec-events").textContent, /Private/);
  assert.equal(ui.el("ec-policy-form").closest('[data-draft-recovery="off"]'), ui.el("ec-root"));
});
