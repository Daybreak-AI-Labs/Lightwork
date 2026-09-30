const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const script = fs.readFileSync(path.join(__dirname, '../../maverick_dashboard/static/lightwork-i18n.js'), 'utf8');

function page(language='fr', locale='fr-CA') {
  const catalog = {
    'Save': {text:'Enregistrer',lang:'fr'},
    'Hi {name}': {text:'Bonjour {name}',lang:'fr'},
    '{count} review': {text:'{count} révision',lang:'fr'},
    '{count} reviews': {text:'{count} révisions',lang:'fr'},
    'Complete this field.': {text:'Renseignez ce champ.',lang:'fr'},
  };
  const dom = new JSDOM('<!doctype html><html lang="en"><head></head><body><div id="message"></div><div data-user-content dir="auto" lang="und"><span id="user" data-i18n="Save">Save</span></div><dialog id="dialog"><table><tr><th data-i18n="Save">Save</th><td data-user-content lang="und" dir="auto">مرحبا Save</td></tr></table></dialog><div id="stream" data-user-content dir="auto" lang="und"></div></body></html>', {runScripts:'outside-only'});
  const config=dom.window.document.createElement('script');config.id='lightwork-i18n-data';config.type='application/json';config.textContent=JSON.stringify({language,locale,catalog});dom.window.document.head.appendChild(config);
  dom.window.eval(script);
  return dom;
}

test('only explicitly marked product regions translate, including new dialogs',()=>{
  const dom=page(), doc=dom.window.document, api=dom.window.LightworkI18n;
  api.translate();
  assert.equal(doc.querySelector('th').textContent,'Enregistrer');
  assert.equal(doc.querySelector('th').lang,'fr');
  assert.equal(doc.getElementById('user').textContent,'Save');
  assert.equal(doc.querySelector('td').textContent,'مرحبا Save');
  doc.getElementById('stream').append('Save');api.translate();
  assert.equal(doc.getElementById('stream').textContent,'Save');
  api.set(doc.getElementById('message'),'Missing translation');
  assert.equal(doc.getElementById('message').lang,'en');
  dom.window.close();
});

test('parameters remain escaped, verbatim and bidi-isolated',()=>{
  const dom=page(), doc=dom.window.document;
  const original='<img src=x onerror=alert(1)> مرحبا';
  dom.window.LightworkI18n.set(doc.getElementById('message'),'Hi {name}',{name:original});
  assert.equal(doc.getElementById('message').textContent,'Bonjour '+original);
  assert.equal(doc.querySelector('img'),null);
  assert.equal(doc.querySelector('bdi').textContent,original);
  assert.equal(doc.querySelector('bdi').dir,'auto');
  assert.equal(doc.querySelector('bdi').lang,'und');
  dom.window.close();
});

test('regional number, currency, dates and plural rules use selected locale',()=>{
  const dom=page(),api=dom.window.LightworkI18n;
  assert.equal(api.number(12345.5),new Intl.NumberFormat('fr-CA').format(12345.5));
  assert.equal(api.number(3,{style:'currency',currency:'EUR'}),new Intl.NumberFormat('fr-CA',{style:'currency',currency:'EUR'}).format(3));
  assert.equal(api.plural('{count} review','{count} reviews',1),'1 révision');
  assert.equal(api.plural('{count} review','{count} reviews',2),'2 révisions');
  assert.equal(api.formatDate('2026-09-30'),new Intl.DateTimeFormat('fr-CA',{dateStyle:'medium'}).format(new Date(2026,8,30)));
  assert.equal(api.formatDate('1760000000'),api.formatDate(1760000000));
  dom.window.close();
});

test('Arabic retains English fallback regions while source remains independently directed',()=>{
  const dom=page('ar','ar-EG'),doc=dom.window.document;
  dom.window.LightworkI18n.set(doc.getElementById('message'),'Review before starting');
  assert.equal(doc.getElementById('message').lang,'en');
  assert.equal(doc.getElementById('message').dir,'ltr');
  assert.equal(doc.getElementById('stream').dir,'auto');
  dom.window.close();
});

test('required-field errors localize without modifying drafts and clear on input',()=>{
  const dom=page(),doc=dom.window.document,form=doc.createElement('form');
  form.innerHTML='<textarea required lang="und" dir="auto" data-user-content></textarea>';
  doc.body.appendChild(form);dom.window.LightworkI18n.validation(form);
  const field=form.querySelector('textarea');
  assert.equal(form.checkValidity(),false);
  assert.equal(field.validationMessage,'Renseignez ce champ.');
  assert.equal(form.querySelector('[role=alert]').lang,'fr');
  field.value='Save مرحبا';field.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  assert.equal(form.checkValidity(),true);
  assert.equal(field.value,'Save مرحبا');
  assert.equal(form.querySelector('[role=alert]').hidden,true);
  dom.window.close();
});
