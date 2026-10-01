// Local lifecycle regressions; no provider calls.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const script = fs.readFileSync(path.resolve(__dirname, '../../maverick_dashboard/static/lightwork-answer-panel.js'), 'utf8');
const settle = async () => { for (let i=0;i<12;i++) await new Promise(resolve=>setImmediate(resolve)); };
function fragment(version, content='', status='done', operate='1') { return `<section id="answer-panel" data-goal="4" data-context="scope" data-updated-at="1" data-version="${version}" data-status="${status}" data-operate="${operate}">${content}</section>`; }
function page(content='', status='done') {
  const dom = new JSDOM(`<div id="cs-scroll">${fragment('one',content,status)}</div><p id="answer-refresh-status"></p><button id="answer-refresh"></button><textarea id="answer-input">Unsent follow-up</textarea>`,
    {url:'https://lightwork.invalid/chat/goal/4',runScripts:'outside-only',pretendToBeVisual:true});
  let answer = {ok:true,status:200,headers:{get:()=> 'scope'},text:async()=>fragment('one',content,status)};
  dom.window.fetch = async url => String(url).endsWith('/feedback') ? {ok:true,json:async()=>({})} : answer;
  dom.window.mvErrorText = ()=>'Refresh unavailable.';
  dom.window.mvRenderError = node=>{node.textContent='Refresh unavailable.';};
  dom.window.mvConfirm = async()=>true;
  dom.window.eval(script);
  return {dom, doc:dom.window.document, setAnswer(value){answer=value;}, update(html){answer={ok:true,status:200,headers:{get:()=> 'scope'},text:async()=>html};return dom.window.mvRefreshAnswerPanel();}};
}
test('completion mounts result controls and artifacts once without replacing focused composer', async()=>{
  const p=page('', 'running'); await settle(); const composer=p.doc.getElementById('answer-input');composer.focus();
  p.doc.getElementById('cs-scroll').scrollTop=70;
  const html=fragment('two','<div id="result-section"><div id="result">Finished</div></div><div id="feedback"><button id="copy-result">Copy</button><span id="feedback-msg"></span></div><div class="artifacts">New file</div><button id="signoff-approve">Approve current result</button>');
  p.setAnswer({ok:true,status:200,headers:{get:()=> 'scope'},text:async()=>html});
  p.doc.dispatchEvent(new p.dom.window.CustomEvent('lightwork:task-projection', {detail:{status:'done',terminal:true,result:'Finished',artifactsChanged:true}}));
  await settle(); await p.update(html);await settle();
  assert.equal(p.doc.querySelectorAll('#copy-result').length,1);
  assert.equal(p.doc.querySelector('.artifacts').textContent,'New file');
  assert.equal(p.doc.querySelectorAll('#signoff-approve').length,1);
  assert.equal(p.doc.activeElement,composer);assert.equal(composer.value,'Unsent follow-up');
  assert.equal(p.doc.getElementById('cs-scroll').scrollTop,70);p.dom.window.close();
});
test('accepted handoff updates the live closure notice and owner controls while preserving the draft',async()=>{
  const p=page('<button id="signoff-approve">Approve</button>');await settle();
  const composer=p.doc.getElementById('answer-input');composer.focus();
  await p.update(fragment('closed','<p id="handoff-closed-notice" role="status">Responsibility accepted. Original conversation closed.</p><div id="result">Saved result</div>','done','0'));await settle();
  assert.match(p.doc.querySelector('#answer-panel #handoff-closed-notice').textContent,/Original conversation closed/);
  assert.equal(p.doc.getElementById('signoff-approve'),null);
  assert.equal(p.doc.activeElement,composer);assert.equal(composer.value,'Unsent follow-up');p.dom.window.close();
});
test('private projection clears when account scope changes and preserves composer',async()=>{
  const p=page('Private saved result');await settle();
  p.setAnswer({ok:true,status:200,headers:{get:()=> 'other-scope'}});await p.dom.window.mvRefreshAnswerPanel();await settle();
  assert.equal(p.doc.getElementById('answer-panel').textContent,'');
  assert.equal(p.doc.getElementById('answer-input').value,'Unsent follow-up');p.dom.window.close();
});
test('dirty review note defers replacement until an explicit refresh decision',async()=>{
  const p=page('<textarea id="signoff-note">Unsaved review note</textarea>');await settle();
  await p.update(fragment('two','New result'));await settle();
  assert.equal(p.doc.getElementById('signoff-note').value,'Unsaved review note');
  assert.equal(p.doc.getElementById('answer-refresh').hidden,false);
  p.doc.getElementById('answer-refresh').click();await settle();
  assert.match(p.doc.getElementById('answer-panel').textContent,/New result/);
  assert.equal(p.doc.getElementById('answer-input').value,'Unsent follow-up');p.dom.window.close();
});
