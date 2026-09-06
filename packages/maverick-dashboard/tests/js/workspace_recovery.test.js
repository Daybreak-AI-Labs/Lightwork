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
    return { ok: !body.error, status: body.error ? 409 : 200,
      json: async () => body.error ? { detail: body.error } : JSON.parse(JSON.stringify(body)),
      blob: async () => new dom.window.Blob([JSON.stringify(body)], { type: 'application/json' }) };
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

for (const deniedStatus of [401, 403]) {
  test(`HTML ${deniedStatus} removes private recovery state before parsing and blocks further actions`, async () => {
    const authorization = 'a'.repeat(32) + ':' + 'b'.repeat(64);
    const h = await harness(async url => url.endsWith('/authorize') ? { authorization } : { recovery_points: [
      { id: 'c'.repeat(32), status: 'authorized', old_version: '1.0.0', can_retire: true, record_sha256: 'd'.repeat(64) }
    ] });
    h.select();
    h.get('installation-confirm').checked = true;
    h.get('installation-confirm').dispatchEvent(new h.dom.window.Event('change'));
    h.get('installation-authorize').click(); await tick();
    assert.equal(h.get('installation-code').value.length, 97);
    h.get('key-input').value = 'synthetic private recovery key';
    h.get('result').textContent = 'Private snapshot verification';
    h.get('retire-confirm').checked = true;
    let requests = 0, parsed = 0;
    h.dom.window.fetch = async () => {
      requests++;
      return { ok: false, status: deniedStatus, json: async () => { parsed++; throw new Error('HTML sign-in response'); } };
    };
    h.get('inspect').click(); await tick();
    assert.equal(parsed, 0, 'Access denial must clear state without parsing an HTML response.');
    for (const field of ['installation-code', 'key-input']) assert.equal(h.get(field).value, '');
    for (const field of ['identity', 'result', 'installation-result', 'installation-retention-list']) assert.equal(h.get(field).textContent, '');
    assert.equal(h.get('snapshot').options.length, 0);
    assert.equal(h.get('installation-retire-point').options.length, 0);
    assert.equal(h.get('retire-confirm').checked, false);
    assert.match(h.get('status').textContent, /reload/i);
    for (const button of h.dom.window.document.querySelectorAll('#workspace-recovery button')) assert.equal(button.disabled, true);
    h.get('refresh').dispatchEvent(new h.dom.window.Event('click'));
    h.get('create').dispatchEvent(new h.dom.window.Event('click'));
    await tick();
    assert.equal(requests, 1, 'Even programmatic clicks cannot reuse cached authority.');
    h.dom.window.close();
  });
}

test('service interruption preserves safe inputs and selection but invalidates all confirmations', async () => {
  const h = await harness(async url => url.endsWith('/prepare') ? { review: 'review-a', target: 'target-a', tenant: 'acme', application_version: '1.0.0', file_count: 3 } : {});
  h.select(); h.get('prepare').click(); await tick();
  h.get('key-input').value = 'synthetic pending recovery key';
  for (const name of ['confirm', 'retire-confirm', 'encryption-confirm', 'installation-confirm', 'installation-retire-confirm']) h.get(name).checked = true;
  h.dom.window.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Synthetic temporary storage interruption' }) });
  h.get('inspect').click(); await tick();
  assert.equal(h.get('key-input').value, 'synthetic pending recovery key');
  assert.equal(h.get('snapshot').value, 'snapshot-a');
  assert.match(h.get('status').textContent, /temporary storage interruption/);
  for (const name of ['confirm', 'retire-confirm', 'encryption-confirm', 'installation-confirm', 'installation-retire-confirm']) assert.equal(h.get(name).checked, false);
  assert.equal(h.get('review').hidden, true);
  assert.equal(h.get('restore').disabled, true);
  assert.equal(h.get('refresh').disabled, false);
  h.dom.window.close();
});

