'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');

const script = fs.readFileSync(path.resolve(__dirname,
  '../../maverick_dashboard/static/lightwork-commitment-viability.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const reply = body => ({ok:true, status:200, json:async()=>body});
const review = (id, title) => ({context:'local', id, title, revision:1,
  assessment:{status:'feasible', stale_source_ids:[], evaluated_at:1, components:[]},
  model:{options:[{id:'option', units:1, kind:'capacity', ready_at:1, expires_at:2,
    cost_note:'', owner_confirmation:false}], commitments:[]},
  labels:{}, sources:[], proposals:[], audit_status:'recorded', owner_can_confirm:false});

function setup() {
  const ids = ['cmv-message','cmv-report-title','cmv-report-state','cmv-report-explanation',
    'cmv-source-note','cmv-components','cmv-options','cmv-proposals','cmv-sources'];
  const dom = new JSDOM(`<div id="cmv-enabled" data-context="local">
    <select id="cmv-select"><option value="">Choose a review</option></select>
    <button id="cmv-open"></button><button id="cmv-refresh"></button>
    <button id="cmv-more"></button><button id="cmv-reassess"></button>
    <details id="cmv-new-details"><form id="cmv-new"></form></details>
    <section id="cmv-report" hidden>${ids.map(id=>`<div id="${id}"></div>`).join('')}
      <form id="cmv-trace"><select name="running_order"></select>
        <select name="blocked_order"></select><textarea name="basis"></textarea></form></section></div>`,
  {url:'http://testserver/commitment-reviews',runScripts:'outside-only'});
  const w = dom.window, d = w.document, calls = [], selections = [];
  let releaseRead = null;
  w.fetch = async (url, options) => {
    calls.push({url, method:options.method});
    if (url.includes('?')) return reply({context:'local', items:[
      {id:'a',title:'First review'},{id:'b',title:'Second review'}],
    page:{has_more:false,next_cursor:''}});
    if (url.endsWith('/a') && releaseRead) return new Promise(resolve => {
      const release = releaseRead; releaseRead = null;
      release(() => resolve(reply(review('a','First review'))));
    });
    return reply(review(url.endsWith('/a')?'a':'b',url.endsWith('/a')?'First review':'Second review'));
  };
  d.addEventListener('lightwork:copilot-selection', event => selections.push(event.detail.selection));
  w.eval(script);
  return {dom,w,d,calls,selections,delayNextRead:callback => {releaseRead=callback;}};
}

test('switch, refresh and delayed read cannot leave a different review actionable', async () => {
  const ui=setup(), d=ui.d, select=d.getElementById('cmv-select');
  await tick();
  select.value='b'; select.dispatchEvent(new ui.w.Event('change'));
  d.getElementById('cmv-open').click(); await tick();
  assert.equal(d.getElementById('cmv-report-title').textContent,'Second review');
  d.getElementById('cmv-trace').elements.basis.value='Second review observation';
  select.value='a'; // Programmatic selection changes do not dispatch a change event.
  d.getElementById('cmv-trace').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));
  assert.equal(d.getElementById('cmv-report').hidden,true);
  assert.equal(d.getElementById('cmv-trace').elements.basis.value,'');
  assert.equal(ui.calls.some(call=>call.method==='POST'),false);
  select.value='b'; select.dispatchEvent(new ui.w.Event('change'));
  d.getElementById('cmv-open').click(); await tick();
  d.getElementById('cmv-trace').elements.basis.value='Another second review observation';
  select.value='a'; select.dispatchEvent(new ui.w.Event('change'));
  assert.equal(d.getElementById('cmv-report').hidden,true);
  assert.equal(d.getElementById('cmv-trace').elements.basis.value,'');
  assert.equal(select.value,'a');
  assert.equal(Object.keys(ui.selections.at(-1)).length,0);
  d.getElementById('cmv-trace').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));
  assert.equal(ui.calls.some(call=>call.method==='POST'),false);

  let release;
  ui.delayNextRead(callback => {release=callback;});
  d.getElementById('cmv-open').click(); await tick();
  select.value='b'; select.dispatchEvent(new ui.w.Event('change'));
  release(); await tick(); await tick();
  assert.equal(d.getElementById('cmv-report').hidden,true);
  assert.equal(select.value,'b');

  d.getElementById('cmv-open').click(); await tick();
  assert.equal(d.getElementById('cmv-report-title').textContent,'Second review');
  d.getElementById('cmv-refresh').click();
  assert.equal(d.getElementById('cmv-report').hidden,true);
  assert.equal(Object.keys(ui.selections.at(-1)).length,0);
  await tick();
  assert.equal(select.value,'b');
  assert.equal(d.getElementById('cmv-report').hidden,true);
  ui.dom.window.close();
});
