(function () {
  'use strict';
  const root = document.getElementById('outcome-pilots');
  if (!root) return;
  const context = root.dataset.context;
  const $ = id => document.getElementById(id);
  const status = $('op-status'), chosen = $('op-selected'), report = $('op-report');
  const loadMore = $('op-load-more');
  let sources = [], current = null, busy = false, serial = 0, lostAuthority = false, releaseReady = false;
  let pilotCursor = '';
  const node = (tag, text) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; return e; };
  const operation = () => crypto.randomUUID();
  const sourceKey = source => source.kind + ':' + source.reference;
  const publishSelection = id => document.dispatchEvent(new CustomEvent('lightwork:copilot-selection',
    {detail:{selection:id ? {kind:'outcome_pilot',id} : {}}}));
  function clearAuthority() {
    lostAuthority = true; releaseReady = false; current = null; sources = []; pilotCursor = ''; report.replaceChildren(); $('op-independent-report').replaceChildren();
    publishSelection('');
    if ($('op-prepared')) $('op-prepared').replaceChildren();
    root.querySelectorAll('textarea,input').forEach(input => { input.value = ''; });
    root.querySelectorAll('select').forEach(select => select.replaceChildren()); controls(true);
    if (loadMore) loadMore.hidden = true;
  }
  async function request(path, method, body) {
    const r = await fetch('/api/v1/outcome-pilots' + path, {method: method || 'GET', cache:'no-store',
      headers: {'Content-Type':'application/json', 'X-Lightwork-Pilot-Context':context},
      ...(body ? {body: JSON.stringify(body)} : {})});
    if ([401,403].includes(r.status)) {
      clearAuthority();
    }
    let data;
    try { data = await r.json(); } catch (_) { throw new Error('Pilot information could not be read. Reload this page before continuing.'); }
    if (!r.ok) {
      const detail = Array.isArray(data.detail) ? null : data.detail;
      if (r.status === 409 && detail && detail.code === 'pilot_context_changed') clearAuthority();
      const raw = typeof detail === 'string' ? detail : detail && typeof detail.message === 'string' ? detail.message : '';
      // Pilot rules answer in full sentences; anything code-like (field
      // paths, keys, symbols) is replaced by a plain status sentence.
      const readable = raw && /^[A-Z][^{}<>=_\[\]\\/]*[.!?]$/.test(raw.trim()) && raw.length <= 400;
      const message = readable ? raw.trim()
        : typeof window.mvErrorText === 'function' ? window.mvErrorText({status: r.status, headers: r.headers}, 'save that')
        : (r.status === 422 || r.status === 400 ? 'Some of the details need fixing. Check what you entered and try again.' : 'That could not be completed. Please try again.');
      const error = new Error(message); error.status = r.status; throw error;
    }
    if (data.context !== context) { clearAuthority(); const error = new Error('The account or workspace changed. Reload this page.'); error.status = 409; throw error; }
    return data;
  }
  function controls(disabled) {
    root.setAttribute('aria-busy', String(disabled));
    root.querySelectorAll('button,input,select,textarea').forEach(b => { b.disabled = disabled || lostAuthority; });
    $('op-export').disabled = disabled || lostAuthority || !current;
    $('op-candidate').disabled = disabled || lostAuthority || !releaseReady;
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
  const costCoverageFamilies = [
    ['provider_usage', 'Provider usage'], ['failed_attempts', 'Failed attempts'],
    ['setup', 'Setup'], ['review', 'Human review'],
    ['exception', 'Exception handling'], ['rework', 'Rework'],
    ['support', 'Support'], ['allocation', 'Shared allocation'],
  ];
  function coverageFields(form, measurement) {
    const fieldset = node('fieldset'), legend = node('legend', 'Delivered-cost coverage');
    const help = node('p', 'For every family, choose Included in total, Confirmed zero, or Unknown, then cite the evidence or assumption. Unknown keeps ROI unavailable.');
    const grid = node('div'); grid.className = 'op-grid';
    fieldset.append(legend, help, grid);
    const current = measurement && measurement.cost_coverage;
    costCoverageFamilies.forEach(([family, label]) => {
      const wrapper = node('div'); wrapper.dataset.coverageFamily = family;
      const statusLabel = node('label', label), select = node('select');
      select.className = 'input'; select.name = 'coverage_' + family + '_status';
      [['unknown','Unknown'],['included','Included in total'],['zero','Confirmed zero']]
        .forEach(([value, text]) => select.append(new Option(text, value)));
      select.value = current && current[family] ? current[family].status : 'unknown';
      statusLabel.append(select);
      const noteLabel = node('label', 'Evidence or assumption'), note = node('textarea');
      note.className = 'input'; note.name = 'coverage_' + family + '_note';
      note.maxLength = 1000; note.rows = 2; note.required = true;
      note.value = current && current[family]
        ? current[family].note : 'No cost evidence was established for this family.';
      noteLabel.append(note); wrapper.append(statusLabel, noteLabel); grid.append(wrapper);
    });
    form.append(fieldset);
  }
  function draw(data) {
    current = data; report.replaceChildren(); $('op-export').disabled = false;
    report.append(node('h2', data.title), node('p', data.customer + ' · ' + data.workflow), node('p', 'Frozen criteria: ' + data.criteria));
    report.append(node('p', 'Pilot reference: ' + data.id + ' · ' + (data.state || 'frozen')));
    if (data.admission) report.append(node('p', data.admission.status === 'complete' && data.admission.admitted_count === data.admission.expected_count ?
      'Outcome accounting admission: complete for all ' + data.admission.expected_count + ' planned cases.' :
      'Outcome accounting admission is incomplete. This pilot cannot run or accept observations yet.'));
    if (data.learning_policy) {
      const learning = data.learning_policy;
      report.append(node('h3', 'Local learning policy'), node('p', learning.automatic_local_exclusion_enforced ? 'Automatic local learning exclusion is recorded for every evaluation task.' : 'Automatic local learning exclusion is not established for all evaluation tasks.'));
      report.append(node('p', 'Prior-exposure statement by ' + learning.prior_exposure_actor + ': ' + (learning.prior_exposure_statement || 'Not provided.')));
      if (learning.independent_review) report.append(node('p', 'Independent review by ' + learning.independent_review.actor + ': ' + learning.independent_review.decision.replaceAll('_',' ') + (learning.independent_review.current ? ' (current).' : ' (evidence changed; review again).')));
    }
    if (data.release_binding) {
      const box = node('section'); box.className = 'panel';
      box.append(node('h3', 'Frozen improvement release'));
      box.append(node('p', 'Candidate ' + data.release_binding.candidate_id + ' was bound to the deployed baseline before these cases started. The frozen plan preserves both exact release commitments.'));
      box.append(node('p', 'This installation does not attest which release served each case. Reported scores, effort, acceptance and cost do not establish a causal release improvement.'));
      const check = node('button', 'Check current release and recovery'); check.type = 'button'; check.className = 'btn';
      const feedback = node('p'); feedback.setAttribute('role','status'); feedback.setAttribute('aria-live','polite');
      check.addEventListener('click', async () => {
        if (busy || lostAuthority) return;
        check.disabled = true; feedback.textContent = 'Checking the current promotion and deployed release…';
        try {
          const result = await request('/' + encodeURIComponent(data.id) + '/release-status');
          if (!current || current.id !== data.id) return;
          const deployed = {baseline_active:'The original baseline is currently deployed.', candidate_active:'The candidate is currently deployed.',
            other_release_active:'Another release is currently deployed.', candidate_changed:'The candidate record changed after this plan was frozen.',
            rollback_unavailable:'Deployment compatibility or rollback readiness changed.', unavailable:'The deployed release could not be verified now. Try again in a minute.'};
          const states = {awaiting_review:'waiting for independent approval', authorized:'approved for staging', staged:'staged',
            canary:'in monitored rollout', active:'active', rolled_back:'rolled back', uncertain:'outcome uncertain',
            denied:'denied', blocked:'blocked', not_advancing:'not advancing', unavailable:'unavailable'};
          feedback.replaceChildren(node('span', (deployed[result.deployment_state] || 'Release status is unavailable. Refresh the page and try again.') +
            ' Promotion is ' + (states[result.promotion_state] || 'under review') + '. Case release exposure and causal lift remain unverified. '));
          const link = node('a', 'Open candidate approval, promotion and rollback'); link.href = '/research#research-promotions'; feedback.append(link);
        } catch (error) { feedback.textContent = error.message + ' Recheck the candidate in Research before continuing.'; }
        finally { check.disabled = lostAuthority; }
      });
      box.append(check, feedback); report.append(box);
    }
    if (data.evidence_readiness) {
      const ready = data.evidence_readiness, box = node('section'); box.className = 'panel';
      box.append(node('h3', 'What this evidence supports'));
      box.append(node('p', ready.reported_effort_comparison_ready ?
        'Every planned case has a current complete pair. You can review the reported hands-on effort difference across the full cohort.' :
        'The reported effort difference is partial. Do not present it as the result for all planned cases.'));
      box.append(node('p', ready.frozen_criteria_review_ready ?
        'Every case has a recorded decision against the criteria set before work began.' :
        'At least one case still needs a decision against the original criteria.'));
      box.append(node('p', ready.reported_cost_comparison_ready ?
        'Every case has reported total cost, one currency, and a complete cost-family declaration. This is still operator-reported evidence, not an independently verified invoice.' :
        'The cost comparison is not ready to use as a complete financial result.'));
      box.append(node('p', 'This pilot does not establish that a release caused an improvement or that the reported result will repeat for another customer.'));
      if (ready.next_steps.length) {
        box.append(node('h4', 'What to finish'));
        const list = node('ul'); ready.next_steps.forEach(text => list.append(node('li', text))); box.append(list);
      }
      report.append(box);
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
      if (!c.source) { details.append(node('p', 'Source unavailable. Its observations are hidden until access is restored; ask the source owner to restore access.')); report.append(details); return; }
      const link = node('a', 'Review current source'); link.href = c.source.href; details.append(link);
      details.append(node('p', (c.source.sample ? 'Sample source. ' : '') + (c.source.accepted ? 'Current source has a recorded acceptance.' : 'Current source has no qualifying acceptance.') +
        ' Recorded runtime price: ' + (c.source.runtime_cost_usd === null ? 'unmeasured' : window.mvMoney ? window.mvMoney(c.source.runtime_cost_usd) : 'USD ' + c.source.runtime_cost_usd) + '; provider invoice unverified.'));
      details.append(node('p', 'Leave unknown values blank. Enter non-overlapping effort: preparation, review, and rework are separate. Reported total cost includes all attempts.'));
      if (c.learning_exclusion) details.append(node('p', c.learning_exclusion.enforced ? 'Local learning exclusion receipt: ' + c.learning_exclusion.policy_id : 'Local learning exclusion receipt is unavailable or no longer matches these task inputs. Refresh the page and try again.'));
      details.append(node('p', 'Human and rework cost details are operator reports. They are already part of your reported total; provider allocations are shown separately and are not added automatically.'));
      const form = node('form'), grid = node('div'); grid.className = 'op-grid';
      measurements.forEach(([key,label,type]) => field(grid,key,label,(c.measurement || {})[key],type));
      form.append(grid); coverageFields(form, c.measurement || null);
      const criteria = node('label','Did this case meet the frozen criteria?'), select = node('select'); select.name = 'criteria_met'; select.className = 'input';
      [['','Not measured'],['true','Yes'],['false','No']].forEach(([v,t]) => select.append(new Option(t,v)));
      select.value = c.measurement && c.measurement.criteria_met !== null ? String(c.measurement.criteria_met) : ''; criteria.append(select); form.append(criteria);
      const submit = node('button','Save observation'); submit.className = 'btn'; submit.type = 'submit'; form.append(submit);
      const feedback = node('p'); feedback.setAttribute('role','status'); feedback.setAttribute('aria-live','polite'); form.append(feedback);
      form.addEventListener('submit', event => {
        event.preventDefault(); if (!form.reportValidity()) return;
        const fd = new FormData(form), body = {expected_revision:data.revision, operation_id:form.dataset.operation || operation(), source_version:c.source.version};
        measurements.forEach(([key,,type]) => { const v = String(fd.get(key) || '').trim(); body[key] = v === '' ? null : type ? v : Number(v); });
        body.cost_coverage = Object.fromEntries(costCoverageFamilies.map(([family]) => [family, {
          status: String(fd.get('coverage_' + family + '_status') || 'unknown'),
          note: String(fd.get('coverage_' + family + '_note') || '').trim(),
        }]));
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
    if (!id) { current = null; report.replaceChildren(); $('op-export').disabled = true; publishSelection(''); return; }
    try { const data = await request('/' + id); if (ticket === serial) { draw(data); publishSelection(data.id); } }
    catch (e) { if (ticket === serial) { current = null; report.replaceChildren(); $('op-export').disabled = true; status.textContent = e.message; publishSelection(''); } throw e; }
  }
  async function refreshList(selected, cursor) {
    const continuation = cursor || '';
    const data = await request('?limit=100' + (continuation ? '&cursor=' + encodeURIComponent(continuation) : ''));
    if (!continuation) chosen.replaceChildren(new Option('Choose a pilot', ''));
    const existing = new Set([...chosen.options].map(option => option.value));
    data.items.forEach(r => {
      if (!existing.has(r.id)) chosen.append(new Option(r.title + ' · ' + r.cases + ' cases',r.id));
    });
    const page = data.page && typeof data.page === 'object' ? data.page : {};
    const hasMore = page.has_more === true;
    pilotCursor = hasMore && typeof page.next_cursor === 'string' ? page.next_cursor : '';
    if (loadMore) loadMore.hidden = !pilotCursor;
    if (selected && [...chosen.options].some(option => option.value === selected)) chosen.value = selected;
    if (hasMore) status.textContent = 'More pilots are available. Load the next page to reach older cohorts.';
    return {found: !selected || chosen.value === selected, hasMore};
  }
  async function refreshThroughSelected(selected) {
    let page = await refreshList(selected);
    for (let count = 0; selected && !page.found && page.hasMore && count < 10; count++) {
      page = await refreshList(selected, pilotCursor);
    }
    return page;
  }
  $('op-plan').addEventListener('submit', event => {
    event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
    const fd = new FormData(form), cases = [...$('op-planned-cases').querySelectorAll('select')].map(s => sources.find(source => sourceKey(source) === s.value));
    if (cases.some(c => !c)) { status.textContent = 'Choose a currently available source for every case.'; return; }
    if (cases.length < 4 || new Set(cases.map(c => c.kind + ':' + c.reference)).size !== cases.length) { status.textContent = 'Choose at least four different unstarted cases.'; return; }
    const mode = fd.get('learning_mode') || 'observational', prior = String(fd.get('prior_exposure_statement') || '').trim();
    const candidate = String(fd.get('candidate_id') || '').trim();
    if (candidate && !releaseReady) { status.textContent = 'Release comparisons are unavailable. Check release readiness again before saving this plan.'; return; }
    if (candidate && mode !== 'exclude_local_learning') { status.textContent = 'An improvement candidate needs protected evaluation tasks. Choose that learning-data treatment and record prior-exposure checks.'; return; }
    if (mode === 'exclude_local_learning' && (prior.length < 20 || cases.some(c => c.kind !== 'goal'))) { status.textContent = 'Protected pilots need task cases and a prior-exposure statement of at least 20 characters.'; return; }
    const body = {title:fd.get('title'),customer:fd.get('customer'),workflow:fd.get('workflow'),criteria:fd.get('criteria'),cases, learning_mode:mode, prior_exposure_statement:prior,
      operation_id:form.dataset.operation || operation()}; form.dataset.operation = body.operation_id;
    if (candidate) body.candidate_id = candidate;
    mutation(async () => { const saved = await request('', 'POST', body); await refreshThroughSelected(saved.id); await load(saved.id); form.reset(); delete form.dataset.operation; $('op-plan-details').open = false; });
  });
  $('op-plan').addEventListener('input', () => { delete $('op-plan').dataset.operation; });
  $('op-learning-mode').addEventListener('change', () => { $('op-prior-exposure').required = $('op-learning-mode').value === 'exclude_local_learning'; });
  $('op-candidate').addEventListener('change', () => {
    if (releaseReady && $('op-candidate').value) $('op-learning-mode').value = 'exclude_local_learning';
    $('op-prior-exposure').required = $('op-learning-mode').value === 'exclude_local_learning';
    delete $('op-plan').dataset.operation;
  });
  async function refreshReleaseCandidates() {
    if (busy || lostAuthority) return;
    busy = true; controls(true); const note = $('op-candidate-status'); note.textContent = 'Checking release readiness…';
    try {
      const data = await request('/release-candidates');
      const picker = $('op-candidate'), prior = picker.value;
      releaseReady = data.available === true && data.readiness && data.readiness.status === 'ready';
      picker.replaceChildren(new Option('No candidate release — workflow pilot', ''));
      if (releaseReady) (data.items || []).forEach(item => picker.append(new Option('Candidate ' + item.candidate_id + ' · ' + item.target_id + ' · ' + item.state.replaceAll('_',' '), item.candidate_id)));
      if (releaseReady && [...picker.options].some(option => option.value === prior)) picker.value = prior;
      if (picker.value !== prior) delete $('op-plan').dataset.operation;
      note.textContent = String(data.readiness && data.readiness.next_action || data.detail || 'Release readiness could not be verified. Check again before comparing releases.') +
        (data.partial ? ' Only the most recent candidates are shown.' : '');
    } catch (error) { releaseReady = false; $('op-candidate').replaceChildren(new Option('No candidate release — workflow pilot', '')); delete $('op-plan').dataset.operation; note.textContent = error.message + ' Check release readiness again before comparing releases.'; }
    finally { busy = false; controls(false); }
  }
  $('op-candidate-refresh').addEventListener('click', refreshReleaseCandidates);
  $('op-plan-details').addEventListener('toggle', () => { if ($('op-plan-details').open) refreshReleaseCandidates(); });
  $('op-review-load').addEventListener('click', () => {
    if (busy || lostAuthority) return;
    const identifier = $('op-review-reference').value.trim();
    if (!/^OPL-[A-Za-z0-9_-]+$/.test(identifier)) { status.textContent = 'Choose a pilot to review.'; return; }
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
  // Reviewers choose from the protected pilots waiting in this workspace
  // instead of typing a reference the owner shared.
  let reviewQueueLoaded = false, reviewQueueLoading = false;
  async function loadReviewQueue() {
    const picker = $('op-review-reference'), note = $('op-review-note');
    if (!picker || reviewQueueLoaded || reviewQueueLoading || lostAuthority) return;
    reviewQueueLoading = true;
    note.textContent = 'Loading pilots waiting for review…';
    try {
      // Read directly: a person who is not an administrator is simply told
      // who can review, without resetting the rest of the page.
      let r;
      try {
        r = await fetch('/api/v1/outcome-pilots/independent-review', {cache:'no-store',
          headers: {'X-Lightwork-Pilot-Context':context}});
      } catch (_) { throw new Error('Lightwork is not responding. Check your connection and try again.'); }
      if (r.status === 401 || r.status === 403) throw new Error('Only an administrator who did not create a pilot can review it. Sign in with that account to see pilots waiting for review.');
      if (!r.ok) throw new Error(typeof window.mvErrorText === 'function' ? window.mvErrorText(r, 'load pilots waiting for review') : 'Pilots waiting for review could not be loaded. Try again.');
      let data;
      try { data = await r.json(); } catch (_) { throw new Error('Pilots waiting for review could not be loaded. Try again.'); }
      if (!data || data.context !== context) throw new Error('The account or workspace changed. Reload this page.');
      const items = Array.isArray(data.items) ? data.items.filter(item => item && /^OPL-[A-Za-z0-9_-]+$/.test(String(item.id || ''))) : [];
      picker.replaceChildren(new Option(items.length ? 'Choose a pilot' : 'No pilots are waiting for review', ''));
      const decisions = {accepted_local_scope: 'accepted', needs_more_evidence: 'more evidence requested'};
      items.forEach(item => {
        const when = Number.isFinite(item.frozen_at) ? (window.mvDateAbsolute ? window.mvDateAbsolute(item.frozen_at) : new Date(item.frozen_at * 1000).toLocaleDateString()) : '';
        const done = item.decision ? ' · already reviewed (' + (decisions[item.decision] || 'reviewed') + ')' : '';
        picker.append(new Option(String(item.title || 'Untitled pilot') + (when ? ' · planned ' + when : '') + done, item.id));
      });
      note.textContent = items.length ? '' : 'No protected pilots from other people are waiting in this workspace.';
      reviewQueueLoaded = true;
    } catch (error) {
      picker.replaceChildren(new Option('Pilots could not be loaded', ''));
      note.textContent = error.message || 'Only an administrator who did not create a pilot can review it.';
    } finally {
      reviewQueueLoading = false;
    }
  }
  const reviewPanel = $('op-review-panel');
  if (reviewPanel) reviewPanel.addEventListener('toggle', () => { if (reviewPanel.open) loadReviewQueue(); });
  $('op-add-case').addEventListener('click', addCase);
  if (loadMore) loadMore.addEventListener('click', async () => {
    if (busy || lostAuthority || !pilotCursor) return;
    busy = true; controls(true); status.textContent = 'Loading more pilots…';
    try {
      const page = await refreshList(chosen.value, pilotCursor);
      status.textContent = page.hasMore
        ? 'Another pilot page loaded. More older cohorts are available.'
        : 'All pilots for this account and workspace are available.';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false; controls(false);
    }
  });
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
      const selected = $('op-specialist').value; $('op-specialist').replaceChildren(new Option('Choose an agent or specialist',''));
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
