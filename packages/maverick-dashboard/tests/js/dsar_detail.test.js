"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const source = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-dsar-detail.js"), "utf8");
const html = `<div id="dsar-page" data-request-id="DSAR-1" data-subject="a@example.com" data-channel="email" data-kind="erasure" data-fulfillment-id="F-1" data-revision="4">
<div id="dsar-status"></div><div id="dsar-worksheet-result"></div>
<button id="dsar-identity"></button><button id="dsar-link"></button><button id="dsar-erase"></button><button id="dsar-close"></button>
<div id="dsar-link-box" hidden><input id="dsar-link-url"><button id="dsar-link-copy"></button></div></div>`;
const json = (v, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

async function fixture({ formValues, confirm = true, reply }) {
  const dom = new JSDOM(html, { url: "https://lightwork.example/privacy/dsar/DSAR-1", runScripts: "outside-only" });
  const { window } = dom;
  const calls = [], forms = [], confirms = [];
  window.mvForm = async (title, fields) => { forms.push({ title, fields }); return formValues.shift(); };
  window.mvConfirm = async (m) => { confirms.push(m); return confirm; };
  window.mvErrorText = (f, action) => `Could not ${action}.`;
  window.fetch = async (url, opts = {}) => { calls.push({ url, ...opts }); return reply(url, opts); };
  window.eval(source);
  const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((d) => setImmediate(d)); };
  return { window, doc: window.document, calls, forms, confirms, settle };
}

test("identity is confirmed through a method dropdown, carrying the record version", async (t) => {
  const ui = await fixture({ formValues: [{ method: "document_check", reference: "ticket 9" }], reply: () => json({}) });
  t.after(() => ui.window.close());
  ui.doc.getElementById("dsar-identity").click(); await ui.settle();
  const methodField = ui.forms[0].fields[0];
  assert.equal(methodField.type, "select");
  assert.ok(methodField.options.every((o) => !/_/.test(o.label)));
  const call = ui.calls[0];
  assert.match(call.url, /fulfillment\/F-1\/identity$/);
  assert.deepEqual(JSON.parse(call.body), { method: "document_check", reference: "ticket 9", revision: 4 });
});

test("the secure link is shown once in a copy box", async (t) => {
  const ui = await fixture({ formValues: [{ hours: "24", single: true }], reply: () => json({ response_path: "/dsar/response/abc", revision: 5 }) });
  t.after(() => ui.window.close());
  ui.doc.getElementById("dsar-link").click(); await ui.settle();
  assert.deepEqual(JSON.parse(ui.calls[0].body), { expires_hours: 24, revision: 4, max_reads: 1 });
  assert.equal(ui.doc.getElementById("dsar-link-box").hidden, false);
  assert.equal(ui.doc.getElementById("dsar-link-url").value, "https://lightwork.example/dsar/response/abc");
});

test("erasure picks the matching person and confirms before erasing", async (t) => {
  const ui = await fixture({
    formValues: [{ who: "0" }],
    reply: (url) => url.endsWith("/people")
      ? json({ people: [{ channel: "slack", user: "someone", person: "someone", channel_label: "Slack" }, { channel: "email", user: "a@example.com", person: "a@example.com", channel_label: "Email" }] })
      : json({ message: "Erased 1 conversation(s) and everything linked to them." }),
  });
  t.after(() => ui.window.close());
  ui.doc.getElementById("dsar-erase").click(); await ui.settle();
  assert.equal(ui.forms[0].fields[0].options.length, 1);
  assert.equal(ui.confirms.length, 1);
  const erase = ui.calls.find((c) => c.url.endsWith("/people/erase"));
  assert.deepEqual(JSON.parse(erase.body), { channel: "email", user: "a@example.com" });
  assert.match(ui.doc.getElementById("dsar-status").textContent, /Erased 1 conversation/);
});

test("closing needs confirmation and errors stay plain", async (t) => {
  const ui = await fixture({ formValues: [], reply: () => new Response("x", { status: 500 }) });
  t.after(() => ui.window.close());
  ui.doc.getElementById("dsar-close").click(); await ui.settle();
  assert.equal(ui.confirms.length, 1);
  assert.equal(ui.doc.getElementById("dsar-status").textContent, "Could not close the request.");
});
