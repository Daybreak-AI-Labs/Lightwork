'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = name => fs.readFileSync(path.join(__dirname, '../../maverick_dashboard/static/', name), 'utf8');
const copy = {
  'Emergency stop': 'Arrêt d’urgence',
  'STOPPED — click to resume': 'ARRÊTÉ — cliquer pour reprendre',
  'Stop status unavailable': 'État d’arrêt indisponible',
  'Checking stop status…': 'Vérification de l’état d’arrêt…',
  'Stop work on this server? Work covered by this emergency stop pauses at its next step and waits for an authorized resume.': 'Arrêter le travail sur ce serveur ? Le travail couvert par cet arrêt d’urgence se met en pause à l’étape suivante et attend une reprise autorisée.',
  'Stop work across connected workers? Work covered by this emergency stop pauses at its next step and waits for an authorized resume.': 'Arrêter le travail sur les exécutants connectés ? Le travail couvert par cet arrêt d’urgence se met en pause à l’étape suivante et attend une reprise autorisée.',
  'Stop work': 'Arrêter le travail',
  'Cancel': 'Annuler',
  'Your workspace changed in another tab. Changes here are paused. Keep any draft you need, then reopen this page in the current workspace.': 'Votre espace de travail a changé dans un autre onglet. Les modifications ici sont suspendues. Conservez les brouillons nécessaires, puis rouvrez cette page dans l’espace de travail actuel.',
  'Reopen in current workspace': 'Rouvrir dans l’espace de travail actuel',
  'Required: {fields}': 'Obligatoire : {fields}',
  '{count} page': '{count} page',
  '{count} pages': '{count} pages',
  '{count} record shown': '{count} enregistrement affiché',
  '{count} records shown': '{count} enregistrements affichés',
  'Setting': 'Paramètre',
  'Copied to clipboard': 'Copié dans le presse-papiers',
  '{count} matching recent title.': '{count} titre récent correspondant.',
  '{count} matching recent titles.': '{count} titres récents correspondants.',
  'No recent titles match. Open task search for older chats or a match in task contents.': 'Aucun titre récent ne correspond. Ouvrez la recherche de tâches pour rechercher d’anciennes discussions ou du contenu de tâche.',
};
const tick = () => new Promise(resolve => setImmediate(resolve));
function page(html='', {language='fr', workspace=false, fetch=async()=>({ok:true,json:async()=>({})})}={}) {
  const dom = new JSDOM(`<!doctype html><html lang="en"><head>${workspace ? '<meta name="lightwork-workspace-tenant" content="owned">' : ''}</head><body>${html}<div id="mv-toasts"></div><div id="mv-toasts-alert"></div><dialog id="mv-confirm"><p id="mv-confirm-msg"></p><button id="mv-confirm-cancel"></button><button id="mv-confirm-ok"></button></dialog></body></html>`, {url:'https://local.test/chat/goal/1',runScripts:'outside-only'});
  const w=dom.window, doc=w.document;
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  w.fetch=fetch;w.Headers=Headers;w.Request=Request;w.Response=Response;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  const config=doc.createElement('script');config.id='lightwork-i18n-data';config.type='application/json';
  config.textContent=JSON.stringify({language,locale:language==='fr'?'fr-CA':'ar-EG',catalog:language==='fr'?Object.fromEntries(Object.entries(copy).map(([s,text])=>[s,{text,lang:'fr'}])):{}});
  doc.head.appendChild(config);w.eval(source('lightwork-i18n.js'));w.eval(source('lightwork-ui.js'));
  return dom;
}

test('localized workspace warning still blocks mutations and retains the untouched draft', async()=>{
  let calls=0;
  const dom=page('<textarea id="draft" data-user-content dir="auto" lang="und"></textarea>', {workspace:true,fetch:async()=>{calls++;return {ok:true};}});
  const w=dom.window, doc=w.document;
  const draft='Emergency stop <img src=x> العربية';doc.getElementById('draft').value=draft;
  w.dispatchEvent(new w.StorageEvent('storage',{key:'lightwork-workspace-changed',newValue:'other'}));
  const alert=doc.querySelector('section[role=alert]');
  assert.equal(alert.lang,'fr');assert.match(alert.textContent,/autre onglet/);
  assert.equal(alert.querySelector('a').textContent,copy['Reopen in current workspace']);
  assert.equal(alert.querySelector('a').href,w.location.href);
  await assert.rejects(w.fetch('/api/v1/save',{method:'POST'}),/Workspace changed/);
  assert.equal(calls,0);assert.equal(doc.getElementById('draft').value,draft);
  dom.window.close();
});

for (const scope of ['local','cluster']) test(`localized ${scope} stop review keeps authority and exact scope`, async()=>{
  const calls=[];
  const dom=page('<a id="halt-pill" data-can-halt="true"><span id="halt-label"></span></a>',{fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({active:false,scope,cluster_halt:false})};}});
  const w=dom.window,doc=w.document;await tick();
  assert.equal(doc.getElementById('halt-label').textContent,copy['Emergency stop']);
  assert.equal(doc.getElementById('halt-label').lang,'fr');
  doc.getElementById('halt-pill').click();await tick();
  const consequence=doc.getElementById('mv-confirm-msg');
  assert.equal(consequence.lang,'fr');assert.match(consequence.textContent,scope==='local'?/ce serveur/:/exécutants connectés/);
  assert.match(consequence.textContent,/étape suivante/);assert.match(consequence.textContent,/reprise autorisée/);
  assert.equal(doc.getElementById('mv-confirm-ok').textContent,'Arrêter le travail');
  doc.getElementById('mv-confirm-cancel').click();await tick();
  assert.equal(calls.length,1);assert.equal(calls[0].options.method,undefined);
  dom.window.close();
});

