'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const script = fs.readFileSync(path.resolve(__dirname, '../../maverick_dashboard/static/lightwork-deliverable-sharing.js'),'utf8');
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));};
function response(body,status=200){return {ok:status===200,status,redirected:false,json:async()=>body};}
function page(handler){
  const dom=new JSDOM('<div class="goal-share" data-goal="4"><button id="share-create">Review share link</button><input id="share-url"><p id="share-msg"></p><ul id="share-list"></ul></div>',{url:'https://lightwork.invalid/chat/goal/4',runScripts:'outside-only',pretendToBeVisual:true});
  dom.window.fetch=handler;dom.window.mvErrorText=()=> 'Unable to manage link';dom.window.mvRenderError=(node,error)=>{node.textContent=error.message;};dom.window.mvConfirm=async()=>true;dom.window.eval(script);
  return {dom,doc:dom.window.document,stop(){dom.window.dispatchEvent(new dom.window.Event('pagehide'));dom.window.close();}};
}
const preview={audience:'Anyone with the link',scope:'Only selected saved result',source:{title:'Private title',result:'Private result',sha256:'digest',artifacts:[]},allowed_lifetimes:[3600,86400,604800]};
test('404 clears private link metadata and disables further disclosure',async()=>{
  let status=200;const p=page(async()=>response(status===200?{links:[{id:1,label:'Private review',state:'active',expires_at:1}]}:{},status));await settle();assert.match(p.doc.body.textContent,/Private review/);
  p.doc.getElementById('share-url').value='https://example.test/private-token';status=404;p.dom.window.dispatchEvent(new p.dom.window.Event('focus'));await settle();
  assert.doesNotMatch(p.doc.body.textContent,/Private review/);assert.equal(p.doc.getElementById('share-url').value,'');assert.equal(p.doc.getElementById('share-create').disabled,true);p.stop();
});
test('access loss during preview blocks a late private result from repopulating review',async()=>{
  let finish,signal;const p=page(async(url,options)=>{if(!url.endsWith('/preview'))return response({links:[]});signal=options.signal;return new Promise(resolve=>{finish=resolve;});});await settle();
  p.doc.getElementById('share-create').click();await settle();p.doc.dispatchEvent(new p.dom.window.CustomEvent('lightwork:task-access-lost'));assert.equal(signal.aborted,true);
  finish(response(preview));await settle();assert.doesNotMatch(p.doc.body.textContent,/Private result|Private title/);assert.equal(p.doc.getElementById('share-confirm'),null);assert.equal(p.doc.getElementById('share-create').disabled,true);p.stop();
});
test('review alone never creates a link and permission loss removes an already shown preview',async()=>{
  const calls=[];const p=page(async(url,options)=>{calls.push([url,options.method]);return response(url.endsWith('/preview')?preview:{links:[]});});await settle();p.doc.getElementById('share-create').click();await settle();
  assert.match(p.doc.body.textContent,/Private result/);assert.equal(calls.filter(([url,method])=>method==='POST'&&!url.endsWith('/preview')).length,0);
  p.doc.dispatchEvent(new p.dom.window.CustomEvent('lightwork:task-access-lost'));assert.doesNotMatch(p.doc.body.textContent,/Private result/);assert.equal(p.doc.getElementById('share-confirm'),null);p.stop();
});
