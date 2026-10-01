'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../maverick_dashboard');
const code = name => fs.readFileSync(path.join(root, 'static', name), 'utf8');
// Use the literal shipped catalog, whose Python loader separately validates
// placeholders. Each row uses JSON-compatible string literals.
const rows = fs.readFileSync(path.join(root, 'i18n_journey_task_controls.py'), 'utf8').split('\n')
  .map(line => line.trim()).filter(line => line.startsWith('("'))
  .map(line => JSON.parse('[' + line.slice(1, -2) + ']'));
const settle = async () => { for (let n=0;n<20;n++) await new Promise(resolve => setImmediate(resolve)); };
function page(markup, language) {
  const dom = new JSDOM(markup, {url:'https://lightwork.invalid/chat/goal/4', runScripts:'outside-only', pretendToBeVisual:true});
  const index = ['en','fr','de','ja','zh'].indexOf(language);
  const catalog = Object.fromEntries(rows.map(row => [row[0], {text:row[index],lang:language}]));
  const payload = dom.window.document.createElement('script'); payload.id='lightwork-i18n-data'; payload.type='application/json';
  payload.textContent=JSON.stringify({language,locale:language,catalog}); dom.window.document.head.append(payload);
  dom.window.eval(code('lightwork-i18n.js'));
  dom.window.mvErrorText=()=> 'Could not complete the request.';
  return {dom,w:dom.window,d:dom.window.document,catalog};
}
for (const language of ['fr','de','ja','zh']) {
  test(language + ': planning controls translate while authored output stays verbatim', async t => {
    const p=page('<div id="planning-steps" data-goal="4"><button id="ps-run"></button><select id="ps-passes"><option value="2">2</option></select><div id="ps-result" hidden></div><p id="ps-status"></p></div>',language);
    t.after(()=>p.w.close()); p.w.setTimeout=fn=>{fn();return 0;};
    p.w.fetch=async url=>new Response(JSON.stringify(String(url).endsWith('/planning-steps') ? {job:'a'.repeat(32)} : {state:'done',result:{ran:true,plan:['The plan'],steps:[{step:'Reviews',output:'<img src=x> Saving.'}],reviews:[{verdict:'done',notes:'The case for'}],status:'done',cost:1.25}}));
    p.w.eval(code('lightwork-planning-steps.js')); p.d.getElementById('ps-run').click(); await settle();
    assert.equal(p.d.querySelector('#ps-result h3').textContent,p.catalog['The plan'].text);
    assert.equal(p.d.querySelector('#ps-result h3').lang,language);
    const sources=[...p.d.querySelectorAll('#ps-result [data-user-content]')];
    assert.ok(sources.some(node=>node.textContent==='The plan' && node.lang==='und'));
    assert.ok(sources.some(node=>node.textContent==='<img src=x> Saving.'));
    assert.equal(p.d.querySelector('#ps-result img'),null);
    assert.equal(p.d.getElementById('ps-status').textContent,p.catalog['The planning steps are ready below.'].text);
  });
  test(language + ': viewpoints retain source labels and text under translated controls', async t=>{
    const p=page('<form id="vp-form"><input id="vp-question" value="Decision"><select id="vp-rounds"><option value="2">2</option></select><button id="vp-run"></button></form><p id="vp-status"></p><div id="vp-result" hidden></div>',language);
    t.after(()=>p.w.close()); p.w.setTimeout=fn=>{fn();return 0;};
    p.w.fetch=async url=>new Response(JSON.stringify(url==='/api/v1/viewpoints/jobs' ? {job:'b'.repeat(32)} : {state:'done',result:{ran:true,turns:[{side:'for',text:'The case for'}],verdict:'for',reason:'Verdict',key_point:'<script>kept</script>',cost:0.005}}));
    p.w.eval(code('lightwork-viewpoints.js')); p.d.getElementById('vp-form').dispatchEvent(new p.w.Event('submit',{cancelable:true}));await settle();
    assert.equal(p.d.querySelector('#vp-result h3').textContent,p.catalog.Verdict.text);
    assert.equal(p.d.querySelector('#vp-result li').textContent,'The case for');
    assert.equal(p.d.querySelector('#vp-result li').lang,'und');
    assert.equal(p.d.querySelector('#vp-result script'),null);
    assert.equal(p.d.getElementById('vp-status').lang,language);
  });
  test(language + ': result access loss clears private content and localizes recovery', async t=>{
    const p=page('<section id="answer-panel" data-goal="4" data-context="mine" data-status="done">private</section><p id="answer-refresh-status"></p><button id="answer-refresh"></button>',language);
    t.after(()=>p.w.close());p.w.fetch=async()=>({ok:false,status:403});
    p.w.eval(code('lightwork-answer-panel.js'));await settle();
    assert.equal(p.d.getElementById('answer-panel').textContent,'');
    assert.equal(p.d.getElementById('answer-refresh-status').lang,language);
    assert.equal(p.d.getElementById('answer-refresh-status').textContent,p.catalog['Access to the saved result changed. Sign in or reopen the authorized task to continue.'].text);
  });
}
