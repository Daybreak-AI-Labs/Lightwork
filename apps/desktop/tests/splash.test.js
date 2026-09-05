"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function splash(copyResult, invoke) {
  const handlers = {};
  const nodes = Object.fromEntries(
    ["status-text", "trouble", "retry", "copy", "copied", "diagnostics", "refresh-details"].map((id) => [id, {
      style: {},
      textContent: id === "copied" ? "Copied" : "",
      addEventListener(name, handler) { handlers[`${id}:${name}`] = handler; },
    }]),
  );
  const appended = [];
  const context = {
    window: { location: { replace() {} }, __TAURI__: invoke ? { core: { invoke } } : undefined },
    navigator: {},
    fetch: () => new Promise(() => {}),
    setTimeout: () => 1,
    clearTimeout() {},
    document: {
      getElementById: (id) => nodes[id],
      createElement: () => ({ style: {}, select() {} }),
      execCommand: copyResult,
      body: {
        appendChild(node) { appended.push(node); },
        removeChild(node) { appended.splice(appended.indexOf(node), 1); },
      },
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../ui/app.js"), "utf8"), context,
  );
  return { clickCopy: handlers["copy:click"], feedback: nodes.copied, appended, handlers, nodes };
}

test("fallback clipboard rejection reports failure and removes temporary input", () => {
  const app = splash(() => false);
  app.clickCopy();
  assert.match(app.feedback.textContent, /Could not copy/);
  assert.equal(app.feedback.style.opacity, "1");
  assert.equal(app.appended.length, 0);
});

test("fallback clipboard exception remains visible and cleans up", () => {
  const app = splash(() => { throw new Error("clipboard unavailable"); });
  app.clickCopy();
  assert.match(app.feedback.textContent, /Could not copy/);
  assert.equal(app.appended.length, 0);
});

test("successful fallback copy confirms the result", () => {
  const app = splash(() => true);
  app.clickCopy();
  assert.equal(app.feedback.textContent, "Copied");
  assert.equal(app.feedback.style.opacity, "1");
  assert.equal(app.appended.length, 0);
});

test("retry invokes the native launcher once while the request is pending", async () => {
  const calls = [];
  let resolve;
  const app = splash(() => true, (command) => {
    calls.push(command);
    return new Promise((done) => { resolve = done; });
  });
  const pending = app.handlers["retry:click"]();
  assert.equal(app.nodes.retry.disabled, true);
  await app.handlers["retry:click"]();
  assert.deepEqual(calls, ["retry_dashboard"]);
  resolve("Starting");
  await pending;
  assert.equal(app.nodes.retry.disabled, false);
});

test("native launch failure keeps retry available and preserves diagnostic details", async () => {
  const app = splash(() => true, () => Promise.reject("synthetic executable missing"));
  await app.handlers["retry:click"]();
  assert.match(app.nodes["status-text"].textContent, /could not start/);
  assert.doesNotMatch(app.nodes["status-text"].textContent, /executable/);
  assert.equal(app.nodes.diagnostics.textContent, "synthetic executable missing");
  assert.equal(app.nodes.trouble.style.display, "block");
  assert.equal(app.nodes.retry.disabled, false);
});

test("startup details are loaded only by the explicit in-app action", async () => {
  const calls = [];
  const app = splash(() => true, (command) => {
    calls.push(command);
    return Promise.resolve("Synthetic startup output");
  });
  assert.deepEqual(calls, []);
  await app.handlers["refresh-details:click"]();
  assert.deepEqual(calls, ["dashboard_diagnostics"]);
  assert.equal(app.nodes.diagnostics.textContent, "Synthetic startup output");
});
