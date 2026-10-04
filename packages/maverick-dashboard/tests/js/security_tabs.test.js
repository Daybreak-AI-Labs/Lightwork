// Run with: node --test packages/maverick-dashboard/tests/js/security_tabs.test.js
'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');

const template = fs.readFileSync(path.resolve(__dirname, '../../maverick_dashboard/templates/security.html'), 'utf8');
const start = template.indexOf('<script>\n(function () {');
// The page script, without its server-only hunter block (rendered for admins).
const script = template.slice(start + 8, template.indexOf('</script>', start))
  .replace(/\{% if security_can_manage_hunters %\}[\s\S]*?\{% endif %\}/, '');
const KEYS = ['controls', 'evidence', 'risks', 'poams', 'vendors', 'policies', 'incidents', 'audits'];

function page(url) {
  const tabs = KEYS.map((key, index) => `<button id="sec-tab-${key}" role="tab" aria-selected="${index ? 'false' : 'true'}" data-sec-tab="${key}" type="button">${key}</button>`).join('');
  const panels = KEYS.map((key, index) => `<section id="sec-panel-${key}" role="tabpanel" data-sec-panel="${key}"${index ? ' hidden' : ''}></section>`).join('');
  const dom = new JSDOM(`<div class="sec-tabs" role="tablist">${tabs}</div>${panels}`, {url, runScripts: 'outside-only'});
  dom.window.eval(script);
  return dom;
}
const open = (dom) => [...dom.window.document.querySelectorAll('[data-sec-panel]')].filter((panel) => !panel.hidden).map((panel) => panel.dataset.secPanel);

test('a security tab opens from its address and choosing a tab updates the address', () => {
  const dom = page('https://local.test/security#sec-incidents');
  const w = dom.window, d = w.document, before = w.history.length;
  assert.deepEqual(open(dom), ['incidents']);
  assert.equal(d.getElementById('sec-tab-incidents').getAttribute('aria-selected'), 'true');
  d.getElementById('sec-tab-risks').click();
  assert.equal(w.location.hash, '#sec-risks');
  assert.deepEqual(open(dom), ['risks']);
  d.getElementById('sec-tab-risks').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'End', bubbles: true}));
  assert.equal(w.location.hash, '#sec-audits');
  assert.equal(w.history.length, before);
  w.location.hash = '#sec-vendors';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.deepEqual(open(dom), ['vendors']);
  dom.window.close();
});

test('an unknown tab address leaves the first tab open', () => {
  const dom = page('https://local.test/security#sec-nothing');
  assert.deepEqual(open(dom), ['controls']);
  dom.window.close();
});
