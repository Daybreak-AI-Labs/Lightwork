"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {JSDOM} = require("jsdom");

const source = fs.readFileSync(path.resolve(__dirname,
  "../../maverick_dashboard/static/lightwork-product-operations.js"), "utf8");
const turn = () => new Promise(resolve => setImmediate(resolve));
const reply = (value, ok = true, status = 200) => ({ok, status, json: async () => value});

function setup(operationFields) {
  const dom = new JSDOM('<aside id="mv-copilot"><section id="mv-product-operations" hidden>' +
    '<p id="mv-product-operation-status"></p><div id="mv-product-operation-setup" hidden></div>' +
    '<input id="mv-product-operation-search"><div id="mv-product-operation-empty" hidden>' +
    '<button id="mv-product-operation-search-all"></button></div>' +
    '<div id="mv-product-operation-buttons"></div><div id="mv-product-operation-form"></div></section></aside>',
  {url: "http://testserver/chat"});
  dom.window.HTMLFormElement.prototype.reportValidity = () => true;
  const calls = [];
  let catalogReads = 0, reviewReads = 0;
  const operation = {id: "tasks.start", label: "Start a new task", available: true,
    revision: "current-task-settings", source: "Current task settings", summary: "Start a task",
    direct_ui: "/chat", confirmation_required: true, fields: operationFields || [
      {name: "title", label: "What should Lightwork do?", kind: "text", required: true, value: ""},
      {name: "max_dollars", label: "Maximum dollars", kind: "number", required: false, value: 0.1},
    ]};
  const fetch = async (url, options) => {
    const body = JSON.parse(options.body || "{}"); calls.push({url, body});
    if (url.endsWith("/catalog")) return reply({schema: 1, selection: {}, context: "c".repeat(64),
      checked_at: 1, actions_enabled: true, operations: [{...operation, revision: `current-${++catalogReads}`} ]});
    if (url.endsWith("/prepare")) return reply({operation: operation.id, read_only: false,
      token: `review-${++reviewReads}`, expires_at: Date.now() / 1000 + 600,
      summary: "Review this task", source: "Current task settings", checked_at: 1,
      changes: []});
    if (url.endsWith("/execute")) return reply({detail: {code: "task_not_started",
      message: "Nothing was started because access changed after the review."}}, false, 409);
    throw new Error(`Unexpected request: ${url}`);
  };
  vm.runInNewContext(source, {window: dom.window, document: dom.window.document,
    CustomEvent: dom.window.CustomEvent, URL: dom.window.URL, URLSearchParams,
    AbortController, setTimeout, clearTimeout, fetch});
  return {dom, calls};
}

test("a definite pre-start refusal offers a fresh review and preserves task details", async () => {
  const ui = setup(), {document, window} = ui.dom.window;
  window.LightworkProductOperations.open(); await turn(); await turn();
  document.querySelector("#mv-product-operation-buttons button").click(); await turn(); await turn();

  const formHost = document.querySelector("#mv-product-operation-form");
  formHost.querySelector('[name="title"]').value = "Prepare the customer report";
  formHost.querySelector('[name="max_dollars"]').value = "0.37";
  formHost.querySelector("form button[type=submit]").click(); await turn(); await turn();
  assert.deepEqual(ui.calls.find(call => call.url.endsWith("/prepare")).body.arguments,
    {title: "Prepare the customer report", max_dollars: 0.37});

  const consent = formHost.querySelector('input[type="checkbox"]'); consent.checked = true;
  consent.dispatchEvent(new window.Event("change"));
  [...formHost.querySelectorAll("button")].find(button => button.textContent === "Apply reviewed change").click();
  await turn(); await turn();
  assert.equal(ui.calls.filter(call => call.url.endsWith("/execute")).length, 1);
  assert.match(document.querySelector("#mv-product-operation-status").textContent, /Nothing was started/);
  assert.match(formHost.textContent, /access changed after the review/);

  [...formHost.querySelectorAll("button")].find(button => button.textContent === "Review current task options").click();
  await turn(); await turn();
  assert.equal(formHost.querySelector('[name="title"]').value, "Prepare the customer report");
  assert.equal(formHost.querySelector('[name="max_dollars"]').value, "0.37");
  assert.equal(ui.calls.filter(call => call.url.endsWith("/execute")).length, 1,
    "the refreshed review still requires the person to confirm again");

  formHost.querySelector("form button[type=submit]").click(); await turn(); await turn();
  assert.deepEqual(ui.calls.filter(call => call.url.endsWith("/prepare"))[1].body.arguments,
    {title: "Prepare the customer report", max_dollars: 0.37});
  assert.equal(ui.calls.filter(call => call.url.endsWith("/catalog")).length, 3,
    "the retry reads current settings before preparing the new review");
  ui.dom.window.close();
});

test("repeatable Copilot fields collect optional order details as typed values", async () => {
  const ui = setup([
    {name: "title", label: "Review name", kind: "text", required: true},
    {name: "additional_orders", label: "Additional accepted orders (optional)", kind: "repeatable",
      item_label: "Accepted order", add_label: "Add another accepted order", max_items: 30,
      fields: [
        {name: "name", label: "Order name", kind: "text", required: true},
        {name: "due_at", label: "Delivery date and time", kind: "datetime-local", required: true},
        {name: "material_units", label: "Material units needed", kind: "number", required: true, min: 1, max: 1000000},
        {name: "capacity_units", label: "Shared work-center units needed", kind: "number", required: true, min: 0, max: 1000000},
      ]},
  ]);
  const {document, window} = ui.dom.window;
  window.LightworkProductOperations.open(); await turn(); await turn();
  document.querySelector("#mv-product-operation-buttons button").click(); await turn(); await turn();
  const host = document.querySelector("#mv-product-operation-form");
  host.querySelector('[name="title"]').value = "Quarterly customer commitments";
  host.querySelector('[type="button"]').click();
  const order = host.querySelectorAll('fieldset')[1];
  order.querySelector('[name="additional_orders_0_name"]').value = "Gamma";
  order.querySelector('[name="additional_orders_0_due_at"]').value = "2030-06-15T16:30";
  order.querySelector('[name="additional_orders_0_material_units"]').value = "7";
  order.querySelector('[name="additional_orders_0_capacity_units"]').value = "12";
  host.querySelector('form button[type="submit"]').click(); await turn(); await turn();
  const args = ui.calls.find(call => call.url.endsWith("/prepare")).body.arguments;
  assert.equal(args.title, "Quarterly customer commitments");
  assert.deepEqual(args.additional_orders, [{name:"Gamma", due_at:new Date("2030-06-15T16:30").toISOString(),
    material_units:7, capacity_units:12}]);
  ui.dom.window.close();
});
