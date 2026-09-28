/* Settings: confirmations, the "make a secret" button and the Maintenance tab. */
(function () {
  'use strict';

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function errorText(failure, action) {
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.';
  }
  function badge(ok, yes, no) {
    return el('span', 'badge ' + (ok ? 'active' : 'blocked'), ok ? yes : no);
  }
  async function call(path, method) {
    var response = await window.fetch(path, {
      method: method || 'GET', cache: 'no-store', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'}
    });
    if (!response.ok) throw response;
    return response.json();
  }

  // Forms marked data-confirm ask first (remove a key, reset visibility...).
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (event.defaultPrevented || form.dataset.confirmed === 'true') {
        delete form.dataset.confirmed;
        return;
      }
      event.preventDefault();
      window.mvConfirm(form.dataset.confirm, {okText: form.dataset.confirmOk || 'Confirm'}).then(function (ok) {
        if (!ok) return;
        form.dataset.confirmed = 'true';
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      });
    });
  });

  // Make a new signing secret for other apps, show it once, offer Copy.
  var generate = document.getElementById('webhook-generate');
  if (generate) {
    var box = document.getElementById('webhook-new');
    var value = document.getElementById('webhook-new-value');
    var status = document.getElementById('webhook-status');
    generate.addEventListener('click', async function () {
      if (generate.dataset.replace === 'true') {
        var ok = await window.mvConfirm('Make a new secret? Apps using the old secret will stop working until you give them the new one.', {okText: 'Make new secret'});
        if (!ok) return;
      }
      generate.disabled = true;
      status.textContent = 'Making a secret…';
      try {
        var data = await call('/settings/webhooks/generate', 'POST');
        value.value = data.secret || '';
        box.hidden = false;
        generate.dataset.replace = 'true';
        generate.textContent = 'Make a new secret';
        status.textContent = 'Secret saved. Copy it into the other app now.';
        value.focus(); value.select();
      } catch (failure) {
        status.textContent = errorText(failure, 'make a secret');
      } finally {
        generate.disabled = false;
      }
    });
    document.getElementById('webhook-copy').addEventListener('click', function () {
      if (value.value) window.mvCopy(value.value);
    });
  }

  // ---- Maintenance tab -------------------------------------------------------
  var base = '/api/v1/admin/system';
  function out(name) { return document.querySelector('[data-maintenance-out="' + name + '"]'); }
  function say(name, text) {
    var node = document.querySelector('[data-maintenance-status="' + name + '"]');
    if (node) node.textContent = text;
  }
  function when(ts) { return window.mvWhen ? window.mvWhen(ts) : ''; }

  var renderers = {
    readiness: function (data) {
      var list = out('readiness'); list.textContent = '';
      data.checks.forEach(function (row) {
        var li = el('li');
        li.appendChild(el('span', 'maintenance-label', row.label));
        li.appendChild(badge(row.ok, 'Ready', 'Not ready'));
        li.appendChild(el('span', 'muted', row.detail));
        if (row.fix_href) {
          var link = el('a', 'btn btn--ghost', row.fix_label);
          link.href = row.fix_href;
          if (row.fix_href.charAt(0) === '#') {
            link.addEventListener('click', function () {
              var target = document.getElementById(row.fix_href.slice(1));
              var panel = target && target.closest('.section-head');
              var button = panel && panel.nextElementSibling && panel.nextElementSibling.querySelector('[data-maintenance-run]');
              if (button) button.click();
            });
          }
          li.appendChild(link);
        }
        list.appendChild(li);
      });
      return data.ready ? 'Everything is ready.' : 'Some switches still need attention. Use the buttons to fix them.';
    },
    health: function (data) {
      var list = out('health'); list.textContent = '';
      data.technical.forEach(function (row) {
        var li = el('li');
        li.appendChild(badge(row.state === 'ok', 'OK', row.state === 'problem' ? 'Problem' : 'Could improve'));
        li.appendChild(el('span', 'maintenance-label', row.label));
        if (row.detail) li.appendChild(el('span', 'muted', row.detail));
        if (row.fix) li.appendChild(el('span', 'muted', row.fix));
        list.appendChild(li);
      });
      document.querySelector('[data-maintenance-details="health"]').hidden = !data.technical.length;
      return data.summary;
    },
    settings: function (data) {
      var list = out('settings'); list.textContent = '';
      data.technical.forEach(function (row) {
        var li = el('li');
        li.appendChild(badge(false, '', row.level === 'error' ? 'Cannot be used' : 'Suggestion'));
        li.appendChild(el('span', 'muted', row.text));
        list.appendChild(li);
      });
      document.querySelector('[data-maintenance-details="settings"]').hidden = !data.technical.length;
      return data.summary;
    },
    license: function (data) {
      var box = out('license'); box.textContent = '';
      var dl = el('dl', 'maintenance-facts');
      function fact(label, value) { if (!value) return; dl.appendChild(el('dt', null, label)); dl.appendChild(el('dd', null, value)); }
      fact('Status', data.status_label);
      fact('Plan', data.plan);
      fact('Licensed to', data.customer);
      fact('Renews or ends', data.expires ? new Date(data.expires + 'T00:00:00').toLocaleDateString() : '');
      fact('Add-ons', data.add_ons.join(', '));
      box.appendChild(dl);
      var list = el('ul', 'maintenance-list');
      data.features.forEach(function (f) {
        var li = el('li');
        li.appendChild(el('span', 'maintenance-label', f.label));
        li.appendChild(badge(f.included, 'Included', 'Not included'));
        list.appendChild(li);
      });
      box.appendChild(list);
      return data.summary;
    },
    queue: function (data) {
      var box = out('queue'); box.textContent = '';
      if (!data.counts.length) return 'There is no background work.';
      var summary = el('ul', 'maintenance-list');
      data.counts.forEach(function (c) {
        var li = el('li');
        li.appendChild(el('span', 'maintenance-label', c.state));
        li.appendChild(el('span', null, String(c.count)));
        summary.appendChild(li);
      });
      box.appendChild(summary);
      if (!data.stopped.length) return 'Nothing has stopped.';
      box.appendChild(el('h3', null, 'Stopped jobs'));
      var table = el('table');
      var head = el('tr');
      ['Job', 'Stopped', 'What to do'].forEach(function (h) { var th = el('th', null, h); th.scope = 'col'; head.appendChild(th); });
      var thead = el('thead'); thead.appendChild(head); table.appendChild(thead);
      var body = el('tbody');
      data.stopped.forEach(function (job) {
        var tr = el('tr');
        tr.appendChild(el('td', null, job.what));
        tr.appendChild(el('td', null, when(job.when)));
        var action = el('td');
        if (job.needs_decision) {
          var button = el('button', 'btn btn--primary', 'Resume schedule');
          button.type = 'button';
          button.addEventListener('click', async function () {
            var ok = await window.mvConfirm('Let this schedule run again from its next time? The run that could not be confirmed will not be repeated.', {okText: 'Resume schedule'});
            if (!ok) return;
            button.disabled = true;
            try {
              var response = await window.fetch(base + '/queue/' + encodeURIComponent(job.id) + '/resume', {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({attempts: job.attempts})
              });
              if (!response.ok) throw response;
              say('queue', 'The schedule will run again at its next time.');
              run('queue');
            } catch (failure) {
              say('queue', errorText(failure, 'resume the schedule'));
              button.disabled = false;
            }
          });
          action.appendChild(button);
        } else {
          action.appendChild(el('span', 'muted', 'Nothing to do. It will not run again.'));
        }
        if (job.technical) {
          var details = el('details');
          details.appendChild(el('summary', null, 'Technical details'));
          details.appendChild(el('p', 'muted', job.technical));
          action.appendChild(details);
        }
        tr.appendChild(action);
        body.appendChild(tr);
      });
      table.appendChild(body);
      box.appendChild(table);
      return data.stopped.length + ' job' + (data.stopped.length === 1 ? '' : 's') + ' stopped.';
    },
    encryption: function (data) {
      var box = out('encryption'); box.textContent = '';
      var p = el('p');
      p.appendChild(badge(data.enabled, 'Encrypted', 'Not encrypted'));
      p.appendChild(document.createTextNode(' ' + (data.enabled
        ? 'Saved records are encrypted on this computer.'
        : 'Saved records are not encrypted. Whoever installed Lightwork can turn encryption on.')));
      box.appendChild(p);
      if (!data.enabled) return '';
      var actions = el('p', 'settings-row-form');
      if (data.older_unprotected) {
        box.appendChild(el('p', null, data.older_unprotected + ' older record' + (data.older_unprotected === 1 ? ' was' : 's were') + ' saved before encryption was turned on.'));
        var protect = el('button', 'btn btn--primary', 'Encrypt older records');
        protect.type = 'button';
        protect.addEventListener('click', function () {
          mutate('encryption', '/encryption/protect-older',
            'Encrypt the older records now? This can take a few minutes. Keep a backup of your encryption key first.',
            'Encrypt records', function (res) { return res.protected + ' record' + (res.protected === 1 ? '' : 's') + ' encrypted.'; });
        });
        actions.appendChild(protect);
      } else if (data.older_note) {
        // Records kept in a shared database cannot be checked from here:
        // say so instead of claiming they are encrypted.
        box.appendChild(el('p', 'muted', data.older_note));
      } else if (data.older_unprotected === 0) {
        box.appendChild(el('p', 'muted', 'All older records are encrypted.'));
      }
      var rotate = el('button', 'btn', 'Make a new encryption key');
      rotate.type = 'button';
      rotate.addEventListener('click', function () {
        mutate('encryption', '/encryption/new-key',
          'Make a new encryption key? New records use it straight away. Older keys are kept so existing records stay readable.',
          'Make new key', function () { return 'New encryption key in use.'; });
      });
      actions.appendChild(rotate);
      box.appendChild(actions);
      return '';
    },
    diagnostics: function (data) {
      var box = out('diagnostics'); box.textContent = '';
      if (data.paused_services.length) {
        var list = el('ul', 'maintenance-list');
        data.paused_services.forEach(function (s) {
          var li = el('li');
          li.appendChild(el('span', 'maintenance-label', s.name));
          li.appendChild(badge(false, '', s.state));
          list.appendChild(li);
        });
        box.appendChild(list);
      }
      if (data.recent_calls.length) {
        var calls = el('ul', 'maintenance-list');
        data.recent_calls.forEach(function (c) {
          var li = el('li');
          li.appendChild(el('span', 'maintenance-label', c.provider));
          li.appendChild(el('span', 'muted', c.calls + ' recent request' + (c.calls === 1 ? '' : 's')));
          calls.appendChild(li);
        });
        box.appendChild(calls);
      }
      if (!data.paused_services.length && !data.recent_calls.length) return 'No AI services have been called since Lightwork started.';
      return data.paused_services.length ? 'Some services are paused after errors. Lightwork retries them automatically.' : 'All services are working.';
    }
  };
  var paths = {readiness: ['/readiness', 'GET'], health: ['/health-check', 'POST'], settings: ['/settings-check', 'GET'],
    license: ['/license', 'GET'], queue: ['/queue', 'GET'], encryption: ['/encryption', 'GET'], diagnostics: ['/diagnostics', 'GET']};
  var actions = {readiness: 'check readiness', health: 'run the health check', settings: 'check the installation settings',
    license: 'show the license', queue: 'show background work', encryption: 'check encryption', diagnostics: 'check services'};

  async function run(name) {
    var button = document.querySelector('[data-maintenance-run="' + name + '"]');
    if (button) button.disabled = true;
    say(name, 'Checking…');
    try {
      var data = await call(base + paths[name][0], paths[name][1]);
      say(name, renderers[name](data) || '');
    } catch (failure) {
      say(name, errorText(failure, actions[name]));
    } finally {
      if (button) button.disabled = false;
    }
  }
  async function mutate(name, path, question, okText, done) {
    var ok = await window.mvConfirm(question, {okText: okText});
    if (!ok) return;
    say(name, 'Working…');
    try {
      var result = await call(base + path, 'POST');
      await run(name);
      say(name, done(result));
    } catch (failure) {
      say(name, errorText(failure, okText.toLowerCase()));
    }
  }
  document.querySelectorAll('[data-maintenance-run]').forEach(function (button) {
    button.addEventListener('click', function () { run(button.dataset.maintenanceRun); });
  });
  window.lightworkSettingsMaintenance = {run: run};
})();
