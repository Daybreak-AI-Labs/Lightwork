/* Workspaces page: create by name, change plan and daily limit, pause,
   resume and remove. Every change goes through the admin workspace API. */
(function () {
  'use strict';
  var addMsg = document.getElementById('tenant-add-msg');
  var rowMsg = document.getElementById('tenant-quota-msg') || document.getElementById('removed-tenants-msg');

  function errorText(failure, action) {
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.';
  }
  async function send(url, method, body) {
    var response = await window.fetch(url, {
      method: method, credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (!response.ok) throw response;
    return response.status === 204 ? {} : response.json().catch(function () { return {}; });
  }
  function reloadSoon() { window.setTimeout(function () { window.location.reload(); }, 350); }
  function base(id) { return '/api/v1/admin/tenants/' + encodeURIComponent(id); }
  function nameOf(id) {
    var row = document.querySelector('[data-tenant-row="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]');
    return row ? row.dataset.tenantName : 'this workspace';
  }

  // "No limit" disables the amount; unticking asks for one.
  document.querySelectorAll('[data-limit-toggle]').forEach(function (box) {
    var scope = box.closest('form');
    var amount = scope && scope.querySelector('[data-limit-amount]');
    if (!amount) return;
    box.addEventListener('change', function () {
      amount.disabled = box.checked;
      amount.required = !box.checked;
      if (!box.checked) amount.focus();
    });
  });
  function limitFrom(form) {
    var box = form.querySelector('[data-limit-toggle]');
    if (box && box.checked) return 0;
    var value = Number(form.querySelector('[data-limit-amount]').value);
    return isFinite(value) && value > 0 ? value : null;
  }

  var add = document.getElementById('tenant-add');
  if (add) {
    add.addEventListener('submit', async function (event) {
      event.preventDefault();
      var data = new FormData(add);
      var limit = limitFrom(add);
      var name = String(data.get('display_name') || '').trim();
      if (!name) { addMsg.textContent = 'Give the workspace a name.'; return; }
      if (limit === null) { addMsg.textContent = 'Enter a daily limit above $0, or tick "No daily limit".'; return; }
      var adminEmail = String(data.get('admin_email') || '').trim();
      var out = document.getElementById('tenant-onboard-out');
      if (out) out.hidden = true;
      addMsg.textContent = 'Creating…';
      try {
        if (adminEmail) {
          // Workspace + its first administrator's invitation in one step. The
          // page does not reload: the invitation link is shown only once.
          var result = await send('/api/v1/admin/workspace-ops/onboard', 'POST', {
            display_name: name, plan: String(data.get('plan') || 'free'), max_daily_dollars: limit,
            admin_email: adminEmail, role: String(data.get('role') || 'admin')});
          addMsg.textContent = result.summary || ('Workspace "' + name + '" created and ' + adminEmail + ' invited.');
          if (window.mvToast) window.mvToast(addMsg.textContent);
          var link = document.getElementById('tenant-onboard-link');
          if (link && out) { link.value = result.invite_link || ''; out.hidden = !link.value; }
          add.reset();
          return;
        }
        await send('/api/v1/admin/tenants', 'POST', {display_name: name, plan: String(data.get('plan') || 'free'), max_daily_dollars: limit});
        addMsg.textContent = 'Workspace "' + name + '" created.';
        if (window.mvToastAfterReload) window.mvToastAfterReload('Workspace "' + name + '" created.');
        reloadSoon();
      } catch (failure) {
        var plain = '';
        try { if (failure && typeof failure.clone === 'function') { var body = await failure.clone().json(); plain = window.mvPlainDetail ? window.mvPlainDetail(body && body.detail) : ''; } } catch (ignored) {}
        addMsg.textContent = plain || errorText(failure, 'create the workspace');
        if (window.mvToast) window.mvToast(addMsg.textContent, {error: true});
      }
    });
  }

  document.querySelectorAll('form.tenant-quota').forEach(function (form) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var id = form.getAttribute('data-tenant');
      var limit = limitFrom(form);
      if (limit === null) { rowMsg.textContent = 'Enter a daily limit above $0, or tick "No limit".'; return; }
      rowMsg.textContent = 'Saving…';
      try {
        await send(base(id) + '/quota', 'POST', {max_daily_dollars: limit});
        rowMsg.textContent = 'Daily limit for ' + nameOf(id) + ' is now ' + (limit ? '$' + limit + ' a day' : 'no limit') + '.';
        if (window.mvToastAfterReload) window.mvToastAfterReload(rowMsg.textContent);
        reloadSoon();
      } catch (failure) { rowMsg.textContent = errorText(failure, 'save the daily limit'); }
    });
  });

  document.querySelectorAll('[data-tenant-plan]').forEach(function (select) {
    select.addEventListener('change', async function () {
      var id = select.dataset.tenantPlan;
      var plan = select.value;
      var ok = await window.mvConfirm('Move ' + nameOf(id) + ' to the ' + select.options[select.selectedIndex].textContent + ' plan? The features it can use change straight away.', {okText: 'Change plan'});
      if (!ok) { select.value = select.dataset.current; return; }
      rowMsg.textContent = 'Saving…';
      try {
        await send(base(id) + '/plan', 'POST', {plan: plan});
        select.dataset.current = plan;
        rowMsg.textContent = nameOf(id) + ' is now on the ' + select.options[select.selectedIndex].textContent + ' plan.';
        if (window.mvToast) window.mvToast(rowMsg.textContent);
      } catch (failure) {
        select.value = select.dataset.current;
        rowMsg.textContent = errorText(failure, 'change the plan');
      }
    });
  });

  var questions = {
    suspend: ['Pause {name}? No new work can start in it until you resume it. Nothing is deleted.', 'Pause', 'pause the workspace', 'POST', '/suspend', '{name} is paused.'],
    resume: ['Resume {name}? Work can start in it again.', 'Resume', 'resume the workspace', 'POST', '/resume', '{name} is active again.'],
    delete: ['Remove {name} from the list? Access and member roles will be revoked. Its stored records stay on this computer for deliberate recovery.', 'Remove workspace', 'remove the workspace', 'DELETE', '', '{name} was removed.']
  };
  document.querySelectorAll('[data-tenant-action]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var row = button.closest('[data-tenant-row]');
      var id = row.dataset.tenantRow;
      var q = questions[button.dataset.tenantAction];
      var ok = await window.mvConfirm(q[0].replace('{name}', row.dataset.tenantName), {okText: q[1]});
      if (!ok) return;
      button.disabled = true;
      rowMsg.textContent = 'Saving…';
      try {
        await send(base(id) + q[4], q[3]);
        rowMsg.textContent = q[5].replace('{name}', row.dataset.tenantName);
        if (window.mvToastAfterReload) window.mvToastAfterReload(rowMsg.textContent);
        reloadSoon();
      } catch (failure) {
        rowMsg.textContent = errorText(failure, q[2]);
        button.disabled = false;
      }
    });
  });

  document.querySelectorAll('[data-tenant-restore]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var row = button.closest('[data-removed-workspace]');
      var name = row.dataset.removedName;
      var ok = await window.mvConfirm('Restore ' + name + ' with its old records? It will stay paused, and former members will not regain access.', {okText: 'Restore paused workspace'});
      if (!ok) return;
      button.disabled = true;
      rowMsg.textContent = 'Restoring…';
      try {
        var restored = await send(base(row.dataset.removedWorkspace) + '/restore', 'POST');
        var restoredName = restored.display_name || name;
        rowMsg.textContent = restoredName + ' was restored as paused. Review it before resuming.';
        if (window.mvToastAfterReload) window.mvToastAfterReload(rowMsg.textContent);
        reloadSoon();
      } catch (failure) {
        var plain = '';
        try { if (failure && typeof failure.clone === 'function') { var body = await failure.clone().json(); plain = window.mvPlainDetail ? window.mvPlainDetail(body && body.detail) : ''; } } catch (ignored) {}
        rowMsg.textContent = plain || errorText(failure, 'restore the workspace');
        button.disabled = false;
      }
    });
  });
})();
