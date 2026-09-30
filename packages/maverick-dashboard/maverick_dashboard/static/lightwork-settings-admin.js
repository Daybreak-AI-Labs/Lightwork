/* Settings: confirmations and the Maintenance tab. */
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
  // Technical rows (kernel wording) stay inside the collapsed details.
  function technical(name, rows) {
    var list = document.querySelector('[data-maintenance-technical="' + name + '"]');
    var details = document.querySelector('[data-maintenance-details="' + name + '"]');
    if (!list || !details) return;
    list.textContent = '';
    (rows || []).forEach(function (row) { list.appendChild(el('li', 'muted', row.text)); });
    details.hidden = !(rows && rows.length);
  }
  function plainList(name, items) {
    var list = out(name); list.textContent = '';
    items.forEach(function (item) {
      var li = el('li');
      li.appendChild(el('span', 'maintenance-label', item.label));
      li.appendChild(badge(item.ok, item.yes || 'Yes', item.no || 'No'));
      list.appendChild(li);
    });
  }
  renderers.enterprise = function (data) {
    plainList('enterprise', data.checks.map(function (c) { return {label: c.label, ok: c.ok, yes: 'In place', no: 'Not in place'}; }));
    technical('enterprise', data.technical);
    return data.summary;
  };
  renderers.offline = function (data) {
    plainList('offline', data.reasons.map(function (r) { return {label: r, ok: false, no: 'Can send data out'}; }));
    technical('offline', data.technical);
    return data.summary;
  };
  renderers.hardware = function (data) {
    plainList('hardware', []);
    technical('hardware', data.technical);
    return data.summary;
  };
  renderers.plugins = function (data) {
    plainList('plugins', data.problems.map(function (p) { return {label: p, ok: false, no: 'Does not match'}; }));
    technical('plugins', data.technical);
    return data.summary;
  };
  function saveBlob(blob, name) {
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  }
  async function plainFailure(failure, action) {
    // A server sentence written for people is shown as is; anything else
    // falls back to the shared plain message.
    try {
      if (failure && typeof failure.clone === 'function') {
        var body = await failure.clone().json();
        var plain = window.mvPlainDetail ? window.mvPlainDetail(body && body.detail) : '';
        if (plain) return plain;
      }
    } catch (ignored) { /* not a readable body */ }
    return errorText(failure, action);
  }
  function waitForExport(job, done) {
    // The file is prepared in the background; ask for its status until it is
    // ready, then offer the download link.
    var tries = 0;
    async function poll() {
      tries += 1;
      try {
        var state = await call(base + '/client/export/' + encodeURIComponent(job), 'GET');
        if (state.state === 'ready') { done(null, base + '/client/export/' + encodeURIComponent(job) + '/download'); return; }
        if (state.state === 'failed') { done(state.message || 'The export could not be prepared.'); return; }
      } catch (failure) { done(await plainFailure(failure, 'prepare the download')); return; }
      if (tries > 400) { done('The export is taking too long. Try again later.'); return; }
      setTimeout(poll, 3000);
    }
    setTimeout(poll, 500);
  }
  function areaList(preview) {
    var list = el('ul', 'maintenance-list');
    preview.areas.forEach(function (area) {
      var li = el('li');
      li.appendChild(el('span', 'maintenance-label', area.label));
      li.appendChild(el('span', null, area.files + ' file' + (area.files === 1 ? '' : 's')));
      list.appendChild(li);
    });
    return list;
  }
  renderers.client = function (data) {
    var box = out('client'); box.textContent = '';
    technical('client', data.technical);
    var row = el('p', 'settings-row-form');
    var download = el('button', 'btn btn--primary', 'Download all data');
    download.type = 'button';
    var ready = el('p', 'settings-row-form');
    download.addEventListener('click', async function () {
      var ok = await window.mvConfirm('Download a copy of all of this organisation\'s data? The file holds everything Lightwork keeps, so store it somewhere safe. Encryption keys are not included.', {okText: 'Download'});
      if (!ok) return;
      download.disabled = true;
      ready.textContent = '';
      say('client', 'Preparing the download. This can take a few minutes; you can keep working.');
      try {
        var started = await call(base + '/client/export', 'POST');
        waitForExport(started.job, function (problem, href) {
          download.disabled = false;
          if (problem) { say('client', problem); return; }
          var link = el('a', 'btn btn--primary', 'Save the file');
          link.href = href;
          link.setAttribute('download', '');
          ready.appendChild(link);
          say('client', 'Your download is ready. It has been added to the activity record.');
        });
      } catch (failure) {
        say('client', await plainFailure(failure, 'download the data'));
        download.disabled = false;
      }
    });
    row.appendChild(download);
    if (data.bound) {
      var keepLabel = el('label', 'settings-field');
      var keep = el('input'); keep.type = 'checkbox'; keep.checked = true; keep.name = 'keep_activity_record';
      var keepRow = el('span'); keepRow.appendChild(keep);
      keepRow.appendChild(document.createTextNode(' Keep the activity record (needed if the law requires you to keep it)'));
      keepLabel.appendChild(keepRow);
      var erase = el('button', 'btn btn--danger', 'Erase all data');
      erase.type = 'button';
      var previewBox = el('div');
      erase.addEventListener('click', async function () {
        erase.disabled = true;
        previewBox.textContent = '';
        say('client', 'Counting what would be erased…');
        var preview;
        try {
          preview = await call(base + '/client/erase/preview?keep_activity_record=' + (keep.checked ? 'true' : 'false'), 'GET');
        } catch (failure) {
          say('client', await plainFailure(failure, 'count what would be erased'));
          erase.disabled = false;
          return;
        }
        previewBox.appendChild(el('p', null, 'Erasing removes ' + preview.files + ' file' + (preview.files === 1 ? '' : 's') + ':'));
        previewBox.appendChild(areaList(preview));
        say('client', preview.keeps_activity_record ? 'The activity record is kept.' : 'The activity record is erased too.');
        var ok = await window.mvConfirm('Erase all data for ' + data.organisation + '? Every task, conversation, lesson and saved file for this organisation is deleted for good and cannot be brought back. Download a copy first if you may need it.', {okText: 'Erase everything'});
        if (!ok) { erase.disabled = false; return; }
        var typed = window.mvForm ? await window.mvForm('Type the organisation name to confirm', [
          {key: 'organisation', label: 'Organisation name (' + data.organisation + ')', type: 'text', required: true}],
          {okText: 'Erase everything'}) : {organisation: data.organisation};
        if (!typed) { erase.disabled = false; return; }
        if (String(typed.organisation || '').trim() !== data.organisation) {
          say('client', 'The name you typed does not match ' + data.organisation + '. Nothing was erased.');
          erase.disabled = false;
          return;
        }
        say('client', 'Erasing…');
        try {
          var response = await window.fetch(base + '/client/erase', {
            method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({organisation: String(typed.organisation).trim(), keep_activity_record: keep.checked, confirm: true})
          });
          if (!response.ok) throw response;
          var result = await response.json();
          say('client', 'All data was erased (' + result.files_removed + ' file' + (result.files_removed === 1 ? '' : 's') + ' removed).');
        } catch (failure) {
          say('client', await plainFailure(failure, 'erase the data'));
          erase.disabled = false;
        }
      });
      row.appendChild(erase);
      box.appendChild(row);
      box.appendChild(keepLabel);
      box.appendChild(previewBox);
    } else {
      box.appendChild(row);
    }
    box.appendChild(ready);
    return data.summary;
  };
  var paths = {readiness: ['/readiness', 'GET'], health: ['/health-check', 'POST'], settings: ['/settings-check', 'GET'],
    license: ['/license', 'GET'], queue: ['/queue', 'GET'], encryption: ['/encryption', 'GET'], diagnostics: ['/diagnostics', 'GET'],
    enterprise: ['/enterprise-check', 'GET'], offline: ['/offline-check', 'GET'], hardware: ['/hardware-check', 'GET'],
    plugins: ['/plugin-check', 'GET'], client: ['/client', 'GET']};
  var actions = {readiness: 'check readiness', health: 'run the health check', settings: 'check the installation settings',
    license: 'show the license', queue: 'show background work', encryption: 'check encryption', diagnostics: 'check services',
    enterprise: 'check the protections', offline: 'check connections', hardware: 'check the hardware',
    plugins: 'check add-ons', client: 'show the data status'};

  // Tidy up old records: preview the counts first, then confirm.
  var cleanup = document.querySelector('[data-maintenance-cleanup]');
  if (cleanup) {
    cleanup.addEventListener('submit', async function (event) {
      event.preventDefault();
      var days = Number(cleanup.elements.days.value);
      var activityDays = Number(cleanup.elements.activity_days.value);
      var box = out('cleanup'); box.textContent = '';
      say('cleanup', 'Counting…');
      try {
        var preview = await call(base + '/cleanup/preview?days=' + days + '&activity_days=' + activityDays, 'GET');
        var conversations = preview.conversations;
        var list = el('ul', 'maintenance-list');
        var li1 = el('li'); li1.appendChild(el('span', 'maintenance-label', 'Conversations'));
        li1.appendChild(el('span', null, conversations === null ? 'Counted when you remove them' : String(conversations)));
        var li2 = el('li'); li2.appendChild(el('span', 'maintenance-label', 'Task activity entries'));
        li2.appendChild(el('span', null, String(preview.activity)));
        list.appendChild(li1); list.appendChild(li2);
        box.appendChild(list);
        if (conversations === 0 && preview.activity === 0) { say('cleanup', 'Nothing is old enough to remove.'); return; }
        say('cleanup', 'This is what would be removed. Nothing has been removed yet.');
        var go = el('button', 'btn btn--danger', 'Remove these records');
        go.type = 'button';
        go.addEventListener('click', async function () {
          var ok = await window.mvConfirm('Remove these old records from this workspace? They cannot be brought back.', {okText: 'Remove'});
          if (!ok) return;
          go.disabled = true;
          say('cleanup', 'Removing…');
          try {
            var response = await window.fetch(base + '/cleanup', {
              method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({days: days, activity_days: activityDays, confirm: true})
            });
            if (!response.ok) throw response;
            var done = await response.json();
            box.textContent = '';
            say('cleanup', 'Removed ' + done.conversations + ' conversation' + (done.conversations === 1 ? '' : 's') +
              ' and ' + done.activity + ' activity entr' + (done.activity === 1 ? 'y' : 'ies') + '.');
          } catch (failure) {
            say('cleanup', await plainFailure(failure, 'remove the records'));
            go.disabled = false;
          }
        });
        box.appendChild(go);
      } catch (failure) {
        say('cleanup', await plainFailure(failure, 'count old records'));
      }
    });
  }

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
