'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise,resolve}; };
function response(body, status=200) { return {ok:status>=200&&status<300,status,json:async()=>body}; }
function fixture(html, file, fetch) {
  const dom = new JSDOM(html, {url:'http://localhost/',runScripts:'outside-only'});
  dom.window.fetch = fetch; dom.window.mvConfirm = async()=>true;
  dom.window.eval(fs.readFileSync(path.join(__dirname,'../../maverick_dashboard/static',file),'utf8'));
  return dom;
}
const factsHTML = `<h2 id="fact-editor-title">Add a fact</h2><form id="fact-editor"><input id="fact-name" required><textarea id="fact-detail" required></textarea><button id="fact-save">Save</button><button id="fact-cancel" type="button" hidden>Cancel</button><p id="fact-status"></p></form><p id="facts-empty">Empty</p><table id="fact-list" hidden><tbody></tbody></table>`;
function fillFact(window,key='Office hours',value='Nine to five') {
  const d=window.document; d.getElementById('fact-name').value=key; d.getElementById('fact-detail').value=value;
  d.getElementById('fact-editor').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
}
test('fact save blocks duplicates, checks persisted content, and renders text safely',async()=>{
  const pending=deferred(); const calls=[];
  const dom=fixture(factsHTML,'lightwork-facts.js',async(url,options)=>{calls.push({url,options}); return calls.length===1?pending.promise:response({'Office hours':'<img src=x onerror=alert(1)>\nNine to five'});});
  const w=dom.window; fillFact(w,'Office hours','<img src=x onerror=alert(1)>\nNine to five');
  fillFact(w,'Office hours','<img src=x onerror=alert(1)>\nNine to five'); assert.equal(calls.length,1);
  pending.resolve(response(null,204)); await tick(); await tick();
  assert.equal(calls.length,2); assert.equal(w.document.querySelector('.fact-value').textContent,'<img src=x onerror=alert(1)>\nNine to five');
  assert.equal(w.document.querySelectorAll('img').length,0); assert.equal(w.document.getElementById('fact-status').textContent,'Fact saved.');
  w.document.querySelector('[data-edit-fact]').click(); assert.equal(w.document.getElementById('fact-name').readOnly,true);
  assert.equal(w.document.getElementById('fact-detail').value,'<img src=x onerror=alert(1)>\nNine to five');
  w.close();
});
test('unconfirmed fact save preserves the draft and does not claim success',async()=>{
  let count=0; const dom=fixture(factsHTML,'lightwork-facts.js',async()=>++count===1?response(null,204):response({}));
  fillFact(dom.window); await tick(); await tick(); const d=dom.window.document;
  assert.match(d.getElementById('fact-status').textContent,/could not be confirmed/);
  assert.equal(d.getElementById('fact-detail').value,'Nine to five'); assert.equal(d.getElementById('fact-save').disabled,false);
  dom.window.close();
});
test('failed fact write does not query or discard the draft',async()=>{
  let calls=0; const dom=fixture(factsHTML,'lightwork-facts.js',async()=>{calls++;return response({},403);});
  fillFact(dom.window); await tick(); const d=dom.window.document;
  assert.equal(calls,1); assert.match(d.getElementById('fact-status').textContent,/permission/); assert.equal(d.getElementById('fact-name').value,'Office hours'); dom.window.close();
});
const libraryHTML = `<button id="templates-suggested">Suggested</button><button id="templates-all" hidden>All</button><p id="templates-status"></p><div id="cards"><div data-template-name="review"><span class="tplm__stars">unrated</span><button data-prepare-template>Use</button><form class="template-prepare" hidden><textarea data-template-param="company" required></textarea><button type="submit">Review</button><button type="button" data-cancel-prepare>Cancel</button><p class="prepare-status"></p></form><form class="template-rating"><select required><option value="">Choose</option><option value="4">4</option></select><button>Save</button><p class="rating-status"></p></form></div></div>`;
test('template preparation keeps full text and workspace context in a one-time local draft',async()=>{
  const calls=[]; const full='Full instructions. '.repeat(300);
  const dom=fixture(libraryHTML,'lightwork-template-library.js',async(url,options)=>{calls.push({url,options});return response({title:'Review Example',description:full,context:'workspace-marker',started:false});});
  const w=dom.window,d=w.document; d.querySelector('[data-prepare-template]').click();
  assert.equal(d.querySelector('.template-prepare').hidden,false); d.querySelector('textarea').value='Example';
  d.querySelector('.template-prepare').dispatchEvent(new w.Event('submit',{cancelable:true})); await tick();
  const draft=JSON.parse(w.sessionStorage.getItem('lightwork:template-draft:v1'));
  assert.equal(draft.description,full); assert.equal(draft.context,'workspace-marker');
  assert.deepEqual(JSON.parse(calls[0].options.body),{params:{company:'Example'}});
  assert.equal(calls.length,1); assert.equal(calls[0].url,'/api/v1/templates/review/prepare'); w.close();
});
test('failed template preparation retains inputs and creates no draft',async()=>{
  const dom=fixture(libraryHTML,'lightwork-template-library.js',async()=>response({detail:'Template changed'},409));
  const w=dom.window,d=w.document; d.querySelector('textarea').value='Example'; d.querySelector('.template-prepare').dispatchEvent(new w.Event('submit',{cancelable:true})); await tick();
  assert.equal(w.sessionStorage.length,0); assert.equal(d.querySelector('textarea').value,'Example'); assert.match(d.querySelector('.prepare-status').textContent,/changed since you opened it/); assert.doesNotMatch(d.querySelector('.prepare-status').textContent,/Template changed/); w.close();
});
test('rating saves require a matching confirmation and prevent duplicate requests',async()=>{
  const pending=deferred(); let calls=0; const dom=fixture(libraryHTML,'lightwork-template-library.js',async()=>{calls++;return pending.promise;});
  const w=dom.window,d=w.document; d.querySelector('select').value='4';
  const submit=()=>d.querySelector('.template-rating').dispatchEvent(new w.Event('submit',{cancelable:true})); submit();submit();assert.equal(calls,1);
  pending.resolve(response({saved:true,name:'different-template',stars:4})); await tick();
  assert.equal(d.querySelector('.tplm__stars').textContent,'unrated'); assert.match(d.querySelector('.rating-status').textContent,/could not be confirmed/); w.close();
});
test('suggestion failure leaves catalog cards visible',async()=>{
  const dom=fixture(libraryHTML,'lightwork-template-library.js',async()=>response({},503));
  dom.window.document.getElementById('templates-suggested').click(); await tick();
  assert.equal(dom.window.document.querySelector('[data-template-name]').hidden,false); assert.match(dom.window.document.getElementById('templates-status').textContent,/unavailable/); dom.window.close();
});

