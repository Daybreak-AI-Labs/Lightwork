"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {JSDOM}=require("jsdom"),folder=path.resolve(__dirname,"../../maverick_dashboard");
const markup=fs.readFileSync(path.join(folder,"templates/project_reviews.html"),"utf8").replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,"");
const source=fs.readFileSync(path.join(folder,"static/lightwork-project-reviews.js"),"utf8");
const snapshot={goal_id:7,title:"Captured <img src=x>",result:"Sensitive <img src=x onerror=alert(1)> result",suite:"finance"};
const packet={id:"PRP-one",revision:1,source_version:"a".repeat(64),snapshot,owner:"user:owner",reviewer:"user:reviewer",project_name:"Project",status:"pending",due_at:2000000000,note:"Review this.",history:[{actor:"user:owner",action:"sent",at:1000,note:"Review this."}],stale:false,can_respond:true};
async function fixture({principal="user:owner",intercept=()=>undefined,fragment=""}={}){
  const dom=new JSDOM(markup,{url:"https://lightwork.example/project-reviews"+fragment,runScripts:"outside-only"}),{window}=dom,calls=[];
  window.document.getElementById("pr-root").dataset.context="context-one";window.TextEncoder=TextEncoder;window.confirm=()=>true;
  window.fetch=async(url,options={})=>{const call={url:new URL(url,window.location),...options};calls.push(call);const injected=await intercept(call);if(injected instanceof Response)return injected;let data=injected;
    if(data===undefined){const route=call.url.pathname;
      if(route.endsWith("/preview"))data={snapshot,source_version:packet.source_version};
      else if(route.endsWith("/projects/1"))data={team:{revision:2,members:["user:reviewer"]},eligible_members:["user:reviewer"],goals:[{id:7,title:"Result",status:"done"}]};
      else if(route.includes("/packets/"))data=packet;
      else if(route.endsWith("/packets"))data=packet;
      else data={context:"context-one",principal,projects:principal==="user:owner"?[{id:1,name:"Project"}]:[],items:[{...packet,title:snapshot.title}],counts:{total:1,pending:1,overdue:0},total:1,offset:0,limit:50,has_more:false};
    }return new Response(JSON.stringify(data),{headers:{"Content-Type":"application/json"}});
  };
  const el=id=>window.document.getElementById("pr-"+id);async function settle(){for(let i=0;i<20;i++)await new Promise(done=>setImmediate(done));}
  async function click(id){el(id).click();await settle();}
  function change(id,value){el(id).value=value;el(id).dispatchEvent(new window.Event("input",{bubbles:true}));el(id).dispatchEvent(new window.Event("change",{bubbles:true}));}
  function submit(id){el(id).dispatchEvent(new window.Event("submit",{cancelable:true,bubbles:true}));}
  window.eval(source);await settle();return{window,calls,el,settle,click,change,submit};
}
async function preview(ui){ui.change("project","1");await ui.settle();ui.change("goal","7");await ui.click("preview");ui.change("reviewer","user:reviewer");ui.change("due","2027-01-01T12:00");ui.change("note","Review the explicit limitations.");}
test("sharing needs a current preview and explicit confirmation, and keeps the exact captured version",async t=>{
  const ui=await fixture();t.after(()=>ui.window.close());assert.equal(ui.el("send-button").disabled,true);ui.submit("send");await ui.settle();assert.equal(ui.calls.some(row=>row.method==="POST"),false);
  await preview(ui);assert.equal(ui.el("preview-text").querySelector("img"),null);assert.match(ui.el("preview-text").textContent,/Sensitive/);
  ui.window.confirm=()=>false;ui.submit("send");await ui.settle();assert.equal(ui.calls.some(row=>row.method==="POST"),false);
  ui.window.confirm=()=>true;ui.submit("send");await ui.settle();const write=ui.calls.find(row=>row.method==="POST"),body=JSON.parse(write.body);
  assert.equal(body.source_version,packet.source_version);assert.equal(body.expected_team_revision,2);assert.equal(body.goal_id,7);assert.equal(write.headers["X-Lightwork-Review-Context"],"context-one");assert.match(ui.el("status").textContent,/No task was run or signed off/);
});
test("an uncertain send preserves input and exact retry identity while refusing duplicate clicks",async t=>{
  let release,writes=0;const blocked=new Promise(done=>release=done);
  const ui=await fixture({intercept:async call=>{if(call.method==="POST"){writes++;await blocked;throw new Error("Connection interrupted");}}});t.after(()=>ui.window.close());await preview(ui);
  ui.submit("send");await new Promise(done=>setImmediate(done));ui.submit("send");assert.equal(ui.el("send-button").disabled,true);release();await ui.settle();assert.equal(writes,1);
  assert.equal(ui.el("note").value,"Review the explicit limitations.");ui.submit("send");await ui.settle();const bodies=ui.calls.filter(row=>row.method==="POST").map(row=>row.body);assert.equal(bodies.length,2);assert.equal(bodies[0],bodies[1]);
});
test("same packet refresh keeps an unsaved review comment; changed packet version resets the decision",async t=>{
  let revision=1;const ui=await fixture({principal:"user:reviewer",intercept:call=>call.url.pathname.includes("/packets/")?{...packet,revision}:undefined});t.after(()=>ui.window.close());
  ui.el("inbox").querySelector("button").click();await ui.settle();ui.change("response-note","A correction still being written.");ui.change("action","approve");await ui.click("refresh");assert.equal(ui.el("response-note").value,"A correction still being written.");assert.equal(ui.el("action").value,"approve");
  revision=2;await ui.click("refresh");assert.equal(ui.el("response-note").value,"");assert.equal(ui.el("action").value,"comment");assert.equal(ui.el("result").querySelector("img"),null);
});
test("stale packets cannot be approved in the UI and revoked packet reads clear prior content",async t=>{
  let revoked=false;const ui=await fixture({principal:"user:reviewer",intercept:call=>call.url.pathname.includes("/packets/")?(revoked?new Response('{"detail":"Access revoked"}',{status:404}):{...packet,stale:true}):undefined});t.after(()=>ui.window.close());
  ui.el("inbox").querySelector("button").click();await ui.settle();assert.equal(ui.el("action").querySelector('option[value="approve"]').disabled,true);assert.match(ui.el("stale").textContent,/source task changed/i);
  revoked=true;await ui.click("refresh");assert.equal(ui.el("packet").hidden,true);assert.equal(ui.el("result").textContent,"");assert.match(ui.el("status").textContent,/Access revoked/);
});

