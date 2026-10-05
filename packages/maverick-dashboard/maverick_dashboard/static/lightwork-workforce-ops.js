// Workforce page: what each specialist can do and what stops it, a check of
// every specialist's setup (a background job) and the specialist test suite.
// Tasks page: tasks coming due. Plain words only.
(function () {
  'use strict';
  if (!window.LWJobs || window.LWWorkforceOps) return;
  window.LWWorkforceOps = true;
  var J = window.LWJobs;
  var el = J.el, plural = J.plural;
  // The roster mixes Lightwork's built-in specialists with agents built here.
  function members(n) { return plural(n, 'agent or specialist', 'agents and specialists'); }
  var API = '/api/v1/workforce-ops';
  function $(id) { return document.getElementById(id); }
  function fill(id, nodes) {
    var box = $(id);
    if (!box) return;
    box.textContent = '';
    (Array.isArray(nodes) ? nodes : [nodes]).forEach(function (n) { if (n) box.appendChild(n); });
  }
  var RUNGS = { observe: 'Watch only', suggest: 'Suggest', request: 'Ask before acting', auto: 'Act on their own' };
  var testAdmin = false;
  var testCases = [];
  var testLiveEnabled = false;
  var editingCase = null;
  function runStorageKey(specialist) { return 'lightwork-specialist-answer-run:' + specialist; }
  function pendingRun(specialist) {
    try { return window.sessionStorage.getItem(runStorageKey(specialist)); } catch (error) { return null; }
  }
  function pendingRunCases(specialist) {
    try { return JSON.parse(window.sessionStorage.getItem(runStorageKey(specialist) + ':cases') || '[]'); }
    catch (error) { return []; }
  }
  function clearPendingRun(specialist) {
    try {
      window.sessionStorage.removeItem(runStorageKey(specialist));
      window.sessionStorage.removeItem(runStorageKey(specialist) + ':cases');
    } catch (error) { /* no browser storage */ }
  }
  function newRunId() {
    if (!window.crypto || !window.crypto.getRandomValues || !window.sessionStorage) return null;
    var bytes = new Uint8Array(16); window.crypto.getRandomValues(bytes);
    return Array.from(bytes, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
  }

  function testPhrases(id) {
    return $(id).value.split(/\r?\n/).map(function (x) { return x.trim(); }).filter(Boolean);
  }
  function testCriteria(c) {
    var parts = [];
    if (c.includes.length) parts.push('Must mention: ' + c.includes.join(', '));
    if (c.excludes.length) parts.push('Must never say: ' + c.excludes.join(', '));
    if (c.refusal) parts.push('Must refuse');
    if (c.citation) parts.push('Must reference a source');
    return parts.join(' · ');
  }
  function clearTestEditor() {
    editingCase = null;
    $('wo-test-form').reset();
    $('wo-test-form-title').textContent = 'Add an answer check';
    $('wo-test-cancel').hidden = true;
  }
  function editTest(c) {
    editingCase = c;
    $('wo-test-form-title').textContent = 'Edit answer check';
    $('wo-test-task').value = c.task;
    $('wo-test-includes').value = c.includes.join('\n');
    $('wo-test-excludes').value = c.excludes.join('\n');
    $('wo-test-refusal').checked = c.refusal;
    $('wo-test-citation').checked = c.citation;
    $('wo-test-cancel').hidden = false;
    $('wo-test-task').focus();
  }
  function renderTests(data) {
    testCases = data.cases || [];
    testLiveEnabled = !!data.live_enabled;
    var list = [];
    if (!testCases.length) list.push(el('p', 'muted', 'No answer checks yet. Add one below.'));
    else {
      list.push(el('p', null, plural(testCases.length, 'saved check') + ' for this one. Choose up to 5 for the next run.'));
      var rows = testCases.map(function (c) {
        var choose = el('input'); choose.type = 'checkbox'; choose.className = 'wo-test-choice';
        choose.value = c.id; choose.checked = testCases.length <= 5;
        choose.setAttribute('aria-label', 'Include this answer check in the next run');
        choose.addEventListener('change', updateRunAvailability);
        var actions = el('span');
        if (testAdmin) {
          var edit = el('button', 'btn btn--ghost', 'Edit'); edit.type = 'button';
          edit.addEventListener('click', function () { editTest(c); });
          var remove = el('button', 'btn btn--ghost', 'Remove'); remove.type = 'button';
          remove.addEventListener('click', async function () {
            if (!(await window.mvConfirm('Remove this answer check?', {okText: 'Remove check'}))) return;
            remove.disabled = true;
            try {
              await J.request(API + '/test-suite/customer/remove', {method: 'POST', json: {
                specialist: c.specialist, identity: c.id, expected_revision: c.revision
              }});
              clearTestEditor(); await loadCustomerTests();
            } catch (failure) { $('wo-test-status').textContent = await J.problem(failure, 'remove the check'); }
            finally { remove.disabled = false; }
          });
          actions.appendChild(edit); actions.appendChild(document.createTextNode(' ')); actions.appendChild(remove);
        }
        return [choose, c.task, testCriteria(c), actions];
      });
      list.push(J.table(['Choose', 'Question', 'A passing answer', ''], rows));
    }
    fill('wo-tests-list', list);
    document.querySelectorAll('#wo-my-tests .wo-test-admin').forEach(function (n) { n.hidden = !testAdmin; });
    var run = $('wo-test-run');
    updateRunAvailability();
    var pending = pendingRun($('wo-test-specialist').value);
    run.textContent = pending ? 'Check original result' : 'Check answers';
    $('wo-test-new-run').hidden = !testAdmin || !pending;
    if (!data.live_enabled) {
      var p = el('p', 'muted', 'Live answer checks are off. An administrator can enable bounded model tests in All settings.');
      var link = el('a', null, 'Open All settings'); link.href = '/settings/all'; p.appendChild(document.createTextNode(' ')); p.appendChild(link);
      list.push(p); fill('wo-tests-list', list);
    }
    var history = data.runs || [];
    if (!history.length) { fill('wo-test-history', el('p', 'muted', 'No live checks have been run yet.')); return; }
    fill('wo-test-history', history.map(function (r) {
      var box = el('details');
      var state = r.state === 'done' ? (r.passed + ' of ' + r.total + ' passed')
        : r.state === 'stopped' ? 'Stopped after ' + r.results.length + ' answers'
          : r.state === 'prepared' ? 'Not started' : 'Result uncertain after ' + r.results.length + ' answers';
      box.appendChild(el('summary', null, J.when(r.started_at) + ' · ' + state));
      if (r.message) box.appendChild(el('p', null, r.message));
      box.appendChild(el('p', 'muted', 'Model: ' + r.model + '. Recorded cost: ' + J.money(r.spent_dollars) +
        '. Each check used the instructions shown by this version; no actions or company knowledge were used.'));
      if (r.results.length) box.appendChild(J.table(['Question', 'Answer', 'Result'], r.results.map(function (answer) {
        return [answer.task, answer.answer, answer.passed ? 'Passed' : 'Needs review'];
      })));
      return box;
    }));
  }
  async function loadCustomerTests() {
    var specialist = $('wo-test-specialist').value;
    clearTestEditor();
    if (!specialist) {
      fill('wo-tests-list', el('p', 'muted', 'Choose an agent or specialist to see its answer checks.'));
      fill('wo-test-history', null);
      document.querySelectorAll('#wo-my-tests .wo-test-admin').forEach(function (n) { n.hidden = true; });
      return;
    }
    $('wo-test-status').textContent = 'Loading answer checks…';
    try {
      var data = await J.request(API + '/test-suite/customer?specialist=' + encodeURIComponent(specialist));
      $('wo-test-status').textContent = '';
      renderTests(data);
    } catch (failure) { $('wo-test-status').textContent = await J.problem(failure, 'load the answer checks'); }
  }
  function selectedTestIds() {
    return Array.from(document.querySelectorAll('#wo-tests-list .wo-test-choice:checked'), function (box) { return box.value; });
  }
  function updateRunAvailability() {
    var count = selectedTestIds().length;
    $('wo-test-run').disabled = !testLiveEnabled || !count || count > 5;
    if (testCases.length > 5) $('wo-test-status').textContent = count > 5
      ? 'Choose no more than 5 checks for one run.' : count ? '' : 'Choose up to 5 checks to run.';
  }
  async function loadTestSpecialists() {
    try {
      var data = await J.request(API + '/test-suite/specialists');
      var select = $('wo-test-specialist');
      (data.specialists || []).forEach(function (s) {
        var option = el('option', null, s.label); option.value = s.value; select.appendChild(option);
      });
      var requested = new URLSearchParams(location.search).get('specialist');
      if (requested && (data.specialists || []).some(function (s) { return s.value === requested; })) select.value = requested;
      await loadCustomerTests();
    } catch (failure) { $('wo-test-status').textContent = await J.problem(failure, 'list the agents and specialists'); }
  }

  // ---- what each specialist can do -------------------------------------------------
  var suitesLoaded = false;
  function loadInventory() {
    var suite = $('wo-inv-suite').value;
    fill('wo-inv', el('p', 'muted', 'Loading…'));
    J.request(API + '/inventory' + (suite ? '?suite=' + encodeURIComponent(suite) : '')).then(function (d) {
      if (!suitesLoaded) {
        d.suites.forEach(function (s) { var o = el('option', null, s.label); o.value = s.value; $('wo-inv-suite').appendChild(o); });
        suitesLoaded = true;
      }
      var facts = [
        members(d.specialists) + ' in ' + plural(d.departments, 'department') + ', including ' + plural(d.builders, 'builder') + '.',
        d.can_change_things_while_drafting
          ? plural(d.can_change_things_while_drafting, 'agent or specialist', 'agents and specialists') + ' that should only draft can reach tools that change things. Review these below.'
          : 'No agent or specialist that only drafts can reach tools that change things.',
        members(d.sign_off_enforced) + ' must have a person sign off their work' +
          (d.sign_off_only_asked ? '; ' + plural(d.sign_off_only_asked, 'more') + ' only ask for it in their instructions.' : '.'),
        members(d.extra_refusals) + ' refuse extra kinds of requests for their field; ' +
          members(d.with_deliverable) + ' produce a defined piece of work.',
        d.levels_enabled === false
          ? 'Independence levels are turned off, so every agent and specialist follows the same approval rules.'
          : 'Independence: ' + Object.keys(RUNGS).map(function (r) { return RUNGS[r] + ' ' + ((d.independence || {})[r] || 0); }).join(', ') +
            '. ' + members(d.still_supervised) + ' still supervised while new.'
      ];
      var nodes = [el('ul')];
      facts.forEach(function (f) { nodes[0].appendChild(el('li', null, f)); });
      if (d.flagged.length) {
        nodes.push(el('h3', null, 'Agents and specialists to review'));
        nodes.push(J.table(['Name', 'Can reach'], d.flagged.map(function (f) {
          return [f.specialist, f.can_reach.map(window.mvHuman || String).join(', ')];
        })));
      }
      fill('wo-inv', nodes);
    }).catch(async function (failure) {
      fill('wo-inv', el('p', null, await J.problem(failure, 'load the list of agents and specialists')));
    });
  }

  function wireWorkforce() {
    if (!$('wo-root')) return;
    J.request('/api/v1/value-proof/access').then(function (a) {
      document.querySelectorAll('#wo-root .wo-admin').forEach(function (n) { n.hidden = !(a && a.admin); });
      testAdmin = !!(a && a.admin);
      if ($('wo-test-specialist').value) loadCustomerTests();
    }).catch(function () { /* controls stay hidden */ });
    $('wo-inv-suite').addEventListener('change', loadInventory);
    loadInventory();

    var download = $('wo-inv-download');
    download.addEventListener('click', async function () {
      download.disabled = true;
      try {
        var suite = $('wo-inv-suite').value;
        var r = await J.request(API + '/inventory/download' + (suite ? '?suite=' + encodeURIComponent(suite) : ''), { raw: true });
        J.saveBlob(await r.blob(), 'Lightwork-specialist-inventory.json');
      } catch (failure) {
        window.mvToast(await J.problem(failure, 'download the inventory'), { error: true });
      } finally { download.disabled = false; }
    });

    J.wire($('wo-lint-run'), async function () {
      return { url: API + '/setup-check', init: { method: 'POST', json: {} } };
    }, { status: $('wo-lint-status'), out: $('wo-lint-out'), action: 'check the agents and specialists', render: function (d) {
      var head = el('p', null, 'Checked ' + members(d.specialists) + ': ' + plural(d.must_fix, 'problem') + ' to fix and ' +
        plural(d.could_improve, 'thing') + ' that could be better.');
      if (!d.findings.length) return head;
      var rows = d.findings.map(function (f) {
        return [f.specialist, f.must_fix.join(' ') || '—', f.could_improve.join(' ') || '—'];
      });
      var nodes = [head, J.table(['Name', 'Must fix', 'Could be better'], rows)];
      if (d.more) nodes.push(el('p', 'muted', plural(d.more, 'more agent or specialist', 'more agents and specialists') + ' not shown.'));
      nodes.push(J.technical(d.findings.slice(0, 50).map(function (f) { return f.specialist + ': ' + f.technical.join(' | '); })));
      return nodes;
    } });

    J.request(API + '/test-suite').then(function (d) {
      var nodes = [el('p', null, plural(d.cases, 'test') + ' across ' + plural(d.specialists, 'specialist') + '. ' +
        (d.problems ? plural(d.problems, 'test') + ' need fixing: each must name a real specialist and say what a good answer looks like.'
          : 'Every test is complete.'))];
      var box = el('details');
      box.appendChild(el('summary', null, 'Show the tests'));
      box.appendChild(J.table(['Specialist', 'Task', 'A good answer'], d.list.map(function (c) {
        return [c.specialist, c.task, c.expects.join('; ')];
      })));
      nodes.push(box);
      if (d.technical.length) nodes.push(J.technical(d.technical));
      nodes.push(el('p', 'muted', 'Running the tests live needs a separate test run with an AI provider; this only checks the tests themselves.'));
      fill('wo-suite', nodes);
    }).catch(async function (failure) {
      fill('wo-suite', el('p', null, await J.problem(failure, 'load the test suite')));
    });

    $('wo-test-specialist').addEventListener('change', loadCustomerTests);
    $('wo-test-cancel').addEventListener('click', clearTestEditor);
    $('wo-test-form').addEventListener('submit', async function (event) {
      event.preventDefault();
      var specialist = $('wo-test-specialist').value;
      if (!specialist) return;
      var save = $('wo-test-save'); save.disabled = true;
      $('wo-test-status').textContent = 'Saving…';
      try {
        var body = { specialist: specialist, task: $('wo-test-task').value,
          includes: testPhrases('wo-test-includes'), excludes: testPhrases('wo-test-excludes'),
          refusal: $('wo-test-refusal').checked, citation: $('wo-test-citation').checked };
        if (editingCase) { body.identity = editingCase.id; body.expected_revision = editingCase.revision; }
        await J.request(API + '/test-suite/customer', {method: 'POST', json: body});
        await loadCustomerTests();
      } catch (failure) { $('wo-test-status').textContent = await J.problem(failure, 'save the answer check'); }
      finally { save.disabled = false; }
    });
    async function readOriginal(specialist, id) {
      try {
        var result = await J.request(API + '/test-suite/customer/run/' + encodeURIComponent(id));
        var r = result.run;
        $('wo-test-run-status').textContent = r.state === 'done'
          ? r.passed + ' of ' + r.total + ' answers passed. Open Previous checks to review them.'
          : r.state === 'prepared' ? 'The request was saved but has not started. Check the original request again to start it.'
            : r.message || 'The original request may still be running. No second request was sent.';
        if (r.state === 'done' || r.state === 'stopped') clearPendingRun(specialist);
        await loadCustomerTests();
        return r;
      } catch (failure) {
        $('wo-test-run-status').textContent = failure && failure.status === 404
          ? 'No saved result is visible yet. The original request may still be starting. Check again; no second request was sent.'
          : await J.problem(failure, 'check the original result');
        return null;
      }
    }
    async function launch(specialist, id, caseIds) {
      var revisions = {};
      caseIds.forEach(function (caseId) {
        var found = testCases.find(function (c) { return c.id === caseId; });
        if (found) revisions[caseId] = found.revision;
      });
      var started = await J.request(API + '/test-suite/customer/run',
        {method: 'POST', json: {specialist: specialist, operation_id: id,
          case_ids: caseIds, case_revisions: revisions}});
      if (started.run) {
        await readOriginal(specialist, id);
        return;
      }
      var done = await J.follow(started.job, {status: $('wo-test-run-status'), out: $('wo-test-run-out'),
        action: 'check the answers', render: function (d) {
          var r = d.run;
          return el('p', null, r.state === 'done' ? r.passed + ' of ' + r.total + ' answers passed. Open Previous checks to review them.'
            : (r.message || 'The check stopped early. Open Previous checks for completed answers.'));
        }});
      if (!done || done.state === 'failed') await readOriginal(specialist, id);
      else {
        if (done.result && done.result.run && ['done', 'stopped'].includes(done.result.run.state)) clearPendingRun(specialist);
        await loadCustomerTests();
      }
    }
    $('wo-test-run').addEventListener('click', async function () {
      var specialist = $('wo-test-specialist').value;
      var selected = selectedTestIds();
      if (!specialist || !selected.length || selected.length > 5) return;
      var id = pendingRun(specialist);
      var button = $('wo-test-run'); button.disabled = true;
      try {
        if (id) {
          var original = await readOriginal(specialist, id);
          if (original && original.state === 'prepared' && await window.mvConfirm(
            'No provider call started for this request. Try starting the same request?', {okText: 'Try original request'})) {
            await launch(specialist, id, pendingRunCases(specialist));
          }
          return;
        }
        if (!(await window.mvConfirm('Check ' + plural(selected.length, 'answer') +
          ' now? This may make paid AI requests. The run has a $0.25 spending limit, uses no tools, and never automatically retries an interrupted request.',
          {okText: 'Check answers'}))) return;
        id = newRunId();
        if (!id) { $('wo-test-run-status').textContent = 'This browser cannot keep a safe request reference, so no check was started.'; return; }
        try {
          window.sessionStorage.setItem(runStorageKey(specialist), id);
          window.sessionStorage.setItem(runStorageKey(specialist) + ':cases', JSON.stringify(selected));
        }
        catch (error) {
          clearPendingRun(specialist);
          id = null;
          $('wo-test-run-status').textContent = 'This browser cannot keep a safe request reference, so no check was started.';
          return;
        }
        await launch(specialist, id, selected);
      } catch (failure) {
        $('wo-test-run-status').textContent = await J.problem(failure, 'check the answers');
        if (id) await readOriginal(specialist, id);
      } finally { updateRunAvailability(); }
    });
    $('wo-test-new-run').addEventListener('click', async function () {
      var specialist = $('wo-test-specialist').value;
      if (!(await window.mvConfirm('Start a separate paid check? The original request may still be running or billed. Review its result and provider accounting first.',
        {okText: 'Start separately'}))) return;
      clearPendingRun(specialist);
      $('wo-test-run-status').textContent = 'The original request reference was cleared. Review Previous checks before starting another.';
      $('wo-test-run').textContent = 'Check answers';
      $('wo-test-new-run').hidden = true;
    });
    loadTestSpecialists();
  }

  // ---- tasks coming due --------------------------------------------------------------
  function loadUpcoming() {
    var days = $('wo-up-days').value;
    J.request(API + '/upcoming?days=' + encodeURIComponent(days)).then(function (d) {
      if (!d.tasks.length) {
        var period = 'the next ' + plural(Number(days), 'day');
        var further = $('wo-up-days').options[$('wo-up-days').options.length - 1].value !== String(days);
        var body = 'Tasks with a due date appear here, overdue ones first.' + (further ? ' Choose a longer period above to look further ahead.' : '');
        fill('wo-up', window.mvEmpty ? window.mvEmpty('Nothing is due in ' + period, body, null, {compact: true})
          : el('p', 'muted', 'Nothing is due in ' + period + '. ' + body));
        return;
      }
      fill('wo-up', J.table(['Task', 'Due', 'Department', 'Waiting on you'], d.tasks.map(function (t) {
        var link = el('a', null, t.title || 'Task');
        link.href = '/chat/goal/' + encodeURIComponent(t.task);
        var due = el('span', t.overdue ? 'badge blocked' : '', (t.overdue ? 'Overdue: ' : '') + J.when(t.due));
        return [link, due, t.department ? (window.mvHuman ? window.mvHuman(t.department) : t.department) : '—',
          t.questions ? plural(t.questions, 'question') : '—'];
      })));
    }).catch(async function (failure) {
      fill('wo-up', el('p', null, await J.problem(failure, 'load the tasks coming due')));
    });
  }
  if ($('wo-upcoming-root')) {
    $('wo-up-days').addEventListener('change', loadUpcoming);
    loadUpcoming();
  }
  wireWorkforce();
})();
