// Run with: node --test packages/maverick-dashboard/tests/js/lightwork_ui_shared_helpers.test.js
"use strict";
// Shared helpers in lightwork-ui.js that pages use instead of their own
// copies: the browser chrome colour follows the theme.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const source = fs.readFileSync(
  path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-ui.js"), "utf8");

function load(head = "", body = "") {
  const dom = new JSDOM(`<!doctype html><html><head>${head}</head><body class="theme-dark">
    <div id="mv-toasts"></div><div id="mv-toasts-alert"></div><main id="content">${body}</main></body></html>`,
  { url: "https://lightwork.test/overview", runScripts: "outside-only" });
  dom.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  dom.window.eval(source);
  return dom.window;
}

test("switching the theme moves the browser chrome colour to the new canvas", () => {
  const w = load(`<meta name="theme-color" content="#04050A">
    <style>body.theme-dark { background-color: rgb(4, 5, 10); } body.theme-light { background-color: rgb(244, 246, 250); }</style>`);
  w.mvApplyDisplayPreference("theme", "light");
  assert.equal(w.document.querySelector('meta[name="theme-color"]').getAttribute("content"), "rgb(244, 246, 250)");
  w.mvApplyDisplayPreference("density", "compact");   // other preferences leave it alone
  assert.equal(w.document.querySelector('meta[name="theme-color"]').getAttribute("content"), "rgb(244, 246, 250)");
  w.close();
});

test("without a canvas colour the server's theme colour stays", () => {
  const w = load('<meta name="theme-color" content="#04050A">');
  w.mvApplyDisplayPreference("theme", "light");
  assert.equal(w.document.querySelector('meta[name="theme-color"]').getAttribute("content"), "#04050A");
  w.close();
});
