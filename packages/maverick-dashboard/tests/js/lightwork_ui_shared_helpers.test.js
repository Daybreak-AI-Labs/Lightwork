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

// ---- mvFetchJSON: one JSON request helper ---------------------------------
function respond(status, body, headers = {}) {
  const text = body === undefined ? "" : (typeof body === "string" ? body : JSON.stringify(body));
  return { ok: status >= 200 && status < 300, status, headers: new Map(Object.entries(headers)),
    text: async () => text, json: async () => JSON.parse(text) };
}

test("mvFetchJSON sends JSON with the page's cookies and returns the parsed body", async () => {
  const w = load();
  const calls = [];
  w.fetch = async (url, options) => { calls.push({ url, options }); return respond(200, { saved: true }); };
  const result = await w.mvFetchJSON("/api/v1/things", { method: "POST", body: { name: "Q3" } }, "save the thing");
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { saved: true });
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(calls[0].options.body, JSON.stringify({ name: "Q3" }));
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.headers.Accept, "application/json");
  w.close();
});

test("mvFetchJSON passes strings and form data through and reads an empty body as null", async () => {
  const w = load();
  const seen = [];
  w.fetch = async (url, options) => { seen.push(options); return respond(204); };
  assert.equal(await w.mvFetchJSON("/a", { method: "POST", body: "raw=1", headers: { "Content-Type": "text/plain" } }), null);
  const form = new w.FormData(); form.append("file", "x");
  await w.mvFetchJSON("/b", { method: "POST", body: form });
  assert.equal(seen[0].body, "raw=1");
  assert.equal(seen[0].headers["Content-Type"], "text/plain");
  assert.equal(seen[1].body, form);
  assert.equal(seen[1].headers["Content-Type"], undefined);
  w.close();
});

test("a failed request rejects with plain words from mvErrorText, never the server's internals", async () => {
  const w = load();
  w.fetch = async () => respond(500, { detail: "Traceback (most recent call last): KeyError 'tenant'" });
  await assert.rejects(w.mvFetchJSON("/api/v1/x", {}, "load the report"), (error) => {
    assert.equal(error.message, "Could not load the report. Something went wrong on our side. Try again in a minute.");
    assert.equal(error.status, 500);
    assert.doesNotMatch(error.message, /Traceback|500|tenant/);
    return true;
  });
  w.fetch = async () => respond(403, { detail: "off" }, { "X-Lightwork-Reason": "feature-off", "X-Lightwork-Feature-Label": "Ekko" });
  await assert.rejects(w.mvFetchJSON("/api/v1/x", {}, "open Ekko"), (error) => {
    assert.match(error.message, /^Could not open Ekko\. Ekko is turned off\./);
    assert.ok(w.mvFeatureOff(error), "the error still tells a page the feature is off");
    return true;
  });
  w.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(w.mvFetchJSON("/api/v1/x", {}, "save"), (error) => {
    assert.equal(error.message, "Could not save. Lightwork could not be reached. Check your connection and try again.");
    assert.equal(error.status, 0);
    return true;
  });
  w.close();
});

test("a business error keeps its authored message and its payload for mvRenderError", async () => {
  const w = load();
  const detail = { schema: "lightwork.error.v1", code: "limit.too_low", message: "Pick a limit above today's spend." };
  w.fetch = async () => respond(422, { detail });
  await assert.rejects(w.mvFetchJSON("/api/v1/limits", { method: "PUT", body: {} }, "save the limit"), (error) => {
    assert.equal(error.message, "Pick a limit above today's spend.");
    assert.equal(error.payload.detail.code, "limit.too_low");
    return true;
  });
  w.close();
});

