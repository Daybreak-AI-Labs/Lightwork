"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");
const source = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/templates/automations.html"), "utf8").replace(/\r\n/g, "\n");

function fixture() {
  const nodes = {}, calls = [], storage = new Map();
  class Element {
    constructor(tag = "div") { this.tagName = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.value = ""; this.checked = false; this.disabled = false; this.valid = true; }
    set id(value) { this._id = value; nodes[value] = this; }
    get id() { return this._id; }
    set textContent(value) { this.text = value; this.children = []; }
    get textContent() { return (this.text || "") + this.children.map(child => child.textContent).join(""); }
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
    addEventListener(type, callback) { this.listeners[type] = callback; }
    setAttribute(name, value) { this[name] = value; }
    focus() { this.focused = true; }
    reportValidity() { return this.valid; }
    querySelector() { return this.submit || (this.submit = new Element("button")); }
    reset() { this.resets = (this.resets || 0) + 1; }
  }
  function get(id) { return nodes[id] || (nodes[id] = new Element()); }
  const document = { getElementById: get, createElement: tag => new Element(tag), createTextNode: text => { const e = new Element(); e.textContent = text; return e; },
    querySelectorAll: () => [] };
  const providers = { providers: [{ name: "github", label: "GitHub" }], connected: ["github"], status: {}, vault_enabled: true, connection_context: "context-a" };
  const reply = data => ({ ok: true, status: 200, json: async () => data });
  const context = { document, URL, Uint8Array, console, Date,
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (url === "/api/v1/oauth/providers") return reply(providers);
      if (url === "/api/v1/templates") return reply({ templates: [{ name: "triage", title: "Triage new requests", params: ["subject"] }] });
      if (url === "/api/v1/flows") return reply({ flows: [{ id: "draft", name: "Draft", published: false }, { id: "ready", name: "Ready flow", published: true }] });
      throw new Error("Unexpected request " + url);
    }, mvToast() {}, mvConfirm: async () => false, fmtWhen: () => "later", delBtn: () => new Element(),
    window: { location: { origin: "https://app.example", href: "https://app.example/automations", assign(url) { this.assigned = url; } },
      history: { replaceState(_, __, url) { this.cleaned = url; } }, crypto: webcrypto,
      sessionStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) } } };
  context.el = (tag, cls, text = "") => { const e = new Element(tag); e.className = cls; e.textContent = text; return e; };
  vm.createContext(context);
  return { context, get, nodes, calls, storage, providers, reply };
}
async function settle() { await new Promise(resolve => setImmediate(resolve)); }
function eventFixture() {
  const ui = fixture();
  ui.context.evForm = ui.get("auto-event-form");
  ui.get("auto-event-target-kind").value = "template";
  vm.runInContext(source.slice(source.indexOf("    var evList ="), source.indexOf("    async function loadEventTriggers()")), ui.context);
  ui.context.loadEventTriggers = () => {};
  vm.runInContext(source.slice(source.indexOf("    evForm.addEventListener('submit'"), source.indexOf("\n    loadEventTriggers();\n  }")), ui.context);
  return ui;
}

test("guided source fields preserve typed values, advanced options, and per-source drafts", async () => {
  const ui = eventFixture(); await settle();
  ui.get("auto-event-source").value = "file_dir"; ui.context.changeSource();
  ui.get("auto-event-config").value = '{"path":"inbound","recursive":false,"extension_setting":{"retain":true}}';
  ui.context.fillSourceFields();
  assert.equal(ui.get("auto-source-recursive").checked, false);
  ui.get("auto-source-recursive").checked = true; ui.get("auto-source-recursive").listeners.input();
  assert.deepEqual(JSON.parse(ui.get("auto-event-config").value), { path: "inbound", recursive: true, extension_setting: { retain: true } });
  ui.get("auto-event-source").value = "rss"; ui.context.changeSource();
  ui.get("auto-source-url").value = "https://example.org/feed.xml"; ui.get("auto-source-url").listeners.input();
  assert.equal(JSON.parse(ui.get("auto-event-config").value).url, "https://example.org/feed.xml");
  ui.get("auto-event-source").value = "file_dir"; ui.context.changeSource();
  assert.equal(ui.get("auto-source-recursive").checked, true);
  assert.equal(JSON.parse(ui.get("auto-event-config").value).extension_setting.retain, true);
  ui.get("auto-event-source").value = "imap_email"; ui.context.changeSource();
  ui.get("auto-source-port").value = "1993"; ui.get("auto-source-port").listeners.input();
  assert.equal(JSON.parse(ui.get("auto-event-config").value).port, 1993);
});

