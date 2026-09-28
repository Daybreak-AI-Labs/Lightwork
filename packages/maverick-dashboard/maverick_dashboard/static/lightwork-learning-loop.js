// Learned page: suggested instruction changes (list, suggest, apply, undo)
// and self-coaching read-outs (lesson results, clashes, what comes next,
// learning activity, hold-out check, run now). Plain words only; reference
// numbers stay inside collapsed "Technical details".
(function () {
  'use strict';
  var root = document.getElementById('llp-root');
  if (!root) return;
  var API = '/api/v1/learning-loop';
  var admin = root.getAttribute('data-admin') === '1';
  function $(id) { return document.getElementById(id); }
  function say(id, text) { var el = $(id); if (el) el.textContent = String(text || ''); }
  function el(tag, text, cls) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function when(ts) { return (typeof window.mvWhen === 'function' ? window.mvWhen(ts) : '') || ''; }
  function pct(rate) { return rate == null ? 'Not used yet' : Math.round(rate * 100) + '%'; }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  // A failed request as one plain sentence. Our own refusals (409) carry a
  // sentence written for people; everything else uses the shared wording.
  async function problem(failure, action) {
    if (failure && failure.status === 409 && typeof failure.json === 'function') {
      try {
        var body = await failure.json();
        if (body && typeof body.detail === 'string' && body.detail.length < 240) return body.detail;
      } catch (e) { /* fall through */ }
    }
    if (failure && failure.status === 403 && action.indexOf('see') === 0) {
      return 'Only administrators and auditors can ' + action + '.';
    }
    return typeof window.mvErrorText === 'function' ? window.mvErrorText(failure, action) : 'Could not ' + action + '.';
  }
  function get(path) {
    return fetch(API + path, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw r;
      return r.json();
    });
  }
  function post(path, body) {
    return fetch(API + path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {})
    }).then(function (r) {
      if (!r.ok) throw r;
      return r.json();
    });
  }
  function technical(pairs) {
    var box = el('details');
    box.appendChild(el('summary', 'Technical details'));
    var list = el('ul');
    pairs.forEach(function (p) { if (p[1] != null && p[1] !== '') list.appendChild(el('li', p[0] + ': ' + p[1])); });
    box.appendChild(list);
    return box;
  }

  // ---- suggested instruction changes ----------------------------------
  function statusText(p) {
    if (p.status === 'applied') return 'In use since ' + (when(p.applied_at) || 'it was applied');
    if (p.status === 'reverted') return 'Undone ' + (when(p.reverted_at) || '');
    if (p.status === 'pending') return p.approval || 'Waiting to be applied';
    return typeof window.mvHuman === 'function' ? window.mvHuman(p.status) : p.status;
  }
  function refineRow(p) {
    var tr = el('tr');
    var what = el('td');
    what.appendChild(el('strong', p.title));
    what.appendChild(el('div', p.target, 'muted'));
    what.appendChild(el('div', p.change));
    if (p.rationale) what.appendChild(el('div', 'Why: ' + p.rationale, 'muted'));
    what.appendChild(technical([['Reference', p.technical && p.technical.reference],
      ['Approval number', p.technical && p.technical.approval_number],
      ['Fingerprint', p.technical && p.technical.fingerprint]]));
    tr.appendChild(what);
    tr.appendChild(el('td', p.failure));
    var st = el('td', statusText(p));
    if (p.proposed_at) st.appendChild(el('div', 'Suggested ' + when(p.proposed_at), 'muted'));
    tr.appendChild(st);
    if (admin) {
      var act = el('td');
      if (p.can_apply) act.appendChild(button('Apply', 'btn btn--primary', function (b) { applyChange(p, b); }));
      if (p.can_revert) act.appendChild(button('Undo', 'btn btn--danger', function (b) { undoChange(p, b); }));
      if (!p.can_apply && !p.can_revert) {
        if (p.status === 'pending') {
          var link = el('a', 'Decide it under Approvals', 'btn btn--ghost');
          link.href = '/approvals';
          act.appendChild(link);
        } else act.textContent = '—';
      }
      tr.appendChild(act);
    }
    return tr;
  }
  function button(label, cls, onClick) {
    var b = el('button', label, cls);
    b.type = 'button';
    b.addEventListener('click', function () { onClick(b); });
    return b;
  }
  function loadRefinements() {
    return get('/refinements').then(function (data) {
      var rows = $('llp-ref-rows');
      rows.textContent = '';
      var state = data.enabled
        ? (data.require_approval ? 'Suggestions are on. Each change needs an approval before it can be applied.'
          : 'Suggestions are on. Changes can be applied without a separate approval.')
        : 'Suggestions are turned off, so no new changes can be suggested. Changes already in use stay in use and can still be undone.';
      say('llp-ref-state', state);
      if (!data.enabled) {
        var on = el('a', 'Turn on suggestions in All settings', 'btn btn--ghost');
        on.href = '/settings/all';
        $('llp-ref-state').appendChild(document.createTextNode(' '));
        $('llp-ref-state').appendChild(on);
      }
      if (!data.proposals.length) {
        var tr = el('tr');
        var td = el('td', admin && data.enabled
          ? 'No changes have been suggested yet. Use "Suggest a change" below when an agent keeps making the same mistake.'
          : 'No changes have been suggested yet.');
        td.colSpan = admin ? 4 : 3;
        tr.appendChild(td);
        rows.appendChild(tr);
      } else {
        data.proposals.forEach(function (p) { rows.appendChild(refineRow(p)); });
      }
      var list = $('llp-in-force');
      list.textContent = '';
      if (!data.in_force.length) list.appendChild(el('li', 'No suggested changes are in use.', 'muted'));
      data.in_force.forEach(function (r) {
        var li = el('li');
        li.appendChild(el('strong', r.title + ' (' + r.target + ')'));
        li.appendChild(el('div', r.change));
        li.appendChild(el('div', 'Applied ' + when(r.applied_at), 'muted'));
        list.appendChild(li);
      });
      var box = $('llp-propose-box');
      if (box) box.hidden = !data.enabled;
    }).catch(async function (failure) {
      var rows = $('llp-ref-rows');
      rows.textContent = '';
      var tr = el('tr');
      var td = el('td', await problem(failure, 'load the suggested changes'));
      td.colSpan = admin ? 4 : 3;
      tr.appendChild(td);
      rows.appendChild(tr);
    });
  }
  async function applyChange(p, b) {
    if (!(await window.mvConfirm('Apply "' + p.title + '"? Lightwork saves a restore point first, so you can undo it later.',
      { okText: 'Apply change' }))) return;
    b.disabled = true;
    say('llp-ref-msg', 'Applying…');
    try {
      await post('/refinements/' + encodeURIComponent(p.id) + '/apply');
      say('llp-ref-msg', 'Change applied. Agents use it from their next task.');
      await loadRefinements();
    } catch (failure) {
      say('llp-ref-msg', await problem(failure, 'apply the change'));
      b.disabled = false;
    }
  }
  async function undoChange(p, b) {
    if (!(await window.mvConfirm('Undo "' + p.title + '"? Instructions go back to exactly how they were before it was applied, which also undoes any change applied after it.',
      { okText: 'Undo change' }))) return;
    b.disabled = true;
    say('llp-ref-msg', 'Undoing…');
    try {
      await post('/refinements/' + encodeURIComponent(p.id) + '/revert');
      say('llp-ref-msg', 'Change undone.');
      await loadRefinements();
    } catch (failure) {
      say('llp-ref-msg', await problem(failure, 'undo the change'));
      b.disabled = false;
    }
  }
  var form = $('llp-propose');
  if (form) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var f = new FormData(form);
      var body = {};
      ['target', 'title', 'failure', 'change', 'rationale'].forEach(function (k) { body[k] = String(f.get(k) || '').trim(); });
      if (!body.title || !body.failure || !body.change || !body.rationale) {
        say('llp-ref-msg', 'Fill in every box before suggesting the change.');
        return;
      }
      var submit = form.querySelector('button[type=submit]');
      if (submit) submit.disabled = true;
      say('llp-ref-msg', 'Sending your suggestion…');
      try {
        var out = await post('/refinements', body);
        say('llp-ref-msg', out.needs_approval
          ? 'Suggestion saved. It now waits for approval on the Approvals page; then apply it here.'
          : 'Suggestion saved. You can apply it from the list.');
        form.reset();
        await loadRefinements();
      } catch (failure) {
        say('llp-ref-msg', await problem(failure, 'save the suggestion'));
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  }

  // ---- self-coaching read-outs ------------------------------------------
  var picker = $('llp-model');
  function model() { return picker && picker.value ? picker.value : ''; }
  function loadEfficacy() {
    var rows = $('llp-eff-rows');
    if (!model()) return Promise.resolve();
    return get('/efficacy?model_id=' + encodeURIComponent(model())).then(function (data) {
      rows.textContent = '';
      if (!data.lessons.length) {
        var tr = el('tr'); var td = el('td', 'This model has no lessons yet.'); td.colSpan = 4;
        tr.appendChild(td); rows.appendChild(tr); return;
      }
      data.lessons.forEach(function (r) {
        var tr = el('tr');
        var lesson = el('td', r.text);
        if (r.department) lesson.appendChild(el('div', 'Only for ' + (window.mvHuman ? window.mvHuman(r.department) : r.department), 'muted'));
        tr.appendChild(lesson);
        tr.appendChild(el('td', String(r.went_well)));
        tr.appendChild(el('td', String(r.did_not)));
        var rate = el('td', pct(r.rate));
        if (r.recent_rate != null) rate.appendChild(el('div', 'Lately ' + pct(r.recent_rate), 'muted'));
        tr.appendChild(rate);
        rows.appendChild(tr);
      });
    }).catch(async function (failure) {
      rows.textContent = '';
      var tr = el('tr'); var td = el('td', await problem(failure, 'load the lesson results')); td.colSpan = 4;
      tr.appendChild(td); rows.appendChild(tr);
    });
  }
  function loadConflicts() {
    var list = $('llp-conf');
    if (!model()) return Promise.resolve();
    return get('/conflicts?model_id=' + encodeURIComponent(model())).then(function (data) {
      list.textContent = '';
      if (!data.pairs.length) { list.appendChild(el('li', 'No lessons seem to contradict each other.', 'muted')); return; }
      data.pairs.forEach(function (p) {
        list.appendChild(el('li', '“' + p.first + '” may contradict “' + p.second + '”. Remove whichever is wrong from the lessons above.'));
      });
    }).catch(async function (failure) {
      list.textContent = '';
      list.appendChild(el('li', await problem(failure, 'check for clashing lessons')));
    });
  }
  function loadLog() {
    var list = $('llp-log');
    return get('/log?limit=30').then(function (data) {
      list.textContent = '';
      if (!data.events.length) { list.appendChild(el('li', 'Nothing has been learned or removed yet.', 'muted')); return; }
      data.events.forEach(function (e) {
        var li = el('li');
        li.appendChild(el('strong', (when(e.when) || 'Recently') + ': ' + e.what));
        if (e.lesson) li.appendChild(el('div', '“' + e.lesson + '”'));
        list.appendChild(li);
      });
    }).catch(async function (failure) {
      list.textContent = '';
      list.appendChild(el('li', await problem(failure, 'see the learning activity'), 'muted'));
    });
  }
  function modelChanged() { return Promise.all([loadEfficacy(), loadConflicts()]); }
  function loadModels() {
    return get('/models').then(function (data) {
      picker.textContent = '';
      if (!data.models.length) {
        picker.appendChild(el('option', 'No models yet'));
        picker.disabled = true;
        return;
      }
      data.models.forEach(function (m) {
        var o = el('option', m.label); o.value = m.value; picker.appendChild(o);
      });
      return modelChanged();
    }).catch(async function (failure) {
      say('llp-harness-msg', await problem(failure, 'load the models'));
    });
  }
  if (picker) picker.addEventListener('change', modelChanged);

  var previewBtn = $('llp-preview-btn');
  if (previewBtn) previewBtn.addEventListener('click', async function () {
    var box = $('llp-preview');
    box.textContent = '';
    previewBtn.disabled = true;
    try {
      var data = await get('/preview' + (model() ? '?model_id=' + encodeURIComponent(model()) : ''));
      if (!data.enabled) {
        box.appendChild(el('p', 'Self-coaching is off, so there is nothing to preview. Turn it on on the Learning page.', 'muted'));
      } else if (!data.weaknesses.length) {
        box.appendChild(el('p', 'No repeated mistakes found in the last ' + plural(data.scanned, 'failure', 'failures') +
          ' (' + data.eligible + ' could be used for this model). Nothing would change.', 'muted'));
      } else {
        var ul = el('ul');
        data.weaknesses.forEach(function (w) {
          var li = el('li');
          li.appendChild(el('strong', 'Seen ' + plural(w.times, 'time', 'times') + ': ' + w.pattern));
          if (w.would_add) li.appendChild(el('div', 'Would try the lesson: “' + w.would_add + '”'));
          ul.appendChild(li);
        });
        box.appendChild(ul);
        box.appendChild(el('p', 'This is only a preview. Nothing was changed.', 'muted'));
      }
    } catch (failure) {
      box.appendChild(el('p', await problem(failure, 'see the preview')));
    } finally {
      previewBtn.disabled = false;
    }
  });

  var holdoutBtn = $('llp-holdout-btn');
  if (holdoutBtn) holdoutBtn.addEventListener('click', async function () {
    var box = $('llp-holdout');
    box.textContent = '';
    holdoutBtn.disabled = true;
    try {
      var data = await get('/holdout');
      if (!data.configured) box.appendChild(el('p', 'No hold-out record is set up, so there is nothing to check.', 'muted'));
      else if (!data.verified) box.appendChild(el('p', 'The hold-out record did not pass the check. It may be incomplete or changed. Ask your administrator to look into it.'));
      else {
        box.appendChild(el('p', 'The hold-out record is complete and untouched: ' + plural(data.events, 'entry', 'entries') +
          ', ' + plural(data.checks_used, 'test use', 'test uses') + ' recorded.'));
        box.appendChild(technical([['Record', data.technical.ledger], ['Latest fingerprint', data.technical.latest_fingerprint]]));
      }
    } catch (failure) {
      box.appendChild(el('p', await problem(failure, 'see the hold-out check')));
    } finally {
      holdoutBtn.disabled = false;
    }
  });

  function reportBlock(r) {
    var div = el('div');
    var head = 'Found ' + plural(r.patterns_found, 'repeated mistake', 'repeated mistakes') + ', tried ' +
      plural(r.ideas_tried, 'idea', 'ideas') + ', ' + r.passed_testing + ' passed testing, ' +
      plural(r.adopted, 'new lesson', 'new lessons') + ' adopted, ' + plural(r.retired, 'old lesson', 'old lessons') + ' retired.';
    div.appendChild(el('p', head));
    if (r.paused_for_calibration) div.appendChild(el('p', 'New lessons are paused until the quality checker is reliable again.', 'muted'));
    else if (r.promotion_gate_off) div.appendChild(el('p', 'Nothing new can be adopted until self-improvement is turned on on the Learning page.', 'muted'));
    if (r.new_lessons.length) {
      var ul = el('ul');
      r.new_lessons.forEach(function (ln) { ul.appendChild(el('li', ln)); });
      div.appendChild(ul);
    }
    return div;
  }
  var runBtn = $('llp-run');
  if (runBtn) runBtn.addEventListener('click', async function () {
    var scope = $('llp-scope').value;
    if (!(await window.mvConfirm('Run a learning pass now for ' + (scope === 'all' ? 'every model in use' : 'the main assistant model') +
      '? It looks for repeated mistakes, tests possible lessons within the evaluation spending limit, and retires old ones.',
      { okText: 'Run now' }))) return;
    runBtn.disabled = true;
    var box = $('llp-run-result');
    box.textContent = '';
    say('llp-harness-msg', 'Running. This can take a few minutes…');
    try {
      var data = await post('/run', { scope: scope });
      data.reports.forEach(function (r) { box.appendChild(reportBlock(r)); });
      say('llp-harness-msg', 'Learning pass finished.');
      await Promise.all([modelChanged(), loadLog()]);
    } catch (failure) {
      say('llp-harness-msg', await problem(failure, 'run the learning pass'));
    } finally {
      runBtn.disabled = false;
    }
  });

  var qualityBtn = $('llp-quality-btn');
  if (qualityBtn) qualityBtn.addEventListener('click', async function () {
    if (!(await window.mvConfirm('Check every test case now? Each one is tried a couple of times, which uses part of the evaluation spending limit. Nothing is removed.',
      { okText: 'Check test cases' }))) return;
    qualityBtn.disabled = true;
    var box = $('llp-run-result');
    box.textContent = '';
    say('llp-harness-msg', 'Checking the test cases…');
    try {
      var data = await post('/corpus-quality', { model_id: model() || null });
      if (!data.configured) box.appendChild(el('p', 'No test cases are set up yet, so there is nothing to check.', 'muted'));
      else if (!data.cases.length) box.appendChild(el('p', 'There are no test cases for this model yet.', 'muted'));
      else {
        var weak = data.cases.filter(function (c) { return !c.useful; }).length;
        box.appendChild(el('p', plural(data.cases.length, 'test case', 'test cases') + ' checked. ' +
          (weak ? weak + (weak === 1 ? ' is' : ' are') + ' too easy to show whether a lesson helps.' : 'All of them can show whether a lesson helps.')));
        var ul = el('ul');
        data.cases.forEach(function (c) {
          ul.appendChild(el('li', c.task + ': ' + (c.useful ? 'useful' : 'too easy') +
            (c.baseline_rate != null ? ' (passed ' + Math.round(c.baseline_rate * 100) + '% without help)' : '')));
        });
        box.appendChild(ul);
      }
      say('llp-harness-msg', 'Test case check finished.');
    } catch (failure) {
      say('llp-harness-msg', await problem(failure, 'check the test cases'));
    } finally {
      qualityBtn.disabled = false;
    }
  });

  loadRefinements();
  loadModels();
  loadLog();
})();
