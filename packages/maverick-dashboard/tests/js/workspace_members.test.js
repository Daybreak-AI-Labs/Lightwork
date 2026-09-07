"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),{JSDOM}=require("jsdom");
const file=path.resolve(__dirname,"../../maverick_dashboard/static/lightwork-workspace-members.js"),source=fs.readFileSync(file,"utf8");
async function fixture(){
  const dom=new JSDOM('<section id="wm-root" data-tenant="tenant-a"><p id="wm-status"></p><button id="wm-refresh">Refresh</button><ul id="wm-list"></ul><form id="wm-form"><input id="wm-principal"><select id="wm-role"><option value="operator">Operator</option><option value="viewer">Viewer</option></select><button>Save</button></form></section>',{url:"https://lightwork.example/users",runScripts:"outside-only"}),{window}=dom,calls=[];
  window.confirm=()=>true;window.fetch=async(url,options={})=>{calls.push({url,...options});return options.method?new Response(null,{status:204}):new Response('{"user:reviewer":"operator"}');};
  window.eval(source);const el=id=>window.document.getElementById("wm-"+id);async function settle(){for(let i=0;i<10;i++)await new Promise(done=>setImmediate(done));}
  return{window,calls,el,settle};
}
test("workspace membership uses the existing explicit-tenant role API and demands confirmation",async t=>{
  const ui=await fixture();t.after(()=>ui.window.close());ui.el("principal").value="user:reviewer";ui.el("role").value="viewer";
  const submit=()=>ui.el("form").dispatchEvent(new ui.window.Event("submit",{bubbles:true,cancelable:true}));ui.window.confirm=()=>false;submit();await ui.settle();assert.equal(ui.calls.length,0);
  ui.window.confirm=()=>true;submit();await ui.settle();const write=ui.calls[0];assert.equal(write.method,"PUT");assert.equal(write.url,"/api/v1/admin/tenants/tenant-a/roles/user%3Areviewer");assert.equal(write.headers["X-Lightwork-Expected-Tenant"],"tenant-a");assert.deepEqual(JSON.parse(write.body),{role:"viewer"});
});
test("workspace removal stays explicit and failure preserves the form",async t=>{
  const ui=await fixture();t.after(()=>ui.window.close());ui.el("refresh").click();await ui.settle();ui.window.confirm=()=>false;ui.el("list").querySelectorAll("button")[1].click();await ui.settle();assert.equal(ui.calls.some(row=>row.method==="DELETE"),false);
  ui.el("principal").value="user:reviewer";ui.window.confirm=()=>true;ui.window.fetch=async()=>new Response('{"detail":"Membership unavailable"}',{status:503});ui.el("form").dispatchEvent(new ui.window.Event("submit",{bubbles:true,cancelable:true}));await ui.settle();assert.equal(ui.el("principal").value,"user:reviewer");assert.match(ui.el("status").textContent,/Membership unavailable/);
});