function comparison(offset = 0, overrides = {}) {
  const total = 51, hash = 'a'.repeat(64);
  return { report_id: 'report-a', target: 'target-a', snapshot: { id: 'snapshot-a', sha256: hash, application_version: '1.0.0', compatibility: 'compatible' },
    compared_at: 1700000000, application_version: '1.0.0', total_changes: total, protected_changes: 2,
    live_file_count: 60, snapshot_file_count: 9, live_state_sha256: 'b'.repeat(64), snapshot_state_sha256: 'c'.repeat(64),
    offset, limit: 50, has_more: offset === 0, restore_authorized: false, isolated: false,
    items: Array.from({ length: Math.min(50, total - offset) }, (_, index) => ({
      path: index + offset === 0 ? '<img src=x onerror=alert(1)>.json' : 'records/file-' + (offset + index) + '.json',
      change: 'added', protected: index + offset < 2, snapshot: null, current: { sha256: hash, size: 32 }
    })), ...overrides };
}

test('comparison pages and downloads the exact captured report without enabling restore or treating metadata as HTML', async () => {
  const h = await harness(async (url, init) => {
    if (url.endsWith('/compare')) return comparison();
    if (url.endsWith('/comparison')) return comparison(JSON.parse(init.body).offset);
    return {};
  }, { live_restore_available: false });
  assert.equal(h.get('compare').disabled, true);
  h.select(); h.get('compare').click(); await tick();
  assert.equal(h.get('comparison-report').hidden, false);
  assert.match(h.get('comparison-summary').textContent, /51.*2/);
  assert.equal(h.get('comparison-items').children.length, 50);
  assert.match(h.get('comparison-items').textContent, /<img/);
  assert.equal(h.get('comparison-items').querySelector('img'), null);
  assert.equal(h.get('prepare').disabled, true);
  assert.equal(h.get('comparison-previous').disabled, true);
  h.get('comparison-next').click(); await tick();
  assert.equal(h.get('comparison-items').children.length, 1);
  assert.match(h.get('comparison-status').textContent, /51.*51/);
  assert.equal(h.get('comparison-next').disabled, true);
  const next = h.calls.find(call => call.url.endsWith('/comparison'));
  assert.deepEqual(JSON.parse(next.init.body), { report_id: 'report-a', offset: 50, limit: 50 });
  assert.equal(next.init.headers['X-Lightwork-Recovery-Context'], 'account-context');
  let downloaded = '';
  h.dom.window.URL.createObjectURL = () => 'blob:synthetic-comparison';
  h.dom.window.URL.revokeObjectURL = () => {};
  h.dom.window.HTMLAnchorElement.prototype.click = function () { downloaded = this.download; };
  h.get('comparison-download').click(); await tick();
  assert.match(downloaded, /comparison.*\.json$/);
  const download = h.calls.find(call => call.url.endsWith('/comparison/download'));
  assert.deepEqual(JSON.parse(download.init.body), { report_id: 'report-a' });
  assert.equal(h.calls.some(call => /\/(prepare|restore|authorize)$/.test(call.url)), false);
  h.dom.window.close();
});

test('comparison rejects changed target, report identity or authority claims and cannot download stale evidence', async () => {
  for (const invalid of [{ target: 'other-target' }, { report_id: 'report-b' }, { live_state_sha256: 'd'.repeat(64) }, { restore_authorized: true }]) {
    const h = await harness(async url => url.endsWith('/compare') ? comparison() : url.endsWith('/comparison') ? comparison(50, invalid) : {});
    h.select(); h.get('compare').click(); await tick();
    h.get('comparison-next').click(); await tick();
    assert.equal(h.get('comparison-report').hidden, true);
    assert.equal(h.get('comparison-download').disabled, true);
    assert.equal(h.get('comparison-items').children.length, 0);
    assert.match(h.get('status').textContent, /comparison.*(changed|verified|match)/i);
    h.dom.window.close();
  }
});

test('snapshot edits during a comparison discard the late response and interruptions invalidate a loaded report', async () => {
  let complete;
  const h = await harness(async url => url.endsWith('/compare') ? new Promise(resolve => { complete = resolve; }) : {});
  h.select(); h.get('compare').click(); await tick();
  h.get('snapshot').value = ''; h.get('snapshot').dispatchEvent(new h.dom.window.Event('change'));
  complete(comparison()); await tick();
  assert.equal(h.get('comparison-report').hidden, true);
  assert.equal(h.get('comparison-download').disabled, true);
  h.select(); h.get('compare').click(); await tick(); complete(comparison()); await tick();
  assert.equal(h.get('comparison-report').hidden, false);
  h.dom.window.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Synthetic comparison storage interruption' }) });
  h.get('comparison-next').click(); await tick();
  assert.equal(h.get('snapshot').value, 'snapshot-a');
  assert.equal(h.get('comparison-report').hidden, true);
  assert.equal(h.get('comparison-download').disabled, true);
  assert.match(h.get('status').textContent, /storage interruption/);
  h.dom.window.close();
});

