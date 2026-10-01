'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../../maverick_dashboard/static/board.js'), 'utf8');

function fixture(t) {
  const dom = new JSDOM('<div class="bd-slicer"><button data-days="30">Month</button><button data-days="7">Week</button></div><p id="bd-stamp"></p><output id="result"></output>', {
    url: 'https://lightwork.test/spend', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  t.after(() => dom.window.close());
  const {window} = dom, calls = [], timers = new Map(), polls = new Map();
  let serial = 0;
  window.setTimeout = (fn) => { timers.set(++serial, fn); return serial; };
  window.clearTimeout = (id) => timers.delete(id);
  window.setInterval = (fn) => { polls.set(++serial, fn); return serial; };
  window.clearInterval = (id) => polls.delete(id);
  window.fetch = (url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject}));
  window.eval(source);
  window.LWBoard.attach({board: 'spend', render: (value) => { window.document.getElementById('result').textContent = value.label; }});
  const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(setImmediate); };
  const deliver = async (call, label) => { call.resolve({ok: true, json: async () => ({label})}); await settle(); };
  return {window, calls, timers, polls, settle, deliver,
    stamp: () => window.document.getElementById('bd-stamp').textContent,
    result: () => window.document.getElementById('result').textContent,
    select: (days) => window.document.querySelector('[data-days="' + days + '"]').click()};
}

test('a late old-period response cannot replace the selected period or its timestamp', async (t) => {
  const ui = fixture(t);
  ui.select(7);
  assert.equal(ui.calls[0].options.signal.aborted, true);
  assert.match(ui.calls[1].url, /days=7$/);
  await ui.deliver(ui.calls[1], 'Current week');
  const stamp = ui.stamp();
  await ui.deliver(ui.calls[0], 'Obsolete quarter');
  assert.equal(ui.result(), 'Current week');
  assert.equal(ui.stamp(), stamp);
  assert.match(stamp, /^Showing 7 days,/);
  assert.equal(ui.window.document.querySelector('[data-days="7"]').getAttribute('aria-pressed'), 'true');
});

test('an unavailable new period retains and labels the actual last successful period', async (t) => {
  const ui = fixture(t);
  await ui.deliver(ui.calls[0], 'Known quarter');
  ui.select(30);
  assert.match(ui.stamp(), /^Loading 30 days/);
  ui.calls[1].reject(new TypeError('Synthetic offline response'));
  await ui.settle();
  assert.equal(ui.result(), 'Known quarter');
  assert.match(ui.stamp(), /Previous view retained: 90 days,/);
  assert.doesNotMatch(ui.stamp(), /Showing 30 days/);
});

test('a revoked company board shows access recovery and stops automatic retries', async (t) => {
  const ui = fixture(t);
  ui.calls[0].resolve({ok: false, status: 403});
  await ui.settle();
  assert.match(ui.stamp(), /departments you cannot access/i);
  assert.match(ui.stamp(), /Ask an administrator/);
  assert.doesNotMatch(ui.stamp(), /Trying again in a minute/);
  assert.equal(ui.polls.size, 0);
});

test('navigation retires a pending response and restored pages make a fresh request', async (t) => {
  const ui = fixture(t);
  ui.window.dispatchEvent(new ui.window.PageTransitionEvent('pagehide'));
  assert.equal(ui.calls[0].options.signal.aborted, true);
  assert.equal(ui.polls.size, 0);
  await ui.deliver(ui.calls[0], 'Response after leaving');
  assert.equal(ui.result(), '');
  ui.window.dispatchEvent(new ui.window.PageTransitionEvent('pageshow', {persisted: true}));
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.polls.size, 1);
  await ui.deliver(ui.calls[1], 'Restored current view');
  assert.equal(ui.result(), 'Restored current view');
  assert.equal(ui.timers.size, 0);
});
