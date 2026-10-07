"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.resolve(__dirname, "../../maverick_dashboard/templates/ekko.html"), "utf8"
).replace(/\r\n/g, "\n");
const start = source.indexOf("  async function handoff(id, kind) {");
const end = source.indexOf("  async function eraseData()", start);
assert.ok(start >= 0 && end > start);

for (const kind of ["flow", "agent"]) {
  test(`Ekko ${kind} handoff carries the current workspace binding into authoring`, async () => {
    const storage = new Map();
    const window = { location: { href: "" } };
    const context = {
      Date, JSON, encodeURIComponent, window, current: { workflows_enabled: true },
      device: () => "desk",
      request: async () => ({
        kind, brief: "Review this work", context: "current-workspace",
        flow: kind === "flow" ? { start: "human-review", nodes: [{ id: "human-review", kind: "approval", next: "observed-1" }, { id: "observed-1", kind: "agent" }] } : undefined,
        redirect: kind === "flow" ? "/flows/designer" : "/workflow-builder"
      }),
      sessionStorage: { setItem: (key, value) => storage.set(key, value) },
      mvToast: message => assert.fail(message),
    };
    vm.runInNewContext(source.slice(start, end), context);
    await context.handoff("opportunity", kind);
    const handoff = JSON.parse(storage.get("lightwork.authoring-handoff"));
    assert.equal(handoff.context, "current-workspace");
    assert.equal(handoff.brief, "Review this work");
    assert.equal(handoff.kind, kind);
    if (kind === "flow") assert.equal(handoff.flow.start, "human-review");
    assert.equal(window.location.href, kind === "flow" ? "/flows/designer" : "/workflow-builder");
  });
}

test("Ekko does not open an unusable workflow draft while Workflows are off", async () => {
  const storage = new Map();
  const notices = [];
  const context = {
    Date, JSON, encodeURIComponent,
    current: { workflows_enabled: false },
    window: { location: { href: "" } },
    device: () => "desk",
    request: () => assert.fail("the handoff API must not be called"),
    sessionStorage: { setItem: (key, value) => storage.set(key, value) },
    mvToast: (message, options) => notices.push({ message, options }),
  };
  vm.runInNewContext(source.slice(start, end), context);
  await context.handoff("opportunity", "flow");
  assert.equal(storage.size, 0);
  assert.equal(context.window.location.href, "");
  assert.match(notices[0].message, /Workflows are off/);
  assert.equal(notices[0].options.error, true);
});