test('a late comparison download cannot complete after the selected snapshot changes', async () => {
  const h = await harness(async url => url.endsWith('/compare') ? comparison() : {});
  h.select(); h.get('compare').click(); await tick();
  let complete, downloads = 0;
  h.dom.window.URL.createObjectURL = () => { downloads++; return 'blob:synthetic-comparison'; };
  h.dom.window.URL.revokeObjectURL = () => {};
  h.dom.window.HTMLAnchorElement.prototype.click = function () {};
  h.dom.window.fetch = async () => ({ ok: true, status: 200, blob: () => new Promise(resolve => { complete = resolve; }) });
  h.get('comparison-download').click(); await tick();
  h.get('snapshot').value = ''; h.get('snapshot').dispatchEvent(new h.dom.window.Event('change'));
  complete(new h.dom.window.Blob(['{}'])); await tick();
  assert.equal(downloads, 0);
  assert.equal(h.get('comparison-download').disabled, true);
  assert.match(h.get('status').textContent, /comparison.*changed/i);
  h.dom.window.close();
});

test('auth loss while refreshing installation status clears the comparison and cannot be swallowed as a partial-load warning', async () => {
  const h = await harness(async url => url.endsWith('/compare') ? comparison() : {});
  h.select(); h.get('compare').click(); await tick();
  assert.equal(h.get('comparison-report').hidden, false);
  h.dom.window.fetch = async url => url.endsWith('/status') ? { ok: true, status: 200, json: async () => h.state }
    : { ok: false, status: 403, json: async () => { throw new Error('HTML denial must not be parsed'); } };
  h.get('refresh').click(); await tick(); await tick();
  assert.equal(h.get('comparison-report').hidden, true);
  assert.equal(h.get('comparison-items').textContent, '');
  assert.equal(h.get('comparison-download').disabled, true);
  assert.equal(h.get('identity').textContent, '');
  assert.equal(h.get('refresh').disabled, true);
  assert.match(h.get('status').textContent, /reload/i);
  h.dom.window.close();
});

function drillReport(overrides = {}) {
  return { state: 'recorded', report_id: 'd'.repeat(32), snapshot: 'snapshot-a', target: 'target-a', archive_sha256: 'a'.repeat(64),
    snapshot_created_at: 1700000000, completed_at: 1700003600, application_version: '0.9.0', application_identity: 'b'.repeat(64),
    current_build_matches: false, file_count: 7, elapsed_seconds: 0.05, restored: true, post_restore_verified: true,
    restore_authorized: false, live_workspace_changed: false, external_stores_tested: false,
    verification_scope: 'File hashes, sizes and SQLite integrity only <img src=x>.', ...overrides };
}

test('saved restore evidence loads explicitly after reload and downloads the exact historical report without enabling restore', async () => {
  const h = await harness(async url => url.endsWith('/drill/report') ? drillReport() : {}, { live_restore_available: false });
  h.select();
  assert.equal(h.calls.some(call => call.url.includes('/drill')), false, 'Loading the page must not run a test or load private evidence automatically.');
  h.get('drill-report-load').click(); await tick();
  assert.equal(h.get('drill-report').hidden, false);
  assert.match(h.get('drill-report-summary').textContent, /7 files/);
  assert.match(h.get('drill-report-summary').textContent, /1 hour/);
  assert.match(h.get('drill-report-build').textContent, /different.*build/i);
  assert.equal(h.get('drill-report-scope').querySelector('img'), null);
  assert.match(h.get('drill-report-scope').textContent, /<img/);
  let downloaded = '';
  h.dom.window.URL.createObjectURL = () => 'blob:synthetic-drill';
  h.dom.window.URL.revokeObjectURL = () => {};
  h.dom.window.HTMLAnchorElement.prototype.click = function () { downloaded = this.download; };
  h.get('drill-report-download').click(); await tick();
  assert.match(downloaded, /restore-test.*\.json$/);
  const call = h.calls.find(call => call.url.endsWith('/drill/report/download'));
  assert.deepEqual(JSON.parse(call.init.body), { report_id: 'd'.repeat(32) });
  assert.equal(call.init.headers['X-Lightwork-Recovery-Context'], 'account-context');
  assert.equal(h.calls.some(call => /\/(drill|prepare|restore|authorize)$/.test(call.url)), false);
  assert.equal(h.get('prepare').disabled, true);
  h.get('refresh').click(); await tick(); await tick();
  assert.equal(h.get('drill-report').hidden, true);
  assert.equal(h.get('drill-report-download').disabled, true);
  h.dom.window.close();
});

