(function () {
  'use strict';
  var source = document.getElementById('activation-data'); if (!source) return;
  var data = JSON.parse(source.textContent), current = data.draft, selectedSource = null, busy = false, requests = new Set(), generation = 0;
  function el(id) { return document.getElementById('activation-' + id); }
  var form = el('form'), status = el('status');
  var operation = Array.from(crypto.getRandomValues(new Uint8Array(16)), function (v) { return v.toString(16).padStart(2,'0'); }).join('');
  async function call(url, body) {
    var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, 30000); requests.add(controller);
    try {
      var response = await fetch(url, {method: body ? 'POST' : 'GET', credentials:'same-origin', cache:'no-store', signal:controller.signal,
        headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined});
      var value = await response.json();
      if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : value.detail && value.detail.message || 'This action could not be confirmed. Refresh the saved draft before trying again.');
      return value;
    } finally { clearTimeout(timer); requests.delete(controller); }
  }
  function sourceControls() {
    el('real').hidden = el('mode').value !== 'real';
    var connected = el('source-kind').value === 'connected'; el('connected').hidden = !connected; el('material-label').hidden = connected;
    var job = data.jobs[el('job').value]; el('sample').textContent = el('mode').value === 'sample' && job ? job.sample : '';
  }
  function draftBody() {
    return {context:data.context, operation_id:operation, expected_revision:current ? current.revision : null,
      job:el('job').value, mode:el('mode').value, source_kind:el('source-kind').value,
      material:el('material').value, criteria:el('criteria').value, source:selectedSource || {}};
  }
  function render(value) {
    if (value.context !== data.context) throw new Error('Your account or workspace changed. Reopen this private draft.');
    current = value; var values = value.values;
    el('job').value=values.job; el('mode').value=values.mode; el('source-kind').value=values.source_kind;
    el('material').value=values.material; el('criteria').value=values.criteria; selectedSource=values.source;
    el('selected-source').textContent=selectedSource.name || 'No connected document selected.';
    form.querySelectorAll('input,textarea,select,button').forEach(function (field) { field.disabled = !data.readiness.can_start || value.state !== 'draft' || !!value.result; });
    sourceControls(); el('review').hidden=false;
    el('review-text').textContent = data.jobs[value.job].label + '\n' + (values.mode === 'sample' ? 'Fictional practice data' : 'Real data') + '\n\nAcceptance criteria:\n' + values.criteria + '\n\nSource:\n' + (values.source_kind === 'text' ? values.material : values.source.name || 'Choose a connected document before starting.');
    el('launch').hidden=!!value.result; el('launch').disabled=!data.readiness.can_start || !data.readiness.provider_ready || (values.source_kind==='connected' && !values.source.doc_id);
    el('result').hidden=!value.result;
    if(value.result) {
      el('task-link').href=value.result.href; el('result-text').textContent=value.result.text || ('Current task state: ' + value.result.status);
      el('accept').disabled=!value.result.complete || value.result.accepted || !data.readiness.can_start;
      el('workflow').disabled=!value.result.accepted || !data.readiness.can_start;
      el('acceptance-state').textContent=value.result.accepted ? 'You accepted this exact current result.' : 'This result has not received your current acceptance. A completed task alone does not finish this path.';
    }
    el('workflow-link').hidden=!value.workflow; if(value.workflow) el('workflow-link').href='/workflow-builder?edit='+encodeURIComponent(value.workflow);
    status.textContent=(value.message || 'Private draft saved.') + (value.audit_pending ? ' Its audit delivery is pending.' : '');
    history.replaceState(null,'',value.return_href);
    document.dispatchEvent(new CustomEvent('lightwork:copilot-selection',{detail:{selection:{activation_id:value.id}}}));
  }
  async function perform(work) {
    if(busy)return; busy=true; var started=++generation;
    try { await work(started); } catch(error) { if(started===generation)status.textContent=error.name==='AbortError'?'The response was interrupted. Refresh this draft to recover its existing task before starting anything else.':error.message; }
    finally { busy=false; }
  }
  async function saveDraft() {
    var value=await call('/api/v1/employee-activation'+(current?'?draft='+encodeURIComponent(current.id):''),draftBody()); render(value); return value;
  }
  form.addEventListener('submit',function(event){event.preventDefault();perform(saveDraft);});
  ['mode','source-kind'].forEach(function(id){el(id).addEventListener('change',sourceControls);});
  el('job').addEventListener('change',function(){el('criteria').value=data.jobs[el('job').value].criteria; sourceControls();});
  el('search').addEventListener('click',function(){perform(async function(started){
    var query=el('query').value.trim(); if(!query)throw new Error('Describe the document you want to find.');
    var value=await call('/api/v1/docs/discover',{subject:query,limit:8}); if(started!==generation)return;
    var list=el('hits'); list.replaceChildren();
    (value.hits||[]).forEach(function(hit){var row=document.createElement('li'),button=document.createElement('button'); button.type='button';button.className='btn btn--ghost';button.textContent=hit.name||hit.title||'Select document';
      button.addEventListener('click',function(){selectedSource={source:hit.source,doc_id:hit.doc_id,name:hit.name||hit.title||'',ref:hit.ref||{}};el('selected-source').textContent=selectedSource.name;});row.appendChild(button);list.appendChild(row);});
    status.textContent=value.hits&&value.hits.length?'Choose one exact document, then save and review your draft.':'No permitted document was found. Check Connections or use source text you are authorized to provide.';
  });});
  el('repair').addEventListener('click',function(){perform(async function(){
    var value=await saveDraft(); window.location.assign(value.repair_href);
  });});
  el('refresh').addEventListener('click',function(){if(current)perform(async function(){render(await call('/api/v1/employee-activation/'+encodeURIComponent(current.id)));});});
  ['launch','accept','workflow'].forEach(function(action){el(action).addEventListener('click',function(){if(!current)return;perform(async function(){
    var message=action==='launch'?'Start this exact reviewed task? It may incur provider cost.':action==='accept'?'Does the current result meet your acceptance criteria? This records your human review.':'Save reusable instructions with a new source input? Original source documents, result text and connection bindings are excluded.';
    if(!(await window.mvConfirm(message)))return;
    var body={context:data.context,expected_revision:current.revision,confirmed:true,result_fingerprint:current.result?current.result.fingerprint:''};
    render(await call('/api/v1/employee-activation/'+encodeURIComponent(current.id)+'/'+action,body));
  });});});
  window.addEventListener('pagehide',function(){generation++;requests.forEach(function(controller){controller.abort();});});
  window.addEventListener('pageshow',function(event){if(event.persisted)window.location.reload();});
  if(current)render(current);else if(data.jobs[el('job').value]){el('criteria').value=data.jobs[el('job').value].criteria;sourceControls();}
}());
