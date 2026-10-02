'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const script = fs.readFileSync(path.resolve(__dirname,'../../maverick_dashboard/static/lightwork-outcome-pilots.js'),'utf8');
const template = fs.readFileSync(path.resolve(__dirname,'../../maverick_dashboard/templates/outcome_pilots.html'),'utf8').replace(/{%[\s\S]*?%}/g,'').replace('{{ pilot_context }}','alice');
const tick = () => new Promise(resolve => setImmediate(resolve));
function report() {
  return {context:'alice',id:'OPL-test',title:'<script>bad</script>',customer:'Example',workflow:'Review',criteria:'Good evidence',revision:2,
    admission:{status:'complete',expected_count:4,admitted_count:4,manifest_sha256:'b'.repeat(64),completion_sha256:'c'.repeat(64)},
    groups:{all:{accepted:0,planned:4,paired:0,unavailable:0,reported_minutes_difference:null,reported_cost_difference:null}},limitations:['Operator reported.'],
    cases:[{id:'1',label:'A case',allocation:'holdout',status:'unmeasured',measurement:null,
      source:{href:'/chat/goal/1',version:'a'.repeat(64),accepted:false,runtime_cost_usd:null}}]};
}
async function setup(next) {
  const dom = new JSDOM(template,{url:'https://lightwork.test',runScripts:'outside-only'});
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
function pickPilot(ui,id) {
  const picker=ui.d.getElementById('op-review-reference');
  picker.append(new ui.w.Option('A pilot',id));picker.value=id;
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
  assert.match(ui.d.getElementById('op-report').textContent,/complete for all 4 planned cases/);
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

test('protected plan sends an explicit learning mode and attributed prior-exposure statement',async()=>{
  const ui=await setup([{context:'alice',id:'OPL-test',revision:2},{context:'alice',items:[{id:'OPL-test',title:'Pilot',cases:4}]},report()]);
  const form=ui.d.getElementById('op-plan');
  for(const name of ['title','customer','workflow','criteria'])form.elements[name].value='Representative pilot '+name;
  [...ui.d.querySelectorAll('#op-planned-cases select')].forEach((select,index)=>{select.value='goal:'+(index+1);});
  ui.d.getElementById('op-learning-mode').value='exclude_local_learning';
  form.dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert.equal(ui.calls.length,2);assert.match(ui.d.getElementById('op-status').textContent,/prior-exposure/);
  ui.d.getElementById('op-prior-exposure').value='Checked local source history; provider prior exposure is unverified.';
  form.dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
  const body=JSON.parse(ui.calls[2].options.body);assert.equal(body.learning_mode,'exclude_local_learning');assert.match(body.prior_exposure_statement,/provider prior exposure/);
  assert.equal(body.cases.length,4);ui.dom.window.close();
});

test('improvement pilot chooses a governed candidate and keeps release lift unqualified',async()=>{
  const bound={...report(),release_binding:{candidate_id:'candidate-one'},case_exposure_verified:false,causal_lift_qualified:false};
  const ui=await setup([
    {context:'alice',available:true,items:[{candidate_id:'candidate-one',target_id:'installed-lightwork',state:'authorized'}]},
    {context:'alice',id:'OPL-test',revision:2},
    {context:'alice',items:[{id:'OPL-test',title:'Pilot',cases:4}]},
    bound,
    {context:'alice',deployment_state:'candidate_active',promotion_state:'canary',case_exposure_verified:false,
      causal_lift_qualified:false,review_href:'/research'},
  ]);
  const selections=[];
  ui.d.addEventListener('lightwork:copilot-selection',event=>selections.push(event.detail.selection));
  ui.d.getElementById('op-candidate-refresh').click();await tick();
  const picker=ui.d.getElementById('op-candidate');picker.value='candidate-one';
  picker.dispatchEvent(new ui.w.Event('change'));
  const form=ui.d.getElementById('op-plan');
  for(const name of ['title','customer','workflow','criteria'])form.elements[name].value='Improvement '+name;
  ui.d.getElementById('op-prior-exposure').value='Checked task inputs and local history; provider exposure remains unknown.';
  [...ui.d.querySelectorAll('#op-planned-cases select')].forEach((select,index)=>{select.value='goal:'+(index+1);});
  form.dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
  const create=ui.calls.find(call=>call.url==='/api/v1/outcome-pilots' && call.options.method==='POST');
  assert.equal(JSON.parse(create.options.body).candidate_id,'candidate-one');
  assert.equal(JSON.parse(create.options.body).learning_mode,'exclude_local_learning');
  assert.equal(selections.at(-1).kind,'outcome_pilot');
  assert.equal(selections.at(-1).id,'OPL-test');
  assert.match(ui.d.getElementById('op-report').textContent,/do not establish a causal release improvement/);
  ui.d.querySelector('#op-report section button').click();await tick();
  assert.match(ui.d.getElementById('op-report').textContent,/candidate is currently deployed/);
  assert.match(ui.calls.at(-1).url,/OPL-test\/release-status$/);
  ui.d.getElementById('op-selected').value='';
  ui.d.getElementById('op-selected').dispatchEvent(new ui.w.Event('change'));await tick();
  assert.equal(Object.keys(selections.at(-1)).length,0);
  ui.dom.window.close();
});

test('duplicate-pilot conflict keeps the complete plan and exact retry reference',async()=>{
  const conflict=()=>({ok:false,status:409,json:async()=>({detail:'One or more selected work items already belongs to an active or frozen pilot in this tenant. No allocation model is configured.'})});
  const ui=await setup([conflict]);
  const form=ui.d.getElementById('op-plan');
  form.elements.title.value='Competing customer pilot';form.elements.customer.value='Private customer';
  form.elements.workflow.value='Review evidence';form.elements.criteria.value='Every citation is current';
  [...ui.d.querySelectorAll('#op-planned-cases select')].forEach((select,index)=>{select.value='goal:'+(index+1);});
  form.dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert.equal(form.elements.title.value,'Competing customer pilot');
  assert.equal(form.elements.customer.value,'Private customer');
  assert.equal(ui.d.querySelectorAll('#op-planned-cases select').length,4);
  assert.ok([...ui.d.querySelectorAll('#op-planned-cases select')].every(select=>select.value));
  assert.match(form.dataset.operation,/^[a-f0-9-]{16,100}$/);
  assert.match(ui.d.getElementById('op-status').textContent,/No allocation model is configured/);
  assert.match(ui.d.getElementById('op-status').textContent,/inputs are still here/);
  assert.equal(form.elements.title.disabled,false);ui.dom.window.close();
});

test('independent review binds exact evidence and needs a separate confirmation',async()=>{
  const packet={...report(),learning_policy:{prior_exposure_statement:'Local inputs checked; external training unknown.',automatic_local_exclusion_enforced:true,evidence_version:'c'.repeat(64)}};
  const ui=await setup([packet,{context:'alice',id:'OPL-test',revision:3}]);
  pickPilot(ui,'OPL-test');ui.d.getElementById('op-review-load').click();await tick();
  const form=ui.d.querySelector('#op-independent-report form');form.querySelector('textarea').value='Independent policy receipts and their limits checked.';
  form.dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await tick();assert.equal(ui.calls.length,3);
  form.querySelector('input[type="checkbox"]').checked=true;form.dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  const body=JSON.parse(ui.calls[3].options.body);assert.equal(body.confirm,true);assert.equal(body.evidence_version,'c'.repeat(64));assert.equal(body.expected_revision,2);
  assert.match(ui.calls[3].url,/independent-review\/OPL-test$/);ui.dom.window.close();
});

test('HTML access loss during independent review clears private report, prepared inputs and picker labels',async()=>{
  const ui=await setup([report(),()=>({ok:false,status:403,json:async()=>{throw new SyntaxError('HTML');}})]);await open(ui);
  ui.d.getElementById('op-prior-exposure').value='Private trial notes';pickPilot(ui,'OPL-test');ui.d.getElementById('op-review-load').click();await tick();
  assert.equal(ui.d.getElementById('op-report').textContent,'');assert.equal(ui.d.getElementById('op-prior-exposure').value,'');assert.equal(ui.d.getElementById('op-selected').options.length,0);
  assert.equal(ui.d.getElementById('op-review-load').disabled,true);ui.dom.window.close();
});

test('structured account-context conflict still clears private pilot inputs',async()=>{
  const changed=()=>({ok:false,status:409,json:async()=>({detail:{code:'pilot_context_changed',message:'The account or workspace changed. Reload the pilot page.'}})});
  const ui=await setup([changed]);
  ui.d.getElementById('op-prior-exposure').value='Private trial notes';
  pickPilot(ui,'OPL-test');ui.d.getElementById('op-review-load').click();await tick();
  assert.equal(ui.d.getElementById('op-prior-exposure').value,'');
  assert.equal(ui.d.getElementById('op-review-reference').value,'');
  assert.equal(ui.d.getElementById('op-review-load').disabled,true);
  assert.match(ui.d.getElementById('op-status').textContent,/account or workspace changed/);ui.dom.window.close();
});

test('pilot picker can load a later context-filtered page',async()=>{
  const dom = new JSDOM(template,{url:'https://lightwork.test',runScripts:'outside-only'});
  const w=dom.window,d=w.document,calls=[];
  w.fetch=async(url,options)=>{
    calls.push({url,options});
    if (url === '/api/v1/outcome-pilots/sources') return {ok:true,json:async()=>({
      context:'alice',items:[],coverage:[],
    })};
    if (url.includes('cursor=OPL-page-one.json')) return {ok:true,json:async()=>({
      context:'alice',partial:false,page:{has_more:false,next_cursor:null},
      items:[{id:'OPL-page-two',title:'Reachable older pilot',cases:4}],
    })};
    if (url.startsWith('/api/v1/outcome-pilots?')) return {ok:true,json:async()=>({
      context:'alice',partial:true,page:{has_more:true,next_cursor:'OPL-page-one.json'},
      items:[{id:'OPL-page-one',title:'First pilot',cases:4}],
    })};
    throw new Error('Unexpected URL: '+url);
  };
  w.eval(script);await tick();await tick();
  const more=d.getElementById('op-load-more');
  assert.equal(more.hidden,false);
  more.click();await tick();await tick();
  assert.deepEqual([...d.getElementById('op-selected').options].map(option=>option.value),[
    '', 'OPL-page-one', 'OPL-page-two',
  ]);
  assert.equal(more.hidden,true);
  assert.ok(calls.some(call=>call.url.includes('cursor=OPL-page-one.json')));
  dom.window.close();
});

test('a code-like server detail is replaced by a plain sentence',()=>{
  const source=require('node:fs').readFileSync(require('node:path').resolve(__dirname,'../../maverick_dashboard/static/lightwork-outcome-pilots.js'),'utf8');
  assert.equal(source.includes("e.loc.slice(1)"),false);
  assert.match(source,/const readable = raw && /);
});

test('reviewers choose a pilot from the waiting list instead of typing a reference',async()=>{
  const ui=await setup([{context:'alice',items:[
    {id:'OPL-one',title:'Invoice triage',frozen_at:1800000000,decision:null},
    {id:'OPL-two',title:'Supplier checks',frozen_at:1800000500,decision:'needs_more_evidence'},
    {id:'not a pilot',title:'Injected',frozen_at:1,decision:null}]}]);
  const panel=ui.d.getElementById('op-review-panel');
  assert.equal(ui.d.getElementById('op-review-reference').tagName,'SELECT');
  Object.defineProperty(panel,'open',{value:true});panel.dispatchEvent(new ui.w.Event('toggle'));for(let i=0;i<10;i++)await tick();
  const labels=[...ui.d.getElementById('op-review-reference').options].map(option=>option.textContent);
  assert.equal(labels.length,3);assert.equal(labels[0],'Choose a pilot');
  assert.match(labels[1],/^Invoice triage · planned /);assert.match(labels[2],/already reviewed \(more evidence requested\)/);
  assert.match(ui.calls.at(-1).url,/independent-review$/);
  assert.doesNotMatch(ui.d.getElementById('op-review-panel').textContent,/OPL-|reference shared/);
  ui.dom.window.close();
});

test('a person who cannot review is told who can, without clearing the page',async()=>{
  const ui=await setup([()=>({ok:false,status:403,json:async()=>({detail:'A different named global administrator must review this pilot.'})})]);
  const panel=ui.d.getElementById('op-review-panel');
  ui.d.getElementById('op-prior-exposure').value='Kept notes';
  Object.defineProperty(panel,'open',{value:true});panel.dispatchEvent(new ui.w.Event('toggle'));for(let i=0;i<10;i++)await tick();
  assert.match(ui.d.getElementById('op-review-note').textContent,/Only an administrator who did not create a pilot/);
  assert.equal(ui.d.getElementById('op-prior-exposure').value,'Kept notes');
  for(let i=0;i<10;i++)await tick();
  ui.dom.window.close();
});
