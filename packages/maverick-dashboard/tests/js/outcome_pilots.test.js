'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const script = fs.readFileSync(path.resolve(__dirname,'../../maverick_dashboard/static/lightwork-outcome-pilots.js'),'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function report() {
  return {context:'alice',id:'OPL-test',title:'<script>bad</script>',customer:'Example',workflow:'Review',criteria:'Good evidence',revision:2,
    groups:{all:{accepted:0,planned:4,paired:0,unavailable:0,reported_minutes_difference:null,reported_cost_difference:null}},limitations:['Operator reported.'],
    cases:[{id:'1',label:'A case',allocation:'holdout',status:'unmeasured',measurement:null,
      source:{href:'/chat/goal/1',version:'a'.repeat(64),accepted:false,runtime_cost_usd:null}}]};
}
async function setup(next) {
  const dom = new JSDOM(`<div id="outcome-pilots" data-context="alice"><p id="op-status"></p><select id="op-selected"></select><button id="op-refresh">Refresh</button><button id="op-export">Export</button><div id="op-report"></div><details id="op-plan-details"><p id="op-source-coverage"></p><form id="op-plan"><input name="title" required><input name="customer" required><textarea name="workflow" required></textarea><textarea name="criteria" required></textarea><div id="op-planned-cases"></div><button id="op-add-case" type="button">Add case</button><button type="submit">Freeze</button></form></details></div>`,{url:'https://lightwork.test',runScripts:'outside-only'});
  const w=dom.window,d=w.document,calls=[];
  w.fetch=async(url,options)=>{
    calls.push({url,options});
    if (calls.length===1) return {ok:true,json:async()=>({context:'alice',items:[{id:'OPL-test',title:'A pilot',cases:4}]})};
    if (calls.length===2) return {ok:true,json:async()=>({context:'alice',items:Array.from({length:4},(_,i)=>({kind:'goal',reference:String(i+1),label:'Task '+(i+1)})),coverage:[{kind:'vendor',status:'unavailable'}]})};
    const value=next.shift();
    if (value instanceof Error) throw value;
    if (typeof value==='function') return value();
    return {ok:true,json:async()=>value};
  };
  w.eval(script);await tick();
  return {dom,w,d,calls};
}
async function open(ui) {
  ui.d.getElementById('op-selected').value='OPL-test';
  ui.d.getElementById('op-selected').dispatchEvent(new ui.w.Event('change'));await tick();
}
test('record picker, missing coverage, source titles and unknown values render honestly',async()=>{
  const ui=await setup([report()]);await open(ui);
  assert.equal(ui.d.querySelectorAll('#op-planned-cases select').length,4);
  assert.match(ui.d.getElementById('op-source-coverage').textContent,/unavailable/);
  assert.equal(ui.d.querySelector('#op-report script'),null);
  assert.match(ui.d.getElementById('op-report').textContent,/<script>bad<\/script>/);
  assert.equal(ui.d.querySelector('#op-report input[name="baseline_minutes"]').value,'');
  assert.match(ui.d.getElementById('op-report').textContent,/provider invoice unverified/);
  ui.dom.window.close();
});
test('lost save keeps measurements and exact retry identity without automatic retry',async()=>{
  const ui=await setup([report(),new Error('Connection interrupted')]);await open(ui);
  const form=ui.d.querySelector('#op-report form');
  form.elements.evidence.value='Timing sheet row 5';form.elements.note.value='Observed work';form.elements.baseline_minutes.value='45';
  form.dispatchEvent(new ui.w.Event('submit',{cancelable:true,bubbles:true}));await tick();
  assert.equal(ui.calls.length,4);
  assert.equal(form.elements.baseline_minutes.value,'45');
  assert.equal(form.elements.review_minutes.value,'');
  assert.equal(form.dataset.operation,JSON.parse(ui.calls[3].options.body).operation_id);
  assert.equal(JSON.parse(ui.calls[3].options.body).review_minutes,null);
  assert.match(ui.d.getElementById('op-status').textContent,/Connection interrupted/);
  assert.equal(ui.d.activeElement,ui.d.getElementById('op-status'));
  ui.dom.window.close();
});
test('account switch clears old report and refuses the new response',async()=>{
  const ui=await setup([report(),{...report(),context:'bob'}]);await open(ui);
  ui.d.getElementById('op-refresh').click();await tick();
  assert.equal(ui.d.getElementById('op-report').children.length,0);
  assert.equal(ui.d.getElementById('op-export').disabled,true);
  assert.match(ui.d.getElementById('op-status').textContent,/account or workspace changed/);
  ui.dom.window.close();
});
test('pending save disables fields and duplicate submissions',async()=>{
  let finish;
  const ui=await setup([report(),()=>new Promise(resolve=>{finish=resolve;}),report()]);await open(ui);
  const form=ui.d.querySelector('#op-report form');form.elements.evidence.value='Timing';form.elements.note.value='Observed';
  form.dispatchEvent(new ui.w.Event('submit',{cancelable:true,bubbles:true}));
  form.dispatchEvent(new ui.w.Event('submit',{cancelable:true,bubbles:true}));
  assert.equal(ui.calls.length,4);
  assert.equal(form.elements.note.disabled,true);
  finish({ok:true,json:async()=>({context:'alice',id:'OPL-test',revision:3})});await tick();await tick();
  assert.equal(ui.calls.length,5);
  assert.equal(ui.d.getElementById('outcome-pilots').getAttribute('aria-busy'),'false');
  ui.dom.window.close();
});
