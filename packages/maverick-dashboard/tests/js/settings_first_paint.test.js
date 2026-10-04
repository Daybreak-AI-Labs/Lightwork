'use strict';
// Settings paints one group from the first frame. The server names it on the
// sections element; a group named in the address (/settings#settings-access)
// is applied by a small script before the sections are parsed, and the page
// script then takes over the tabs without a visible change.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const template = fs.readFileSync(path.resolve(__dirname, '../../maverick_dashboard/templates/settings.html'), 'utf8');
const earlyScript = (() => {
  const sections = template.indexOf('<div id="settings-sections"');
  assert.ok(sections > 0, 'sections element found');
  const start = template.indexOf('<script>', sections) + '<script>'.length;
  assert.ok(start < template.indexOf('data-settings-group=', sections), 'the script comes before the first section');
  return template.slice(start, template.indexOf('</script>', start));
})();
const pageScript = (() => {
  const marker = "var forms = Array.prototype.slice.call(document.querySelectorAll('form[action^=\"/settings\"]'));";
  const at = template.indexOf(marker);
  const start = template.lastIndexOf('<script>', at) + '<script>'.length;
  return template.slice(start, template.indexOf('</script>', at));
})();

const GROUPS = ['general', 'models', 'integrations', 'controls', 'access', 'maintenance'];
function tabs(selected) {
  return '<div role="tablist">' + GROUPS.map(group => `<button type="button" role="tab" data-settings-tab="${group}" ` +
    `aria-selected="${group === selected ? 'true' : 'false'}" tabindex="${group === selected ? '0' : '-1'}">${group}</button>`).join('') + '</div>';
}
function sections(active) {
  return `<div id="settings-sections" data-settings-active="${active}">` + GROUPS.map(group =>
    `<div class="section-head" data-settings-group="${group}"><h2>${group}</h2></div><div class="panel" data-settings-group="${group}"><p data-settings-search-item>${group} setting</p></div>`).join('') + '</div>';
}
function load(hash, serverGroup = 'general') {
  const html = `<main><section class="settings-index" data-initial-group="${serverGroup}"><input id="settings-search">${tabs(serverGroup)}` +
    `<p id="settings-filter-status" role="status"></p></section>${sections(serverGroup)}</main>`;
  const dom = new JSDOM(html, {url: 'https://lightwork.example/settings' + hash, runScripts: 'outside-only'});
  dom.window.eval(earlyScript);
  return dom;
}
const selectedTabs = doc => Array.from(doc.querySelectorAll('[data-settings-tab][aria-selected="true"]'), tab => tab.dataset.settingsTab);

for (const [hash, group] of [['#settings-access', 'access'], ['#features', 'controls'], ['#settings-maintenance', 'maintenance'], ['#providers', 'integrations']]) {
  test(`${hash} paints the ${group} group and selects its tab before the sections are parsed`, () => {
    const dom = load(hash);
    const doc = dom.window.document;
    assert.equal(doc.getElementById('settings-sections').dataset.settingsActive, group);
    assert.deepEqual(selectedTabs(doc), [group]);
    assert.equal(doc.querySelector(`[data-settings-tab="${group}"]`).tabIndex, 0);
    dom.window.close();
  });
}

for (const hash of ['', '#api-keys', '#settings-', '#constructor', '#settings-model-prices']) {
  test(`"${hash}" keeps the group the server chose`, () => {
    const dom = load(hash, 'integrations');
    const doc = dom.window.document;
    assert.equal(doc.getElementById('settings-sections').dataset.settingsActive, 'integrations');
    assert.deepEqual(selectedTabs(doc), ['integrations']);
    dom.window.close();
  });
}

test('the page script takes over the painted group without switching it', () => {
  const dom = load('#settings-access');
  const doc = dom.window.document;
  dom.window.eval(pageScript);
  const root = doc.getElementById('settings-sections');
  assert.equal(root.hasAttribute('data-settings-active'), false, 'the tabs now drive the hidden attributes');
  const shown = new Set(Array.from(doc.querySelectorAll('[data-settings-group]')).filter(node => !node.hidden).map(node => node.dataset.settingsGroup));
  assert.deepEqual([...shown], ['access']);
  assert.deepEqual(selectedTabs(doc), ['access']);
  // Clicking a tab still switches groups.
  doc.querySelector('[data-settings-tab="models"]').click();
  const now = new Set(Array.from(doc.querySelectorAll('[data-settings-group]')).filter(node => !node.hidden).map(node => node.dataset.settingsGroup));
  assert.deepEqual([...now], ['models']);
  assert.equal(dom.window.location.hash, '#settings-models');
  dom.window.close();
});
