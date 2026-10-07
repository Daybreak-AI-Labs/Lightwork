'use strict';
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), {JSDOM} = require('jsdom');
const {shell} = require('./_shell');
const folder = path.resolve(__dirname, '../../maverick_dashboard');
const script = fs.readFileSync(path.join(folder, 'static/lightwork-license.js'), 'utf8');

// Every id and field the page script reads; mirrors templates/settings_license.html.
function terms(prefix) {
  return `<input id="${prefix}-customer" name="customer" value="Acme Bank">
    <select id="${prefix}-tier" name="tier"><option value="basic">Basic</option><option value="gold" selected>Gold</option></select>
    <input id="${prefix}-expires" name="expires" type="date" value="2031-01-31">
    <input id="${prefix}-grace" name="grace_days" type="number" value="14">
    <input id="${prefix}-seats" name="seats" type="number" value="">
    <input type="hidden" name="edition" value="enterprise">
    <input type="checkbox" name="suites" value="fleet" checked>
    <input type="checkbox" name="suites" value="finance">
    <input type="checkbox" name="features" value="advanced_evolve">
    <button type="submit">Go</button>`;
}
const MARKUP = `<div id="license-page" data-installed="INSTALLED" data-signing="ready">
  <p id="license-current-summary"></p><div id="license-current"></div>
  <form id="license-file-form"><input type="file" id="license-file" name="file"><button type="submit">Check</button></form>
  <p id="license-file-status"></p><div id="license-file-result"></div>
  <form id="license-set-form">${terms('license-set')}</form><p id="license-set-status"></p><div id="license-set-result"></div>
  <form id="license-issue-form">${terms('license-issue')}</form><p id="license-issue-status"></p><div id="license-issue-result"></div>
  <button type="button" id="license-keygen">Make a signing key pair</button>
  <p id="license-keygen-status"></p><div id="license-keygen-result" hidden></div>
</div>`;

const PRIVATE = 'a1'.repeat(32), PUBLIC = 'b2'.repeat(32);
const SUMMARY = {status: 'licensed', status_label: 'Active', summary: 'Your license is active.', plan: 'Gold',
  customer: 'Acme Bank', expires: '2031-01-31', add_ons: ['Fleet'], features: [{label: 'Outside agents', included: true}]};
const STATE = {installed: true, summary: SUMMARY, current: {customer: 'Acme Bank', tier: 'gold', edition: 'enterprise',
  suites: ['fleet'], features: [], expires: '2031-01-31', grace_days: 14, seats: null}};
const LICENSE = {customer: 'Acme Bank', plan: 'Gold', add_ons: ['Agent fleet'], features: [], expires: '2031-01-31', seats: null};
const SIGNED = {installed: false, trusted: false, warning: 'This server does not trust the key that signed this license yet.',
  license: LICENSE, file: {name: 'lightwork-license-acme-bank.json', text: '{"customer": "Acme Bank"}\n'},
  signed_with: {fingerprint: PUBLIC.slice(0, 16), public_key: PUBLIC}};

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
}
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve)); };

function fixture(route, {installed = false, confirm = true} = {}) {
  const dom = new JSDOM('<!doctype html><body>' + MARKUP.replace('INSTALLED', String(installed)) + '</body>',
    {url: 'https://lightwork.example/settings/license', runScripts: 'outside-only'});
  const {window} = dom, calls = [], confirms = [], copies = [], downloads = [];
  shell(window);
  window.fetch = async (url, options = {}) => {
    const call = {url, options, body: typeof options.body === 'string' ? JSON.parse(options.body) : options.body};
    calls.push(call);
    if (url === '/api/v1/admin/license' && (options.method || 'GET') === 'GET') return json(STATE);
    return route(call, calls);
  };
  window.mvConfirm = async (message) => { confirms.push(message); return confirm; };
  window.mvErrorText = (failure, action) => 'Could not ' + action + '. Please try again.';
  window.mvPlainDetail = (text) => (typeof text === 'string' && /^[A-Z][^_{}]*\.$/.test(text) ? text : '');
  window.mvCopy = (text) => copies.push(text);
  window.URL.createObjectURL = () => 'blob:license';
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { downloads.push({name: this.download, href: this.href}); };
  window.eval(script);
  const $ = (id) => window.document.getElementById(id);
  return {window, calls, confirms, copies, downloads, $};
}
function submit(h, id) {
  h.$(id).dispatchEvent(new h.window.Event('submit', {bubbles: true, cancelable: true}));
  return settle();
}
function chooseFile(h, text) {
  const file = new h.window.File([text], 'license.json', {type: 'application/json'});
  Object.defineProperty(h.$('license-file'), 'files', {value: [file]});
}

test('a new key pair is shown once, can be copied and hidden, and is never kept by the page', async () => {
  const h = fixture((call) => json({private_key: PRIVATE, public_key: PUBLIC, fingerprint: PUBLIC.slice(0, 16),
    store_as: 'MAVERICK_LICENSE_PRIVATE_KEY', trusted_keys_name: 'MAVERICK_LICENSE_PUBKEYS'}));
  h.$('license-keygen').click();
  await settle();
  const box = h.$('license-keygen-result');
  assert.equal(h.calls[0].url, '/api/v1/admin/license/keygen');
  assert.equal(h.calls[0].options.method, 'POST');
  assert.equal(box.hidden, false);
  assert.match(box.textContent, new RegExp(PRIVATE));
  assert.match(box.textContent, /Store this private key in your secret manager as MAVERICK_LICENSE_PRIVATE_KEY\./);
  assert.match(h.$('license-keygen-status').textContent, /shown only once/);
  Array.from(box.querySelectorAll('button')).find((b) => /private key/.test(b.textContent)).click();
  assert.deepEqual(h.copies, [PRIVATE]);
  Array.from(box.querySelectorAll('button')).find((b) => /hide the key/.test(b.textContent)).click();
  assert.equal(box.hidden, true);
  assert.equal(h.window.document.body.innerHTML.includes(PRIVATE), false);
  assert.equal(h.window.localStorage.length + h.window.sessionStorage.length, 0);
  assert.equal(h.calls.length, 1);
});

