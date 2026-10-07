const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const {createHash} = require('node:crypto');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');
function source(file, context = {}) {
  const module = {exports: {}};
  const compiled = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true}});
  vm.runInNewContext(compiled.outputText, {module, exports: module.exports, require, URL, URLSearchParams,
    Buffer, TextDecoder, Uint8Array, AbortController, console, setTimeout, clearTimeout, ...context});
  return module.exports;
}
function cli() {
  let tick, killed = 0, requests = 0;
  const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.kill = () => { killed++; };
  const {ownCli} = source('apps/vscode-extension/src/ownedCli.ts', {
    setTimeout(fn) { tick = fn; return 1; }, clearTimeout() {},
    require: name => name === 'child_process' ? {spawn(_cli, _args, options) {
      requests++; assert.equal(options.shell, false); return child;
    }} : require(name)});
  return {ownCli, child, deadline: () => tick(), killed: () => killed, requests: () => requests};
}
test('hung CLI is owned, rejected once as unknown and ignores late success', async () => {
  const f = cli(), lines = []; const owner = f.ownCli('fixture', [], {onLine: line => lines.push(line)});
  const result = assert.rejects(owner.result, /deadline.*unconfirmed/);
  f.deadline(); f.child.stdout.emit('data', Buffer.from('late\n')); f.child.emit('close', 0, null);
  await result; assert.equal(f.killed(), 1); assert.equal(f.requests(), 1); assert.deepEqual(lines, []);
});
test('cancel and bounded output never become a successful zero-exit acknowledgement', async () => {
  for (const operation of ['cancel', 'oversize']) {
    const f = cli(); const owner = f.ownCli('fixture', [], {maxBytes: 16});
    const result = assert.rejects(owner.result, /unconfirmed/);
    if (operation === 'cancel') owner.cancel(); else f.child.stdout.emit('data', Buffer.alloc(17));
    f.child.emit('close', 0, null); await result; assert.equal(f.killed(), 1);
  }
});
function connectionFixture({legacy = '', base = 'https://one.example', failStore = false, consent} = {}) {
  const settings = {dashboardUrl: base, dashboardToken: legacy}, secrets = new Map(), state = new Map(), warnings = [];
  const context = {secrets: {get: async key => secrets.get(key), store: async (key, value) => {
    if (failStore) throw new Error('protected store unavailable'); secrets.set(key, value);
  }, delete: async key => secrets.delete(key)}, globalState: {get: key => state.get(key), update: async (key, value) => state.set(key, value)}};
  const vscode = {ConfigurationTarget: {Global: 1}, workspace: {getConfiguration: () => ({
    get: (key, fallback) => settings[key] ?? fallback, update: async (key, value) => { settings[key] = value; }})},
    window: {showWarningMessage: async (...args) => { warnings.push(args[0]); return consent ? consent(settings, secrets) : 'Use this dashboard'; }}};
  const transport = source('apps/vscode-extension/src/eventWatch.ts');
  const {DashboardConnections} = source('apps/vscode-extension/src/dashboardConnection.ts', {
    require: name => name === 'vscode' ? vscode : name === './eventWatch' ? transport : require(name)});
  let owner; owner = new DashboardConnections(context, () => owner.dispose());
  return {owner, settings, secrets, warnings};
}
test('legacy token clears only after protected readback; failed secure save retains recovery', async () => {
  for (const failStore of [false, true]) {
    const f = connectionFixture({legacy: 'secret', failStore}); const result = await f.owner.current();
    assert.equal(result.token, 'secret'); assert.equal(f.settings.dashboardToken, failStore ? 'secret' : undefined);
    assert.equal([...f.secrets.values()].includes('secret'), !failStore);
  }
});
test('remote HTTP is rejected before token migration or consent; changing host during consent rejects', async () => {
  const unsafe = connectionFixture({legacy: 'secret', base: 'http://remote.example'});
  await assert.rejects(unsafe.owner.current(), /HTTPS/); assert.equal(unsafe.secrets.size, 0); assert.equal(unsafe.warnings.length, 0);
  const changed = connectionFixture({legacy: 'secret', consent(settings) { settings.dashboardUrl = 'https://two.example'; return 'Use this dashboard'; }});
  await assert.rejects(changed.owner.current(), /changed/);
});
test('credential rotation during destination consent cannot return the old credential', async () => {
  const f = connectionFixture({legacy: 'old', consent(_settings, secrets) {
    secrets.set('lightwork.dashboard.' + createHash('sha256').update('https://one.example').digest('hex'), 'new');
    return 'Use this dashboard';
  }});
  await assert.rejects(f.owner.current(), /credentials changed/);
});
test('external helper preserves safe rate-limit metadata and never retries a write', async () => {
  let requests = 0;
  const {Lightwork} = source('docs/clients/examples/lightwork.ts', {fetch: async () => {}});
  const client = new Lightwork('https://dashboard.example', 'secret', 100, async () => {
    requests++; return new Response('private body', {status: 429, headers: {'Retry-After': '12', 'X-Request-ID': 'req-7'}});
  });
  await assert.rejects(client.start('private title'), error => error.kind === 'rate_limit' && error.status === 429 &&
    error.retryAfterSeconds === 12 && error.requestId === 'req-7' && !error.message.includes('private'));
  assert.equal(requests, 1); client.dispose();
});
test('external helper aborts an owned stalled write without replaying it', async () => {
  let requests = 0, aborts = 0, deadline;
  const {Lightwork} = source('docs/clients/examples/lightwork.ts', {fetch: async () => {},
    setTimeout(fn) { deadline = fn; return 1; }, clearTimeout() {}});
  const client = new Lightwork('https://dashboard.example', 'secret', 100, async (_url, options) => {
    requests++; return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => { aborts++; reject(new Error('private fetch details')); }));
  });
  const pending = assert.rejects(client.start('private title'), error => error.kind === 'unknown_effect' && !error.message.includes('private'));
  deadline(); await pending; assert.equal(requests, 1); assert.equal(aborts, 1); client.dispose();
});
