/* Full setup guide: check and install a prepared settings file. */
(function () {
  'use strict';
  var form = document.getElementById('guide-file-form');
  if (!form) return;
  var input = document.getElementById('guide-file-input');
  var install = document.getElementById('guide-file-install');
  var status = document.getElementById('guide-file-status');
  var out = document.getElementById('guide-file-out');
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
  async function send(path, file) {
    var response = await window.fetch(path, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/octet-stream'}, body: file});
    if (!response.ok) throw response;
    return response.json();
  }
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    install.hidden = true;
    out.textContent = '';
    var file = input.files[0];
    if (!file) { status.textContent = 'Choose a settings file first.'; return; }
    status.textContent = 'Checking the file…';
    try {
      var data = await send('/api/v1/setup/settings-file/check', file);
      status.textContent = data.summary;
      if (data.technical && data.technical.length) {
        var details = el('details'); details.appendChild(el('summary', null, 'Technical details (for your IT team)'));
        var ul = el('ul'); data.technical.forEach(function (t) { ul.appendChild(el('li', 'muted', t.text)); });
        details.appendChild(ul); out.appendChild(details);
      }
      install.hidden = !data.valid;
    } catch (failure) { status.textContent = await failText(failure, 'check the file'); }
  });
  install.addEventListener('click', async function () {
    var file = input.files[0];
    if (!file) { status.textContent = 'Choose a settings file first.'; return; }
    var ok = window.mvConfirm ? await window.mvConfirm('Replace this installation\'s settings with this file? The current settings file is kept as a backup copy.', {okText: 'Install'}) : false;
    if (!ok) return;
    install.hidden = true;
    status.textContent = 'Installing…';
    try {
      var data = await send('/api/v1/setup/settings-file/install?confirm=true', file);
      status.textContent = data.summary;
    } catch (failure) { status.textContent = await failText(failure, 'install the settings file'); }
  });
})();