test('invalid fields open every enclosing disclosure before validation focus',()=>{
  const dom=new JSDOM('<details id="outer"><summary>Options</summary><details id="inner"><summary>Details</summary><input required></details></details>',{runScripts:'outside-only'});
  const source=fs.readFileSync(path.join(__dirname,'../../maverick_dashboard/static/lightwork-ui.js'),'utf8');
  const start=source.indexOf("document.addEventListener('invalid'");
  const end=source.indexOf('}, true);',start)+'}, true);'.length;
  assert.ok(start>=0&&end>start); dom.window.eval(source.slice(start,end));
  dom.window.document.querySelector('input').checkValidity();
  assert.equal(dom.window.document.getElementById('outer').open,true); assert.equal(dom.window.document.getElementById('inner').open,true); dom.window.close();
});

function authoringFixture(fetch, brief='Review the project') {
  const dom=new JSDOM('<form action="/chat/send"><select name="authoring_kind"><option value="flow">Flow</option></select><input name="title"><textarea name="description"></textarea><button type="submit">Continue</button></form><p id="goal-error"></p>',{url:'http://localhost/',runScripts:'outside-only'});
  const w=dom.window; w.fetch=fetch;
  w.document.querySelector('[name="title"]').value=brief;
  const source=fs.readFileSync(path.join(__dirname,'../../maverick_dashboard/static/lightwork-ui.js'),'utf8');
  const start=source.indexOf('    (function() {',source.indexOf('// Shared goal-create form handler.'));
  const end=source.indexOf('    })();',start)+'    })();'.length;
  assert.ok(start>=0&&end>start); w.eval(source.slice(start,end));
  const submit=()=>w.document.querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  return {dom,submit};
}

