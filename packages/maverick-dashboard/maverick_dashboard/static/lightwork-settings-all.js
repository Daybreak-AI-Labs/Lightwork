/* Settings > All settings: save, reset, search and module switches. */
(function () {
  'use strict';

  var API = '/api/v1/settings/catalog';
  var MODULES = '/api/v1/features/modules';
  var root = document.getElementById('settings-all-groups');
  if (!root) return;

  function errorText(failure, action) {
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.';
  }
  function rows(scope) {
    return Array.prototype.slice.call((scope || root).querySelectorAll('.settings-all-row[data-setting-id]'));
  }
  function rowById(id) {
    return rows().filter(function (row) { return row.dataset.settingId === id; })[0] || null;
  }
  function say(row, text) {
    var node = row && row.querySelector('[data-row-status]');
    if (node) node.textContent = text || '';
  }
  function announce(text) {
    var box = document.getElementById('settings-all-status');
    if (!box) return;
    box.textContent = text;
    box.hidden = !text;
  }

  // ---- reading a control ---------------------------------------------------
  function read(row) {
    var control = row.dataset.control;
    var input = row.querySelector('[data-input]');
    if (control === 'toggle') return input ? input.checked : null;
    if (control === 'select' || control === 'text') return input ? input.value : null;
    if (control === 'number') {
      if (!input) return null;
      var raw = input.value.trim();
      return raw === '' ? null : Number(raw);
    }
    if (control === 'multiselect') {
      return Array.prototype.slice.call(row.querySelectorAll('[data-choice]'))
        .filter(function (box) { return box.checked; })
        .map(function (box) { return box.value; });
    }
    if (control === 'list') {
      return Array.prototype.slice.call(row.querySelectorAll('[data-list-item]'))
        .map(function (item) { return item.value.trim(); })
        .filter(function (item) { return item; });
    }
    if (control === 'rate') {
      var count = row.querySelector('[data-rate-count]');
      var period = row.querySelector('[data-rate-period]');
      if (!count || count.value.trim() === '') return null;
      return {count: Number(count.value), seconds: Number(period.value)};
    }
    if (control === 'secret') {
      return input && !input.hidden ? input.value : null;
    }
    if (control === 'keys') {
      return Array.prototype.slice.call(row.querySelectorAll('[data-key-item]'))
        .map(function (item) { return item.getAttribute('data-key') || ''; });
    }
    if (control === 'table') {
      var kind = (row.querySelector('[data-table]') || {dataset: {}}).dataset.rowKind;
      return Array.prototype.slice.call(row.querySelectorAll('[data-table-row]')).map(function (tr) {
        if (kind === 'rate') {
          var cnt = tr.querySelector('[data-count]');
          var per = tr.querySelector('[data-period]');
          return {name: tr.dataset.name, count: cnt && cnt.value.trim() !== '' ? Number(cnt.value) : null,
                  seconds: per ? Number(per.value) : 60};
        }
        var risk = tr.querySelector('[data-risk]');
        return {name: tr.dataset.name, risk: risk ? risk.value : 'high'};
      });
    }
    return null;
  }
  function snapshot(row) { return JSON.stringify(read(row)); }
  function changed(row) {
    if (row.dataset.control === 'secret') {
      var input = row.querySelector('[data-input]');
      return !!(input && !input.hidden && input.value.trim());
    }
    return row.dataset.initial !== undefined && snapshot(row) !== row.dataset.initial;
  }
  function editable(row) { return !!row.querySelector('[data-save]'); }
  function remember(row) { row.dataset.initial = snapshot(row); }

  // ---- writing a control from the server's state ---------------------------
  function apply(row, data) {
    var control = row.dataset.control;
    var input = row.querySelector('[data-input]');
    if (control === 'toggle' && input) input.checked = data.value === true;
    else if ((control === 'select' || control === 'text') && input) input.value = data.value == null ? '' : String(data.value);
    else if (control === 'number' && input) input.value = data.value == null ? '' : String(data.value);
    else if (control === 'multiselect') {
      row.querySelectorAll('[data-choice]').forEach(function (box) {
        box.checked = (data.value || []).indexOf(box.value) !== -1;
      });
    } else if (control === 'list') {
      var list = row.querySelector('.settings-all-list-items');
      if (list) {
        list.textContent = '';
        (data.value || []).forEach(function (value) { addListItem(row, value); });
      }
    } else if (control === 'rate') {
      var count = row.querySelector('[data-rate-count]');
      var period = row.querySelector('[data-rate-period]');
      if (count) count.value = data.rate_count === '' || data.rate_count == null ? '' : String(data.rate_count);
      if (period && data.rate_seconds) period.value = String(data.rate_seconds);
    } else if (control === 'keys') {
      var keyList = row.querySelector('[data-key-list]');
      if (keyList) {
        keyList.textContent = '';
        (data.key_items || []).forEach(function (key) { addKeyItem(row, key.value, key.fingerprint); });
      }
      syncEmpty(row);
    } else if (control === 'table') {
      var body = row.querySelector('[data-table-body]');
      if (body) {
        body.textContent = '';
        (data.rows || []).forEach(function (r) { addTableRow(row, r, data); });
      }
      syncEmpty(row);
    } else if (control === 'secret') {
      var state = row.querySelector('[data-secret-state]');
      var replace = row.querySelector('[data-secret-replace]');
      if (state) {
        state.textContent = data.is_set ? 'Set' : 'Not set';
        state.className = 'badge ' + (data.is_set ? 'ok' : 'idle');
      }
      if (replace) replace.textContent = data.is_set ? 'Replace' : 'Add';
      if (input) { input.value = ''; input.hidden = true; }
    }
    var valueText = row.querySelector('[data-value-text]');
    if (valueText) valueText.textContent = data.value_text;
    var source = row.querySelector('[data-source-text]');
    if (source) {
      source.textContent = data.source_text;
      source.className = 'badge ' + (data.source === 'here' ? 'ok' : (data.source === 'deployment' ? 'warn' : 'idle'));
    }
    row.dataset.source = data.source;
    remember(row);
  }
  function applyAll(payload) {
    (payload.groups || []).forEach(function (group) {
      (group.settings || []).forEach(function (data) {
        var row = rowById(data.id);
        if (row) apply(row, data);
      });
    });
  }

  // ---- saving --------------------------------------------------------------
  async function send(changes, confirmed) {
    var response = await window.fetch(API, {
      method: 'POST', cache: 'no-store', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'},
      body: JSON.stringify({changes: changes, confirm: !!confirmed})
    });
    var data = null;
    try { data = await response.json(); } catch (ignore) { data = null; }
    return {response: response, data: data};
  }
  function warningText(list) {
    var lines = list.map(function (item) { return item.label + ': ' + item.warning; });
    return 'This lowers a protection. ' + lines.join(' ') + ' Do you want to go ahead?';
  }
  async function commit(changes, busyRows, action) {
    if (!changes.length) {
      busyRows.forEach(function (row) { say(row, 'Nothing to save.'); });
      return;
    }
    busyRows.forEach(function (row) {
      say(row, 'Saving…');
      row.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
    });
    try {
      var result = await send(changes, false);
      if (result.response.status === 409 && result.data && Array.isArray(result.data.confirm)) {
        var ok = await window.mvConfirm(warningText(result.data.confirm), {okText: 'Change anyway'});
        if (!ok) {
          busyRows.forEach(function (row) { say(row, 'Not changed.'); });
          return;
        }
        result = await send(changes, true);
      }
      if (!result.response.ok) {
        var message = result.data && typeof result.data.message === 'string'
          ? result.data.message : errorText(result.response, action);
        busyRows.forEach(function (row) { say(row, message); });
        if (window.mvToast) window.mvToast(message, {error: true});
        return;
      }
      applyAll(result.data || {});
      var done = (result.data.changed || []).length;
      var text = done ? 'Saved.' : 'Nothing changed.';
      if (result.data.restart) text += ' Some changes take effect after Lightwork restarts.';
      busyRows.forEach(function (row) { say(row, text); });
      announce(done ? (done === 1 ? '1 setting saved.' : done + ' settings saved.') + (result.data.restart ? ' Some changes take effect after Lightwork restarts.' : '') : '');
      updateFilter();
    } catch (failure) {
      var text2 = errorText(failure, action);
      busyRows.forEach(function (row) { say(row, text2); });
    } finally {
      busyRows.forEach(function (row) {
        row.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
      });
    }
  }

  function saveRows(list) {
    var pending = list.filter(function (row) { return editable(row) && changed(row); });
    var changes = pending.map(function (row) { return {id: row.dataset.settingId, value: read(row)}; });
    if (!pending.length) {
      list.forEach(function (row) { if (editable(row)) say(row, ''); });
      announce('There are no changes to save in this group.');
      return Promise.resolve();
    }
    return commit(changes, pending, 'save the setting');
  }

  root.addEventListener('click', async function (event) {
    var target = event.target.closest('button');
    if (!target) return;
    var row = target.closest('.settings-all-row');
    if (target.hasAttribute('data-save') && row) {
      var single = row.dataset.control === 'secret' && !changed(row)
        ? [] : [{id: row.dataset.settingId, value: read(row)}];
      if (!single.length) { say(row, 'Choose Replace and enter a value first.'); return; }
      await commit(single, [row], 'save the setting');
    } else if (target.hasAttribute('data-reset') && row) {
      if (row.dataset.source === 'default' && !changed(row)) {
        say(row, 'Already using the default.');
        return;
      }
      await commit([{id: row.dataset.settingId, reset: true}], [row], 'reset the setting');
    } else if (target.dataset.saveGroup) {
      await saveRows(rows(target.closest('.settings-all-group')));
    } else if (target.dataset.saveSubgroup) {
      var part = target.dataset.saveSubgroup;
      await saveRows(rows(target.closest('.settings-all-group')).filter(function (r) {
        return r.dataset.subgroup === part;
      }));
    } else if (target.hasAttribute('data-key-remove') && row) {
      var keyItem = target.closest('li');
      if (keyItem) keyItem.remove();
      syncEmpty(row);
      say(row, 'Removed from the list. Choose Save to stop trusting this publisher.');
      var fileBox = row.querySelector('[data-key-file]');
      if (fileBox) fileBox.focus();
    } else if (target.hasAttribute('data-key-add') && row) {
      var paste = row.querySelector('[data-key-paste]');
      var text = paste ? paste.value.trim() : '';
      if (!text) { say(row, 'Paste the publisher key first.'); return; }
      addKeyItem(row, text, '');
      if (paste) paste.value = '';
      syncEmpty(row);
      say(row, 'Key added to the list. Choose Save to trust this publisher.');
    } else if (target.hasAttribute('data-table-remove') && row) {
      var tr = target.closest('tr');
      if (tr) tr.remove();
      syncEmpty(row);
      var focusTo = row.querySelector('[data-table-search], [data-table-newname]');
      if (focusTo) focusTo.focus();
    } else if (target.hasAttribute('data-table-add') && row) {
      addFromTableForm(row);
    } else if (target.dataset.resetGroup) {
      var group = target.closest('.settings-all-group');
      var name = group.querySelector('h2') ? group.querySelector('h2').textContent : 'this group';
      var list = rows(group).filter(function (r) { return editable(r) && r.dataset.source !== 'default'; });
      if (!list.length) { announce('Everything in ' + name + ' already uses its default.'); return; }
      var ok = await window.mvConfirm('Put every setting in ' + name + ' back to its default?', {okText: 'Reset all'});
      if (!ok) return;
      await commit(list.map(function (r) { return {id: r.dataset.settingId, reset: true}; }), list, 'reset the settings');
    } else if (target.hasAttribute('data-secret-replace') && row) {
      var input = row.querySelector('[data-input]');
      if (input) { input.hidden = false; input.focus(); }
    } else if (target.hasAttribute('data-list-add') && row) {
      var added = addListItem(row, '');
      if (added) added.focus();
    } else if (target.hasAttribute('data-list-remove')) {
      var item = target.closest('li');
      var owner = item && item.closest('.settings-all-row');
      if (item) item.remove();
      if (owner) { var add = owner.querySelector('[data-list-add]'); if (add) add.focus(); }
    }
  });

  function safeId(row) { return 'sa-' + row.dataset.settingId.replace(/[^A-Za-z0-9_-]/g, '-'); }
  function addListItem(row, value) {
    var list = row.querySelector('.settings-all-list-items');
    if (!list) return null;
    var box = row.querySelector('[data-list]');
    var noun = (box && box.dataset.itemLabel) || 'Address';
    var index = list.children.length + 1;
    var li = document.createElement('li');
    var id = safeId(row) + '-item-new-' + index + '-' + Date.now();
    var label = document.createElement('label');
    label.className = 'sr-only';
    label.htmlFor = id;
    label.textContent = noun + ' ' + index;
    var input = document.createElement('input');
    input.className = 'input';
    input.type = 'text';
    input.id = id;
    input.maxLength = Number((box && box.dataset.maxLength) || 300);
    input.placeholder = (box && box.dataset.itemHint) || '';
    input.setAttribute('data-list-item', '');
    input.value = value || '';
    var remove = document.createElement('button');
    remove.className = 'btn btn--ghost';
    remove.type = 'button';
    remove.textContent = 'Remove';
    remove.setAttribute('data-list-remove', '');
    li.appendChild(label); li.appendChild(input); li.appendChild(remove);
    list.appendChild(li);
    return input;
  }

  // ---- publisher keys --------------------------------------------------------
  function addKeyItem(row, value, fingerprint) {
    var list = row.querySelector('[data-key-list]');
    if (!list) return null;
    var li = document.createElement('li');
    li.setAttribute('data-key-item', '');
    li.setAttribute('data-key', value);
    var text = document.createElement('span');
    if (fingerprint) {
      text.appendChild(document.createTextNode('Publisher key, fingerprint '));
      var strong = document.createElement('strong');
      strong.textContent = fingerprint;
      text.appendChild(strong);
    } else {
      text.textContent = 'New publisher key (its fingerprint is shown after saving)';
    }
    var remove = document.createElement('button');
    remove.className = 'btn btn--ghost';
    remove.type = 'button';
    remove.textContent = 'Remove';
    remove.setAttribute('data-key-remove', '');
    li.appendChild(text); li.appendChild(remove);
    list.appendChild(li);
    return li;
  }
  function syncEmpty(row) {
    var keysEmpty = row.querySelector('[data-keys-empty]');
    if (keysEmpty) keysEmpty.hidden = !!row.querySelector('[data-key-item]');
    var tableEmpty = row.querySelector('[data-table-empty]');
    if (tableEmpty) tableEmpty.hidden = !!row.querySelector('[data-table-row]');
  }
  root.addEventListener('change', async function (event) {
    var file = event.target.closest('[data-key-file]');
    if (!file || !file.files || !file.files.length) return;
    var row = file.closest('.settings-all-row');
    var chosen = file.files[0];
    if (chosen.size > 4096) {
      say(row, 'That file is too large to be a publisher key. Ask the publisher for their public key file.');
      file.value = '';
      return;
    }
    try {
      var text = await chosen.text();
      addKeyItem(row, text.trim(), '');
      syncEmpty(row);
      say(row, 'Key added to the list. Choose Save to trust this publisher.');
    } catch (failure) {
      say(row, 'That file could not be read. Try again or paste the key instead.');
    }
    file.value = '';
  });

  // ---- tables (usage limits, reviewed tools) -------------------------------
  function periodOptions(select, periods, seconds) {
    (periods || [{value: 60, label: 'Every minute'}, {value: 3600, label: 'Every hour'},
                 {value: 86400, label: 'Every day'}]).forEach(function (p) {
      var option = document.createElement('option');
      option.value = String(p.value);
      option.textContent = p.label;
      if (Number(p.value) === Number(seconds)) option.selected = true;
      select.appendChild(option);
    });
  }
  function riskOptions(select, risks, value) {
    (risks || [{value: 'low', label: 'Low risk'}, {value: 'medium', label: 'Medium risk'},
               {value: 'high', label: 'High risk'}]).forEach(function (k) {
      var option = document.createElement('option');
      option.value = k.value;
      option.textContent = k.label;
      if (k.value === value) option.selected = true;
      select.appendChild(option);
    });
  }
  function addTableRow(row, r, data) {
    var body = row.querySelector('[data-table-body]');
    var kind = row.querySelector('[data-table]').dataset.rowKind;
    if (!body) return null;
    var tr = document.createElement('tr');
    tr.setAttribute('data-table-row', '');
    tr.dataset.name = r.name;
    var name = document.createElement('td');
    name.setAttribute('data-row-label', '');
    name.textContent = r.label || r.name;
    tr.appendChild(name);
    if (kind === 'rate') {
      var countCell = document.createElement('td');
      var count = document.createElement('input');
      count.className = 'input'; count.type = 'number'; count.min = '1'; count.max = '100000'; count.step = '1';
      count.setAttribute('data-count', '');
      count.setAttribute('aria-label', 'How many times for ' + (r.label || r.name));
      count.value = r.count == null ? '' : String(r.count);
      countCell.appendChild(count);
      var periodCell = document.createElement('td');
      var period = document.createElement('select');
      period.className = 'select';
      period.setAttribute('data-period', '');
      period.setAttribute('aria-label', 'How often for ' + (r.label || r.name));
      periodOptions(period, data && data.periods, r.seconds || 60);
      periodCell.appendChild(period);
      tr.appendChild(countCell); tr.appendChild(periodCell);
    } else {
      var riskCell = document.createElement('td');
      var risk = document.createElement('select');
      risk.className = 'select';
      risk.setAttribute('data-risk', '');
      risk.setAttribute('aria-label', 'Risk level for ' + (r.label || r.name));
      riskOptions(risk, data && data.risks, r.risk || 'high');
      riskCell.appendChild(risk);
      tr.appendChild(riskCell);
    }
    var removeCell = document.createElement('td');
    var remove = document.createElement('button');
    remove.className = 'btn btn--ghost'; remove.type = 'button'; remove.textContent = 'Remove';
    remove.setAttribute('data-table-remove', '');
    removeCell.appendChild(remove);
    tr.appendChild(removeCell);
    body.appendChild(tr);
    return tr;
  }
  function hasRow(row, name) {
    return Array.prototype.slice.call(row.querySelectorAll('[data-table-row]'))
      .some(function (tr) { return tr.dataset.name === name; });
  }
  function addFromTableForm(row) {
    var kind = row.querySelector('[data-table]').dataset.rowKind;
    if (kind === 'rate') {
      var pick = row.querySelector('[data-table-pick]');
      var chosen = pick && pick.value;
      if (!chosen) { say(row, 'Find and choose a tool first.'); return; }
      if (hasRow(row, chosen)) { say(row, 'That tool already has a limit. Change it in the list.'); return; }
      var label = pick.options[pick.selectedIndex].textContent;
      var tr = addTableRow(row, {name: chosen, label: label, count: 10, seconds: 60}, null);
      syncEmpty(row);
      say(row, 'Limit added. Set how many and how often, then choose Save.');
      if (tr) tr.querySelector('[data-count]').focus();
      return;
    }
    var nameBox = row.querySelector('[data-table-newname]');
    var riskBox = row.querySelector('[data-table-newrisk]');
    var name = nameBox ? nameBox.value.trim() : '';
    if (!name) { say(row, 'Type the tool name first.'); return; }
    if (!/^[A-Za-z0-9_.-]{1,128}$/.test(name) || name.indexOf('__') !== -1) {
      say(row, 'A tool name uses letters, numbers, dots, dashes and single underscores only.');
      return;
    }
    if (hasRow(row, name)) { say(row, 'That tool is already in the list.'); return; }
    addTableRow(row, {name: name, label: name, risk: riskBox ? riskBox.value : 'high'}, null);
    nameBox.value = '';
    syncEmpty(row);
    say(row, 'Tool added. Choose Save to offer it.');
  }
  var loadedChoices = {};
  async function loadChoices(row) {
    var box = row.querySelector('[data-table]');
    var url = box && box.dataset.choicesUrl;
    if (!url) return [];
    if (loadedChoices[url]) return loadedChoices[url];
    try {
      var response = await window.fetch(url, {cache: 'no-store', credentials: 'same-origin',
                                              headers: {Accept: 'application/json'}});
      if (!response.ok) throw response;
      var data = await response.json();
      loadedChoices[url] = data.choices || [];
    } catch (failure) {
      say(row, errorText(failure, 'load the list of tools'));
      return [];
    }
    return loadedChoices[url];
  }
  async function filterChoices(row) {
    var search = row.querySelector('[data-table-search]');
    var pick = row.querySelector('[data-table-pick]');
    if (!search || !pick) return;
    var words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    var choices = await loadChoices(row);
    var matches = choices.filter(function (c) {
      var text = (c.label + ' ' + c.value).toLowerCase();
      return words.every(function (w) { return text.indexOf(w) !== -1; });
    });
    pick.textContent = '';
    var first = document.createElement('option');
    first.value = '';
    first.textContent = matches.length
      ? (matches.length > 50 ? 'Showing 50 of ' + matches.length + ' tools: search to narrow' : 'Choose a tool')
      : 'No tools match that search';
    pick.appendChild(first);
    matches.slice(0, 50).forEach(function (c) {
      var option = document.createElement('option');
      option.value = c.value;
      option.textContent = c.label;
      pick.appendChild(option);
    });
  }
  root.addEventListener('input', function (event) {
    var search = event.target.closest('[data-table-search]');
    if (search) filterChoices(search.closest('.settings-all-row'));
  });
  root.addEventListener('focusin', function (event) {
    var pick = event.target.closest('[data-table-pick], [data-table-search]');
    if (pick && pick.closest('.settings-all-row').querySelector('[data-table-pick]').options.length <= 1) {
      filterChoices(pick.closest('.settings-all-row'));
    }
  });

  // ---- module switches -----------------------------------------------------
  root.addEventListener('change', async function (event) {
    var toggle = event.target.closest('[data-module-toggle]');
    if (!toggle) return;
    var row = toggle.closest('.settings-all-row');
    var label = row.querySelector('.settings-all-label').textContent;
    var on = toggle.checked;
    if (!on) {
      var ok = await window.mvConfirm('Turn off ' + label + '? Its pages stop working until it is turned on again.', {okText: 'Turn off'});
      if (!ok) { toggle.checked = true; return; }
    }
    toggle.disabled = true;
    say(row, on ? 'Turning on…' : 'Turning off…');
    try {
      var response = await window.fetch(MODULES, {
        method: 'POST', cache: 'no-store', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', Accept: 'application/json'},
        body: JSON.stringify({section: row.dataset.module, enabled: on})
      });
      if (!response.ok) throw response;
      var data = await response.json();
      toggle.checked = data.enabled === true;
      row.querySelector('[data-value-text]').textContent = data.enabled ? 'On' : 'Off';
      say(row, data.enabled ? 'Turned on.' : 'Turned off.');
    } catch (failure) {
      toggle.checked = !on;
      say(row, errorText(failure, on ? 'turn this on' : 'turn this off'));
    } finally {
      toggle.disabled = false;
    }
  });

  // ---- search and filters --------------------------------------------------
  var search = document.getElementById('settings-all-search');
  var groupPick = document.getElementById('settings-all-group');
  var changedOnly = document.getElementById('settings-all-changed');
  var status = document.getElementById('settings-all-filter-status');
  var empty = document.getElementById('settings-all-empty');
  function updateFilter() {
    var words = (search && search.value || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    var groupWanted = groupPick ? groupPick.value : '';
    var onlyChanged = changedOnly && changedOnly.checked;
    var shown = 0;
    root.querySelectorAll('.settings-all-group').forEach(function (group) {
      var inGroup = !groupWanted || group.dataset.group === groupWanted;
      var partPick = group.querySelector('[data-subgroup-pick]');
      var part = partPick ? partPick.value : '';
      var visible = 0;
      var partsShown = {};
      group.querySelectorAll('.settings-all-row').forEach(function (row) {
        var text = row.dataset.search || '';
        var match = inGroup && words.every(function (w) { return text.indexOf(w) !== -1; });
        if (match && part) match = row.dataset.subgroup === part;
        if (match && onlyChanged) match = !!row.dataset.settingId && row.dataset.source === 'here';
        row.hidden = !match;
        if (match) { visible += 1; if (row.dataset.subgroup) partsShown[row.dataset.subgroup] = true; }
      });
      group.querySelectorAll('[data-subgroup-head]').forEach(function (head) {
        head.hidden = !partsShown[head.dataset.subgroupHead];
      });
      group.querySelectorAll('[data-subgroup-actions]').forEach(function (actions) {
        actions.hidden = !partsShown[actions.dataset.subgroupActions];
      });
      group.hidden = visible === 0;
      shown += visible;
    });
    var filtering = words.length || groupWanted || onlyChanged;
    if (status) status.textContent = filtering ? (shown === 1 ? '1 setting shown.' : shown + ' settings shown.') : '';
    if (empty) empty.hidden = shown !== 0;
  }
  [search, groupPick, changedOnly].forEach(function (node) {
    if (node) node.addEventListener(node === search ? 'input' : 'change', updateFilter);
  });
  root.querySelectorAll('[data-subgroup-pick]').forEach(function (node) {
    node.addEventListener('change', updateFilter);
  });

  rows().forEach(remember);
  updateFilter();
})();