test('a license file is checked first and installed only on request', async () => {
  const h = fixture((call) => {
    const check = call.body.get('check_only') === 'true';
    return json({installed: !check, status: 'licensed', status_sentence: 'This license checks out and is in date.',
      license: LICENSE, replaces: false, note: ''});
  });
  chooseFile(h, '{"customer": "Acme Bank"}');
  await submit(h, 'license-file-form');
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/api/v1/admin/license/install-file');
  assert.equal(h.calls[0].body.get('check_only'), 'true');
  assert.match(h.$('license-file-result').textContent, /This license checks out and is in date\./);
  assert.match(h.$('license-file-status').textContent, /Nothing is installed until you choose Install this license/);
  const install = Array.from(h.$('license-file-result').querySelectorAll('button')).find((b) => b.textContent === 'Install this license');
  install.click();
  await settle();
  const post = h.calls.find((call) => call.body instanceof h.window.FormData && call.body.get('confirm') !== null);
  assert.equal(post.body.get('confirm'), 'false');
  assert.deepEqual(h.confirms, []);  // nothing installed yet, so nothing is replaced
  assert.match(h.$('license-file-status').textContent, /The license is installed/);
  assert.match(h.$('license-current').textContent, /Active/);
});

test('replacing an installed license asks first and sends nothing when declined', async () => {
  const h = fixture(() => json({installed: false, status: 'licensed', status_sentence: 'This license checks out and is in date.',
    license: LICENSE, replaces: true, note: 'This license is for a different customer than the one installed now.'}),
  {installed: true, confirm: false});
  chooseFile(h, '{}');
  await submit(h, 'license-file-form');
  assert.match(h.$('license-file-result').textContent, /different customer/);
  Array.from(h.$('license-file-result').querySelectorAll('button'))[0].click();
  await settle();
  assert.equal(h.confirms.length, 1);
  assert.match(h.confirms[0], /replaces the license installed now/);
  assert.equal(h.calls.length, 1);
  assert.equal(h.$('license-file-status').textContent, 'Nothing was installed.');
});

test('changing the license signs and installs, confirming when the server says one arrived meanwhile', async () => {
  const h = fixture((call, calls) => {
    const signs = calls.filter((c) => c.url === '/api/v1/admin/license/sign');
    if (signs.length === 1) return json({detail: 'A license is already installed. Confirm that you want to replace it.'}, 409);
    return json({...SIGNED, installed: true, warning: ''});
  });
  await submit(h, 'license-set-form');
  const signs = h.calls.filter((c) => c.url === '/api/v1/admin/license/sign');
  assert.equal(signs.length, 2);
  assert.deepEqual(signs[0].body, {customer: 'Acme Bank', tier: 'gold', edition: 'enterprise', suites: ['fleet'], features: [],
    expires: '2031-01-31', grace_days: 14, seats: null, install: true, confirm: false});
  assert.equal(signs[1].body.confirm, true);
  assert.equal(h.confirms.length, 1);
  assert.equal(h.$('license-set-status').textContent, 'The new license is installed.');
});

test('issuing a license offers a download and installing it here, with the trust warning', async () => {
  const h = fixture((call) => (call.url.endsWith('/sign') ? json(SIGNED) : json({...SIGNED, installed: true})));
  await submit(h, 'license-issue-form');
  const sign = h.calls.find((c) => c.url.endsWith('/sign'));
  assert.equal(sign.body.install, false);
  const result = h.$('license-issue-result');
  assert.match(result.textContent, /does not trust the key/);
  const buttons = Array.from(result.querySelectorAll('button'));
  buttons.find((b) => b.textContent === 'Download the signed license').click();
  assert.deepEqual(h.downloads, [{name: 'lightwork-license-acme-bank.json', href: 'blob:license'}]);
  buttons.find((b) => b.textContent === 'Install on this server').click();
  await settle();
  const install = h.calls.find((c) => c.url.endsWith('/install-issued'));
  assert.deepEqual(install.body, {license: SIGNED.file.text, confirm: false});
  assert.equal(h.$('license-issue-status').textContent, 'The license is installed on this server.');
  assert.equal(result.querySelector('details summary').textContent, 'Technical details');
});

test('failures read as plain sentences, never raw server text', async () => {
  const h = fixture((call) => (call.url.endsWith('/sign')
    ? json({detail: 'This server cannot sign licenses because no signing key is set up on it. Nothing was signed.'}, 503)
    : json({detail: 'insufficient role for this action'}, 403)));
  await submit(h, 'license-issue-form');
  assert.equal(h.$('license-issue-status').textContent,
    'This server cannot sign licenses because no signing key is set up on it. Nothing was signed.');
  h.$('license-keygen').click();
  await settle();
  assert.equal(h.$('license-keygen-status').textContent, 'Could not make a key pair. Please try again.');
  assert.equal(h.$('license-keygen-result').hidden, true);
});