test('authoring refuses oversized text visibly without losing or storing it',async()=>{
  let calls=0; const text='x'.repeat(8001);
  const {dom,submit}=authoringFixture(async()=>{calls++;return response({context:'workspace'});},text);
  submit(); await tick(); const w=dom.window;
  assert.equal(calls,0); assert.equal(w.document.querySelector('[name="title"]').value,text);
  assert.match(w.document.getElementById('goal-error').textContent,/8,000/);
  assert.equal(w.sessionStorage.length,0); w.close();
});

test('authoring preserves Unicode and exact detail text with verified workspace context',async()=>{
  const pending=deferred(); let calls=0;
  const {dom,submit}=authoringFixture(async()=>{calls++;return pending.promise;},'Review');
  const w=dom.window,details='😀'.repeat(7990)+'  ';
  w.document.querySelector('textarea').value=details; submit();submit();assert.equal(calls,1);
  pending.resolve(response({context:'workspace-marker'})); await tick();await tick();
  const draft=JSON.parse(w.sessionStorage.getItem('lightwork.authoring-handoff'));
  assert.equal(draft.brief,'Review\n\n'+details); assert.equal(draft.context,'workspace-marker');
  assert.equal(draft.kind,'flow'); assert.ok(draft.created_at<=Date.now()); w.close();
});

test('authoring preserves edits made while workspace verification is pending',async()=>{
  const pending=deferred(); const {dom,submit}=authoringFixture(()=>pending.promise);
  const w=dom.window; submit();w.document.querySelector('textarea').value='New details';
  pending.resolve(response({context:'workspace-marker'}));await tick();await tick();
  assert.equal(w.sessionStorage.length,0);assert.equal(w.document.querySelector('textarea').value,'New details');
  assert.match(w.document.getElementById('goal-error').textContent,/brief changed/); w.close();
});

test('authoring verification failure keeps the brief and enables retry',async()=>{
  const {dom,submit}=authoringFixture(async()=>response({},403));const w=dom.window;
  submit();await tick();assert.equal(w.sessionStorage.length,0);
  assert.equal(w.document.querySelector('[name="title"]').value,'Review the project');
  assert.equal(w.document.querySelector('button').disabled,false);
  assert.match(w.document.getElementById('goal-error').textContent,/verify your workspace/);w.close();
});

test('authoring with attachments never silently starts an ordinary task',async()=>{
  let calls=0;const {dom,submit}=authoringFixture(async()=>{calls++;return response({context:'workspace'});});
  const w=dom.window,file=w.document.createElement('input');file.type='file';
  Object.defineProperty(file,'files',{value:[new w.File(['evidence'],'evidence.txt')]});
  w.document.querySelector('form').append(file);submit();await tick();
  assert.equal(calls,0);assert.equal(w.sessionStorage.length,0);assert.equal(file.files.length,1);
  assert.match(w.document.getElementById('goal-error').textContent,/do not accept attachments/);
  assert.equal(w.document.querySelector('[name="title"]').value,'Review the project');w.close();
});


test('search and suggestion modes compose without stale empty states',async()=>{
  const html='<input id="tplm-find"><p id="tplm-none" hidden>No matches</p>'+libraryHTML.replace('data-template-name="review"','data-template-name="review" data-tplm-text="review company"');
  const dom=fixture(html,'lightwork-template-library.js',async()=>response({suggested:[{name:'review'}]}));
  const w=dom.window,d=w.document,find=d.getElementById('tplm-find');
  find.value='missing';find.dispatchEvent(new w.Event('input'));d.getElementById('templates-suggested').click();await tick();
  assert.equal(d.querySelector('[data-template-name]').hidden,true);assert.equal(d.getElementById('tplm-none').hidden,false);
  d.getElementById('templates-all').click();assert.equal(d.querySelector('[data-template-name]').hidden,true);
  find.value='';find.dispatchEvent(new w.Event('input'));assert.equal(d.querySelector('[data-template-name]').hidden,false);assert.equal(d.getElementById('tplm-none').hidden,true);w.close();
});
