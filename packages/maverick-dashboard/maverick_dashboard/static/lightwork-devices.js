(function () {
  'use strict';
  var form = document.getElementById('device-review-form');
  if (!form) return;
  var status = document.getElementById('device-status'), error = document.getElementById('device-error');
  var panel = document.getElementById('device-review'), list = document.getElementById('device-list');
  var review = null, context = '', busy = false, controller = null;
  async function request(path, body) {
    controller = new AbortController();
    try {
      return await window.mvFetchJSON(path, {method: body ? 'POST' : 'GET', body: body, signal: controller.signal, timeout: 15000, plainDetail: true,
        onAccess: function () { list.replaceChildren(); panel.hidden = true; review = null; }}, body ? 'change the device' : 'load the devices');
    } finally { controller = null; }
  }
  async function run(action) {
    if (busy) return;
    busy = true; error.hidden = true;
    document.querySelectorAll('#device-review-form button,#device-review button,#device-refresh,#device-list button').forEach(function (button) { button.disabled = true; });
    try { await action(); }
    catch (failure) { error.textContent = failure.name === 'AbortError' ? 'The reply was not confirmed. Refresh before repeating a change.' : failure.message; error.hidden = false; }
    finally {
      busy = false;
      document.querySelectorAll('#device-review-form button,#device-review button,#device-refresh,#device-list button').forEach(function (button) { button.disabled = false; });
    }
  }
  async function refresh() {
    var value = await request('/api/v1/devices');
    if (context && context !== value.context) { review = null; panel.hidden = true; }
    context = value.context; list.replaceChildren();
    value.devices.forEach(function (device) {
      var row = document.createElement('div'), description = document.createElement('p');
      description.textContent = device.label + ' · ' + (device.revoked ? 'Removed' : 'Expires ' + (window.mvTimeAbsolute ? window.mvTimeAbsolute(device.expires_at) : new Date(device.expires_at * 1000).toLocaleString())) + ' · ' + (device.actions ? 'Reviewed own-task actions enabled' : 'Read only');
      row.appendChild(description);
      if (!device.revoked) {
        var remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn btn--ghost'; remove.textContent = 'Remove ' + device.label;
        remove.addEventListener('click', function () { run(async function () {
          await request('/api/v1/devices/remove', {id: device.id, context: context});
          status.textContent = 'Device removed. Its next request will be denied.'; await refresh();
        }); }); row.appendChild(remove);
      }
      list.appendChild(row);
    });
    if (!value.devices.length) list.textContent = 'No devices are paired in this workspace.';
  }
  form.addEventListener('submit', function (event) { event.preventDefault(); run(async function () {
    var code = form.elements.code.value.replace(/[\s-]/g, '').toUpperCase();
    review = await request('/api/v1/devices/review?code=' + encodeURIComponent(code));
    document.getElementById('device-review-description').textContent = review.label + ' · Code ' + review.code + ' · Account ' + review.principal + ' · Workspace ' + (review.tenant || 'Default') + ' · ' + review.scope;
    document.getElementById('device-actions-option').hidden = !review.allow_actions;
    document.getElementById('device-actions').checked = false;
    panel.hidden = false; panel.focus();
  }); });
  document.getElementById('device-confirm').addEventListener('click', function () { run(async function () {
    if (!review) return;
    await request('/api/v1/devices/confirm', {code: review.code, context: review.context, allow_actions: document.getElementById('device-actions').checked});
    review = null; panel.hidden = true; status.textContent = 'Approved. Return to the device to finish connecting.'; await refresh();
  }); });
  document.getElementById('device-cancel').addEventListener('click', function () { review = null; panel.hidden = true; form.elements.code.focus(); });
  document.getElementById('device-refresh').addEventListener('click', function () { run(refresh); });
  var code = new URLSearchParams(location.search).get('code');
  if (code && /^[A-Z2-9]{12}$/.test(code)) form.elements.code.value = code;
  if (code) history.replaceState(null, '', location.pathname);
  window.addEventListener('pagehide', function () { if (controller) controller.abort(); review = null; });
  run(refresh);
}());
