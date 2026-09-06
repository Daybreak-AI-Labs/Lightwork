"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, "../../maverick_dashboard");
const code = fs.readFileSync(path.join(root, "static/lightwork-vendor-onboarding.js"), "utf8");
const markup = fs.readFileSync(path.join(root, "templates/vendor_onboarding.html"), "utf8").replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
const response = (body, status=200) => ({ok:status >= 200 && status < 300, status, async json(){return body;}});
const tick = () => new Promise(resolve => setImmediate(resolve));
function record(id="one", extra={}) {
  return {id, vendor_name:`Synthetic ${id}`, revision:1, document_id:"", document_ids:[], uploads:[],
    stage:"intake", evidence:null, assessment:null, operation:null, outcome:null, metrics:{}, ...extra};
}
async function fixture(records, intercept=()=>undefined) {
  const dom = new JSDOM(markup, {url:"https://lightwork.example/third-party/vendor-onboarding",runScripts:"outside-only"});
  const {window} = dom, calls=[];
  const el = id => window.document.getElementById(`vo-${id}`);
  window.fetch = async (url, opts={}) => {
    const call={url:new URL(url,window.location),...opts}; calls.push(call);
    const intercepted = await intercept(call, records);
    if (intercepted !== undefined) return intercepted;
    if (call.url.pathname.endsWith("/cases")) return response({cases:records});
    const row=records.find(item=>call.url.pathname.endsWith(`/cases/${item.id}`));
    return response(row || {detail:"Unexpected request"},row?200:404);
  };
  window.eval(code);
  async function settle() {
    for (let i=0;i<30;i++) { await tick(); if (window.document.getElementById("vendor-onboarding").getAttribute("aria-busy")==="false") return; }
    throw new Error("UI did not settle");
  }
  async function load(id) { el("cases").querySelector(`[data-case-id="${id}"]`).click(); await settle(); }
  await settle();
  return {dom,window,el,calls,settle,load,async click(id){el(id).click();await settle();}};
}

test("Drive picker selects supported documents without hand-entered IDs for new and existing requests", async t=>{
  const records=[record()];
  const ui=await fixture(records,call=>{
    if (call.url.pathname.endsWith("/documents") && call.method==="GET")
      return response({documents:[{id:"drive-policy",name:"<img src=x onerror=alert(1)> Policy"}],has_more:true});
    if (call.url.pathname.endsWith("/documents") && call.method==="POST") {
      const body=JSON.parse(call.body);
      assert.equal(body.expected_revision,1); assert.deepEqual(body.document_ids,["drive-policy"]);
      records[0]=record("one",{revision:2,document_ids:body.document_ids}); return response(records[0]);
    }
  }); t.after(()=>ui.dom.window.close());
  ui.el("document-search").value="policy"; await ui.click("search-documents");
  assert.equal(ui.el("document-results").querySelector("img"),null);
  ui.el("document-results").querySelector("button").click();
  assert.equal(ui.el("document").value,"drive-policy");
  assert.match(ui.el("new-documents").textContent,/1 Drive document/);
  await ui.load("one"); ui.el("document-target").value="selected";
  ui.el("document-results").querySelector("button").click(); await ui.settle();
  assert.equal(ui.el("case-documents").value,"drive-policy");
  assert.match(ui.el("selected-documents").textContent,/Remove Drive document/);
});

test("multiple uploads use each acknowledged version, lock controls, and require an explicit document date", async t=>{
  const records=[record()]; let release;
  const blocked = new Promise(resolve=>{release=resolve;}); let uploads=0;
  const ui=await fixture(records,async call=>{
    if (!call.url.pathname.endsWith("/upload")) return;
    uploads++;
    assert.equal(call.url.searchParams.get("expected_revision"),String(uploads));
    assert.equal(call.url.searchParams.get("document_date"),String(Date.parse("2026-09-01T00:00:00Z")/1000));
    assert.equal(call.credentials,"same-origin");
    if (uploads===1) await blocked;
    records[0]=record("one",{revision:uploads+1,uploads:Array.from({length:uploads},(_,i)=>({document_id:`u${i}`,name:`Policy ${i}`,status:"available",modified_at:1}))});
    return response(records[0]);
  }); t.after(()=>ui.dom.window.close());
  await ui.load("one");
  Object.defineProperty(ui.el("upload"),"files",{value:[new ui.window.File(["text"],"one.txt"),new ui.window.File(["%PDF"],"two.pdf")],configurable:true});
  await ui.click("upload-submit"); assert.equal(uploads,0); assert.match(ui.el("message").textContent,/document date/);
  ui.el("document-date").value="2026-09-01";
  ui.el("upload-submit").click(); await tick();
  assert.equal(ui.el("upload-submit").disabled,true); assert.equal(ui.el("save-documents").disabled,true);
  ui.el("upload-submit").click(); release(); await ui.settle();
  assert.equal(uploads,2); assert.match(ui.el("message").textContent,/Files added/);
  assert.equal(ui.el("assess").disabled,true);
});

test("a failed second upload retains the first acknowledgement and makes the failure visible", async t=>{
  const records=[record()]; let uploads=0;
  const ui=await fixture(records,call=>{
    if (!call.url.pathname.endsWith("/upload")) return;
    uploads++;
    if (uploads===2) return response({detail:"Document was rejected by extraction limits"},413);
    records[0]=record("one",{revision:2,uploads:[{document_id:"uploaded",name:"first.txt",status:"available",modified_at:1}]});
    return response(records[0]);
  }); t.after(()=>ui.dom.window.close()); await ui.load("one");
  Object.defineProperty(ui.el("upload"),"files",{value:[new ui.window.File(["a"],"first.txt"),new ui.window.File(["b"],"second.pdf")]});
  ui.el("document-date").value="2026-09-01"; await ui.click("upload-submit");
  assert.equal(uploads,2); assert.match(ui.el("uploaded-documents").textContent,/first.txt/);
  assert.equal(ui.el("message").getAttribute("role"),"alert"); assert.match(ui.el("message").textContent,/extraction limits/);
  assert.equal(ui.el("upload-submit").disabled,false);
});

test("citation view distinguishes PDF pages, uploaded source dates and conflicting renewal claims", async t=>{
  const citation={quote:"Model provenance is undisclosed.",pages:[3],start:0,end:32};
  const row=record("one",{evidence:{status:"available",documents:[{document_id:"pdf",name:"Policy.pdf",source:"upload",status:"available",version:"1",modified_at:100,
    extracted:{answers:{aiv_provenance:{answer:"no",citations:[citation]}}}}],changes:{comparison:"previous_review",documents:[{document_id:"pdf",change:"changed",before_version:"1",after_version:"2",
      answer_changes:[{question_id:"aiv_provenance",before:"yes",after:"no",before_citations:[{quote:"Model provenance is documented."}],after_citations:[citation]}]}]}},
    assessment:{answers:{aiv_provenance:{answer:"unknown",note:"Conflicting current document answers require human resolution."}},
      template_snapshot:{questions:[{id:"aiv_provenance",text:"Model provenance"}]}}});
  const ui=await fixture([row,record("two")]);t.after(()=>ui.dom.window.close());await ui.load("one");
  assert.match(ui.el("assessment").textContent,/pages 3/); assert.match(ui.el("assessment").textContent,/Conflicting/);
  assert.match(ui.el("evidence").textContent,/uploader asserted/);
  assert.match(ui.el("evidence-changes").textContent,/Previous: Model provenance is documented/);
  ui.el("document-date").value="2026-09-01";await ui.load("two");assert.equal(ui.el("document-date").value,"");
});
