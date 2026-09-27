// Run with: node --test packages/maverick-dashboard/tests/js/governance_json_response_contracts.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

const JSON_CLIENTS = [
  ["consent_delivery.html", "call", ["/probe"]],
  ["consent_experiments.html", "call", ["/probe"]],
  ["consent_public_clients.html", "call", ["/probe"]],
  ["consent_publishing.html", "call", ["/probe"]],
  ["policy_publication_links.html", "call", ["/probe"]],
  ["privacy_data_discovery.html", "call", ["/probe"]],
  ["privacy_regulatory_intelligence.html", "call", ["/probe"]],
  ["third_party_intelligence.html", "request", ["/probe", "GET", {}]],
  ["consent_scan_notice_proposals.html", "call", ["/probe"]],
  ["consent_profile_data.html", "request", ["/probe"]],
  ["data_use_governance.html", "call", ["/probe"]],
  ["consent_scan_inventory.html", "call", ["/probe"]],
  ["data_use_providers.html", "call", ["/probe"]],
  ["consent_preferences.html", "call", ["/probe"]],
  ["agents.html", "call", ["GET", "/probe"]],
  ["ekko.html", "request", ["/probe"]],
];

const TEXT_CLIENTS = [
  "third_party_lifecycle.html",
  "third_party_collaboration.html",
  "consumer_trust_center_authority.html",
  "compliance_scoping.html",
  "compliance_scope_actions.html",
  "privacy_rights_consumer_delivery.html",
  "privacy_notice_governance.html",
  "privacy_rights_orchestration.html",
].map((file) => [file, "run", ["GET", "/probe", {}]]);

function extractScript(file, functionName) {
  const template = fs.readFileSync(
    path.resolve(__dirname, "../../maverick_dashboard/templates", file),
    "utf8",
  );
  const scripts = [...template.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/gi)].map(
    (match) => {
      const external = match[1].match(/\bsrc\s*=\s*["'](\/static\/[^"']+)["']/i);
      if (!external) return match[2];
      return fs.readFileSync(
        path.resolve(__dirname, "../../maverick_dashboard", external[1].slice(1)),
        "utf8",
      );
    },
  );
  const selected = scripts.find((source) =>
    new RegExp(`async\\s+function\\s+${functionName}\\b`).test(source)
  );
  assert.ok(selected, `${file} must contain the ${functionName} client`);
  let source = selected.replace(
    /\{\{\s*"true"\s*if\s*consent_scan_notice_proposals_enabled\s*else\s*"false"\s*\}\}/g,
    "false",
  );
  if (file === "ekko.html") {
    source = source.replace(/\s*renderStatus\(initial\);\s*refresh\(\);/, "\n");
  }
  return source;
}

function instrument(source, functionName, file) {
  const classicClose = source.lastIndexOf("})();");
  const alternateClose = source.lastIndexOf("}());");
  const windowClose = source.lastIndexOf("}(typeof window");
  const close = Math.max(classicClose, alternateClose, windowClose);
  if (close === -1) {
    return `${source}\nglobalThis.__requestUnderTest=${functionName};\n`;
  }
  return `${source.slice(0, close)}globalThis.__requestUnderTest=${functionName};\n${source.slice(close)}`;
}

function streamedJsonBody(text) {
  const bytes = new TextEncoder().encode(text);
  return {
    headers: {
      get(name) {
        return String(name).toLowerCase() === "content-length"
          ? String(bytes.byteLength)
          : null;
      },
    },
    body: {
      getReader() {
        let delivered = false;
        return {
          async read() {
            if (delivered) return { done: true, value: undefined };
            delivered = true;
            return { done: false, value: bytes };
          },
          async cancel() {},
        };
      },
      async cancel() {},
    },
  };
}

