'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');
const folder=path.resolve(__dirname,'../../maverick_dashboard');
const markup=fs.readFileSync(path.join(folder,'templates/vendor_onboarding.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const initial={context:'a'.repeat(64),configuration:'b'.repeat(64),revision:0,binding:{resource_owner:''},owner_locked:false,owner_source:'initial',eligible_owners:['user:other','user:owner'],principal:'user:owner',modules_enabled:true,connections:[{name:'jira',connector:'jira',revision:'j1',has_token:true},{name:'drive',connector:'gdrive',revision:'d1',has_token:true}]};
async function fixture(intercept=()=>initial){
  const dom=new JSDOM(markup,{url:'https://app.example/vendor-onboarding',runScripts:'outside-only'}),calls=[],w=dom.window;
  w.fetch=async(url,options)=>{calls.push({url,options});const value=await intercept(url,options);return value instanceof Response?value:new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});};
  w.eval(fs.readFileSync(path.join(folder,'static/lightwork-vendor-setup.js'),'utf8'));
  async function settle(){for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));}
  const el=id=>w.document.getElementById('vs-'+id);
  await settle();return{w,calls,el,settle};
}
test('selection changes clear confirmation and submission binds exact displayed generations',async t=>{
  const ui=await fixture();t.after(()=>ui.w.close());assert.equal(ui.calls.length,1);assert.equal(ui.el('save').disabled,true);
  ui.el('resource-owner').value='user:other';ui.el('resource-owner').dispatchEvent(new ui.w.Event('change',{bubbles:true}));
  ui.el('ticket').value='jira';ui.el('confirm').checked=true;ui.el('confirm').dispatchEvent(new ui.w.Event('change',{bubbles:true}));
  ui.el('document').value='drive';ui.el('document').dispatchEvent(new ui.w.Event('change',{bubbles:true}));assert.equal(ui.el('confirm').checked,false);
  ui.el('confirm').checked=true;ui.el('confirm').dispatchEvent(new ui.w.Event('change',{bubbles:true}));ui.el('save').click();await ui.settle();
  assert.deepEqual(JSON.parse(ui.calls[1].options.body),{confirm:true,context:initial.context,configuration:initial.configuration,ticket:'jira',ticket_revision:'j1',document:'drive',document_revision:'d1',expected_revision:0,resource_owner:'user:other'});assert.equal(ui.el('confirm').checked,false);
});
test('failed refresh clears credentials and changed account cannot retain prior setup',async t=>{
  let mode=0;const ui=await fixture(()=>mode===1?new Response('<h1>Sign in</h1>',{status:401}):mode===2?{...initial,context:'c'.repeat(64)}:initial);t.after(()=>ui.w.close());
  mode=2;ui.el('refresh').click();await ui.settle();assert.equal(ui.el('ticket').options.length,0);assert.equal(ui.el('refresh').disabled,true);assert.match(ui.el('status').textContent,/account or workspace changed/);
  const other=await fixture(()=>mode===1?new Response('<h1>Sign in</h1>',{status:401}):initial);t.after(()=>other.w.close());mode=1;other.el('refresh').click();await other.settle();assert.equal(other.el('ticket').options.length,0);assert.equal(other.el('save').disabled,true);
  assert.equal(other.el('resource-owner').options.length,0);assert.equal(other.el('owner').textContent,'');
});

test('fresh owner choice is explicit and changes require a fresh confirmation',async t=>{
  const ui=await fixture();t.after(()=>ui.w.close());assert.equal(ui.el('resource-owner').value,'');
  assert.equal(ui.el('resource-owner').options[1].textContent,'other');assert.equal(ui.el('resource-owner').options[1].value,'user:other');
  ui.el('ticket').value='jira';ui.el('confirm').checked=true;ui.el('confirm').dispatchEvent(new ui.w.Event('change',{bubbles:true}));
  assert.equal(ui.el('save').disabled,true);ui.el('save').click();assert.equal(ui.calls.length,1);
  ui.el('resource-owner').value='user:other';ui.el('resource-owner').dispatchEvent(new ui.w.Event('change',{bubbles:true}));
  assert.equal(ui.el('confirm').checked,false);assert.equal(ui.el('save').disabled,true);
  ui.el('confirm').checked=true;ui.el('confirm').dispatchEvent(new ui.w.Event('change',{bubbles:true}));
  assert.equal(ui.el('save').disabled,false);
});

test('existing owner is fixed while reviewed connection updates keep its exact identity',async t=>{
  const fixed={...initial,revision:2,owner_locked:true,owner_source:'saved',eligible_owners:[],binding:{resource_owner:'user:historical',ticket_connection:'jira'}};
  const ui=await fixture(()=>fixed);t.after(()=>ui.w.close());
  assert.equal(ui.el('resource-owner').disabled,true);assert.equal(ui.el('resource-owner').value,'user:historical');
  assert.equal(ui.el('resource-owner').selectedOptions[0].textContent,'historical');assert.doesNotMatch(ui.el('owner').textContent,/user:/);
  assert.match(ui.el('owner').textContent,/cannot transfer ownership/);
  ui.el('confirm').checked=true;ui.el('confirm').dispatchEvent(new ui.w.Event('change',{bubbles:true}));ui.el('save').click();await ui.settle();
  assert.equal(JSON.parse(ui.calls[1].options.body).resource_owner,'user:historical');
  assert.equal(JSON.parse(ui.calls[1].options.body).expected_revision,2);
  const legacy=await fixture(()=>({...fixed,binding:{...fixed.binding,resource_owner:'Procurement team'}}));t.after(()=>legacy.w.close());
  assert.equal(legacy.el('resource-owner').selectedOptions[0].textContent,'Procurement team');assert.match(legacy.el('owner').textContent,/Procurement team/);
});
