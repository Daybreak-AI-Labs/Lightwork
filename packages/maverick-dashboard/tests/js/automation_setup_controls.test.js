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
    mvHuman: value => String(value || "").replace(/[_-]+/g, " ").replace(/^./, c => c.toUpperCase()),
    plainError: (_, what) => "Could not " + what + ". Please try again.",
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

test("guided source fields keep typed values, tucked-away options and per-source drafts", async () => {
  const ui = eventFixture(); await settle();
  ui.get("auto-event-source").value = "file_dir"; ui.context.changeSource();
  ui.get("auto-source-path").value = "inbound"; ui.get("auto-source-path").listeners.input();
  ui.get("auto-source-recursive").checked = true; ui.get("auto-source-recursive").listeners.input();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.context.readSourceConfig())), { path: "inbound", recursive: true });
  // The file pattern is an optional extra, tucked under "More settings".
  assert.ok(ui.get("auto-event-more").children.some(child => child.children.some(c => c.id === "auto-source-glob")));
  ui.get("auto-event-source").value = "rss"; ui.context.changeSource();
  ui.get("auto-source-url").value = "https://example.org/feed.xml"; ui.get("auto-source-url").listeners.input();
  assert.equal(ui.context.readSourceConfig().url, "https://example.org/feed.xml");
  ui.get("auto-event-source").value = "file_dir"; ui.context.changeSource();
  assert.equal(ui.get("auto-source-recursive").checked, true);
  assert.equal(ui.get("auto-source-path").value, "inbound");
  ui.get("auto-event-source").value = "imap_email"; ui.context.changeSource();
  ui.get("auto-source-port").value = "1993"; ui.get("auto-source-port").listeners.input();
  assert.equal(ui.context.readSourceConfig().port, 1993);
});

test("extra settings are name and value rows, never a code box", async () => {
  const ui = eventFixture(); await settle();
  ui.get("auto-event-source").value = "custom_source"; ui.context.changeSource();
  assert.equal(ui.get("auto-event-config-advanced").open, true);
  ui.get("auto-event-extra-add").listeners.click();
  const row = ui.get("auto-event-extra").children[0];
  row._key.value = "region"; row._val.value = "eu";
  const row2 = ui.context.addExtraRow("retain", "true");
  assert.ok(row2);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.context.readSourceConfig())), { region: "eu", retain: true });
  assert.equal(source.includes('id="auto-event-config"'), false);
  assert.equal(source.includes("Source settings (JSON)"), false);
  assert.equal(source.includes("Exported definitions (JSON)"), false);
});

test("a form source asks the server to create its private link", async () => {
  const ui = eventFixture(); await settle();
  ui.get("auto-event-source").value = "form"; ui.context.changeSource();
  ui.get("auto-event-name").value = "Vendor Intake Form";
  ui.get("auto-event-template").value = "triage";
  let body;
  ui.context.fetch = async (_, options) => { body = JSON.parse(options.body); return ui.reply({ name: "vendor" }); };
  await ui.get("auto-event-form").listeners.submit({ preventDefault() {} });
  assert.deepEqual(body.config, {});
  assert.notEqual(body.config.token, "vendor-intake-form");
});

test("form creation works without browser crypto because the server mints the link", async () => {
  const ui = eventFixture(); await settle();
  ui.get("auto-event-source").value = "form"; ui.context.changeSource();
  ui.get("auto-event-template").value = "triage";
  ui.context.window.crypto = null;
  let created = false;
  ui.context.fetch = async () => { created = true; return ui.reply({name: "intake"}); };
  await ui.get("auto-event-form").listeners.submit({ preventDefault() {} });
  assert.equal(created, true);
  assert.match(ui.get("auto-event-msg").textContent, /Created/);
});

test("a new form cannot silently submit a manually chosen weak link", async () => {
  const ui = eventFixture(); await settle();
  ui.get("auto-event-source").value = "form"; ui.context.changeSource();
  assert.equal(ui.get("auto-event-config-advanced").hidden, true);
  ui.context.sourceDrafts.form.token = "legacy.link";
  assert.deepEqual(JSON.parse(JSON.stringify(ui.context.readSourceConfig())), {});
  const hint = ui.get("auto-event-formurl");
  assert.match(hint.textContent, /open or copy the private form link/);
  assert.equal(hint.children.length, 0);
});

test("form automations show pending and unavailable submission states", async () => {
  const ui = eventFixture(); await settle();
  ui.context.loadRuns = () => {};
  ui.context.fetch = async url => {
    if (url === "/api/v1/event-triggers") return ui.reply({
      sources: ["form"], triggers: [
        { name: "pending", source: "form", flow: "intake", cursor: "0", pending_submissions: 2 },
        { name: "unknown", source: "form", flow: "intake", cursor: "0", pending_submissions: null }
      ]
    });
    if (url.endsWith("/history?limit=500")) return ui.reply({ history: [] });
    if (url.endsWith("/outcomes")) return ui.reply({ outcomes: {} });
    throw new Error("Unexpected request " + url);
  };
  vm.runInContext(source.slice(source.indexOf("    async function loadEventTriggers()"),
    source.indexOf("    async function delEventTrigger(name)")), ui.context);
  await ui.context.loadEventTriggers();
  const entries = ui.get("auto-event-list").children;
  assert.match(entries[0].textContent, /2 submissions are waiting.*Check now/);
  assert.match(entries[1].textContent, /Submission status is unavailable.*Changes are paused/);
});

