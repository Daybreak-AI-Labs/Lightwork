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
        : 'Suggestions are turned off, so no new changes can be suggested. Changes already in use stay in use and can still be undone.' +
          (data.switch_place ? ' To turn them on, go to ' + data.switch_place + '.' : '');
      say('llp-ref-state', state);
      if (!data.enabled) {
        var on = el('a', 'Open All settings', 'btn btn--ghost');
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
  // Before/after for a change: what the instructions say now and what they
  // will say, or which later changes an undo also removes.
  async function preview(p) {
    try {
      var r = await fetch('/api/v1/learning-ops/refinements/' + encodeURIComponent(p.id) + '/preview', { credentials: 'same-origin' });
      if (!r.ok) throw r;
      return await r.json();
    } catch (failure) {
      say('llp-ref-msg', await problem(failure, 'show what would change'));
      return null;
    }
  }
  function previewBox(lines) {
    var box = el('div', null, 'llp-preview');
    box.setAttribute('role', 'status');
    lines.forEach(function (l) { box.appendChild(el('p', l[1] ? l[0] + ' ' + l[1] : l[0])); });
    return box;
  }
  function showPreview(b, box) {
    var cell = b.parentNode;
    var old = cell.querySelector('.llp-preview');
    if (old) old.remove();
    cell.appendChild(box);
  }
  async function applyChange(p, b) {
    b.disabled = true;
    var pv = await preview(p);
    b.disabled = false;
    if (!pv) return;
    showPreview(b, previewBox([['Now it says:', pv.now || '(nothing yet for this)'], ['After applying:', pv.after]]));
    if (!(await window.mvConfirm('Apply "' + p.title + '"? Agents follow the new text from their next task. Lightwork saves a restore point first, so you can undo it later.',
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
    b.disabled = true;
    var pv = await preview(p);
    b.disabled = false;
    if (!pv) return;
    var also = pv.undo_also_removes || [];
    showPreview(b, previewBox([['Removes:', pv.after]].concat(also.length ? [['Also removes, because they came later:', also.join('; ')]] : [])));
    if (!(await window.mvConfirm('Undo "' + p.title + '"? Instructions go back to exactly how they were before it was applied' +
      (also.length ? ', which also undoes ' + also.length + ' change' + (also.length === 1 ? '' : 's') + ' applied after it.' : '.'),
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
  function publishModel() {
    document.dispatchEvent(new CustomEvent('lightwork:copilot-selection',
      { detail: { selection: model() ? { kind: 'learning_model', id: model() } : {} } }));
  }
  async function withdrawLesson(row, reason, buttonEl) {
    var chosenModel = model();
    var why = reason.value.trim();
    if (why.length < 10) { say('llp-harness-msg', 'Explain why this lesson should be removed in at least 10 characters.'); return; }
    if (!(await window.mvConfirm('Remove this exact lesson for ' + row.scope_label + '? New work will no longer use it. Past work remains recorded.',
      { okText: 'Remove lesson' }))) return;
    buttonEl.disabled = true;
    say('llp-harness-msg', 'Removing the lesson…');
    try {
      await post('/lessons/withdraw', { model_id: chosenModel, token: row.token, reason: why, confirm: true });
      say('llp-harness-msg', 'Lesson removed. New work will no longer use it.');
    } catch (failure) {
      say('llp-harness-msg', await problem(failure, 'remove the lesson') + ' Refresh the results to check its current state.');
    } finally {
      await Promise.all([loadModels(), loadLog()]);
      buttonEl.disabled = false;
    }
  }
  function lessonAction(row) {
    var cell = el('td');
    var details = el('details');
    details.appendChild(el('summary', 'Remove lesson'));
    var label = el('label', 'Why remove this lesson?');
    var reason = el('textarea', null, 'textarea');
    reason.rows = 2;
    reason.maxLength = 1000;
    reason.required = true;
    label.appendChild(reason);
    details.appendChild(label);
    details.appendChild(el('p', 'Only this lesson in this part of the work will be removed. Past work stays in the activity record.', 'muted'));
    details.appendChild(button('Confirm removal', 'btn btn--danger', function (b) { withdrawLesson(row, reason, b); }));
    cell.appendChild(details);
    return cell;
  }
  function loadEfficacy() {
    var rows = $('llp-eff-rows');
    if (!model()) return Promise.resolve();
    return get('/efficacy?model_id=' + encodeURIComponent(model())).then(function (data) {
      rows.textContent = '';
      if (!data.lessons.length) {
        var tr = el('tr'); var td = el('td', 'This model has no currently available shared lessons.'); td.colSpan = admin ? 5 : 4;
        tr.appendChild(td); rows.appendChild(tr); return;
      }
      data.lessons.forEach(function (r) {
        var tr = el('tr');
        var lesson = el('td', r.text);
        lesson.appendChild(el('div', r.scope_label, 'muted'));
        tr.appendChild(lesson);
        tr.appendChild(el('td', String(r.went_well)));
        tr.appendChild(el('td', String(r.did_not)));
        var rate = el('td', pct(r.rate));
        if (r.recent_rate != null) rate.appendChild(el('div', 'Lately ' + pct(r.recent_rate), 'muted'));
        tr.appendChild(rate);
        if (admin) tr.appendChild(lessonAction(r));
        rows.appendChild(tr);
      });
    }).catch(async function (failure) {
      rows.textContent = '';
      var tr = el('tr'); var td = el('td', await problem(failure, 'load the lesson results')); td.colSpan = admin ? 5 : 4;
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
  function modelChanged() { publishModel(); return Promise.all([loadEfficacy(), loadConflicts()]); }
  function loadModels() {
    return get('/models').then(function (data) {
      var previous = model();
      picker.textContent = '';
      if (!data.models.length) {
        var empty = el('option', 'No models yet'); empty.value = '';
        picker.appendChild(empty);
        picker.disabled = true;
        $('llp-eff-rows').textContent = '';
        var row = el('tr'); var cell = el('td', 'No currently available shared lessons.'); cell.colSpan = admin ? 5 : 4;
        row.appendChild(cell); $('llp-eff-rows').appendChild(row);
        $('llp-conf').textContent = '';
        publishModel();
        return;
      }
      picker.disabled = false;
      data.models.forEach(function (m) {
        var o = el('option', m.label); o.value = m.value; picker.appendChild(o);
      });
      if (data.models.some(function (m) { return m.value === previous; })) picker.value = previous;
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
      var support = $('llp-support') ? $('llp-support').value : '3';
      var data = await get('/preview?min_support=' + encodeURIComponent(support) +
        (model() ? '&model_id=' + encodeURIComponent(model()) : ''));
      if (!data.enabled) {
        box.appendChild(el('p', 'Self-coaching is off, so there is nothing to preview.' +
          (data.switch_place ? ' Turn on ' + data.switch_place + '.' : ''), 'muted'));
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
    else if (r.promotion_gate_off) div.appendChild(el('p', 'Nothing new can be adopted until you turn on ' +
      (r.gate_place || 'adopting proven improvements on the Learning page') + '.', 'muted'));
    if (r.new_lessons.length) {
      var ul = el('ul');
      r.new_lessons.forEach(function (ln) { ul.appendChild(el('li', ln)); });
      div.appendChild(ul);
    }
    return div;
  }
  // Learning pass and test-case check run as background jobs (see
  // lightwork-ops-jobs.js): the page shows progress and, for every model,
  // a Stop button.
  var J = window.LWJobs;
  function runUi(action, render, after) {
    return { status: $('llp-run-status'), out: $('llp-run-result'), action: action, render: render, after: after };
  }
  function loadPlan() {
    var cost = $('llp-cost');
    if (!cost || !J) return;
    J.request('/api/v1/learning-ops/self-harness/plan').then(function (d) {
      var parts = [d.spending_limit != null ? 'Each run can spend up to ' + J.money(d.spending_limit) + ' on testing.'
        : 'No testing spending limit is set, so the general limits apply.'];
      if (d.checker) parts.push('Answers are checked by ' + d.checker + '.');
      if (d.careful_testing) parts.push('Careful testing is on, so include the current standing instructions for new lessons to be adopted.');
      parts.push('It stops if learning is paused.');
      cost.textContent = parts.join(' ');
    }).catch(function () { /* the default sentence stays */ });
  }
  if (J) J.wire($('llp-run'), async function () {
    var scope = $('llp-scope').value;
    var input = $('llp-instructions');
    var file = input && input.files && input.files[0];
    var text = null;
    if (file) {
      if (file.size > 1024 * 1024) { say('llp-run-status', 'That instructions file is larger than 1 MB.'); return null; }
      text = await J.readText(file);
    }
    if (scope === 'chosen' && !model()) { say('llp-run-status', 'Choose a model first.'); return null; }
    var who = scope === 'all' ? 'every model in use' : (scope === 'chosen' ? 'the model chosen above' : 'the main assistant model');
    if (!(await window.mvConfirm('Run a learning pass now for ' + who +
      '? It looks for repeated mistakes, tests possible lessons within the evaluation spending limit' +
      ($('llp-retire').checked ? ', and retires old ones.' : '.'), { okText: 'Run now' }))) return null;
    var body = { scope: scope, trial: $('llp-trial').checked, retire: $('llp-retire').checked, instructions: text };
    if (scope === 'chosen') body.model_id = model();
    return { url: API + '/run', init: { method: 'POST', json: body } };
  }, runUi('run the learning pass', function (d) {
    return d.reports.map(reportBlock);
  }, function () { modelChanged(); loadLog(); }));

  if (J) J.wire($('llp-quality-btn'), async function () {
    var remove = $('llp-quality-remove').checked;
    var msg = remove
      ? 'Check every test case now and remove the ones that are too easy to show whether a lesson helps? Each case is tried a couple of times, which uses part of the evaluation spending limit. Removed cases cannot be recovered here.'
      : 'Check every test case now? Each one is tried a couple of times, which uses part of the evaluation spending limit. Nothing is removed.';
    if (!(await window.mvConfirm(msg, { okText: remove ? 'Check and remove' : 'Check test cases' }))) return null;
    return { url: API + '/corpus-quality', init: { method: 'POST', json: { model_id: model() || null, remove_weak: remove, confirm: remove } } };
  }, runUi('check the test cases', function (data) {
    if (!data.configured) return el('p', 'No test cases are set up yet, so there is nothing to check.', 'muted');
    if (!data.cases.length) return el('p', 'There are no test cases for this model yet.', 'muted');
    var weak = data.cases.filter(function (c) { return !c.useful; }).length;
    var head = el('p', plural(data.cases.length, 'test case', 'test cases') + ' checked. ' +
      (weak ? weak + (weak === 1 ? ' is' : ' are') + ' too easy to show whether a lesson helps.' : 'All of them can show whether a lesson helps.') +
      (data.removed ? ' Removed ' + plural(data.removed, 'test case', 'test cases') + '.' : ''));
    var ul = el('ul');
    data.cases.forEach(function (c) {
      ul.appendChild(el('li', c.task + ': ' + (c.useful ? 'useful' : 'too easy') +
        (c.baseline_rate != null ? ' (passed ' + Math.round(c.baseline_rate * 100) + '% without help)' : '')));
    });
    return [head, ul];
  }));

  loadRefinements();
  loadModels();
  loadLog();
  loadPlan();
})();
