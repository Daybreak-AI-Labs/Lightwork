// Workforce page: what each specialist can do and what stops it, a check of
// every specialist's setup (a background job) and the specialist test suite.
// Tasks page: tasks coming due. Plain words only.
(function () {
  'use strict';
  if (!window.LWJobs || window.LWWorkforceOps) return;
  window.LWWorkforceOps = true;
  var J = window.LWJobs;
  var el = J.el, plural = J.plural;
  var API = '/api/v1/workforce-ops';
  function $(id) { return document.getElementById(id); }
  function fill(id, nodes) {
    var box = $(id);
    if (!box) return;
    box.textContent = '';
    (Array.isArray(nodes) ? nodes : [nodes]).forEach(function (n) { if (n) box.appendChild(n); });
  }
  var RUNGS = { observe: 'Watch only', suggest: 'Suggest', request: 'Ask before acting', auto: 'Act on their own' };

  // ---- what each specialist can do -------------------------------------------------
  var suitesLoaded = false;
  function loadInventory() {
    var suite = $('wo-inv-suite').value;
    fill('wo-inv', el('p', 'Loading…', 'muted'));
    J.request(API + '/inventory' + (suite ? '?suite=' + encodeURIComponent(suite) : '')).then(function (d) {
      if (!suitesLoaded) {
        d.suites.forEach(function (s) { var o = el('option', s.label); o.value = s.value; $('wo-inv-suite').appendChild(o); });
        suitesLoaded = true;
      }
      var facts = [
        plural(d.specialists, 'specialist') + ' in ' + plural(d.departments, 'department') + ', including ' + plural(d.builders, 'builder') + '.',
        d.can_change_things_while_drafting
          ? plural(d.can_change_things_while_drafting, 'specialist') + ' that should only draft can reach tools that change things. Review these below.'
          : 'No specialist that only drafts can reach tools that change things.',
        plural(d.sign_off_enforced, 'specialist') + ' must have a person sign off their work' +
          (d.sign_off_only_asked ? '; ' + plural(d.sign_off_only_asked, 'more') + ' only ask for it in their instructions.' : '.'),
        plural(d.extra_refusals, 'specialist') + ' refuse extra kinds of requests for their field; ' +
          plural(d.with_deliverable, 'specialist') + ' produce a defined piece of work.',
        d.levels_enabled === false
          ? 'Independence levels are turned off, so every specialist follows the same approval rules.'
          : 'Independence: ' + Object.keys(RUNGS).map(function (r) { return RUNGS[r] + ' ' + ((d.independence || {})[r] || 0); }).join(', ') +
            '. ' + plural(d.still_supervised, 'specialist') + ' still supervised while new.'
      ];
      var nodes = [el('ul')];
      facts.forEach(function (f) { nodes[0].appendChild(el('li', f)); });
      if (d.flagged.length) {
        nodes.push(el('h3', 'Specialists to review'));
        nodes.push(J.table(['Specialist', 'Can reach'], d.flagged.map(function (f) {
          return [f.specialist, f.can_reach.map(window.mvHuman || String).join(', ')];
        })));
      }
      fill('wo-inv', nodes);
    }).catch(async function (failure) {
      fill('wo-inv', el('p', await J.problem(failure, 'load the specialist inventory')));
    });
  }

  function wireWorkforce() {
    if (!$('wo-root')) return;
    J.request('/api/v1/value-proof/access').then(function (a) {
      document.querySelectorAll('#wo-root .wo-admin').forEach(function (n) { n.hidden = !(a && a.admin); });
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
    }, { status: $('wo-lint-status'), out: $('wo-lint-out'), action: 'check the specialists', render: function (d) {
      var head = el('p', 'Checked ' + plural(d.specialists, 'specialist') + ': ' + plural(d.must_fix, 'problem') + ' to fix and ' +
        plural(d.could_improve, 'thing') + ' that could be better.');
      if (!d.findings.length) return head;
      var rows = d.findings.map(function (f) {
        return [f.specialist, f.must_fix.join(' ') || '—', f.could_improve.join(' ') || '—'];
      });
      var nodes = [head, J.table(['Specialist', 'Must fix', 'Could be better'], rows)];
      if (d.more) nodes.push(el('p', plural(d.more, 'more specialist') + ' not shown.', 'muted'));
      nodes.push(J.technical(d.findings.slice(0, 50).map(function (f) { return f.specialist + ': ' + f.technical.join(' | '); })));
      return nodes;
    } });

    J.request(API + '/test-suite').then(function (d) {
      var nodes = [el('p', plural(d.cases, 'test') + ' across ' + plural(d.specialists, 'specialist') + '. ' +
        (d.problems ? plural(d.problems, 'test') + ' need fixing: each must name a real specialist and say what a good answer looks like.'
          : 'Every test is complete.'))];
      var box = el('details');
      box.appendChild(el('summary', 'Show the tests'));
      box.appendChild(J.table(['Specialist', 'Task', 'A good answer'], d.list.map(function (c) {
        return [c.specialist, c.task, c.expects.join('; ')];
      })));
      nodes.push(box);
      if (d.technical.length) nodes.push(J.technical(d.technical));
      nodes.push(el('p', 'Running the tests live needs a separate test run with an AI provider; this only checks the tests themselves.', 'muted'));
      fill('wo-suite', nodes);
    }).catch(async function (failure) {
      fill('wo-suite', el('p', await J.problem(failure, 'load the test suite')));
    });
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
          : el('p', 'Nothing is due in ' + period + '. ' + body, 'muted'));
        return;
      }
      fill('wo-up', J.table(['Task', 'Due', 'Department', 'Waiting on you'], d.tasks.map(function (t) {
        var link = el('a', t.title || 'Task');
        link.href = '/chat/goal/' + encodeURIComponent(t.task);
        var due = el('span', (t.overdue ? 'Overdue: ' : '') + J.when(t.due), t.overdue ? 'badge blocked' : '');
        return [link, due, t.department ? (window.mvHuman ? window.mvHuman(t.department) : t.department) : '—',
          t.questions ? plural(t.questions, 'question') : '—'];
      })));
    }).catch(async function (failure) {
      fill('wo-up', el('p', await J.problem(failure, 'load the tasks coming due')));
    });
  }
  if ($('wo-upcoming-root')) {
    $('wo-up-days').addEventListener('change', loadUpcoming);
    loadUpcoming();
  }
  wireWorkforce();
})();
