// Compliance page: turns each "what to do" into a button (switch a protection
// on, stop all work, set retention, export or erase a person's data, download
// auditor reports, run the EU AI Act self-check, search the control library).
(function () {
  'use strict';
  var doc = document;
  function $(id) { return doc.getElementById(id); }
  function errorText(failure, action) {
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.';
  }
  function confirmAction(message, okText) {
    return window.mvConfirm ? window.mvConfirm(message, { okText: okText }) : Promise.resolve(false);
  }
  function say(el, text) { if (el) el.textContent = text; }
  function jsonHeaders() { return { 'Content-Type': 'application/json', Accept: 'application/json' }; }
  function getJSON(url) {
    return fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw r;
      return r.json();
    });
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST', headers: jsonHeaders(), credentials: 'same-origin',
      body: JSON.stringify(body || {}),
    }).then(function (r) { if (!r.ok) throw r; return r; });
  }
  function human(days) {
    if (days % 365 === 0) { var y = days / 365; return y + (y === 1 ? ' year' : ' years'); }
    return days + ' days';
  }
  function saveBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = doc.createElement('a');
    a.href = url; a.download = name; doc.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  var status = $('cmp-status');

  // Auto-apply the regulation filter as soon as a choice is made.
  var framework = $('cmp-framework');
  if (framework && framework.form) {
    framework.addEventListener('change', function () { framework.form.submit(); });
  }

  // Protection switches.
  doc.addEventListener('click', function (ev) {
    var btn = ev.target && ev.target.closest ? ev.target.closest('[data-cmp-switch]') : null;
    if (!btn) return;
    var key = btn.getAttribute('data-cmp-switch');
    confirmAction(btn.getAttribute('data-cmp-confirm') || 'Turn this protection on?', 'Turn on').then(function (ok) {
      if (!ok) return;
      btn.disabled = true;
      post('/api/v1/compliance/switches/' + encodeURIComponent(key)).then(function (r) { return r.json(); }).then(function (body) {
        say(status, body.message || 'Saved.');
        if (body.active) setTimeout(function () { location.reload(); }, 800);
        else btn.disabled = false;
      }).catch(function (failure) {
        say(status, failure && failure.status === 409
          ? 'Set up sign-in for Lightwork first. Once this is on, everyone must sign in.'
          : errorText(failure, 'turn this protection on'));
        btn.disabled = false;
      });
    });
  });

  // Stop all running work.
  doc.addEventListener('click', function (ev) {
    var btn = ev.target && ev.target.closest ? ev.target.closest('[data-cmp-halt]') : null;
    if (!btn) return;
    confirmAction('Stop all running work now? Nothing new will start until someone resumes it from the Stop button at the top of the page.', 'Stop everything').then(function (ok) {
      if (!ok) return;
      post('/api/v1/halt', { reason: 'Stopped from the compliance page' }).then(function () {
        say(status, 'All work is stopped. Use the Stop button at the top of the page to resume.');
      }).catch(function (failure) { say(status, errorText(failure, 'stop the running work')); });
    });
  });

  // People: export and erase.
  var person = $('cmp-person');
  var peopleResult = $('cmp-people-result');
  function chosenPerson() {
    if (!person || !person.value) return null;
    var opt = person.options[person.selectedIndex];
    return { channel: opt.getAttribute('data-channel'), user: opt.getAttribute('data-user'), label: opt.textContent };
  }
  function loadPeople() {
    if (!person) return Promise.resolve();
    return getJSON('/api/v1/compliance/people').then(function (body) {
      person.textContent = '';
      var rows = body.people || [];
      var first = doc.createElement('option');
      first.value = '';
      first.textContent = rows.length ? 'Choose a person' : 'No one has talked with Lightwork yet';
      person.appendChild(first);
      rows.forEach(function (row, i) {
        var opt = doc.createElement('option');
        opt.value = String(i + 1);
        opt.setAttribute('data-channel', row.channel);
        opt.setAttribute('data-user', row.user);
        opt.setAttribute('data-name', row.person);
        opt.textContent = row.person + ' (' + row.channel_label + ', ' + row.conversations +
          (row.conversations === 1 ? ' conversation)' : ' conversations)');
        person.appendChild(opt);
      });
    }).catch(function (failure) {
      person.textContent = '';
      var opt = doc.createElement('option'); opt.value = ''; opt.textContent = 'People could not be loaded';
      person.appendChild(opt);
      say(peopleResult, errorText(failure, 'load the list of people'));
    });
  }
  var exportBtn = $('cmp-export');
  if (exportBtn) exportBtn.addEventListener('click', function () {
    var p = chosenPerson();
    if (!p) { say(peopleResult, 'Choose a person first.'); person.focus(); return; }
    exportBtn.disabled = true;
    post('/api/v1/compliance/people/export', { channel: p.channel, user: p.user }).then(function (r) {
      return r.blob();
    }).then(function (blob) {
      saveBlob(blob, 'lightwork-person-data.json');
      say(peopleResult, 'Their data has been downloaded. Send it to them securely.');
    }).catch(function (failure) {
      say(peopleResult, errorText(failure, 'export their data'));
    }).then(function () { exportBtn.disabled = false; });
  });
  var eraseBtn = $('cmp-erase');
  if (eraseBtn) eraseBtn.addEventListener('click', function () {
    var p = chosenPerson();
    if (!p) { say(peopleResult, 'Choose a person first.'); person.focus(); return; }
    confirmAction('Permanently erase everything Lightwork holds about ' + p.label +
      '? Their conversations, tasks and files are deleted and cannot be recovered. Download their data first if they asked for a copy.', 'Erase').then(function (ok) {
      if (!ok) return;
      eraseBtn.disabled = true;
      post('/api/v1/compliance/people/erase', { channel: p.channel, user: p.user }).then(function (r) {
        return r.json();
      }).then(function (body) {
        say(peopleResult, body.message || 'Erased.');
        return loadPeople();
      }).catch(function (failure) {
        say(peopleResult, errorText(failure, 'erase their data'));
      }).then(function () { eraseBtn.disabled = false; });
    });
  });

  // Retention saves explicit nulls; deletion is bound to a reviewed receipt.
  var retentionForm = $('cmp-retention-form');
  var retentionResult = $('cmp-retention-result');
  var retentionReview = $('cmp-retention-review');
  var retentionReceipt = null;
  var retentionDirty = false;
  var retentionBusy = false;
  var saveRet = $('cmp-retention-save');
  var applyRet = $('cmp-retention-apply');
  var confirmRet = $('cmp-retention-confirm');
  function retentionBusyState(busy) {
    retentionBusy = busy;
    [saveRet, applyRet, confirmRet].forEach(function (button) { if (button) button.disabled = busy; });
  }
  function showReceipt(receipt) {
    if (!retentionReview) return;
    retentionReceipt = receipt;
    retentionReview.hidden = false;
    var summary = $('cmp-retention-summary'); summary.textContent = '';
    (receipt.targets || []).forEach(function (target) {
      var title = doc.createElement('h4'); title.textContent = target.name; summary.appendChild(title);
      var policy = doc.createElement('p');
      policy.textContent = (receipt.steps || []).filter(function (step) { return step.tenant === target.tenant; }).map(function (step) {
        return step.label + ': ' + human(step.days) + ', ' + step.eligible + ' eligible at review';
      }).join('; ') || 'No automatic deletion periods.';
      summary.appendChild(policy);
    });
    (receipt.steps || []).forEach(function (step) {
      var line = doc.createElement('p');
      line.textContent = step.workspace + ' / ' + step.label + ': ' + (step.status === 'running' ? 'outcome not yet confirmed' : step.status) +
        (step.removed !== undefined ? ', ' + step.removed + ' confirmed removed' : '') +
        (step.skipped ? ', ' + step.skipped + ' skipped' : '') +
        (step.audit_record === 'unconfirmed' ? ', activity record unconfirmed' : '') +
        (step.uncertain_prior ? ', earlier attempt still needs reconciliation' : '');
      summary.appendChild(line);
    });
    confirmRet.hidden = receipt.status === 'completed' || !receipt.steps.length;
    confirmRet.textContent = receipt.status === 'review' ? 'Delete reviewed old records' : 'Retry unresolved stores only';
    try { sessionStorage.setItem('lightwork-retention-receipt', receipt.id); } catch (_) {}
  }
  function loadRetention() {
    if (!retentionForm) return Promise.resolve();
    return getJSON('/api/v1/compliance/retention').then(function (body) {
      (body.fields || []).forEach(function (field) {
        var sel = $('cmp-ret-' + field.key);
        if (!sel) return;
        while (sel.options.length > 1) sel.remove(1);
        var choices = (body.choices || []).slice();
        if (field.days && choices.indexOf(field.days) < 0) choices.push(field.days);
        choices.sort(function (a, b) { return a - b; });
        choices.forEach(function (days) {
          var opt = doc.createElement('option'); opt.value = String(days); opt.textContent = human(days); sel.appendChild(opt);
        });
        sel.value = field.days ? String(field.days) : '';
      });
      retentionDirty = false;
    }).catch(function (failure) { say(retentionResult, errorText(failure, 'load the retention periods')); });
  }
  if (retentionForm) retentionForm.addEventListener('change', function () {
    retentionDirty = true; retentionReceipt = null;
    if (retentionReview) retentionReview.hidden = true;
    say(retentionResult, 'Unsaved periods. Save them before reviewing deletion.');
  });
  if (saveRet) saveRet.addEventListener('click', async function () {
    if (retentionBusy) return;
    var body = {};
    retentionForm.querySelectorAll('select').forEach(function (sel) { body[sel.name] = sel.value ? parseInt(sel.value, 10) : null; });
    retentionBusyState(true); retentionReceipt = null; retentionReview.hidden = true;
    try {
      var res = await (await post('/api/v1/compliance/retention', body)).json();
      await loadRetention(); say(retentionResult, res.message);
    } catch (failure) { say(retentionResult, errorText(failure, 'save the retention periods')); }
    finally { retentionBusyState(false); }
  });
  if (applyRet) applyRet.addEventListener('click', async function () {
    if (retentionBusy) return;
    if (retentionDirty) { say(retentionResult, 'Save your edited periods before reviewing deletion.'); return; }
    retentionBusyState(true);
    try {
      showReceipt(await (await post('/api/v1/compliance/retention/preview')).json());
      $('cmp-retention-holds').checked = false;
      say(retentionResult, 'Review the saved effective policy and exact workspace scope below. No records have been deleted.');
    } catch (failure) { say(retentionResult, errorText(failure, 'preview old records')); }
    finally { retentionBusyState(false); }
  });
  if (confirmRet) confirmRet.addEventListener('click', async function () {
    if (retentionBusy || !retentionReceipt || retentionDirty) return;
    if (!$('cmp-retention-holds').checked) { say(retentionResult, 'Check preservation holds with your records owner first.'); return; }
    retentionBusyState(true);
    try {
      var ok = await confirmAction('Permanently delete the reviewed old records in the listed workspaces? Completed deletions cannot be undone.', 'Delete reviewed records');
      if (!ok) return;
      var result = await (await post('/api/v1/compliance/retention/apply', {
        review_id: retentionReceipt.id, confirm_no_holds: true, retry: retentionReceipt.status !== 'review'
      })).json();
      showReceipt(result.receipt); say(retentionResult, result.message);
    } catch (failure) {
      say(retentionResult, 'The request did not finish cleanly. Reading the saved receipt; completed deletions cannot be undone.');
      try { showReceipt(await getJSON('/api/v1/compliance/retention/runs/' + retentionReceipt.id)); }
      catch (_) { say(retentionResult, 'The receipt is temporarily unavailable. Reopen this page before retrying; some deletions may already have completed.'); }
    } finally { retentionBusyState(false); }
  });
  if (retentionReview) {
    var lastReceipt = null;
    try { lastReceipt = sessionStorage.getItem('lightwork-retention-receipt'); } catch (_) {}
    if (lastReceipt && /^[a-f0-9]{32}$/.test(lastReceipt)) getJSON('/api/v1/compliance/retention/runs/' + lastReceipt).then(showReceipt).catch(function () {});
  }

  // Assessment downloads.
  var assessment = $('cmp-assessment');
  var assessmentResult = $('cmp-assessment-result');
  function loadAssessments() {
    if (!assessment) return Promise.resolve();
    return getJSON('/api/v1/compliance/assessments').then(function (body) {
      assessment.textContent = '';
      var rows = body.assessments || [];
      var first = doc.createElement('option'); first.value = '';
      first.textContent = rows.length ? 'Choose an assessment' : 'No assessments yet';
      assessment.appendChild(first);
      rows.forEach(function (row) {
        var opt = doc.createElement('option');
        opt.value = row.id;
        var when = window.mvWhen ? window.mvWhen(row.created_at) : '';
        var kind = window.mvHuman ? window.mvHuman(row.type) : row.type;
        opt.textContent = row.subject + (kind ? ' – ' + kind : '') + (when ? ' – ' + when : '');
        assessment.appendChild(opt);
      });
      if (!rows.length) say(assessmentResult, 'Start an assessment on the Assessments page, then come back to download it.');
    }).catch(function (failure) {
      assessment.textContent = '';
      var opt = doc.createElement('option'); opt.value = ''; opt.textContent = 'Assessments could not be loaded';
      assessment.appendChild(opt);
      say(assessmentResult, errorText(failure, 'load the assessments'));
    });
  }
  function download(kind) {
    if (!assessment || !assessment.value) { say(assessmentResult, 'Choose an assessment first.'); return; }
    var a = doc.createElement('a');
    a.href = '/api/v1/compliance/downloads/' + kind + '?assessment=' + encodeURIComponent(assessment.value);
    a.setAttribute('download', '');
    doc.body.appendChild(a); a.click(); a.remove();
    say(assessmentResult, 'Your download has started.');
  }
  var oscal = $('cmp-oscal'); if (oscal) oscal.addEventListener('click', function () { download('oscal'); });
  var soa = $('cmp-soa'); if (soa) soa.addEventListener('click', function () { download('soa'); });

  // EU AI Act self-check.
  var aiAct = $('cmp-ai-act');
  var aiActResult = $('cmp-ai-act-result');
  function list(title, items) {
    var wrap = doc.createElement('div');
    var h = doc.createElement('h3'); h.textContent = title; wrap.appendChild(h);
    var ul = doc.createElement('ul'); ul.className = 'cmp-list';
    items.forEach(function (text) { var li = doc.createElement('li'); li.textContent = text; ul.appendChild(li); });
    wrap.appendChild(ul);
    return wrap;
  }
  if (aiAct) aiAct.addEventListener('click', function () {
    getJSON('/api/v1/compliance/ai-act').then(function (body) {
      aiActResult.textContent = '';
      var p = doc.createElement('p');
      p.textContent = body.default_level + ' The AI notice is ' + (body.notice_on ? 'switched on.' : 'switched off. Turn it on above.');
      aiActResult.appendChild(p);
      aiActResult.appendChild(list('Make sure none of these banned uses apply', body.banned_uses || []));
      aiActResult.appendChild(list('Check whether you use AI for any of these high-risk purposes', body.high_risk_uses || []));
      aiActResult.appendChild(list('What each level requires', (body.duties || []).map(function (d) { return d.level + ': ' + d.text; })));
      if (body.note) { var n = doc.createElement('p'); n.className = 'muted'; n.textContent = body.note; aiActResult.appendChild(n); }
    }).catch(function (failure) { say(aiActResult, errorText(failure, 'run the self-check')); });
  });

  // Control finder.
  var finder = $('cmp-finder');
  var finderResult = $('cmp-finder-result');
  if (finder) finder.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var q = ($('cmp-finder-q').value || '').trim();
    if (!q) { say(finderResult, 'Describe the risk first.'); return; }
    getJSON('/api/v1/compliance/control-finder?q=' + encodeURIComponent(q)).then(function (body) {
      finderResult.textContent = '';
      var rows = body.controls || [];
      if (!rows.length) { say(finderResult, 'No matching controls. Try other words, such as "encryption" or "vendor".'); return; }
      var ul = doc.createElement('ul'); ul.className = 'cmp-list';
      rows.forEach(function (c) {
        var li = doc.createElement('li');
        var strong = doc.createElement('strong'); strong.textContent = c.title; li.appendChild(strong);
        var refs = (c.references || []).map(function (r) { return r.framework + ' ' + r.reference; }).join('; ');
        li.appendChild(doc.createTextNode(' (' + c.area + '). ' + refs));
        ul.appendChild(li);
      });
      finderResult.appendChild(ul);
    }).catch(function (failure) { say(finderResult, errorText(failure, 'search the controls')); });
  });

  loadPeople();
  loadRetention();
  loadAssessments();
})();
