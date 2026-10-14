(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('intent-value')) return;
  const API = '/api/v1/intent-value';
  const dialog = $('iv-detail');
  const operations = new WeakMap();
  const dirty = new Set();
  let report = null, settings = null, current = null;
  let readGeneration = 0, detailGeneration = 0, reportQuery = '';
  let benchmarkRevision = null, settingsRevision = null, taskRevision = null;
  let busy = false, taskBusy = false, opener = null, taskMode = 'all';
  let allGroups = {}, allTasks = [];
  const arr = value => Array.isArray(value) ? value : [];
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  const text = value => typeof value === 'string' ? value : '';
  const number = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
  const money = value => number(value) === null ? 'Not yet known' : new Intl.NumberFormat(undefined, {style:'currency', currency:'USD', maximumFractionDigits:2}).format(Number(value));
  const amount = value => number(value) === null ? 'Not yet known' : new Intl.NumberFormat(undefined, {maximumFractionDigits:1}).format(Number(value));
  const date = value => {
    const numeric = number(value);
    const d = new Date(numeric === null ? value : numeric * 1000);
    return Number.isFinite(d.getTime()) ? d.toLocaleString() : 'Date unavailable';
  };
  function node(tag, value, cls) {
    const el = document.createElement(tag);
    if (value !== undefined) el.textContent = String(value);
    if (cls) el.className = cls;
    return el;
  }
  function say(id, message, error = false) {
    const el = $(id);
    el.textContent = message;
    el.classList.toggle('iv-error', error);
  }
  function option(select, value, label) {
    const el = node('option', label);
    el.value = value;
    select.appendChild(el);
  }
  function requestId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  }
  function publish(task) {
    document.dispatchEvent(new CustomEvent('lightwork:copilot-selection', {
      detail:{selection: task ? {kind:'intent_value_task', id:task.task_id} : {}}
    }));
  }
  class RequestError extends Error {
    constructor(status) {
      const messages = {
        400:'Check the choices and numbers, then try again.',
        401:'Your sign-in expired. Sign in again, then reload this page.',
        403:'You no longer have permission to view or change this work. Ask your workspace administrator for access.',
        404:'This work is no longer available in your current workspace. Refresh the report.',
        409:'This information changed since you opened it. Review the latest version below, then save your edits again.',
        422:'Check the estimate range and required fields, then try again.',
        503:'Value reporting is temporarily unavailable. Your edits are kept; retry when the service is ready.'
      };
      super(messages[status] || 'The request could not be completed. Your edits are kept; try again.');
      this.status = status;
    }
  }
  async function request(path, options = {}) {
    let response;
    try { response = await fetch(API + path, {credentials:'same-origin', ...options}); }
    catch (_) { throw new RequestError(0); }
    if (!response.ok) throw new RequestError(response.status);
    try { return await response.json(); }
    catch (_) { throw new Error('The report could not be read. Refresh and try again.'); }
  }
  function facts(host, entries) {
    const list = node('dl', undefined, 'iv-facts');
    entries.forEach(([label, value]) => {
      list.append(node('dt', label), node('dd', value));
    });
    host.appendChild(list);
  }
  function history(host, rows) {
    host.replaceChildren();
    if (!arr(rows).length) { host.appendChild(node('li', 'No recorded changes in this view.')); return; }
    arr(rows).forEach(row => {
      const descriptions = {intent_correction:'Work type or size corrected', correction:'Work type or size corrected',
        benchmark_update:'Company benchmark updated', benchmark:'Company benchmark updated', continuity:'Related work linked',
        task_continuity:'Related work linked', settings:'Reporting settings updated', settings_update:'Reporting settings updated',
        execution:'Lightwork activity recorded', completed:'Work completed', observation:'Opportunity observed'};
      const label = text(row.label) || text(row.description) || descriptions[row.kind || row.event || row.action] || 'Value record updated';
      const entry = node('li');
      entry.appendChild(node('strong', label));
      if (row.at || row.created_at || row.timestamp) entry.appendChild(node('span', ' · ' + date(row.at || row.created_at || row.timestamp)));
      if (row.reason) entry.appendChild(node('p', text(row.reason), 'muted'));
      host.appendChild(entry);
    });
  }
  function benchmarkText(value) {
    if (!object(value)) return 'No benchmark is assigned. Choose the type of work to estimate its value.';
    const sized = object(value.sized_minutes) ? value.sized_minutes : value;
    return amount(sized.minutes) + ' minutes, range ' + amount(sized.minutes_low) + '–' + amount(sized.minutes_high) +
      ' minutes; ' + money(value.hourly_rate) + ' per hour. ' + (text(value.provenance) || 'Source not provided') +
      '. Version ' + (text(value.version) || 'not provided') + '.';
  }
  function checkReport(value) {
    if (!object(value) || !object(value.totals) || !Array.isArray(value.tasks) || !Number.isInteger(value.revision)) {
      throw new Error('The report is incomplete. Refresh and try again.');
    }
    return value;
  }
  function filteredQuery() {
    const until = Math.floor(Date.now() / 1000);
    const query = new URLSearchParams({since:String(until - Number($('iv-period').value) * 86400), until:String(until), scope_kind:$('iv-scope').value});
    if ($('iv-scope').value !== 'platform') query.set('scope_id', $('iv-entity').value);
    if ($('iv-intent-filter').value) query.set('intent_id', $('iv-intent-filter').value);
    return query.toString();
  }
  function namedGroups(kind) {
    const key = {specialist:'specialists', agent:'agents', process:'processes'}[kind];
    return arr(allGroups[key]);
  }
  function scopeChoices() {
    const kind = $('iv-scope').value, select = $('iv-entity'), previous = select.value;
    $('iv-entity-field').hidden = kind === 'platform';
    select.replaceChildren();
    namedGroups(kind).forEach(row => option(select, row.id || row.scope_id, row.label || row.name || 'Unnamed ' + kind));
    if (Array.from(select.options).some(o => o.value === previous)) select.value = previous;
    if (!select.options.length) option(select, '', 'No authorized work in this period');
    select.disabled = busy || kind === 'platform' || !select.value;
  }
  function fillIntents(select, includeAll) {
    const previous = select.value;
    select.replaceChildren();
    if (includeAll) option(select, '', 'All types');
    option(select, 'unknown', 'Uncertain or not yet classified');
    arr(report.benchmarks).forEach(row => option(select, row.intent_id, row.label));
    if (Array.from(select.options).some(o => o.value === previous)) select.value = previous;
  }
  function lock() {
    $('intent-value').setAttribute('aria-busy', String(busy));
    $('iv-filters').querySelectorAll('button,input,select').forEach(el => { el.disabled = busy; });
    $('iv-refresh').disabled = busy || taskBusy;
    const canAdmin = !!(report && report.access && report.access.can_admin);
    ['iv-benchmark-form', 'iv-settings-form'].forEach(id => {
      $(id).querySelectorAll('input,select,textarea,button').forEach(el => { el.disabled = busy || taskBusy || !canAdmin; });
    });
    if ($('iv-scope').value === 'platform') $('iv-entity').disabled = true;
    if (!$('iv-benchmark-reload').hidden) $('iv-benchmark-form').querySelector('[type=submit]').disabled = true;
    if (!$('iv-settings-reload').hidden) $('iv-settings-form').querySelector('[type=submit]').disabled = true;
  }
  function metric(label, value, help, mode) {
    const button = node('button', undefined, 'panel iv-metric');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(taskMode === mode));
    button.append(node('span', label), node('strong', value), node('small', help));
    button.addEventListener('click', () => { taskMode = mode; renderMetrics(); renderTasks(); $('iv-work-heading').scrollIntoView({block:'nearest'}); });
    $('iv-metrics').appendChild(button);
  }
  function renderMetrics() {
    const totals = report.totals;
    $('iv-metrics').replaceChildren();
    metric('Remaining opportunity', money(totals.opportunity_value), amount(totals.opportunity_count) + ' tasks with potential · not executed value', 'opportunity');
    metric('Gross estimated labor value', money(totals.gross_labor_value), 'Benchmark estimate for audit-backed Lightwork work', 'execution');
    metric('AI and execution costs', money(totals.execution_cost), totals.costs_complete ? 'Recorded costs for this work' : money(totals.known_execution_cost) + ' recorded so far; some costs are unknown', 'cost');
    metric('Net estimated benefit', money(totals.net_benefit), 'Gross estimated labor value minus execution costs', 'net');
    metric('Audit-backed completed work', amount(totals.completed_count), amount(totals.executed_count) + ' tasks with performed activity; completion is a separate state', 'completed');
  }
  function renderTasks() {
    const list = $('iv-tasks');
    list.replaceChildren();
    const rows = report.tasks.filter(task => taskMode === 'all' ||
      (taskMode === 'opportunity' ? number(task.opportunity_value) > 0 || task.state === 'opportunity' :
        taskMode === 'completed' ? task.state === 'completed' : task.state !== 'opportunity'));
    const descriptions = {all:'All contributing work', opportunity:'Opportunity and its contributing work', execution:'Work behind the gross labor estimate', cost:'Work behind recorded or unknown costs', net:'Work behind the net benefit estimate', completed:'Completed work with execution evidence'};
    $('iv-work-heading').textContent = descriptions[taskMode];
    $('iv-work-note').textContent = rows.length + ' tasks. Open a task to see attribution, costs, benchmarks, history and next actions.';
    if (!rows.length) { list.appendChild(node('p', 'No matching work in this period. Choose another period or scope. New authorized work appears as Lightwork observes it.', 'muted iv-empty')); return; }
    rows.forEach(task => {
      const card = node('article', undefined, 'iv-task');
      const body = node('div');
      body.appendChild(node('h3', task.title || task.intent_label || 'Business task'));
      const state = {opportunity:'Opportunity', executed:'Lightwork activity recorded', completed:'Completed with execution evidence'}[task.state] || 'State not yet known';
      body.appendChild(node('p', (task.intent_label || 'Intent uncertain') + ' · ' + state, 'muted'));
      body.appendChild(node('p', task.state === 'opportunity' ? 'Opportunity estimate: ' + money(task.opportunity_value) : 'Gross estimate: ' + money(task.gross_labor_value) + ' · Cost: ' + money(task.execution_cost) + ' · Net estimate: ' + money(task.net_benefit)));
      const button = node('button', 'Inspect work', 'btn');
      button.type = 'button';
      button.setAttribute('aria-label', 'Inspect work: ' + (task.title || task.intent_label || 'Business task'));
      button.addEventListener('click', () => openTask(task.task_id, button));
      card.append(body, button); list.appendChild(card);
    });
  }
  function renderBenchmark(force = false) {
    if (dirty.has('benchmark') && !force) return;
    const selected = $('iv-benchmark-intent').value;
    const row = arr(report.benchmarks).find(item => item.intent_id === selected);
    if (!row) return;
    $('iv-minutes-low').value = row.minutes_low;
    $('iv-minutes').value = row.minutes;
    $('iv-minutes-high').value = row.minutes_high;
    $('iv-rate').value = row.hourly_rate;
    $('iv-benchmark-source').textContent = benchmarkText(row);
    benchmarkRevision = report.revision;
    if (force) { dirty.delete('benchmark'); operations.delete($('iv-benchmark-form')); }
  }
  function renderSettings(force = false) {
    if (!settings || (dirty.has('settings') && !force)) return;
    $('iv-enabled').checked = !!settings.enable;
    $('iv-confirmation').checked = !!settings.confirmation_required;
    settingsRevision = settings.revision;
    if (force) { dirty.delete('settings'); operations.delete($('iv-settings-form')); }
  }
  function renderReport() {
    $('iv-content').hidden = false;
    renderMetrics(); renderTasks(); history($('iv-history'), report.history);
    const access = report.access || {};
    $('iv-period-note').textContent = date(report.period.since) + ' – ' + date(report.period.until) + '. ' + (access.scope === 'workspace' ? 'Authorized workspace totals.' : 'Only work you are authorized to inspect.') + ' Shared work is counted once.';
    const coverageNotes = arr(report.notes).filter(value => typeof value === 'string');
    $('iv-coverage').textContent = coverageNotes.join(' ') || 'These estimates reconcile related tasks and process runs. Do not add them to existing process savings totals.';
    $('iv-benchmark-permission').textContent = access.can_admin ? 'An administrator can adapt these assumptions for this workspace.' : 'View only. A workspace administrator can edit these benchmarks.';
    $('iv-settings-permission').textContent = access.can_admin ? 'Changes apply to value reporting for this workspace.' : 'View only. A workspace administrator can change these settings.';
    fillIntents($('iv-intent-filter'), true);
    const previous = $('iv-benchmark-intent').value;
    $('iv-benchmark-intent').replaceChildren();
    arr(report.benchmarks).forEach(row => option($('iv-benchmark-intent'), row.intent_id, row.label));
    if (Array.from($('iv-benchmark-intent').options).some(o => o.value === previous)) $('iv-benchmark-intent').value = previous;
    renderBenchmark(); renderSettings();
  }
  async function loadReport() {
    if (busy || taskBusy) return;
    if ($('iv-scope').value !== 'platform' && !$('iv-entity').value) { say('iv-status', 'Choose a specialist, agent or process with authorized work.'); return; }
    const generation = ++readGeneration;
    busy = true; lock(); $('iv-content').hidden = true; publish(null);
    say('iv-status', 'Loading the current period and its contributing work…');
    const query = filteredQuery();
    try {
      const [value, configuration] = await Promise.all([request('?' + query), request('/settings')]);
      if (generation !== readGeneration) return;
      report = checkReport(value); settings = configuration; reportQuery = query;
      if ($('iv-scope').value === 'platform' && !$('iv-intent-filter').value) { allGroups = report.groups || {}; allTasks = report.tasks; }
      scopeChoices(); renderReport();
      say('iv-status', 'Updated ' + new Date().toLocaleTimeString() + '. Select any metric to inspect its work.');
    } catch (error) {
      if (generation !== readGeneration) return;
      say('iv-status', error.message, true);
      if ([401,403].includes(error.status)) { report = null; allGroups = {}; allTasks = []; }
    } finally { if (generation === readGeneration) { busy = false; lock(); } }
  }
  function taskPath(id) { return '/tasks/' + encodeURIComponent(id); }
  function taskLock() {
    const allowed = !!(report && report.access && report.access.can_operate);
    ['iv-correction-form', 'iv-continuity-form'].forEach(id => {
      $(id).querySelectorAll('input,select,textarea,button').forEach(el => { el.disabled = taskBusy || !current || !allowed; });
    });
    if (!$('iv-detail-reload').hidden) {
      $('iv-correction-form').querySelector('[type=submit]').disabled = true;
      $('iv-continuity-form').querySelector('[type=submit]').disabled = true;
    }
    $('iv-close').disabled = taskBusy;
    $('iv-detail-reload').disabled = taskBusy;
  }
  function renderTask(task, preserve) {
    $('iv-detail-title').textContent = task.title || task.intent_label || 'Business task';
    $('iv-detail-summary').replaceChildren();
    facts($('iv-detail-summary'), [
      ['Work type', task.intent_label || 'Uncertain'], ['Interpretation', {moderate:'Detected from explicit words; review this interpretation', uncertain:'More than one type may fit', unknown:'Not yet classified', corrected:'Chosen by a person'}[task.confidence] || 'Review this interpretation'],
      ['State', {opportunity:'Observed opportunity; no audited Lightwork execution yet',executed:'Lightwork performed recorded activity',completed:'Completed work with recorded execution'}[task.state] || 'Not yet known'],
      ['Remaining opportunity', money(task.opportunity_value)], ['Gross estimated labor value', money(task.gross_labor_value)],
      ['AI and execution costs', money(task.execution_cost)], ['Net estimated benefit', money(task.net_benefit)]
    ]);
    const evidence = $('iv-evidence'); evidence.replaceChildren();
    const attribution = task.attribution || {};
    const names = rows => arr(rows).map(row => typeof row === 'string' ? row : row.label || row.name).filter(Boolean).join(', ') || 'Not attributed';
    facts(evidence, [['Specialists', names(attribution.specialists)], ['Agents', names(attribution.agents)], ['Processes', names(attribution.processes)]]);
    const records = arr(task.evidence);
    if (!records.length) evidence.appendChild(node('p', 'No authorized audit activity is attached. This remains opportunity, even if a person confirms it is complete.', 'muted'));
    records.forEach(item => {
      const line = node('p', text(item.label) || text(item.description) || 'Lightwork activity recorded');
      if (item.at || item.timestamp) line.appendChild(node('span', ' · ' + date(item.at || item.timestamp), 'muted'));
      evidence.appendChild(line);
    });
    arr(task.costs).forEach(item => evidence.appendChild(node('p', (text(item.label) || text(item.kind) || 'Execution cost') + ': ' + money(item.amount === undefined ? item.cost : item.amount))));
    if (task.execution_cost === null) evidence.appendChild(node('p', 'Cost coverage is incomplete. Unknown costs are not treated as zero; net benefit remains unavailable.', 'muted'));
    $('iv-task-benchmark').replaceChildren(node('p', benchmarkText(task.benchmark)));
    $('iv-task-benchmark').appendChild(node('p', 'The human-effort baseline is credited once for this work. Follow-ups and rework retain continuity. Recorded activity is evidence of work, not proof of cash saved.', 'muted'));
    history($('iv-task-history'), task.history);
    const allowed = !!(report.access && report.access.can_operate);
    $('iv-detail-permission').textContent = allowed ? 'Corrections recalculate estimates and retain their audit history.' : 'View only. You do not have permission to change this work.';
    if (!preserve) {
      $('iv-correction-form').reset(); $('iv-continuity-form').reset();
      fillIntents($('iv-correct-intent'), false);
      $('iv-correct-intent').value = task.intent_id || 'unknown';
      $('iv-size').value = task.size || 'standard';
      $('iv-confirmed').checked = !!task.confirmed;
      $('iv-related-task').replaceChildren(); option($('iv-related-task'), '', 'Choose the original task');
      allTasks.filter(item => item.task_id !== task.task_id).forEach(item => option($('iv-related-task'), item.task_id, item.title || item.intent_label || 'Business task'));
      say('iv-correction-status', ''); say('iv-continuity-status', '');
      operations.delete($('iv-correction-form')); operations.delete($('iv-continuity-form'));
    }
    $('iv-confirmation-field').hidden = !(settings && settings.confirmation_required);
    $('iv-detail-content').hidden = false;
    taskLock(); publish(task);
  }
  function approvedLink(value) {
    if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u001f]/.test(value)) return null;
    const url = new URL(value, window.location.origin);
    if (url.origin !== window.location.origin || !/^\/(?:specialists|specialist|agents|agent|flows|flow|processes|process|chat|automations)(?:\/|$)/.test(url.pathname)) return null;
    return url.pathname + url.search + url.hash;
  }
  async function discovery(task, generation) {
    $('iv-discovery').replaceChildren(node('p', 'Finding relevant authorized work…', 'muted'));
    $('iv-discovery-retry').hidden = true;
    try {
      const value = await request(taskPath(task.task_id) + '/discovery');
      if (generation !== detailGeneration || !dialog.open) return;
      const host = $('iv-discovery'); host.replaceChildren();
      if (!value.can_operate) { host.appendChild(node('p', 'You can inspect the evidence. Starting related work requires permission to operate.', 'muted')); return; }
      const rows = arr(value.recommendations).filter(row => approvedLink(row.url));
      if (!rows.length) host.appendChild(node('p', 'No relevant authorized specialist or process is available for this work yet.', 'muted'));
      rows.forEach(row => {
        const link = node('a', 'Review ' + (row.label || 'recommended work'), 'btn iv-recommendation');
        link.href = approvedLink(row.url); host.appendChild(link);
        if (row.reason) host.appendChild(node('p', text(row.reason), 'muted'));
      });
    } catch (error) {
      if (generation !== detailGeneration || !dialog.open) return;
      $('iv-discovery').replaceChildren(node('p', error.message, 'iv-error'));
      $('iv-discovery-retry').hidden = false;
    }
  }
  async function openTask(id, trigger, preserve = false) {
    if (taskBusy) return;
    const generation = ++detailGeneration;
    current = null; taskRevision = null; publish(null);
    $('iv-detail-content').hidden = true; $('iv-detail-reload').hidden = true;
    say('iv-detail-status', 'Loading the task and its evidence…');
    if (!dialog.open) { opener = trigger; dialog.showModal(); }
    taskLock();
    try {
      const value = await request(taskPath(id) + '?' + reportQuery);
      if (generation !== detailGeneration || !dialog.open) return;
      if (!object(value.task) || !Number.isInteger(value.revision)) throw new Error('The task evidence is incomplete. Close this view and refresh the report.');
      current = value.task; taskRevision = value.revision;
      renderTask(current, preserve); say('iv-detail-status', preserve ? 'Latest task loaded. Your edits are preserved; review the updated evidence before saving.' : 'Evidence loaded for the report period.');
      $('iv-detail-title').focus();
      discovery(current, generation);
    } catch (error) { if (generation === detailGeneration) say('iv-detail-status', error.message, true); }
  }
  async function write(form, path, method, payload, statusId, staleId, after) {
    if (busy || taskBusy) return;
    const fingerprint = JSON.stringify({path, payload});
    let operation = operations.get(form);
    if (!operation || operation.fingerprint !== fingerprint) {
      operation = {fingerprint, id:requestId()}; operations.set(form, operation);
    }
    taskBusy = true; lock(); taskLock(); say(statusId, 'Saving…');
    try {
      await request(path, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify({...payload, operation_id:operation.id})});
      operations.delete(form); say(statusId, 'Saved. Estimates will use the updated record.');
      await after();
    } catch (error) {
      say(statusId, error.message, true);
      if (error.status === 409) $(staleId).hidden = false;
      if ([401,403].includes(error.status)) {
        current = null; report = null; $('iv-content').hidden = true; $('iv-detail-content').hidden = true; publish(null);
        say('iv-detail-status', error.message, true); say('iv-status', error.message, true);
      }
    } finally { taskBusy = false; lock(); taskLock(); }
  }
  $('iv-filters').addEventListener('submit', ev => { ev.preventDefault(); taskMode = 'all'; loadReport(); });
  $('iv-scope').addEventListener('change', scopeChoices);
  $('iv-refresh').addEventListener('click', loadReport);
  $('iv-show-all').addEventListener('click', () => { taskMode = 'all'; renderMetrics(); renderTasks(); });
  $('iv-close').addEventListener('click', () => { if (!taskBusy) dialog.close(); });
  dialog.addEventListener('cancel', ev => { if (taskBusy) ev.preventDefault(); });
  dialog.addEventListener('close', () => { detailGeneration++; current = null; publish(null); if (opener && opener.isConnected) opener.focus(); });
  $('iv-discovery-retry').addEventListener('click', () => { if (current) discovery(current, detailGeneration); });
  $('iv-detail-reload').addEventListener('click', () => { if (current) openTask(current.task_id, opener, true); });
  $('iv-benchmark-form').addEventListener('input', () => dirty.add('benchmark'));
  $('iv-settings-form').addEventListener('input', () => dirty.add('settings'));
  $('iv-benchmark-intent').addEventListener('change', () => { renderBenchmark(true); $('iv-benchmark-reason').value = ''; });
  $('iv-benchmark-reload').addEventListener('click', async () => {
    try {
      const value = checkReport(await request('?' + reportQuery));
      report = value; benchmarkRevision = value.revision;
      const row = arr(value.benchmarks).find(item => item.intent_id === $('iv-benchmark-intent').value);
      $('iv-benchmark-source').textContent = 'Latest benchmark: ' + benchmarkText(row) + ' Your entered numbers are preserved.';
      $('iv-benchmark-reload').hidden = true; operations.delete($('iv-benchmark-form')); lock();
    } catch (error) { say('iv-benchmark-status', error.message, true); }
  });
  $('iv-settings-reload').addEventListener('click', async () => {
    try {
      settings = await request('/settings'); settingsRevision = settings.revision;
      say('iv-settings-status', 'Latest saved settings: reporting ' + (settings.enable ? 'on' : 'off') + ', confirmation ' + (settings.confirmation_required ? 'offered' : 'off') + '. Your choices are preserved; review before saving.');
      $('iv-settings-reload').hidden = true; operations.delete($('iv-settings-form')); lock();
    } catch (error) { say('iv-settings-status', error.message, true); }
  });
  $('iv-benchmark-form').addEventListener('submit', ev => {
    ev.preventDefault(); if (!report || !ev.target.reportValidity()) return;
    const low = Number($('iv-minutes-low').value), minutes = Number($('iv-minutes').value), high = Number($('iv-minutes-high').value);
    if (low > minutes || minutes > high) { say('iv-benchmark-status', 'The low estimate must be no greater than the typical estimate, and the high estimate no smaller.', true); return; }
    write(ev.target, '/benchmarks/' + encodeURIComponent($('iv-benchmark-intent').value), 'PUT', {minutes_low:low, minutes, minutes_high:high, hourly_rate:Number($('iv-rate').value), reason:$('iv-benchmark-reason').value.trim(), expected_revision:benchmarkRevision}, 'iv-benchmark-status', 'iv-benchmark-reload', async () => {
      dirty.delete('benchmark'); taskBusy = false; await loadReport();
    });
  });
  $('iv-settings-form').addEventListener('submit', ev => {
    ev.preventDefault(); if (!settings || !ev.target.reportValidity()) return;
    write(ev.target, '/settings', 'PUT', {enable:$('iv-enabled').checked, confirmation_required:$('iv-confirmation').checked, reason:$('iv-settings-reason').value.trim(), expected_revision:settingsRevision}, 'iv-settings-status', 'iv-settings-reload', async () => {
      dirty.delete('settings'); taskBusy = false; await loadReport();
    });
  });
  $('iv-correction-form').addEventListener('submit', ev => {
    ev.preventDefault(); if (!current || !ev.target.reportValidity()) return;
    const id = current.task_id;
    const payload = {intent_id:$('iv-correct-intent').value, size:$('iv-size').value, reason:$('iv-correct-reason').value.trim(), expected_revision:taskRevision};
    if (settings && settings.confirmation_required) payload.confirmed = $('iv-confirmed').checked;
    write(ev.target, taskPath(id) + '/correction', 'POST', payload, 'iv-correction-status', 'iv-detail-reload', async () => {
      taskBusy = false; await loadReport(); await openTask(id, opener);
    });
  });
  $('iv-continuity-form').addEventListener('submit', ev => {
    ev.preventDefault(); if (!current || !ev.target.reportValidity()) return;
    const target = $('iv-related-task').value;
    write(ev.target, taskPath(current.task_id) + '/continuity', 'POST', {target_task_id:target, reason:$('iv-continuity-reason').value.trim(), expected_revision:taskRevision}, 'iv-continuity-status', 'iv-detail-reload', async () => {
      taskBusy = false; await loadReport(); await openTask(target, opener);
    });
  });
  loadReport();
})();
