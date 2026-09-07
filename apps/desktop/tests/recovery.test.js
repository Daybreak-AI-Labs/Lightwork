const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../ui/recovery.js'), 'utf8');

function harness(invoke) {
  const nodes = new Map(), destinations = [];
  function get(id) { if (!nodes.has(id)) nodes.set(id, { disabled: false, textContent: '', listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; } }); return nodes.get(id); }
  vm.runInNewContext(source, { document: { getElementById: get }, window: { __TAURI__: { core: { invoke } }, location: { replace(url) { destinations.push(url); } } }, fetch: async () => ({}), setTimeout });
  return { get, destinations };
}

test('recovery mode starts only on explicit action and uses fixed destination', async () => {
  const calls = [];
  const h = harness(async (command, args) => calls.push({ command, ...args }));
  assert.equal(calls.length, 0);
  await h.get('enter-recovery').listeners.click();
  assert.equal(calls[0].command, 'switch_recovery_mode');
  assert.equal(calls[0].recovery, true);
  assert.equal(calls[0].confirm, true);
  assert.deepEqual(h.destinations, ['http://127.0.0.1:8765/workspace/recovery']);
});

test('an independent service refusal stays visible and never navigates as if isolated', async () => {
  const h = harness(async () => { throw new Error('The workspace is managed outside this app.'); });
  await h.get('enter-recovery').listeners.click();
  assert.equal(h.destinations.length, 0);
  assert.match(h.get('details').textContent, /managed outside/);
  assert.equal(h.get('enter-recovery').disabled, false);
});

test('return to work chooses the fixed normal mode', async () => {
  const calls = [];
  const h = harness(async (command, args) => calls.push(args.recovery));
  await h.get('leave-recovery').listeners.click();
  assert.deepEqual(calls, [false]);
  assert.deepEqual(h.destinations, ['http://127.0.0.1:8765/']);
});
