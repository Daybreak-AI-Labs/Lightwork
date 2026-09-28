// Value and proof read-outs for the Spend and Savings pages: where spend
// went, a suggested cap, why tasks failed, cheaper-over-time results,
// automatically approved actions (with Withdraw), outside agents, the record
// of decisions, signed downloads and a file check. Plain words only.
(function () {
  'use strict';
  var API = '/api/v1/value-proof';
  var spend = document.getElementById('vp-spend');
  var proof = document.getElementById('vp-proof');
  if (!spend && !proof) return;
  if (window.__lwValueProof) return;   // both pages include the script once each
  window.__lwValueProof = true;

  function $(id) { return document.getElementById(id); }
  function el(tag, text, cls) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function fill(id, node) { var box = $(id); if (!box) return; box.textContent = ''; if (node) box.appendChild(node); }
  function say(id, text) { var box = $(id); if (box) box.textContent = String(text || ''); }
  function money(n) {
    var v = Number(n) || 0;
    return '$' + (v < 1 && v > 0 ? v.toFixed(4) : v.toFixed(2));
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  function errorText(failure, action) {
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
  async function planError(failure, action) {
    if (failure && (failure.status === 409 || failure.status === 422) && typeof failure.json === 'function') {
      try {
        var body = await failure.json();
        if (body && typeof body.detail === 'string' && body.detail.length < 240) return body.detail;
      } catch (e) { /* fall through */ }
    }
    return errorText(failure, action);
  }
  function table(headers, rows) {
    var t = el('table');
    var head = el('thead'); var hr = el('tr');
    headers.forEach(function (h) { var th = el('th', h); th.scope = 'col'; hr.appendChild(th); });
    head.appendChild(hr); t.appendChild(head);
    var body = el('tbody');
    rows.forEach(function (cells) {
      var tr = el('tr');
      cells.forEach(function (c) {
        var td = el('td');
        if (c && c.nodeType) td.appendChild(c); else td.textContent = c;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    t.appendChild(body);
    return t;
  }
  function failed(id, action) {
    return async function (failure) {
      if (failure && failure.status === 403) {
        fill(id, el('p', 'Only administrators and auditors can ' + action + '.', 'muted'));
        return;
      }
      fill(id, el('p', errorText(failure, action)));
    };
  }

  // ---- Spend page -------------------------------------------------------
  function loadRetro() {
    return get('/cost-retro').then(function (d) {
      if (!d.tasks) {
        say('vp-retro-summary', 'No task costs recorded yet. This fills in as tasks finish.');
        return;
      }
      var line = money(d.total) + ' spent across ' + plural(d.tasks, 'task', 'tasks') + '.';
      if (d.failed_spend) line += ' ' + money(d.failed_spend) + ' (' + Math.round(d.failed_share * 100) + '%) went to tasks that failed.';
      say('vp-retro-summary', line);
      var advice = $('vp-retro-advice');
      advice.textContent = '';
      d.advice.forEach(function (a) { advice.appendChild(el('li', a)); });
      var rows = $('vp-retro-rows');
      rows.textContent = '';
      d.costliest.forEach(function (r) {
        var tr = el('tr');
        var td = el('td');
        var link = el('a', r.title || 'Task');
        link.href = '/chat/goal/' + encodeURIComponent(r.task);
        td.appendChild(link);
        tr.appendChild(td);
        tr.appendChild(el('td', money(r.cost)));
        tr.appendChild(el('td', r.failed ? 'Failed' : 'Finished'));
        rows.appendChild(tr);
      });
      $('vp-retro-table').hidden = !d.costliest.length;
    }).catch(async function (failure) {
      say('vp-retro-summary', failure && failure.status === 403
        ? 'Only administrators and auditors can see which tasks cost the most.'
        : errorText(failure, 'load where spend went'));
    });
  }
  function loadCap() {
    return get('/budget-tune').then(function (d) {
      if (!d.suggestions.length) {
        fill('vp-cap', el('p', 'Not enough finished tasks with costs yet. A suggestion appears after ' +
          d.minimum_tasks + ' tasks.', 'muted'));
        return;
      }
      fill('vp-cap', table(['For', 'Suggested cap', 'Based on'], d.suggestions.map(function (s) {
        return [s.for, money(s.cap), plural(s.tasks, 'task', 'tasks') + ' (nine in ten cost up to ' + money(s.typical_high) + ')'];
      })));
    }).catch(failed('vp-cap', 'load the suggested cap'));
  }
  function loadFailures() {
    return get('/failures').then(function (d) {
      if (!d.total) {
        fill('vp-fail', el('p', d.recording ? 'No failed tasks recorded. Good news.'
          : 'Failure reasons are not being recorded. Turn on failure recording on the Learning page.', 'muted'));
        return;
      }
      var top = Math.max.apply(null, d.reasons.map(function (r) { return r.count; }));
      fill('vp-fail', table(['Reason', 'Tasks', ''], d.reasons.map(function (r) {
        var bar = el('span', null, 'vp-bar');
        bar.style.width = Math.max(2, Math.round(r.count / top * 100)) + '%';
        bar.setAttribute('aria-hidden', 'true');
        return [r.reason, String(r.count), bar];
      })));
    }).catch(failed('vp-fail', 'load why tasks failed'));
  }

  // ---- Savings page -----------------------------------------------------
  var access = { admin: false, audit: false };
  function pct(n) { var v = Number(n) || 0; return (v > 0 ? '+' : '') + Math.round(v) + '%'; }
  function loadCompounding() {
    return get('/compounding').then(function (d) {
      if (!d.task_types.length) {
        fill('vp-comp', el('p', 'Not enough repeated tasks yet. This appears once the same kind of task has run several times.', 'muted'));
        return;
      }
      fill('vp-comp', table(['Kind of task', 'Runs', 'Cost change', 'Success change', 'Verdict'], d.task_types.map(function (t) {
        return [t.name, String(t.runs), pct(t.cost_change_pct), pct(t.success_change * 100),
          t.improving ? 'Getting cheaper and no less reliable' : 'Not improving yet'];
      })));
    }).catch(failed('vp-comp', 'load the results'));
  }
  function loadDepartments() {
    return get('/departments').then(function (d) {
      if (!d.departments.length) {
        fill('vp-dept', el('p', 'No department has enough runs yet to show a trend.', 'muted'));
        return;
      }
      fill('vp-dept', table(['Department', 'Runs', 'Cost per task then', 'Cost per task now', 'Verdict'], d.departments.map(function (x) {
        return [x.name, String(x.runs), money(x.cost_before), money(x.cost_now), x.improving ? 'Getting cheaper' : 'Not yet'];
      })));
    }).catch(failed('vp-dept', 'load the department results'));
  }
  function loadEarned() {
    return get('/earned-autonomy').then(function (d) {
      var rule = 'An action is approved automatically only after ' + d.needs.outcomes + ' real outcomes, ' +
        d.needs.streak + ' correct predictions in a row and ' + d.needs.accuracy_pct + '% accuracy.';
      var state = !d.enabled ? 'Earning automatic approval is off. ' + rule
        : (d.auto_grant_on ? rule : 'Evidence is being collected, but automatic approval is not switched on. ' + rule);
      say('vp-ea-state', state);
      if (!d.actions.length) {
        fill('vp-ea', el('p', 'No evidence yet. It builds up as checked actions run and their real outcomes come back.', 'muted'));
        return;
      }
      fill('vp-ea', table(['Action', 'Now', 'Why'].concat(access.admin ? ['Actions'] : []), d.actions.map(function (a) {
        var row = [a.name, a.auto_approved ? 'Approved automatically' : 'A person approves',
          a.confirmed + ' of ' + (a.confirmed + a.missed) + ' predictions matched what really happened; ' +
          a.streak + ' correct in a row'];
        if (access.admin) {
          if (a.auto_approved) {
            var b = el('button', 'Withdraw', 'btn btn--danger');
            b.type = 'button';
            b.setAttribute('aria-label', 'Withdraw automatic approval for ' + a.name);
            b.addEventListener('click', function () { withdraw(a, b); });
            row.push(b);
          } else row.push('—');
        }
        return row;
      })));
    }).catch(async function (failure) { say('vp-ea-state', errorText(failure, 'load automatic approvals')); });
  }
  async function withdraw(a, b) {
    if (!(await window.mvConfirm('Withdraw automatic approval for "' + a.name + '"? A person will need to approve it again from now on.',
      { okText: 'Withdraw' }))) return;
    b.disabled = true;
    try {
      await post('/earned-autonomy/revoke', { action: a.action });
      say('vp-proof-msg', 'Automatic approval withdrawn for ' + a.name + '.');
      await loadEarned();
    } catch (failure) {
      say('vp-proof-msg', await planError(failure, 'withdraw automatic approval'));
      b.disabled = false;
    }
  }
  function loadFleet() {
    return get('/fleet-memory').then(function (d) {
      if (!d.enabled) {
        fill('vp-fleet', el('p', 'Shared memory for outside agents is turned off.', 'muted'));
        return;
      }
      if (!d.agents.length) {
        var p = el('p', 'No outside agents are registered yet. ', 'muted');
        var link = el('a', 'Connect an outside agent'); link.href = '/external-agents';
        p.appendChild(link);
        fill('vp-fleet', p);
        return;
      }
      fill('vp-fleet', table(['Agent', 'Made by', 'Shared successes', 'Shared failures'], d.agents.map(function (a) {
        return [a.description || a.agent, a.vendor, String(a.worked), String(a.failed)];
      })));
    }).catch(failed('vp-fleet', 'load the outside agents'));
  }
  function loadRecord() {
    return get('/record-stats').then(function (d) {
      var text = plural(d.records, 'decision', 'decisions') + ' on record: ' + plural(d.tasks, 'task', 'tasks') + ' and ' +
        plural(d.approvals, 'approval', 'approvals') + ' (' + d.human_decisions + ' decided by a person).';
      if (d.departments.length) {
        text += ' Busiest: ' + d.departments.slice(0, 3).map(function (x) { return x.name + ' (' + x.count + ')'; }).join(', ') + '.';
      }
      say('vp-rec', text);
    }).catch(async function (failure) { say('vp-rec', errorText(failure, 'load the record')); });
  }
  function download(link, action) {
    link.addEventListener('click', async function (event) {
      event.preventDefault();
      say('vp-proof-msg', 'Preparing the download…');
      try {
        var r = await fetch(link.getAttribute('href'), { credentials: 'same-origin' });
        if (!r.ok) throw r;
        var blob = await r.blob();
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = link.id === 'vp-rec-download' ? 'lightwork-operating-record.json' : 'lightwork-learning-statement.json';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        say('vp-proof-msg', 'Download ready.');
      } catch (failure) {
        say('vp-proof-msg', await planError(failure, action));
      }
    });
  }
  var check = $('vp-check');
  if (check) {
    check.addEventListener('submit', async function (event) {
      event.preventDefault();
      var box = $('vp-check-result');
      box.textContent = '';
      var input = $('vp-check-file');
      var file = input.files && input.files[0];
      if (!file) { box.textContent = 'Choose a file first.'; return; }
      if (file.size > 10000000) { box.textContent = 'That file is too large to check here.'; return; }
      var kind = $('vp-check-kind').value;
      box.textContent = 'Checking…';
      try {
        var text = await file.text();
        var out = await post(kind === 'memory' ? '/memory-verify' : '/record-verify', { document: text });
        box.textContent = '';
        if (kind === 'record') {
          box.appendChild(el('p', !out.intact
            ? 'This file could not be confirmed. It may have been changed, or it is not a signed Lightwork record.'
            : (out.from_this_installation
              ? 'This record is intact and was signed by this Lightwork installation. It holds ' + plural(out.records, 'decision', 'decisions') + '.'
              : 'This record is intact, but it was signed by a different Lightwork installation.')));
        } else {
          box.appendChild(el('p', out.intact
            ? 'This learning statement is intact and was signed by this Lightwork installation.'
            : 'This learning statement could not be confirmed against this installation. It may have been changed or signed elsewhere.'));
          if (out.claims.length) {
            var ul = el('ul');
            out.claims.forEach(function (c) { ul.appendChild(el('li', c.claim + ': ' + c.result)); });
            box.appendChild(ul);
          }
        }
      } catch (failure) {
        box.textContent = await planError(failure, 'check the file');
      }
    });
  }

  if (spend) { loadRetro(); loadCap(); loadFailures(); }
  if (proof) {
    get('/access').catch(function () { return access; }).then(function (a) {
      access = a || access;
      document.querySelectorAll('#vp-proof .vp-admin').forEach(function (n) { n.hidden = !access.admin; });
      loadEarned();
    });
    loadCompounding(); loadDepartments(); loadFleet(); loadRecord();
    var rec = $('vp-rec-download'); if (rec) download(rec, 'download the record');
    var mem = $('vp-mem-download'); if (mem) download(mem, 'download the learning statement');
  }
})();