// ---- mvStatus: one inline status line --------------------------------------
test("mvStatus writes the text at once and styles errors", () => {
  const w = load("", '<p id="s"></p><p id="own" aria-live="assertive"></p>');
  const line = w.document.getElementById("s");
  w.mvStatus(line, "Saving…");
  assert.equal(line.textContent, "Saving…");
  assert.equal(line.classList.contains("mv-status--error"), false);
  w.mvStatus(line, "Could not save. Try again.", { error: true });
  assert.equal(line.textContent, "Could not save. Try again.");
  assert.equal(line.classList.contains("mv-status--error"), true);
  w.mvStatus(line, "");
  assert.equal(line.textContent, "");
  assert.equal(line.classList.contains("mv-status--error"), false);
  const own = w.document.getElementById("own");   // a page's own live region is left as it is
  w.mvStatus(own, "Done", { error: true });
  assert.equal(own.getAttribute("role"), null);
  assert.equal(own.textContent, "Done");
  assert.equal(w.mvStatus(null, "x"), null);
  w.close();
});

// A live region that appears, or gets its role, in the same moment as its
// first words is often not read out at all. mvStatus never makes the line
// itself a fresh region: it speaks through two regions that exist before
// any message (base.html, or made once here), and writes into them a moment
// after clearing them, so even the first message, and a repeat of the same
// words, is a change to a region the screen reader already knows.
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("the first message into a brand-new status line is announced through a region that already existed", async () => {
  const w = load("", '<p id="s"></p>');
  const polite = w.document.getElementById("mv-announce"), alert = w.document.getElementById("mv-announce-alert");
  assert.ok(polite && alert, "both announcers exist before the first message");
  assert.equal(polite.getAttribute("aria-live"), "polite");
  assert.equal(alert.getAttribute("aria-live"), "assertive");
  assert.equal(polite.textContent + alert.textContent, "", "they start empty");
  const line = w.document.getElementById("s");
  w.mvStatus(line, "Saved.");
  assert.equal(line.textContent, "Saved.", "the words show at once");
  assert.equal(line.hasAttribute("role") || line.hasAttribute("aria-live"), false, "the line is not made a fresh live region");
  assert.equal(polite.textContent, "", "the announcer is cleared first");
  await wait(150);
  assert.equal(polite.textContent, "Saved.");
  w.mvStatus(line, "Saved.");   // the same words again are spoken again
  assert.equal(polite.textContent, "");
  await wait(150);
  assert.equal(polite.textContent, "Saved.");
  w.mvStatus(line, "Could not save. Try again.", { error: true });
  await wait(150);
  assert.equal(alert.textContent, "Could not save. Try again.", "an error is spoken at once");
  assert.equal(polite.textContent, "", "and the polite region does not repeat it");
  w.close();
});

test("only the latest of quick messages is spoken, and clearing the line speaks nothing", async () => {
  const w = load("", '<p id="s"></p>');
  const polite = w.document.getElementById("mv-announce");
  const line = w.document.getElementById("s");
  w.mvStatus(line, "Saving…");
  w.mvStatus(line, "Saved.");
  await wait(150);
  assert.equal(polite.textContent, "Saved.");
  w.mvStatus(line, "");
  await wait(150);
  assert.equal(polite.textContent, "Saved.", "an empty line is not announced");
  w.close();
});

test("announcers already in the page (base.html) are reused, not duplicated", async () => {
  const w = load("", '<div id="mv-announce" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div><p id="s"></p>');
  assert.equal(w.document.querySelectorAll("#mv-announce").length, 1);
  w.mvStatus(w.document.getElementById("s"), "Done.");
  await wait(150);
  assert.equal(w.document.querySelector("#mv-announce").textContent, "Done.");
  w.close();
});

test("an unreachable server gives the same words whether the error or its cause is passed on", async () => {
  const w = load();
  w.fetch = async () => { throw new TypeError("Failed to fetch"); };
  const error = await w.mvFetchJSON("/x", {}, "save").catch((e) => e);
  assert.equal(w.mvErrorText(error, "save"), error.message);
  assert.equal(error.name, "TypeError");
  w.close();
});
