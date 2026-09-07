"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict");
const fs=require("node:fs"), path=require("node:path");
const {JSDOM}=require("jsdom");
const folder=path.resolve(__dirname,"../../maverick_dashboard");
const markup=fs.readFileSync(path.join(folder,"templates/privacy_data_discovery.html"),"utf8").replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,"");
const source=fs.readFileSync(path.join(folder,"static/lightwork-privacy-data-discovery.js"),"utf8");
const reference="connection:cmdb:"+"a".repeat(32);
const key={name:"inventory",revision:2,reference:"evidence-key:inventory:2"};
const setup={providers:[{provider:"servicenow_cmdb",kind:"structured",resource_ref_contract:"one_admitted_cmdb_table"}],
  connections:[{name:"CMDB <img src=x>",provider:"servicenow_cmdb",origin:"https://tenant.service-now.com",reference}],evidence_keys:[key]};
async function fixture(intercept=()=>undefined) {
  const dom=new JSDOM(markup,{url:"https://lightwork.example/privacy/data-discovery",runScripts:"outside-only"}), {window}=dom;
  const document=window.document,root=document.getElementById("pdd-root"),calls=[];
  root.disabled=false;Object.assign(root.dataset,{canAdmin:"true",canOperate:"true",canAudit:"true",featureEnabled:"true",apiBase:"/api/v1/privacy/data-discovery"});
  window.TextEncoder=TextEncoder;window.TextDecoder=TextDecoder;window.confirm=()=>true;
  window.fetch=async(url,opts={})=>{
    const call={url:new URL(url,window.location),...opts};calls.push(call);
    let body=await intercept(call);
    if(body===undefined) body=call.url.pathname.endsWith("/setup")?setup:call.url.pathname.endsWith("/sources")?{items:[]}:{};
    return new Response(JSON.stringify(body),{headers:{"Content-Type":"application/json"}});
  };
  window.eval(source);
  async function settle(){for(let i=0;i<25;i++)await new Promise(done=>setImmediate(done));}
  const el=id=>document.getElementById("pdd-"+id);
  async function click(id){el(id).click();await settle();}
  function change(element,value){element.value=value;element.dispatchEvent(new window.Event("change",{bubbles:true}));}
  await settle();return{dom,window,document,calls,el,settle,click,change,form:el("source"),keys:el("key-form")};
}

test("guided setup selects exact saved references and pins source origin without credential values",async t=>{
  const ui=await fixture(call=>{
    if(call.method==="PUT" && call.url.pathname.endsWith("/sources/customers"))return{revision:1};
  });t.after(()=>ui.window.close());await ui.click("load-setup");
  ui.change(ui.form.elements.provider,"servicenow_cmdb");ui.change(ui.el("connection"),reference);
  ui.change(ui.el("evidence-key"),key.reference);
  assert.equal(ui.form.elements.origin.value,"https://tenant.service-now.com");
  assert.equal(ui.form.elements.credential_env.value,reference);assert.equal(ui.form.elements.evidence_key_env.value,key.reference);
  assert.equal(ui.el("connection").querySelector("img"),null);
  ui.form.elements.resource_ref.value="cmdb_ci";
  ui.form.dispatchEvent(new ui.window.Event("submit",{bubbles:true,cancelable:true}));await ui.settle();
  const saved=ui.calls.find(call=>call.method==="PUT");assert.ok(saved);
  const body=JSON.parse(saved.body);assert.equal(body.credential_env,reference);assert.equal(body.evidence_key_env,key.reference);
  assert.equal(body.expected_revision,0);assert.equal(ui.form.elements.expected_revision.value,"1");
});

test("key rotation remains explicit, locks duplicate submission, and never silently updates sources",async t=>{
  let release;const blocked=new Promise(done=>{release=done;});let writes=0;
  const ui=await fixture(async call=>{
    if(call.url.pathname.includes("/evidence-keys/")){writes++;await blocked;return{...key,revision:3,reference:"evidence-key:inventory:3",sources_updated:false,secret_returned:false};}
  });t.after(()=>ui.window.close());await ui.click("load-setup");
  ui.change(ui.el("key-picker"),key.reference);
  ui.form.elements.evidence_key_env.value="evidence-key:inventory:1";
  const event=()=>new ui.window.Event("submit",{bubbles:true,cancelable:true});
  ui.window.confirm=()=>false;ui.keys.dispatchEvent(event());await ui.settle();assert.equal(writes,0);
  ui.window.confirm=()=>true;ui.keys.dispatchEvent(event());await new Promise(done=>setImmediate(done));
  assert.equal(ui.keys.querySelector("button").disabled,true);
  ui.keys.dispatchEvent(event());release();await ui.settle();assert.equal(writes,1);
  assert.equal(ui.form.elements.evidence_key_env.value,"evidence-key:inventory:1");
  assert.match(ui.el("status").textContent,/existing sources were not changed/);
});

test("reloading a source with a retired key keeps that old reference until a deliberate selection",async t=>{
  const ui=await fixture(call=>call.url.pathname.endsWith("/sources")?{items:[{source_id:"customers",kind:"structured",provider:"servicenow_cmdb",origin:"https://tenant.service-now.com",resource_ref:"cmdb_ci",credential_env:reference,evidence_key_env:"evidence-key:inventory:1",revision:7}]}:undefined);
  t.after(()=>ui.window.close());await ui.click("load-setup");ui.change(ui.el("source-picker"),"customers");
  assert.equal(ui.form.elements.expected_revision.value,"7");assert.equal(ui.form.elements.evidence_key_env.value,"evidence-key:inventory:1");
  assert.equal(ui.el("evidence-key").selectedOptions[0].disabled,true);
  ui.change(ui.el("evidence-key"),key.reference);assert.equal(ui.form.elements.evidence_key_env.value,key.reference);
  assert.equal(ui.calls.filter(call=>call.method==="PUT").length,0);
});

test("access test submits only saved identity and version, without unsaved origin or any source bytes",async t=>{
  const ui=await fixture(call=>call.url.pathname.endsWith("/test-access")?{access:"readable",raw_data_returned:false,report_created:false}:undefined);
  t.after(()=>ui.window.close());ui.form.elements.expected_revision.value="4";ui.form.elements.origin.value="https://unsaved.example";
  ui.window.confirm=()=>false;await ui.click("test-source");assert.equal(ui.calls.filter(call=>call.method==="POST").length,0);
  ui.window.confirm=()=>true;await ui.click("test-source");
  const call=ui.calls.find(call=>call.method==="POST");assert.deepEqual(JSON.parse(call.body),{confirm:true,expected_revision:4});
  assert.match(ui.el("status").textContent,/does not verify complete coverage/);
});

test("an existing deployment-managed provider keeps its exact identity when editing saved settings",async t=>{
  const ui=await fixture(call=>call.url.pathname.endsWith("/sources")?{items:[{source_id:"customers",kind:"structured",provider:"custom_adapter",origin:"https://custom.example",resource_ref:"customers",credential_env:"LEGACY_TOKEN",evidence_key_env:"LEGACY_KEY",revision:4}]}:undefined);
  t.after(()=>ui.window.close());await ui.click("load-setup");ui.change(ui.el("source-picker"),"customers");
  assert.equal(ui.form.elements.provider.value,"custom_adapter");assert.equal(ui.form.elements.credential_env.value,"LEGACY_TOKEN");
  assert.equal(ui.form.elements.evidence_key_env.value,"LEGACY_KEY");assert.equal(ui.form.elements.expected_revision.value,"4");
});
