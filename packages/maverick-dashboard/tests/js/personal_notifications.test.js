'use strict';
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), {JSDOM} = require('jsdom');
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };

test('notification access denial clears private destination, receipt and task labels', async t => {
  const names = ['mode','timezone','digest_time','quiet_start','quiet_end','quiet_enabled','critical_exceptions','watch_all_projects'];
  const nodes = ['personal-notification-status','personal-notification-error','personal-notification-items','personal-notification-save-status',
    'personal-notification-refresh','notification-project-watches','notification-mutes','personal-background-status','personal-external-status',
    'personal-delivery-receipts','personal-external-links','personal-critical-policy','notification-add-mute','notification-reload-preferences'];
  const dom = new JSDOM('<form id="notification-preferences">' + names.map(name => '<input name="'+name+'">').join('')
    + '<button type="submit">Save</button><select id="notification-mute-task"></select><select id="notification-mute-duration"><option value="3600">Hour</option></select></form>'
    + nodes.map(id => '<div id="'+id+'"></div>').join(''), {url:'https://dashboard.example.test/notifications', runScripts:'outside-only', pretendToBeVisual:true});
  t.after(() => dom.window.close());
  const state = {context:'same-account', revision:'a'.repeat(64), checked_at:1, delivery_note:'Ready', items:[], tasks:[{id:1,title:'PRIVATE TASK'}],
    projects:[], preferences:{mode:'immediate',timezone:'UTC',digest_time:'09:00',quiet_start:'22:00',quiet_end:'08:00',events:[],muted_tasks:{},external_links:[]},
    private_delivery:{note:'Ready',channels:[{id:'b'.repeat(32),channel:'slack',sender_id:'PRIVATE SENDER',available:true,receipts:[{at:1,message:'PRIVATE RECEIPT'}]}]}};
  let calls=0;
  dom.window.fetch = async () => ++calls === 1 ? {ok:true,json:async()=>state} : {ok:false,status:403,json:async()=>({detail:'Access changed'})};
  dom.window.setInterval = () => 1; dom.window.clearInterval = () => {};
  dom.window.eval(fs.readFileSync(path.resolve(__dirname,'../../maverick_dashboard/static/lightwork-personal-notifications.js'),'utf8'));
  await settle();
  assert.match(dom.window.document.body.textContent, /PRIVATE SENDER/);
  dom.window.document.getElementById('personal-notification-refresh').click(); await settle();
  assert.doesNotMatch(dom.window.document.body.textContent, /PRIVATE TASK|PRIVATE SENDER|PRIVATE RECEIPT/);
  assert.equal(dom.window.document.getElementById('notification-preferences').hidden, true);
});

test('saving notification preferences says "Saving…", then the truth, and a failure keeps the draft', async t => {
  const names = ['mode','timezone','digest_time','quiet_start','quiet_end','quiet_enabled','critical_exceptions','watch_all_projects'];
  const nodes = ['personal-notification-status','personal-notification-error','personal-notification-items','personal-notification-save-status',
    'personal-notification-refresh','notification-project-watches','notification-mutes','personal-background-status','personal-external-status',
    'personal-delivery-receipts','personal-external-links','personal-critical-policy','notification-add-mute','notification-reload-preferences'];
  const dom = new JSDOM('<form id="notification-preferences">' + names.map(name => '<input name="'+name+'">').join('')
    + '<button type="submit">Save</button><select id="notification-mute-task"></select><select id="notification-mute-duration"><option value="3600">Hour</option></select></form>'
    + nodes.map(id => '<div id="'+id+'"></div>').join(''), {url:'https://dashboard.example.test/notifications', runScripts:'outside-only', pretendToBeVisual:true});
  t.after(() => dom.window.close());
  const state = {context:'same-account', revision:'a'.repeat(64), checked_at:1, delivery_note:'Ready', items:[], tasks:[], projects:[],
    preferences:{mode:'immediate',timezone:'UTC',digest_time:'09:00',quiet_start:'22:00',quiet_end:'08:00',events:[],muted_tasks:{},external_links:[]},
    private_delivery:{note:'Ready',channels:[]}};
  const pending = [];
  dom.window.fetch = (url, options) => new Promise(resolve => { pending.push({options, resolve}); });
  dom.window.setInterval = () => 1; dom.window.clearInterval = () => {};
  dom.window.eval(fs.readFileSync(path.resolve(__dirname,'../../maverick_dashboard/static/lightwork-personal-notifications.js'),'utf8'));
  const doc = dom.window.document, form = doc.getElementById('notification-preferences');
  const saveStatus = doc.getElementById('personal-notification-save-status');
  const submit = () => form.dispatchEvent(new dom.window.Event('submit', {cancelable:true}));
  pending.shift().resolve({ok:true, status:200, json:async()=>state}); await settle();
  // A successful save.
  submit(); await settle();
  assert.equal(saveStatus.textContent, 'Saving…');
  pending.shift().resolve({ok:true, status:200, json:async()=>state}); await settle();
  assert.match(saveStatus.textContent, /were saved/);
  // The next save fails: the earlier "saved" must not stay next to the button.
  form.elements.timezone.value = 'Europe/Paris';
  submit(); await settle();
  assert.equal(saveStatus.textContent, 'Saving…');
  pending.shift().resolve({ok:false, status:500, json:async()=>({detail:'Internal Server Error'})}); await settle();
  assert.doesNotMatch(saveStatus.textContent, /were saved/);
  assert.match(saveStatus.textContent, /not saved/i);
  assert.equal(form.elements.timezone.value, 'Europe/Paris');
  assert.equal(doc.getElementById('personal-notification-error').hidden, false);
  assert.equal(pending.length, 0);
});
