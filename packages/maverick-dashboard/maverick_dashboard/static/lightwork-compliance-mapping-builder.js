(function () {
  'use strict';
  if (!document.getElementById('cm-app')) return;
  const API = '/api/v1/compliance/mapping-builder', $ = id => document.getElementById('cm-' + id);
  let current = null, source = null, review = null, pending = null, busy = false, queued = false, generation = 0, detail = null;
  const storage = 'lightwork.compliance.mapping.original';
  try { pending = JSON.parse(sessionStorage.getItem(storage) || 'null'); } catch (_) { pending = null; }
  function save(value) { pending = value; try { if (value) sessionStorage.setItem(storage, JSON.stringify(value)); else sessionStorage.removeItem(storage); } catch (_) {} $('recovery').hidden = !value; }
  function say(message) { window.mvStatus($('status'), message); }
  function problem(error) { return window.mvPlainDetail?.(error.detail) || window.mvErrorText?.(error, 'check this mapping') || 'The result could not be checked. Read saved records before trying again.'; }
  async function request(path, body) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(API + path, {method: body ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
        headers: body ? {'Content-Type': 'application/json'} : {}, ...(body ? {body: JSON.stringify(body)} : {})});
      const text = await response.text(); if (text.length > 262144) throw new Error('The reply is too large. Check saved records.');
      const value = JSON.parse(text); if (!response.ok) throw {status: response.status, detail: value.detail}; return value;
    } finally { clearTimeout(timer); }
  }
  function lock(value) { busy = value; document.querySelectorAll('#cm-app button, #cm-app input, #cm-app select').forEach(el => { el.disabled = value; }); $('review').disabled = value || !source || !!pending; $('apply').disabled = value || !review || !$('confirm').checked || !!pending; }
  function clearReview() { review = null; $('review-panel').hidden = true; $('confirm').checked = false; }
  function node(tag, text) { const value = document.createElement(tag); value.textContent = text; return value; }
  function button(text, action) { const value = node('button', text); value.type = 'button'; value.className = 'btn btn--ghost'; value.addEventListener('click', action); return value; }
  function summary(value) { const list = document.createElement('dl'); Object.entries(value).forEach(([key, text]) => { if (text && typeof text === 'object') return; list.append(node('dt', key.replaceAll('_', ' ')), node('dd', String(text))); }); return list; }
  function showReview(value, approval) { review = value; $('consequence').textContent = value.message; $('summary').replaceChildren(summary(value.details)); $('review-panel').hidden = false; $('confirm').checked = false; $('apply').textContent = approval ? 'Approve reviewed mapping' : 'Save reviewed mapping'; $('review-panel').scrollIntoView({block: 'nearest'}); }
  async function refresh() {
    if (busy) { queued = true; return; }
    const ticket = ++generation, keep = $('scope').value; clearReview(); source = null; detail = null; $('detail').hidden = true; lock(true);
    try {
      const value = await request(''); if (ticket !== generation) return; current = value;
      if (pending && pending.context !== current.context) save(null);
      $('scope').replaceChildren(new Option('Choose a final scope', ''));
      current.scopes.forEach(row => { const option = new Option(row.label, row.value); option.disabled = !row.supported; $('scope').append(option); });
      if (current.scopes.some(row => row.value === keep && row.supported)) $('scope').value = keep;
      $('mappings').replaceChildren();
      current.mappings.forEach(row => { const article = node('article', ''); article.append(node('h3', row.label), node('p', row.status));
        const actions = node('div', ''); actions.className = 'cm-actions'; actions.append(button('Review saved choices', () => openMapping(row.value)));
        if (row.can_approve) actions.append(button('Approve for use', () => approve(row.value))); article.append(actions); $('mappings').append(article); });
      if (!current.mappings.length) $('mappings').append(node('p', 'No mappings yet. Build one from a final scope.'));
      $('listing').textContent = current.listing_notice; $('recovery').hidden = !pending;
      say(current.scopes.length ? 'Choose a final scope and its business choices.' : 'Finalize an assessment scope before building its task mapping.');
    } catch (error) { say(problem(error)); }
    finally { lock(false); if (queued) { queued = false; refresh(); } else if ($('scope').value && current) loadControls(false); }
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
    finally { lock(false); if (queued) { queued = false; refresh(); } }
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
    event.preventDefault(); if (busy || !source || pending) return; clearReview(); lock(true); const context = current.context, ticket = ++generation;
    try { const value = await request('/review', {context, revision: source.revision, scope: $('scope').value, name: $('name').value,
      control_state: $('control-state').value, control_owner: $('control-owner').value, evidence_kind: $('evidence-kind').value, evidence_owner: $('evidence-owner').value, cadence: $('cadence').value});
      if (ticket === generation && current.context === context && value.context === context) showReview(value, false);
    } catch (error) { say(problem(error)); } finally { lock(false); if (queued) { queued = false; refresh(); } }
  });
  $('form').addEventListener('input', event => { if (event.target.id !== 'cm-scope') { clearReview(); lock(false); } });
  $('scope').addEventListener('change', () => loadControls(false));
  $('evidence-kind').addEventListener('change', () => { $('evidence-fields').hidden = $('evidence-kind').value === 'none'; });
  $('confirm').addEventListener('change', () => lock(false)); $('cancel').addEventListener('click', () => { clearReview(); lock(false); });
  $('apply').addEventListener('click', async () => {
    if (busy || !review || !$('confirm').checked || pending) return; const exact = {token: review.token, context: review.context}; save(exact); clearReview(); lock(true);
    let message;
    try { const value = await request('/apply', {token: exact.token, confirmed: true}); if (current.context === exact.context) { save(null); message = value.message; } }
    catch (error) { if (error.status && error.status < 500) save(null); message = problem(error) + (pending ? ' Check the original saved result before starting another request.' : ''); }
    finally { lock(false); queued = false; await refresh(); if (current?.context === exact.context && message) say(message); }
  });
  $('readback').addEventListener('click', async () => {
    if (busy || !pending) return; const exact = pending; lock(true); let message;
    try { const value = await request('/readback', {token: exact.token}); if (current.context === exact.context) { message = value.message;
      if (value.status !== 'not_found') save(null); else message += ' If you continue, retry only this exact reviewed request; do not create another mapping while its result is uncertain.'; } }
    catch (error) { message = problem(error); }
    finally { lock(false); queued = false; await refresh(); if (current?.context === exact.context && message) say(message); }
  });
  $('retry').addEventListener('click', async () => {
    if (busy || !pending || typeof window.mvConfirm !== 'function') return;
    const exact = pending, ok = await window.mvConfirm('Continue the exact earlier reviewed request? The same protected request is reused. If it was already saved or approved, Lightwork reads the saved result instead of making another mapping.', {okText: 'Continue exact request'});
    if (!ok || busy || pending !== exact) return; lock(true); let message;
    try { const value = await request('/apply', {token: exact.token, confirmed: true}); if (current.context === exact.context) { save(null); message = value.message; } }
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
  save(pending); refresh();
})();
