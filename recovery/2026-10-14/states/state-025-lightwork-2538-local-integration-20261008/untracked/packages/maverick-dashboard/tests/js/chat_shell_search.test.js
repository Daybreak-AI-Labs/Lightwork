'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const shell = fs.readFileSync(path.resolve(__dirname, '../../maverick_dashboard/templates/chat_base.html'), 'utf8');
const marker = '// Search saved requests and answers without leaving Chat.';
const filterMarker = '// Filter the recents list as you type. Titles only, on the client.';
const start = shell.indexOf(marker);
const scriptStart = shell.lastIndexOf('<script>', start);
const scriptEnd = shell.indexOf('</script>', start);
assert.ok(start > 0 && scriptStart >= 0 && scriptEnd > start, 'chat search controller exists');
const controller = shell.slice(scriptStart + '<script>'.length, scriptEnd);
const filterStart = shell.indexOf('(function () {', shell.indexOf(filterMarker));
const filterEnd = shell.indexOf('})();', filterStart) + 4;
assert.ok(filterStart > 0 && filterEnd > filterStart, 'chat recent-title filter exists');
const filterController = shell.slice(filterStart, filterEnd);

function fixture(t, responses) {
  const dom = new JSDOM(`<!doctype html><html><body>
    <input id="cs-filter"><p id="cs-filter-status"></p><ul id="cs-list"><li><a data-conversation-root="7"><bdi data-user-content>Private chat title</bdi></a></li></ul><a id="cs-full-task-search"></a>
    <section id="cs-full-search-results" hidden><ul id="cs-full-search-list"></ul>
      <p id="cs-full-search-status"></p>
      <button id="cs-full-search-older" hidden></button>
      <button id="cs-full-search-retry" hidden></button>
    </section>
  </body></html>`, {url:'https://lightwork.test/chat', runScripts:'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window;
  w.AbortController = AbortController;
  // The real page loads this safe status helper from the shared shell.
  w.mvStatus = (element, message) => { element.textContent = String(message || ''); };
  const calls = [];
  w.fetch = async (url, options = {}) => {
    calls.push({url:String(url), options});
    const reply = responses.shift();
    if (!reply) throw new Error('unexpected request');
    return new Response(JSON.stringify(reply.body), {
      status:reply.status || 200,
      headers:{'content-type':'application/json'}
    });
  };
  w.eval(filterController);
  w.eval(controller);
  return {w, calls, input:w.document.getElementById('cs-filter'),
    list:w.document.getElementById('cs-full-search-list'),
    status:w.document.getElementById('cs-full-search-status'),
    recentList:w.document.getElementById('cs-list'),
    region:w.document.getElementById('cs-full-search-results'),
    older:w.document.getElementById('cs-full-search-older')};
}

const settle = () => new Promise(resolve => setTimeout(resolve, 320));

test('chat searches saved work, displays safe excerpts and follows the older-work cursor', async t => {
  const h = fixture(t, [
    {body:{context:'owned-context'}},
    {body:{items:[{id:'7',title:'Margin <img src=x>',match_area:'Answer',snippet:'Profit improved after the new workflow.',updated_at:1760000000}],continuation:'sealed-next',complete:false}},
    {body:{items:[{id:'8',title:'Vendor review',match_area:'Request details',snippet:'Check the renewal terms.',updated_at:1760000300}],continuation:'',complete:true}}
  ]);
  h.input.value = 'profit';
  h.input.dispatchEvent(new h.w.Event('input', {bubbles:true}));
  await settle();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].url, '/api/v1/goals/search-page');
  assert.equal(h.calls[1].options.method, 'POST');
  assert.equal(JSON.parse(h.calls[1].options.body).q, 'profit');
  assert.doesNotMatch(h.calls[1].url, /profit/);
  assert.equal(h.calls[1].options.headers['X-Lightwork-Task-Search-Context'], 'owned-context');
  assert.equal(h.list.querySelectorAll('li').length, 1);
  assert.equal(h.list.querySelector('img'), null);
  assert.equal(h.list.querySelector('a').getAttribute('href'), '/chat/goal/7');
  assert.match(h.status.textContent, /Older work remains unchecked/);
  assert.equal(h.older.hidden, false);

  h.older.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.length, 3);
  assert.equal(JSON.parse(h.calls[2].options.body).cursor, 'sealed-next');
  assert.equal(h.calls[2].url, '/api/v1/goals/search-page');
  assert.equal(h.list.querySelectorAll('li').length, 2);
  assert.match(h.status.textContent, /2 matching results/);
  assert.equal(h.older.hidden, true);
});

test('chat search clears private matches when the server says access changed', async t => {
  const h = fixture(t, [
    {body:{context:'owned-context'}},
    {body:{items:[{id:'7',title:'Private work',match_area:'Answer',snippet:'Private answer text.',updated_at:1760000000}],continuation:'sealed-next',complete:false}},
    {status:409,body:{detail:'stale access'}}
  ]);
  h.input.value = 'private';
  h.input.dispatchEvent(new h.w.Event('input', {bubbles:true}));
  await settle();
  assert.equal(h.list.querySelectorAll('li').length, 1);
  h.older.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.list.querySelectorAll('li').length, 0);
  assert.equal(h.older.hidden, true);
  assert.match(h.status.textContent, /access changed during search/i);
});

test('workspace context changes clear recent titles, search text, excerpts and pending cursors', async t => {
  const h = fixture(t, [
    {body:{context:'owned-context'}},
    {body:{items:[{id:'9',title:'Private work',match_area:'Answer',snippet:'Private customer answer.',updated_at:1760000000}],continuation:'sealed-next',complete:false}}
  ]);
  h.input.value = 'private';
  h.input.dispatchEvent(new h.w.Event('input', {bubbles:true}));
  await settle();
  assert.equal(h.recentList.querySelectorAll('li').length, 1);
  assert.equal(h.list.querySelectorAll('li').length, 1);
  assert.match(h.w.document.body.textContent, /Private customer answer/);

  h.w.dispatchEvent(new h.w.CustomEvent('lightwork:context-changed'));

  assert.equal(h.input.value, '');
  assert.equal(h.recentList.childElementCount, 0);
  assert.equal(h.list.childElementCount, 0);
  assert.equal(h.region.hidden, true);
  assert.equal(h.older.hidden, true);
  assert.doesNotMatch(h.w.document.body.textContent, /Private chat title|Private customer answer|Private work/);

  const requestsBefore = h.calls.length;
  h.input.value = 'another private query';
  h.input.dispatchEvent(new h.w.Event('input', {bubbles:true}));
  await new Promise(resolve => setTimeout(resolve, 20));
  h.w.dispatchEvent(new h.w.CustomEvent('lightwork:context-changed'));
  await settle();
  assert.equal(h.calls.length, requestsBefore, 'a scheduled search is canceled before it can send the old workspace query');
});
