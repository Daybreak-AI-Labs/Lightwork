(function () {
  'use strict';
  var root = document.getElementById('facts-workspace');
  if (!root) return;
  var context = root.dataset.context, canEdit = root.dataset.canEdit === 'true';
  var form = document.getElementById('fact-editor'), name = document.getElementById('fact-name');
  var detail = document.getElementById('fact-detail'), status = document.getElementById('fact-status');
  var table = document.getElementById('fact-list'), busy = false, offset = 0, revision = 0, currentRevision = 0;
  var prev = document.getElementById('fact-prev'), next = document.getElementById('fact-next');
  var inspector = document.getElementById('fact-inspector'), body = document.getElementById('fact-inspector-body');
  var conflict = document.getElementById('fact-conflict');
  function node(tag, text) { var element = document.createElement(tag); element.textContent = text; return element; }
  function date(value) { return value ? new Date(value * 1000).toLocaleString() : 'Not recorded'; }
  function label(value) { return String(value).replace(/_/g, ' ').replace(/^./, function (c) { return c.toUpperCase(); }); }
  function clearPrivate() {
    table.querySelector('tbody').replaceChildren(); body.replaceChildren(); inspector.hidden = true;
    if (form) form.reset(); if (conflict) conflict.hidden = true;
  }
  async function request(url, options) {
    var response = await fetch(url, Object.assign({cache:'no-store', credentials:'same-origin'}, options || {}));
    if (response.status === 401 || response.status === 403) {
      clearPrivate(); throw new Error('Access changed. Sign in or choose an authorized workspace.');
    }
    if (!response.ok) { var failure = new Error('The request could not be completed. Your draft is preserved.'); failure.status = response.status; throw failure; }
    if (response.status === 204) return null;
    var payload = await response.json();
    if (payload.context !== context) { clearPrivate(); throw new Error('Your account or workspace changed. Reload to continue.'); }
    return payload;
  }
  function setBusy(value) {
    busy = value;
    root.querySelectorAll('button').forEach(function (button) { button.disabled = value; });
    if (!value) { prev.disabled = offset === 0; next.disabled = next.dataset.more !== 'true'; }
  }
  next.dataset.more = next.disabled ? 'false' : 'true';
  function reset() {
    if (!form) return;
    form.reset(); name.readOnly = false; revision = 0; conflict.hidden = true;
    document.getElementById('fact-cancel').hidden = true;
    document.getElementById('fact-editor-title').textContent = 'Add a fact';
  }
  function render(items) {
    var tbody = table.querySelector('tbody'); tbody.replaceChildren();
    items.forEach(function (item) {
      var row = document.createElement('tr'); row.dataset.key = item.key; row.dataset.revision = item.revision;
      var selectCell = document.createElement('td'), checkbox = document.createElement('input');
      checkbox.type = 'checkbox'; checkbox.setAttribute('data-select-fact', ''); checkbox.setAttribute('aria-label', 'Select ' + item.key); selectCell.append(checkbox);
      var keyCell = node('td', label(item.key)); keyCell.className = 'fact-key';
      var valueCell = node('td', item.value); valueCell.className = 'fact-value'; valueCell.style.whiteSpace = 'pre-wrap'; valueCell.style.overflowWrap = 'anywhere';
      var actions = document.createElement('td'); actions.className = 'mv-row__actions';
      [['view','Details and history']].concat(canEdit ? [['edit','Edit'],['remove','Remove']] : []).forEach(function (action) {
        var button = node('button', action[1]); button.type = 'button'; button.className = 'btn btn--ghost'; button.setAttribute('data-' + action[0] + '-fact', ''); actions.append(button);
      });
      row.append(selectCell, keyCell, valueCell, node('td', date(item.updated_at)), actions); tbody.append(row);
    });
    table.hidden = items.length === 0; document.getElementById('facts-empty').hidden = items.length > 0;
  }
  async function load() {
    var params = new URLSearchParams({query:document.getElementById('fact-query').value, source:document.getElementById('fact-source').value, offset:offset, limit:50});
    var before = document.getElementById('fact-before').value;
    if (before) params.set('updated_before', new Date(before + 'T00:00:00').getTime() / 1000);
    var payload = await request('/api/v1/facts/records?' + params);
    render(payload.items); next.dataset.more = payload.has_more ? 'true' : 'false';
    status.textContent = payload.items.length + ' facts on this page. Search covers workspace fact names and source metadata.';
  }
  async function browse(delta) {
    if (busy) return; var prior = offset; offset = Math.max(0, offset + delta); setBusy(true);
    try { await load(); } catch (error) { offset = prior; status.textContent = error.message; } finally { setBusy(false); }
  }
  document.getElementById('fact-search').addEventListener('submit', function (event) { event.preventDefault(); browse(-offset); });
  prev.addEventListener('click', function () { browse(-50); }); next.addEventListener('click', function () { browse(50); });
  async function current(key) { return request('/api/v1/facts/record?' + new URLSearchParams({key:key})); }
  async function showConflict(key) {
    try {
      var payload = await current(key); currentRevision = payload.item.revision;
      document.getElementById('fact-current').textContent = 'Current saved detail:\n' + payload.item.value;
      conflict.hidden = false; status.textContent = 'This fact changed. Compare the current detail with your preserved draft.';
    } catch (error) { status.textContent = error.status === 404 ? 'This fact was removed. Your draft is preserved; cancel editing before creating a new fact.' : error.message; }
  }
  function showRecord(payload) {
    var item = payload.item, section = document.createElement('section');
    section.append(node('h3', label(item.key)), node('p', item.value));
    section.append(node('p', 'Updated: ' + date(item.updated_at) + '. Source or author: ' + (item.source || 'Not recorded') + '. Trust: ' + ({0:'External',1:'Agent working note',2:'Learned',3:'First party'}[item.trust_tier] || 'Unknown') + '. Sensitivity: ' + item.sensitivity + '.'));
    if (item.source_episode_id) section.append(node('p', 'Source run reference: ' + item.source_episode_id));
    section.append(node('p', payload.history_notice), node('p', 'Expiry is governed by workspace retention settings; no individual expiry is recorded for this fact.'));
    if (!payload.history.length) section.append(node('p', 'No recorded version history is available.'));
    payload.history.forEach(function (version) {
      var entry = document.createElement('details'); entry.append(node('summary', 'Version from ' + date(version.valid_from) + (version.valid_to ? ' to ' + date(version.valid_to) : ' (current)')));
      var value = node('pre', version.value || ''); value.style.whiteSpace = 'pre-wrap'; value.style.overflowWrap = 'anywhere'; entry.append(value);
      entry.append(node('p', 'Source or author: ' + (version.source || 'Not recorded') + '. Sensitivity: ' + version.sensitivity + '.'));
      if (canEdit) {
        var restore = node('button', 'Use this version as an edit draft'); restore.type = 'button'; restore.className = 'btn btn--ghost';
        restore.addEventListener('click', function () {
          if (busy) return;
          if (name.value && (name.value !== item.key || detail.value !== item.value)) {
            status.textContent = 'Save or cancel your existing draft before restoring a version.'; return;
          }
          name.value = item.key; detail.value = version.value; revision = item.revision; name.readOnly = true;
          document.getElementById('fact-cancel').hidden = false; document.getElementById('fact-editor-title').textContent = 'Review restored version'; conflict.hidden = true;
          status.textContent = 'Review this historical detail against the current value below, then save as a new version. Current permissions and concurrent changes are checked again.'; detail.focus();
        });
        entry.append(restore);
      }
      section.append(entry);
    });
    body.append(section); inspector.hidden = false;
  }
  document.getElementById('fact-review-selection').addEventListener('click', async function () {
    if (busy) return;
    var selected = Array.from(table.querySelectorAll('[data-select-fact]:checked')).map(function (input) { return input.closest('tr').dataset.key; });
    if (!selected.length) { status.textContent = 'Select the facts you want to review on this page.'; return; }
    setBusy(true); body.replaceChildren();
    try { for (var key of selected) showRecord(await current(key)); document.getElementById('fact-inspector-title').focus(); }
    catch (error) { body.replaceChildren(); inspector.hidden = true; status.textContent = error.message; }
    finally { setBusy(false); }
  });
  table.addEventListener('click', async function (event) {
    var button = event.target.closest('button'), row = button && button.closest('tr'); if (!row || busy) return;
    var key = row.dataset.key;
    if (button.hasAttribute('data-edit-fact')) {
      name.value = key; detail.value = row.querySelector('.fact-value').textContent; revision = Number(row.dataset.revision); name.readOnly = true;
      document.getElementById('fact-cancel').hidden = false; document.getElementById('fact-editor-title').textContent = 'Edit fact'; conflict.hidden = true; detail.focus(); return;
    }
    if (button.hasAttribute('data-remove-fact')) {
      if (!(await window.mvConfirm('Remove "' + key + '" from new recall? Recorded versions and audit evidence may remain under workspace retention policy. This is not permanent erasure.', {okText:'Stop using this fact'}))) return;
      setBusy(true);
      try { await request('/api/v1/facts/' + encodeURIComponent(key) + '?' + new URLSearchParams({expected_revision:row.dataset.revision,context:context}), {method:'DELETE'}); await load(); status.textContent = 'Fact removed from new recall. Retained history follows workspace policy.'; }
      catch (error) { status.textContent = error.status === 409 ? 'This fact changed. Refresh and review its current value before removing; nothing was removed.' : error.message; }
      finally { setBusy(false); } return;
    }
    setBusy(true); body.replaceChildren();
    try { showRecord(await current(key)); document.getElementById('fact-inspector-title').focus(); }
    catch (error) { status.textContent = error.message; } finally { setBusy(false); }
  });
  if (form) {
    document.getElementById('fact-cancel').addEventListener('click', function () { if (!busy) reset(); });
    document.getElementById('fact-rebase').addEventListener('click', function () { revision = currentRevision; conflict.hidden = true; status.textContent = 'Your draft is preserved. Save when you are ready; another change will be checked again.'; detail.focus(); });
    form.addEventListener('submit', async function (event) {
      event.preventDefault(); if (busy || !form.reportValidity()) return;
      var payload = {key:name.value.trim(), value:detail.value, expected_revision:revision, context:context};
      setBusy(true);
      try {
        await request('/api/v1/facts', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
        reset();
        try { await load(); status.textContent = 'Fact saved.'; }
        catch (refreshError) { status.textContent = 'Fact saved, but this list could not refresh. Refresh the list to see it.'; }
      }
      catch (error) { if (error.status === 409) await showConflict(payload.key); else status.textContent = error.message; }
      finally { setBusy(false); }
    });
  }
})();
