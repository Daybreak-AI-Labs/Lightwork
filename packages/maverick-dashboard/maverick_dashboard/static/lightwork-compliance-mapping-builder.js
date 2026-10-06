(function () {
  'use strict';
  if (!document.getElementById('cm-app')) return;
  const API = '/api/v1/compliance/mapping-builder', $ = id => document.getElementById('cm-' + id);
  let current = null, source = null, review = null, pending = null, busy = false, queued = false, generation = 0, detail = null, draft = null, reviewOffset = null, reviewedCount = 0, draftEpoch = 0;
  const requests = new Set();
  const storage = 'lightwork.compliance.mapping.original';
  function accepted(value,exact){if(!value.mapping||!/^[A-Za-z0-9][A-Za-z0-9._:-]*\/[1-9][0-9]*$/.test(value.mapping))return;
    if(draft&&draftEpoch!==exact.draft_epoch)return;
    if(!Array.from($('target').options).some(option=>option.value===value.mapping))$('target').append(new Option(value.details.name+' · version '+value.details.version,value.mapping));
    $('target').value=value.mapping;draft=null;$('control-editor').replaceChildren();
  }
  try { pending = JSON.parse(sessionStorage.getItem(storage) || 'null'); } catch (_) { pending = null; }
  function save(value) { pending = value; try { if (value) sessionStorage.setItem(storage, JSON.stringify(value)); else sessionStorage.removeItem(storage); } catch (_) {} $('recovery').hidden = !value; }
  function say(message) { window.mvStatus($('status'), message); }
  function problem(error) { return window.mvPlainDetail?.(error.detail) || window.mvErrorText?.(error, 'check this mapping') || 'The result could not be checked. Read saved records before trying again.'; }
  async function request(path, body) {
    const controller = new AbortController(); requests.add(controller); const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(API + path, {method: body ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
        headers: body ? {'Content-Type': 'application/json'} : {}, ...(body ? {body: JSON.stringify(body)} : {})});
      const text = await response.text(); if (text.length > 262144) throw new Error('The reply is too large. Check saved records.');
      const value = JSON.parse(text); if (!response.ok) throw {status: response.status, detail: value.detail}; return value;
    } finally { clearTimeout(timer); requests.delete(controller); }
  }
  function lock(value) { busy = value; document.querySelectorAll('#cm-app button, #cm-app input, #cm-app select').forEach(el => { el.disabled = value; }); $('review').disabled = value || !source || !draft || draft.stale || !!pending; $('confirm').disabled=value||reviewOffset!==null; $('apply').disabled = value || !review || !$('confirm').checked || reviewOffset !== null || !!pending; }
  function clearReview() { review = null; reviewOffset = null; reviewedCount = 0; $('review-rows').replaceChildren(); $('review-panel').hidden = true; $('confirm').checked = false; }
  function node(tag, text) { const value = document.createElement(tag); value.textContent = text; return value; }
  function button(text, action) { const value = node('button', text); value.type = 'button'; value.className = 'btn btn--ghost'; value.addEventListener('click', action); return value; }
  function summary(value) { const list = document.createElement('dl'); Object.entries(value).forEach(([key, text]) => { if (text && typeof text === 'object') return; list.append(node('dt', key.replaceAll('_', ' ')), node('dd', String(text))); }); return list; }
  function showReview(value, approval) { review = value; reviewOffset = value.control_review?.next_offset ?? null; reviewedCount = value.control_review?.rows.length || 0; $('consequence').textContent = value.message; $('summary').replaceChildren(summary(value.details)); $('review-rows').replaceChildren(); if (value.control_review) addControls(value.control_review.rows, $('review-rows'), true); reviewProgress(value.control_review?.total || 0); $('review-panel').hidden = false; $('confirm').checked = false; $('apply').textContent = approval ? 'Approve reviewed mapping' : 'Save reviewed mapping'; $('review-panel').scrollIntoView({block: 'nearest'}); }
  function reviewProgress(total) { $('more-review').hidden = reviewOffset === null; $('review-count').textContent = total ? reviewedCount + ' of ' + total + ' mapped controls displayed for review.' : ''; $('confirm').disabled = reviewOffset !== null; }
  function defaults() { return {control_status: $('control-state').value, control_responsibility: $('control-owner').value, evidence_work: $('evidence-kind').value === 'none' ? [] : [{saved_work:'', work:$('evidence-kind').value, responsibility:$('evidence-owner').value, frequency:$('cadence').value}]}; }
  function schema(row) { const choice = (title, values) => ({type:'string',title,enum:Object.keys(values),enumNames:Object.values(values)});
    return {type:'object',title:'Control work',additionalProperties:false,required:['control_status','control_responsibility','evidence_work'],properties:{
      control_status:choice('Control status',{planned:'Planned',not_started:'Not started',not_implemented:'Not in place yet'}),
      control_responsibility:choice('Control responsibility',{control_owner:'Control owner',second_line:'Risk and compliance owner'}),
      evidence_work:{type:'array',title:'Evidence jobs',maxItems:64,items:{type:'object',additionalProperties:false,required:['saved_work','work','responsibility','frequency'],properties:{
        saved_work:choice('Saved evidence work',Object.fromEntries([['','New evidence job'],...row.saved_work.map(work=>[work.value,work.label])])),
        work:choice('Work',{collect:'Collect evidence',refresh:'Refresh evidence',review:'Review evidence'}),
        responsibility:choice('Responsibility',{evidence_owner:'Evidence owner',control_owner:'Control owner',second_line:'Risk and compliance owner'}),
        frequency:choice('Frequency',{one_time:'Once',monthly:'Every month',quarterly:'Every quarter',annual:'Every year',continuous:'Continuously'})}}}}}; }
  function editorNotice() { $('edited-count').textContent = (draft?.overrides.size || 0) + ' controls changed individually. Other controls keep their starting or saved choices.'; $('reset-control').hidden = !$('control-picker').value; $('reload-editor').hidden = !draft?.stale; }
  function showEditor() { $('control-editor').replaceChildren(); const key=$('control-picker').value, row=draft?.rows.get(key); if (!row) { editorNotice(); return; }
    const value = draft.overrides.get(key) || {params:structuredClone(row.choices || defaults())}; let mounting=true;
    try { const plan=FDActionFields.compile(schema(row)); FDActionFields.mount($('control-editor'),value,{plan,revision:draft.mapping_revision||draft.source},()=>{const legend=$('control-editor').querySelector('fieldset > legend');if(legend)legend.textContent='Control work';if(mounting)return; ++draftEpoch; draft.overrides.set(key,value);clearReview();editorNotice();lock(false);}); }
    catch(error){say(problem(error));} const legend=$('control-editor').querySelector('fieldset > legend');if(legend)legend.textContent='Control work';mounting=false; editorNotice(); }
  async function loadEditors(more=false) {
    if(busy||!source||!current)return; const scope=$('scope').value,target=$('target').value,ticket=++generation; lock(true);
    try { const value=await request('/editor?scope='+encodeURIComponent(scope)+'&mapping='+encodeURIComponent(target)+'&offset='+(more?draft?.next_offset||0:0));
      if(ticket!==generation||value.context!==current.context||scope!==$('scope').value||target!==$('target').value)return;
      if(draft&&(draft.scope!==scope||draft.target!==target))draft=null;
      if(draft&&(draft.source!==value.revision||draft.mapping_revision!==value.mapping_revision)){draft.stale=true;throw {detail:'The saved mapping or scope changed. Your individual draft is kept. Reload current mapping choices before a new review.'};}
      if(!draft)draft={scope,target,source:value.revision,mapping_revision:value.mapping_revision,rows:new Map(),overrides:new Map(),total:value.total};
      value.rows.forEach(row=>draft.rows.set(row.value,row));draft.next_offset=value.next_offset;draft.stale=false;
      const keep=$('control-picker').value; $('control-picker').replaceChildren(new Option('Choose a control','')); draft.rows.forEach(row=>$('control-picker').append(new Option(row.label,row.value))); if(draft.rows.has(keep))$('control-picker').value=keep;draft.context=value.context;
      $('load-editors').hidden=draft.next_offset===null;$('defaults').hidden=!!target;$('name').readOnly=!!target;if(target)$('name').value=value.name;
      showEditor();
    }catch(error){if(draft)draft.stale=true;say(problem(error));editorNotice();}
    finally{lock(false);if(queued){queued=false;refresh();}}
  }
  async function reloadEditor(){if(busy||!draft||typeof window.mvConfirm!=='function')return;const exact=draft;
    const ok=await window.mvConfirm('Replace the individual draft with the latest saved choices? Nothing is saved or approved by reloading.',{okText:'Reload current choices'});if(!ok||busy||draft!==exact)return;
    if(exact.target){const id=exact.target.slice(0,exact.target.lastIndexOf('/')),row=current.mappings.find(item=>item.latest&&item.value.slice(0,item.value.lastIndexOf('/'))===id);if(!row){say('This mapping is unavailable. Your draft is kept.');return;}$('target').value=row.value;}
    draft=null;clearReview();await loadEditors(false);
  }
  async function refresh() {
    if (busy) { queued = true; return; }
    const ticket = ++generation, keep = $('scope').value, target = $('target').value; clearReview(); source = null; detail = null; $('detail').hidden = true; lock(true);
    try {
      const value = await request(''); if (ticket !== generation) return; current = value;
      if (pending && pending.context !== current.context) save(null);
      if (draft && draft.context && draft.context !== current.context) { draft=null; $('name').value=''; $('control-editor').replaceChildren(); }
      $('scope').replaceChildren(new Option('Choose a final scope', ''));
      current.scopes.forEach(row => { const option = new Option(row.label, row.value); option.disabled = !row.supported; $('scope').append(option); });
      if (current.scopes.some(row => row.value === keep && row.supported)) $('scope').value = keep;
      $('target').replaceChildren(new Option('Create a new mapping','')); current.mappings.filter(row=>row.latest).forEach(row=>$('target').append(new Option(row.label,row.value)));
      if(target&&!current.mappings.some(row=>row.latest&&row.value===target)){const missing=new Option('Earlier selected version · reload current choices',target);missing.disabled=true;$('target').append(missing);} $('target').value=target;
      $('mappings').replaceChildren();
      current.mappings.forEach(row => { const article = node('article', ''); article.append(node('h3', row.label), node('p', row.status));
        const actions = node('div', ''); actions.className = 'cm-actions'; actions.append(button('Review saved choices', () => openMapping(row.value)));
        if(row.latest) actions.append(button('Revise saved choices',()=>{if(busy||pending)return;$('target').value=row.value;draft=null;clearReview();$('form').scrollIntoView({block:'start'});if(source)loadEditors();else $('scope').focus();}));
        if (row.can_approve) actions.append(button('Approve for use', () => approve(row.value))); article.append(actions); $('mappings').append(article); });
      if (!current.mappings.length) $('mappings').append(node('p', 'No mappings yet. Build one from a final scope.'));
      $('listing').textContent = current.listing_notice; $('recovery').hidden = !pending;
      say(current.scopes.length ? 'Choose a final scope and its business choices.' : 'Finalize an assessment scope before building its task mapping.');
    } catch (error) { say(problem(error)); }
    finally { lock(false); if (queued) { queued = false; refresh(); } else if ($('scope').value && current) await loadControls(false); }
  }
  function addControls(rows, target, mapped) {
    rows.forEach(row => { const card = node('article', ''); card.append(node('h3', row.control), node('p', row.framework));
      if (mapped) { card.append(node('p', row.initial_status + ' · ' + row.owner)); row.evidence.forEach(work => card.append(node('p', work.work + ' · ' + work.owner + ' · ' + work.frequency))); }
      else if (row.reason) card.append(node('p', row.reason)); target.append(card); });
  }
  async function loadControls(more) {
    if (busy) return; clearReview(); const scope = $('scope').value, ticket = ++generation;
    if (!scope) { source = null; $('source').textContent = ''; $('control-list').replaceChildren(); lock(false); return; }
    const offset = more ? source?.next_offset : 0; lock(true);
    try {
      const value = await request('/controls?scope=' + encodeURIComponent(scope) + '&offset=' + (offset || 0));
      if (ticket !== generation || value.context !== current.context || scope !== $('scope').value) return;
      if (more && source.revision !== value.revision) throw {detail: 'The scope changed. Refresh its controls before continuing.'};
      source = value; if (!more) $('control-list').replaceChildren(); addControls(value.controls, $('control-list'), false);
      $('source').textContent = value.total + ' controls. Every control will be included; none will be skipped. Control owner: ' + value.responsibilities.control_owner + '. Evidence owner: ' + value.responsibilities.evidence_owner + '. Risk and compliance owner: ' + value.responsibilities.second_line + '.'; $('more-controls').hidden = value.next_offset === null;
    } catch (error) { source = null; say(problem(error)); }
    finally { lock(false); if (queued) { queued = false; refresh(); } else if(!more && source) await loadEditors(); }
  }
  async function openMapping(key, more) {
    if (busy) return; const ticket = ++generation; lock(true);
    try {
      const value = await request('/mapping?mapping=' + encodeURIComponent(key) + '&offset=' + (more ? detail?.next_offset || 0 : 0));
      if (ticket !== generation || value.context !== current.context) return; detail = {...value, key};
      if (!more) $('detail-rows').replaceChildren(); addControls(value.rows, $('detail-rows'), true);
      $('detail-title').textContent = value.name + ' · version ' + value.version; $('detail-status').textContent = value.status + ' · ' + value.controls + ' controls';
      $('detail').hidden = false; $('more-details').hidden = value.next_offset === null;
    } catch (error) { say(problem(error)); }
    finally { lock(false); if (queued) { queued = false; refresh(); } }
  }
  async function approve(key) {
    if (busy || pending) { say('Check the original interrupted request first.'); return; } clearReview(); lock(true); const context = current.context, ticket = ++generation;
    try { const value = await request('/review-approval', {context, mapping: key}); if (ticket === generation && current.context === context && value.context === context) showReview(value, true); }
    catch (error) { say(problem(error)); } finally { lock(false); if (queued) { queued = false; refresh(); } }
  }
  $('form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !source || !draft || draft.stale || pending) return;
    for(const [key,value] of draft.overrides){const issue=FDActionFields.issue(FDActionFields.compile(schema(draft.rows.get(key))),value.params)||Object.values(value._actionErrors||{}).find(Boolean);if(issue){$('control-picker').value=key;showEditor();say(issue);return;}}
    clearReview(); lock(true); const context = current.context, ticket = ++generation;
    try { const value = await request('/review', {context, revision: draft.source, scope: $('scope').value, name: $('name').value,mapping:draft.target,mapping_revision:draft.mapping_revision,controls:Array.from(draft.overrides,([control,value])=>({control,...value.params})),
      control_state: $('control-state').value, control_owner: $('control-owner').value, evidence_kind: $('evidence-kind').value, evidence_owner: $('evidence-owner').value, cadence: $('cadence').value});
      if (ticket === generation && current.context === context && value.context === context) showReview(value, false);
    } catch (error) { say(problem(error)); } finally { lock(false); if (queued) { queued = false; refresh(); } }
  });
  $('form').addEventListener('input', event => { if (!busy && event.target.id !== 'cm-scope') { ++draftEpoch; clearReview(); lock(false); } });
  $('scope').addEventListener('change', () => {draft=null;$('control-editor').replaceChildren();loadControls(false);});
  $('target').addEventListener('change',()=>{draft=null;clearReview();$('control-editor').replaceChildren();if(source)loadEditors();});
  $('control-picker').addEventListener('change',showEditor);$('load-editors').addEventListener('click',()=>loadEditors(true));$('reload-editor').addEventListener('click',reloadEditor);
  $('reset-control').addEventListener('click',()=>{if(busy||!draft)return;++draftEpoch;draft.overrides.delete($('control-picker').value);clearReview();showEditor();lock(false);});
  $('more-review').addEventListener('click',async()=>{if(busy||!review||reviewOffset===null)return;const exact=review,ticket=++generation;lock(true);try{const value=await request('/read-review',{token:exact.token,offset:reviewOffset});if(ticket!==generation||review!==exact||value.context!==current.context)return;addControls(value.rows,$('review-rows'),true);reviewedCount+=value.rows.length;reviewOffset=value.next_offset;reviewProgress(value.total);}catch(error){clearReview();say(problem(error));}finally{lock(false);}});
  $('evidence-kind').addEventListener('change', () => { $('evidence-fields').hidden = $('evidence-kind').value === 'none'; });
  $('defaults').addEventListener('input',()=>{if(!busy)showEditor();});
  $('confirm').addEventListener('change', () => lock(false)); $('cancel').addEventListener('click', () => { clearReview(); lock(false); });
  $('apply').addEventListener('click', async () => {
    if (busy || !review || !$('confirm').checked || reviewOffset!==null || pending) return; const exact = {token: review.token, context: review.context, draft_epoch:draftEpoch}; save(exact); clearReview(); lock(true);
    let message;
    try { const value = await request('/apply', {token: exact.token, confirmed: true}); if (current.context === exact.context) { accepted(value,exact);save(null); message = value.message; } }
    catch (error) { if (error.status && error.status < 500) save(null); message = problem(error) + (pending ? ' Check the original saved result before starting another request.' : ''); }
    finally { lock(false); queued = false; await refresh(); if (current?.context === exact.context && message) say(message); }
  });
  $('readback').addEventListener('click', async () => {
    if (busy || !pending) return; const exact = pending; lock(true); let message;
    try { const value = await request('/readback', {token: exact.token}); if (current.context === exact.context) { message = value.message;
      if (value.status !== 'not_found') {accepted(value,exact);save(null);} else message += ' If you continue, retry only this exact reviewed request; do not create another mapping while its result is uncertain.'; } }
    catch (error) { message = problem(error); }
    finally { lock(false); queued = false; await refresh(); if (current?.context === exact.context && message) say(message); }
  });
  $('retry').addEventListener('click', async () => {
    if (busy || !pending || typeof window.mvConfirm !== 'function') return;
    const exact = pending, ok = await window.mvConfirm('Continue the exact earlier reviewed request? The same protected request is reused. If it was already saved or approved, Lightwork reads the saved result instead of making another mapping.', {okText: 'Continue exact request'});
    if (!ok || busy || pending !== exact) return; lock(true); let message;
    try { const value = await request('/apply', {token: exact.token, confirmed: true}); if (current.context === exact.context) { accepted(value,exact);save(null); message = value.message; } }
    catch (error) { message = problem(error); }
    finally { lock(false); queued = false; await refresh(); if (current?.context === exact.context && message) say(message); }
  });
  $('dismiss').addEventListener('click', async () => {
    if (busy || !pending || typeof window.mvConfirm !== 'function') return;
    const exact = pending, ok = await window.mvConfirm('Have you checked the saved mappings and their current review status? Clearing this interrupted-request reminder does not prove the earlier request succeeded or undo any saved mapping.', {okText: 'Clear reminder'});
    if (!ok || busy || pending !== exact) return; save(null); clearReview(); lock(false); say('The reminder is cleared. Saved mappings remain unchanged; the earlier request is not being reported as successful.');
  });
  $('refresh').addEventListener('click', refresh); $('more-controls').addEventListener('click', () => loadControls(true)); $('more-details').addEventListener('click', () => openMapping(detail.key, true));
  window.addEventListener('lightwork:product-state-changed', refresh); document.addEventListener('lightwork:product-operation-complete', refresh);
  document.addEventListener('lightwork:copilot-context-changed',()=>{++generation;requests.forEach(controller=>controller.abort());current=null;source=null;draft=null;detail=null;save(null);clearReview();$('name').value='';['mappings','control-list','control-editor','detail-rows'].forEach(id=>$(id).replaceChildren());$('detail').hidden=true;queued=true;if(!busy){queued=false;refresh();}});
  window.LightworkComplianceMapping={open:async(context,mode)=>{if(busy||pending||!['create','revise'].includes(mode))return false;const value=await request('');if(value.context!==context||current?.context!==context)return false;clearReview();$('form').scrollIntoView({block:'start'});if(mode==='revise')$('target').focus();else $('scope').focus();return true;}};
  save(pending); refresh();
})();
