/* Recovery uses the existing encrypted, account/workspace-bound draft service. */
(function () {
  'use strict';
  function initialize() {
    if (!window.mvDraftRecovery) return;
    document.querySelectorAll('form[action="/chat/send"], #answer-form').forEach(function (form) {
      if (form.dataset.draftReady) return;
      form.dataset.draftReady = 'true';
      var fields = Array.from(form.querySelectorAll('[name="title"], [name="description"], [name="message"]'));
      var sent = false;
      function read() {
        var value = {};
        fields.forEach(function (field) { value[field.name] = field.value; });
        return value;
      }
      var host = document.createElement('div'); form.parentElement.insertBefore(host, form);
      host.appendChild(form);
      var note = document.createElement('p'); note.className = 'muted';
      note.textContent = 'Recovery copies keep unsent text. Attachments must be selected again after reload; files are not kept in a recovery copy.';
      host.appendChild(note);
      form.mvRecovery = window.mvDraftRecovery.create({host: host,
        key: 'chat-composer:' + new URL(form.action).pathname, read: read,
        restore: function (draft) {
          fields.forEach(function (field) { if (typeof draft[field.name] === 'string') field.value = draft[field.name]; });
          note.textContent = 'Text restored for review. Select any attachments again before sending.';
          sent = false;
        }});
      window.addEventListener('beforeunload', function (event) {
        if (!sent && fields.some(function (field) { return field.value.trim(); })) {
          event.preventDefault(); event.returnValue = '';
        }
      });
      window.addEventListener('lightwork:composer-sent', function (event) {
        if (event.detail && event.detail.form === form) {
          sent = true;
          if (form.mvRecovery && !event.detail.recoveryCleared) form.mvRecovery.clear();
        }
      });
      form.addEventListener('input', function () { sent = false; });
    });
  }
  window.addEventListener('lightwork:drafts-ready', initialize);
  initialize();
})();
