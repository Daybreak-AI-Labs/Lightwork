// Run with: node --test packages/maverick-dashboard/tests/js/ai_cloud_agent_governance.test.js
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const base = path.join(__dirname, "../../maverick_dashboard");
const template = fs.readFileSync(path.join(base, "templates/ai_cloud_agent_governance.html"), "utf8");
const source = fs.readFileSync(path.join(base, "static/lightwork-ai-cloud-agent-governance.js"), "utf8");
const shell = template.slice(template.indexOf('<div class="cag-shell"'), template.indexOf("<script src"))
  .replace(/\{\{[^}]*\}\}/g, "true");

const AGENT = "cag_" + "a".repeat(32);
const TOOL = "cat_" + "b".repeat(32);
const POLICY = { policy_ref: "pol_" + "c".repeat(32), policy_revision: 4, policy_sha256: "d".repeat(64), title: "Stop risky tools" };
const EVAL = "cpe_" + "e".repeat(32);
const INTENT = "cei_" + "f".repeat(32);

function fixture(t, overrides = {}) {
  const dom = new JSDOM(`<main>${shell}</main>`, { url: "https://lightwork.test/assurance/cloud-agents", runScripts: "outside-only" });
  t.after(() => dom.window.close());
  const w = dom.window;
  const calls = [];
  let confirmAnswer = true;
  w.mvConfirm = async () => confirmAnswer;
  w.mvHuman = (v) => String(v).replace(/[_.]+/g, " ").replace(/^./, (c) => c.toUpperCase());
  const routes = {
    "GET /status": { enabled: true, provider_bindings: [{ provider: "azure_ai_foundry", bound: true, active: true }], databricks_framework_enabled: false },
    "GET /inventory?limit=500": { providers: [{ provider: "azure_ai_foundry", metadata: { snapshot_complete: true }, records: [{ provider: "azure_ai_foundry", agent_ref: AGENT, record_revision: 3, inventory_revision: 9, lifecycle_state: "active", first_seen_at: 1800000000, last_seen_at: 1800000100, drifted: false, tool_refs: [TOOL], model_refs: [], data_source_refs: [] }] }] },
    "GET /policies": { items: [POLICY] },
    "GET /policy-evaluations": { items: [{ evaluation_ref: EVAL, agent_ref: AGENT, requested_action: "restrict_tool", target_ref: TOOL, decision: "allow_intent", reason_codes: ["policy.approved"], evaluated_at: 1800000200 }] },
    "GET /enforcement-intents": { items: [{ intent_ref: INTENT, evaluation_ref: "cpe_" + "0".repeat(32), provider: "azure_ai_foundry", agent_ref: AGENT, action: "disable_agent", target_ref: AGENT, status: "prepared", revision: 2, attempts: 0 }] },
    ...overrides,
  };
  w.fetch = async (url, options = {}) => {
    const route = String(url).replace("/api/v1/assurance/cloud-agents", "");
    const key = (options.method || "GET") + " " + route;
    calls.push({ key, body: options.body ? JSON.parse(options.body) : undefined });
    const value = routes[key];
    if (typeof value === "function") return value();
    if (value === undefined) return new Response(JSON.stringify({ decision: "allow_intent", status: "dispatching" }), { status: 200 });
    return new Response(JSON.stringify(value), { status: 200 });
  };
  w.eval(source);
  const settle = async () => { for (let i = 0; i < 30; i += 1) await new Promise((r) => setImmediate(r)); };
  return { w, calls, settle, setConfirm: (v) => { confirmAnswer = v; }, $: (id) => w.document.getElementById(id) };
}

test("agents, checks and changes load on open with row buttons and readable labels", async (t) => {
  const h = fixture(t);
  await h.settle();
  const agents = h.$("cag-agents").textContent;
  assert.match(agents, /Azure AI Foundry agent 1/);
  assert.match(agents, /Running/);
  assert.match(h.$("cag-checks").textContent, /Stop it using a tool · Tool 1/);
  assert.match(h.$("cag-checks").textContent, /Allowed/);
  assert.match(h.$("cag-changes").textContent, /Ready to send/);
  const text = h.$("cag-root").textContent;
  assert.doesNotMatch(text, /cag_|cat_|cpe_|cei_|allow_intent|restrict_tool|HTTP/);
  assert.ok([...h.$("cag-changes").querySelectorAll("button")].some((b) => b.textContent === "Send to provider"));
});

test("a policy check carries the chosen agent and policy pins, never typed values", async (t) => {
  const h = fixture(t);
  await h.settle();
  const check = [...h.$("cag-agents").querySelectorAll("button")].find((b) => b.textContent === "Check against a policy");
  check.click();
  await h.settle();
  assert.equal(h.$("cag-check-panel").hidden, false);
  assert.equal(h.$("cag-check-policy").options[0].textContent, "Stop risky tools");
  h.$("cag-check-action").value = "restrict_tool";
  h.$("cag-check-action").dispatchEvent(new h.w.Event("change"));
  assert.equal(h.$("cag-check-target").options[0].textContent, "Tool 1");
  h.$("cag-check-form").elements.idempotency_key.value = "ui-reference-0001";
  h.$("cag-check-form").dispatchEvent(new h.w.Event("submit", { cancelable: true }));
  await h.settle();
  const post = h.calls.find((c) => c.key === "POST /policy-evaluations");
  assert.deepEqual(post.body, {
    agent_ref: AGENT, expected_agent_revision: 3, policy_ref: POLICY.policy_ref,
    policy_revision: 4, policy_sha256: POLICY.policy_sha256, requested_action: "restrict_tool",
    target_ref: TOOL, idempotency_key: "ui-reference-0001",
  });
  assert.match(h.$("cag-result").textContent, /Policy check finished: Allowed/);
});

test("sending a change needs confirmation and carries the row's version", async (t) => {
  const h = fixture(t);
  await h.settle();
  h.setConfirm(false);
  const send = () => [...h.$("cag-changes").querySelectorAll("button")].find((b) => b.textContent === "Send to provider");
  send().click();
  await h.settle();
  assert.equal(h.calls.some((c) => c.key.includes("/dispatch")), false);
  h.setConfirm(true);
  send().click();
  await h.settle();
  const post = h.calls.find((c) => c.key === `POST /enforcement-intents/${INTENT}/dispatch`);
  assert.equal(post.body.expected_revision, 2);
  assert.match(post.body.idempotency_key, /^ui-/);
});

test("server refusals are plain sentences, never the raw server code", async (t) => {
  const h = fixture(t, {
    "GET /enforcement-intents": () => new Response(JSON.stringify({ detail: "cloud_agent_governance_state_conflict" }), { status: 409 }),
  });
  await h.settle();
  const text = h.$("cag-changes").textContent;
  assert.match(text, /changed since you opened it|Something went wrong/);
  assert.doesNotMatch(text, /cloud_agent|409/);
});

test("a reply that is not valid fails closed without showing its body", async (t) => {
  const h = fixture(t, { "GET /inventory?limit=500": () => new Response("<html>secret body</html>", { status: 200 }) });
  await h.settle();
  const text = h.$("cag-agents").textContent;
  assert.match(text, /returned something unexpected/);
  assert.doesNotMatch(text, /secret body/);
});
