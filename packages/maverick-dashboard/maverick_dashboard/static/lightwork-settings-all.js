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
    if (control === 'text' && row.querySelector('[data-segment-at]')) return segmentAddress(row);
    if (control === 'select' || control === 'text' || control === 'path' || control === 'fingerprint') {
      return input ? input.value : null;
    }
    if (control === 'picks') {
      return Array.prototype.slice.call(row.querySelectorAll('[data-picks] [data-pick-item]'))
        .map(function (li) { return li.getAttribute('data-value'); });
    }
    if (control === 'document') return row.pendingDocument || null;
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
    if (row.querySelector('[data-grid]')) return readGrid(row);
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
    if (row.dataset.control === 'document') return !!row.pendingDocument;
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
    else if ((control === 'select' || control === 'text' || control === 'path' || control === 'fingerprint') && input) {
      input.value = data.value == null ? '' : String(data.value);
      var at = row.querySelector('[data-segment-at]');
      if (at) { at.setAttribute('data-at', String(data.segment_at == null ? -1 : data.segment_at)); segmentOptions(row); }
      var printed = row.querySelector('[data-fingerprint-shown]');
      if (printed) printed.textContent = data.value_text;
      var preview = row.querySelector('[data-preview]');
      if (preview) preview.textContent = '';
    }
    else if (control === 'picks') {
      var chips = row.querySelector('[data-picks] [data-pick-list]');
      if (chips) {
        chips.textContent = '';
        (data.picks || []).forEach(function (p) { addChip(row.querySelector('[data-picks]'), p.value, p.label); });
      }
      syncPicks(row);
    } else if (control === 'document') {
      row.pendingDocument = null;
      showDocument(row, data.document);
    }
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
    } else if (data.grid && row.querySelector('[data-grid]')) {
      buildGrid(row, data.grid);
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
    var access = list.some(function (item) { return item.kind === 'access'; });
    var learning = list.some(function (item) { return item.kind === 'learning'; });
    var lowers = list.some(function (item) { return item.kind !== 'access' && item.kind !== 'learning'; });
    var lead = lowers ? 'This lowers a protection. ' : '';
    if (access) lead += 'This changes how people get into Lightwork. ';
    if (learning) lead += 'This turns on learning systems. ';
    return lead + lines.join(' ') + ' Do you want to go ahead?';
  }

  // ---- "Test connection" / "Check this location" ------------------------------
  function checkValue(row) {
    var control = row.dataset.control;
    var input = row.querySelector('[data-input]');
    if (control === 'secret') {
      return input && !input.hidden && input.value.trim() ? input.value : null;
    }
    if (control === 'text' || control === 'path' || control === 'select') {
      var value = read(row);
      return value && String(value).trim() ? value : null;
    }
    return null;  // other controls: check what is saved now
  }
  async function runCheck(row, button) {
    say(row, 'Checking…');
    button.disabled = true;
    try {
      var response = await window.fetch(API + '/test', {
        method: 'POST', cache: 'no-store', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', Accept: 'application/json'},
        body: JSON.stringify({id: row.dataset.settingId, value: checkValue(row)})
      });
      var data = null;
      try { data = await response.json(); } catch (ignore) { data = null; }
      say(row, data && typeof data.message === 'string' ? data.message : errorText(response, 'run the check'));
    } catch (failure) {
      say(row, errorText(failure, 'run the check'));
    } finally {
      button.disabled = false;
    }
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
      if (result.response.status === 202 && typeof result.data.message === 'string') {
        // Held for approval by other administrators: nothing was saved yet.
        busyRows.forEach(function (row) { say(row, result.data.message); });
        announce(result.data.message);
        updateFilter();
        return;
      }
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
      var needsNew = row.dataset.control === 'secret' || row.dataset.control === 'document';
      var single = needsNew && !changed(row)
        ? [] : [{id: row.dataset.settingId, value: read(row)}];
      if (!single.length) {
        say(row, row.dataset.control === 'document' ? 'Choose the file to import first.'
          : 'Choose Replace and enter a value first.');
        return;
      }
      await commit(single, [row], 'save the setting');
    } else if (target.hasAttribute('data-test') && row) {
      await runCheck(row, target);
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
    } else if (target.hasAttribute('data-grid-add') && row) {
      addFromGridForm(row);
    } else if (target.hasAttribute('data-grid-remove') && row) {
      var gridTr = target.closest('tr');
      if (gridTr) gridTr.remove();
      syncGrid(row);
      var addControl = row.querySelector('[data-grid-newkey], [data-grid-add]');
      if (addControl) addControl.focus();
      say(row, 'Removed from the list. Choose Save to keep this change.');
    } else if (target.hasAttribute('data-pick-add') && row) {
      addPick(row, target.closest('td, [data-picks]'));
      syncPicks(row);
    } else if (target.hasAttribute('data-pick-remove') && row) {
      var pickCell = target.closest('td, [data-picks]');
      var pickItem = target.closest('li');
      if (pickItem) pickItem.remove();
      syncPicks(row);
      var pickSelect = pickCell && pickCell.querySelector('[data-pick-select]');
      if (pickSelect) pickSelect.focus();
    } else if (target.hasAttribute('data-fingerprint-server') && row) {
      await fingerprintFrom(row, null, target);
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
    var pickSearch = event.target.closest('[data-pick-search]');
    if (pickSearch) filterPick(pickSearch.closest('.settings-all-row'), pickSearch.closest('td, [data-picks]'));
    var keySearch = event.target.closest('[data-grid-keysearch]');
    if (keySearch) filterGridKeys(keySearch.closest('.settings-all-row'));
    var address = event.target.closest('.settings-all-row[data-control="text"] [data-input]');
    if (address && address.closest('.settings-all-row').querySelector('[data-segment-at]')) {
      segmentOptions(address.closest('.settings-all-row'));
    }
  });
  root.addEventListener('focusin', function (event) {
    var pick = event.target.closest('[data-table-pick], [data-table-search]');
    if (pick && pick.closest('.settings-all-row').querySelector('[data-table-pick]').options.length <= 1) {
      filterChoices(pick.closest('.settings-all-row'));
    }
    var cellPick = event.target.closest('[data-pick-search], [data-pick-select]');
    var cell = cellPick && cellPick.closest('td, [data-picks]');
    if (cell && !cell.dataset.loaded) {
      cell.dataset.loaded = '1';
      filterPick(cellPick.closest('.settings-all-row'), cell);
    }
    var keyPick = event.target.closest('[data-grid-keysearch], [data-grid-newkey]');
    var keyRow = keyPick && keyPick.closest('.settings-all-row');
    if (keyRow && keyRow.querySelector('[data-grid-keysearch]') && !keyRow.dataset.keysLoaded) {
      keyRow.dataset.keysLoaded = '1';
      filterGridKeys(keyRow);
    }
  });

  // ---- grids and rules (tools per chat app, spending per service, rules) ---
  var columnChoiceCache = {};
  function gridState(row) {
    var box = row.querySelector('[data-grid]');
    if (!box) return null;
    if (!box.gridState) {
      try { box.gridState = JSON.parse(box.getAttribute('data-grid-state') || '{}'); } catch (ignore) { box.gridState = {}; }
    }
    box.gridState.columns = box.gridState.columns || [];
    return box.gridState;
  }
  function columnFor(row, td) {
    if (td && td.hasAttribute('data-picks')) {
      var inline = [];
      try { inline = JSON.parse(td.getAttribute('data-choices') || '[]'); } catch (ignore) { inline = []; }
      return {kind: 'picks', label: td.dataset.label || 'Choice', choices: inline,
              choices_url: td.dataset.choicesUrl || ''};
    }
    var key = td && td.getAttribute('data-cell');
    return (gridState(row).columns || []).filter(function (c) { return c.key === key; })[0] || null;
  }
  function addOption(select, value, label, selected) {
    var option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (selected) option.selected = true;
    select.appendChild(option);
    return option;
  }
  function gridRows(row) {
    return Array.prototype.slice.call(row.querySelectorAll('[data-grid-row]'));
  }
  function syncGrid(row) {
    var empty = row.querySelector('[data-grid-empty]');
    if (empty) empty.hidden = gridRows(row).length > 0;
  }
  function addChip(td, value, label) {
    var list = td.querySelector('[data-pick-list]');
    if (!list) return;
    var li = document.createElement('li');
    li.setAttribute('data-pick-item', '');
    li.setAttribute('data-value', value);
    var text = document.createElement('span');
    text.textContent = label || value;
    var remove = document.createElement('button');
    remove.className = 'btn btn--ghost';
    remove.type = 'button';
    remove.textContent = 'Remove';
    remove.setAttribute('data-pick-remove', '');
    remove.setAttribute('aria-label', 'Remove ' + (label || value));
    li.appendChild(text); li.appendChild(remove);
    list.appendChild(li);
  }
  function searchBox(td, column, where) {
    var search = document.createElement('input');
    search.className = 'input';
    search.type = 'search';
    search.maxLength = 80;
    search.placeholder = 'Search';
    search.setAttribute('data-pick-search', '');
    search.setAttribute('aria-label', 'Find ' + column.label.toLowerCase() + ' ' + where);
    td.appendChild(search);
  }
  function buildCell(td, column, value, where) {
    var kind = column.kind;
    var input;
    if (kind === 'select' || kind === 'pick') {
      var current = value && typeof value === 'object' ? value : {value: column.default || '', label: ''};
      if (kind === 'pick') searchBox(td, column, where);
      input = document.createElement('select');
      input.className = 'select';
      var seen = false;
      (column.choices || []).slice(0, kind === 'pick' ? 50 : 1000).forEach(function (c) {
        if (c.value === current.value) seen = true;
        addOption(input, c.value, c.label, c.value === current.value);
      });
      if (!seen) {
        var label = current.label;
        if (!label) label = current.value === '*' ? 'Any tool' : (current.value ? current.value : 'Choose one');
        var first = addOption(input, current.value, label, true);
        input.insertBefore(first, input.firstChild);
      }
      if (kind === 'pick') input.setAttribute('data-pick-select', '');
    } else if (kind === 'picks') {
      var list = document.createElement('ul');
      list.className = 'settings-all-list-items';
      list.setAttribute('data-pick-list', '');
      list.setAttribute('aria-label', column.label + ' ' + where);
      td.appendChild(list);
      (Array.isArray(value) ? value : []).forEach(function (item) { addChip(td, item.value, item.label); });
      if (!column.choices || !column.choices.length || column.choices.length > 20) searchBox(td, column, where);
      input = document.createElement('select');
      input.className = 'select';
      input.setAttribute('data-pick-select', '');
      addOption(input, '', 'Choose one to add', true);
      (column.choices || []).slice(0, 50).forEach(function (c) { addOption(input, c.value, c.label, false); });
      input.setAttribute('aria-label', 'Choose ' + column.label.toLowerCase() + ' to add ' + where);
      td.appendChild(input);
      var add = document.createElement('button');
      add.className = 'btn btn--ghost';
      add.type = 'button';
      add.textContent = 'Add';
      add.setAttribute('data-pick-add', '');
      add.setAttribute('aria-label', 'Add to ' + column.label.toLowerCase() + ' ' + where);
      td.appendChild(add);
      return;
    } else if (kind === 'number') {
      input = document.createElement('input');
      input.className = 'input';
      input.type = 'number';
      if (column.minimum != null) input.min = String(column.minimum);
      if (column.maximum != null) input.max = String(column.maximum);
      input.step = String(column.step || 'any');
      input.value = value == null ? '' : String(value);
    } else if (kind === 'switch') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = value === true;
    } else if (kind === 'color') {
      // A colour picker; an optional colour can be left out with its switch.
      input = document.createElement('input');
      input.type = 'color';
      input.value = typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value.toLowerCase()
        : (typeof value === 'string' && /^#[0-9a-fA-F]{3}$/.test(value)
          ? '#' + value.slice(1).split('').map(function (c) { return c + c; }).join('').toLowerCase()
          : '#888888');
      if (!column.required) {
        var use = document.createElement('input');
        use.type = 'checkbox';
        use.checked = typeof value === 'string' && value !== '';
        use.setAttribute('data-cell-use', '');
        use.setAttribute('aria-label', 'Use ' + column.label.toLowerCase() + ' ' + where);
        td.appendChild(use);
      }
    } else if (kind === 'path' || kind === 'program') {
      // A file on the server: the server checks it when the row is saved.
      input = document.createElement('input');
      input.className = 'input';
      input.type = 'text';
      input.maxLength = 1024;
      input.spellcheck = false;
      input.autocomplete = 'off';
      input.placeholder = kind === 'program' ? 'Full location of the program on the server'
        : 'Full location of the file on the server';
      input.value = value == null ? '' : String(value);
    } else if (kind === 'secret') {
      // A saved password is never sent to the page: leave empty to keep it.
      input = document.createElement('input');
      input.className = 'input';
      input.type = 'password';
      input.autocomplete = 'new-password';
      input.maxLength = Number(column.max_length || 512);
      input.placeholder = value && value.is_set ? 'Saved. Leave empty to keep it' : 'Not set';
      input.value = '';
    } else {
      input = document.createElement('input');
      input.className = 'input';
      input.type = 'text';
      input.maxLength = 2000;
      input.value = value == null ? '' : String(value);
    }
    input.setAttribute('data-cell-input', '');
    input.setAttribute('aria-label', column.label + ' ' + where + (column.unit ? ' (' + column.unit + ')' : ''));
    td.appendChild(input);
  }
  function addGridRow(row, entry) {
    var state = gridState(row);
    var body = row.querySelector('[data-grid-body]');
    if (!body) return null;
    var tr = document.createElement('tr');
    tr.setAttribute('data-grid-row', '');
    var where;
    if (state.kind === 'grid' || state.kind === 'named') {
      tr.dataset.name = entry.name;
      where = 'for ' + (entry.label || entry.name);
      var name = document.createElement('td');
      name.setAttribute('data-row-label', '');
      name.textContent = entry.label || entry.name;
      tr.appendChild(name);
    } else {
      where = 'in ' + (state.item_label || 'Rule').toLowerCase() + ' ' + (body.children.length + 1);
    }
    state.columns.forEach(function (column) {
      var td = document.createElement('td');
      td.setAttribute('data-cell', column.key);
      buildCell(td, column, entry.cells ? entry.cells[column.key] : undefined, where);
      tr.appendChild(td);
    });
    var removeCell = document.createElement('td');
    var remove = document.createElement('button');
    remove.className = 'btn btn--ghost'; remove.type = 'button'; remove.textContent = 'Remove';
    remove.setAttribute('data-grid-remove', '');
    remove.setAttribute('aria-label', 'Remove ' + where.replace(/^(for|in) /, ''));
    removeCell.appendChild(remove);
    tr.appendChild(removeCell);
    body.appendChild(tr);
    return tr;
  }
  function buildGrid(row, state) {
    var box = row.querySelector('[data-grid]');
    if (!box) return;
    box.gridState = state;
    state.columns = state.columns || [];
    var body = row.querySelector('[data-grid-body]');
    if (body) body.textContent = '';
    (state.rows || []).forEach(function (entry) { addGridRow(row, entry); });
    var keys = row.querySelector('[data-grid-newkey]');
    if (keys && state.key_choices) {
      keys.textContent = '';
      addOption(keys, '', 'Choose one', true);
      state.key_choices.forEach(function (k) { addOption(keys, k.value, k.label, false); });
    }
    syncGrid(row);
  }
  function readCell(td, column) {
    if (!td) return null;
    if (column.kind === 'picks') {
      return Array.prototype.slice.call(td.querySelectorAll('[data-pick-item]'))
        .map(function (li) { return li.getAttribute('data-value'); });
    }
    var input = td.querySelector('[data-cell-input]');
    if (!input) return null;
    if (column.kind === 'switch') return input.checked;
    if (column.kind === 'color') {
      var use = td.querySelector('[data-cell-use]');
      return use && !use.checked ? '' : input.value;
    }
    if (column.kind === 'number') return input.value.trim() === '' ? null : Number(input.value);
    return input.value;
  }
  function readGrid(row) {
    var state = gridState(row);
    return gridRows(row).map(function (tr) {
      var out = state.kind === 'grid' || state.kind === 'named' ? {name: tr.dataset.name} : {};
      state.columns.forEach(function (column) {
        out[column.key] = readCell(tr.querySelector('[data-cell="' + column.key + '"]'), column);
      });
      return out;
    });
  }
  function addFromGridForm(row) {
    var state = gridState(row);
    var tr;
    if (state.kind === 'named') {
      var box = row.querySelector('[data-grid-newname]');
      var typed = box ? box.value.trim() : '';
      if (!typed) { say(row, 'Type a name first.'); if (box) box.focus(); return; }
      var used = gridRows(row).some(function (r) { return r.dataset.name === typed; });
      if (used) { say(row, 'That name is already in the list. Change it there.'); return; }
      tr = addGridRow(row, {name: typed, label: typed, cells: {}});
      box.value = '';
    } else if (state.kind === 'grid') {
      var pick = row.querySelector('[data-grid-newkey]');
      var chosen = pick && pick.value;
      if (!chosen) { say(row, 'Choose one from the list first.'); return; }
      var taken = gridRows(row).some(function (r) { return r.dataset.name === chosen; });
      if (taken) { say(row, 'That one is already in the list. Change it there.'); return; }
      tr = addGridRow(row, {name: chosen, label: pick.options[pick.selectedIndex].textContent, cells: {}});
      pick.value = '';
    } else {
      tr = addGridRow(row, {cells: {}});
    }
    syncGrid(row);
    say(row, 'Added. Fill in the row, then choose Save.');
    var first = tr && tr.querySelector('select, input, button');
    if (first) first.focus();
  }
  async function columnChoices(row, column) {
    if (!column.choices_url) return column.choices || [];
    if (columnChoiceCache[column.choices_url]) return columnChoiceCache[column.choices_url];
    try {
      var response = await window.fetch(column.choices_url, {cache: 'no-store', credentials: 'same-origin',
                                                             headers: {Accept: 'application/json'}});
      if (!response.ok) throw response;
      var data = await response.json();
      columnChoiceCache[column.choices_url] = data.choices || [];
    } catch (failure) {
      say(row, errorText(failure, 'load the list of options'));
      return [];
    }
    return columnChoiceCache[column.choices_url];
  }
  async function filterPick(row, td) {
    var column = columnFor(row, td);
    var select = td && td.querySelector('[data-pick-select]');
    if (!column || !select) return;
    var search = td.querySelector('[data-pick-search]');
    var words = (search ? search.value : '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    var choices = await columnChoices(row, column);
    var matches = choices.filter(function (c) {
      var text = (c.label + ' ' + c.value).toLowerCase();
      return words.every(function (w) { return text.indexOf(w) !== -1; });
    });
    var keep = column.kind === 'pick' ? select.value : '';
    var keepLabel = column.kind === 'pick' && select.selectedIndex >= 0
      ? select.options[select.selectedIndex].textContent : '';
    select.textContent = '';
    if (column.kind === 'pick') {
      addOption(select, keep, keepLabel || keep, true);
    } else {
      addOption(select, '', matches.length
        ? (matches.length > 50 ? 'Showing 50 of ' + matches.length + ': search to narrow' : 'Choose one to add')
        : 'Nothing matches that search', true);
    }
    matches.slice(0, 50).forEach(function (c) {
      if (column.kind === 'pick' && c.value === keep) return;
      addOption(select, c.value, c.label, false);
    });
  }
  function addPick(row, td) {
    var select = td && td.querySelector('[data-pick-select]');
    if (!select || !select.value) { say(row, 'Choose one to add first.'); return; }
    var value = select.value;
    var exists = Array.prototype.slice.call(td.querySelectorAll('[data-pick-item]'))
      .some(function (li) { return li.getAttribute('data-value') === value; });
    if (exists) { say(row, 'That one is already in the list.'); return; }
    addChip(td, value, select.options[select.selectedIndex].textContent);
    select.value = '';
    say(row, 'Added. Choose Save to keep this change.');
  }
  root.querySelectorAll('[data-grid]').forEach(function (box) {
    var owner = box.closest('.settings-all-row');
    if (owner) buildGrid(owner, gridState(owner));
  });

  // ---- rows named from a long list (people who write from a chat app) ------
  async function filterGridKeys(row) {
    var state = gridState(row);
    var search = row.querySelector('[data-grid-keysearch]');
    var pick = row.querySelector('[data-grid-newkey]');
    if (!state || !pick || !state.key_choices_url) return;
    var words = (search ? search.value : '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    var choices = await columnChoices(row, {choices_url: state.key_choices_url, choices: []});
    var matches = choices.filter(function (c) {
      var text = (c.label + ' ' + c.value).toLowerCase();
      return words.every(function (w) { return text.indexOf(w) !== -1; });
    });
    pick.textContent = '';
    addOption(pick, '', matches.length
      ? (matches.length > 50 ? 'Showing 50 of ' + matches.length + ': search to narrow' : 'Choose one')
      : 'Nothing matches that search', true);
    matches.slice(0, 50).forEach(function (c) { addOption(pick, c.value, c.label, false); });
  }

  // ---- several choices from a long list -----------------------------------
  function syncPicks(row) {
    var empty = row.querySelector('[data-picks-empty]');
    if (empty) empty.hidden = !!row.querySelector('[data-picks] [data-pick-item]');
  }

  // ---- an address with one part for each workspace -------------------------
  function addressParts(text) {
    var match = /^https:\/\/([^\/?#\s]+)(\/[^?#\s]*)?$/.exec((text || '').trim());
    if (!match) return null;
    return {host: match[1], parts: (match[2] || '').split('/').filter(Boolean)};
  }
  function segmentOptions(row) {
    var select = row.querySelector('[data-segment-at]');
    var input = row.querySelector('[data-input]');
    if (!select || !input) return;
    var address = addressParts(input.value);
    var parts = address ? address.parts : [];
    var wanted = select.value !== '' ? Number(select.value) : Number(select.getAttribute('data-at'));
    if (!(wanted >= 0 && wanted <= parts.length)) wanted = parts.length;
    select.textContent = '';
    for (var i = 0; i <= parts.length; i += 1) {
      var label = i === parts.length ? 'At the end of the address'
        : 'Before “' + parts[i] + '”';
      addOption(select, String(i), label, i === wanted);
    }
  }
  function segmentAddress(row) {
    var input = row.querySelector('[data-input]');
    var select = row.querySelector('[data-segment-at]');
    var text = input ? input.value.trim() : '';
    if (!text) return '';
    var address = addressParts(text);
    if (!address) return text;  // the server explains what is wrong with it
    var at = select && select.value !== '' ? Number(select.value) : address.parts.length;
    var parts = address.parts.slice();
    parts.splice(Math.min(Math.max(at, 0), parts.length), 0, '{tenant_sha256}');
    return 'https://' + address.host + '/' + parts.join('/');
  }
  root.querySelectorAll('[data-segment-at]').forEach(function (select) {
    segmentOptions(select.closest('.settings-all-row'));
    select.addEventListener('change', function () {
      select.setAttribute('data-at', select.value);
    });
  });

  // ---- fingerprints from an uploaded file ----------------------------------
  async function fingerprintFrom(row, file, button) {
    var url = API + '/fingerprint?setting_id=' + encodeURIComponent(row.dataset.settingId) +
      (file ? '' : '&server=true');
    say(row, file ? 'Working out the fingerprint…' : 'Reading the files on this server…');
    if (button) button.disabled = true;
    try {
      var response = await window.fetch(url, {
        method: 'POST', cache: 'no-store', credentials: 'same-origin',
        headers: {'Content-Type': 'application/octet-stream', Accept: 'application/json'},
        body: file || new Blob([])
      });
      var data = null;
      try { data = await response.json(); } catch (ignore) { data = null; }
      if (!response.ok || !data || !data.fingerprint) {
        say(row, data && typeof data.message === 'string' ? data.message : errorText(response, 'work out the fingerprint'));
        return;
      }
      row.querySelector('[data-input]').value = data.fingerprint;
      var printed = row.querySelector('[data-fingerprint-shown]');
      if (printed) printed.textContent = data.shown;
      say(row, 'Fingerprint worked out. Choose Save to trust it.');
    } catch (failure) {
      say(row, errorText(failure, 'work out the fingerprint'));
    } finally {
      if (button) button.disabled = false;
    }
  }

  // ---- documents imported from a file --------------------------------------
  function showDocument(row, doc) {
    var text = row.querySelector('[data-document-text]');
    var items = row.querySelector('[data-document-items]');
    if (text) text.textContent = doc ? doc.text : 'Nothing imported yet.';
    if (!items) return;
    items.textContent = '';
    ((doc && doc.items) || []).forEach(function (item) {
      var li = document.createElement('li');
      li.appendChild(document.createTextNode(item.label + ', fingerprint '));
      var strong = document.createElement('strong');
      strong.textContent = item.fingerprint;
      li.appendChild(strong);
      items.appendChild(li);
    });
  }
  root.addEventListener('change', async function (event) {
    var print = event.target.closest('[data-fingerprint-file]');
    if (print && print.files && print.files.length) {
      await fingerprintFrom(print.closest('.settings-all-row'), print.files[0], null);
      print.value = '';
      return;
    }
    var doc = event.target.closest('[data-document-file]');
    if (doc && doc.files && doc.files.length) {
      var owner = doc.closest('.settings-all-row');
      var chosen = doc.files[0];
      if (chosen.size > 65536) {
        say(owner, 'That file is too large to be this kind of document.');
      } else {
        try {
          owner.pendingDocument = await chosen.text();
          say(owner, 'Ready to import “' + chosen.name + '”. Choose Save to check and use it.');
        } catch (failure) {
          say(owner, 'That file could not be read. Try again.');
        }
      }
      doc.value = '';
      return;
    }
    var level = event.target.closest('select[data-preview-url]');
    if (level) await showPreview(level.closest('.settings-all-row'), level);
  });

  // ---- what saving a choice would also change -------------------------------
  async function showPreview(row, select) {
    var box = row.querySelector('[data-preview]');
    if (!box) return;
    box.textContent = '';
    if (!select.value) return;
    box.textContent = 'Working out what this changes…';
    try {
      var response = await window.fetch(select.getAttribute('data-preview-url') + '?value=' +
        encodeURIComponent(select.value), {cache: 'no-store', credentials: 'same-origin',
                                          headers: {Accept: 'application/json'}});
      var data = null;
      try { data = await response.json(); } catch (ignore) { data = null; }
      box.textContent = '';
      if (!response.ok || !data) {
        box.textContent = data && typeof data.message === 'string' ? data.message : errorText(response, 'work out what this changes');
        return;
      }
      var lead = document.createElement('p');
      lead.textContent = (data.items || []).length
        ? 'Saving this also changes:' : 'Saving this changes nothing else: every setting it covers already matches.';
      box.appendChild(lead);
      if ((data.items || []).length) {
        var list = document.createElement('ul');
        data.items.forEach(function (item) {
          var li = document.createElement('li');
          li.textContent = item.label + ': ' + item.now + ' → ' + item.new + (item.lowers ? ' (lowers a protection)' : '');
          list.appendChild(li);
        });
        box.appendChild(list);
      }
      if ((data.kept || []).length) {
        var kept = document.createElement('p');
        kept.className = 'muted';
        kept.textContent = 'Left as they are (set by your server): ' + data.kept.join(', ') + '.';
        box.appendChild(kept);
      }
    } catch (failure) {
      box.textContent = errorText(failure, 'work out what this changes');
    }
  }

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
