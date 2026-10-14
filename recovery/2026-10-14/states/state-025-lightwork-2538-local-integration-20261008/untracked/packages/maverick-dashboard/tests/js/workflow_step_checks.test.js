"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const root = path.resolve(__dirname, "../../maverick_dashboard");
const script = fs.readFileSync(path.join(root, "static/lightwork-workflow-step-checks.js"), "utf8");
const settle = async () => { for (let index = 0; index < 12; index++) await new Promise(resolve => setImmediate(resolve)); };

test("one-step check reviews an optional expected answer and applies only after confirmation", async t => {
  const dom = new JSDOM(`<!doctype html><body>
    <section id="fd-selected-step-check" hidden><p id="fd-step-check-status"></p>
      <button id="fd-step-check-read" hidden>Read original</button><div id="fd-step-check-result"></div>
    </section></body>`, {url: "https://lightwork.test/flows/designer/sample", runScripts: "outside-only"});
  t.after(() => dom.window.close());
  const {window} = dom, calls = [];
  window.Element.prototype.scrollIntoView = function () {};
  window.__fdLiveFlow = () => ({id: "sample"});
  window.__fdEngine = {selected: () => "check-total"};
  window.mvForm = async (_title, fields) => {
    assert.equal(fields.at(-2).key, "compare_expected");
    assert.equal(fields.at(-1).key, "expected_answer");
    assert.equal(fields.at(-1).required, false);
    assert.equal(fields.at(-1).allowEmpty, true);
    return {amount: "7", compare_expected: true, expected_answer: "approved"};
  };
  window.mvFetchJSON = async (url, options) => {
    calls.push({url, options});
    if (url.endsWith("/inputs")) return {context: "a".repeat(64), label: "Check total",
      fields: {amount: {type: "integer", min: "0", max: "10"}}};
    if (url.endsWith("/review")) return {context: "a".repeat(64), id: "WSC-" + "b".repeat(32), token: "sealed",
      changes: [{label: "Selected taught step", after: "Check total"}]};
    if (url.endsWith("/apply")) return {context: "a".repeat(64), current: true,
      message: "The complete original check was retained. Nothing ran again.",
      result: {answered: true, label: "Answer", value: "approved", message: "Every retained rule alternative agreed on this answer.",
        assertion: {expected: "approved", matches: true, message: "The answer matches the value you entered."}}};
    throw new Error("Unexpected request: " + url);
  };
  window.eval(script);

  assert.equal(await window.LWWorkflowStepChecks.openSelected(), true);
  await settle();
  assert.deepEqual(calls.map(call => call.url), [
    "/api/v1/workflow-step-checks/inputs", "/api/v1/workflow-step-checks/review",
  ]);
  const review = calls[1].options.body;
  assert.equal(review.expected_answer, "approved");
  assert.deepEqual(JSON.parse(JSON.stringify(review.inputs)), {amount: "7"});
  assert.equal(calls.some(call => call.url.endsWith("/apply")), false, "opening the review does not run the step");

  const confirmation = window.document.querySelector("#fd-step-check-result input[type=checkbox]");
  const apply = window.document.querySelector("#fd-step-check-result button");
  assert.ok(confirmation && apply);
  confirmation.checked = true;
  confirmation.dispatchEvent(new window.Event("change"));
  assert.equal(apply.disabled, false);
  apply.click();
  await settle();

  assert.equal(calls.filter(call => call.url.endsWith("/apply")).length, 1);
  assert.match(window.document.getElementById("fd-step-check-result").textContent, /Your expected answer: «approved»/);
  assert.match(window.document.getElementById("fd-step-check-result").textContent, /matches the value you entered/);
  assert.match(window.document.getElementById("fd-step-check-result").textContent, /does not verify business truth/);
});
