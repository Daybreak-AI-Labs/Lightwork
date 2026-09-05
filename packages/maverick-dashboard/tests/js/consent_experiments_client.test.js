// Run with: node --test packages/maverick-dashboard/tests/js/consent_experiments_client.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function fixture(overrides = {}) {
  const values = {
    experiment_id: "banner-copy-v1", metric_id: "preference-completion",
    variants: '[{"variant_id":"control"},{"variant_id":"treatment"}]',
    audience: '{"country_codes":["US"]}', starts_at: "100", ends_at: "200",
    traffic_bps: "10000", max_exposures: "1000", min_sample_per_variant: "100",
    alpha: "0.05", max_analysis_looks: "5", minimum_effect: "0",
    dedupe_retention_days: "30", idempotency_key: "create-test-key", ...overrides,
  };
  const form = { elements: Object.fromEntries(Object.entries(values).map(([name, value]) => [name, { value }])) };
  const nodes = new Map();
  const requests = [];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      textContent: "No operation run.", listeners: {},
      elements: { experiment_id: { value: "" }, expected_revision: { value: "" } },
      addEventListener(type, fn) { this.listeners[type] = fn; },
    });
    return nodes.get(id);
  }
  const context = {
    document: { getElementById: node },
    async fetch(url, options) {
      requests.push({ url, payload: JSON.parse(options.body) });
      return { ok: true, status: 201, async json() { return { revision: 3 }; } };
    },
  };
  const template = fs.readFileSync(path.resolve(__dirname,
    "../../maverick_dashboard/templates/consent_experiments.html"), "utf8");
  const scripts = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.equal(scripts.length, 1);
  vm.runInNewContext(scripts[0][1], context);
  return {
    form, requests, node,
    submit() { return node("ce-create").listeners.submit({ currentTarget: form, preventDefault() {} }); },
  };
}

for (const [name, label] of [["variants", "Variants"], ["audience", "Audience"]]) {
  test(`invalid ${name} JSON produces an actionable message without a request or rejected event handler`, async () => {
    const ui = fixture({ [name]: "{ unfinished" });
    await assert.doesNotReject(ui.submit());
    assert.equal(ui.requests.length, 0);
    assert.equal(ui.node("ce-status").textContent, `${label} must be valid JSON.`);
    assert.equal(ui.form.elements[name].value, "{ unfinished");
    assert.equal(ui.node("ce-lifecycle").elements.expected_revision.value, "");
  });
}

test("a corrected JSON draft can be submitted and populates the lifecycle revision", async () => {
  const ui = fixture({ audience: "{ unfinished" });
  await assert.doesNotReject(ui.submit());
  ui.form.elements.audience.value = '{"country_codes":["US"]}';
  await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.deepEqual(ui.requests[0].payload.audience, { country_codes: ["US"] });
  assert.equal(ui.node("ce-lifecycle").elements.expected_revision.value, 3);
  assert.equal(ui.node("ce-status").textContent, "Immutable experiment draft created.");
});

test("an empty optional audience is omitted while the parsed variants are sent", async () => {
  const ui = fixture({ audience: "  " });
  await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.equal("audience" in ui.requests[0].payload, false);
  assert.equal(ui.requests[0].payload.variants.length, 2);
});
