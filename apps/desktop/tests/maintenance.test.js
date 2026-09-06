const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function render(invoke) {
  const elements = Object.fromEntries([
    "installed-version", "maintenance-status", "maintenance-details", "check-update",
    "repair-install", "install-update", "update-review", "update-version", "update-notes", "back",
    "maintenance-authorization", "maintenance-incomplete", "maintenance-recovery-result", "rollback-install", "resume-maintenance",
    "maintenance-logs", "maintenance-log-point",
  ].map(id => [id, { textContent: "", value: "", hidden: false, disabled: false, handlers: {},
    addEventListener(name, handler) { this.handlers[name] = handler; } }]));
  const context = { document: { getElementById: id => elements[id] },
    window: { __TAURI__: { core: { invoke } }, location: { replace() {} } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../ui/maintenance.js"), "utf8"), context);
  return elements;
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test("maintenance checks never install without an explicit review action", async () => {
  const calls = [];
  const elements = render(async (command, payload) => {
    calls.push([command, payload]);
    if (command === "maintenance_status") return { version: "1.0.0", updates_configured: true };
    if (command === "check_desktop_update") return {
      review: "7", version: "1.0.1", current_version: "1.0.0", notes: "<img src=x onerror=bad()>", repair: false,
    };
  });
  await settle();
  await elements["check-update"].handlers.click();
  assert.equal(calls.length, 2);
  assert.equal(elements["update-notes"].textContent, "<img src=x onerror=bad()>");
  assert.equal(elements["install-update"].disabled, true);
  elements["maintenance-authorization"].value = "a".repeat(32) + ":" + "b".repeat(64);
  elements["maintenance-authorization"].handlers.input();
  await elements["install-update"].handlers.click();
  assert.equal(calls[2][0], "install_desktop_update");
  assert.deepEqual(JSON.parse(JSON.stringify(calls[2][1])), { review: "7", confirm: true, authorization: "a".repeat(32) + ":" + "b".repeat(64) });
});

test("unconfigured customer channel cannot offer a pretend update or repair", async () => {
  const elements = render(async () => ({ version: "1.0.0", updates_configured: false }));
  await settle();
  assert.equal(elements["check-update"].disabled, true);
  assert.equal(elements["repair-install"].disabled, true);
  assert.match(elements["maintenance-status"].textContent, /administrator/);
});

test("failed install discards the stale review and permits a fresh check", async () => {
  const elements = render(async command => {
    if (command === "maintenance_status") return { version: "1.0.0", updates_configured: true };
    if (command === "check_desktop_update") return { review: "1", version: "1.0.0", repair: true, notes: "" };
    throw new Error("signature mismatch");
  });
  await settle();
  await elements["repair-install"].handlers.click();
  await elements["install-update"].handlers.click();
  assert.equal(elements["update-review"].hidden, true);
  assert.equal(elements["check-update"].disabled, false);
  assert.match(elements["maintenance-status"].textContent, /could not/);
  assert.match(elements["maintenance-details"].textContent, /signature mismatch/);
});

test("double clicks cannot queue another maintenance operation", async () => {
  let finish;
  let checks = 0;
  const elements = render(async command => {
    if (command === "maintenance_status") return { version: "1.0.0", updates_configured: true };
    checks += 1;
    return new Promise(resolve => { finish = resolve; });
  });
  await settle();
  const pending = elements["check-update"].handlers.click();
  await elements["repair-install"].handlers.click();
  assert.equal(checks, 1);
  finish(null);
  await pending;
});
