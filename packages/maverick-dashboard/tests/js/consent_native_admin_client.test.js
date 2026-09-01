// Run with: node --test packages/maverick-dashboard/tests/js/consent_native_admin_client.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function source() {
  return fs.readFileSync(
    path.resolve(
      __dirname,
      "../../maverick_dashboard/static/lightwork-consent-native-admin.js",
    ),
    "utf8",
  );
}

test("client bounds same-origin JSON and omits ambient authority for public keys", () => {
  const text = source();
  assert.match(text, /maxResponseBytes = 512 \* 1024/);
  assert.match(text, /maxRequestBytes = 128 \* 1024/);
  assert.match(text, /parsed\.origin !== window\.location\.origin/);
  assert.match(text, /credentials: publicMetadata \? "omit" : "same-origin"/);
  assert.match(text, /response\.body\.getReader/);
  assert.match(text, /await reader\.cancel\(\)/);
  assert.match(text, /new TextEncoder\(\)\.encode\(settings\.body\)/);
  assert.match(text, /replaceChildren\(\)/);
  assert.match(text, /textContent = JSON\.stringify/);
  assert.doesNotMatch(text, /\.innerHTML\s*=/);
  assert.doesNotMatch(text, /eval\s*\(/);
  assert.doesNotMatch(text, /document\.cookie/);
  assert.doesNotMatch(text, /localStorage/);
});

test("mutations carry only closed selectors, evidence, and exact CAS fields", () => {
  const text = source();
  assert.match(text, /expected_revision: 0/);
  assert.match(text, /expected_directory_revision:/);
  assert.match(text, /attestation_evidence_sha256:/);
  assert.match(text, /attestation_material_b64url:/);
  assert.match(text, /evidence_sha256:/);
  assert.match(text, /mutation\("\/applications", body\)/);
  assert.match(text, /"\/receipt-key\/rotate"/);
  assert.match(text, /encodeURIComponent\(deviceId\) \+ "\/rotate"/);
  assert.match(text, /encodeURIComponent\(deviceId\) \+ "\/revoke"/);
  assert.doesNotMatch(text, /tenant_id\s*:/);
  assert.doesNotMatch(text, /principal_ref\s*:/);
  assert.doesNotMatch(text, /receipt_key_secret/);
});
