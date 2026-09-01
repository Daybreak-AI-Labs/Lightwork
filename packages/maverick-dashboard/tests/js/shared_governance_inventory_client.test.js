// Run with: node --test packages/maverick-dashboard/tests/js/shared_governance_inventory_client.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function source() {
  return fs.readFileSync(
    path.resolve(
      __dirname,
      "../../maverick_dashboard/static/lightwork-shared-governance-inventory.js",
    ),
    "utf8",
  );
}

test("client uses bounded same-origin JSON and safe DOM projection", () => {
  const text = source();
  assert.match(text, /maxResponseBytes = 2 \* 1024 \* 1024/);
  assert.match(text, /credentials: "same-origin"/);
  assert.match(text, /response\.body\.getReader/);
  assert.match(text, /await reader\.cancel\(\)/);
  assert.match(text, /replaceChildren\(\)/);
  assert.match(text, /textContent = JSON\.stringify/);
  assert.doesNotMatch(text, /\.innerHTML\s*=/);
  assert.doesNotMatch(text, /eval\s*\(/);
});

test("mutations carry closed fields and exact compare-and-swap authority", () => {
  const text = source();
  assert.match(text, /expected_active_revision:/);
  assert.match(text, /expected_active_integrity_sha256:/);
  assert.match(text, /operation_id:/);
  assert.match(text, /request\("\/reconcile", \{ method: "POST"/);
  assert.match(text, /request\("\/recovery\/checkpoint", \{ method: "POST"/);
  assert.match(text, /request\("\/recovery\/restore", \{ method: "POST"/);
  assert.doesNotMatch(text, /tenant_id\s*:/);
  assert.doesNotMatch(text, /provider_url\s*:/);
});
