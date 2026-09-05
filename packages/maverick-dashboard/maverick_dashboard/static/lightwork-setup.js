(function () {
  'use strict';
  var form = document.getElementById('setup-initialize');
  if (form) form.addEventListener('submit', async function (event) {
    event.preventDefault();
    var button = form.querySelector('button');
    var message = document.getElementById('setup-message');
    if (button.disabled || !form.reportValidity()) return;
    button.disabled = true; message.textContent = 'Saving initial settings…';
    try {
      var response = await fetch('/api/v1/setup/initialize', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({confirm:document.getElementById('setup-confirm').checked})});
      var result = await response.json();
      if (!response.ok || result.initialized !== true) throw new Error(typeof result.detail === 'string' ? result.detail : 'Initial settings could not be confirmed.');
      window.location.reload();
    } catch (error) { message.textContent = error.message || 'Could not reach Lightwork. Try again.'; button.disabled = false; }
  });
  var download = document.getElementById('setup-download');
  if (download) download.addEventListener('click', async function () {
    if (download.disabled) return;
    download.disabled = true;
    var message = document.getElementById('setup-report-message');
    message.textContent = 'Preparing setup report…';
    try {
      var response = await fetch('/api/v1/setup/checks', {cache:'no-store'});
      if (!response.ok) throw new Error('The setup report is unavailable. An administrator can download it.');
      var result = await response.json();
      if (!result.runtime || !result.assurance) throw new Error('The setup report could not be verified.');
      var url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type:'application/json'}));
      var link = document.createElement('a'); link.href = url; link.download = 'Lightwork-setup-report.json'; link.click();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      message.textContent = 'Setup report downloaded. Live connections still need separate testing.';
    } catch (error) { message.textContent = error.message || 'Could not prepare the report. Try again.'; }
    finally { download.disabled = false; }
  });
})();
