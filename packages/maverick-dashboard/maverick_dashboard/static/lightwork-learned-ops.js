// Learned page: more self-coaching tools -- clash check by meaning, trying
// proven lessons on other models, test cases (find, download, add) and the
// one-time hold-out record. Global administrators only.
(function () {
  'use strict';
  var root = document.getElementById('lpo-root');
  if (!root || !window.LWJobs) return;
  var J = window.LWJobs;
  var el = J.el, plural = J.plural;
  var API = '/api/v1/learning-ops/self-harness';
  function $(id) { return document.getElementById(id); }
  function say(id, text) { window.mvStatus($(id), text || ''); }
  function fill(id, nodes) {
    var box = $(id);
    box.textContent = '';
    (Array.isArray(nodes) ? nodes : [nodes]).forEach(function (n) { if (n) box.appendChild(n); });
  }
  function addOptions(select, models) {
    models.forEach(function (m) { var o = el('option', null, m.label); o.value = m.value; select.appendChild(o); });
  }

  function loadPlan() {
    J.request(API + '/plan').then(function (d) {
      var parts = [d.enabled ? 'Self-coaching is on.' : 'Self-coaching is off. Some tools below need it on: turn on ' + d.places.self_coaching + '.'];
      parts.push(d.test_cases_set_up ? '' : 'Test cases are not set up yet. Use Self-coaching questions below to choose Lightwork’s managed location.');
      parts.push(d.spending_limit != null ? 'Testing can spend up to ' + J.money(d.spending_limit) + ' per run.' : '');
      say('lpo-plan', parts.filter(Boolean).join(' '));
      addOptions($('lpo-clash-model'), d.models);
    }).catch(async function (failure) {
      say('lpo-plan', await J.problem(failure, 'load the self-coaching tools'));
    });
  }

  // The native request is retained BEFORE dispatch. Recovery only reads it.
  (function meaningCheck() {
    var context = '', pending = null, busy = false, generation = 0, poll = null;
    var run = $('lpo-clash-run'), model = $('lpo-clash-model'), review = $('lpo-clash-review');
    var choice = $('lpo-clash-original'), read = $('lpo-clash-recover'), refresh = $('lpo-clash-refresh');
    var key = 'lightwork-meaning-check';
    var initial = new URL(location.href).searchParams.get('meaning_check');
    if (!/^[a-f0-9]{32}$/.test(initial || '')) initial = '';
    function lock(value) { busy = value; run.disabled = value || !context; model.disabled = value; refresh.disabled = value; choice.disabled = value; read.disabled = value || !choice.value; }
    function forget() { generation++; clearTimeout(poll); context = ''; pending = null; review.replaceChildren(); fill('lpo-clash-out', []); choice.replaceChildren(el('option', null, 'Choose your original check')); say('lpo-clash-status', 'Your account or access changed. Refresh the current controls before continuing.'); lock(false); }
    function save(value) { sessionStorage.setItem(key, JSON.stringify(value)); pending = value; }
    function saved() { try { var value = JSON.parse(sessionStorage.getItem(key) || 'null'); return value && value.context === context && /^[a-f0-9]{32}$/.test(value.operation) ? value : null; } catch (_) { return null; } }
    async function request(path, body) {
      var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, 20000);
      try {
        var response = await fetch(API + '/clash-check' + path, {method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined, signal: controller.signal});
        var text = await response.text(); if (text.length > 262144) throw new Error('This complete response is too large to review. No work was repeated.');
        var value; try { value = JSON.parse(text); } catch (_) { value = {}; }
        if (!response.ok || response.redirected) { var error = new Error(typeof value.detail === 'string' ? value.detail : 'The current check could not be read. Nothing was repeated.'); error.authority = response.redirected || response.status === 401 || response.status === 403; throw error; }
        if (context && value.context && value.context !== context) { var changed = new Error('Your account or workspace changed.'); changed.authority = true; throw changed; }
        return value;
      } finally { clearTimeout(timer); }
    }
    function failure(error) { if (error.authority) forget(); else say('lpo-clash-status', error.name === 'AbortError' ? 'The reply was interrupted. Read the original result; no AI work will be repeated automatically.' : error.message); }
    function present(result) {
      if (result.context !== context) { forget(); return; }
      fill('lpo-clash-out', []); var output = $('lpo-clash-out'); output.append(el('p', null, result.message));
      if (result.state) output.append(el('p', null, 'Recorded progress: ' + ({admitted: 'Waiting to start', running: 'In progress', stopping: 'Stop requested', done: 'Finished', stopped: 'Stopped', failed: 'Could not finish', not_started: 'Not started', unconfirmed: 'Latest observation is unconfirmed'}[result.state] || 'Unavailable')));
      if (result.problem) output.append(el('p', null, result.problem));
      if (result.checked !== undefined) {
        output.append(el('p', null, result.checked + ' AI judgments received; ' + result.unknown + ' unavailable or not completed. ' + result.flagged + ' possible conflicts flagged by AI; ' + result.wording_flags + ' wording flags without an AI judgment. Recorded AI cost: ' + J.money(result.spent) + '.'));
        (result.pairs || []).forEach(function (pair) { var section = el('section'); section.append(el('h4', null, pair.area), el('p', null, pair.first), el('p', null, pair.second), el('p', 'muted', pair.basis)); output.append(section); });
        if (!result.flagged && !result.wording_flags) output.append(el('p', null, 'No possible conflicts were flagged among the completed comparisons. Unavailable judgments remain unknown.'));
      }
      if (result.task_href) { var link = el('a', null, 'Open progress and stop controls'); link.href = result.task_href; output.append(link); }
      output.append(el('p', 'muted', result.notice)); say('lpo-clash-status', result.active ? 'The original check is running. Read its latest progress without starting another.' : 'Original result read. No work was repeated.');
      clearTimeout(poll); if (result.active) { var current = generation; poll = setTimeout(function () { if (current === generation && !busy && choice.value === result.operation) readOriginal(); }, 1500); }
    }
    async function load() {
      if (busy) return; var current = ++generation, selected = choice.value || initial; initial = ''; clearTimeout(poll); lock(true);
      try {
        var state = await request(''); if (current !== generation) return;
        context = state.context; pending = saved(); selected = selected || (pending && pending.operation) || '';
        choice.replaceChildren(); var empty = el('option', null, 'Choose your original check'); empty.value = ''; choice.append(empty);
        state.requests.forEach(function (row) { var option = el('option', null, row.label); option.value = row.value; choice.append(option); });
        if (selected && !state.requests.some(function (row) { return row.value === selected; })) { var original = el('option', null, 'Original request saved in this browser'); original.value = selected; choice.append(original); }
        choice.value = selected; say('lpo-clash-status', state.notice); review.replaceChildren();
      } catch (error) { if (current === generation) failure(error); }
      finally { if (current === generation) lock(false); }
    }
    async function readOriginal() {
      if (busy || !context || !choice.value) return; var current = ++generation, operation = choice.value; clearTimeout(poll); lock(true); review.replaceChildren();
      try { var result = await request('/requests/' + encodeURIComponent(operation)); if (current === generation) present(result); }
      catch (error) { if (current === generation) failure(error); }
      finally { if (current === generation) lock(false); }
    }
    run.addEventListener('click', async function () {
      if (busy || !context) return; var current = ++generation; clearTimeout(poll); lock(true); review.replaceChildren();
      try {
        var reviewed = await request('/review', {context: context, model_id: model.value || null}); if (current !== generation) return;
        review.append(el('h4', null, 'Review this exact meaning check'));
        reviewed.changes.forEach(function (change) { review.append(el('h5', null, change.label)); if (Array.isArray(change.after)) { var list = el('ul'); change.after.forEach(function (line) { list.append(el('li', null, line)); }); review.append(list); } else review.append(el('p', null, change.after)); });
        var label = el('label'), consent = el('input'); consent.type = 'checkbox'; label.append(consent, document.createTextNode(' I reviewed all listed lessons and spending settings.')); review.append(label);
        var apply = el('button', 'btn', 'Start reviewed meaning check'); apply.type = 'button'; apply.disabled = true; review.append(apply);
        consent.addEventListener('change', function () { apply.disabled = busy || !consent.checked; });
        apply.addEventListener('click', async function () {
          if (busy || !consent.checked || current !== generation) return;
          try { save({context: context, operation: reviewed.operation}); } catch (_) { say('lpo-clash-status', 'This browser could not retain the original request. Allow site storage before starting; no AI call was made.'); return; }
          var option = el('option', null, 'Original request just reviewed'); option.value = reviewed.operation; choice.append(option); choice.value = reviewed.operation;
          lock(true); apply.disabled = true; say('lpo-clash-status', 'Submitting this reviewed original request…');
          try { var result = await request('', {context: context, token: reviewed.token, confirmed: true}); if (current === generation) { review.replaceChildren(); present(result); } }
          catch (error) { if (current === generation) { review.replaceChildren(); failure(error); } }
          finally { if (current === generation) lock(false); }
        });
        var cancel = el('button', 'btn btn--ghost', 'Cancel this review'); cancel.type = 'button'; cancel.addEventListener('click', function () { if (!busy) review.replaceChildren(); }); review.append(cancel); consent.focus();
        say('lpo-clash-status', 'No AI call has been made. Read every listed lesson before confirming.');
      } catch (error) { if (current === generation) failure(error); }
      finally { if (current === generation) lock(false); }
    });
    model.addEventListener('change', function () { generation++; clearTimeout(poll); review.replaceChildren(); lock(false); say('lpo-clash-status', 'Selection changed. Review the current lessons before starting. Your original request remains available.'); });
    choice.addEventListener('change', readOriginal); read.addEventListener('click', readOriginal); refresh.addEventListener('click', load);
    document.addEventListener('lightwork:copilot-context-changed', function (event) { if (event.detail && event.detail.reason === 'selection') { if (!busy) load(); } else { forget(); load(); } });
    window.addEventListener('lightwork:context-changed', function () { forget(); load(); });
    lock(false); load().then(function () { if (choice.value && context && !busy) readOriginal(); });
  })();

  var harvest = $('lpo-harvest');
  harvest.addEventListener('click', async function () {
    var mode = $('lpo-harvest-mode').value;
    if (mode === 'auto' && !(await window.mvConfirm('Add new test cases from past work straight away, without reviewing them first? They are used to judge future lessons.',
      { okText: 'Add them' }))) return;
    harvest.disabled = true;
    say('lpo-cases-status', 'Looking through past work…');
    try {
      var d = await J.request(API + '/test-cases/harvest', { method: 'POST', json: { mode: mode } });
      say('lpo-cases-status', !d.found ? 'No new test cases were found.' : (mode === 'auto'
        ? 'Added ' + plural(d.found, 'test case') + '.'
        : 'Found ' + plural(d.found, 'test case') + '. Review them in the lessons list above (refresh the page).'));
    } catch (failure) {
      say('lpo-cases-status', await J.problem(failure, 'find new test cases'));
    } finally { harvest.disabled = false; }
  });

  var exportBtn = $('lpo-export');
  exportBtn.addEventListener('click', async function () {
    exportBtn.disabled = true;
    say('lpo-cases-status', 'Preparing the download…');
    try {
      var r = await J.request(API + '/test-cases/export', { raw: true });
      J.saveBlob(await r.blob(), 'Lightwork-test-cases.json');
      say('lpo-cases-status', 'Downloaded ' + plural(Number(r.headers.get('X-Lightwork-Cases') || 0), 'test case') + '.');
    } catch (failure) {
      say('lpo-cases-status', await J.problem(failure, 'download the test cases'));
    } finally { exportBtn.disabled = false; }
  });

  var check = $('lpo-import-check');
  check.addEventListener('click', async function () {
    var input = $('lpo-import-file');
    var file = input.files && input.files[0];
    if (!file) { say('lpo-cases-status', 'Choose a test-case file first.'); return; }
    var replace = $('lpo-import-how').value === 'replace';
    var body = await J.readFile(file);
    check.disabled = true;
    $('lpo-cases-out').textContent = '';
    say('lpo-cases-status', 'Checking the file…');
    try {
      var d = await J.request(API + '/test-cases/import/preview?replace=' + replace, { method: 'POST', body: body, type: 'application/json' });
      say('lpo-cases-status', replace
        ? 'Replacing would change the test cases from ' + d.total_now + ' to ' + d.total_after + '.'
        : 'This would add ' + plural(d.added, 'new test case') + '. Existing ones stay as they are.');
      var nodes = [J.table(['Model', 'Test cases now', 'After'], d.keys.map(function (k) { return [k.label, String(k.now), String(k.after)]; }))];
      var apply = el('button', replace ? 'btn btn--danger' : 'btn btn--primary', replace ? 'Replace the test cases' : 'Add these test cases');
      apply.type = 'button';
      apply.addEventListener('click', async function () {
        var msg = replace
          ? 'Replace all ' + d.total_now + ' test cases with the ' + d.total_after + ' in this file? Test cases not in the file are removed.'
          : 'Add ' + plural(d.added, 'new test case') + ' from this file?';
        if (!(await window.mvConfirm(msg, { okText: replace ? 'Replace' : 'Add' }))) return;
        apply.disabled = true;
        try {
          var r = await J.request(API + '/test-cases/import?confirm=true&replace=' + replace, { method: 'POST', body: body, type: 'application/json' });
          $('lpo-cases-out').textContent = '';
          say('lpo-cases-status', replace ? 'Replaced. There are now ' + plural(r.cases, 'test case') + '.' : 'Added ' + plural(r.cases, 'test case') + '.');
        } catch (failure) {
          say('lpo-cases-status', await J.problem(failure, 'add the test cases'));
          apply.disabled = false;
        }
      });
      nodes.push(apply);
      fill('lpo-cases-out', nodes);
    } catch (failure) {
      say('lpo-cases-status', await J.problem(failure, 'check the file'));
    } finally { check.disabled = false; }
  });

  loadPlan();
})();
