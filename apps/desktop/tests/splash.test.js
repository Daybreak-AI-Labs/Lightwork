"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function splash(copyResult) {
  const handlers = {};
  const nodes = Object.fromEntries(
    ["status-text", "trouble", "retry", "copy", "copied"].map((id) => [id, {
      style: {},
      textContent: id === "copied" ? "Copied" : "",
      addEventListener(name, handler) { handlers[`${id}:${name}`] = handler; },
    }]),
  );
  const appended = [];
  const context = {
    window: { location: { replace() {} } },
    navigator: {},
    fetch: () => new Promise(() => {}),
    setTimeout: () => 1,
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
  return { clickCopy: handlers["copy:click"], feedback: nodes.copied, appended };
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