function harness(file, functionName, args) {
  const elements = new Map();
  const listeners = new Map();
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const controls = new Proxy({}, {
      get(_target, name) { return element(`${id}:${String(name)}`); },
    });
    const item = {
      id,
      value: "status",
      textContent: "",
      innerHTML: "",
      checked: false,
      disabled: false,
      hidden: false,
      selectedIndex: 0,
      dataset: { method: "GET", path: "/status" },
      options: [{ dataset: { method: "GET", path: "/status" } }],
      elements: controls,
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(type, listener) { listeners.set(`${id}:${type}`, listener); },
      querySelectorAll() { return []; },
      querySelector() { return element(`${id}:query`); },
      replaceChildren() {},
      appendChild() {},
      append() {},
      setAttribute() {},
      removeAttribute() {},
      focus() {},
      closest() { return item; },
    };
    elements.set(id, item);
    return item;
  }

  const validBootResponse = {
    ok: true,
    status: 200,
    ...streamedJsonBody("{}"),
    async json() { return {}; },
    async text() { return "{}"; },
  };
  const context = {
    console,
    crypto: webcrypto,
    TextEncoder,
    TextDecoder,
    Uint8Array,
    document: {
      getElementById: element,
      querySelector() { return element("document:query"); },
      querySelectorAll() { return []; },
      createElement() { return element("document:created"); },
    },
    window: { confirm() { return true; } },
    confirm() { return true; },
    mvToast() {},
    fetch: async () => validBootResponse,
    setTimeout() { return 0; },
    clearTimeout() {},
  };
  vm.createContext(context);
  if (file === "consent_preferences.html") {
    element("cp-policy-form").elements.purposes.value = '{"analytics":"consent"}';
    vm.runInContext(fs.readFileSync(path.resolve(__dirname,
      "../../maverick_dashboard/static/lightwork-consent-policy-editor.js"), "utf8"), context);
  }
  const source = instrument(extractScript(file, functionName), functionName, file);
  vm.runInContext(source, context, { filename: file });
  return {
    invoke(response) {
      context.fetch = async () => response;
      context.__args = args;
      return vm.runInContext("__requestUnderTest(...__args)", context);
    },
    element,
  };
}

for (const [file, functionName, args] of [...JSON_CLIENTS, ...TEXT_CLIENTS]) {
  const usesText = functionName === "run";

  test(`${file} rejects malformed 2xx without exposing its body`, async () => {
    const secret = `private-response-content-for-${file}`;
    const response = {
      ok: true,
      status: 200,
      ...streamedJsonBody(secret),
      async json() { throw new Error(secret); },
      async text() { return secret; },
    };

    await assert.rejects(harness(file, functionName, args).invoke(response), (error) => {
      assert.equal(error.message, "Request failed: the server returned an invalid JSON response.");
      assert.equal(error.message.includes(secret), false);
      return true;
    });
  });

  test(`${file} preserves a valid JSON error detail`, async () => {
    const detail = `Reviewed conflict for ${file}.`;
    const response = {
      ok: false,
      status: 409,
      ...streamedJsonBody(JSON.stringify({ detail })),
      async json() { return { detail }; },
      async text() { return JSON.stringify({ detail }); },
    };

    await assert.rejects(harness(file, functionName, args).invoke(response), (error) => {
      assert.equal(error.message, detail);
      return true;
    });
    assert.equal(usesText || functionName === "call" || functionName === "request", true);
  });
}

test("redact.html marks malformed 2xx as failed without exposing its body", async () => {
  const secret = "private-redaction-response-content";
  const client = harness("redact.html", "preview", [null]);
  await client.invoke({
    ok: true,
    status: 200,
    async json() { throw new Error(secret); },
  });

  const message = client.element("proof-badge").textContent;
  assert.equal(message, "Request failed: the server returned an invalid JSON response.");
  assert.equal(message.includes(secret), false);
  assert.equal(client.element("proof-badge").className, "badge failure");
});

test("redact.html preserves a valid JSON error detail", async () => {
  const client = harness("redact.html", "preview", [null]);
  await client.invoke({
    ok: false,
    status: 422,
    async json() { return { detail: "Reviewed redaction error." }; },
  });

  assert.equal(client.element("proof-badge").textContent, "Reviewed redaction error.");
  assert.equal(client.element("proof-badge").className, "badge failure");
});
