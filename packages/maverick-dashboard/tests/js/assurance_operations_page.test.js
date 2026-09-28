// The /security/assurance governance tools: people pick records and fill in
// fields; the page sends exact requests and never asks for typed data,
// references, version numbers or fingerprints.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const base = path.join(__dirname, "../../maverick_dashboard");
const D = "d".repeat(64);
const plain = value => JSON.parse(JSON.stringify(value));
const BANNED = /json|sha-?256|digest|principal|revision|idempotency|tenant|endpoint|schema|uuid|ed25519/i;

function pageHtml() {
  let html = fs.readFileSync(path.join(base, "templates/_governance_operations.html"), "utf8");
  html = html.replace(/\{% macro[\s\S]*?\{% endmacro %\}/g, "");
  html = html.replace(/\{\{ asset_options\([^)]*\) \}\}/g,
    `<option value="">Choose an AI system</option><option value="MAA-1" data-revision="3" data-digest="${D}" data-version="2.1">Support bot</option>`);
  html = html.replace(/\{\{ version_select\('([^']*)', '([^']*)', '([^']*)'\) \}\}/g,
    (_, label, kind, empty) => `<label>${label}<select class="select" name="version" data-version-options="${kind}"><option value="">${empty}</option></select></label>`);
  html = html.replace("{{ MRA_NAMES|default({}, true)|tojson }}", JSON.stringify({"MAA-1": "Support bot"}));
  html = html.replace("{{ 'true' if gateway_admin else 'false' }}", "true");
  html = html.replace(/\{% else %\}[\s\S]*?\{% endif %\}/g, "");
  html = html.replace(/\{%[\s\S]*?%\}/g, "").replace(/\{\{[\s\S]*?\}\}/g, "");
  return html;
}

async function fixture(t, routes = {}) {
  const html = pageHtml();
  const inline = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const dom = new JSDOM(`<!doctype html><body>${html.replace(/<script[\s\S]*?<\/script>/g, "")}</body>`,
    {runScripts: "outside-only", url: "https://lightwork.test/security/assurance"});
  t.after(() => dom.window.close());
  const w = dom.window;
  Object.defineProperty(w, "crypto", {value: globalThis.crypto, configurable: true});
  w.TextEncoder = TextEncoder;
  w.mvHuman = value => { const text = String(value ?? "").replace(/[_-]+/g, " ").trim(); return text ? text[0].toUpperCase() + text.slice(1) : ""; };
  w.URL.createObjectURL = () => "blob:test"; w.URL.revokeObjectURL = () => {};
  w.HTMLAnchorElement.prototype.click = () => {};
  const confirms = [];
  let confirmAnswer = true;
  w.mvConfirm = async message => { confirms.push(message); return confirmAnswer; };
  const calls = [];
  w.fetch = async (url, options = {}) => {
    const call = {url: String(url).replace("/api/v1/security/assurance", ""), method: options.method || "GET",
      body: typeof options.body === "string" ? JSON.parse(options.body) : options.body};
    calls.push(call);
    const match = Object.keys(routes).find(key => {
      const [method, route] = key.includes(" ") ? key.split(" ") : ["GET", key];
      return method === call.method && call.url === route;
    });
    const handler = match ? routes[match] : {};
    const value = typeof handler === "function" ? handler(call) : handler;
    if (value instanceof Response) return value;
    return new Response(JSON.stringify(value), {status: 200, headers: {"Content-Type": "application/json"}});
  };
  w.eval(fs.readFileSync(path.join(base, "static/lightwork-assurance-forms.js"), "utf8"));
  w.eval(inline);
  const settle = async () => { for (let i = 0; i < 30; i += 1) await new Promise(resolve => setImmediate(resolve)); };
  await settle();
  const $ = selector => w.document.querySelector(selector);
  const byId = id => w.document.getElementById(id);
  const open = label => { const button = w.document.querySelector(`button[aria-label="Open ${label}"]`); assert.ok(button, `no row for ${label}`); button.click(); };
  const change = (element, value) => { element.value = value; element.dispatchEvent(new w.Event("change", {bubbles: true})); };
  const submit = form => form.dispatchEvent(new w.Event("submit", {cancelable: true, bubbles: true}));
  const field = (root, text) => {
    const label = Array.from(root.querySelectorAll("label")).find(item => item.textContent.trim().startsWith(text));
    assert.ok(label, `no field labelled ${text}`);
    return label.querySelector("input,select,textarea");
  };
  const posted = route => calls.filter(call => call.method !== "GET" && call.url === route);
  const statuses = () => Array.from(w.document.querySelectorAll("[role=status]")).map(node => node.textContent).join("\n");
  return {w, $, byId, open, change, submit, field, settle, calls, posted, confirms, statuses,
    setConfirm: value => { confirmAnswer = value; }};
}

const policies = {policies: [{policy_id: "customer-data", status: "active", active_version: 1, revision: 4, versions: [
  {version: 1, title: "Customer data rules", description: "", statements: [{statement_id: "external-pii", text: "Keep personal data internal", effect: "restrict",
    applicability: {risk_tiers: [], jurisdictions: [], data_classes: ["personal"], deployment_types: [], asset_types: [], tags: [], use_case_tags: []}, required_control_ids: []}]},
  {version: 2, title: "Customer data rules, tighter", description: "", statements: [{statement_id: "external-pii", text: "Keep personal data internal", effect: "prohibit",
    applicability: {risk_tiers: [], jurisdictions: [], data_classes: [], deployment_types: [], asset_types: [], tags: [], use_case_tags: []}, required_control_ids: []}]}
]}]};

test("choosing rules offers their versions and rules, and turning one on asks first in plain words", async t => {
  const h = await fixture(t, {"/ai-policy-management/policies": policies, "POST /ai-policy-management/policies/customer-data/activate": {policy_id: "customer-data"}});
  h.open("Customer data rules, tighter");
  await h.settle();
  const activate = h.byId("ai-policy-activate");
  const versions = Array.from(activate.elements.version.options).map(option => option.textContent);
  assert.deepEqual(versions, ["Choose a version", "Version 2: Customer data rules, tighter", "Version 1: Customer data rules (in use)"]);
  assert.equal(h.$('[data-structured="policy-version-statements"] textarea').value, "Keep personal data internal", "the new-version editor starts from the saved wording");
  h.setConfirm(false);
  h.change(activate.elements.version, "2");
  h.submit(activate); await h.settle();
  assert.equal(h.posted("/ai-policy-management/policies/customer-data/activate").length, 0, "nothing is turned on without confirmation");
  assert.doesNotMatch(h.confirms[0], BANNED);
  h.setConfirm(true);
  h.submit(activate); await h.settle();
  assert.deepEqual(plain(h.posted("/ai-policy-management/policies/customer-data/activate")[0].body), {version: 2, expected_revision: 4});
});

test("an exception is requested from checkboxes, with names made from plain words", async t => {
  const h = await fixture(t, {"/ai-policy-management/policies": policies, "POST /ai-policy-management/exceptions": {}});
  h.open("Customer data rules, tighter");
  await h.settle();
  const form = h.byId("ai-policy-exception-request");
  form.elements.exception_key.value = "Holiday support pilot";
  form.elements.expires_at.value = "2026-12-31T17:00";
  const rule = Array.from(form.querySelectorAll("label")).find(label => label.textContent.trim() === "Keep personal data internal").querySelector("input");
  rule.checked = true;
  form.elements.reason.value = "Seasonal demand";
  form.elements.compensating_control_ids.value = "Manual review, traffic cap";
  h.submit(form); await h.settle();
  const body = plain(h.posted("/ai-policy-management/exceptions")[0].body);
  assert.equal(body.exception_key, "holiday-support-pilot");
  assert.equal(body.policy_version, 1);
  assert.deepEqual(body.scope, {asset_ids: [], statement_ids: ["external-pii"], risk_tiers: []});
  assert.deepEqual(body.compensating_control_ids, ["manual-review", "traffic-cap"]);
  assert.equal(typeof body.expires_at, "number");
});

test("checking a use against the rules is built from pickers and a refusal is one plain sentence", async t => {
  const h = await fixture(t, {"POST /ai-policy-management/preview": () => new Response('{"detail":"evaluation context: asset_id must be a token"}', {status: 422})});
  const form = h.byId("ai-policy-preview");
  h.change(h.field(form, "AI system"), "MAA-1");
  h.submit(form); await h.settle();
  assert.deepEqual(plain(h.posted("/ai-policy-management/preview")[0].body.context).asset_id, "MAA-1");
  const status = h.byId("ai-policy-preview-status").textContent;
  assert.match(status, /Some of the details need fixing/);
  assert.doesNotMatch(status, /asset_id|token|context/);
});

test("records sent to ServiceNow are chosen from a list and sending asks first", async t => {
  const source = {source_kind: "finding", source_id: "MRF-1", asset_id: "MAA-1", severity: "high", summary: "No accountable owner", source_revision: 1, source_digest: D, finding_type: "undeclared_asset"};
  const h = await fixture(t, {
    "/pickers/grc-sources?source_kind=finding": {sources: [{label: "No accountable owner", asset_id: "MAA-1", source}]},
    "POST /grc-outbound/servicenow_irm/preview": {status: "previewed"},
    "POST /grc-outbound/servicenow_irm/sync": {status: "delivered"}
  });
  const form = h.byId("grc-outbound-preview");
  const options = Array.from(form.elements.source_choice.options).map(option => option.textContent);
  assert.deepEqual(options, ["Choose a record", "Support bot: No accountable owner (High)"]);
  h.change(form.elements.source_choice, "0");
  h.submit(form); await h.settle();
  assert.deepEqual(plain(h.posted("/grc-outbound/servicenow_irm/preview")[0].body), source);
  h.setConfirm(false);
  h.byId("grc-outbound-sync").click(); await h.settle();
  assert.equal(h.posted("/grc-outbound/servicenow_irm/sync").length, 0);
  h.setConfirm(true);
  h.byId("grc-outbound-sync").click(); await h.settle();
  assert.equal(h.posted("/grc-outbound/servicenow_irm/sync").length, 1);
  assert.match(h.byId("grc-outbound-sync-status").textContent, /Delivered/);
});

test("an insurer profile cites only approved evidence of the chosen AI system", async t => {
  const h = await fixture(t, {
    "/pickers/package-forms": {insurability: {measurement_schema: "m1", measurements: ["evaluation"], measurement_units: ["percent"],
      framework_postures: ["implemented"], safeguard_states: ["planned"], severities: ["none"]}, regulated: {nyc_checks: [], naic_checks: []},
      bank_mrm: {section_schema: "s1", sections: []}, conformity: {checklist_sections: {fria: ["scope"]}}},
    "/model-risk/evidence?asset_id=MAA-1": {evidence: [{id: "MRE-1", status: "approved", freshness: "current", result: "passed", payload_sha256: D, evidence_kind: "evaluation"}]},
    "POST /ai-insurability/profiles": {profile_id: "support-2026"}
  });
  const form = h.byId("ai-insurability-create");
  form.elements.profile_id.value = "Support 2026";
  h.change(form.elements.asset_id, "MAA-1"); await h.settle();
  h.field(form, "Framework name").value = "NIST AI RMF";
  h.field(form, "Accountable role").value = "Risk lead";
  form.querySelector("input[type=checkbox][value='MRE-1']").checked = true;
  h.field(form, "Safeguard name").value = "Human review";
  h.field(form, "What this profile does not cover").value = "Voice";
  h.field(form, "Known limitations").value = "Small sample";
  h.field(form, "Steps to follow").value = "Open the report";
  h.submit(form); await h.settle();
  const body = plain(h.posted("/ai-insurability/profiles")[0].body);
  assert.equal(body.profile_id, "support-2026");
  assert.equal(body.asset_id, "MAA-1");
  assert.deepEqual(body.evidence_bindings, [{evidence_id: "MRE-1", digest: D}]);
  assert.deepEqual(body.content.governance_posture.frameworks[0].evidence_ids, ["MRE-1"]);
});

test("agent identity controls use chosen identities and ask before changing Purview", async t => {
  const principal = "prn_" + "1".repeat(32);
  const h = await fixture(t, {
    "/pickers/identity-controls": {available: true, capabilities: ["agent.execute", "purview.classification.write"],
      federation_bindings: [{provider: "okta", principal_ref: principal, provider_subject_id: "sub_" + "2".repeat(32), binding_sha256: D, authority_revision: 2}],
      purview_assets: [], purview_intents: [{intent_id: "IGPW-" + "3".repeat(32), principal_ref: principal, capability_mapping_sha256: D, revision: 5,
        status: "prepared", classification_codes: ["confidential"], created_at: 1800000000}]},
    "POST /identity-governance/controls/capabilities/okta/decision": {decision: {action: "permit"}}
  });
  const workbench = h.byId("ai-identity-control-workbench");
  workbench.open = true; workbench.dispatchEvent(new h.w.Event("toggle")); await h.settle();
  const evaluate = h.byId("ai-identity-control-evaluate");
  assert.equal(evaluate.elements.identity_choice.options[1].textContent, "Agent identity 1 (Okta)");
  h.change(evaluate.elements.identity_choice, "0");
  h.change(evaluate.elements.required_capability, "purview.classification.write");
  h.byId("ai-identity-capability-decision").click(); await h.settle();
  const body = plain(h.posted("/identity-governance/controls/capabilities/okta/decision")[0].body);
  assert.equal(body.principal_ref, principal);
  assert.equal(body.expected_binding_sha256, D);
  assert.equal(body.expected_revision, 2);
  assert.equal(body.required_capability, "purview.classification.write");
  assert.match(h.byId("ai-identity-control-evaluate-status").textContent, /Decision: allowed/);
  const resume = h.byId("ai-identity-purview-resume");
  h.change(resume.elements.intent_choice, "0");
  h.setConfirm(false);
  h.byId("ai-identity-purview-execute").click(); await h.settle();
  const route = "/identity-governance/controls/purview/writebacks/IGPW-" + "3".repeat(32) + "/execute";
  assert.equal(h.posted(route).length, 0, "nothing changes in Purview without confirmation");
  assert.match(h.confirms.at(-1), /Microsoft Purview/);
});

test("a sensor sighting carries the chosen sensor's setup and a repeatable fingerprint", async t => {
  const h = await fixture(t, {
    "/ai-discovery-sensors/sensors?limit=100": {sensors: [{sensor_id: "DSN-" + "4".repeat(32), sensor_name: "office-browsers", sensor_type: "browser_extension",
      sensor_revision: 2, config_sha256: D, status: "active", revision: 7}]},
    ["POST /ai-discovery-sensors/sensors/DSN-" + "4".repeat(32) + "/observations"]: {observation_id: "DSO-1"}
  });
  h.open("Office browsers");
  await h.settle();
  const form = h.byId("ai-discovery-observation-ingest");
  h.field(form, "When it was seen").value = "2026-05-01T09:30";
  h.field(form, "AI service").value = "Chat assistant";
  h.field(form, "Product").value = "Team chat";
  h.field(form, "Provider").value = "Example Vendor";
  h.submit(form); await h.settle();
  const body = plain(h.posted("/ai-discovery-sensors/sensors/DSN-" + "4".repeat(32) + "/observations")[0].body);
  assert.equal(body.sensor_revision, 2);
  assert.equal(body.config_sha256, D);
  assert.match(body.event_sha256, /^[0-9a-f]{64}$/);
  assert.equal(body.ai_service_code, "chat-assistant");
  assert.equal(body.provider_code, "example-vendor");
  assert.deepEqual(body.context_codes, []);
});

test("messages the page writes are plain sentences", async t => {
  const h = await fixture(t, {"/ai-policy-management/policies": policies});
  const text = h.statuses();
  assert.doesNotMatch(text, BANNED);
  assert.doesNotMatch(text, /bounded|projection|control-plane|digest-only|manifest/i);
  assert.equal(h.w.document.querySelectorAll("textarea[name=statements], textarea[name=context], textarea[name=receipt], textarea[name=evidence_bindings], textarea[name=source], textarea[name=observation], textarea[name=report], textarea[name=details]").length, 0);
});
