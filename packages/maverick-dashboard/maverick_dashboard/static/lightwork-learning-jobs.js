/* Current native learning work remains reachable without starting it again. */
(function () {
  'use strict';
  var root = document.getElementById('learning-job-history'); if (!root) return;
  var choice = document.getElementById('learning-job-choice'), refresh = document.getElementById('learning-job-refresh');
  var status = document.getElementById('learning-job-status'), output = document.getElementById('learning-job-result');
  var stop = document.getElementById('learning-job-stop'), review = document.getElementById('learning-job-review');
  var original = document.getElementById('learning-job-original'), originalStatus = document.getElementById('learning-job-original-status');
  var generation = 0, current = null, busy = false;
  var API = '/api/v1/learning-ops/job-history';
  function node(tag, text, parent) { var element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (parent) parent.append(element); return element; }
  function when(value) { return window.mvWhen ? window.mvWhen(value) : new Date(value * 1000).toLocaleString(); }
  function lock(value) { busy = value; refresh.disabled = value; choice.disabled = value; stop.disabled = value || !current || current.job.state !== 'running' || !current.job.can_stop; original.disabled = value; }
  function selection() { document.dispatchEvent(new CustomEvent('lightwork:copilot-selection', {detail: {selection: current ? {kind: 'learning_job', id: current.job.id} : {}}})); }
  function saved() { if (!current) return null; try { var value = JSON.parse(sessionStorage.getItem('learning-job-stop:' + current.context + ':' + current.job.id) || 'null'); if (value && value.context === current.context && value.job === current.job.id && /^[a-f0-9]{32}$/.test(value.operation)) return value; } catch (_) { /* The authorized native receipt can still be read. */ } return current.original_stop && /^[a-f0-9]{32}$/.test(current.original_stop.operation) ? {context: current.context, job: current.job.id, operation: current.original_stop.operation} : null; }
  function save(row) {
    sessionStorage.setItem('learning-job-stop:' + row.context + ':' + row.job, JSON.stringify(row));
    var keys = Object.keys(sessionStorage).filter(function (key) { return key.startsWith('learning-job-stop:'); });
    keys.slice(0, Math.max(0, keys.length - 40)).forEach(function (key) { sessionStorage.removeItem(key); });
  }
  async function request(path, body) {
    var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, 20000);
    try {
      var response = await fetch(API + path, {method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined, signal: controller.signal});
      var value = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'The current result could not be read. No work was repeated.');
      return value;
    } finally { clearTimeout(timer); }
  }
  function show(row) {
    current = row; output.replaceChildren();
    var heading = node('h3', row.job.label, output); heading.style.overflowWrap = 'anywhere';
    var details = node('dl', undefined, output);
    [['Started', when(row.job.started_at)], ['Current recorded status', row.status]].forEach(function (item) { node('dt', item[0], details); node('dd', item[1], details); });
    if (row.job.finished_at) { node('dt', 'Finished', details); node('dd', when(row.job.finished_at), details); }
    if (row.job.problem) node('p', row.job.problem, output);
    (row.observations || []).forEach(function (report) {
      node('h4', report.label, output); var counts = node('dl', undefined, output);
      Object.entries(report.counts).forEach(function (item) { node('dt', item[0], counts); node('dd', String(item[1]), counts); });
    });
    node('p', row.notice, output); status.textContent = 'Current retained result read. No task was started or repeated.';
    original.hidden = !saved(); selection(); lock(false);
  }
  async function readSelected() {
    if (busy || !choice.value) return;
    var id = choice.value, started = ++generation; review.replaceChildren(); originalStatus.textContent = ''; lock(true);
    try { var row = await request('/' + encodeURIComponent(id)); if (started === generation) show(row); }
    catch (error) { if (started === generation) { current = null; output.replaceChildren(); status.textContent = error.message; original.hidden = true; selection(); } }
    finally { if (started === generation) lock(false); }
  }
  async function load() {
    if (busy) return; var selected = choice.value, started = ++generation; review.replaceChildren(); current = null; selection(); lock(true);
    try {
      var history = await request(''); if (started !== generation) return;
      choice.replaceChildren(); var empty = node('option', 'Choose a retained learning task', choice); empty.value = '';
      history.jobs.forEach(function (row) { var option = node('option', row.job.label + ' / ' + when(row.job.started_at) + ' / ' + row.status, choice); option.value = row.job.id; });
      choice.value = history.jobs.some(function (row) { return row.job.id === selected; }) ? selected : '';
      output.replaceChildren(); status.textContent = history.notice; original.hidden = true;
      if (choice.value) show(history.jobs.find(function (row) { return row.job.id === choice.value; }));
    } catch (error) { if (started === generation) { output.replaceChildren(); status.textContent = error.message; original.hidden = true; } }
    finally { if (started === generation) lock(false); }
  }
  refresh.addEventListener('click', load); choice.addEventListener('change', readSelected);
  stop.addEventListener('click', async function () {
    if (busy || !current || !current.job.can_stop) return;
    var started = ++generation, id = current.job.id; lock(true); review.replaceChildren();
    try {
      var reviewed = await request('/' + encodeURIComponent(id)); if (started !== generation) return;
      show(reviewed); if (!reviewed.job.can_stop || reviewed.job.state !== 'running') return;
      node('h3', 'Review this stop request', review);
      node('p', reviewed.job.label + ' / ' + when(reviewed.job.started_at) + ' / ' + reviewed.status, review);
      node('p', 'Request a stop at this task\'s next safe point. Completed work remains. This does not undo earlier changes or prove the task has stopped.', review);
      var label = node('label', undefined, review), consent = node('input', undefined, label); consent.type = 'checkbox'; label.append(document.createTextNode(' I reviewed this exact task and want to request a stop.'));
      var apply = node('button', 'Confirm reviewed stop', review); apply.type = 'button'; apply.className = 'btn'; apply.disabled = true;
      consent.addEventListener('change', function () { apply.disabled = busy || !consent.checked; });
      apply.addEventListener('click', async function () {
        if (busy || !consent.checked || started !== generation || !current || current.job.id !== id) return;
        var operation = crypto.randomUUID().replace(/-/g, ''), body = {context: reviewed.context, revision: reviewed.revision, operation: operation, confirm: true};
        try { save({context: reviewed.context, job: id, operation: operation}); }
        catch (_) { originalStatus.textContent = 'The original request could not be saved in this browser. Nothing was submitted. Allow site storage before continuing.'; return; }
        lock(true); apply.disabled = true; original.hidden = false;
        try { var result = await request('/' + encodeURIComponent(id) + '/stop', body); if (started === generation) { show(result.current); review.replaceChildren(); originalStatus.textContent = result.receipt.message; } }
        catch (error) { if (started === generation) { review.replaceChildren(); originalStatus.textContent = 'The original stop reply is unconfirmed. Check its original outcome; no stop or work will be repeated automatically. ' + (error.name === 'AbortError' ? '' : error.message); } }
        finally { if (started === generation) lock(false); }
      });
      var cancel = node('button', 'Cancel this review', review); cancel.type = 'button'; cancel.className = 'btn btn--ghost'; cancel.addEventListener('click', function () { if (!busy) review.replaceChildren(); });
      consent.focus();
    } catch (error) { if (started === generation) status.textContent = error.message; }
    finally { if (started === generation) lock(false); }
  });
  original.addEventListener('click', async function () {
    var row = saved(); if (busy || !row) return; var started = ++generation; review.replaceChildren(); lock(true);
    try { var result = await request('/' + encodeURIComponent(row.job) + '/stop/' + encodeURIComponent(row.operation)); if (started === generation) { show(result.current); originalStatus.textContent = result.message; } }
    catch (error) { if (started === generation) originalStatus.textContent = error.message + ' Nothing was repeated.'; }
    finally { if (started === generation) lock(false); }
  });
  function changed() { generation++; current = null; review.replaceChildren(); output.replaceChildren(); choice.replaceChildren(); original.hidden = true; originalStatus.textContent = ''; status.textContent = 'Your account, workspace or access changed. Refresh retained tasks before continuing.'; lock(false); }
  document.addEventListener('lightwork:workspace-changed', changed);
  document.addEventListener('lightwork:account-context-changed', changed);
  window.addEventListener('lightwork:context-changed', changed);
  load();
}());
