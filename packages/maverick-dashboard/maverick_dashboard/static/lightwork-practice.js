// ONB-4 practice mode: start it, see what practice held back, and go back to live.
// The switch lives in this browser session; the server keeps practice tasks in
// a separate practice workspace and records anything that would leave Lightwork.
(function () {
  'use strict';
  if (window.LWPractice) return;
  window.LWPractice = true;

  function errorText(failure, action) {
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '.';
  }
  async function request(on) {
    if (window.mvFetchJSON) return window.mvFetchJSON('/api/v1/practice', { method: 'POST', body: { on: on } }, on ? 'start practice mode' : 'go back to live');
    var response = await fetch('/api/v1/practice', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ on: on }) });
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw { status: response.status, detail: data.detail };
    return data;
  }
  function say(node, text) {
    if (!node) return;
    if (window.mvStatus) window.mvStatus(node, text, { error: true }); else node.textContent = text;
  }
  async function switchTo(on, button, statusNode) {
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    try {
      await request(on);
      // Every screen must show the right mode, so the page reloads in it.
      window.location.reload();
    } catch (failure) {
      say(statusNode, errorText(failure, on ? 'start practice mode' : 'go back to live'));
      if (button) { button.disabled = false; button.removeAttribute('aria-busy'); }
    }
  }
  function cell(row, text) { var td = document.createElement('td'); td.textContent = text; row.appendChild(td); }
  function renderOutbox(box, rows) {
    box.textContent = '';
    if (!rows.length) {
      var body = 'When a practice task tries to send a message, pay, post or change something outside practice, it is listed here instead of happening.';
      box.appendChild(window.mvEmpty ? window.mvEmpty('Nothing was held back yet', body, null, { compact: true })
        : Object.assign(document.createElement('p'), { textContent: 'Nothing was held back yet. ' + body }));
      return;
    }
    var table = document.createElement('table');
    var head = table.createTHead().insertRow();
    ['When', 'What it would have done', 'Task', 'Details'].forEach(function (label) {
      var th = document.createElement('th'); th.scope = 'col'; th.textContent = label; head.appendChild(th);
    });
    var body2 = table.createTBody();
    rows.forEach(function (item) {
      var row = body2.insertRow();
      cell(row, window.mvTimeAbsolute ? window.mvTimeAbsolute(item.at) : String(item.at));
      cell(row, item.kind === 'tool' ? 'Use ' + String(item.name).replace(/_/g, ' ') : item.name);
      var task = document.createElement('td');
      if (item.goal_id) { var link = document.createElement('a'); link.href = '/chat/goal/' + encodeURIComponent(item.goal_id); link.textContent = 'Practice task ' + item.goal_id; task.appendChild(link); }
      else task.textContent = '—';
      row.appendChild(task);
      cell(row, item.detail || '');
    });
    box.appendChild(table);
  }
  async function toggleOutbox(button) {
    var box = document.getElementById(button.getAttribute('aria-controls'));
    if (!box) return;
    var open = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', open ? 'false' : 'true');
    box.hidden = open;
    if (open) return;
    box.textContent = 'Reading what practice held back…';
    try {
      var data = window.mvFetchJSON ? await window.mvFetchJSON('/api/v1/practice', {}, 'read what practice held back')
        : await (await fetch('/api/v1/practice', { credentials: 'same-origin' })).json();
      renderOutbox(box, data.outbox || []);
    } catch (failure) { box.textContent = errorText(failure, 'read what practice held back'); }
  }
  document.addEventListener('click', function (event) {
    var leave = event.target.closest && event.target.closest('[data-practice-leave]');
    if (leave) { event.preventDefault(); switchTo(false, leave, document.getElementById('practice-outbox')); return; }
    var start = event.target.closest && event.target.closest('[data-practice-start]');
    if (start) { event.preventDefault(); switchTo(true, start, document.getElementById(start.getAttribute('data-practice-status') || '')); return; }
    var outbox = event.target.closest && event.target.closest('[data-practice-outbox]');
    if (outbox) { event.preventDefault(); toggleOutbox(outbox); }
  });
})();
