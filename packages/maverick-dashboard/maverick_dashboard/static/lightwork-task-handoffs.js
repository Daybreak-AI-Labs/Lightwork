(function () {
  'use strict';
  const root = document.getElementById('handoff-root'); if (!root) return;
  const base = '/api/v1/task-handoffs', status = document.getElementById('handoff-status');
  const form = document.getElementById('handoff-form'), previewBox = document.getElementById('handoff-preview');
  const offer = document.getElementById('handoff-offer'), detail = document.getElementById('handoff-detail');
  let reviewed = null, pending = null, selected = null, busy = false;
  document.getElementById('handoff-compose').hidden = !root.dataset.bundle;
  function paragraph(parent, text) { const p = document.createElement('p'); p.textContent = text; parent.appendChild(p); }
  function link(parent, label, href) { const a = document.createElement('a'); a.textContent = label; a.href = href; parent.appendChild(a); }
  function clear() { detail.hidden = true; document.getElementById('handoff-content').replaceChildren(); previewBox.replaceChildren(); previewBox.hidden = true; reviewed = null; selected = null; offer.hidden = true; form.reset(); }
  async function request(path, body) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(base + path, {method:body ? 'POST':'GET', credentials:'same-origin', cache:'no-store',
        headers:{'Content-Type':'application/json','X-Lightwork-Bundle-Context':root.dataset.context}, body:body ? JSON.stringify(body):undefined,signal:controller.signal});
      const value = await response.json().catch(() => ({}));
      if (response.status === 401 || response.status === 403 || (value.detail && value.detail.code === 'context_changed')) clear();
      if (!response.ok) { const business=window.mvBusinessError ? window.mvBusinessError(value) : null; const error = new Error(business ? business.message : 'The handoff was not accepted. Review current access and retry.'); error.known = response.status < 500; throw error; }
      if (value.context !== root.dataset.context) { clear(); const error = new Error('Your account or workspace changed. Reopen handoffs.'); error.known = true; throw error; }
      return value;
    } finally { clearTimeout(timer); }
  }
  function review(parent, p) {
    parent.replaceChildren(); paragraph(parent, 'Recipient: ' + p.recipient + '. Source task: #' + p.source_id + '.');
    paragraph(parent, 'Next work: ' + p.instruction); paragraph(parent, 'Sender-declared pending commitments: ' + p.commitments);
    paragraph(parent, 'New task ceiling: $' + p.max_dollars.toFixed(2) + '. Source recorded spend: $' + p.source_cost_dollars.toFixed(2) + '. Unanswered source questions: ' + p.open_questions + '.');
    paragraph(parent,p.source_cost_scope || 'Source task recorded spend only.');
    paragraph(parent, 'Conversation/task IDs closed on acceptance: ' + p.closed_task_ids.join(', ') + '. Offer expires ' + new Date(p.expires_at * 1000).toLocaleString() + '.');
    const list = document.createElement('ul'); (p.items || []).forEach(item => { const li = document.createElement('li'); li.textContent = item.title + ' — ' + item.bytes + ' bytes — SHA-256 ' + item.sha256; list.appendChild(li); }); parent.appendChild(list);
    (p.consequences || []).forEach(text => paragraph(parent,text));
    link(parent, 'Review the exact selected bundle', '/deliverable-bundles?open=' + encodeURIComponent(p.bundle_id));
  }
  async function refresh() {
    if (busy) return;
    try {
      const value = await request(''); const list = document.getElementById('handoff-list'); list.replaceChildren();
      if (!value.items.length) paragraph(list,'No handoffs are waiting for your account.');
      value.items.forEach(item => { const button = document.createElement('button'); button.className='btn'; button.type='button'; button.textContent=item.title + ' — ' + item.state; button.onclick=() => open(item.id); list.appendChild(button); });
      status.textContent='Handoffs refreshed. Saved receipts remain available after reload.';
    } catch (error) { status.textContent=error.message; }
  }
  async function open(id) {
    if (pending || busy) { status.textContent='Resolve the unconfirmed action using its existing confirmation button before switching handoffs.'; return; }
    try { show((await request('/' + encodeURIComponent(id))).handoff); } catch (error) { status.textContent=error.message; if (error.known) detail.hidden=true; }
  }
  function show(row) {
    if (row.content_withheld) { clear(); status.textContent='Handoff declined. Future access ended; copies already received cannot be recalled.'; return; }
    selected=row; detail.hidden=false; document.getElementById('handoff-detail-title').textContent=row.preview.title + ' — ' + row.state;
    const content=document.getElementById('handoff-content'); review(content,row.preview);
    const actions=document.getElementById('handoff-actions'); actions.replaceChildren();
    if (row.acceptance) { paragraph(content,'Responsibility accepted. Original history remains private. No work started automatically.'); if (row.acceptance.successor_id) link(actions,'Review and explicitly start your continuation','/chat/goal/' + row.acceptance.successor_id); }
    if (row.can_accept) { addAction(actions,'accept','Accept responsibility and create unstarted continuation'); addAction(actions,'decline','Decline handoff'); }
    if (row.can_withdraw) addAction(actions,'withdraw','Withdraw offer');
    document.dispatchEvent(new CustomEvent('lightwork:copilot-selection',{detail:{selection:{kind:'handoff',id:row.id}}}));
  }
  function addAction(parent, action, label) { const button=document.createElement('button'); button.type='button'; button.className='btn'; button.textContent=label; button.onclick=() => mutate('/' + encodeURIComponent(selected.id) + '/respond', {action:action,expected_revision:selected.revision}, button); parent.appendChild(button); }
  function lock(value) { form.querySelectorAll('input,textarea,button').forEach(node => { node.disabled=value; }); }
  async function mutate(path, values, button) {
    if (busy) return;
    const signature=value=>JSON.stringify(Object.keys(value).filter(key=>key!=='operation_id').sort().map(key=>[key,value[key]]));
    if (pending && (pending.path !== path || signature(pending.body)!==signature(values))) { status.textContent='Retry the exact previous unconfirmed action first. Your new choice was not sent.'; return; }
    if (!pending) pending={path:path,body:{...values,operation_id:crypto.randomUUID()}};
    busy=true; button.disabled=true; lock(true); status.textContent='Saving the exact reviewed handoff. No task execution is being started.';
    try { const value=await request(pending.path,pending.body); pending=null; reviewed=null; offer.hidden=true; previewBox.hidden=true; show(value.handoff); status.textContent='Handoff receipt saved.'; }
    catch (error) { if (error.known) { pending=null; reviewed=null; offer.hidden=true; previewBox.hidden=true; } status.textContent=error.message + (pending ? ' The outcome is unconfirmed. Retry this same button; the saved reference prevents a second continuation.' : ' Your draft has been kept; refresh and review current state.'); }
    finally { busy=false; button.disabled=false; lock(!!pending); }
  }
  form.addEventListener('input',()=> { if (!pending) { reviewed=null; offer.hidden=true; previewBox.hidden=true; } });
  form.addEventListener('submit',async event => {
    event.preventDefault(); if (busy || pending) return;
    const values=Object.fromEntries(new FormData(form)); values.max_dollars=Number(values.max_dollars); busy=true; lock(true);
    try { const value=await request('/bundle/' + encodeURIComponent(root.dataset.bundle) + '/preview',values); reviewed={values:values,preview:value.preview}; review(previewBox,value.preview); previewBox.hidden=false; offer.hidden=false; status.textContent='Review the recipient, selected evidence, commitments and ceiling before confirming.'; }
    catch (error) { status.textContent=error.message; } finally { busy=false; lock(false); }
  });
  offer.addEventListener('click',()=> { if (pending) return mutate(pending.path,pending.body,offer); if (reviewed) mutate('/bundle/' + encodeURIComponent(root.dataset.bundle) + '/offer',{...reviewed.values,expected_review:reviewed.preview.review_version},offer); });
  document.getElementById('handoff-refresh').onclick=refresh;
  window.addEventListener('focus',()=> { if (!pending && !busy) refresh(); });
  window.addEventListener('beforeunload',event => { if (pending || (form.elements.namedItem('instruction').value && !offer.hidden)) { event.preventDefault(); event.returnValue=''; } });
  if (root.dataset.bundle) document.dispatchEvent(new CustomEvent('lightwork:copilot-selection',{detail:{selection:{kind:'bundle',id:root.dataset.bundle}}}));
  refresh();
}());