test("catalog picker offers only published flows and retains exact template parameter names", async () => {
  const ui = eventFixture(); await settle();
  assert.deepEqual(ui.get("auto-event-template").children.map(option => option.value), ["", "triage"]);
  ui.get("auto-event-template").value = "triage"; ui.context.renderTargetParams();
  assert.equal(ui.get("auto-event-param-0").dataset.param, "subject");
  ui.get("auto-event-target-kind").value = "flow"; ui.context.renderTargetChoices();
  assert.deepEqual(ui.get("auto-event-template").children.map(option => option.value), ["", "ready"]);
  assert.equal(ui.get("auto-event-params").children.length, 0);
});

for (const malformed of ["null", "[]", "{incomplete"]) {
  test("invalid advanced source " + malformed + " is revealed without creating an automation", async () => {
    const ui = eventFixture(); await settle();
    ui.get("auto-event-config").value = malformed;
    await ui.get("auto-event-form").listeners.submit({ preventDefault() {} });
    assert.equal(ui.get("auto-event-config-advanced").open, true);
    assert.equal(ui.get("auto-event-config").focused, true);
    assert.equal(ui.calls.some(call => call.options?.method === "POST"), false);
  });
}

test("guided create uses the selected flow contract and suppresses duplicate submissions", async () => {
  const ui = eventFixture(); await settle();
  ui.get("auto-event-source").value = "github_issues"; ui.context.changeSource();
  for (const [key, value] of [["owner", "example"], ["repo", "app"]]) {
    ui.get("auto-source-" + key).value = value; ui.get("auto-source-" + key).listeners.input();
  }
  ui.get("auto-event-target-kind").value = "flow"; ui.get("auto-event-template").value = "ready";
  let resolve, count = 0, body;
  ui.context.fetch = async (_, options) => { count++; body = JSON.parse(options.body); return await new Promise(done => { resolve = done; }); };
  const first = ui.get("auto-event-form").listeners.submit({ preventDefault() {} });
  await ui.get("auto-event-form").listeners.submit({ preventDefault() {} });
  assert.equal(count, 1); assert.equal(body.flow, "ready"); assert.equal("template" in body, false);
  assert.deepEqual(body.config, { owner: "example", repo: "app" });
  resolve(ui.reply({ name: "new-issues" })); await first;
  assert.equal(ui.get("auto-event-form").querySelector().disabled, false);
  assert.equal(ui.get("auto-event-form").resets, 1);
});

test("failed check never claims the source was checked and restores the action", async () => {
  const ui = eventFixture(); await settle();
  ui.context.fetch = async () => ({ ok: false, json: async () => ({ detail: "Access denied" }) });
  const button = ui.get("auto-event-poll"); await button.listeners.click.call(button);
  assert.equal(ui.get("auto-event-msg").textContent, "Access denied"); assert.equal(button.disabled, false);
});

function loadOAuth(ui) {
  const start = source.indexOf("  var oauthBox ="), end = source.indexOf("  // ---- flows (", start);
  vm.runInContext(source.slice(start, end), ui.context);
}

function importFixture() {
  const ui = fixture();
  ui.context.loadSchedules = () => {}; ui.context.loadTriggers = () => {};
  const original = ui.context.fetch;
  ui.context.fetch = (url, options) => url === "/api/v1/import/sources" ? Promise.resolve(ui.reply({ sources: [{ source: "n8n", mode: "fetch" }] })) : original(url, options);
  vm.runInContext(source.slice(source.indexOf("  var importForm ="), source.indexOf("  // ---- event triggers (")), ui.context);
  return ui;
}

test("uploading an export fills definitions without starting an import or changing activation choices", async () => {
  const ui = importFixture(); await settle();
  const file = ui.get("auto-import-file"); file.files = [{ size: 80, name: "workflows.json", text: async () => '[{"name":"Review","nodes":[]}]' }];
  await file.listeners.change();
  assert.deepEqual(JSON.parse(ui.get("auto-import-defs").value), [{ name: "Review", nodes: [] }]);
  assert.equal(ui.get("auto-import-webhooks").checked, false); assert.equal(ui.get("auto-import-schedules").checked, false);
  assert.equal(ui.calls.some(call => call.options?.method === "POST"), false);
});

test("an older file read cannot replace a newer manual edit", async () => {
  const ui = importFixture(); await settle(); let resolve;
  const file = ui.get("auto-import-file"); file.files = [{ size: 80, name: "old.json", text: () => new Promise(done => { resolve = done; }) }];
  const loading = file.listeners.change();
  ui.get("auto-import-defs").value = '{"name":"Newer draft"}'; ui.get("auto-import-defs").listeners.input();
  resolve('{"name":"Old file"}'); await loading;
  assert.equal(JSON.parse(ui.get("auto-import-defs").value).name, "Newer draft");
});

