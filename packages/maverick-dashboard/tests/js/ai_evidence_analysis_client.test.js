// Run with: node --test packages/maverick-dashboard/tests/js/ai_evidence_analysis_client.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function client(response) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
        value: "",
        files: [],
        checked: false,
        disabled: false,
        dataset: {},
        textContent: "",
        addEventListener() {},
      });
    }
    return elements.get(id);
  }
  const context = {
    console,
    document: {
      getElementById(id) { return id === "evidence-shell" ? null : element(id); },
    },
    fetch: async () => response,
  };
  vm.createContext(context);
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-ai-evidence-analysis.js"),
    "utf8",
  );
  vm.runInContext(source, context, { filename: "lightwork-ai-evidence-analysis.js" });
  return () => vm.runInContext('apiRequest("/probe")', context);
}

test("malformed 2xx evidence response rejects generically without echoing its body", async () => {
  const secret = "private-evidence-response-content";
  const request = client({
    ok: true,
    status: 200,
    async json() { throw new Error(secret); },
  });

  await assert.rejects(request(), (error) => {
    assert.equal(error.message, "The server returned an invalid JSON response.");
    assert.equal(error.message.includes(secret), false);
    assert.equal(error.status, 200);
    return true;
  });
});

test("valid JSON evidence error detail remains available", async () => {
  const request = client({
    ok: false,
    status: 409,
    async json() { return { detail: "Evidence revision conflict." }; },
  });

  await assert.rejects(request(), (error) => {
    assert.equal(error.message, "Evidence revision conflict.");
    assert.equal(error.status, 409);
    return true;
  });
});
