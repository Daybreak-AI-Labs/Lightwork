(function () {
  'use strict';
  var form = document.getElementById('fact-editor');
  if (!form) return;
  var name = document.getElementById('fact-name');
  var detail = document.getElementById('fact-detail');
  var status = document.getElementById('fact-status');
  var save = document.getElementById('fact-save');
  var cancel = document.getElementById('fact-cancel');
  var table = document.getElementById('fact-list');
  var busy = false;
  function reset() {
    form.reset(); name.readOnly = false; cancel.hidden = true;
    document.getElementById('fact-editor-title').textContent = 'Add a fact';
  }
  cancel.addEventListener('click', function () { if (!busy) { reset(); status.textContent = ''; name.focus(); } });
  function keyOf(row) { return row.getAttribute('data-key') || row.querySelector('.fact-key').textContent; }
  function label(key) {
    return /_/.test(key) && !/\s/.test(key) && window.mvHuman ? window.mvHuman(key) : key;
  }
  table.addEventListener('click', async function (event) {
    var remove = event.target.closest('[data-remove-fact]');
    if (remove && !busy) {
      var gone = remove.closest('tr'), key = keyOf(gone);
      if (!(await window.mvConfirm('Remove the fact “' + label(key) + '”? Lightwork will no longer use it in new tasks.', {okText: 'Remove'}))) return;
      busy = true; remove.disabled = true;
      try {
        var response = await fetch('/api/v1/facts/' + encodeURIComponent(key), {method: 'DELETE'});
        if (response.status !== 204 && response.status !== 404) {
          throw new Error(window.mvErrorText ? window.mvErrorText(response, 'remove the fact') : 'The fact could not be removed.');
        }
        gone.remove();
        if (!table.querySelector('tbody tr')) {
          table.hidden = true;
          var empty = document.getElementById('facts-empty'); if (empty) empty.hidden = false;
        }
        status.textContent = 'Fact removed.';
      } catch (error) {
        status.textContent = error.message || 'The fact could not be removed.';
        remove.disabled = false;
      } finally { busy = false; }
      return;
    }
    var button = event.target.closest('[data-edit-fact]');
    if (!button || busy) return;
    var row = button.closest('tr');
    name.value = keyOf(row);
    detail.value = row.querySelector('.fact-value').textContent;
    name.readOnly = true; cancel.hidden = false;
    document.getElementById('fact-editor-title').textContent = 'Edit fact';
    status.textContent = ''; detail.focus();
  });
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    var payload = {key: name.value.trim(), value: detail.value};
    if (!payload.key) { status.textContent = 'Enter a name for this fact.'; name.focus(); return; }
    var rows = Array.from(table.querySelectorAll('tbody tr'));
    var existing = rows.find(function (row) { return keyOf(row) === payload.key; });
    busy = true; save.disabled = true; cancel.disabled = true;
    name.disabled = true; detail.disabled = true;
    try {
      if (existing && !name.readOnly && !(await window.mvConfirm('Replace the saved fact “' + label(payload.key) + '” with this detail?', {okText:'Replace fact'}))) return;
      status.textContent = 'Saving fact…';
      var response = await fetch('/api/v1/facts', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      if (response.status !== 204) throw new Error(response.status === 403 ? 'You do not have permission to save facts.' : 'The fact could not be saved. Your draft is still here; try again.');
      var check = await fetch('/api/v1/facts', {cache:'no-store'});
      if (!check.ok) throw new Error('The save was accepted, but could not be checked. Refresh the page before trying again.');
      var facts = await check.json();
      if (!facts || facts[payload.key] !== payload.value) throw new Error('The saved fact could not be confirmed. Refresh the page before trying again.');
      var tbody = table.querySelector('tbody'); tbody.replaceChildren();
      Object.keys(facts).forEach(function (key) {
        var row = document.createElement('tr'); row.setAttribute('data-key', key);
        var keyCell = document.createElement('td'); keyCell.className = 'fact-key'; keyCell.textContent = label(key);
        var valueCell = document.createElement('td'); valueCell.className = 'fact-value'; valueCell.textContent = facts[key]; valueCell.style.whiteSpace = 'pre-wrap'; valueCell.style.overflowWrap = 'anywhere';
        var action = document.createElement('td'); action.className = 'mv-row__actions'; var button = document.createElement('button');
        button.type = 'button'; button.className = 'btn btn--ghost'; button.setAttribute('data-edit-fact', ''); button.setAttribute('aria-label', 'Edit ' + key); button.textContent = 'Edit'; action.append(button);
        var rm = document.createElement('button');
        rm.type = 'button'; rm.className = 'btn btn--ghost btn--danger'; rm.setAttribute('data-remove-fact', ''); rm.setAttribute('aria-label', 'Remove ' + key); rm.textContent = 'Remove'; action.append(rm);
        row.append(keyCell, valueCell, action); tbody.append(row);
      });
      table.hidden = false;
      var empty = document.getElementById('facts-empty'); if (empty) empty.hidden = true;
      reset(); status.textContent = 'Fact saved.';
    } catch (error) { status.textContent = error.message || 'Could not reach Lightwork. Your draft is still here.'; }
    finally { busy = false; save.disabled = false; cancel.disabled = false; name.disabled = false; detail.disabled = false; }
  });
})();
