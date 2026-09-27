"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path");
const {JSDOM}=require("jsdom");
const root=path.resolve(__dirname,"../../maverick_dashboard");
const read=name=>fs.readFileSync(path.join(root,name),"utf8");
const flush=async()=>{for(let i=0;i<20;i++){await new Promise(setImmediate);await new Promise(r=>setTimeout(r,2));}};
const reply=(status,body)=>({ok:status<400,status,headers:{get:()=>null},json:async()=>body});

// Markup mirrors templates/settings_all.html for four representative rows.
const HTML=`<!doctype html><main>
<div role="status" id="settings-all-status" hidden></div>
<input id="settings-all-search"><select id="settings-all-group"><option value="">All</option><option value="safety">Safety</option><option value="modules">Modules</option></select>
<input type="checkbox" id="settings-all-changed"><p id="settings-all-filter-status"></p>
<div id="settings-all-groups">
<section class="settings-all-group" data-group="safety"><h2>Safety</h2>
 <div class="settings-all-row" data-setting-id="safety.profile" data-control="select" data-source="default" data-search="safety level screening">
  <strong data-value-text>Balanced</strong><span data-source-text>Default</span>
  <select data-input id="p"><option value="strict">Strict</option><option value="balanced" selected>Balanced</option><option value="off">Off</option></select>
  <button data-save>Save</button><button data-reset>Reset to default</button><p data-row-status></p></div>
 <div class="settings-all-row" data-setting-id="sandbox.timeout" data-control="number" data-source="here" data-search="time limit command">
  <strong data-value-text>60 seconds</strong><span data-source-text>Set here</span>
  <input type="number" data-input value="60"><button data-save>Save</button><button data-reset>Reset to default</button><p data-row-status></p></div>
 <div class="settings-all-row" data-setting-id="notifications.ntfy_topic" data-control="secret" data-source="default" data-search="ntfy topic">
  <span data-secret-state>Not set</span><button data-secret-replace>Add</button><input type="password" data-input hidden>
  <button data-save>Save</button><button data-reset>Reset to default</button><p data-row-status></p></div>
 <div class="settings-all-row" data-setting-id="webhooks.outbound" data-control="list" data-source="default" data-search="task events addresses">
  <ul class="settings-all-list-items"></ul><button data-list-add>Add an address</button>
  <button data-save>Save</button><button data-reset>Reset to default</button><p data-row-status></p></div>
 <p><button data-save-group="safety">Save changes in Safety</button><button data-reset-group="safety">Reset all in Safety</button></p>
</section>
<section class="settings-all-group" data-group="modules"><h2>Modules</h2>
 <div class="settings-all-row" data-module="policy_packs" data-search="policy packs module">
  <span class="settings-all-label">Policy packs</span><strong data-value-text>Off</strong>
  <input type="checkbox" data-module-toggle><p data-row-status></p></div>
</section></div><p id="settings-all-empty" hidden></p></main>`;

function stateFor(id,over){
  return {groups:[{settings:[Object.assign({id,value:null,value_text:"x",source:"here",source_text:"Set here"},over||{})]}]};
}

async function page(handler){
  const dom=new JSDOM(HTML,{url:"https://lightwork.test/settings/all",runScripts:"outside-only"});
  const w=dom.window,d=w.document,calls=[],confirms=[];
  w.mvConfirm=async message=>{confirms.push(message);return true;};
  w.mvErrorText=(failure,action)=>"Could not "+action+".";
  w.mvToast=()=>{};
  w.fetch=async(url,init={})=>{
    const body=init.body?JSON.parse(init.body):null;calls.push({url,method:init.method||"GET",body});
    return handler(url,body,calls.length);
  };
  w.eval(read("static/lightwork-settings-all.js"));
  await flush();
  return {dom,w,d,calls,confirms};
}
const row=(ui,id)=>ui.d.querySelector('[data-setting-id="'+id+'"]');
const click=async(node)=>{node.click();await flush();};

test("saving one setting sends its value and shows where it now comes from",async()=>{
  const ui=await page((url,body)=>reply(200,{changed:[body.changes[0].id],restart:false,...stateFor("safety.profile",{value:"strict",value_text:"Strict"})}));
  try{
    row(ui,"safety.profile").querySelector("select").value="strict";
    await click(row(ui,"safety.profile").querySelector("[data-save]"));
    assert.deepEqual(ui.calls[0].body,{changes:[{id:"safety.profile",value:"strict"}],confirm:false});
    assert.equal(ui.calls[0].url,"/api/v1/settings/catalog");
    assert.equal(row(ui,"safety.profile").querySelector("[data-value-text]").textContent,"Strict");
    assert.equal(row(ui,"safety.profile").querySelector("[data-source-text]").textContent,"Set here");
    assert.match(row(ui,"safety.profile").querySelector("[data-row-status]").textContent,/Saved/);
  }finally{ui.dom.window.close();}
});

