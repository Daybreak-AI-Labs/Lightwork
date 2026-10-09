'use strict';
// A refusal from the sign-in or access check must discard private entries even
// when a gateway in front of the dashboard answers it without a JSON body, and
// an unrelated gateway failure must keep the person's entered work.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const assets = path.join(__dirname, '../../maverick_dashboard/static');
const templates = path.join(__dirname, '../../maverick_dashboard/templates');
const script = fs.readFileSync(path.join(assets, 'lightwork-company-saml-setup.js'), 'utf8');
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve)); };
const CONTEXT = 'a'.repeat(64);
const html = (status) => new Response('<html><body>gateway</body></html>', {status, headers: {'content-type': 'text/html'}});
const json = (status, body) => new Response(JSON.stringify(body), {status, headers: {'content-type': 'application/json'}});

function load(markup, answer) {
  const dom = new JSDOM(markup, {url: 'https://lightwork.invalid/settings/all', runScripts: 'outside-only'});
  const w = dom.window;
  w.TextDecoder = TextDecoder;
  w.fetch = async (url, options = {}) => answer(url, options);
  w.eval(script);
  return {w, doc: w.document};
}

const setupMarkup = () => fs.readFileSync(path.join(templates, '_company_saml_setup.html'), 'utf8');
const loaded = {context: CONTEXT, runtime: {message: 'Single sign-on is installed.'}, checks: [], requests: []};

async function setup(refusal) {
  const ui = load(setupMarkup(), (url, options) => options.method === 'POST' ? refusal() : json(200, loaded));
  await settle();
  const phrase = ui.doc.querySelector('[data-saml-activation-phrase]');
  phrase.value = 'owner passphrase';
  ui.doc.querySelector('[data-saml-activation-review]').click();
  await settle();
  return {...ui, phrase, status: ui.doc.querySelector('[data-saml-status]')};
}

test('a 403 answered by a gateway without JSON still clears the private activation entries', async () => {
  const {phrase, status} = await setup(() => html(403));
  assert.equal(phrase.value, '');
  assert.match(status.textContent, /Private sign-in files and choices were cleared/);
});

test('a 401 answered by a gateway without JSON still clears the private activation entries', async () => {
  const {phrase} = await setup(() => html(401));
  assert.equal(phrase.value, '');
});

test('a gateway failure that is not a refusal keeps what the person entered', async () => {
  const {phrase, status} = await setup(() => html(502));
  assert.equal(phrase.value, 'owner passphrase');
  assert.match(status.textContent, /could not be verified/);
});

test('a JSON refusal still clears the private activation entries', async () => {
  const {phrase} = await setup(() => json(403, {detail: 'Sign in again.'}));
  assert.equal(phrase.value, '');
});

const recoveryMarkup = '<section data-saml-recovery><input type="email" data-saml-email><input type="password" data-saml-phrase>' +
  '<button type="button" data-saml-lookup>Read</button><select data-saml-recovery-original><option value="">Choose</option><option value="op1">Original</option></select>' +
  '<button type="button" data-saml-original-read>Read original</button><button type="button" data-saml-restore-review>Review</button>' +
  '<div data-saml-recovery-review hidden></div><p data-saml-recovery-status></p></section>';

async function recover(refusal) {
  const ui = load(recoveryMarkup, () => refusal());
  const phrase = ui.doc.querySelector('[data-saml-phrase]'), choices = ui.doc.querySelector('[data-saml-recovery-original]');
  ui.doc.querySelector('[data-saml-email]').value = 'owner@example.test';
  phrase.value = 'owner passphrase'; choices.value = 'op1';
  ui.doc.querySelector('[data-saml-lookup]').click();
  await settle();
  return {phrase, choices};
}

test('the recovery page clears its passphrase and choices on a refusal without a JSON body', async () => {
  const {phrase, choices} = await recover(() => html(403));
  assert.equal(phrase.value, '');
  assert.equal(choices.options.length, 0);
});

test('the recovery page keeps its entries on a gateway failure that is not a refusal', async () => {
  const {phrase, choices} = await recover(() => html(502));
  assert.equal(phrase.value, 'owner passphrase');
  assert.equal(choices.value, 'op1');
});
