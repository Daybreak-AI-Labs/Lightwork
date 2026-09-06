'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {webcrypto}=require('node:crypto');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.resolve(__dirname,'../../maverick_dashboard/static/lightwork-draft-recovery.js'),'utf8');
const guided=fs.readFileSync(path.resolve(__dirname,'../../maverick_dashboard/static/lightwork-guided-actions.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture(options={}) {
  const dom=new JSDOM('<meta name="lightwork-draft-context" content="account-workspace"><form id="editor"><textarea id="brief"></textarea></form>',{url:'https://lightwork.test/editor',runScripts:'outside-only'});
  const w=dom.window,d=w.document,requests=[],writes=[];
  Object.defineProperty(w,'crypto',{value:webcrypto});w.TextEncoder=TextEncoder;
  let data={context:'account-workspace',revision:options.revision||0,payload:options.remote||null};
  let fail=options.fail||false,lastOperation=null;
  w.confirm=()=>true;
  w.fetch=async(url,config)=>{
    const request={url,...config};if(config.body)request.payload=JSON.parse(config.body);requests.push(request);
    if(options.fetchHook) await options.fetchHook(request);
    if(config.method==='PUT') {
      writes.push(request.payload);
      if(fail==='before')throw new Error('offline');
      if(JSON.stringify(request.payload)!==JSON.stringify(lastOperation)) {
        if(request.payload.expected_revision!==data.revision)return {ok:false,status:409,json:async()=>({detail:'A newer recovery copy exists.'})};
        data={...data,revision:data.revision+1,payload:request.payload.payload};lastOperation=request.payload;
      }
      if(fail==='after'){fail=false;throw new Error('response lost');}
    }
    return {ok:true,json:async()=>({...data,slot:url.split('/').pop()})};
  };
  const input=d.getElementById('brief');input.value=options.initial||'';
  let restores=0;
  w.eval(source);
  const recovery=w.mvDraftRecovery.create({key:'editor',host:d.getElementById('editor'),read:()=>({brief:input.value}),restore:payload=>{restores++;input.value=payload.brief;}});
  if(options.beforeReady) options.beforeReady({w,input,recovery});
  await recovery.ready;
  function button(label){return [...recovery.panel.querySelectorAll('button')].find(button=>button.textContent===label);}
  async function click(label){button(label).click();await tick();await tick();}
  function edit(value){input.value=value;input.dispatchEvent(new w.Event('input',{bubbles:true}));}
  return {dom,w,d,input,recovery,requests,writes,button,click,edit,get restores(){return restores;},get data(){return data;},set data(value){data={...data,...value};}};
}

test('restart offers a recovery copy but never changes the editor or submits on its own',async()=>{
  const ui=await fixture({remote:{brief:'unfinished'},revision:1,initial:'current'});
  assert.equal(ui.input.value,'current');assert.equal(ui.restores,0);assert.equal(ui.writes.length,0);
  await ui.click('Restore recovery copy');assert.equal(ui.input.value,'unfinished');assert.equal(ui.restores,1);assert.equal(ui.writes.length,0);ui.dom.window.close();
});

test('edits while a restore is awaiting confirmation are preserved',async()=>{
  const ui=await fixture({remote:{brief:'old'},revision:1});
  ui.w.mvConfirm=async()=>{ui.edit('new typing');return true;};
  await ui.click('Restore recovery copy');assert.equal(ui.input.value,'new typing');assert.equal(ui.restores,0);ui.dom.window.close();
});

test('a newer remote copy cannot be overwritten by Keep current or discarded without another review',async()=>{
  for(const action of ['Keep current draft','Discard recovery copy','Restore recovery copy']){
    const ui=await fixture({remote:{brief:'first'},revision:1,initial:'local'});
    ui.w.mvConfirm=async()=>{ui.data={revision:2,payload:{brief:'another tab'}};return true;};
    await ui.click(action);assert.equal(ui.writes.length,0);assert.equal(ui.restores,0);assert.equal(ui.data.payload.brief,'another tab');ui.dom.window.close();
  }
});

test('failed autosave keeps work and reuses the identical request on retry',async()=>{
  const ui=await fixture({fail:'before'});ui.edit('unfinished');await ui.recovery.save();
  assert.equal(ui.input.value,'unfinished');assert.equal(ui.button('Retry recovery').hidden,false);
  await ui.click('Retry recovery');assert.deepEqual(ui.writes[1],ui.writes[0]);assert.equal(ui.writes[0].expected_revision,0);ui.dom.window.close();
});

test('nested serialized credentials never reach recovery transport or its comparison preview',async()=>{
  const ui=await fixture({remote:{brief:'clean'},revision:1});
  ui.edit(JSON.stringify({nodes:[{params:{accessToken:'private-credential'}}]}));
  await ui.click('Keep current draft');assert.equal(ui.writes.length,0);assert.doesNotMatch(ui.recovery.panel.textContent,/private-credential/);ui.dom.window.close();
});

test('account or workspace mismatch rejects restore before editor mutation',async()=>{
  const ui=await fixture({remote:{brief:'private'},revision:1});ui.data={context:'different-account'};
  await ui.click('Restore recovery copy');assert.equal(ui.restores,0);assert.equal(ui.input.value,'');ui.dom.window.close();
});

test('edits made during initial recovery lookup are saved after that lookup',async()=>{
  const ui=await fixture({beforeReady:({w,input})=>{input.value='edited while loading';input.dispatchEvent(new w.Event('input',{bubbles:true}));}});
  await ui.recovery.save();assert.equal(ui.writes[0].payload.brief,'edited while loading');ui.dom.window.close();
});

test('a lost save response retries the exact operation before saving later edits',async()=>{
  const ui=await fixture({fail:'after'});ui.edit('first');await ui.recovery.save();assert.equal(ui.data.revision,1);
  ui.edit('later edits');await ui.click('Retry recovery');assert.deepEqual(ui.writes[1],ui.writes[0]);
  await ui.recovery.save();assert.equal(ui.data.revision,2);assert.equal(ui.data.payload.brief,'later edits');ui.dom.window.close();
});

test('guided recovery keeps raw request references and separate resource drafts without submitting',async()=>{
  const dom=new JSDOM('<form id="resource"><select id="kind" name="kind"><option value="first" data-guided-endpoint="/first">First</option><option value="second" data-guided-endpoint="/second">Second</option></select><label>Details<textarea name="payload" data-guided-selector="kind">{"expected_revision":7,"idempotency_key":"exact-request"}</textarea></label><input type="password" name="password" value="not-stored"><button>Save</button></form>',{url:'https://lightwork.test/resource',runScripts:'outside-only'});
  const w=dom.window,d=w.document,adapters=[];let submits=0;
  d.querySelector('form').addEventListener('submit',()=>{submits++;});
  w.mvDraftRecovery={create:options=>{adapters.push(options);return {};}};
  w.fetch=async()=>({ok:true,json:async()=>({paths:{'/first':{post:{requestBody:{content:{'application/json':{schema:{type:'object'}}}}}},'/second':{post:{requestBody:{content:{'application/json':{schema:{type:'object'}}}}}}},components:{schemas:{}}})});
  w.eval(guided);await tick();
  const adapter=adapters[0],raw=d.querySelector('textarea[name=payload]'),select=d.getElementById('kind');
  select.value='second';select.dispatchEvent(new w.Event('change',{bubbles:true}));raw.value='{"expected_revision":2,"idempotency_key":"second-request"}';raw.dispatchEvent(new w.Event('change',{bubbles:true}));
  const saved=JSON.parse(JSON.stringify(adapter.read()));assert.equal(saved.fields.password,undefined);
  raw.value='{}';adapter.restore(saved);assert.equal(raw.value,'{"expected_revision":2,"idempotency_key":"second-request"}');
  select.value='first';select.dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal(JSON.parse(raw.value).idempotency_key,'exact-request');assert.equal(submits,0);dom.window.close();
});
