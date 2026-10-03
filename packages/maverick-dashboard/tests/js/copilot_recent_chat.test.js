"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const base = path.resolve(__dirname, "../../maverick_dashboard");
const read = name => fs.readFileSync(path.join(base, name), "utf8");
const key = "lightwork:copilot:recent:v1";
const tick = async () => { await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve)); };
const response = body => ({ok: true, status: 200, redirected: false, json: async () => body});

function harness(saved) {
  const dom = new JSDOM(read("templates/_copilot.html"), {
    url: "http://localhost/start", runScripts: "outside-only"
  });
  const w = dom.window, d = w.document, calls = [], state = {scope: "a".repeat(64)};
  if (saved) w.sessionStorage.setItem(key, saved);
  w.fetch = async (url, options = {}) => {
    calls.push({url, body: options.body ? JSON.parse(options.body) : null});
    if (url.includes("/copilot/context")) return response({client_context: state.scope, boundaries: [], suggested_questions: []});
    if (url === "/api/v1/copilot") return response({reply: "Answer to " + JSON.parse(options.body).message, mode: "built_in"});
    throw Error("Unexpected request " + url);
  };
  w.eval(read("static/lightwork-copilot.js"));
  const get = id => d.getElementById(id);
  return {dom, w, calls, state, get,
    async open() { get("mv-copilot-fab").click(); await tick(); },
    async ask(value) {
      get("mv-copilot-input").value = value;
      get("mv-copilot-form").dispatchEvent(new w.Event("submit", {cancelable: true}));
      await tick();
    },
    close() { dom.window.close(); }
  };
}

test("recent Copilot chat returns on another page in the same scoped tab", async () => {
  const first = harness();
  await first.open(); await first.ask("What is next?");
  const saved = first.w.sessionStorage.getItem(key);
  assert.equal(JSON.parse(saved).turns.length, 2);
  first.close();

  const next = harness(saved);
  await next.open();
  assert.match(next.get("mv-copilot-log").textContent, /What is next\?Answer to What is next\?/);
  await next.ask("And after that?");
  assert.deepEqual(next.calls.filter(call => call.url === "/api/v1/copilot").at(-1).body.history,
    [{role: "user", content: "What is next?"}]);
  next.close();
});

test("a changed account scope never restores the prior chat", async () => {
  const first = harness(); await first.open(); await first.ask("Private question");
  const saved = first.w.sessionStorage.getItem(key); first.close();
  const next = harness(saved); next.state.scope = "b".repeat(64);
  await next.open();
  assert.equal(next.get("mv-copilot-log").textContent, "");
  assert.equal(next.w.sessionStorage.getItem(key), null);
  await next.ask("New account");
  assert.deepEqual(next.calls.filter(call => call.url === "/api/v1/copilot").at(-1).body.history, []);
  next.close();
});

test("Clear recent chat removes this tab's transcript and prompt context", async () => {
  const h = harness(); await h.open(); await h.ask("First question");
  h.get("mv-copilot-clear").click();
  assert.equal(h.get("mv-copilot-log").textContent, "");
  assert.equal(h.w.sessionStorage.getItem(key), null);
  await h.ask("Second question");
  assert.deepEqual(h.calls.filter(call => call.url === "/api/v1/copilot").at(-1).body.history, []);
  h.close();
});

test("expired local chat is removed before any text is shown", async () => {
  const old = JSON.stringify({scope: "a".repeat(64), at: Date.now() - 3600001,
    turns: [{role: "user", text: "Expired private question"}]});
  const h = harness(old); await h.open();
  assert.equal(h.get("mv-copilot-log").textContent, "");
  assert.equal(h.w.sessionStorage.getItem(key), null);
  h.close();
});

test("expired saved chat is removed even when the panel is never opened", () => {
  const old = JSON.stringify({scope: "a".repeat(64), at: Date.now() - 3600001,
    turns: [{role: "user", text: "Private question"}]});
  const h = harness(old);
  assert.equal(h.w.sessionStorage.getItem(key), null);
  h.close();
});

test("an idle open panel drops old context before the next question", async () => {
  const h = harness(); await h.open(); await h.ask("Old question");
  h.w.Date.now = () => Date.now() + 3600001;
  await h.ask("Fresh question");
  assert.deepEqual(h.calls.filter(call => call.url === "/api/v1/copilot").at(-1).body.history, []);
  assert.doesNotMatch(h.get("mv-copilot-log").textContent, /Old question/);
  h.close();
});

test("an open panel removes expired chat when the tab becomes active", async () => {
  const h = harness(); await h.open(); await h.ask("Old private question");
  h.w.Date.now = () => Date.now() + 3600001;
  h.w.dispatchEvent(new h.w.Event("focus"));
  assert.equal(h.get("mv-copilot-log").textContent, "");
  assert.equal(h.w.sessionStorage.getItem(key), null);
  h.close();
});

test("changing selected work discards an in-flight reply and its draft", async () => {
  const h = harness(); await h.open();
  let finish;
  const original = h.w.fetch;
  h.w.fetch = (url, options) => url === "/api/v1/copilot"
    ? new Promise(resolve => { finish = () => resolve(response({reply: "Old selected work", mode: "built_in"})); })
    : original(url, options);
  h.get("mv-copilot-input").value = "Question about old selection";
  h.get("mv-copilot-form").dispatchEvent(new h.w.Event("submit", {cancelable: true}));
  await tick();
  assert.equal(typeof finish, "function");
  h.w.document.dispatchEvent(new h.w.Event("lightwork:copilot-context-changed"));
  finish(); await tick();
  assert.equal(h.get("mv-copilot-log").textContent, "");
  assert.equal(h.get("mv-copilot-input").value, "");
  assert.equal(h.get("mv-copilot-send").disabled, false);
  assert.equal(h.w.sessionStorage.getItem(key), null);
  h.close();
});

test("a typed control proposal remains in recent chat without replaying assistant text", async () => {
  const h = harness();
  h.w.LightworkProductOperations = {open() {}, isBusy: () => false, propose: async () => true};
  await h.open(); await h.ask("Review the selected order");
  const saved = JSON.parse(h.w.sessionStorage.getItem(key));
  assert.deepEqual(saved.turns.map(turn => turn.role), ["user", "bot"]);
  assert.match(saved.turns[1].text, /Nothing has been changed/);
  assert.equal(h.get("mv-copilot-input").value, "");
  h.w.LightworkProductOperations = null;
  await h.ask("What else should I check?");
  const followup = h.calls.filter(call => call.url === "/api/v1/copilot").at(-1).body;
  assert.deepEqual(followup.history, [{role: "user", content: "Review the selected order"}]);
  h.close();
});

test("a failed control suggestion keeps the question and never claims a review opened", async () => {
  const h = harness();
  h.w.LightworkProductOperations = {open() {}, isBusy: () => false, propose: async () => 'unavailable'};
  await h.open(); await h.ask('Review the selected order');
  assert.equal(h.get('mv-copilot-input').value, 'Review the selected order');
  assert.doesNotMatch(h.get('mv-copilot-log').textContent, /Review the selected control/);
  assert.match(h.get('mv-copilot-note').textContent, /could not open the current control/);
  assert.equal(h.w.sessionStorage.getItem(key), null);
  assert.equal(h.calls.filter(call => call.url === '/api/v1/copilot').length, 0);
  h.close();
});
