'use strict';
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), {JSDOM} = require('jsdom');
const folder = path.resolve(__dirname, '../../maverick_dashboard');
const markup = '<div id="settings-sections">' + fs.readFileSync(path.join(folder, 'templates/_maintenance_ops.html'), 'utf8')
  .replace(/\{#[\s\S]*?#\}/g, '').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') + '</div>';
const script = fs.readFileSync(path.join(folder, 'static/lightwork-maintenance-ops.js'), 'utf8');

function response(value, status = 200) {
  return new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
}
const SERVICES = {services: [
  {kind: 'worker', label: 'Background worker', about: 'Runs scheduled and queued tasks.', running: true, since: 1, last_error: '', log: ['ready']},
  {kind: 'channels', label: 'Chat channel server', about: 'Receives messages.', running: false, since: null, last_error: 'Stopped unexpectedly (exit code 3).', log: []}]};

async function fixture(route, {confirm = true} = {}) {
  const dom = new JSDOM(markup, {url: 'https://lightwork.example/settings', runScripts: 'outside-only'});
  const {window} = dom, calls = [], confirms = [];
  window.fetch = async (url, options = {}) => {
    calls.push({url, options});
    if (url === '/api/v1/admin/services') return response(SERVICES);
    if (url === '/api/v1/admin/tenants') return response([{id: 'acme', display_name: 'Acme'}]);
    if (url.endsWith('/release-checks')) return response({releases: ['0.1.7'], current_release: '0.1.7', measured: true});
    if (url.endsWith('/task-records')) return response({summary: 'Nothing scheduled.', scheduled: null, last_result: null});
    return route(url, options);
  };
  window.mvConfirm = async (message) => { confirms.push(message); return confirm; };
  window.mvErrorText = (failure, action) => 'Could not ' + action + '.';
  window.mvPlainDetail = (detail) => (typeof detail === 'string' && /^[A-Z].*\.$/.test(detail) ? detail : '');
  window.mvWhen = () => 'today';
  window.mvHuman = (v) => String(v);
  window.eval(script);
  const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((done) => setImmediate(done)); };
  await settle();
  return {window, document: window.document, calls, confirms, settle};
}

test('services load with plain status and stopping asks first', async (t) => {
  const ui = await fixture((url) => response({service: {}}));
  t.after(() => ui.window.close());
  const box = ui.document.querySelector('[data-ops-out="services"]');
  assert.match(box.textContent, /Running since today/);
  assert.match(box.textContent, /Stopped unexpectedly/);
  const stop = [...box.querySelectorAll('button')].find((b) => b.textContent === 'Stop');
  assert.equal(stop.getAttribute('aria-label'), 'Stop Background worker');
  stop.click();
  await ui.settle();
  assert.match(ui.confirms[0], /Stop Background worker/);
  const sent = ui.calls.find((c) => c.url === '/api/v1/admin/services/stop');
  assert.deepEqual(JSON.parse(sent.options.body), {service: 'worker', confirm: true});
});

test('a cancelled stop sends nothing', async (t) => {
  const ui = await fixture(() => response({}), {confirm: false});
  t.after(() => ui.window.close());
  [...ui.document.querySelectorAll('[data-ops-out="services"] button')].find((b) => b.textContent === 'Stop').click();
  await ui.settle();
  assert.equal(ui.calls.some((c) => c.url.endsWith('/stop')), false);
});

test('whole configuration shows values, sources and where to change them', async (t) => {
  const ui = await fixture((url) => response({summary: '2 settings are in effect.', unreadable: false, sections: [
    {name: 'Safety', keys: [{setting: 'How strict screening is', where: 'Safety', value: 'strict', source: 'Installation settings file',
      href: '/settings/all#group-safety', link_label: 'Change in All settings'}]},
    {name: 'Grpc', keys: [{setting: 'Port', where: 'Grpc', value: '50051', source: 'Installation settings file', href: '', link_label: ''}]}]}));
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-ops-run="config"]').click();
  await ui.settle();
  const out = ui.document.querySelector('[data-ops-out="config"]');
  assert.equal(out.querySelectorAll('details').length, 2);
  assert.equal(out.querySelector('a').getAttribute('href'), '/settings/all#group-safety');
  assert.match(out.textContent, /Only in the installation settings file/);
});

test('settings file update is offered only when there is something to rewrite, and confirms', async (t) => {
  const ui = await fixture((url, options) => {
    if (url.endsWith('/settings-file/update')) return response({summary: '1 setting name was updated.', backup: 'config.toml.bak-1', technical: []});
    return response({summary: '1 setting name will be updated.', can_update: true, technical: [{text: 'renamed a -> b'}]});
  });
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-ops-run="settings-file"]').click();
  await ui.settle();
  const update = [...ui.document.querySelectorAll('[data-ops-out="config"] button')].find((b) => b.textContent === 'Update the settings file');
  update.click();
  await ui.settle();
  assert.match(ui.confirms[0], /backup copy/);
  assert.match(ui.document.querySelector('[data-ops-out="config"]').textContent, /kept as a backup copy/);
});

test('workspace pickers fill from the workspace list and older records preview before assigning', async (t) => {
  const ui = await fixture((url) => {
    if (url.endsWith('/older-records/preview')) return response({total: 4, summary: '4 older records would be assigned to this workspace.', rows: [{records: 'Goals', count: 4}]});
    if (url.endsWith('/older-records/assign')) return response({job: {id: 'j1', state: 'done', result: {summary: 'Assigned 4 older records to the workspace.'}}});
    return response({});
  });
  t.after(() => ui.window.close());
  const form = ui.document.querySelector('[data-ops-form="older-records"]');
  assert.equal(form.elements.workspace.options.length, 2);
  form.elements.workspace.value = 'acme';
  form.dispatchEvent(new ui.window.Event('submit', {cancelable: true}));
  await ui.settle();
  const assign = ui.document.querySelector('[data-ops-run="older-assign"]');
  assert.equal(assign.hidden, false);
  assign.click();
  await ui.settle();
  assert.match(ui.confirms[0], /Assign every older record/);
  assert.match(ui.document.querySelector('[data-ops-out="older-records"]').textContent, /Assigned 4 older records/);
});

test('refusals show the server sentence when it is plain', async (t) => {
  const ui = await fixture(() => response({detail: 'This installation\'s program files are read-only, so components can\'t be prepared here.'}, 409));
  t.after(() => ui.window.close());
  ui.document.querySelector('[data-ops-run="components"]').click();
  await ui.settle();
  assert.match(ui.document.querySelector('[data-ops-status="upkeep"]').textContent, /read-only/);
});
