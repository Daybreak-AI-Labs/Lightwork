// Run with: node --test packages/maverick-dashboard/tests/js/lightwork_ui_formats.test.js
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const static_ = (name) => fs.readFileSync(
  path.resolve(__dirname, "../../maverick_dashboard/static", name), "utf8");
const source = static_("lightwork-ui.js");
const i18nSource = static_("lightwork-i18n.js");
// 2026-10-04 13:01:12 UTC, read by a viewer in London (BST, UTC+1).
const NOW = Date.UTC(2026, 9, 4, 13, 1, 12);
process.env.TZ = "Europe/London";

function load(body = "", { language = null } = {}) {
  const config = language
    ? `<script id="lightwork-i18n-data" type="application/json">${JSON.stringify(
      { language, locale: language === "fr" ? "fr-CA" : "en-US", catalog: {} })}</script>` : "";
  const dom = new JSDOM(`<!doctype html><html><head>${config}</head><body>` +
    `<div id="mv-toasts"></div><div id="mv-toasts-alert"></div>${body}</body></html>`,
    { url: "https://lightwork.test/", runScripts: "outside-only" });
  dom.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  dom.window.Date.now = () => NOW;
  dom.window.eval(source);
  if (language) dom.window.eval(i18nSource);
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

test("absolute times read one way in the viewer's zone, whatever the browser locale", () => {
  const w = load();
  const seconds = NOW / 1000;
  assert.equal(w.mvTimeAbsolute(seconds), "4 Oct 2026, 14:01 BST");
  assert.equal(w.mvTimeAbsolute(NOW), "4 Oct 2026, 14:01 BST");
  assert.equal(w.mvTimeAbsolute(String(seconds)), "4 Oct 2026, 14:01 BST");
  assert.equal(w.mvTimeAbsolute("2026-10-04T13:01:12Z"), "4 Oct 2026, 14:01 BST");
  assert.equal(w.mvWhen(seconds), "4 Oct 2026, 14:01 BST");
  for (const missing of [null, undefined, "", "soon", 0, NaN]) {
    assert.equal(w.mvTimeAbsolute(missing), "", String(missing));
    assert.equal(w.mvWhen(missing), "", String(missing));
  }
  // A calendar date stays that day in every zone; a moment uses the viewer's day.
  assert.equal(w.mvDateAbsolute("2026-10-04"), "4 Oct 2026");
  assert.equal(w.mvDateAbsolute(Date.UTC(2026, 9, 4, 23, 30) / 1000), "5 Oct 2026");
  assert.equal(w.mvDateAbsolute("2026-13-45x"), "");
  w.close();
});

test("server-rendered times become relative, absolute or calendar text", (t) => {
  const seconds = NOW / 1000;
  const w = load(
    `<time id="rel" data-mv-time="${seconds - 180}">x</time>` +
    `<time id="abs" data-mv-time="${seconds}" data-mv-time-mode="absolute">x</time>` +
    `<time id="day" datetime="2026-10-04" data-mv-time="2026-10-04" data-mv-time-mode="date">x</time>` +
    `<span id="both" data-mv-time="${seconds + 7200}" data-mv-time-mode="both">x</span>`);
  t.after(() => w.close()); // stops the once-a-minute relative refresh
  const doc = w.document;
  assert.equal(doc.getElementById("rel").textContent, "3 minutes ago");
  assert.equal(doc.getElementById("rel").title, "4 Oct 2026, 13:58 BST");
  assert.equal(doc.getElementById("abs").textContent, "4 Oct 2026, 14:01 BST");
  assert.equal(doc.getElementById("abs").title, "just now");
  assert.equal(doc.getElementById("day").textContent, "4 Oct 2026");
  assert.equal(doc.getElementById("both").textContent, "4 Oct 2026, 16:01 BST (in 2 hours)");
});

test("a translated interface uses its own locale, and both shared formatters agree", () => {
  const seconds = NOW / 1000;
  const fr = load("", { language: "fr" });
  const expected = new Intl.DateTimeFormat("fr-CA", { day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", timeZoneName: "short" }).format(new Date(NOW));
  assert.equal(fr.mvTimeAbsolute(seconds), expected);
  assert.equal(fr.LightworkI18n.formatDate(seconds), expected);
  assert.equal(fr.mvTimeRelative(seconds - 180), "il y a 3 minutes");
  fr.close();
  // English keeps one pattern even when the browser asks for US formatting.
  const en = load("", { language: "en" });
  assert.equal(en.LightworkI18n.formatDate(seconds), "4 Oct 2026, 14:01 BST");
  assert.equal(en.LightworkI18n.formatDate("2026-10-04"), "4 Oct 2026");
  assert.equal(en.LightworkI18n.formatDate(seconds), en.mvTimeAbsolute(seconds));
  en.close();
});

test("money reads one way: dollars and cents, separators, never a run of decimals", () => {
  const w = load();
  assert.equal(w.mvMoney(1234.5), "$1,234.50");
  assert.equal(w.mvMoney("0"), "$0.00");
  assert.equal(w.mvMoney(-5), "-$5.00");
  assert.equal(w.mvMoney(0.0004), "under $0.01");
  assert.equal(w.mvMoney(0.003, { decimals: 4 }), "$0.0030");
  assert.equal(w.mvMoney(75, { currency: "eur" }), "EUR 75.00");
  for (const missing of [null, undefined, "", "n/a", NaN, Infinity, true]) assert.equal(w.mvMoney(missing), "", String(missing));
  w.close();
  const fr = load("", { language: "fr" });
  assert.equal(fr.mvMoney(1234.5), new Intl.NumberFormat("fr-CA", { style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(1234.5));
  fr.close();
  // English pages keep "$" even when the browser prefers another English region.
  const en = load("", { language: "en" });
  assert.equal(en.mvMoney(1234.5), "$1,234.50");
  en.close();
});
