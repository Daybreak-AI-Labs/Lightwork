'use strict';
// Page scripts for operations that used to be command-line only: each sends
// nothing until the person acts, confirms destructive or outside actions with
// mvConfirm (declining sends nothing), and shows plain results and errors.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const folder = path.resolve(__dirname, '../../maverick_dashboard');
const read = name => fs.readFileSync(path.join(folder, name), 'utf8');
const strip = html => html.replace(/\{%[\s\S]*?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, '1').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
function slice(template, start, end) {
  const text = read('templates/' + template);
  const from = text.indexOf(start);
  assert.ok(from >= 0, start);
  return text.slice(from, text.indexOf(end, from) + end.length);
}

async function page(markup, script, reply, { confirm = true } = {}) {
  const dom = new JSDOM('<main>' + strip(markup) + '</main>', { url: 'https://app.example/x', runScripts: 'outside-only' });
  const w = dom.window, calls = [], asked = [];
  w.mvErrorText = (failure, action) => 'Could not ' + action + '. Plain message.';
  w.mvConfirm = async message => { asked.push(message); return confirm; };
  w.mvWhen = () => 'soon';
  w.setTimeout = fn => { fn(); return 0; };
  w.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const out = await reply(url, options);
    return out instanceof Response ? out : new Response(JSON.stringify(out));
  };
  w.eval(read('static/' + script));
  const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
  await settle();
  return { w, d: w.document, calls, asked, settle, $: id => w.document.getElementById(id) };
}

test('task journal lists, previews and undoes only after a confirmation', async t => {
  const markup = slice('task_file_recovery.html', '<section id="task-journal"', '</section>');
  const context = 'c'.repeat(64);
  const reply = (url, options) => {
    if (url.endsWith('/journal')) return { context, available: true, count: 1, entries: [{ file: 'notes.txt', change: 'existed', saved: true }], more: 0, incomplete: false, recording: true };
    const body = JSON.parse(options.body);
    return body.preview ? { context, available: true, preview: true, done: false, restored: 1, removed: 0, skipped: 0, complete: true }
      : { context, available: true, preview: false, done: true, restored: 1, removed: 0, skipped: 0, complete: true };
  };
  const declined = await page(markup, 'lightwork-task-journal.js', reply, { confirm: false });
  t.after(() => declined.w.close());
  assert.equal(declined.calls.length, 0, 'nothing is loaded until asked');
  declined.$('tj-preview').click();
  await declined.settle();
  assert.equal(declined.$('tj-review').hidden, false);
  assert.match(declined.$('tj-plan').textContent, /put back 1 file/);
  declined.$('tj-undo').click();
  await declined.settle();
  assert.match(declined.asked[0], /can’t be reversed/);
  assert.equal(declined.calls.filter(c => c.options.body && JSON.parse(c.options.body).preview === false).length, 0);
  const ui = await page(markup, 'lightwork-task-journal.js', reply);
  t.after(() => ui.w.close());
  ui.$('tj-preview').click();
  await ui.settle();
  ui.$('tj-undo').click();
  await ui.settle();
  const sent = JSON.parse(ui.calls.at(-1).options.body);
  assert.deepEqual([sent.preview, sent.confirm, sent.context], [false, true, context]);
  assert.match(ui.$('tj-status').textContent, /Put back 1 file/);
});

test('sharing a task on GitHub confirms first and shows only a safe link', async t => {
  const markup = '<div id="goal-export" data-goal="7"><button id="goal-gist"></button><p id="goal-export-msg"></p></div>';
  const declined = await page(markup, 'lightwork-goal-export.js', () => ({ shared: true, url: 'https://gist.github.com/a' }), { confirm: false });
  t.after(() => declined.w.close());
  declined.$('goal-gist').click();
  await declined.settle();
  assert.equal(declined.calls.length, 0);
  const ui = await page(markup, 'lightwork-goal-export.js', () => ({ shared: true, url: 'https://gist.github.com/a' }));
  t.after(() => ui.w.close());
  ui.$('goal-gist').click();
  await ui.settle();
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), { confirm: true });
  assert.equal(ui.d.querySelector('#goal-export-msg a').href, 'https://gist.github.com/a');
  const bad = await page(markup, 'lightwork-goal-export.js', () => ({ shared: true, url: 'javascript:alert(1)' }));
  t.after(() => bad.w.close());
  bad.$('goal-gist').click();
  await bad.settle();
  assert.equal(bad.d.querySelector('#goal-export-msg a'), null);
});

