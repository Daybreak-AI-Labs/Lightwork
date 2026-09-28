'use strict';
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), {JSDOM} = require('jsdom');
const folder = path.resolve(__dirname, '../../maverick_dashboard');
const markup = fs.readFileSync(path.join(folder, 'templates/settings.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
const script = fs.readFileSync(path.join(folder, 'static/lightwork-settings-admin.js'), 'utf8');

function response(value, status = 200) {
  return new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
}

async function fixture(route, {confirm = true} = {}) {
  const dom = new JSDOM(markup, {url: 'https://lightwork.example/settings', runScripts: 'outside-only'});
  const {window} = dom, calls = [], confirms = [], copies = [];
  window.fetch = async (url, options = {}) => { calls.push({url, options}); return route(url, options); };
  window.mvConfirm = async (message) => { confirms.push(message); return confirm; };
  window.mvCopy = (text) => copies.push(text);
  window.mvErrorText = (failure, action) => 'Could not ' + action + '.';
  window.mvWhen = () => 'today';
  window.eval(script);
  const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((done) => setImmediate(done)); };
  return {window, document: window.document, calls, confirms, copies, settle};
}

test('make a secret shows it once and copies it', async (t) => {
  const ui = await fixture(() => response({secret: 'abc123-secret'}));  // pragma: allowlist secret
  t.after(() => ui.window.close());
  ui.document.getElementById('webhook-generate').dataset.replace = 'false';
  ui.document.getElementById('webhook-generate').click();
  await ui.settle();
  assert.equal(ui.calls[0].url, '/settings/webhooks/generate');
  assert.equal(ui.calls[0].options.method, 'POST');
  assert.equal(ui.document.getElementById('webhook-new').hidden, false);
  assert.equal(ui.document.getElementById('webhook-new-value').value, 'abc123-secret');  // pragma: allowlist secret
  ui.document.getElementById('webhook-copy').click();
  assert.deepEqual(ui.copies, ['abc123-secret']);  // pragma: allowlist secret
  // Replacing an existing secret asks first.
  ui.document.getElementById('webhook-generate').click();
  await ui.settle();
  assert.match(ui.confirms[0], /stop working/);
});

test('a failed secret request reads as a plain sentence', async (t) => {
  const ui = await fixture(() => response({detail: 'boom'}, 500));
  t.after(() => ui.window.close());
  ui.document.getElementById('webhook-generate').dataset.replace = 'false';
  ui.document.getElementById('webhook-generate').click();
  await ui.settle();
  assert.equal(ui.document.getElementById('webhook-status').textContent, 'Could not make a secret.');
  assert.equal(ui.document.getElementById('webhook-new').hidden, true);
});

test('readiness check lists each switch with a fix button', async (t) => {
  const ui = await fixture(() => response({ready: false, checks: [
    {key: 'provider', label: 'AI provider connected', ok: false, detail: 'No AI provider key is saved yet.',
     fix_label: 'Add a provider key', fix_href: '/settings#settings-integrations'},
    {key: 'encryption', label: 'Stored data is encrypted', ok: true, detail: 'Saved records are encrypted on disk.',
     fix_label: '', fix_href: ''}]}));
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-maintenance-run="readiness"]').click();
  await ui.settle();
  const list = ui.document.querySelector('[data-maintenance-out="readiness"]');
  assert.equal(list.children.length, 2);
  assert.match(list.textContent, /Not ready/);
  assert.equal(list.querySelector('a').textContent, 'Add a provider key');
  assert.match(ui.document.querySelector('[data-maintenance-status="readiness"]').textContent, /need attention/);
});

test('resuming a paused schedule confirms and sends the attempt count it showed', async (t) => {
  const ui = await fixture((url, options) => {
    if (url.endsWith('/resume')) return response({resumed: true, next: 9});
    return response({counts: [{state: 'Stopped', count: 1}], stopped: [
      {id: 7, attempts: 3, what: 'Run a task', when: 1, needs_decision: true, technical: 'x'}]});
  });
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-maintenance-run="queue"]').click();
  await ui.settle();
  const button = [...ui.document.querySelectorAll('[data-maintenance-out="queue"] button')]
    .find((b) => b.textContent === 'Resume schedule');
  assert.ok(button);
  button.click();
  await ui.settle();
  assert.match(ui.confirms[0], /will not be repeated/);
  const resume = ui.calls.find((c) => c.url.endsWith('/queue/7/resume'));
  assert.deepEqual(JSON.parse(resume.options.body), {attempts: 3});
});

test('forms marked for confirmation do not submit when cancelled', async (t) => {
  const ui = await fixture(() => response({}), {confirm: false});
  t.after(() => ui.window.close());
  const form = ui.document.querySelector('form[data-confirm]');
  let submitted = false;
  form.submit = () => { submitted = true; };
  form.requestSubmit = () => { submitted = true; };
  form.dispatchEvent(new ui.window.Event('submit', {cancelable: true}));
  await ui.settle();
  assert.equal(submitted, false);
  assert.equal(ui.confirms.length, 1);
});