test("saved form automation exposes a usable open and copy link", async () => {
  const ui = eventFixture(); await settle();
  ui.context.loadRuns = () => {};
  ui.context.fetch = async url => {
    if (url === "/api/v1/event-triggers") return ui.reply({sources: ["form"], triggers: [
      {name: "intake", source: "form", flow: "review", cursor: "0", pending_submissions: 0,
        config: {token: "legacy.link"}}
    ]});
    if (url.endsWith("/history?limit=500")) return ui.reply({history: []});
    if (url.endsWith("/outcomes")) return ui.reply({outcomes: {}});
    throw new Error("Unexpected request " + url);
  };
  let copied = "";
  ui.context.window.navigator = {clipboard: {writeText: async value => { copied = value; }}};
  vm.runInContext(source.slice(source.indexOf("    async function loadEventTriggers()"),
    source.indexOf("    async function delEventTrigger(name)")), ui.context);
  await ui.context.loadEventTriggers();
  const actions = ui.get("auto-event-list").children[0].children[1];
  const open = actions.children[0], copy = actions.children[1], status = actions.children[2];
  assert.equal(open.href, "/form/legacy.link");
  assert.equal(open.target, "_blank");
  assert.equal(open.rel, "noopener noreferrer");
  await copy.listeners.click();
  assert.equal(copied, "https://app.example/form/legacy.link");
  assert.equal(status.textContent, "Link copied.");
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
  assert.match(ui.get("auto-event-msg").textContent, /Could not check for new items/); assert.equal(button.disabled, false);
});

test("a check with accepted work still pending does not claim completion", async () => {
  const ui = eventFixture(); await settle();
  ui.context.fetch = async () => ui.reply({ polled: 1, triggers: [{
    name: "intake", new_events: 1, fired: [], fired_flows: [],
    notes: ["flow unavailable: binding is stale"], poll_error: false
  }] });
  const button = ui.get("auto-event-poll"); await button.listeners.click.call(button);
  assert.match(ui.get("auto-event-msg").textContent, /Some submissions are still waiting/);
  assert.equal(button.disabled, false);
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

test("choosing an export file prepares definitions without starting an import or changing activation choices", async () => {
  const ui = importFixture(); await settle();
  const file = ui.get("auto-import-file"); file.files = [{ size: 80, name: "workflows.json", text: async () => '[{"name":"Review","nodes":[]}]' }];
  await file.listeners.change();
  assert.match(ui.get("auto-import-file-msg").textContent, /Ready to import workflows.json \(1 automation\)/);
  assert.equal(ui.get("auto-import-webhooks").checked, false); assert.equal(ui.get("auto-import-schedules").checked, false);
  assert.equal(ui.calls.some(call => call.options?.method === "POST"), false);
  let body; ui.context.fetch = async (_, options) => { body = JSON.parse(options.body); return ui.reply({ imported: [], dry_run: true }); };
  await ui.get("auto-import-form").listeners.submit({ preventDefault() {} });
  assert.deepEqual(body.definitions, [{ name: "Review", nodes: [] }]);
});

test("an older file read cannot replace a newer file choice", async () => {
  const ui = importFixture(); await settle(); let resolve;
  const file = ui.get("auto-import-file"); file.files = [{ size: 80, name: "old.json", text: () => new Promise(done => { resolve = done; }) }];
  const loading = file.listeners.change();
  file.files = [{ size: 80, name: "new.json", text: async () => '{"name":"Newer"}' }];
  await file.listeners.change();
  resolve('{"name":"Old file"}'); await loading;
  assert.match(ui.get("auto-import-file-msg").textContent, /new.json/);
});

test("a file that is not an export blocks an accidental import", async () => {
  const ui = importFixture(); await settle();
  const file = ui.get("auto-import-file"); file.files = [{ size: 10, name: "broken.json", text: async () => "null" }];
  await file.listeners.change();
  assert.match(ui.get("auto-import-file-msg").textContent, /not an automation export/);
  await ui.get("auto-import-form").listeners.submit({ preventDefault() {} });
  assert.equal(ui.calls.some(call => call.options?.method === "POST"), false);
  assert.match(ui.get("auto-import-results").textContent, /valid export/);
});

test("import network failures release the submit control with a plain message", async () => {
  const ui = importFixture(); await settle();
  ui.context.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await ui.get("auto-import-form").listeners.submit({ preventDefault() {} });
  assert.equal(ui.get("auto-import-form").querySelector().disabled, false);
  assert.match(ui.get("auto-import-results").textContent, /Could not import these automations/);
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

test("saved timetables are described in words", () => {
  const ctx = {}; vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf("  var DAYS ="), source.indexOf("  function delBtn(")), ctx);
  assert.equal(ctx.cronText("0 9 * * 1-5"), "Weekdays at 9 am");
  assert.equal(ctx.cronText("30 14 * * *"), "Every day at 2:30 pm");
  assert.equal(ctx.cronText("0 8 * * 1,3"), "Every Monday, Wednesday at 8 am");
  assert.equal(ctx.cronText("0 7 1 * *"), "Monthly on the 1st at 7 am");
  assert.equal(ctx.cronText("15 * * * *"), "Every hour at 15 minutes past");
  assert.equal(ctx.cronText("0 9 1 1 *"), "On a custom timetable");
});
