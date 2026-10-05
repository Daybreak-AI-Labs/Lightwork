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
  var testBaseline = '', testInstructions = '', testContext = '', testLoad = 0, comparisonReview = null;
  function runStorageKey(specialist) { return 'lightwork-specialist-answer-run:' + testContext + ':' + specialist; }
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
      window.sessionStorage.removeItem(runStorageKey(specialist) + ':payload');
    } catch (error) { /* no browser storage */ }
  }
  function pendingRunPayload(specialist) {
    try { return JSON.parse(window.sessionStorage.getItem(runStorageKey(specialist) + ':payload') || 'null'); }
    catch (_) { return null; }
  }
  function clearComparisonReview() { comparisonReview = null; $('wo-compare-review').hidden = true; }
  function comparisonAnswers(run) {
    var nodes = [el('p', 'muted', 'Recorded cost: $' + Number(run.spent_dollars || 0).toFixed(6) + '. The same model and fixed question versions were used. No instructions were changed.')];
    if (run.baseline_current === false || run.questions_current === false)
      nodes.push(el('p', 'muted', 'The current instructions or saved questions have changed. This is a historical comparison; review a new comparison before making a decision.'));
    nodes.push(el('p', 'muted', 'Phrase checks are literal. A source check detects a reference marker, not a verified source. These results do not establish correctness or business improvement.'));
    (run.case_snapshots || []).forEach(function (question) {
      var section = el('section'); section.appendChild(el('h4', null, question.task)); section.appendChild(el('p', 'muted', testCriteria(question)));
      var pair = el('div', 'wo-test-grid', null);
      ['current', 'proposed'].forEach(function (variant) {
        var card = el('div', 'wo-answer-card', null), answer = run.results.find(function (r) { return r.case_id === question.id && r.variant === variant; });
        card.appendChild(el('h5', null, variant === 'current' ? 'Current instructions' : 'Proposed instructions'));
        card.appendChild(el('p', 'wo-answer-text', answer ? answer.answer : 'Not checked.'));
        if (answer) {
          var labels = {includes:'Required phrases', excludes:'Forbidden phrases', refusal:'Refusal', citation:'Source marker', empty_answer:'Answer present'};
          card.appendChild(el('p', null, Object.keys(answer.checks).map(function (key) { return (labels[key] || 'Answer check') + ': ' + (answer.checks[key] === 1 ? 'Passed' : 'Needs review'); }).join(' · ')));
          card.appendChild(el('p', 'muted', 'Recorded answer cost: $' + Number(answer.spent_dollars || 0).toFixed(6)));
        }
        pair.appendChild(card);
      });
      section.appendChild(pair); nodes.push(section);
    });
    var instructions = el('details'); instructions.appendChild(el('summary', null, 'Instructions used'));
    ['current', 'proposed'].forEach(function (variant) { instructions.appendChild(el('h4', null, variant === 'current' ? 'Current instructions' : 'Proposed instructions')); instructions.appendChild(el('p', 'wo-answer-text', run.comparison[variant + '_instructions'])); });
    nodes.push(instructions); return nodes;
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
    testBaseline = data.baseline_revision || ''; testInstructions = data.current_instructions || ''; testContext = data.context || '';
    $('wo-test-current-instructions').textContent = testInstructions;
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
      if (r.comparison) {
        var questionCount = r.case_snapshots.length;
        var currentPassed = r.results.filter(function (answer) { return answer.variant === 'current' && answer.passed; }).length;
        var proposedPassed = r.results.filter(function (answer) { return answer.variant === 'proposed' && answer.passed; }).length;
        box.querySelector('summary').textContent = J.when(r.started_at) + ' · Instruction comparison · ' + (r.state === 'done'
          ? 'Current: ' + currentPassed + '/' + questionCount + ' passed · Proposed: ' + proposedPassed + '/' + questionCount + ' passed' : state);
        comparisonAnswers(r).forEach(function (node) { box.appendChild(node); });
        return box;
      }
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
    var generation = ++testLoad;
    clearComparisonReview(); $('wo-compare-instructions').value = '';
    testCases = []; testBaseline = ''; testInstructions = ''; testContext = '';
    $('wo-test-current-instructions').textContent = ''; fill('wo-test-history', null); updateRunAvailability();
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
      if (generation !== testLoad || specialist !== $('wo-test-specialist').value) return;
      $('wo-test-status').textContent = '';
      renderTests(data);
    } catch (failure) { if (generation === testLoad) $('wo-test-status').textContent = await J.problem(failure, 'load the answer checks'); }
  }
  function selectedTestIds() {
    return Array.from(document.querySelectorAll('#wo-tests-list .wo-test-choice:checked'), function (box) { return box.value; });
  }
  function updateRunAvailability() {
    clearComparisonReview();
    var count = selectedTestIds().length;
    $('wo-test-run').disabled = !$('wo-test-specialist').value || (!pendingRun($('wo-test-specialist').value) && (!testLiveEnabled || !count || count > 5));
    $('wo-compare-review-button').disabled = !testLiveEnabled || !count || count > 5 || !!pendingRun($('wo-test-specialist').value);
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
        members(d.sign_off_enforced) + ' must have a person approve their work' +
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
    async function launch(specialist, id, caseIds, reviewedPayload) {
      var revisions = {};
      caseIds.forEach(function (caseId) {
        var found = testCases.find(function (c) { return c.id === caseId; });
        if (found) revisions[caseId] = found.revision;
      });
      var body = reviewedPayload || {specialist: specialist, operation_id: id, case_ids: caseIds, case_revisions: revisions};
      var started = await J.request(API + '/test-suite/customer/' + (body.proposed_instructions ? 'compare' : 'run'), {method: 'POST', json: body});
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
      var id = pendingRun(specialist);
      if (!specialist || (!id && (!selected.length || selected.length > 5))) return;
      var button = $('wo-test-run'); button.disabled = true;
      try {
        if (id) {
          var original = await readOriginal(specialist, id);
          if (original && original.state === 'prepared' && await window.mvConfirm(
            'No provider call started for this request. Try starting the same request?', {okText: 'Try original request'})) {
            await launch(specialist, id, pendingRunCases(specialist), pendingRunPayload(specialist));
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
      updateRunAvailability();
    });
    $('wo-compare-form').addEventListener('input', clearComparisonReview);
    $('wo-compare-form').addEventListener('submit', function (event) {
      event.preventDefault();
      var ids = selectedTestIds(), specialist = $('wo-test-specialist').value, proposed = $('wo-compare-instructions').value;
      if (!testBaseline || !testContext || !ids.length || ids.length > 5 || pendingRun(specialist)) return;
      var selected = testCases.filter(function (row) { return ids.includes(row.id); }), revisions = {};
      selected.forEach(function (row) { revisions[row.id] = row.revision; });
      comparisonReview = {specialist:specialist, case_ids:ids, case_revisions:revisions, proposed_instructions:proposed,
        baseline_revision:testBaseline, expected_context:testContext};
      var pair = el('div', 'wo-test-grid', null);
      [['Current instructions', testInstructions], ['Proposed instructions', proposed]].forEach(function (item) {
        var card = el('div', 'wo-answer-card', null); card.appendChild(el('h4', null, item[0])); card.appendChild(el('p', 'wo-answer-text', item[1])); pair.appendChild(card);
      });
      fill('wo-compare-reviewed', [J.table(['Question', 'Passing answer'], selected.map(function (row) { return [row.task, testCriteria(row)]; })), pair]);
      $('wo-compare-review').hidden = false; $('wo-compare-review').focus();
    });
    $('wo-compare-edit').addEventListener('click', function () { clearComparisonReview(); $('wo-compare-instructions').focus(); });
    $('wo-compare-start').addEventListener('click', async function () {
      var reviewed = comparisonReview, button = $('wo-compare-start');
      if (!reviewed || pendingRun(reviewed.specialist)) return;
      if (!(await window.mvConfirm('Compare these two instruction versions on the reviewed questions? At most $0.25 total. No instructions will change and uncertain paid requests will not be repeated.', {okText:'Start comparison'}))) return;
      if (comparisonReview !== reviewed) return;
      button.disabled = true;
      var id = newRunId();
      try {
        if (!id) throw new Error('No safe request reference');
        var body = Object.assign({operation_id:id}, reviewed);
        window.sessionStorage.setItem(runStorageKey(reviewed.specialist), id);
        window.sessionStorage.setItem(runStorageKey(reviewed.specialist) + ':cases', JSON.stringify(reviewed.case_ids));
        window.sessionStorage.setItem(runStorageKey(reviewed.specialist) + ':payload', JSON.stringify(body));
        clearComparisonReview();
        await launch(reviewed.specialist, id, reviewed.case_ids, body);
      } catch (failure) {
        var problem = await J.problem(failure, 'compare the instructions');
        var found = id ? await readOriginal(reviewed.specialist, id) : null;
        if (!found && failure && [400, 409, 422].includes(failure.status)) {
          clearPendingRun(reviewed.specialist); await loadCustomerTests();
          $('wo-test-run-status').textContent = problem + ' Review the current instructions and questions before trying again.';
        } else $('wo-test-run-status').textContent = problem;
      } finally { button.disabled = false; updateRunAvailability(); }
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
