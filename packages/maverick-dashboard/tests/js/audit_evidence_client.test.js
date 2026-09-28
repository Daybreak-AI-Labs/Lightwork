"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function scriptFor(file, marker) {
  const template = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/templates", file), "utf8");
  const script = [...template.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).find(source => source.includes(marker));
  assert.ok(script, `${file} must contain its client script`);
  return script;
}

test("binder download preserves the exact signed JSON values without numeric reserialization", async () => {
  // Python distinguishes these integral floats and the integer outside JS's
  // exact range when computing its advertised sorted-JSON artifact digest.
  const original = '{"generated_at":1780000000.0,"amount":1.0,"id":9007199254740993,"artifact":{"binder_id":"sample"}}';
  let click;
  let exported;
  const source = { textContent: original };
  const button = { addEventListener: (_name, callback) => { click = callback; } };
  const link = { click() {}, remove() {} };
  const document = {
    getElementById: id => id === "binder-json" ? button : source,
    createElement: () => link,
    body: { appendChild() {} },
  };
  vm.runInNewContext(scriptFor("audit_binder.html", "binder-json"), {
    document, Blob,
    URL: { createObjectURL: blob => { exported = blob; return "blob:synthetic"; }, revokeObjectURL() {} },
  });
  click();
  assert.equal(await exported.text(), original + "\n");
  assert.equal(link.download, "lightwork-audit-binder-sample.json");
});

test("audit search warns on partial reads even when no match can be returned", async () => {
  let submit;
  const message = { textContent: "", style: {} };
  const output = { textContent: "" };
  const button = { disabled: false };
  const elements = {
    "audit-search": { querySelector: () => button, addEventListener: (_name, callback) => { submit = callback; } },
    "audit-pattern": { value: "payment" },
    "audit-search-day": { value: "2026-08-10" },
    "audit-search-msg": message,
    "audit-search-results": output,
  };
  vm.runInNewContext(scriptFor("audit.html", "audit-search"), {
    document: { getElementById: id => elements[id] },
    window: { location: { origin: "https://synthetic.invalid" } }, URL,
    fetch: async () => ({ ok: true, json: async () => ({
      events: [], scanned: 1, total_matches: 0, scan_complete: false,
      scope: { day: "2026-08-10" }, read_report: { failures: 1, state: "partial" },
    }) }),
  });
  await submit({ preventDefault() {} });
  assert.match(message.textContent, /Search incomplete/);
  assert.match(message.textContent, /readable records only/);
  assert.equal(message.style.color, "var(--danger)");
  assert.equal(button.disabled, false);
});
