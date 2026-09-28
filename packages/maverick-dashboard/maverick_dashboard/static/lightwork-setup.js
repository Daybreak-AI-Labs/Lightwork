(function () {
  'use strict';
  // Plain sentences only: a failed request becomes a readable message, never
  // the server's own detail text or a browser exception name.
  function errText(failure, action) {
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.';
  }
  function plainError(text) { var error = new Error(text); error.plain = true; return error; }
  var form = document.getElementById('setup-initialize');
  if (form) form.addEventListener('submit', async function (event) {
    event.preventDefault();
    var button = form.querySelector('button');
    var message = document.getElementById('setup-message');
    if (button.disabled || !form.reportValidity()) return;
    button.disabled = true; message.textContent = 'Saving initial settings…';
    try {
      var response = await fetch('/api/v1/setup/initialize', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({confirm:document.getElementById('setup-confirm').checked})});
      if (!response.ok) throw plainError(errText(response, 'save the initial settings'));
      var result = await response.json();
      if (!result || result.initialized !== true) throw plainError('Initial settings could not be confirmed.');
      window.location.reload();
    } catch (error) { message.textContent = error && error.plain ? error.message : errText(error, 'save the initial settings'); button.disabled = false; }
  });
  var download = document.getElementById('setup-download');
  if (download) download.addEventListener('click', async function () {
    if (download.disabled) return;
    download.disabled = true;
    var message = document.getElementById('setup-report-message');
    message.textContent = 'Preparing setup report…';
    try {
      var response = await fetch('/api/v1/setup/checks', {cache:'no-store'});
      if (!response.ok) throw plainError('The setup report is unavailable. An administrator can download it.');
      var result = await response.json();
      if (!result.runtime || !result.assurance) throw plainError('The setup report could not be verified.');
      var url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type:'application/json'}));
      var link = document.createElement('a'); link.href = url; link.download = 'Lightwork-setup-report.json'; link.click();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      message.textContent = 'Setup report downloaded. Live connections still need separate testing.';
    } catch (error) { message.textContent = error && error.plain ? error.message : errText(error, 'prepare the setup report'); }
    finally { download.disabled = false; }
  });
})();
