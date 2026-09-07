'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.resolve(__dirname,'../../maverick_dashboard/static/lightwork-consent-picker.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture(){
  const dom=new JSDOM('<div id="host"></div>',{url:'https://lightwork.test/consent/publishing',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],applied=[];
  let draft='current',hold;
  const records=[{resource_id:'analytics',resource_kind:'purpose',locale:'',version:2,current_version:2},{resource_id:'analytics',resource_kind:'purpose',locale:'',version:1,current_version:2}];
  w.fetch=async url=>{calls.push(url);if(hold)await hold;const parsed=new URL(url,w.location);let body;
    if(parsed.pathname.endsWith('/versions/1'))body={...records[1],payload:{title_key:'old.title'}};
    else if(parsed.pathname.endsWith('/versions/2'))body={...records[0],payload:{title_key:'new.title'}};
    else if(parsed.searchParams.has('resource_id'))body={items:records,total:2,next_offset:null};
    else body={items:[records[0]],total:1,next_offset:null};
    return {ok:true,json:async()=>body};};
  w.eval(source);w.mvConsentPicker.attach(d.getElementById('host'),{kind:'purposes',snapshot:()=>draft,apply:record=>applied.push(record)});
  const panel=d.querySelector('.cpp-picker');
  const click=async label=>{[...panel.querySelectorAll('button')].find(button=>button.textContent===label).click();await tick();};
  const choose=async(selector,index)=>{const field=panel.querySelector(selector);field.value=String(index);field.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();};
  return {dom,w,d,panel,calls,applied,click,choose,set draft(value){draft=value;},set hold(value){hold=value;}};
}

test('search and exact version preview only apply after explicit selection',async()=>{
  const ui=await fixture();ui.panel.querySelector('input').value='analytics';await ui.click('Search');
  assert.match(ui.calls[0],/q=analytics/);assert.equal(ui.applied.length,0);
  await ui.choose('[aria-label="Matching saved records"]',0);await ui.choose('[aria-label="Saved version"]',1);
  assert.match(ui.panel.querySelector('pre').textContent,/old.title/);assert.equal(ui.applied.length,0);
  await ui.click('Use selected version');assert.equal(ui.applied[0].version,1);assert.ok(ui.calls.every(url=>!url.endsWith('/publish')));ui.dom.window.close();
});

test('typing while choosing a saved version prevents late replacement of the draft',async()=>{
  const ui=await fixture();await ui.click('Search');await ui.choose('[aria-label="Matching saved records"]',0);await ui.choose('[aria-label="Saved version"]',0);
  ui.draft='newer edits';await ui.click('Use selected version');assert.equal(ui.applied.length,0);assert.match(ui.panel.textContent,/form changed/);ui.dom.window.close();
});