test('encryption explains a shared database instead of claiming older records are encrypted', async (t) => {
  const note = 'Older records are kept in a shared database that this page cannot check or encrypt.';
  const ui = await fixture(() => response({enabled: true, older_unprotected: null, older_note: note}));
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-maintenance-run="encryption"]').click();
  await ui.settle();
  const box = ui.document.querySelector('[data-maintenance-out="encryption"]');
  assert.match(box.textContent, /shared database/);
  assert.doesNotMatch(box.textContent, /All older records are encrypted/);
  assert.ok(![...box.querySelectorAll('button')].some((b) => b.textContent === 'Encrypt older records'));
});

test('a one-button check shows plain rows and keeps kernel wording in technical details', async (t) => {
  const ui = await fixture(() => response({passed: false, summary: '1 protection for a regulated deployment is not in place.',
    checks: [{label: 'Saved data is encrypted', ok: false}],
    technical: [{level: 'fail', text: 'At-rest encryption: enable [encryption] at_rest'}]}));
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-maintenance-run="enterprise"]').click();
  await ui.settle();
  assert.equal(ui.calls[0].url, '/api/v1/admin/system/enterprise-check');
  assert.match(ui.document.querySelector('[data-maintenance-out="enterprise"]').textContent, /Saved data is encrypted.*Not in place/);
  assert.match(ui.document.querySelector('[data-maintenance-status="enterprise"]').textContent, /not in place/);
  const details = ui.document.querySelector('[data-maintenance-details="enterprise"]');
  assert.equal(details.hidden, false);
  assert.equal(details.open, false);
  assert.match(details.textContent, /at_rest/);
});

test('erasing the organisation confirms with a plain consequence and names the organisation shown', async (t) => {
  const ui = await fixture((url) => {
    if (url.endsWith('/client/erase')) return response({erased: true, files_removed: 4, kept_activity_record: true});
    return response({bound: true, organisation: 'acme', enforced: false, summary: 'This installation holds data for one organisation: acme.', technical: []});
  });
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-maintenance-run="client"]').click();
  await ui.settle();
  const erase = [...ui.document.querySelectorAll('[data-maintenance-out="client"] button')].find((b) => b.textContent === 'Erase all data');
  assert.ok(erase);
  erase.click();
  await ui.settle();
  assert.match(ui.confirms[0], /deleted for good and cannot be brought back/);
  const call = ui.calls.find((c) => c.url.endsWith('/client/erase'));
  assert.deepEqual(JSON.parse(call.options.body), {organisation: 'acme', keep_activity_record: true, confirm: true});
  assert.match(ui.document.querySelector('[data-maintenance-status="client"]').textContent, /4 files removed/);
});

test('a cancelled erase sends nothing', async (t) => {
  const ui = await fixture(() => response({bound: true, organisation: 'acme', summary: 'ok', technical: []}), {confirm: false});
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-maintenance-run="client"]').click();
  await ui.settle();
  [...ui.document.querySelectorAll('[data-maintenance-out="client"] button')].find((b) => b.textContent === 'Erase all data').click();
  await ui.settle();
  assert.equal(ui.calls.filter((c) => c.url.endsWith('/client/erase')).length, 0);
});

test('tidy up shows the counts first and removes only after confirming', async (t) => {
  const ui = await fixture((url, options) => {
    if (options.method === 'POST') return response({conversations: 2, activity: 5, duplicate_checks: 0});
    return response({conversations: 2, activity: 5, days: 180, activity_days: 30});
  });
  t.after(() => ui.window.close());
  const form = ui.document.querySelector('[data-maintenance-cleanup]');
  form.elements.days.value = '180';
  form.dispatchEvent(new ui.window.Event('submit', {cancelable: true}));
  await ui.settle();
  assert.equal(ui.calls[0].url, '/api/v1/admin/system/cleanup/preview?days=180&activity_days=30');
  assert.equal(ui.calls.length, 1);
  assert.match(ui.document.querySelector('[data-maintenance-status="cleanup"]').textContent, /Nothing has been removed yet/);
  [...ui.document.querySelectorAll('[data-maintenance-out="cleanup"] button')].find((b) => b.textContent === 'Remove these records').click();
  await ui.settle();
  assert.match(ui.confirms[0], /cannot be brought back/);
  const post = ui.calls.find((c) => c.options.method === 'POST');
  assert.deepEqual(JSON.parse(post.options.body), {days: 180, activity_days: 30, confirm: true});
  assert.match(ui.document.querySelector('[data-maintenance-status="cleanup"]').textContent, /Removed 2 conversations and 5 activity entries/);
});

test('a refused request shows the server sentence only when it is plain', async (t) => {
  const ui = await fixture(() => response({detail: 'Choose a workspace first. Nothing was changed.'}, 409));
  t.after(() => ui.window.close());
  ui.window.mvPlainDetail = (text) => (/^[A-Z].*\.$/.test(text) && !/_/.test(text) ? text : '');
  const form = ui.document.querySelector('[data-maintenance-cleanup]');
  form.dispatchEvent(new ui.window.Event('submit', {cancelable: true}));
  await ui.settle();
  assert.equal(ui.document.querySelector('[data-maintenance-status="cleanup"]').textContent, 'Choose a workspace first. Nothing was changed.');
});
