'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const folder = path.resolve(__dirname, '../../maverick_dashboard');
const template = fs.readFileSync(path.join(folder, 'templates/chat.html'), 'utf8');
const panel = template.slice(template.indexOf('<details class="panel cs-viewpoints"'), template.indexOf('</details>', template.indexOf('cs-viewpoints')) + 10);
const script = fs.readFileSync(path.join(folder, 'static/lightwork-viewpoints.js'), 'utf8');
const answer = {
  ran: true, rounds: 1, verdict: 'against', reason: 'The risks outweigh the gain.', key_point: 'Mondays are busy.', cost: 0.0123,
  turns: [{ side: 'for', text: 'It frees Fridays.' }, { side: 'against', text: '<img src=x onerror=alert(1)> Mondays are packed.' }]
};

const JOB = 'a'.repeat(32);

// The page starts a background job and polls it; `reply` stands in for the
// finished job's result (or a failing response for the start request).
async function fixture(reply) {
  const dom = new JSDOM('<main>' + panel + '</main>', { url: 'https://app.example/chat', runScripts: 'outside-only' });
  const w = dom.window, calls = [];
  w.mvErrorText = (failure, action) => 'Could not ' + action + '. Plain message.';
  w.setTimeout = fn => { fn(); return 0; };
  w.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url === '/api/v1/viewpoints/jobs/' + JOB) {
      const done = await reply(url, options);
      return new Response(JSON.stringify({ id: JOB, state: 'done', result: done }));
    }
    const out = await reply(url, options);
    if (out instanceof Response) return out;
    return new Response(JSON.stringify({ job: JOB }), { status: 202 });
  };
  w.eval(script);
  const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r)); };
  const submit = () => w.document.getElementById('vp-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  return { w, calls, settle, submit, el: id => w.document.getElementById('vp-' + id) };
}

test('the panel is labelled and nothing runs until the person asks', async t => {
  assert.match(panel, /<label for="vp-question">/);
  assert.match(panel, /<label for="vp-rounds">/);
  assert.match(panel, /id="vp-status" role="status"/);
  const ui = await fixture(() => answer);
  t.after(() => ui.w.close());
  assert.equal(ui.calls.length, 0);
  ui.submit();
  await ui.settle();
  assert.equal(ui.calls.length, 0, 'an empty question is not sent');
  assert.match(ui.el('status').textContent, /Type a question/);
});

test('sends the question and chosen depth, and shows both sides and the verdict as text', async t => {
  const ui = await fixture(() => answer);
  t.after(() => ui.w.close());
  ui.el('question').value = '  Should we move the meeting?  ';
  ui.el('rounds').value = '3';
  ui.submit();
  await ui.settle();
  assert.equal(ui.calls[0].url, '/api/v1/viewpoints/jobs');
  assert.equal(ui.calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), { question: 'Should we move the meeting?', rounds: 3 });
  assert.equal(ui.calls[1].url, '/api/v1/viewpoints/jobs/' + JOB, 'the page polls the background job');
  const result = ui.el('result');
  assert.equal(result.hidden, false);
  assert.match(result.textContent, /case against stronger/);
  assert.match(result.textContent, /Why: The risks outweigh the gain\./);
  assert.match(result.textContent, /The case for.*It frees Fridays\./s);
  assert.equal(result.querySelector('img'), null, 'answers are shown as text only');
  assert.match(result.textContent, /This cost \$0\.01\./);
  assert.match(ui.el('status').textContent, /ready/);
  assert.equal(ui.el('run').disabled, false);
});

test('a refusal shows its sentence; failures and odd answers stay plain', async t => {
  const refused = await fixture(() => ({ ran: false, message: 'Your spending limit of $5.00 per day has been reached ($5.00 spent today).' }));
  t.after(() => refused.w.close());
  refused.el('question').value = 'Should we hire?';
  refused.submit();
  await refused.settle();
  assert.match(refused.el('status').textContent, /spending limit of \$5\.00/);
  assert.equal(refused.el('result').hidden, true);
  const failed = await fixture(() => new Response(JSON.stringify({ detail: 'Traceback: boom' }), { status: 503 }));
  t.after(() => failed.w.close());
  failed.el('question').value = 'Should we hire?';
  failed.submit();
  await failed.settle();
  assert.equal(failed.el('status').textContent, 'Could not get two viewpoints. Plain message.');
  const odd = await fixture(() => ({ ran: true, turns: 'nope' }));
  t.after(() => odd.w.close());
  odd.el('question').value = 'Should we hire?';
  odd.submit();
  await odd.settle();
  assert.match(odd.el('status').textContent, /couldn’t be checked/);
});

test('chosen positions for each side are sent only when filled in', async t => {
  const ui = await fixture(() => answer);
  t.after(() => ui.w.close());
  assert.match(panel, /<label for="vp-for">/);
  assert.match(panel, /<label for="vp-against">/);
  ui.el('question').value = 'Should we open on Sundays?';
  ui.el('for').value = '  Sunday trade is worth it ';
  ui.submit();
  await ui.settle();
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), { question: 'Should we open on Sundays?', rounds: 2, for_side: 'Sunday trade is worth it' });
});
