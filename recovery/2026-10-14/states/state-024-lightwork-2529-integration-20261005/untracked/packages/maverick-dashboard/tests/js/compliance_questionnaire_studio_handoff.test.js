"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const base = path.resolve(__dirname, "../../maverick_dashboard");
const source = fs.readFileSync(path.join(base, "static/lightwork-compliance-questionnaire-builder.js"), "utf8");
const panel = fs.readFileSync(path.join(base, "templates/_compliance_questionnaire_builder.html"), "utf8");
const domains = ["organization", "industry", "jurisdiction", "data", "system", "vendor", "ai", "operational"];
const questions = domains.map((domain, index) => ({
  name: "Question " + (index + 1), domain, kind: "boolean", options: [], required: true, condition: null,
}));
questions[0] = { ...questions[0], name: "Supplier record", kind: "opaque_ref" };
const context = "c".repeat(64);

function fixture(t, openedVersion = 3) {
  const dom = new JSDOM("<main>" + panel + "</main>", {
    url: "https://lightwork.test/compliance/scoping#cs-questionnaire-design?studio=supplier-review&form=intake&version=" + openedVersion,
    runScripts: "outside-only",
  });
  t.after(() => dom.window.close());
  const w = dom.window;
  w.Element.prototype.scrollIntoView = function () {};
  w.LwRecords = {
    el(tag, cls, text) {
      const node = w.document.createElement(tag);
      if (cls) node.className = cls;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    },
    confirm: async () => true,
    errorText: () => "The request could not be completed.",
  };
  w.mvDraftRecovery = { create: () => ({}) };
  const calls = [];
  w.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET" });
    let result;
    if (url === "/api/v1/compliance/questionnaire-builder/catalog") {
      result = { context, sources: [], notice: "" };
    } else if (url === "/api/v1/compliance/questionnaire-builder/source?identity=") {
      result = { context, source: "", source_revision: "", source_version: 0,
        design: { name: "", questions, rules: [{ framework: "", controls: [""], overlaps: [], reason: "", conditions: [] }] } };
    } else if (url === "/api/v1/compliance/questionnaire-builder/studio-sources") {
      result = { context, forms: [{ studio: "supplier-review", form: "intake", name: "Supplier review · Intake", version: 3 }], notice: "Choose a published form." };
    } else if (url === "/api/v1/compliance/questionnaire-builder/studio-form?studio=supplier-review&form=intake") {
      result = { context, name: "Supplier intake", source_version: 3,
        studio_source: { studio: "supplier-review", form: "intake", pointer: "a".repeat(64), blueprint: "b".repeat(64) },
        questions, incompatibilities: [], notice: "This form starts a separate questionnaire draft." };
    } else {
      throw new Error("Unexpected request: " + url);
    }
    return { ok: true, status: 200, redirected: false, json: async () => result };
  };
  w.eval(source);
  const settle = async () => { for (let index = 0; index < 80; index += 1) await new Promise((resolve) => setImmediate(resolve)); };
  return { w, calls, settle };
}

test("a Studio link selects and previews the current form without importing it automatically", async (t) => {
  const h = fixture(t, 2);
  await h.settle();
  const w = h.w;
  assert.equal(w.document.getElementById("cqb-studio-source").value, "0");
  assert.match(w.document.getElementById("cqb-status").textContent, /newer published version/);
  assert.match(w.document.getElementById("cqb-studio-preview-body").textContent, /Supplier intake · version 3/);
  assert.match(w.document.getElementById("cqb-studio-preview-body").textContent, /opened an older version.*currently published version/);
  assert.equal(w.document.getElementById("cqb-studio-import").hidden, false);
  assert.deepEqual(h.calls.map((item) => item.method), ["GET", "GET", "GET", "GET"]);

  w.document.getElementById("cqb-studio-import").click();
  await h.settle();
  assert.match(w.document.getElementById("cqb-status").textContent, /Questions imported into your draft/);
  assert.equal(w.document.getElementById("cqb-studio-import").hidden, true);
  assert.match(w.document.getElementById("cqb-work").textContent, /Question 1/);
  assert.equal(w.document.querySelector('[data-design-field="questions.0.kind"]').value, "opaque_ref");
  assert.match(w.document.getElementById("cqb-work").textContent, /does not verify that the item exists/);
  assert.equal(h.calls.length, 4);
});