test('the schedule picker sends plain choices and a chosen time zone, never a typed timetable', async t => {
  const markup = slice('automations.html', '<details id="auto-new-sched"', '</details>\n  </section>');
  const ui = await page(markup, 'lightwork-schedule-picker.js', (url, options) => {
    if (url.endsWith('/options')) return { zones: ['UTC', 'Europe/Paris'], workflows: [], tasks: [{ id: 4, title: 'Weekly notes' }] };
    if (url.endsWith('/preview')) return { cron: '0 9 * * *', zone: 'UTC', next_runs: [1, 2, 3] };
    return { id: 1, next_run: 5 };
  });
  t.after(() => ui.w.close());
  ui.$('auto-new-sched').open = true;
  ui.$('auto-new-sched').dispatchEvent(new ui.w.Event('toggle'));
  await ui.settle();
  assert.equal(ui.$('auto-new-zone').options.length, 2);
  ui.d.querySelector('input[name="what"][value="rerun"]').checked = true;
  ui.$('auto-new-rerun').value = '4';
  ui.$('auto-new-repeat').value = 'weekly';
  ui.$('auto-new-zone').value = 'Europe/Paris';
  ui.$('auto-new-form').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  await ui.settle();
  const add = ui.calls.find(c => c.url === '/api/v1/schedules/add');
  const body = JSON.parse(add.options.body);
  assert.equal(body.what, 'rerun');
  assert.equal(body.goal_id, 4);
  assert.equal(body.timetable.repeat, 'weekly');
  assert.equal(body.timetable.zone, 'Europe/Paris');
  assert.deepEqual(body.timetable.days, [1]);
  assert.equal(body.timetable.advanced, undefined);
  assert.match(ui.$('auto-new-preview').textContent, /Schedule created/);
});

test('library templates are added only by administrators after a confirmation', async t => {
  const markup = slice('templates_market.html', '<section class="panel tplm__library"', '</section>');
  const reply = url => url.endsWith('/add') ? { added: true, name: 'weekly-digest' }
    : { available: true, can_add: true, templates: [{ name: 'weekly-digest', summary: '<b>Weekly</b>', rating: 0, ratings: 0, installed: false }] };
  const ui = await page(markup, 'lightwork-community-templates.js', reply);
  t.after(() => ui.w.close());
  ui.$('tplm-library-load').click();
  await ui.settle();
  assert.equal(ui.d.querySelector('#tplm-library-list b'), null, 'summaries are text');
  ui.d.querySelector('#tplm-library-list button').click();
  await ui.settle();
  assert.match(ui.asked[0], /outside your organisation/);
  assert.deepEqual(JSON.parse(ui.calls.at(-1).options.body), { name: 'weekly-digest', confirm: true });
});

test('business specialist setup drafts, shows the draft plainly and approves by draft only', async t => {
  const markup = slice('agents.html', '<details class="af-card" id="biz-setup"', '</details>');
  const agent = { name: 'acme_bakery', summary: 'Bakery helper', risk: 'low', can_use: ['web_search'], cannot_use: [], refuses: [],
    instructions: 'Be kind', steps: [{ name: 'Read', instruction: 'Read orders', review: false }], gaps: [] };
  const ui = await page(markup, 'lightwork-business-agent.js', (url, options) => {
    if (url.endsWith('/draft')) return { job: 'd'.repeat(32) };
    if (url.endsWith('/approve')) return { job: 'e'.repeat(32) };
    if (url.includes('step=approve')) return { state: 'done', result: { saved: true, name: 'acme_bakery', documents_loaded: 0, message: '' } };
    return { state: 'done', result: { ran: true, with_ai: false, agent } };
  });
  t.after(() => ui.w.close());
  ui.$('biz-name').value = 'Acme Bakery';
  ui.$('biz-form').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  await ui.settle();
  assert.equal(ui.$('biz-review').hidden, false);
  assert.match(ui.$('biz-summary').textContent, /Bakery helper/);
  ui.$('biz-approve').click();
  await ui.settle();
  const approve = ui.calls.find(c => c.url.endsWith('/approve'));
  assert.ok(approve.options.body instanceof ui.w.FormData, 'the pack itself is never sent back');
  assert.equal(approve.options.body.get('confirm'), 'true');
  assert.match(ui.$('biz-status').textContent, /Added/);
});