test('missing or invalid restore-test evidence never exposes a usable historical download', async () => {
  for (const body of [{ state: 'not_recorded', snapshot: 'snapshot-a' }, drillReport({ snapshot: 'other-snapshot' }),
    drillReport({ restored: false }), drillReport({ restore_authorized: true }), drillReport({ target: 'other-workspace' }),
    drillReport({ completed_at: 1699999999 })]) {
    const h = await harness(async url => url.endsWith('/drill/report') ? body : {});
    h.select(); h.get('drill-report-load').click(); await tick();
    assert.equal(h.get('drill-report').hidden, true);
    assert.equal(h.get('drill-report-download').disabled, true);
    assert.match(h.get('drill-report-status').textContent + h.get('status').textContent, /not.*recorded|could not be verified/i);
    h.dom.window.close();
  }
});

test('a snapshot change discards late saved-test responses and late signed downloads', async () => {
  let complete;
  const h = await harness(async url => url.endsWith('/drill/report') ? new Promise(resolve => { complete = resolve; }) : {});
  h.select(); h.get('drill-report-load').click(); await tick();
  h.get('snapshot').value = ''; h.get('snapshot').dispatchEvent(new h.dom.window.Event('change'));
  complete(drillReport()); await tick();
  assert.equal(h.get('drill-report').hidden, true);
  h.select(); h.get('drill-report-load').click(); await tick(); complete(drillReport()); await tick();
  let downloads = 0;
  h.dom.window.URL.createObjectURL = () => { downloads++; return 'blob:synthetic-drill'; };
  h.dom.window.fetch = async () => ({ ok: true, status: 200, blob: () => new Promise(resolve => { complete = resolve; }) });
  h.get('drill-report-download').click(); await tick();
  h.get('snapshot').value = ''; h.get('snapshot').dispatchEvent(new h.dom.window.Event('change'));
  complete(new h.dom.window.Blob(['{}'])); await tick();
  assert.equal(downloads, 0);
  assert.equal(h.get('drill-report-download').disabled, true);
  h.dom.window.close();
});

test('temporary failure invalidates saved-test evidence while retaining safe selection and inputs', async () => {
  const h = await harness(async url => url.endsWith('/drill/report') ? drillReport() : {});
  h.select(); h.get('drill-report-load').click(); await tick();
  assert.equal(h.get('drill-report').hidden, false);
  h.get('key-input').value = 'synthetic pending recovery key';
  h.dom.window.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Synthetic report interruption' }) });
  h.get('drill-report-download').click(); await tick();
  assert.equal(h.get('drill-report').hidden, true);
  assert.equal(h.get('drill-report-download').disabled, true);
  assert.equal(h.get('snapshot').value, 'snapshot-a');
  assert.equal(h.get('key-input').value, 'synthetic pending recovery key');
  h.dom.window.close();
});

for (const status of [401, 403]) test(`HTML ${status} clears retained drill metadata before parsing`, async () => {
  const h = await harness(async url => url.endsWith('/drill/report') ? drillReport() : {});
  h.select(); h.get('drill-report-load').click(); await tick();
  assert.equal(h.get('drill-report').hidden, false);
  let parsed = 0;
  h.dom.window.fetch = async () => ({ ok: false, status, json: async () => { parsed++; throw new Error('HTML denial'); } });
  h.get('drill-report-download').click(); await tick();
  assert.equal(parsed, 0);
  assert.equal(h.get('drill-report-summary').textContent, '');
  assert.equal(h.get('drill-report-identity').textContent, '');
  assert.equal(h.get('drill-report').hidden, true);
  for (const button of h.dom.window.document.querySelectorAll('#workspace-recovery button')) assert.equal(button.disabled, true);
  h.dom.window.close();
});
