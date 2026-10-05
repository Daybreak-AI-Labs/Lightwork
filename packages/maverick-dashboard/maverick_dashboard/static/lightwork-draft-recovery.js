/* Private encrypted recovery copies; every restore is an explicit local edit. */
(function (root) {
  'use strict';
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  var OFFLINE = 'Lightwork is not responding. Your editor contents have been kept. Check your connection and try again.';
  // A failed request reads as a plain sentence, never the server's own text.
  function failText(response, action, fallback) {
    return root.mvErrorText ? root.mvErrorText(response, action) : fallback;
  }
  async function send(url, init) {
    try { return await root.fetch(url, init); } catch (_error) { throw new Error(OFFLINE); }
  }
  // Readable field-by-field view of a draft: {"fields":{"due_date":"x"}} ->
  // [{key:'fields.due_date', name:'due_date', value:'x'}]. Internal
  // bookkeeping (format markers, schema variants) is left out.
  function flatten(value, path, name, out) {
    if (out.length >= 200) return out;
    if (Array.isArray(value)) {
      if (value.every(function (item) { return item === null || typeof item !== 'object'; })) {
        out.push({key:path, name:name, value:value.map(show).join(', ')});
      } else value.forEach(function (item, index) { flatten(item, path + '.' + index, name + ' ' + (index + 1), out); });
    } else if (value && typeof value === 'object') {
      Object.keys(value).forEach(function (key) {
        if (!path && (key === 'format' || key === 'variants')) return;
        flatten(value[key], path ? path + '.' + key : key, (!path && key === 'fields') ? '' : key, out);
      });
    } else out.push({key:path, name:name, value:show(value)});
    return out;
  }
  function show(value) {
    if (value === true) return 'Yes';
    if (value === false) return 'No';
    if (value === null || value === undefined || value === '') return '(empty)';
    var text = String(value);
    return text.length > 300 ? text.slice(0, 300) + '…' : text;
  }
  function assertSafe(value, depth) {
    if ((depth || 0) > 25) throw new Error('This draft is too complex for recovery storage. Save it before leaving.');
    if (Array.isArray(value)) value.forEach(function (child) { assertSafe(child, (depth || 0) + 1); });
    else if (value && typeof value === 'object') Object.keys(value).forEach(function (key) {
      var normalized = key.replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase().replace(/-/g, '_');
      if (/(^|_)(password|passwd|passphrase|secret|credentials|api_key|apikey|token|client_secret|private_key|authorization|cookie)($|_)/i.test(normalized) &&
          !/_(ref|env|reference|id)$/.test(normalized) && value[key] !== '' && value[key] !== null && value[key] !== false) {
        throw new Error('Recovery paused: remove credentials from this draft or use saved connection references. Credentials are never stored in recovery copies.');
      }
      assertSafe(value[key], (depth || 0) + 1);
    });
    else if (typeof value === 'string') {
      if (/\b(password|passwd|api[_ -]?key|client[_ -]?secret|access[_ -]?token|authorization)\s*[=:]\s*\S+/i.test(value) || /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/.test(value)) throw new Error('Recovery paused: remove credentials or use saved connection references.');
      if (/^\s*[\[{]/.test(value)) {
        var decoded; try { decoded = JSON.parse(value); } catch (_error) { return; }
        assertSafe(decoded, (depth || 0) + 1);
      }
    }
  }
  async function slotFor(value) {
    var digest = await root.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), function (n) { return n.toString(16).padStart(2, '0'); }).join('');
  }
  function create(options) {
    var doc = root.document, meta = doc.querySelector('meta[name="lightwork-draft-context"]');
    var context = meta && meta.content;
    if (!context || !options.host || options.host.closest('[data-draft-recovery="off"]')) return null;
    var panel = doc.createElement('section'); panel.className = 'draft-recovery'; panel.setAttribute('aria-label', 'Draft recovery');
    var status = doc.createElement('p'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    var actions = doc.createElement('div'); actions.className = 'draft-recovery__actions';
    var preview = doc.createElement('details'), summary = doc.createElement('summary'), contents = doc.createElement('div');
    summary.textContent = 'Compare recovery copy and current draft'; preview.append(summary, contents);
    function button(label, run) { var b = doc.createElement('button'); b.type = 'button'; b.className = 'btn btn--ghost'; b.textContent = label; b.addEventListener('click', run); actions.append(b); return b; }
    var restore = button('Restore recovery copy', restoreCopy), keep = button('Keep current draft', keepCurrent);
    var discard = button('Discard recovery copy', discardCopy), retry = button('Retry recovery', retryRecovery);
    panel.append(status, actions, preview); options.host.prepend(panel);
    // Rebuild the comparison whenever it is opened so it shows the latest edits.
    preview.addEventListener('toggle', function () { if (preview.open && remote) controls(); });
    var storage = doc.createElement('details'), storageTitle = doc.createElement('summary'), storageStatus = doc.createElement('p');
    var storageRefresh = doc.createElement('button'), storageRetire = doc.createElement('button'), storageSnapshot = null, storageBusy = false;
    storageTitle.textContent = 'Manage recovery storage'; storageStatus.setAttribute('role', 'status');
    storageRefresh.type = storageRetire.type = 'button'; storageRefresh.className = storageRetire.className = 'btn btn--ghost';
    storageRefresh.textContent = 'Refresh storage summary'; storageRetire.textContent = 'Free discarded recovery space'; storageRetire.disabled = true;
    storage.append(storageTitle, storageStatus, storageRefresh, storageRetire); panel.append(storage);
    function storageControls() { storageRefresh.disabled = storageBusy; storageRetire.disabled = storageBusy || !storageSnapshot || !storageSnapshot.discarded; }
    async function storageCall(body) {
      var response = await send('/api/v1/drafts/storage' + (body ? '/retire' : ''), {method:body ? 'POST' : 'GET', cache:'no-store',
        headers:{'Content-Type':'application/json','X-Lightwork-Draft-Context':context}, ...(body ? {body:JSON.stringify(body)} : {})});
      if (response.status === 401 || response.status === 403) throw new Error('Your access changed. Sign in again before managing recovery storage.');
      var data; try { data = await response.json(); } catch (_error) { throw new Error('Recovery storage could not confirm this request. Refresh the summary before trying again.'); }
      if (!response.ok) throw new Error(failText(response, body ? 'free the recovery space' : 'load the recovery storage summary',
        response.status === 409 ? 'Recovery storage changed. Refresh the summary before trying again.' : 'Recovery storage is unavailable. Refresh the page and try again.'));
      if (!data || data.context !== context || !/^[0-9a-f]{64}$/.test(data.version) ||
          !['capacity','saved','discarded','available'].every(function (key) { return Number.isInteger(data[key]) && data[key] >= 0; }) ||
          data.saved + data.discarded + data.available !== data.capacity) throw new Error('The recovery storage summary could not be verified. Refresh the page and try again.');
      return data;
    }
    function showStorage(data, prefix) {
      storageSnapshot = data;
      storageStatus.textContent = (prefix || '') + data.saved + ' saved recovery copies, ' + data.discarded + ' discarded copies, and ' + data.available + ' available spaces. Only already discarded copies can be retired here.';
    }
    async function refreshStorage() {
      if (storageBusy || disposed) return;
      storageBusy = true; storageSnapshot = null; storageControls();
      try { showStorage(await storageCall()); } catch (error) { storageStatus.textContent = error.message; }
      finally { storageBusy = false; storageControls(); }
    }
    async function retireStorage() {
      if (storageBusy || !storageSnapshot || !storageSnapshot.discarded || disposed) return;
      var selected = storageSnapshot; storageBusy = true; storageControls();
      try {
        if (!(await confirmed('Free space from ' + selected.discarded + ' already discarded recovery copies? Open editors will check their saved version before saving again.', 'Free space'))) return;
        showStorage(await storageCall({expected_version:selected.version}), 'Discarded recovery space freed. ');
      } catch (error) { storageSnapshot = null; storageStatus.textContent = error.message; }
      finally { storageBusy = false; storageControls(); }
    }
    storage.addEventListener('toggle', function () { if (storage.open) refreshStorage(); });
    storageRefresh.addEventListener('click', refreshStorage); storageRetire.addEventListener('click', retireStorage);
    var slot, revision = 0, remote = null, blocked = false, initialized = false, timer, running = false;
    var changed = false, disposed = false, lastSaved = JSON.stringify(options.read()), pendingOperation = null, failed = false;
    function read() { var value = options.read(); assertSafe(value); return JSON.parse(JSON.stringify(value)); }
    function controls() {
      restore.hidden = !remote; discard.hidden = !remote; keep.hidden = !blocked || !initialized; retry.hidden = !failed;
      preview.hidden = !remote;
      [restore, keep, discard, retry].forEach(function (b) { b.disabled = running; });
      if (remote) {
        try { compare(remote, read()); }
        catch (_error) { contents.textContent = 'Current draft comparison is unavailable until credentials are removed. The recovery copy has been kept.'; }
      }
    }
    // The label a person sees for a stored field: the editor's own label for
    // that control when there is one, else the field name in words.
    function fieldLabel(row) {
      var control = null;
      try { control = options.host.querySelector('[name="' + String(row.name).replace(/["\\]/g, '\\$&') + '"]') ||
        (/^[A-Za-z][\w-]*$/.test(row.name) ? doc.getElementById(row.name) : null); } catch (_error) { control = null; }
      var text = control && control.labels && control.labels[0] ? control.labels[0].textContent :
        control ? control.getAttribute('aria-label') : '';
      text = String(text || '').replace(/\s+/g, ' ').trim();
      if (text) return text;
      if (root.mvHuman) return root.mvHuman(row.name || row.key);
      var words = String(row.name || row.key).replace(/[_.-]+/g, ' ').trim();
      return words.charAt(0).toUpperCase() + words.slice(1);
    }
    function compare(saved, current) {
      var before = flatten(saved, '', '', []), after = flatten(current, '', '', []), rows = {}, order = [];
      before.forEach(function (row) { if (!rows[row.key]) { rows[row.key] = {row:row, saved:'(empty)', current:'(empty)'}; order.push(row.key); } rows[row.key].saved = row.value; });
      after.forEach(function (row) { if (!rows[row.key]) { rows[row.key] = {row:row, saved:'(empty)', current:'(empty)'}; order.push(row.key); } rows[row.key].current = row.value; });
      var changedRows = order.filter(function (key) { return rows[key].saved !== rows[key].current; });
      contents.replaceChildren();
      var note = doc.createElement('p'); note.className = 'muted';
      note.textContent = changedRows.length
        ? changedRows.length + (changedRows.length === 1 ? ' field differs' : ' fields differ') + ' between the recovery copy and your current draft.' +
          (order.length > changedRows.length ? ' Fields that are the same are not shown.' : '')
        : 'The recovery copy matches your current draft.';
      contents.append(note);
      if (!changedRows.length) return;
      var table = doc.createElement('table'), head = doc.createElement('thead'), headRow = doc.createElement('tr'), bodyEl = doc.createElement('tbody');
      ['Field', 'Recovery copy', 'Current draft'].forEach(function (label) { var th = doc.createElement('th'); th.scope = 'col'; th.textContent = label; headRow.append(th); });
      head.append(headRow); table.append(head, bodyEl);
      changedRows.forEach(function (key) {
        var tr = doc.createElement('tr'), th = doc.createElement('th'), a = doc.createElement('td'), b = doc.createElement('td');
        th.scope = 'row'; th.textContent = fieldLabel(rows[key].row); a.textContent = rows[key].saved; b.textContent = rows[key].current;
        tr.append(th, a, b); bodyEl.append(tr);
      });
      contents.append(table);
    }
    async function call(method, body) {
      var response = await send('/api/v1/drafts/' + slot, {method:method, cache:'no-store', headers:{'Content-Type':'application/json','X-Lightwork-Draft-Context':context}, ...(body ? {body:JSON.stringify(body)} : {})});
      var data;
      try { data = await response.json(); } catch (_error) { throw new Error('Recovery storage returned an unreadable response. Your editor contents have been kept.'); }
      if (!response.ok) {
        var error = new Error(response.status === 409
          ? 'A newer recovery copy was saved from another tab or window. Review it before continuing.'
          : failText(response, 'save the recovery copy', 'Recovery storage could not confirm this request. Refresh the page and try again.'));
        error.status = response.status; throw error;
      }
      if (!data || data.context !== context || data.slot !== slot || !Number.isInteger(data.revision) || data.revision < 0 || !Object.prototype.hasOwnProperty.call(data, 'payload') || (data.payload !== null && (typeof data.payload !== 'object' || Array.isArray(data.payload)))) throw new Error('The recovery copy could not be verified for this workspace. Refresh the page and try again.');
      assertSafe(data.payload); return data;
    }
    async function initialize() {
      if (running || disposed) return;
      running = true; controls();
      try {
        if (!slot) slot = await slotFor(options.key);
        var data = await call('GET'); revision = data.revision; remote = data.payload;
        initialized = true; blocked = !!remote; failed = false;
        if (lastSaved === undefined) lastSaved = JSON.stringify(read());
        status.textContent = remote ? 'A recovery copy is available. Compare it before choosing Restore or Keep current draft.' : 'Draft recovery is ready. Changes are saved privately; attachments and credential fields are excluded.';
      } catch (error) { failed = true; status.textContent = error.message || 'Draft recovery is unavailable. Save your work before leaving.'; }
      finally { running = false; controls(); if (changed && !blocked && !failed) schedule(); }
    }
    async function persist(payload) {
      if (running || disposed) return false;
      running = true; controls();
      var operation = pendingOperation || {expected_revision:revision, operation_id:root.crypto.randomUUID(), payload:payload};
      pendingOperation = operation;
      try {
        var data = await call('PUT', operation);
        if (data.revision !== operation.expected_revision + 1 || !same(data.payload, operation.payload)) throw new Error('The saved recovery copy did not match the submitted draft. Try again.');
        revision = data.revision; remote = data.payload; pendingOperation = null; failed = false; lastSaved = JSON.stringify(operation.payload);
        status.textContent = data.payload ? 'Recovery copy saved. You control when this work is saved, run, or published.' : 'Recovery copy discarded. Your editor contents have been kept.';
        return true;
      } catch (error) {
        failed = true;
        if (error.status === 409) {
          pendingOperation = null; blocked = true;
          try { var latest = await call('GET'); revision = latest.revision; remote = latest.payload; } catch (_readError) { /* Preserve the original conflict. */ }
        } else if (error.status && error.status < 500) pendingOperation = null;
        status.textContent = error.message; return false;
      } finally { running = false; controls(); }
    }
    async function saveChanged() {
      if (!initialized || blocked || running || !changed || disposed || pendingOperation) return;
      var payload;
      try { payload = read(); } catch (error) { failed = true; status.textContent = error.message; controls(); return; }
      if (JSON.stringify(payload) === lastSaved) { changed = false; return; }
      changed = false;
      if (!(await persist(payload))) { changed = true; return; }
      if (changed) schedule();
    }
    function schedule() { root.clearTimeout(timer); timer = root.setTimeout(saveChanged, 600); }
    function markChanged(event) {
      if (event && panel.contains(event.target)) return;
      try { changed = JSON.stringify(read()) !== lastSaved; } catch (_error) { changed = true; }
      if (changed && initialized && !blocked) schedule();
    }
    async function confirmed(message, okText) { return typeof root.mvConfirm === 'function' ? root.mvConfirm(message, {okText: okText}) : root.confirm(message); }
    async function review(message, action, okText) {
      if (running || !initialized) return;
      running = true; controls();
      try {
        var before = JSON.stringify(read()), candidate = remote, expected = revision;
        if (!(await confirmed(message, okText))) return;
        var latest = await call('GET');
        if (latest.revision !== expected || !same(latest.payload, candidate)) {
          remote = latest.payload; revision = latest.revision; blocked = true;
          status.textContent = 'The recovery copy changed. Review the newest copy before continuing.'; return;
        }
        if (before !== JSON.stringify(read())) { status.textContent = 'Your newer editor changes have been kept. Review again before continuing.'; return; }
        await action(candidate);
      } catch (error) { status.textContent = error.message || 'Could not restore this copy. Current work has been kept. Try again.'; }
      finally { running = false; controls(); }
    }
    async function restoreCopy() {
      if (!remote) return;
      await review('Replace the current editor contents with this recovery copy? Nothing will run or publish.', async function (candidate) {
        await options.restore(candidate);
        blocked = false; changed = false; failed = false; lastSaved = JSON.stringify(read());
        status.textContent = 'Recovery copy restored for review. Nothing was run, saved to the live resource, or published.';
      }, 'Restore recovery copy');
    }
    async function replaceReviewed(payload, message, okText) {
      await review(message, async function () {
        running = false; pendingOperation = null;
        if (await persist(payload())) { blocked = false; changed = false; lastSaved = JSON.stringify(read()); }
      }, okText);
    }
    async function keepCurrent() { await replaceReviewed(read, 'Replace the recovery copy with the current editor draft? The live resource is unchanged.', 'Keep current draft'); }
    async function discardCopy() { await replaceReviewed(function () { return null; }, 'Discard the saved recovery copy? It can\'t be restored. Your current editor contents will stay here.', 'Discard recovery copy'); }
    async function retryRecovery() {
      if (running) return;
      if (!initialized) { await initialize(); return; }
      if (blocked) { status.textContent = 'Review the recovery copy, then choose Restore, Keep current draft, or Discard.'; return; }
      if (pendingOperation) {
        if (await persist(pendingOperation.payload)) { changed = JSON.stringify(read()) !== lastSaved; if (changed) schedule(); }
      } else { changed = true; await saveChanged(); }
    }
    options.host.addEventListener('input', markChanged); options.host.addEventListener('change', markChanged);
    function unload(event) { if (changed || pendingOperation || running) { event.preventDefault(); event.returnValue = ''; } }
    root.addEventListener('beforeunload', unload);
    status.textContent = 'Checking for a recovery copy…'; controls();
    var ready = initialize();
    return {ready:ready, changed:markChanged, save:saveChanged, panel:panel,
      clear:async function () {
        await ready;
        if (running || blocked) return false;
        root.clearTimeout(timer);
        if (!(await persist(null))) return false;
        changed = false; lastSaved = JSON.stringify(read()); return true;
      },
      dispose:function () { disposed = true; root.clearTimeout(timer); root.removeEventListener('beforeunload', unload); options.host.removeEventListener('input', markChanged); options.host.removeEventListener('change', markChanged); }};
  }
  root.mvDraftRecovery = {create:create, assertSafe:assertSafe};
  root.dispatchEvent(new Event('lightwork:drafts-ready'));
})(typeof window === 'undefined' ? globalThis : window);
