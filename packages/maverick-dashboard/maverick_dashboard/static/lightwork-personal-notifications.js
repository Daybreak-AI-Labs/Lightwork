(function () {
  'use strict';
  const form = document.getElementById('notification-preferences');
  if (!form) return;
  const status = document.getElementById('personal-notification-status');
  const error = document.getElementById('personal-notification-error');
  const items = document.getElementById('personal-notification-items');
  const saveStatus = document.getElementById('personal-notification-save-status');
  const refresh = document.getElementById('personal-notification-refresh');
  const taskSelect = document.getElementById('notification-mute-task');
  let snapshot = null, editSnapshot = null, dirty = false, busy = false, mutes = {}, timer = null, controller = null;
  const scalar = ['mode','timezone','digest_time','quiet_start','quiet_end'];
  const checks = ['quiet_enabled','critical_exceptions','watch_all_projects'];
  function renderMutes() {
    const container = document.getElementById('notification-mutes'); container.replaceChildren();
    Object.entries(mutes).forEach(function ([id, until]) {
      const row = document.createElement('p');
      const task = (snapshot.tasks || []).find(function (item) { return String(item.id) === id; });
      const label = document.createElement('span'); label.textContent = (task ? task.title : 'Unavailable task') + ': muted until ' + new Date(until * 1000).toLocaleString() + ' ';
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn btn--ghost'; remove.textContent = 'Remove mute';
      remove.addEventListener('click', function () { delete mutes[id]; dirty = true; renderMutes(); });
      row.append(label, remove); container.append(row);
    });
  }
  function render(state, reset) {
    if (snapshot && snapshot.context !== state.context) {
      items.replaceChildren(); form.reset(); mutes = {}; dirty = false;
      throw new Error('Your account or workspace changed. Reload this page before continuing.');
    }
    snapshot = state;
    status.textContent = state.delivery_note + (state.partial ? ' Coverage is limited to the latest 200 owned tasks and a bounded assigned-decision list.' : '') + ' Last checked ' + new Date(state.checked_at * 1000).toLocaleTimeString();
    const active = document.activeElement;
    const restoreId = active && active.dataset ? active.dataset.alertId : null;
    items.replaceChildren();
    if (!state.items.length) { const empty = document.createElement('p'); empty.textContent = 'No current personal alerts. Action Center remains available for decisions.'; items.append(empty); }
    state.items.forEach(function (item) {
      const row = document.createElement('article');
      const heading = document.createElement('h3'); const link = document.createElement('a');
      link.href = item.href; link.textContent = item.title; link.dataset.alertId = item.id + '-link'; heading.append(link);
      const detail = document.createElement('p'); detail.textContent = item.label + ' · ' + (item.seen_at ? 'Marked seen' : 'Available in your inbox; not marked seen');
      row.append(heading, detail);
      if (!item.seen_at) {
        const seen = document.createElement('button'); seen.className = 'btn btn--ghost'; seen.textContent = 'Mark seen'; seen.dataset.alertId = item.id;
        seen.addEventListener('click', function () { request('/api/v1/notifications/personal/seen', {context:snapshot.context, item_id:item.id}); }); row.append(seen);
      }
      items.append(row);
    });
    if (restoreId) { const target = Array.from(items.querySelectorAll('[data-alert-id]')).find(function (element) { return element.dataset.alertId === restoreId || element.dataset.alertId === restoreId + '-link'; }); if (target) target.focus({preventScroll:true}); }
    if (!dirty || reset) {
      dirty = false; editSnapshot = state;
      scalar.forEach(function (name) { form.elements[name].value = state.preferences[name]; });
      checks.forEach(function (name) { form.elements[name].checked = state.preferences[name]; });
      form.querySelectorAll('[name="event"]').forEach(function (element) { element.checked = state.preferences.events.includes(element.value); });
      const watches = document.getElementById('notification-project-watches'); watches.replaceChildren();
      state.projects.forEach(function (project) { const label = document.createElement('label'); const input = document.createElement('input'); input.type = 'checkbox'; input.name = 'project'; input.value = project.id; input.checked = state.preferences.projects.includes(project.id); label.append(input, document.createTextNode(' ' + project.name)); watches.append(label); });
      taskSelect.replaceChildren();
      state.tasks.forEach(function (task) { const option = document.createElement('option'); option.value = task.id; option.textContent = task.title; taskSelect.append(option); });
      mutes = Object.assign({}, state.preferences.muted_tasks); renderMutes();
    }
    document.getElementById('personal-critical-policy').textContent = state.critical_allowed ? 'Organization policy permits this exception if you select it.' : 'Organization policy currently prevents personal alerts from bypassing quiet hours or digests.';
  }
  async function request(url, body, reset) {
    if (busy) return;
    busy = true; refresh.disabled = true; form.querySelector('[type="submit"]').disabled = true;
    controller = new AbortController(); error.hidden = true;
    const timeout = setTimeout(function () { if (controller) controller.abort(); }, 20000);
    try {
      const response = await fetch(url, {method:body ? 'POST':'GET', credentials:'same-origin', signal:controller.signal, headers:body ? {'Content-Type':'application/json'}:{}, body:body ? JSON.stringify(body):undefined});
      let value; try { value = await response.json(); } catch (_) { throw new Error('Your inbox could not be read. Try refreshing.'); }
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) { items.replaceChildren(); form.hidden = true; taskSelect.replaceChildren(); document.getElementById('notification-project-watches').replaceChildren(); document.getElementById('notification-mutes').replaceChildren(); snapshot = null; editSnapshot = null; mutes = {}; }
        throw new Error(typeof value.detail === 'string' ? value.detail : 'Your inbox is unavailable. Refresh before trying again.');
      }
      render(value, reset); if (reset) saveStatus.textContent = reset === true ? 'Your personal notification preferences were saved.' : 'Saved preferences reloaded.';
    } catch (failure) { if (failure.name !== 'AbortError' || !document.hidden) { error.textContent = failure.name === 'AbortError' ? 'The inbox request was interrupted. Refresh to check the latest state before saving again.' : failure.message; error.hidden = false; } }
    finally { clearTimeout(timeout); busy = false; controller = null; refresh.disabled = false; form.querySelector('[type="submit"]').disabled = false; }
  }
  form.addEventListener('input', function () { dirty = true; });
  form.addEventListener('change', function () { dirty = true; });
  form.addEventListener('submit', function (event) {
    event.preventDefault(); if (!editSnapshot || busy) return;
    const preferences = {muted_tasks:mutes, events:[], projects:[]};
    scalar.forEach(function (name) { preferences[name] = form.elements[name].value; });
    checks.forEach(function (name) { preferences[name] = form.elements[name].checked; });
    form.querySelectorAll('[name="event"]:checked').forEach(function (element) { preferences.events.push(element.value); });
    form.querySelectorAll('[name="project"]:checked').forEach(function (element) { preferences.projects.push(Number(element.value)); });
    request('/api/v1/notifications/personal/preferences', {context:editSnapshot.context, expected_revision:editSnapshot.revision, preferences:preferences}, true);
  });
  document.getElementById('notification-add-mute').addEventListener('click', function () {
    if (!taskSelect.value || !snapshot) return; mutes[taskSelect.value] = Date.now() / 1000 + Number(document.getElementById('notification-mute-duration').value); dirty = true; renderMutes();
  });
  refresh.addEventListener('click', function () { request('/api/v1/notifications/personal'); });
  document.getElementById('notification-reload-preferences').addEventListener('click', async function () {
    if (busy) return;
    if (dirty && (!window.mvConfirm || !await window.mvConfirm('Discard this notification preference draft and reload the saved values?', {okText:'Discard draft and reload'}))) return;
    request('/api/v1/notifications/personal', null, 'reload');
  });
  function schedule() { clearInterval(timer); if (!document.hidden) { request('/api/v1/notifications/personal'); timer = setInterval(function () { request('/api/v1/notifications/personal'); }, 30000); } else if (controller) controller.abort(); }
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pagehide', function () { clearInterval(timer); if (controller) controller.abort(); });
  if (location.hash === '#notification-preferences') form.focus();
  schedule();
})();
