// Local lifecycle regressions; no provider calls.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const {shell} = require('./_shell');
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
  shell(dom.window);
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

const FEEDBACK = '<div id="feedback"><button id="feedback-up">up</button><button id="feedback-down">down</button><span id="feedback-msg"></span></div>' +
  '<form id="feedback-reason" hidden><p id="feedback-reason-q">What was wrong? This is optional.</p>' +
  '<button type="button" data-reason="wrong" aria-pressed="false">Wrong or inaccurate</button>' +
  '<button type="button" data-reason="incomplete" aria-pressed="false">Missing something</button>' +
  '<textarea id="feedback-note"></textarea><button type="submit" id="feedback-reason-send">Send</button>' +
  '<button type="button" id="feedback-reason-skip">Skip</button></form>';
function feedbackPage(replies) {
  const p = page(FEEDBACK); const posts = [];
  p.dom.window.fetch = async (url, options) => {
    if (String(url).endsWith('/feedback') && options && options.method === 'POST') {
      const body = JSON.parse(options.body); posts.push(body);
      const reply = replies.shift() || {acknowledgement: 'Thanks. Saved with this task.'};
      return {ok: true, status: 200, json: async () => reply};
    }
    if (String(url).endsWith('/feedback')) return {ok: true, json: async () => ({})};
    return {ok: true, status: 200, headers: {get: () => 'scope'}, text: async () => fragment('one', FEEDBACK)};
  };
  return {p, posts};
}
test('thumbs-down saves at once, then offers optional reasons that send with a private comment', async () => {
  const {p, posts} = feedbackPage([{acknowledgement: 'Thanks. Saved with this task. It was not recorded as a learning signal, so it does not change this agent.'},
    {acknowledgement: "Thanks. Saved with this task. The administrators' summary shows counts and the reasons picked for this agent, not what you wrote."}]);
  await settle();
  p.doc.getElementById('feedback-down').click(); await settle();
  assert.deepEqual(posts[0], {rating: 'down'});
  assert.match(p.doc.getElementById('feedback-msg').textContent, /not recorded as a learning signal/);
  const form = p.doc.getElementById('feedback-reason');
  assert.equal(form.hidden, false);
  form.querySelector('[data-reason="incomplete"]').click();
  assert.equal(form.querySelector('[data-reason="incomplete"]').getAttribute('aria-pressed'), 'true');
  p.doc.getElementById('feedback-note').value = 'It skipped the Fabrikam POs.';
  form.dispatchEvent(new p.dom.window.Event('submit', {cancelable: true})); await settle();
  assert.deepEqual(posts[1], {rating: 'down', reason: 'incomplete', note: 'It skipped the Fabrikam POs.'});
  assert.equal(form.hidden, true);
  assert.match(p.doc.getElementById('feedback-msg').textContent, /not what you wrote/);
  p.dom.window.close();
});
test('the reason step is one tap to skip and sends nothing more', async () => {
  const {p, posts} = feedbackPage([]);
  await settle();
  p.doc.getElementById('feedback-down').click(); await settle();
  p.doc.getElementById('feedback-reason-skip').click(); await settle();
  assert.equal(p.doc.getElementById('feedback-reason').hidden, true);
  assert.equal(posts.length, 1);
  p.dom.window.close();
});
test('after repeated rejections the reason step asks one short open question', async () => {
  const {p} = feedbackPage([{acknowledgement: 'Thanks.', follow_up: {question: 'What would make this better?'}}]);
  await settle();
  p.doc.getElementById('feedback-down').click(); await settle();
  assert.equal(p.doc.getElementById('feedback-reason-q').textContent, 'What would make this better?');
  assert.equal(p.doc.activeElement, p.doc.getElementById('feedback-note'));
  p.dom.window.close();
});
test('thumbs-up never opens the reason step', async () => {
  const {p, posts} = feedbackPage([]);
  await settle();
  p.doc.getElementById('feedback-up').click(); await settle();
  assert.deepEqual(posts[0], {rating: 'up'});
  assert.equal(p.doc.getElementById('feedback-reason').hidden, true);
  p.dom.window.close();
});
