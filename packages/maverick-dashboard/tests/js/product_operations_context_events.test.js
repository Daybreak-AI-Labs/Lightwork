"use strict";
// Copilot's own context changes reach document listeners only. Pages keep a
// non-destructive check on document and a destructive clear on window (meant
// for a real workspace or account change, which lightwork-ui.js sends so both
// hear it). A Copilot selection change must not wipe those pages.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {JSDOM} = require("jsdom");

const source = fs.readFileSync(path.resolve(__dirname,
  "../../maverick_dashboard/static/lightwork-product-operations.js"), "utf8");

test("a Copilot selection change does not reach window listeners", () => {
  const dom = new JSDOM('<aside id="mv-copilot" hidden><section id="mv-product-operations">' +
    '<p id="mv-product-operation-status"></p><div id="mv-product-operation-buttons"></div>' +
    '<div id="mv-product-operation-form"></div></section></aside>', {url: "http://testserver/settings/all"});
  const w = dom.window, heard = {document: 0, window: 0};
  vm.runInNewContext(source, {window: w, document: w.document, CustomEvent: w.CustomEvent, URL: w.URL,
    URLSearchParams, AbortController, setTimeout, clearTimeout, fetch: async () => { throw Error("no network"); }});
  w.document.addEventListener("lightwork:copilot-context-changed", () => { heard.document += 1; });
  w.addEventListener("lightwork:copilot-context-changed", () => { heard.window += 1; });
  w.document.dispatchEvent(new w.CustomEvent("lightwork:copilot-selection", {detail: {selection: {kind: "setting"}}}));
  assert.deepEqual(heard, {document: 1, window: 0});
  dom.window.close();
});
