// Run with: node --test packages/maverick-dashboard/tests/js/lightwork_ui_plain_language.test.js
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const source = fs.readFileSync(
  path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-ui.js"), "utf8");

function load() {
  const dom = new JSDOM('<div id="mv-toasts"></div><div id="mv-toasts-alert"></div>',
    { url: "https://lightwork.test/", runScripts: "outside-only" });
  dom.window.eval(source);
  return dom.window;
}

test("request failures read as plain sentences, never codes or exception names", () => {
  const w = load();
  const cases = [
    [{ status: 400 }, /details need fixing/], [{ status: 422 }, /details need fixing/],
    [{ status: 401 }, /sign-in has expired/], [{ status: 403 }, /do not have permission/],
    [{ status: 404 }, /no longer exists/], [{ status: 409 }, /changed since you opened it/],
    [{ status: 429 }, /Too many requests/], [{ status: 503 }, /temporarily unavailable\. Please try again in a few minutes/],
    [{ status: 500 }, /went wrong on our side/], [new TypeError("Failed to fetch"), /could not be reached/],
    [{ name: "AbortError" }, /took too long/], [new Error("SyntaxError: Unexpected token"), /went wrong/],
  ];
  for (const [failure, expected] of cases) {
    const text = w.mvErrorText(failure, "save the policy");
    assert.match(text, /^Could not save the policy\. /);
    assert.match(text, expected);
    assert.doesNotMatch(text, /\b\d{3}\b|HTTP|Error|Syntax/);
  }
  assert.equal(w.mvErrorText({ status: 404 }), "That item no longer exists. Refresh the page to see the latest.");
  const shown = w.mvFail({ status: 403 }, "delete it");
  assert.match(w.document.getElementById("mv-toasts-alert").textContent, /Could not delete it/);
  assert.match(shown, /permission/);
});

test("a 503 says turned off only when the server said so", () => {
  const w = load();
  assert.match(w.mvErrorText({ status: 503 }, "load it"), /temporarily unavailable/);
  assert.doesNotMatch(w.mvErrorText({ status: 503 }, "load it"), /turned off|turned on|set up/);
  assert.match(w.mvErrorText({ status: 503, featureOff: true }, "load it"), /turned off\. An administrator can turn it on in Settings/);
  assert.match(w.mvErrorText({ status: 503, detail: "Code projects are turned off." }, "load it"), /turned off/);
});

test("server messages pass only when they are already plain sentences", () => {
  const w = load();
  assert.equal(w.mvPlainDetail("Pick a later date."), "Pick a later date.");
  assert.equal(w.mvPlainDetail("Shorten the goal to 10,000 characters or fewer. Move additional instructions to Add details."),
    "Shorten the goal to 10,000 characters or fewer. Move additional instructions to Add details.");
  for (const raw of ["privacy_ops is not enabled", "select an active tenant", "Body must be a JSON object.",
    "The expected_revision is stale.", "Request failed (error 500).", "Path /api/v1/x was not found.",
    "An authenticated principal is required.", { detail: "x" }, null]) {
    assert.equal(w.mvPlainDetail(raw), "", String(raw));
  }
});

test("stored values become readable labels", () => {
  const w = load();
  assert.equal(w.mvHuman("pending_review"), "Pending review");
  assert.equal(w.mvHuman("ai_act"), "AI act");
  assert.equal(w.mvHuman("notice_sha256"), "Notice fingerprint");
  assert.equal(w.mvHuman("gdpr-dsar"), "GDPR DSAR");
  assert.equal(w.mvHuman(""), "");
  assert.equal(w.mvHuman(null), "");
});

function goalForm(reply) {
  const dom = new JSDOM('<div id="mv-toasts"></div><div id="mv-toasts-alert"></div>' +
    '<form action="/chat/send" method="post"><select name="authoring_kind"><option value="goal">Task</option></select>' +
    '<input name="title" value="Summarise the week"><textarea name="description"></textarea><button type="submit">Start</button></form>' +
    '<p id="goal-error" style="display:none"></p>',
    { url: "https://lightwork.test/", runScripts: "outside-only" });
  dom.window.fetch = async (url) => (String(url).includes("/chat/send") ? reply() : { ok: false, status: 404, json: async () => ({}) });
  dom.window.eval(source);
  return dom.window;
}
async function submitGoal(w) {
  w.document.querySelector('form[action="/chat/send"]').dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0));
  return w.document.getElementById("goal-error").textContent;
}

test("starting a task explains failures in plain sentences", async () => {
  const cases = [
    [() => ({ ok: false, status: 500, headers: { get: () => null }, json: async () => ({ detail: "Traceback (most recent call last)" }) }),
      /^Could not start the task\. Something went wrong on our side/],
    [() => ({ ok: false, status: 429, headers: { get: (h) => (h === "Retry-After" ? "30" : null) }, json: async () => ({ detail: "rate limit: 5/min" }) }),
      /^Too many requests right now\. Try again in 30 seconds\.$/],
    [() => ({ ok: false, status: 400, headers: { get: () => null }, json: async () => ({ detail: "The selected AI model route is incomplete (anthropic: ANTHROPIC_API_KEY). An administrator can run 'maverick init'." }) }),
      /An AI model needs to be set up first\. Ask an administrator to finish setup in Settings\./],
    [() => ({ ok: false, status: 422, headers: { get: () => null }, json: async () => ({ detail: "Shorten the goal to 10,000 characters or fewer. Move additional instructions to Add details." }) }),
      /^Shorten the goal to 10,000 characters/],
    [() => { throw new TypeError("Failed to fetch"); }, /^Lightwork is not responding\. Check your connection and try again\.$/],
  ];
  for (const [reply, expected] of cases) {
    const w = goalForm(reply);
    const text = await submitGoal(w);
    assert.match(text, expected);
    assert.doesNotMatch(text, /\(\d{3}\)|error \d|retry in|dashboard still running|Traceback|maverick init|ANTHROPIC/);
    w.close();
  }
});
