"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const {webcrypto} = require("node:crypto");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, "../../maverick_dashboard");
const read = name => fs.readFileSync(path.join(root, name), "utf8");
const flush = async () => { for (let i=0;i<12;i++) await new Promise(setImmediate); };
const response = (status, body) => ({ok:status<400,status,headers:{get:()=>null},text:async()=>JSON.stringify(body),json:async()=>body});
function windowFor(html) {
  const dom = new JSDOM(html,{url:"https://customer.example.test/",runScripts:"outside-only"});
  Object.defineProperty(dom.window,"crypto",{value:webcrypto});
  dom.window.TextEncoder = TextEncoder;
  return dom;
}
async function sdkFixture(initialGranted) {
  const dom=windowFor('<div id="consent"></div>'), w=dom.window;
  const state={granted:initialGranted,revision:0,calls:[],failNext:false,hold:null};
  w.fetch=async(url,options)=>{
    if(url.endsWith("/experiments/assignment"))return response(404,{detail:"experiments disabled"});
    assert.ok(url.endsWith("/preferences"),url);
    if(options.method==="PUT") {
      const body=JSON.parse(options.body);state.calls.push(body);
      if(state.hold)await state.hold;
      if(state.failNext){state.failNext=false;return response(503,{detail:"preference authority unavailable"});}
      if(body.expected_revision!==state.revision)return response(409,{detail:"revision changed"});
      state.granted=body.choices[0].action==="grant";state.revision++;
      return response(200,{profile_revision:state.revision,receipt_id:"receipt-"+state.revision});
    }
    return response(200,{profile_revision:state.revision,preferences:[{purpose_id:"analytics",granted:state.granted}]});
  };
  w.eval(read("static/lightwork-consent-sdk.js"));
  const sdk=new w.LightworkConsent({endpoint:"https://api.example.test/consent",clientCredential:"synthetic-client",autoPrivacySignals:false});
  sdk.profileCredential="synthetic-profile";
  sdk.configuration={publication:{publication_sha256:"a".repeat(64),bundle_sha256:"b".repeat(64)},configuration_sha256:"c".repeat(64),policy_version:1,policy_sha256:"d".repeat(64),rule:{locale:"en-US"},configuration:{purposes:[{id:"analytics",lawful_basis:"consent",title:"Analytics",description:"Optional analytics"}],template:{title:"Preferences",body:"Choose",actions:{}},notice:{title:"Notice",body:"Notice text"}}};
  const container=w.document.getElementById("consent");await sdk.renderPreferenceCenter(container);
  return {dom,w,sdk,state,container,buttons:[...container.querySelectorAll("button")],checkbox:container.querySelector("input")};
}
for(const [initial,index] of [[true,1],[false,0]])test(`bulk ${initial?"reject":"accept"} and unchanged save agree with authoritative readback`,async()=>{
  const f=await sdkFixture(initial);
  try {
    f.buttons[index].click();await flush();
    assert.equal(f.state.granted,!initial);assert.equal(f.checkbox.checked,!initial);
    assert.match(f.container.textContent,/choices are saved/);
    f.buttons[2].click();await flush();
    assert.equal(f.state.granted,!initial);assert.equal(f.state.calls.length,2);
    assert.equal(f.state.calls[1].choices[0].action,initial?"deny":"grant");
  }finally{f.dom.window.close();}
});
test("failed bulk save keeps the requested draft visible and supports same-choice retry",async()=>{
  const f=await sdkFixture(true);
  try {
    f.state.failNext=true;f.buttons[1].click();await flush();
    assert.equal(f.state.granted,true);assert.equal(f.checkbox.checked,false);
    assert.match(f.container.textContent,/could not be confirmed.*unavailable/);
    assert.ok(f.buttons.every(button=>!button.disabled));assert.equal(f.checkbox.disabled,false);
    f.buttons[2].click();await flush();assert.equal(f.state.granted,false);
    assert.equal(f.state.calls[1].choices[0].action,"deny");
  }finally{f.dom.window.close();}
});
test("preference mutations remain serial while a save is pending",async()=>{
  const f=await sdkFixture(false);let release;
  try {
    f.state.hold=new Promise(resolve=>{release=resolve;});
    f.buttons[0].click();await flush();f.buttons[1].click();f.buttons[2].click();
    assert.equal(f.state.calls.length,1);assert.equal(f.checkbox.disabled,true);
    release();await flush();assert.equal(f.state.granted,true);assert.equal(f.checkbox.checked,true);
  }finally{f.dom.window.close();}
});
test("pending experiment reporting cannot keep confirmed preference controls locked",async()=>{
  const f=await sdkFixture(false);
  try {
    f.sdk.reportExperimentOutcome=()=>new Promise(()=>{});
    f.buttons[0].click();await flush();
    assert.equal(f.state.granted,true);assert.equal(f.checkbox.checked,true);
    assert.ok(f.buttons.every(button=>!button.disabled));assert.equal(f.checkbox.disabled,false);
  }finally{f.dom.window.close();}
});
async function policyFixture() {
  const source=read("templates/consent_preferences.html");
  const dom=windowFor(source.replace(/\{%[\s\S]*?%\}/g,"").replace(/\{\{[\s\S]*?\}\}/g,""));
  const w=dom.window,state={current:0,payloads:[]};
  w.document.querySelectorAll("fieldset").forEach(node=>{node.disabled=false;});
  w.fetch=async(url,options)=>{
    if(url.includes("/policies?"))return response(200,{policies:state.current?[{policy_id:"global-consent",version:state.current}]:[],truncated:false});
    if(url.endsWith("/policies")){
      const body=JSON.parse(options.body);state.payloads.push(body);
      if(body.expected_current_version!==state.current)return response(409,{detail:"policy current version changed"});
      state.current=body.version;return response(201,body);
    }
    return response(200,url.includes("profiles?")?{profiles:[]}:{});
  };
  w.eval(read("static/lightwork-consent-policy-editor.js"));
  w.eval([...source.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1]);
  const form=w.document.getElementById("cp-policy-form");
  form.elements.notice_sha256.value="a".repeat(64);form.elements.effective_at.value="1800000000";
  return {dom,w,state,form,submit(){form.dispatchEvent(new w.Event("submit",{bubbles:true,cancelable:true}));},load(){w.document.getElementById("cp-policy-load").click();}};
}
test("policy form publishes v1 and v2 with the reviewed current version",async()=>{
  const f=await policyFixture();
  try{
    f.submit();await flush();assert.equal(f.state.payloads.length,0);
    f.load();await flush();f.submit();await flush();f.submit();await flush();
    assert.deepEqual(f.state.payloads.map(body=>[body.version,body.expected_current_version]),[[1,0],[2,1]]);
    assert.equal(f.state.current,2);
  }finally{f.dom.window.close();}
});
test("policy conflict preserves the reviewed expectation until explicit reload",async()=>{
  const f=await policyFixture();
  try{
    f.state.current=1;f.load();await flush();f.state.current=2;
    f.submit();await flush();assert.equal(f.state.current,2);
    assert.equal(f.form.elements.expected_current_version.value,"1");
    assert.match(f.w.document.getElementById("cp-status").textContent,/changed.*load the current version/);
    f.load();await flush();f.submit();await flush();assert.equal(f.state.current,3);
    assert.equal(f.state.payloads[1].expected_current_version,2);
  }finally{f.dom.window.close();}
});
