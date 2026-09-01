// Run with: node --test packages/maverick-dashboard/tests/js/consent_application_scanner_client.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function element(id) {
  return {
    id,
    disabled: false,
    textContent: "",
    value: "",
    attributes: {},
    addEventListener() {},
    getAttribute(name) {
      if (id === "consent-application-scanner-shell" && name === "data-api-base") {
        return "/api/v1/consent/application-scans";
      }
      return this.attributes[name] || null;
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
  };
}

function response(value, status) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let delivered = false;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        return name.toLowerCase() === "content-length" ? String(bytes.byteLength) : null;
      },
    },
    body: {
      getReader() {
        return {
          async read() {
            if (delivered) return { done: true };
            delivered = true;
            return { done: false, value: bytes };
          },
          async cancel() {},
        };
      },
    },
  };
}

test("503 readiness JSON renders the closed state and keeps operations disabled", async () => {
  const ids = [
    "consent-application-scanner-shell",
    "cas-operations",
    "cas-runtime-status",
    "cas-status-output",
    "cas-operation-status",
    "cas-operation-output",
    "cas-register-form",
    "cas-scan-form",
    "cas-review-form",
    "cas-refresh-status",
    "cas-list-applications",
    "cas-list-scans",
    "cas-verify-integrity",
    "cas-purge",
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, element(id)]));
  const context = {
    TextDecoder,
    TextEncoder,
    Uint8Array,
    console,
    document: { getElementById: (id) => elements[id] || null },
    fetch: async () => response({ enabled: false, ready: false }, 503),
  };
  vm.createContext(context);
  const source = fs.readFileSync(
    path.resolve(
      __dirname,
      "../../maverick_dashboard/static/lightwork-consent-application-scanner.js",
    ),
    "utf8",
  );
  vm.runInContext(source, context, {
    filename: "lightwork-consent-application-scanner.js",
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(
    elements["cas-runtime-status"].textContent,
    "Application manifest analysis is closed until its authority is ready.",
  );
  assert.equal(elements["cas-runtime-status"].attributes["data-state"], "failed");
  assert.equal(elements["cas-operations"].disabled, true);
  assert.match(elements["cas-status-output"].textContent, /"ready": false/);
});