test('Ekko recording needs the consent box and reports counts plainly', async t => {
  const markup = slice('ekko.html', '<section class="card ekko-panel" id="ekko-recording"', '</section>');
  const ui = await page(markup, 'lightwork-ekko-recording.js', url => url.endsWith('/options') ? { windows_active_app: false }
    : url.endsWith('/import') ? { job: 'f'.repeat(32) } : { state: 'done', result: { recorded: 2, skipped: 1 } });
  t.after(() => ui.w.close());
  assert.equal(ui.$('ekko-rec-watch').hidden, true);
  ui.$('ekko-rec-import').click();
  await ui.settle();
  assert.match(ui.$('ekko-rec-msg').textContent, /Tick the box/);
  assert.equal(ui.calls.filter(c => c.url.endsWith('/import')).length, 0);
});

test('planning steps run in the background and show the plan as text', async t => {
  const markup = slice('chat_goal.html', '<details class="panel planning-steps"', '</details>');
  const ui = await page(markup, 'lightwork-planning-steps.js', url => url.endsWith('/planning-steps') ? { job: 'a'.repeat(32) }
    : { state: 'done', result: { ran: true, plan: ['<i>Read</i>', 'Write'], steps: [{ step: 'Read', output: 'ok' }], reviews: [{ verdict: 'done', notes: 'Fine' }], status: 'done', cost: 0.02 } });
  t.after(() => ui.w.close());
  assert.equal(ui.calls.length, 0);
  ui.$('ps-run').click();
  await ui.settle();
  assert.equal(ui.$('ps-result').hidden, false);
  assert.equal(ui.d.querySelector('#ps-result i'), null);
  assert.match(ui.$('ps-result').textContent, /Nothing about the task was changed/);
});

test('teach page learns a rule, and saving the draft needs a confirmation', async t => {
  const markup = read('templates/flow_teach.html').split('{% block content %}')[1].split('<style>')[0];
  const artifact = { spec: { inputs: { amount: { type: 'integer' } }, output_type: 'boolean' } };
  const reply = (url, options) => {
    if (url === '/api/v1/flows/1/teach') return { flow: { id: 'billing', name: 'Billing', version: 1, revision: 'r' }, steps: [{ id: 'decide', label: 'Decide', ready: true, rule: null }], tools: ['web_search'] };
    if (url.endsWith('/teach/examples')) return { available: true, runs: [] };
    if (url.endsWith('/approved')) return { recipes: [] };
    if (url.endsWith('/rule/learn')) return { artifact, summary: { rules: 2, examples: 4, inputs: [{ name: 'amount', kind: 'integer' }] } };
    if (url.endsWith('/rule/attach')) return { draft: { id: 'billing' }, note: 'Save to keep this change.' };
    return { id: 'billing', version: 2 };
  };
  const ui = await page(markup, 'lightwork-flow-teach.js', reply, { confirm: false });
  t.after(() => ui.w.close());
  assert.equal(ui.$('tr-step').value, 'decide');
  ui.$('tr-learn').click();
  await ui.settle();
  const learn = JSON.parse(ui.calls.find(c => c.url.endsWith('/rule/learn')).options.body);
  assert.equal(learn.inputs[0].kind, 'integer');
  assert.equal(learn.examples.length, 2);
  assert.match(ui.$('tr-summary').textContent, /2 rules fit your 4 examples/);
  ui.$('tr-attach').click();
  await ui.settle();
  assert.equal(ui.$('ft-save').hidden, false);
  ui.$('ft-save-go').click();
  await ui.settle();
  assert.match(ui.asked[0], /next version/);
  assert.equal(ui.calls.filter(c => c.url === '/api/v1/flows').length, 0, 'declining saves nothing');
});