test("a weakening change asks first and resends with the confirmation",async()=>{
  const ui=await page((url,body)=>body.confirm
    ?reply(200,{changed:["safety.profile"],...stateFor("safety.profile",{value:"off"})})
    :reply(409,{detail:"confirmation required",confirm:[{id:"safety.profile",label:"Safety level",warning:"Agents will be screened less strictly."}]}));
  try{
    row(ui,"safety.profile").querySelector("select").value="off";
    await click(row(ui,"safety.profile").querySelector("[data-save]"));
    assert.equal(ui.confirms.length,1);
    assert.match(ui.confirms[0],/Safety level: Agents will be screened less strictly/);
    assert.equal(ui.calls.length,2);assert.equal(ui.calls[1].body.confirm,true);
  }finally{ui.dom.window.close();}
});

test("a refused value shows the page's own sentence, never a raw status",async()=>{
  const ui=await page(()=>reply(400,{detail:"x",message:"Time limit for each command: enter at least 5."}));
  try{
    row(ui,"sandbox.timeout").querySelector("input").value="1";
    await click(row(ui,"sandbox.timeout").querySelector("[data-save]"));
    const text=row(ui,"sandbox.timeout").querySelector("[data-row-status]").textContent;
    assert.equal(text,"Time limit for each command: enter at least 5.");
    assert.doesNotMatch(text,/400|HTTP/);
  }finally{ui.dom.window.close();}
});

test("secrets are only sent after Replace, and the field is cleared afterwards",async()=>{
  const ui=await page(()=>reply(200,{changed:["notifications.ntfy_topic"],...stateFor("notifications.ntfy_topic",{is_set:true,value_text:"Set"})}));
  try{
    const r=row(ui,"notifications.ntfy_topic");
    await click(r.querySelector("[data-save]"));
    assert.equal(ui.calls.length,0);
    await click(r.querySelector("[data-secret-replace]"));
    const input=r.querySelector("[data-input]");
    assert.equal(input.hidden,false);
    input.value="topic-123";
    await click(r.querySelector("[data-save]"));
    assert.deepEqual(ui.calls[0].body.changes,[{id:"notifications.ntfy_topic",value:"topic-123"}]);
    assert.equal(input.value,"");assert.equal(input.hidden,true);
    assert.equal(r.querySelector("[data-secret-state]").textContent,"Set");
  }finally{ui.dom.window.close();}
});

test("the address list editor adds rows and the group save sends only changed settings",async()=>{
  const ui=await page(()=>reply(200,{changed:["webhooks.outbound"],restart:true,groups:[]}));
  try{
    const r=row(ui,"webhooks.outbound");
    await click(r.querySelector("[data-list-add]"));
    const item=r.querySelector("[data-list-item]");
    assert.ok(item&&ui.d.querySelector('label[for="'+item.id+'"]'));
    item.value="https://hooks.example/a";
    await click(ui.d.querySelector('[data-save-group="safety"]'));
    assert.deepEqual(ui.calls[0].body.changes,[{id:"webhooks.outbound",value:["https://hooks.example/a"]}]);
    assert.match(ui.d.getElementById("settings-all-status").textContent,/after Lightwork restarts/);
  }finally{ui.dom.window.close();}
});

test("reset all confirms and resets only settings that are not already default",async()=>{
  const ui=await page(()=>reply(200,{changed:["sandbox.timeout"],groups:[]}));
  try{
    await click(ui.d.querySelector('[data-reset-group="safety"]'));
    assert.equal(ui.confirms.length,1);
    assert.deepEqual(ui.calls[0].body.changes,[{id:"sandbox.timeout",reset:true}]);
  }finally{ui.dom.window.close();}
});

test("module switches call the module service and search filters rows",async()=>{
  const ui=await page(()=>reply(200,{section:"policy_packs",enabled:true}));
  try{
    const toggle=ui.d.querySelector("[data-module-toggle]");
    toggle.checked=true;toggle.dispatchEvent(new ui.w.Event("change",{bubbles:true}));await flush();
    assert.equal(ui.calls[0].url,"/api/v1/features/modules");
    assert.deepEqual(ui.calls[0].body,{section:"policy_packs",enabled:true});
    assert.equal(ui.d.querySelector('[data-module="policy_packs"] [data-value-text]').textContent,"On");
    const search=ui.d.getElementById("settings-all-search");
    search.value="ntfy";search.dispatchEvent(new ui.w.Event("input"));
    assert.equal(row(ui,"notifications.ntfy_topic").hidden,false);
    assert.equal(row(ui,"safety.profile").hidden,true);
    assert.equal(ui.d.querySelector('[data-group="modules"]').hidden,true);
    assert.match(ui.d.getElementById("settings-all-filter-status").textContent,/1 setting shown/);
  }finally{ui.dom.window.close();}
});
