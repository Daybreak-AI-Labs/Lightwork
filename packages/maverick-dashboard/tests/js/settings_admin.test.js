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
