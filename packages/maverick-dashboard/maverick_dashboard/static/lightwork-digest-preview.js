/* "Preview today's summary": shows exactly what the daily summary would send
   (GET /api/v1/me/digest/preview, built by the code that sends it). Nothing is sent. */
(function () {
  'use strict';
  Array.prototype.forEach.call(document.querySelectorAll('[data-digest-preview]'), function (box) {
    if (box.dataset.digestPreviewReady) return;
    box.dataset.digestPreviewReady = '1';
    var status = box.querySelector('[data-digest-preview-status]');
    var notes = box.querySelector('[data-digest-preview-notes]');
    var subject = box.querySelector('[data-digest-preview-subject]');
    var text = box.querySelector('[data-digest-preview-text]');
    var button = box.querySelector('[data-digest-preview-load]');
    var busy = false;
    function clear() { notes.replaceChildren(); subject.textContent = ''; text.textContent = ''; }
    async function load() {
      if (busy) return;
      busy = true; button.disabled = true; status.textContent = 'Building today’s summary…';
      try {
        var response = await fetch('/api/v1/me/digest/preview', {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok) {
          clear();
          status.textContent = window.mvErrorText ? window.mvErrorText(response, 'preview your summary') : 'Could not preview your summary. Try again.';
          return;
        }
        var data = await response.json();
        clear();
        (Array.isArray(data.notes) ? data.notes : []).forEach(function (note) {
          var item = document.createElement('li'); item.textContent = String(note); notes.append(item);
        });
        subject.textContent = String(data.subject || '');
        text.textContent = String(data.text || '');
        status.textContent = data.would_send ? 'This is what goes out today.' : 'This is what it would contain; it is not being sent today.';
      } catch (error) {
        clear();
        status.textContent = window.mvErrorText ? window.mvErrorText(error, 'preview your summary') : 'Could not preview your summary. Try again.';
      } finally { busy = false; button.disabled = false; }
    }
    box.addEventListener('toggle', function () { if (box.open && !text.textContent) load(); });
    button.addEventListener('click', load);
  });
})();