test("failed export parsing prevents an accidental live import and keeps earlier definitions", async () => {
  const ui = importFixture(); await settle();
  ui.get("auto-import-defs").value = '{"name":"Earlier draft"}';
  const file = ui.get("auto-import-file"); file.files = [{ size: 10, name: "broken.json", text: async () => "null" }];
  await file.listeners.change();
  await ui.get("auto-import-form").listeners.submit({ preventDefault() {} });
  assert.equal(JSON.parse(ui.get("auto-import-defs").value).name, "Earlier draft");
  assert.equal(ui.calls.some(call => call.options?.method === "POST"), false);
  assert.match(ui.get("auto-import-results").textContent, /valid export/);
});

test("import network failures release the submit control and keep the draft", async () => {
  const ui = importFixture(); await settle();
  ui.get("auto-import-defs").value = '{"name":"Draft"}';
  ui.context.fetch = async () => { throw new Error("Connection lost"); };
  await ui.get("auto-import-form").listeners.submit({ preventDefault() {} });
  assert.equal(ui.get("auto-import-form").querySelector().disabled, false);
  assert.equal(JSON.parse(ui.get("auto-import-defs").value).name, "Draft");
  assert.match(ui.get("auto-import-results").textContent, /Connection lost/);
});
function pending(overrides = {}) {
  return { provider: "github", redirect: "https://app.example/automations", context: "context-a", state: "a".repeat(64), verifier: "v".repeat(64), created: Date.now(), ...overrides };
}
test("account authorization binds provider, return address and random state without putting the verifier in the URL", async () => {
  const ui = fixture(); loadOAuth(ui); await settle();
  ui.get("oauth-provider").value = "github";
  ui.context.fetch = async (_, options) => {
    const body = JSON.parse(options.body);
    return ui.reply({ authorize_url: "https://github.example/authorize?redirect_uri=" + encodeURIComponent(body.redirect_uri) + "&code_challenge_method=S256&code_challenge=challenge", verifier: "v".repeat(64) });
  };
  await ui.get("oauth-get-url").listeners.click.call(ui.get("oauth-get-url"));
  const saved = JSON.parse(ui.storage.get("lightwork.oauth.pending.v1"));
  const url = new URL(ui.context.window.location.assigned);
  assert.equal(url.searchParams.get("state"), saved.state); assert.equal(saved.provider, "github");
  assert.equal(saved.context, "context-a"); assert.equal(url.href.includes(saved.verifier), false);
});

test("valid account return consumes the code once and automatically exchanges the bound provider", async () => {
  const ui = fixture(), saved = pending();
  ui.storage.set("lightwork.oauth.pending.v1", JSON.stringify(saved));
  ui.context.window.location.href += "?code=returned-code&state=" + saved.state;
  const original = ui.context.fetch;
  ui.context.fetch = async (url, options) => {
    if (url.endsWith("/exchange")) { ui.calls.push({ url, options }); return ui.reply({ connected: true, provider: "github" }); }
    return original(url, options);
  };
  loadOAuth(ui); await settle();
  const exchanges = ui.calls.filter(call => call.url.endsWith("/exchange"));
  assert.equal(exchanges.length, 1); assert.equal(exchanges[0].url, "/api/v1/oauth/github/exchange");
  assert.deepEqual(JSON.parse(exchanges[0].options.body), { code: "returned-code", redirect_uri: saved.redirect, verifier: saved.verifier });
  assert.equal(ui.context.window.history.cleaned, "/automations");
  assert.equal(ui.storage.has("lightwork.oauth.pending.v1"), false);
  assert.equal(ui.get("oauth-msg").textContent, "Account connected.");
});

for (const changes of [{ state: "b".repeat(64) }, { context: "another-workspace" }, { redirect: "https://other.example/callback" }, { created: Date.now() - 600001 }]) {
  test("mismatched or expired account return cannot exchange credentials: " + Object.keys(changes)[0], async () => {
    const ui = fixture(); ui.storage.set("lightwork.oauth.pending.v1", JSON.stringify(pending(changes)));
    ui.context.window.location.href += "?code=returned-code&state=" + "a".repeat(64);
    loadOAuth(ui); await settle();
    assert.equal(ui.calls.some(call => call.url.endsWith("/exchange")), false);
    assert.equal(ui.storage.has("lightwork.oauth.pending.v1"), false);
    assert.match(ui.get("oauth-msg").textContent, /expired|match/);
  });
}
