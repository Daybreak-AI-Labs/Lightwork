(function () {
  'use strict';
  const root = document.getElementById('outcome-pilots');
  if (!root) return;
  const context = root.dataset.context;
  const $ = id => document.getElementById(id);
  const status = $('op-status'), chosen = $('op-selected'), report = $('op-report');
  let sources = [], current = null, busy = false, serial = 0, lostAuthority = false;
  const node = (tag, text) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; return e; };
  const operation = () => crypto.randomUUID();
  const sourceKey = source => source.kind + ':' + source.reference;
  function clearAuthority() {
    lostAuthority = true; current = null; sources = []; report.replaceChildren(); $('op-independent-report').replaceChildren();
    if ($('op-prepared')) $('op-prepared').replaceChildren();
    root.querySelectorAll('textarea,input').forEach(input => { input.value = ''; });
    root.querySelectorAll('select').forEach(select => select.replaceChildren()); controls(true);
  }
  async function request(path, method, body) {
    const r = await fetch('/api/v1/outcome-pilots' + path, {method: method || 'GET', cache:'no-store',
      headers: {'Content-Type':'application/json', 'X-Lightwork-Pilot-Context':context},
      ...(body ? {body: JSON.stringify(body)} : {})});
    if ([401,403,409].includes(r.status)) {
      clearAuthority();
    }
    let data;
    try { data = await r.json(); } catch (_) { throw new Error('Pilot information could not be read. Reload this page before continuing.'); }
    if (!r.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(e => e.loc.slice(1).join(' → ') + ': ' + e.msg).join('; ') : data.detail;
      const error = new Error(typeof detail === 'string' ? detail : 'The request could not be completed.'); error.status = r.status; throw error;
    }
    if (data.context !== context) { clearAuthority(); const error = new Error('The account or workspace changed. Reload this page.'); error.status = 409; throw error; }
    return data;
  }
  function controls(disabled) {
    root.setAttribute('aria-busy', String(disabled));
    root.querySelectorAll('button,input,select,textarea').forEach(b => { b.disabled = disabled || lostAuthority; });
    $('op-export').disabled = disabled || lostAuthority || !current;
  }
  async function mutation(task) {
    if (busy) return;
    busy = true; controls(true); status.textContent = 'Saving…';
    try { await task(); status.textContent = 'Saved. The current report is shown below.'; }
    catch (e) { status.textContent = e.message + (lostAuthority ? ' Reload this page to recheck access and current state.' : ' Your inputs are still here. Refresh the source before retrying.'); status.focus(); }
    finally { busy = false; controls(false); }
  }
  function addCase() {
    const count = $('op-planned-cases').children.length;
    if (count >= 100) return;
    const field = node('fieldset'); field.className = 'op-case'; field.append(node('legend', 'Case ' + (count + 1)));
    const label = node('label', 'Unstarted task or vendor case'), select = node('select'); select.required = true; select.className = 'input';
    select.append(new Option('Choose a case', ''));
    sources.forEach(s => select.append(new Option((s.kind === 'goal' ? 'Task: ' : 'Vendor: ') + s.label, sourceKey(s))));
    label.append(select); field.append(label);
    const remove = node('button', 'Remove case'); remove.type = 'button'; remove.className = 'btn';
    remove.addEventListener('click', () => field.remove()); field.append(remove); $('op-planned-cases').append(field);
  }
  function field(form, name, label, value, type) {
    const wrapper = node('label', label), input = node(type === 'textarea' ? 'textarea' : 'input');
    input.name = name; input.className = 'input'; input.value = value === null || value === undefined ? '' : String(value);
    if (type !== 'textarea') input.type = type || 'number';
    if (input.type === 'number') { input.min = '0'; input.max = '1000000'; input.step = name === 'corrections' ? '1' : 'any'; }
    if (type === 'textarea') { input.rows = 3; input.maxLength = 4000; input.required = true; }
    wrapper.append(input); form.append(wrapper);
  }
  const measurements = [
    ['baseline_minutes','Baseline hands-on minutes'], ['ai_hands_on_minutes','AI workflow hands-on minutes, excluding review and rework'],
    ['review_minutes','Review minutes'], ['rework_minutes','Rework minutes'], ['corrections','Corrections requested'],
    ['baseline_wall_minutes','Baseline elapsed minutes'], ['ai_wall_minutes','AI workflow elapsed minutes'],
    ['baseline_cost','Reported total baseline cost','text'], ['ai_total_cost','Reported total AI workflow cost, including failed attempts and human effort','text'],
    ['human_cost','Reported human preparation and review cost, excluding rework','text'], ['rework_cost','Reported rework cost','text'],
    ['currency','Currency, such as USD','text'], ['evidence','Observation and cost evidence references','textarea'], ['note','Method, timing, and limitations','textarea']
  ];
  function draw(data) {
    current = data; report.replaceChildren(); $('op-export').disabled = false;
    report.append(node('h2', data.title), node('p', data.customer + ' · ' + data.workflow), node('p', 'Frozen criteria: ' + data.criteria));
    report.append(node('p', 'Pilot reference: ' + data.id + ' · ' + (data.state || 'frozen')));
    if (data.learning_policy) {
      const learning = data.learning_policy;
      report.append(node('h3', 'Local learning policy'), node('p', learning.automatic_local_exclusion_enforced ? 'Automatic local learning exclusion is recorded for every evaluation task.' : 'Automatic local learning exclusion is not established for all evaluation tasks.'));
      report.append(node('p', 'Prior-exposure statement by ' + learning.prior_exposure_actor + ': ' + (learning.prior_exposure_statement || 'Not provided.')));
      if (learning.independent_review) report.append(node('p', 'Independent review by ' + learning.independent_review.actor + ': ' + learning.independent_review.decision.replaceAll('_',' ') + (learning.independent_review.current ? ' (current).' : ' (evidence changed; review again).')));
    }
    for (const [name, g] of Object.entries(data.groups)) {
      const box = node('section'); box.className = 'panel';
      box.append(node('h3', name === 'all' ? 'All planned cases' : name === 'holdout' ? 'Fixed evaluation group' : 'Process refinement group'));
      box.append(node('p', g.accepted + ' accepted / ' + g.planned + ' planned · ' + g.paired + ' current complete pairs · ' + g.unavailable + ' unavailable'));
      box.append(node('p', 'Reported effort difference: ' + (g.reported_minutes_difference === null ? 'unmeasured' : g.reported_minutes_difference.toFixed(1) + ' minutes') +
        '. Reported cost difference: ' + (g.reported_cost_difference === null ? 'incomplete or mixed currencies' : g.currency + ' ' + g.reported_cost_difference) + '.'));
      box.append(node('p', (g.criteria_met || 0) + ' accepted cases also reported to meet the frozen criteria. Reported total AI cost per accepted outcome: ' +
        (g.reported_ai_cost_per_accepted == null ? 'unmeasured; all planned cases need costs and at least one acceptance' : g.currency + ' ' + g.reported_ai_cost_per_accepted) + '.'));
      report.append(box);
    }
    const limits = node('ul'); data.limitations.forEach(text => limits.append(node('li', text))); report.append(limits);
    data.cases.forEach(c => {
      const details = node('details'); details.className = 'panel';
      details.append(node('summary', 'Case ' + c.id + (c.label ? ': ' + c.label : '') + ' · ' + c.status.replaceAll('_',' ') + ' · ' + (c.allocation === 'holdout' ? 'evaluation' : 'refinement')));
      if (!c.source) { details.append(node('p', 'Source unavailable. Its observations are hidden until access is restored.')); report.append(details); return; }
      const link = node('a', 'Review current source'); link.href = c.source.href; details.append(link);
      details.append(node('p', (c.source.sample ? 'Sample source. ' : '') + (c.source.accepted ? 'Current source has a recorded acceptance.' : 'Current source has no qualifying acceptance.') +
        ' Recorded runtime price: ' + (c.source.runtime_cost_usd === null ? 'unmeasured' : 'USD ' + c.source.runtime_cost_usd) + '; provider invoice unverified.'));
      details.append(node('p', 'Leave unknown values blank. Enter non-overlapping effort: preparation, review, and rework are separate. Reported total cost includes all attempts.'));
      if (c.learning_exclusion) details.append(node('p', c.learning_exclusion.enforced ? 'Local learning exclusion receipt: ' + c.learning_exclusion.policy_id : 'Local learning exclusion receipt is unavailable or no longer matches these task inputs.'));
      details.append(node('p', 'Human and rework cost details are operator reports. They are already part of your reported total; provider allocations are shown separately and are not added automatically.'));
      const form = node('form'), grid = node('div'); grid.className = 'op-grid';
      measurements.forEach(([key,label,type]) => field(grid,key,label,(c.measurement || {})[key],type)); form.append(grid);
      const criteria = node('label','Did this case meet the frozen criteria?'), select = node('select'); select.name = 'criteria_met'; select.className = 'input';
      [['','Not measured'],['true','Yes'],['false','No']].forEach(([v,t]) => select.append(new Option(t,v)));
      select.value = c.measurement && c.measurement.criteria_met !== null ? String(c.measurement.criteria_met) : ''; criteria.append(select); form.append(criteria);
      const submit = node('button','Save observation'); submit.className = 'btn'; submit.type = 'submit'; form.append(submit);
      const feedback = node('p'); feedback.setAttribute('role','status'); feedback.setAttribute('aria-live','polite'); form.append(feedback);
      form.addEventListener('submit', event => {
        event.preventDefault(); if (!form.reportValidity()) return;
        const fd = new FormData(form), body = {expected_revision:data.revision, operation_id:form.dataset.operation || operation(), source_version:c.source.version};
        measurements.forEach(([key,,type]) => { const v = String(fd.get(key) || '').trim(); body[key] = v === '' ? null : type ? v : Number(v); });
        body.criteria_met = fd.get('criteria_met') === '' ? null : fd.get('criteria_met') === 'true';
        form.dataset.operation = body.operation_id;
        mutation(async () => { await request('/' + data.id + '/cases/' + c.id, 'PUT', body); feedback.textContent = 'Observation saved.'; delete form.dataset.operation; await load(data.id); });
      });
      form.addEventListener('input', () => { delete form.dataset.operation; });
      details.append(form); report.append(details);
    });
    if (window.LightworkPilotCosts) window.LightworkPilotCosts.render({host:report,data,request,reload:load,mutation});
    else report.append(node('p','Provider cost controls could not load. Reload this page before allocating billing evidence.'));
  }
  async function load(id) {
    const ticket = ++serial;
    if (!id) { current = null; report.replaceChildren(); $('op-export').disabled = true; return; }
    try { const data = await request('/' + id); if (ticket === serial) draw(data); }
    catch (e) { if (ticket === serial) { current = null; report.replaceChildren(); $('op-export').disabled = true; status.textContent = e.message; } throw e; }
  }
  async function refreshList(selected) {
    const data = await request(''); chosen.replaceChildren(new Option('Choose a pilot', ''));
    data.items.forEach(r => chosen.append(new Option(r.title + ' · ' + r.cases + ' cases',r.id)));
    chosen.value = selected || ''; if (data.partial) status.textContent = 'Pilot coverage is partial; the scan limit was reached.';
  }
  $('op-plan').addEventListener('submit', event => {
    event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
    const fd = new FormData(form), cases = [...$('op-planned-cases').querySelectorAll('select')].map(s => sources.find(source => sourceKey(source) === s.value));
    if (cases.some(c => !c)) { status.textContent = 'Choose a currently available source for every case.'; return; }
    if (cases.length < 4 || new Set(cases.map(c => c.kind + ':' + c.reference)).size !== cases.length) { status.textContent = 'Choose at least four different unstarted cases.'; return; }
    const mode = fd.get('learning_mode') || 'observational', prior = String(fd.get('prior_exposure_statement') || '').trim();
    if (mode === 'exclude_local_learning' && (prior.length < 20 || cases.some(c => c.kind !== 'goal'))) { status.textContent = 'Protected pilots need task cases and a prior-exposure statement of at least20 characters.'; return; }
    const body = {title:fd.get('title'),customer:fd.get('customer'),workflow:fd.get('workflow'),criteria:fd.get('criteria'),cases, learning_mode:mode, prior_exposure_statement:prior,
      operation_id:form.dataset.operation || operation()}; form.dataset.operation = body.operation_id;
    mutation(async () => { const saved = await request('', 'POST', body); await refreshList(saved.id); await load(saved.id); form.reset(); delete form.dataset.operation; $('op-plan-details').open = false; });
  });
  $('op-plan').addEventListener('input', () => { delete $('op-plan').dataset.operation; });
  $('op-learning-mode').addEventListener('change', () => { $('op-prior-exposure').required = $('op-learning-mode').value === 'exclude_local_learning'; });
  $('op-review-load').addEventListener('click', () => {
    if (busy || lostAuthority) return;
    const identifier = $('op-review-reference').value.trim();
    if (!/^OPL-[A-Za-z0-9_-]+$/.test(identifier)) { status.textContent = 'Enter the pilot reference shared by its owner.'; return; }
    mutation(async () => {
      const packet = await request('/independent-review/' + encodeURIComponent(identifier));
      const host = $('op-independent-report'); host.replaceChildren();
      host.append(node('h3', packet.title), node('p', 'Frozen criteria: ' + packet.criteria), node('p', 'Prior-exposure statement: ' + packet.learning_policy.prior_exposure_statement));
      host.append(node('p', packet.learning_policy.automatic_local_exclusion_enforced ? 'All evaluation task exclusion receipts match the plan.' : 'Exclusion is incomplete or unverified.'));
      packet.cases.forEach(item => host.append(node('p', 'Case ' + item.id + ' · ' + item.allocation + ': ' + (item.source ? item.source.title : 'unavailable') + (item.learning_exclusion ? ' · exclusion ' + (item.learning_exclusion.enforced ? 'recorded' : 'unverified') : ''))));
      const form = node('form'), decisionLabel = node('label','Independent decision'), decision = node('select'); decision.className = 'input';
      decision.append(new Option('Needs more evidence', 'needs_more_evidence'), new Option('Accept the stated local scope', 'accepted_local_scope')); decisionLabel.append(decision); form.append(decisionLabel);
      field(form,'notes','Evidence checked and remaining limitations','','textarea'); form.querySelector('textarea').minLength = 20;
      const confirmLabel = node('label','I reviewed this exact plan and local exclusion evidence.'), confirm = node('input'); confirm.type = 'checkbox'; confirm.required = true; confirmLabel.prepend(confirm); form.append(confirmLabel);
      const save = node('button','Record independent review'); save.type = 'submit'; save.className = 'btn'; form.append(save);
      form.addEventListener('submit', event => {
        event.preventDefault(); if (!form.reportValidity()) return;
        const body = {expected_revision:packet.revision, evidence_version:packet.learning_policy.evidence_version, decision:decision.value,
          notes:form.querySelector('textarea').value, confirm:confirm.checked, operation_id:form.dataset.operation || operation()}; form.dataset.operation = body.operation_id;
        mutation(async () => { await request('/independent-review/' + encodeURIComponent(identifier),'POST',body); host.replaceChildren(node('p','Independent review recorded. The owner’s current report will show whether its evidence remains current.')); });
      });
      form.addEventListener('input', () => { delete form.dataset.operation; }); host.append(form);
    });
  });
  $('op-add-case').addEventListener('click', addCase);
  chosen.addEventListener('change', () => { if (!busy) load(chosen.value).catch(() => {}); });
  $('op-refresh').addEventListener('click', () => load(chosen.value).catch(() => {}));
  $('op-export').addEventListener('click', () => {
    if (!current) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}));
    const a = node('a'); a.href = url; a.download = 'workflow-pilot-report.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  status.tabIndex = -1;
  async function refreshSources() {
    const data = await request('/sources');
    sources = data.items; $('op-source-coverage').textContent = data.coverage.map(c => (c.kind === 'goal' ? 'Pending tasks' : 'Vendor intake') + ': ' + c.status).join(' · ');
    if ($('op-specialist')) {
      const selected = $('op-specialist').value; $('op-specialist').replaceChildren(new Option('Choose a specialist',''));
      (data.specialists || []).forEach(s => $('op-specialist').append(new Option(s.label,s.domain))); $('op-specialist').value = selected;
    }
    $('op-planned-cases').querySelectorAll('select').forEach(select => {
      const selected = select.value; select.replaceChildren(new Option('Choose a case',''));
      sources.forEach(s => select.append(new Option((s.kind === 'goal' ? 'Task: ' : 'Vendor: ') + s.label,sourceKey(s)))); select.value = selected;
    });
  }
  if ($('op-prepare')) {
    $('op-prepare').addEventListener('input', () => { delete $('op-prepare').dataset.operation; });
    $('op-prepare').addEventListener('submit', event => {
      event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
      const fd = new FormData(form), body = {title:fd.get('title'),description:fd.get('description'),domain:fd.get('domain'),operation_id:form.dataset.operation || operation()};
      form.dataset.operation = body.operation_id;
      mutation(async () => {
        const saved = await request('/prepared-tasks','POST',body);
        $('op-prepared').replaceChildren(node('span', 'Prepared: ' + saved.title + '. Choose it in the pilot plan before starting. '));
        const link = node('a','Open task'); link.href = saved.href; $('op-prepared').append(link);
        await refreshSources(); form.reset(); delete form.dataset.operation;
      });
    });
  }
  Promise.all([refreshList(), refreshSources().then(() => { for (let i=0; i<4; i++) addCase(); })]).catch(e => { status.textContent = e.message; });
})();