test("failed refresh authorization clears prior packet and project content",async t=>{
  let denied=false;const ui=await fixture({intercept:call=>denied&&call.url.pathname==="/api/v1/project-reviews"?new Response('{"detail":"Workspace changed"}',{status:409}):undefined});t.after(()=>ui.window.close());await preview(ui);ui.el("inbox").querySelector("button").click();await ui.settle();
  denied=true;await ui.click("refresh");assert.equal(ui.el("inbox").textContent,"");assert.equal(ui.el("result").textContent,"");assert.equal(ui.el("preview-text").textContent,"");assert.equal(ui.el("owner").hidden,true);assert.equal(ui.el("packet").hidden,true);assert.equal(ui.el("response-button").disabled,true);
  assert.equal(ui.calls.every(call=>call.cache==="no-store"),true);
});

test("packet fragments read only the exact permitted record after the context checked inbox",async t=>{
  const identifier="PRP-"+"c".repeat(32);
  const ui=await fixture({principal:"user:reviewer",fragment:"#packet="+identifier,intercept:call=>call.url.pathname.endsWith(identifier)?{...packet,id:identifier}:undefined});t.after(()=>ui.window.close());
  assert.equal(ui.calls[0].url.pathname,"/api/v1/project-reviews");assert.equal(ui.calls[1].url.pathname,"/api/v1/project-reviews/packets/"+identifier);assert.equal(ui.calls[1].headers["X-Lightwork-Review-Context"],"context-one");assert.match(ui.el("result").textContent,/Sensitive/);assert.equal(ui.calls.some(call=>call.method==="POST"),false);
  const denied=await fixture({fragment:"#packet="+identifier,intercept:call=>call.url.pathname.endsWith(identifier)?new Response('{"detail":"Access revoked"}',{status:404}):undefined});t.after(()=>denied.window.close());assert.equal(denied.el("packet").hidden,true);assert.equal(denied.el("result").textContent,"");assert.match(denied.el("status").textContent,/Access revoked/);
  const changed=await fixture({fragment:"#packet="+identifier,intercept:call=>call.url.pathname==="/api/v1/project-reviews"?{context:"other-account"}:undefined});t.after(()=>changed.window.close());assert.equal(changed.calls.length,1);assert.equal(changed.el("result").textContent,"");
  const invalid=await fixture({fragment:"#packet=../../secrets"});t.after(()=>invalid.window.close());assert.equal(invalid.calls.length,1);
});
