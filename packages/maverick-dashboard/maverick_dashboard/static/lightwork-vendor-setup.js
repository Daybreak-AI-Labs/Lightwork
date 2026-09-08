/* Reviewed setup uses the same tenant and named credential authority as work. */
(function () {
  'use strict';
  var el = function (id) { return document.getElementById('vs-' + id); };
  if (!el('panel')) return;
  var state = null, context = null, busy = false, locked = false;
  function ownerLabel(owner) { return owner.startsWith('user:') ? owner.slice(5) : owner; }
  function controls() {
    var blocked = !state || !state.readiness || state.readiness.deployment.supported !== true;
    ['ticket', 'document', 'confirm'].forEach(function (id) { el(id).disabled = busy || locked || blocked; });
    el('resource-owner').disabled = busy || locked || blocked || state.owner_locked;
    el('enable').disabled = busy || locked || blocked || state.modules_enabled || !el('confirm').checked;
    el('save').disabled = busy || locked || blocked || !state.modules_enabled || !el('resource-owner').value || !el('ticket').value || !el('confirm').checked;
    el('refresh').disabled = busy || locked;
  }
  function clear(message, lock) {
    state = null; locked = !!lock; el('ticket').replaceChildren(); el('document').replaceChildren();
    el('resource-owner').replaceChildren();
    el('owner').textContent = ''; el('confirm').checked = false; el('status').textContent = message; controls();
    el('readiness-summary').textContent = 'Setup has not been confirmed.'; el('next-actions').replaceChildren();
    el('deployment').textContent = 'Deployment status has not been confirmed.';
  }
  function renderReadiness(readiness) {
    var links = ['/connections', '/users', '#vs-resource-owner', '#vs-ticket', '#vs-enable', '#vs-confirm', '#vs-save', '#vs-deployment'];
    if (!readiness || !['setup_required', 'configured', 'blocked'].includes(readiness.state) || !readiness.deployment || typeof readiness.deployment.supported !== 'boolean' || typeof readiness.deployment.message !== 'string' || !Array.isArray(readiness.actions) || readiness.live_provider_verified !== false || readiness.actions.some(function (action) { return !action || typeof action.label !== 'string' || !links.includes(action.href); })) throw new Error('Setup readiness was incomplete. Refresh before making changes.');
    if ((readiness.state === 'blocked') === readiness.deployment.supported) throw new Error('Setup readiness was inconsistent. Refresh before making changes.');
    el('readiness-summary').textContent = readiness.state === 'blocked' ? 'Deployment needs attention' : readiness.state === 'configured' ? 'Setup saved; provider access still needs checking' : 'Setup required';
    el('deployment').textContent = readiness.deployment.message;
    el('next-actions').replaceChildren();
    readiness.actions.forEach(function (action) {
      var row = document.createElement('li'), link = document.createElement('a');
      link.href = action.href; link.textContent = action.label; row.appendChild(link); el('next-actions').appendChild(row);
    });
  }
  function render(data) {
    if (!data || !/^[a-f0-9]{64}$/.test(data.context) || !/^[a-f0-9]{64}$/.test(data.configuration) || !Array.isArray(data.connections) || !data.binding || typeof data.owner_locked !== 'boolean' || !Array.isArray(data.eligible_owners) || data.eligible_owners.some(function (owner) { return typeof owner !== 'string' || !owner.startsWith('user:') || owner.length > 256; }) || typeof data.binding.resource_owner !== 'string' || (data.owner_locked && !data.binding.resource_owner)) throw new Error('Setup response was incomplete.');
    if (context && data.context !== context) { clear('Your account or workspace changed. Reload the page.', true); return; }
    renderReadiness(data.readiness);
    state = data; context = data.context; el('confirm').checked = false;
    ['ticket', 'document'].forEach(function (name) {
      var select = el(name), placeholder = document.createElement('option');
      placeholder.value = ''; placeholder.textContent = name === 'ticket' ? 'Choose Jira connection' : 'Uploaded evidence only'; select.replaceChildren(placeholder);
      data.connections.filter(function (row) { return row.connector === (name === 'ticket' ? 'jira' : 'gdrive') && row.has_token; }).forEach(function (row) {
        var option = document.createElement('option'); option.value = row.name; option.textContent = row.name; select.appendChild(option);
      });
      select.value = data.binding[name + '_connection'] || '';
    });
    var owners = el('resource-owner'), placeholder = document.createElement('option');
    placeholder.value = ''; placeholder.textContent = 'Choose a workspace member'; owners.replaceChildren(placeholder);
    (data.owner_locked ? [data.binding.resource_owner] : data.eligible_owners).forEach(function (owner) {
      var option = document.createElement('option'); option.value = owner; option.textContent = ownerLabel(owner); owners.appendChild(option);
    });
    owners.value = data.binding.resource_owner;
    el('owner').textContent = data.owner_locked ? 'Existing vendor review owner: ' + ownerLabel(data.binding.resource_owner) + '. This setup cannot transfer ownership.' : 'Choose the owner for this workspace’s vendor records. Your saved credentials remain under your authority.';
    el('status').textContent = !data.readiness.deployment.supported ? 'Resolve the deployment issue above before changing setup.' : data.modules_enabled ? (data.revision ? 'Workspace setup saved. Connection access and evidence still require verification.' : 'Modules enabled. Choose the owner and connections, then save this workspace setup.') : 'Enable the workflow, then choose the owner and saved connections for this workspace.';
  }
  async function call(method, suffix, body) {
    if (busy || locked) return;
    busy = true; controls();
    try {
      var headers = {'Accept': 'application/json', 'Content-Type': 'application/json'};
      if (context) headers['X-Lightwork-Setup-Context'] = context;
      var response = await fetch('/api/v1/vendor-setup' + (suffix || ''), {method: method, headers: headers, credentials: 'same-origin', cache: 'no-store', body: body ? JSON.stringify(body) : undefined});
      if (response.status === 401 || response.status === 403) { clear('Sign in as an administrator and select a workspace to manage setup.', true); return; }
      var data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Setup was not confirmed. Refresh before retrying.');
      render(data);
    } catch (error) { clear(error.message || 'Setup unavailable. Refresh before retrying.', false); }
    finally { busy = false; controls(); }
  }
  function reviewed() { return {confirm: true, context: state.context, configuration: state.configuration}; }
  el('next-actions').addEventListener('click', function (event) {
    var link = event.target.closest('a');
    if (!link || !link.getAttribute('href').startsWith('#vs-')) return;
    var message = document.getElementById('vo-message'), inset = 64;
    if (message) {
      var style = getComputedStyle(message), top = parseFloat(style.top);
      if (Number.isFinite(top)) inset = top;
      if (style.position === 'sticky') inset += message.getBoundingClientRect().height;
    }
    // Keep native fragment navigation and keyboard focus below both sticky surfaces.
    el('panel').style.setProperty('--vs-anchor-offset', (inset + 16) + 'px');
  });
  el('form').addEventListener('change', function (event) { if (event.target !== el('confirm')) el('confirm').checked = false; controls(); });
  el('refresh').addEventListener('click', function () { call('GET'); });
  el('enable').addEventListener('click', function () { if (!el('enable').disabled) call('POST', '/enable', reviewed()); });
  el('form').addEventListener('submit', function (event) {
    event.preventDefault(); if (el('save').disabled) return;
    var ticket = state.connections.find(function (row) { return row.name === el('ticket').value; });
    var doc = state.connections.find(function (row) { return row.name === el('document').value; });
    call('PUT', '', Object.assign(reviewed(), {ticket: ticket.name, ticket_revision: ticket.revision, document: doc ? doc.name : '', document_revision: doc ? doc.revision : '', expected_revision: state.revision, resource_owner: el('resource-owner').value}));
  });
  call('GET');
}());
