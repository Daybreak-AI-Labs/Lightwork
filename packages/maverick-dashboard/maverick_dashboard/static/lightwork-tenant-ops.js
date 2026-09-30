/* Workspaces page: set up a client in one step, and re-protect workspace keys. */
(function () {
  'use strict';
  var BASE = '/api/v1/admin/workspace-ops';
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
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
  async function call(path, method, body) {
    var init = {method: method || 'GET', credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}};
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    var response = await window.fetch(path, init);
    if (!response.ok) throw response;
    return response.json();
  }

  // ---- one-step client setup ---------------------------------------------------
  var onboard = $('tenant-onboard');
  if (onboard) {
    var msg = $('tenant-onboard-msg');
    var roles = onboard.querySelector('[data-onboard-roles]');
    call(BASE + '/roles').then(function (data) {
      roles.textContent = '';
      data.roles.forEach(function (r) {
        var o = el('option', null, r.label); o.value = r.value; if (r.value === 'admin') o.selected = true; roles.appendChild(o);
      });
      if (!data.invitations_on) msg.textContent = 'Invitations are turned off. Turn them on in Settings before setting up a client here.';
    }).catch(function () { /* the built-in Admin choice stays */ });
    onboard.addEventListener('submit', async function (event) {
      event.preventDefault();
      var data = new FormData(onboard);
      var noLimit = onboard.querySelector('[data-limit-toggle]').checked;
      var body = {
        display_name: String(data.get('display_name') || '').trim(),
        plan: String(data.get('plan') || 'free'),
        max_daily_dollars: noLimit ? 0 : Number(data.get('max_daily_dollars') || 0),
        admin_email: String(data.get('admin_email') || '').trim(),
        role: String(data.get('role') || 'admin')
      };
      if (!body.display_name || !body.admin_email) { msg.textContent = 'Enter the workspace name and the administrator\'s email.'; return; }
      msg.textContent = 'Setting up the workspace…';
      $('tenant-onboard-out').hidden = true;
      try {
        var result = await call(BASE + '/onboard', 'POST', body);
        msg.textContent = result.summary;
        $('tenant-onboard-link').value = result.invite_link;
        $('tenant-onboard-out').hidden = false;
        onboard.reset();
      } catch (failure) { msg.textContent = await failText(failure, 'set up the workspace'); }
    });
    $('tenant-onboard-copy').addEventListener('click', async function () {
      var field = $('tenant-onboard-link');
      try { await navigator.clipboard.writeText(field.value); msg.textContent = 'Invitation link copied.'; }
      catch (e) { field.select(); msg.textContent = 'Select the link and copy it.'; }
    });
  }

  // ---- re-protect workspace keys -----------------------------------------------
  var keys = $('tenant-keys');
  if (keys) {
    var keysMsg = $('tenant-keys-msg');
    var keysOut = $('tenant-keys-out');
    var rotate = $('tenant-keys-rotate');
    async function readKeys() {
      var current = keys.elements.current_key.files[0];
      var next = keys.elements.new_key.files[0];
      if (!current || !next) throw new Error('choose');
      if (current.size > 4096 || next.size > 4096) throw new Error('size');
      return {current_key: (await current.text()).trim(), new_key: (await next.text()).trim()};
    }
    function render(data) {
      keysOut.textContent = '';
      keysOut.appendChild(el('p', null, data.summary));
      if (data.rows && data.rows.length) {
        var table = el('table'), head = el('tr');
        ['Workspace', 'Result'].forEach(function (h) { var th = el('th', null, h); th.scope = 'col'; head.appendChild(th); });
        var thead = el('thead'); thead.appendChild(head); table.appendChild(thead);
        var tbody = el('tbody');
        data.rows.forEach(function (r) { var tr = el('tr'); tr.appendChild(el('td', null, r.workspace)); tr.appendChild(el('td', null, r.result)); tbody.appendChild(tr); });
        table.appendChild(tbody); keysOut.appendChild(table);
      }
      if (data.technical && data.technical.length) {
        var details = el('details'); details.appendChild(el('summary', null, 'Technical details (for your IT team)'));
        var ul = el('ul'); data.technical.forEach(function (t) { ul.appendChild(el('li', 'muted', t.text)); });
        details.appendChild(ul); keysOut.appendChild(details);
      }
    }
    function keyProblem(e) {
      return e && e.message === 'size' ? 'Those files are too large to be key files.' : 'Choose both key files first.';
    }
    keys.addEventListener('submit', async function (event) {
      event.preventDefault();
      rotate.hidden = true;
      var body;
      try { body = await readKeys(); } catch (e) { keysMsg.textContent = keyProblem(e); return; }
      keysMsg.textContent = 'Checking each workspace…';
      try {
        var data = await call(BASE + '/keys/preview', 'POST', body);
        render(data);
        rotate.hidden = !(data.total && data.rows.some(function (r) { return r.result === 'Would be re-protected'; }));
        keysMsg.textContent = '';
      } catch (failure) { keysMsg.textContent = await failText(failure, 'check the keys'); }
    });
    rotate.addEventListener('click', async function () {
      var body;
      try { body = await readKeys(); } catch (e) { keysMsg.textContent = keyProblem(e); return; }
      var ok = window.mvConfirm ? await window.mvConfirm('Re-protect every workspace\'s key with the new master key? Keep the current key until this shows no failures.', {okText: 'Re-protect'}) : false;
      if (!ok) return;
      body.confirm = true;
      rotate.hidden = true;
      keysMsg.textContent = 'Re-protecting workspaces…';
      try {
        var job = (await call(BASE + '/keys/rotate', 'POST', body)).job;
        for (var i = 0; i < 600 && job.state === 'running'; i++) {
          await new Promise(function (resolve) { setTimeout(resolve, 1000); });
          job = (await call(BASE + '/jobs/' + encodeURIComponent(job.id))).job;
        }
        if (job.state === 'done') { render(job.result); keysMsg.textContent = 'Done.'; }
        else keysMsg.textContent = job.error || 'Still working. Check back in a few minutes.';
      } catch (failure) { keysMsg.textContent = await failText(failure, 're-protect the workspaces'); }
      keys.reset();
    });
  }
})();
