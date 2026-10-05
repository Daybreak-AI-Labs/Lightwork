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
  function say(id, text) { $(id).textContent = text || ''; }
  function fill(id, nodes) {
    var box = $(id);
    box.textContent = '';
    (Array.isArray(nodes) ? nodes : [nodes]).forEach(function (n) { if (n) box.appendChild(n); });
  }
  function addOptions(select, models) {
    models.forEach(function (m) { var o = el('option', null, m.label); o.value = m.value; select.appendChild(o); });
  }

  function settingsLink() {
    var a = el('a', 'btn btn--ghost', 'Open All settings');
    a.href = '/settings/all';
    return a;
  }

  function holdout(plan) {
    var box = $('lpo-holdout');
    box.textContent = '';
    if (!plan.hold_out.set_up) {
      box.appendChild(el('p', 'muted', 'Choose where to keep it first, in ' + plan.places.hold_out + '.'));
      box.appendChild(settingsLink());
      return;
    }
    if (plan.hold_out.exists) {
      box.appendChild(el('p', 'muted', 'The hold-out record is set up. Check it with “Check the hold-out record” above.'));
      return;
    }
    var btn = el('button', 'btn btn--primary', 'Set up the hold-out record');
    btn.type = 'button';
    btn.addEventListener('click', async function () {
      if (!(await window.mvConfirm('Set up the hold-out record now? This can only be done once; it is never replaced afterwards.',
        { okText: 'Set it up' }))) return;
      btn.disabled = true;
      try {
        var d = await J.request(API + '/hold-out/set-up', { method: 'POST', json: { confirm: true } });
        box.textContent = '';
        box.appendChild(el('p', null, 'The hold-out record is set up. Write down its record number and keep it somewhere separate from this server, so anyone can later confirm the record was never swapped:'));
        box.appendChild(el('p', 'lpo-number', d.record_number));
        say('lpo-holdout-status', '');
      } catch (failure) {
        say('lpo-holdout-status', await J.problem(failure, 'set up the hold-out record'));
        btn.disabled = false;
      }
    });
    box.appendChild(btn);
  }

  function loadPlan() {
    J.request(API + '/plan').then(function (d) {
      var parts = [d.enabled ? 'Self-coaching is on.' : 'Self-coaching is off. Some tools below need it on: turn on ' + d.places.self_coaching + '.'];
      parts.push(d.test_cases_set_up ? '' : 'Test cases are not set up yet; choose where to keep them in ' + d.places.test_cases + '.');
      parts.push(d.spending_limit != null ? 'Testing can spend up to ' + J.money(d.spending_limit) + ' per run.' : '');
      say('lpo-plan', parts.filter(Boolean).join(' '));
      addOptions($('lpo-clash-model'), d.models);
      addOptions($('lpo-transfer-source'), d.models);
      addOptions($('lpo-transfer-target'), d.models);
      holdout(d);
    }).catch(async function (failure) {
      say('lpo-plan', await J.problem(failure, 'load the self-coaching tools'));
    });
  }

  J.wire($('lpo-clash-run'), async function () {
    if (!(await window.mvConfirm('Ask the answer checker to compare the lessons by meaning? It uses a little of the spending limit. You can stop it; any pairs left are then checked by wording only.',
      { okText: 'Check by meaning' }))) return null;
    return { url: API + '/clash-check', init: { method: 'POST', json: { model_id: $('lpo-clash-model').value || null } } };
  }, { status: $('lpo-clash-status'), out: $('lpo-clash-out'), action: 'check the lessons', render: function (d) {
    var head = el('p', null, (d.pairs.length ? plural(d.pairs.length, 'pair') + ' of lessons may contradict each other.'
      : 'No lessons contradict each other.') + ' Spent ' + J.money(d.spent) + '.');
    if (!d.pairs.length) return head;
    var ul = el('ul');
    d.pairs.forEach(function (p) { ul.appendChild(el('li', null, '“' + p.first + '” and “' + p.second + '”')); });
    return [head, ul, el('p', 'muted', 'Remove whichever lesson is wrong from the lessons list above.')];
  } });

  J.wire($('lpo-transfer-run'), async function () {
    var source = $('lpo-transfer-source').value;
    if (!source) { say('lpo-transfer-status', 'There are no models with lessons yet.'); return null; }
    if (!(await window.mvConfirm('Test lessons proven for this model on ' + ($('lpo-transfer-target').value ? 'the chosen model' : 'every other model') +
      '? Testing uses the evaluation spending limit. Lessons that pass start on trial.', { okText: 'Try the lessons' }))) return null;
    return { url: API + '/transfer', init: { method: 'POST', json: { source: source,
      target: $('lpo-transfer-target').value || null, retry_judged: $('lpo-transfer-retry').checked } } };
  }, { status: $('lpo-transfer-status'), out: $('lpo-transfer-out'), action: 'try the lessons', render: function (d) {
    if (!d.targets.length) return el('p', 'muted', 'There were no lessons to try.');
    return d.targets.map(function (t) {
      var p = el('p', null, (window.mvHuman ? window.mvHuman(t.model) : t.model) + ': tried ' + plural(t.tried, 'lesson') + ', ' +
        plural(t.on_trial.length, 'lesson') + ' now on trial.');
      return p;
    });
  } });

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
