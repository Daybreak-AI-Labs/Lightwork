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
    [{ status: 429 }, /Too many requests/], [{ status: 503 }, /not available right now/],
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

test("stored values become readable labels", () => {
  const w = load();
  assert.equal(w.mvHuman("pending_review"), "Pending review");
  assert.equal(w.mvHuman("ai_act"), "AI act");
  assert.equal(w.mvHuman("notice_sha256"), "Notice fingerprint");
  assert.equal(w.mvHuman("gdpr-dsar"), "GDPR DSAR");
  assert.equal(w.mvHuman(""), "");
  assert.equal(w.mvHuman(null), "");
});
