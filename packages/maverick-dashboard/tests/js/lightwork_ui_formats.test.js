// Run with: node --test packages/maverick-dashboard/tests/js/lightwork_ui_formats.test.js
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const source = fs.readFileSync(
  path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-ui.js"), "utf8");
const NOW = Date.UTC(2026, 9, 4, 13, 1, 12);

function load(body = "") {
  const dom = new JSDOM(`<div id="mv-toasts"></div><div id="mv-toasts-alert"></div>${body}`,
    { url: "https://lightwork.test/", runScripts: "outside-only" });
  dom.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  dom.window.Date.now = () => NOW;
  dom.window.eval(source);
  return dom.window;
}

test("relative times divide by the unit they name, before and after now", () => {
  const w = load();
  const at = (seconds) => (NOW + seconds * 1000) / 1000;
  const minute = 60, hour = 3600, day = 86400;
  const cases = [
    [30, "in a moment", "just now"],
    [50, "in 50 seconds", "50 seconds ago"],
    [90, "in 2 minutes", "2 minutes ago"],
    [209, "in 3 minutes", "3 minutes ago"],
    [59 * minute, "in 59 minutes", "59 minutes ago"],
    [59 * minute + 50, "in 1 hour", "1 hour ago"],
    [3 * hour, "in 3 hours", "3 hours ago"],
    [2 * day, "in 2 days", "2 days ago"],
    [10 * day, "next week", "last week"],
    [21 * day, "in 3 weeks", "3 weeks ago"],
    [61 * day, "in 2 months", "2 months ago"],
    [731 * day, "in 2 years", "2 years ago"],
  ];
  for (const [seconds, future, past] of cases) {
    assert.equal(w.mvTimeRelative(at(seconds)), future, `${seconds}s ahead`);
    assert.equal(w.mvTimeRelative(at(-seconds)), past, `${seconds}s ago`);
  }
  // Milliseconds and ISO strings name the same moment.
  assert.equal(w.mvTimeRelative(NOW + 209000), "in 3 minutes");
  assert.equal(w.mvTimeRelative(new Date(NOW - 3 * hour * 1000).toISOString()), "3 hours ago");
  for (const missing of [null, undefined, "", "not a time", 0]) assert.equal(w.mvTimeRelative(missing), "");
  w.close();
});