test('Arabic fallback stop status declares English, without a invented translation',async()=>{
  const dom=page('<span id="halt-pill" data-can-halt="false"><span id="halt-label"></span></span>',{language:'ar',fetch:async()=>({ok:true,json:async()=>({active:false,scope:'local'})})});
  await tick();const label=dom.window.document.getElementById('halt-label');
  assert.equal(label.textContent,'Work is not stopped');assert.equal(label.lang,'en');assert.equal(label.dir,'ltr');
  assert.equal(dom.window.document.getElementById('halt-pill').getAttribute('aria-disabled'),'true');
  dom.window.close();
});

test('finder searches translated labels and English aliases; records remain original content',()=>{
  const dom=page('<button id="navfinder-open"></button><dialog id="navfinder-dialog"><button id="navfinder-close"></button><input id="navfinder-input"><p id="navfinder-count"></p><p id="navfinder-empty"></p><ul id="navfinder-results"><li><a id="allowed" href="/goals" data-navfinder-target data-search="Tâches Tasks jobs Work">Tâches</a></li></ul></dialog>');
  const w=dom.window,doc=w.document;w.eval(source('lightwork-navfinder.js'));
  const controller=w.LightworkNavFinder.init(doc)||doc.getElementById('navfinder-dialog').lightworkFinder;
  controller.open();const input=doc.getElementById('navfinder-input');
  for(const query of ['tâches','tasks','jobs']) {input.value=query;controller.filter();assert.equal(controller.visibleOptions().length,1);}
  const original='Emergency stop <img src=x onerror=alert(1)> العربية';
  controller.setRecords([{href:'/goals/1',title:original,kind:'task',id:'1',revision:'2',state:'private'}]);
  const label=doc.querySelector('#navfinder-record-0 .navfinder__label');
  assert.equal(label.textContent,original);assert.equal(label.lang,'und');assert.equal(label.dir,'auto');assert.equal(label.querySelector('img'),null);
  assert.equal(doc.getElementById('navfinder-count').textContent,'1 page, 1 enregistrement affiché');
  assert.equal(doc.getElementById('navfinder-count').lang,'fr');
  controller.setSettings([{href:'/preferences',title:'Emergency stop',group:original,effective:'owned',required_role:'viewer'}]);
  assert.equal(doc.querySelector('#navfinder-setting-0 .navfinder__group span').textContent,'Paramètre');
  assert.equal(doc.querySelector('#navfinder-setting-0 .navfinder__group bdi').textContent,original+' - owned - viewer');
  assert.equal(doc.querySelector('#navfinder-setting-0 .navfinder__label').textContent,'Emergency stop');
  assert.equal(doc.querySelector('img'),null);
  dom.window.close();
});

test('clipboard and timestamps use interface locale while copied content remains verbatim',async()=>{
  const dom=page(),w=dom.window;let copied;
  w.navigator.clipboard={writeText:async value=>{copied=value;}};
  const original='Emergency stop العربية';w.mvCopy(original);await tick();
  assert.equal(copied,original);const toast=w.document.querySelector('.mv-toast');
  assert.equal(toast.textContent,'Copié dans le presse-papiers');assert.equal(toast.lang,'fr');
  assert.equal(w.mvWhen(1760000000),new Intl.DateTimeFormat('fr-CA',{dateStyle:'medium',timeStyle:'short'}).format(new Date(1760000000000)));
  assert.equal(w.mvWhen(NaN),'');assert.equal(w.mvWhen(null),'');
  dom.window.close();
});

test('recent-chat filtering searches original titles, excluding translated pin labels',()=>{
  const dom=page('<input id="cs-filter"><p id="cs-filter-status"></p><ul id="cs-list"><li><a data-conversation-root="1"><span>Épinglé :</span><bdi data-user-content>Alpha العربية</bdi></a></li><li><a data-conversation-root="2"><bdi data-user-content>Alpha Tasks</bdi></a></li></ul>');
  const template=fs.readFileSync(path.join(__dirname,'../../maverick_dashboard/templates/chat_base.html'),'utf8');
  const start=template.indexOf('    // Filter the recents list as you type.');
  dom.window.eval(template.slice(start,template.indexOf('</script>',start)));
  const w=dom.window,doc=w.document,input=doc.getElementById('cs-filter'),status=doc.getElementById('cs-filter-status');
  input.value='Alpha';input.dispatchEvent(new w.Event('input'));
  assert.equal(status.textContent,'2 titres récents correspondants.');assert.equal(status.lang,'fr');
  input.value='Épinglé';input.dispatchEvent(new w.Event('input'));
  assert.match(status.textContent,/Aucun titre/);assert.equal(doc.querySelectorAll('#cs-list li:not([hidden])').length,0);
  input.value='العربية';input.dispatchEvent(new w.Event('input'));
  assert.equal(status.textContent,'1 titre récent correspondant.');
  assert.equal(doc.querySelector('[data-user-content]').textContent,'Alpha العربية');
  input.value='';input.dispatchEvent(new w.Event('input'));assert.equal(status.textContent,'');
  dom.window.close();
});
