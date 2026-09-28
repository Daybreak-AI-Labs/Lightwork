/* Help page: prepare a bug report for a task that stopped with a problem. */
(function () {
  'use strict';
  var form = document.getElementById('issue-report-form');
  if (!form) return;
  var select = document.getElementById('issue-report-task');
  var status = document.getElementById('issue-report-status');
  var BASE = '/api/v1/support/issue-report';
  function say(text) { status.textContent = text || ''; }
  async function failText(failure, action) {
    try {
      if (failure && typeof failure.clone === 'function') {
        var body = await failure.clone().json();
        var plain = window.mvPlainDetail ? window.mvPlainDetail(body && body.detail) : '';
        if (plain) return plain;
      }
    } catch (ignored) { /* not a readable body */ }
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.';
  }
  function option(value, text) { var o = document.createElement('option'); o.value = value; o.textContent = text; return o; }
  async function load() {
    try {
      var response = await window.fetch(BASE + '/tasks', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw response;
      var data = await response.json();
      select.textContent = '';
      if (!data.tasks.length) {
        select.appendChild(option('', 'No tasks have stopped with a problem'));
        select.disabled = true;
        form.querySelector('button[type="submit"]').disabled = true;
        return;
      }
      select.appendChild(option('', 'Choose a task'));
      data.tasks.forEach(function (t) {
        var when = window.mvWhen ? window.mvWhen(t.when) : '';
        select.appendChild(option(String(t.id), t.title + (when ? ' (' + when + ')' : '')));
      });
    } catch (failure) {
      select.textContent = '';
      select.appendChild(option('', 'Tasks could not be loaded'));
      say(await failText(failure, 'load your tasks'));
    }
  }
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (!select.value) { say('Choose a task first.'); return; }
    say('Preparing the bug report…');
    try {
      var response = await window.fetch(BASE, {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task: Number(select.value)})});
      if (!response.ok) throw response;
      var data = await response.json();
      say(data.summary);
      var ok = window.mvConfirm ? await window.mvConfirm('Open Lightwork\'s public bug-report page in a new tab? The task\'s name and error messages are filled in; nothing is sent until you submit it there.', {okText: 'Open the report'}) : false;
      if (ok) window.open(data.url, '_blank', 'noopener');
    } catch (failure) { say(await failText(failure, 'prepare the bug report')); }
  });
  load();
})();
