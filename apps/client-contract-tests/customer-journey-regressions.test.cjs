"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname,"../..");
const read = file => fs.readFileSync(path.join(root,file),"utf8");
const flush = async () => {for(let i=0;i<12;i++)await new Promise(setImmediate);};
const deferred = () => {let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};};
function clock() {
  let sequence=0;const timers=new Map();
  return {timers,setTimeout(fn,ms){timers.set(++sequence,{fn,ms});return sequence;},clearTimeout(id){timers.delete(id);},
    fire(ms){for(const [id,timer] of [...timers])if(timer.ms===ms){timers.delete(id);timer.fn();}}};
}
function intake(fetch) {
  const source=read("demo/pia-concierge/templates/intake.html"),buttons=[{disabled:false},{disabled:true}],messages=[],scheduled=[];
  const context={fetch,FormData,controls:{querySelectorAll:()=>buttons},bubble:(who,text)=>messages.push({who,text}),
    CASE:"case-1",DTOKEN:"",ANSWERED:{},idx:0,ask(){},setTimeout(fn){scheduled.push(fn);}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("let answering = false;"),source.indexOf("async function sendFree(")),context);
  return {context,buttons,messages,scheduled};
}
for(const failure of [403,404,500,"network","invalid-json","unacknowledged"])test(`PIA answer ${failure} preserves question and permits retry`,async()=>{
  let fail=true;
  const f=intake(async()=>{
    if(!fail)return {ok:true,json:async()=>({ok:true})};
    if(failure==="network")throw new Error("Connection lost");
    return {ok:typeof failure!=="number",json:async()=>{
      if(failure==="invalid-json")throw new Error("Invalid JSON");
      return {ok:false,error:"Not saved"};
    }};
  });
  await f.context.answer({id:"q1"},"yes","Yes");
  assert.equal(f.context.idx,0);assert.deepEqual(f.context.ANSWERED,{});
  assert.equal(f.scheduled.length,0);assert.equal(f.buttons[0].disabled,false);assert.equal(f.buttons[1].disabled,true);
  assert.match(f.messages.at(-1).text,/not saved/);
  fail=false;await f.context.answer({id:"q1"},"yes","Yes");
  assert.equal(f.context.idx,1);assert.equal(f.context.ANSWERED.q1,true);assert.equal(f.scheduled.length,1);
});
test("PIA guided answer ignores duplicate clicks until acknowledgement",async()=>{
  const pending=deferred();let calls=0;const f=intake(async()=>{calls++;return pending.promise;});
  const first=f.context.answer({id:"q1"},"yes","Yes");
  await f.context.answer({id:"q1"},"no","No");assert.equal(calls,1);
  pending.resolve({ok:true,json:async()=>({ok:true})});await first;assert.equal(f.context.idx,1);
  await f.context.answer({id:"q1"},"no","No");assert.equal(calls,1);assert.equal(f.context.idx,1);
  assert.equal(f.buttons[0].disabled,true);
});
async function statusWidget(fetch) {
  const dom=new JSDOM('<body><script id="embed" data-interval="5000"></script></body>',{url:"https://fixture.invalid/",runScripts:"outside-only"});
  const w=dom.window,timers=clock();
  Object.defineProperty(w.document,"currentScript",{value:w.document.getElementById("embed")});
  Object.assign(w,{fetch,AbortController,setTimeout:timers.setTimeout,clearTimeout:timers.clearTimeout});
  w.eval(read("extensions/widget/maverick-widget.js"));await flush();
  return {dom,w,timers,root:w.document.querySelector("[data-maverick-widget]").shadowRoot,
    close(){w.dispatchEvent(new w.Event("pagehide"));w.close();}};
}
const finished=[{id:1,status:"done",title:"Old result",result:"Removed output"}];
for(const next of [[],[{id:2,status:"pending",title:"Working"}]])test(`status widget clears old terminal result for ${next.length?"pending":"empty"} snapshot`,async()=>{
  let rows=finished;
  const f=await statusWidget(async()=>({ok:true,status:200,json:async()=>rows}));
  try {
    assert.match(f.root.querySelector("#last").textContent,/Removed output/);
    rows=next;f.timers.fire(5000);await flush();
    assert.equal(f.root.querySelector("#last").textContent,"No finished runs yet.");
    assert.equal(f.root.querySelector("#n-done").textContent,"0");
  }finally{f.close();}
});
for(const stage of ["fetch","body"])test(`status widget bounds hung ${stage}, rejects late content and retries`,async()=>{
  const pending=deferred();let calls=0,signal;
  const f=await statusWidget(async(_url,options)=>{
    calls++;signal=options.signal;
    if(calls===2)return stage==="fetch"?pending.promise:{ok:true,status:200,json:()=>pending.promise};
    return {ok:true,status:200,json:async()=>calls===1?finished:[]};
  });
  try {
    f.timers.fire(5000);await flush();assert.equal(calls,2);
    f.timers.fire(15000);await flush();assert.equal(signal.aborted,true);
    assert.match(f.root.querySelector("#meta").textContent,/timed out.*stale/);
    assert.deepEqual([...f.timers.timers.values()].map(timer=>timer.ms),[5000]);
    pending.resolve(stage==="fetch"?{ok:true,json:async()=>[{...finished[0],result:"LATE"}]}:[{...finished[0],result:"LATE"}]);
    await flush();assert.doesNotMatch(f.root.querySelector("#last").textContent,/LATE/);
    f.timers.fire(5000);await flush();assert.equal(calls,3);
    assert.equal(f.root.querySelector("#last").textContent,"No finished runs yet.");
  }finally{f.close();}
});
async function pitch(fetch) {
  const dom=new JSDOM('<html><head><meta name="web3forms-access-key" content="synthetic-key"></head><body><a data-demo href="#">Demo</a></body></html>',{url:"https://fixture.invalid/",runScripts:"outside-only"});
  const w=dom.window;await new Promise(resolve=>w.document.addEventListener("DOMContentLoaded",resolve,{once:true}));
  w.fetch=fetch;w.eval(read("pitch/site/app.js"));const form=w.document.querySelector("#demo-form");
  for(const name of ["name","email","company","message","botcheck"])Object.defineProperty(form,name,{value:form.elements.namedItem(name),configurable:true});
  form.elements.namedItem("name").value="Synthetic Name";form.elements.namedItem("email").value="person@example.invalid";
  return {dom,w,form,button:form.querySelector(".df-submit"),open(){w.document.querySelector("[data-demo]").click();},
    close(){w.document.querySelector(".modal-close").click();},submit(){form.dispatchEvent(new w.Event("submit",{bubbles:true,cancelable:true}));}};
}
test("successful optional demo request can be reopened and submitted again",async()=>{
  let calls=0;const f=await pitch(async()=>{calls++;return {ok:true,json:async()=>({success:true})};});
  try {
    f.open();f.submit();await flush();assert.equal(f.form.hidden,true);f.close();f.open();
    assert.equal(f.form.hidden,false);assert.equal(f.button.disabled,false);assert.equal(f.button.textContent,"Request a demo");
    f.submit();await flush();assert.equal(calls,2);
  }finally{f.dom.window.close();}
});
test("late demo completion cannot hide or unlock a newer pending form",async()=>{
  const first=deferred(),second=deferred();let calls=0;
  const f=await pitch(async()=>++calls===1?first.promise:second.promise);
  try {
    f.open();f.submit();f.submit();assert.equal(calls,1);f.close();f.open();f.submit();assert.equal(calls,2);
    first.resolve({ok:true,json:async()=>({success:true})});await flush();
    assert.equal(f.form.hidden,false);assert.equal(f.button.disabled,true);
    second.resolve({ok:true,json:async()=>({success:true})});await flush();assert.equal(f.form.hidden,true);assert.equal(f.button.disabled,false);
  }finally{f.dom.window.close();}
});
