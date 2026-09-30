"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function harness() {
  let now = 0, next = 0;
  const timers = new Map(), calls = [], navigation = [], handlers = {};
  const nodes = Object.fromEntries(["status-text", "trouble", "retry", "diagnostics"].map(id => [id, {
    style: {}, textContent: "", addEventListener(event, handler) { handlers[id + ":" + event] = handler; }
  }]));
  const context = {
    performance: { now: () => now }, AbortController,
    setTimeout(fn, delay) { const id = ++next; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) { return new Promise((resolve, reject) => calls.push({url, options, resolve, reject})); },
    document: {getElementById: id => nodes[id]}, navigator: {},
    window: {addEventListener(event, handler) { handlers[event] = handler; },
      location: {replace(url) { navigation.push(url); }}, __TAURI__: {core: {invoke: async () => "Starting"}}}
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../ui/app.js"), "utf8"), context);
  async function flush() { await Promise.resolve(); await Promise.resolve(); }
  async function advance(ms) {
    const end = now + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([,value]) => value.at <= end).sort((a,b) => a[1].at-b[1].at)[0];
      if (!due) break;
      now = due[1].at; timers.delete(due[0]); due[1].fn(); await flush();
    }
    now = end; await flush();
  }
  return {nodes, calls, navigation, handlers, timers, advance, flush};
}

test("never-settling probes abort and expose recovery by elapsed six seconds", async () => {
  const app = harness();
  await app.advance(6000);
  assert.equal(app.nodes.trouble.style.display, "block");
  assert.ok(app.calls.length >= 2);
  assert.equal(app.calls[0].options.signal.aborted, true);
  assert.notEqual(app.nodes.retry.disabled, true);
  app.calls[0].resolve({status: 200}); await app.flush();
  assert.deepEqual(app.navigation, []);
});

test("old success cannot navigate after retry but current usable onboarding does", async () => {
  const app = harness();
  const old = app.calls[0];
  await app.handlers["retry:click"]();
  assert.equal(old.options.signal.aborted, true);
  old.resolve({status: 200}); await app.flush();
  assert.deepEqual(app.navigation, []);
  // no-cors returns an opaque response for the usable unconfigured 503.
  app.calls.at(-1).resolve({status: 0, type: "opaque"}); await app.flush();
  assert.deepEqual(app.navigation, ["http://127.0.0.1:8765/"]);
  await app.advance(10000);
  assert.equal(app.navigation.length, 1);
  assert.equal(app.timers.size, 0);
});

test("connection refusal retries and a slow startup recovers once", async () => {
  const app = harness();
  app.calls[0].reject(new Error("connection refused")); await app.flush();
  await app.advance(600);
  assert.equal(app.calls.length, 2);
  app.calls[1].resolve({}); await app.flush();
  assert.equal(app.navigation.length, 1);
  assert.equal(app.calls[1].options.mode, "no-cors");
});

test("leaving splash cancels timers and old response authority", async () => {
  const app = harness();
  app.handlers.pagehide();
  app.calls[0].resolve({}); await app.flush();
  await app.advance(10000);
  assert.deepEqual(app.navigation, []);
  assert.equal(app.calls[0].options.signal.aborted, true);
  assert.equal(app.calls.length, 1);
});
