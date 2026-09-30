// Review and remove selected files without clearing the rest of a draft.
(function () {
  'use strict';
  ['attach-input', 'reply-files'].forEach(function (id) {
    var input = document.getElementById(id);
    if (!input) return;
    var host = document.createElement('div'); host.className = 'panel'; host.hidden = true;
    var list = document.createElement('ul'); list.setAttribute('aria-label', 'Files selected for this message');
    var status = document.createElement('p'); status.setAttribute('role', 'status');
    var clear = document.createElement('button'); clear.type = 'button'; clear.className = 'btn btn--ghost'; clear.textContent = 'Remove all selected files';
    host.append(list, clear, status); input.closest('form').after(host);
    function remove(index) {
      try {
        var transfer = new DataTransfer();
        Array.from(input.files).forEach(function (file, i) { if (i !== index) transfer.items.add(file); });
        input.files = transfer.files; input.dispatchEvent(new Event('change', {bubbles: true}));
      } catch (_) { status.textContent = 'This browser cannot remove one file. Choose Remove all selected files, then select the files to keep.'; }
    }
    function render() {
      list.replaceChildren(); status.textContent = ''; host.hidden = !input.files.length;
      var seen = new Set();
      Array.from(input.files).forEach(function (file, index) {
        var row = document.createElement('li');
        var key = file.name + ':' + file.size + ':' + file.lastModified;
        row.append(document.createTextNode(file.name + ' · ' + (file.type || 'Unknown type') + ' · ' + Math.ceil(file.size / 1024) + ' KB' + (seen.has(key) ? ' · Possible duplicate' : '') + ' '));
        seen.add(key);
        var button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn--ghost';
        button.textContent = 'Remove ' + file.name; button.addEventListener('click', function () { remove(index); });
        row.append(button); list.append(row);
      });
      if (input.files.length > 128) status.textContent = 'Choose at most 128 files. The server checks file type, size and workspace limits before admission.';
    }
    clear.addEventListener('click', function () { input.value = ''; input.dispatchEvent(new Event('change', {bubbles: true})); });
    input.addEventListener('change', render);
    window.addEventListener('lightwork:composer-sent', function () { render(); });
    render();
  });
}());
