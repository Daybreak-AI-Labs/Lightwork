const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const base = path.join(__dirname, '../../maverick_dashboard');
const html = fs.readFileSync(path.join(base, 'templates/workspace_recovery.html'), 'utf8');
const source = fs.readFileSync(path.join(base, 'static/lightwork-workspace-recovery.js'), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

async function harness(handler, overrides = {}) {
  const dom = new JSDOM(html, { url: 'http://localhost/workspace/recovery', runScripts: 'outside-only' });
  const calls = [];
  const state = { tenant: 'acme', context: 'account-context', expected_tenant: 'acme', target: 'target-a', application_version: '1.0.0', key_id: 'key-id',
    coverage: ['Local workspace files'], excluded: ['External stores'], restore_blockers: [], live_restore_available: true,
    snapshots: [{ id: 'snapshot-a', bytes: 1024 }], max_archive_bytes: 10000, ...overrides };
  dom.window.fetch = async (url, init) => {
    calls.push({ url, init });
    const body = url.endsWith('/status') ? state : await handler(url, init);
    return { ok: !body.error, json: async () => body.error ? { detail: body.error } : JSON.parse(JSON.stringify(body)) };
  };
  dom.window.eval(source);
  await tick(); await tick();
  const get = name => dom.window.document.getElementById('wrec-' + name);
  function select() { get('snapshot').value = 'snapshot-a'; get('snapshot').dispatchEvent(new dom.window.Event('change')); }
  return { dom, calls, get, state, select };
}

test('restore needs prepared target-bound review and explicit checkbox; changing selection invalidates it', async () => {
  const h = await harness(async url => url.endsWith('/prepare') ? { review: 'review-a', target: 'target-a', tenant: 'acme', application_version: '1.0.0', file_count: 3 } : {});
  h.select();
  h.get('prepare').click(); await tick();
  assert.equal(h.get('restore').disabled, true);
  assert.equal(h.calls.some(c => c.url.endsWith('/restore')), false);
  h.get('confirm').checked = true; h.get('confirm').dispatchEvent(new h.dom.window.Event('change'));
  assert.equal(h.get('restore').disabled, false);
  h.select();
  assert.equal(h.get('restore').disabled, true);
  assert.equal(h.get('review').hidden, true);
  h.dom.window.close();
});

test('failed restore consumes displayed review and keeps the failure visible', async () => {
  const h = await harness(async url => url.endsWith('/prepare') ? { review: 'review-a', target: 'target-a', tenant: 'acme', application_version: '1.0.0', file_count: 3 } : { error: 'Workspace files changed. Prepare a fresh review.' });
  h.select(); h.get('prepare').click(); await tick();
  h.get('confirm').checked = true; h.get('confirm').dispatchEvent(new h.dom.window.Event('change'));
  h.get('restore').click(); await tick();
  assert.match(h.get('status').textContent, /Workspace files changed/);
  assert.equal(h.get('restore').disabled, true);
  const call = h.calls.find(c => c.url.endsWith('/restore'));
  assert.deepEqual(JSON.parse(call.init.body), { review: 'review-a', target: 'target-a', confirm: true });
  assert.equal(call.init.headers['X-Lightwork-Recovery-Context'], 'account-context');
  h.dom.window.close();
});

test('normal dashboard never offers live restore and coverage is text-only', async () => {
  const h = await harness(async () => ({}), { live_restore_available: false, coverage: ['<img src=x onerror=alert(1)>'] });
  h.select();
  assert.equal(h.get('prepare').disabled, true);
  assert.equal(h.get('installation-authorize').disabled, true);
  assert.equal(h.get('coverage').querySelector('img'), null);
  assert.match(h.get('coverage').textContent, /<img/);
  h.dom.window.close();
});

test('whole-home maintenance needs separate consent and returns only bounded private grant', async () => {
  const authorization = 'a'.repeat(32) + ':' + 'b'.repeat(64);
  const h = await harness(async url => url.endsWith('/authorize') ? { authorization } : { recovery_points: [] });
  assert.equal(h.get('installation-authorize').disabled, true);
  h.get('installation-confirm').checked = true;
  h.get('installation-confirm').dispatchEvent(new h.dom.window.Event('change'));
  h.get('installation-authorize').click(); await tick();
  const call = h.calls.find(c => c.url.endsWith('/authorize'));
  assert.deepEqual(JSON.parse(call.init.body), { confirm: true, confirm_all_workspaces: true });
  assert.equal(h.get('installation-code').value, authorization);
  assert.equal(h.get('installation-confirm').checked, false);
  assert.equal(h.calls.some(c => c.url.includes('execute')), false);
  h.state.context = 'different-account';
  h.get('refresh').click(); await tick(); await tick();
  assert.equal(h.get('installation-code').value, '');
  h.dom.window.close();
});

test('wrong-target prepare response cannot create actionable restore controls', async () => {
  const h = await harness(async () => ({ review: 'review-a', target: 'other-workspace' }));
  h.select(); h.get('prepare').click(); await tick();
  assert.match(h.get('status').textContent, /does not match/);
  assert.equal(h.get('restore').disabled, true);
  assert.equal(h.get('review').hidden, true);
  h.dom.window.close();
});

test('shared encryption keys need separate consent and unresolved restore remains visible', async () => {
  const h = await harness(async () => ({}), { encryption_custody: { local_export_available: true, detail: 'Shared keys require separate custody.' }, last_restore: { state: 'completion_unconfirmed' } });
  assert.equal(h.get('encryption-download').disabled, true);
  h.get('encryption-confirm').checked = true;
  h.get('encryption-confirm').dispatchEvent(new h.dom.window.Event('change'));
  assert.equal(h.get('encryption-download').disabled, false);
  assert.match(h.get('last-restore').textContent, /does not have a verified completion receipt/);
  assert.match(h.get('encryption-status').textContent, /separate custody/);
  h.dom.window.close();
});

test('installation retirement requires exact selected point and fresh confirmation; refresh clears consent', async () => {
  const points = [
    { id: 'a'.repeat(32), status: 'authorized', old_version: '1.0.0', can_retire: true, record_sha256: 'b'.repeat(64) },
    { id: 'c'.repeat(32), status: 'upgraded', old_version: '1.0.0', new_version: '1.0.1', can_retire: false, retirement_blocker: 'This protects the installed application.' }
  ];
  const h = await harness(async url => url.endsWith('/installation') ? { recovery_points: points } : { retired: true });
  assert.equal(h.get('installation-retire').disabled, true);
  assert.equal(h.get('installation-retire-point').options.length, 2);
  assert.match(h.get('installation-retention-list').textContent, /protects the installed application/);
  h.get('installation-retire-point').value = 'a'.repeat(32);
  h.get('installation-retire-confirm').checked = true;
  h.get('installation-retire-confirm').dispatchEvent(new h.dom.window.Event('change'));
  h.get('installation-retire-point').dispatchEvent(new h.dom.window.Event('change'));
  assert.equal(h.get('installation-retire').disabled, true);
  h.get('installation-retire-confirm').checked = true;
  h.get('installation-retire-confirm').dispatchEvent(new h.dom.window.Event('change'));
  h.get('refresh').click(); await tick(); await tick();
  assert.equal(h.get('installation-retire-confirm').checked, false);
  h.get('installation-retire-point').value = 'a'.repeat(32);
  h.get('installation-retire-confirm').checked = true;
  h.get('installation-retire-confirm').dispatchEvent(new h.dom.window.Event('change'));
  h.get('installation-retire').click(); h.get('installation-retire').click(); await tick(); await tick();
  const calls = h.calls.filter(call => call.url.includes('/installation/') && call.url.endsWith('/retire'));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/recovery/installation/' + 'a'.repeat(32) + '/retire');
  assert.deepEqual(JSON.parse(calls[0].init.body), { confirm: true, sha256: 'b'.repeat(64) });
  assert.equal(h.get('installation-retire-confirm').checked, false);
  h.dom.window.close();
});

test('retirement failure stays visible and cannot automatically retry destructive action', async () => {
  const h = await harness(async url => url.endsWith('/installation') ? { recovery_points: [{ id: 'a'.repeat(32), status: 'authorized', old_version: '1.0.0', can_retire: true, record_sha256: 'b'.repeat(64) }] } : { error: 'This point became active. Finish recovery first.' });
  h.get('installation-retire-point').value = 'a'.repeat(32);
  h.get('installation-retire-confirm').checked = true;
  h.get('installation-retire-confirm').dispatchEvent(new h.dom.window.Event('change'));
  h.get('installation-retire').click(); await tick();
  assert.match(h.get('status').textContent, /became active/);
  assert.equal(h.get('installation-retire-confirm').checked, false);
  assert.equal(h.get('installation-retire').disabled, true);
  h.dom.window.close();
});
