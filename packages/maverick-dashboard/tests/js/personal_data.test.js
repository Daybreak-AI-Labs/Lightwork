"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-personal-data.js"), "utf8");
function fixture(inventory) {
  class Node {
    constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.style = {}; this.textContent = ""; }
    appendChild(node) { this.children.push(node); return node; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(name, fn) { this.events[name] = fn; }
  }
  const nodes = {}, el = id => nodes[id] || (nodes[id] = new Node("div"));
  el("pd-bootstrap").textContent = JSON.stringify({context:"c",identity:{channels:[],links:[],challenges:[]},scopes:[],sharing:{},policy:{},can_review_deletion:false});
  vm.runInNewContext(source, {document:{getElementById:el,createElement:tag=>new Node(tag),createTextNode:text=>Object.assign(new Node("text"),{textContent:text})}, Option: function(label,value){this.textContent=label;this.value=value;},
    window:{confirm:()=>true}, fetch:async()=>({ok:true,json:async()=>inventory}), Date, console});
  return {el, async click(id) { el(id).events.click(); await new Promise(resolve=>setImmediate(resolve)); }};
}
function content(node) { return node.textContent + node.children.map(content).join(" "); }
test("store inventory distinguishes draft counts from owned learning provenance", async()=>{
  const ui=fixture({holds:{blocked:false},limits:"Specific stores only",tasks:[],stores:[
    {name:"Recovery",state:"available",inventory:{saved:2,discarded:1}},
    {name:"Learning",state:"current-authorized-only",inventory:{tools:[{name:"owned_tool",goal_id:9}],learning_records:[{kind:"tool",goal_id:9,date:1}],learning_records_truncated:true}},
    {name:"Queued",state:"available",count:3,blocked:1}]});
  await ui.click("pd-inventory"); const rendered=content(ui.el("pd-stores"));
  assert.match(rendered,/2 saved private drafts/);assert.match(rendered,/owned_tool; source task 9/);
  assert.match(rendered,/Retained owned tool learning record/);assert.match(rendered,/Showing 500 current authorized/);
  assert.match(rendered,/1 records require reconciliation/);assert.doesNotMatch(rendered,/undefined/);
});
test("new stores have explicit independently reviewed deletion choices",()=>{
  const ui=fixture({}); const rendered=content(ui.el("pd-delete-stores"));
  assert.match(rendered,/model-test history/);assert.match(rendered,/current authorized generated tools/);assert.match(rendered,/queued conversation messages/);
});
