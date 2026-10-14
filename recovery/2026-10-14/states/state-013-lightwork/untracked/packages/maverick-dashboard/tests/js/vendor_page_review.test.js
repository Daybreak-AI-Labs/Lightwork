'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../maverick_dashboard');
const markup=fs.readFileSync(path.join(root,'templates/vendor_onboarding.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const code=fs.readFileSync(path.join(root,'static/lightwork-vendor-onboarding.js'),'utf8');
// Public authored 1x1 PNG used only to exercise byte integrity and UI lifecycle.
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=','base64'); // pragma: allowlist secret
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const response=(body,status=200)=>({status,ok:status>=200&&status<300,async json(){return body;}});
const record=(revision=1)=>({id:'one',revision,vendor_name:'Synthetic PDF',stage:'intake',document_ids:[],uploads:[],assessment:null,evidence:null,operation:null,metrics:{}});
function preview(call,{manual=false,unreadable=false}={}){
  const body=JSON.parse(call.body),text='Model provenance is documented with provider and version.';
  const pages=[text,''].map((text,index)=>({page:index+1,width:1,height:1,png_base64:png.toString('base64'),text}));
  const identities=pages.map(page=>({page:page.page,width:1,height:1,png_sha256:sha(png),text_sha256:sha(page.text),
    method:page.text?'optical_text':'blank_page',word_count:page.text?9:0,confidence_score:page.text?88.25:null,minimum_confidence:page.text?56.5:null}));
  const fulltext=manual?body.transcription:unreadable?'':text;
  return {case_id:'one',expected_revision:body.expected_revision,name:body.name,mime:body.mime,text:fulltext,
    source_sha256:sha(Buffer.from(body.content_base64,'base64')),text_sha256:sha(fulltext),review_digest:'a'.repeat(64),
    expires_at:Date.now()/1000+600,saved:false,review_required:true,status:unreadable?'extraction_failed':'available',can_transcribe:unreadable,page_previews:pages,
    extraction:{method:manual?'human_transcription':'pdf_rendered_page_ocr',scope:manual?'user_supplied_text':'rendered_pages',confidence:'untrusted',review_required:true,
      page_count:2,pages:identities,renderer:{pypdfium2:'synthetic',pdfium:'synthetic'},ocr:{engine:'tesseract',version:'synthetic',language:'eng',model_sha256:'b'.repeat(64)},limits:['Synthetic OCR; check original pages.']}};
}
async function fixture(intercept=call=>call.url.endsWith('/upload-preview')?response(preview(call)):undefined){
  const dom=new JSDOM(markup,{url:'https://lightwork.example/third-party/vendor-onboarding',runScripts:'outside-only'}),w=dom.window;
  Object.defineProperty(w,'crypto',{value:crypto.webcrypto,configurable:true});w.TextEncoder=TextEncoder;
  const created=[],revoked=[],calls=[];let row=record();
  w.URL.createObjectURL=blob=>{const url='blob:synthetic-'+created.length;created.push({url,blob});return url;};
  w.URL.revokeObjectURL=url=>revoked.push(url);
  w.fetch=async(url,options={})=>{
    const call={url,...options};calls.push(call);const result=await intercept(call);if(result!==undefined)return result;
    if(url.endsWith('/upload-reviewed')){row=record(2);return response(row);}
    return response(url.endsWith('/cases')?{cases:[row]}:row);
  };
  const el=id=>w.document.getElementById('vo-'+id);
  async function settle(){for(let i=0;i<200;i++){await new Promise(r=>setTimeout(r,2));if(w.document.getElementById('vendor-onboarding').getAttribute('aria-busy')==='false')return;}throw Error('UI did not settle: '+el('message').textContent);}
  w.eval(code);await settle();el('cases').querySelector('button').click();await settle();
  function choose(name='source.pdf'){Object.defineProperty(el('upload'),'files',{value:[new w.File(['%PDF synthetic original bytes'],name)],configurable:true});el('upload').dispatchEvent(new w.Event('change'));}
  choose();el('document-date').value='2026-09-01';
  return {w,el,calls,created,revoked,choose,settle,async click(id){el(id).click();await settle();},close(){w.dispatchEvent(new w.Event('pagehide'));w.close();}};
}

test('verified original page images and OCR text have accessible navigation and explicit review',async t=>{
  const ui=await fixture();t.after(()=>ui.close());await ui.click('upload-submit');
  assert.equal(ui.el('page-review').hidden,false);assert.equal(ui.created.length,2);assert.equal(ui.el('upload-accept').disabled,true);
  assert.match(ui.el('page-image').src,/^blob:/);assert.match(ui.el('page-image').alt,/Original PDF page 1 of 2/);
  assert.match(ui.el('page-text').textContent,/Model provenance/);assert.match(ui.el('page-confidence').textContent,/88.25.*not verified accuracy/);
  assert.equal(ui.el('page-previous').disabled,true);await ui.click('page-next');
  assert.equal(ui.el('page-position').textContent,'Page 2 of 2');assert.match(ui.el('page-text').textContent,/detected as blank/);assert.match(ui.el('page-confidence').textContent,/unavailable/);assert.equal(ui.el('page-next').disabled,true);
  assert.equal(ui.calls.filter(call=>call.url.endsWith('/upload-reviewed')).length,0);
  ui.el('upload-confirm').checked=true;ui.el('upload-confirm').dispatchEvent(new ui.w.Event('change'));await ui.click('upload-accept');
  assert.equal(ui.calls.filter(call=>call.url.endsWith('/upload-reviewed')).length,1);assert.equal(ui.revoked.length,2);assert.equal(ui.el('page-review').hidden,true);
});

for(const mutation of ['source','png_hash','text_hash','global_text_hash','dimensions','page_order','png_signature','score','missing_page','oversized'])test('refuses altered '+mutation+' before making an image URL or enabling save',async t=>{
  const ui=await fixture(call=>{
    if(!call.url.endsWith('/upload-preview'))return;const value=preview(call);
    if(mutation==='source')value.source_sha256='0'.repeat(64);
    if(mutation==='png_hash')value.extraction.pages[0].png_sha256='0'.repeat(64);
    if(mutation==='text_hash')value.page_previews[0].text='Altered text';
    if(mutation==='global_text_hash')value.text='Altered combined text';
    if(mutation==='dimensions')value.page_previews[0].width=value.extraction.pages[0].width=2;
    if(mutation==='page_order')value.page_previews.reverse();
    if(mutation==='png_signature'){value.page_previews[0].png_base64=Buffer.from('<svg onload="alert(1)"></svg>').toString('base64');value.extraction.pages[0].png_sha256=sha(Buffer.from('<svg onload="alert(1)"></svg>'));}
    if(mutation==='score')value.extraction.pages[0].confidence_score=101;
    if(mutation==='missing_page')value.page_previews.pop();
    if(mutation==='oversized')value.page_previews[0].png_base64=Buffer.alloc(1024*1024+1).toString('base64');
    return response(value);
  });t.after(()=>ui.close());await ui.click('upload-submit');
  assert.equal(ui.created.length,0);assert.equal(ui.el('upload-accept').disabled,true);assert.equal(ui.el('page-review').hidden,true);assert.match(ui.el('message').textContent,/preview|limit/);
});

for(const trigger of ['file','date','revision','pagehide','image_error','auth'])test('clears page bytes and URLs after '+trigger,async t=>{
  let deny=false;const ui=await fixture(call=>{
    if(trigger==='auth'&&deny&&call.url.endsWith('/cases'))return {status:401,ok:false,async json(){throw Error('must not parse HTML');}};
    if(call.url.endsWith('/upload-preview'))return response(preview(call));
    if(trigger==='revision'&&deny&&call.url.endsWith('/cases/one'))return response(record(2));
  });t.after(()=>ui.close());await ui.click('upload-submit');assert.equal(ui.created.length,2);
  if(trigger==='file')ui.choose('other.pdf');
  if(trigger==='date'){ui.el('document-date').value='2026-09-02';ui.el('document-date').dispatchEvent(new ui.w.Event('input'));}
  if(trigger==='pagehide')ui.w.dispatchEvent(new ui.w.Event('pagehide'));
  if(trigger==='image_error')ui.el('page-image').dispatchEvent(new ui.w.Event('error'));
  if(['revision','auth'].includes(trigger)){deny=true;await ui.click('refresh');}
  assert.equal(ui.revoked.length,2);assert.equal(ui.el('page-review').hidden,true);assert.equal(ui.el('page-image').hasAttribute('src'),false);assert.equal(ui.el('page-text').textContent,'');assert.equal(ui.el('upload-accept').disabled,true);
});

test('manual transcription preserves original images and separates unverified paragraph text from OCR',async t=>{
  const ui=await fixture(call=>call.url.endsWith('/upload-preview')?response(preview(call,{manual:true})):undefined);t.after(()=>ui.close());
  ui.el('upload-transcription').value='My explicit transcription, including noted omissions.';await ui.click('upload-transcribe');
  assert.equal(ui.el('page-review').hidden,false);assert.equal(ui.el('page-manual').hidden,false);
  assert.match(ui.el('upload-text').textContent,/My explicit transcription/);assert.match(ui.el('page-text').textContent,/Model provenance/);
  assert.doesNotMatch(ui.el('upload-summary').textContent,/no OCR performed/);assert.match(ui.el('page-manual').textContent,/not verified PDF page/);
});

test('a file changed during asynchronous hash verification cannot receive late images',async t=>{
  const ui=await fixture();t.after(()=>ui.close());let release,entered;const pending=new Promise(r=>{release=r;}),started=new Promise(r=>{entered=r;});
  const subtle={async digest(...args){entered();await pending;return crypto.webcrypto.subtle.digest(...args);}};
  Object.defineProperty(ui.w,'crypto',{value:{subtle}});ui.el('upload-submit').click();await started;ui.choose('changed.pdf');release();await ui.settle();
  assert.equal(ui.created.length,0);assert.equal(ui.el('page-review').hidden,true);assert.equal(ui.el('upload-accept').disabled,true);
});
