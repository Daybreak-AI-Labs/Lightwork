from pathlib import Path
p=Path('packages/maverick-dashboard/tests/js/voice_setup.test.js');s=p.read_text(encoding='utf8')
s=s.replace('async function fixture(intercept=()=>undefined){','async function fixture(intercept=()=>undefined,setup=()=>{}){')
s=s.replace('  if(fs.existsSync(script))window.eval', '  setup(window);if(fs.existsSync(script))window.eval')
s+='''
const installLocale=require('./helpers/recovery_review_i18n');
test('voice setup localizes progress and model copy but keeps backend errors and provenance opaque',async t=>{
  const fr=text=>({text,lang:'fr'}),catalog={
    'Standard multilingual model':fr('Modèle multilingue standard'),
    'Downloading model.':fr('Téléchargement du modèle.'),
    'Setup failed. {reason}':fr('Échec de la configuration. {reason}'),
    '{received} MB of {total} MB ({percent}). Lightwork checks the file before using it.':fr('{received} Mo sur {total} Mo ({percent}). Lightwork vérifie le fichier.'),
    '{model} · {bytes} bytes · {source} · SHA-256: {hash} · Configured speech backend: {backend}':fr('{model} · {bytes} octets · {source} · SHA-256 : {hash} · Moteur : {backend}'),
  };
  let current={...base,job:job('downloading',1500000)};
  const ui=await fixture(()=>current,w=>installLocale(w,{catalog}));t.after(()=>ui.window.close());
  assert.equal(ui.el('model').textContent,'Modèle multilingue standard');assert.equal(ui.el('model').lang,'fr');assert.equal(ui.el('status').textContent,'Téléchargement du modèle.');assert.match(ui.el('progress-text').textContent,/1,5 Mo/);
  const values=Array.from(ui.el('technical').querySelectorAll('bdi'),node=>node.textContent);assert.ok(values.includes(model.source_url));assert.ok(values.includes(model.sha256));assert.ok(values.includes('local'));assert.match(ui.el('technical').textContent,/147.951.465/);
  current={...base,job:{...job('failed'),error:'Standard multilingual model <img src=x> مرحبا'}};await ui.poll();
  assert.match(ui.el('status').textContent,/Échec/);assert.equal(ui.el('status').querySelector('bdi').textContent,current.job.error);assert.equal(ui.el('status').querySelector('bdi').lang,'und');assert.equal(ui.el('status').querySelector('img'),null);
  assert.equal(ui.calls.every(call=>call.options.method==='GET'),true);
});
'''
p.write_text(s,encoding='utf8',newline='\n')
