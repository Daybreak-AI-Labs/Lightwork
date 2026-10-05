/* Reviewed task-support drafts stay on this page until a local copy or download. */
(function () {
  'use strict';
  var form = document.getElementById('issue-report-form');
  if (!form) return;
  var select = document.getElementById('issue-report-task');
  var status = document.getElementById('issue-report-status');
  var panel = document.getElementById('issue-report-preview');
  var title = document.getElementById('issue-report-title-input');
  var body = document.getElementById('issue-report-body');
  var confirm = document.getElementById('issue-report-confirm');
  var copy = document.getElementById('issue-report-copy');
  var download = document.getElementById('issue-report-download');
  var more = document.getElementById('issue-report-more');
  var search = document.getElementById('issue-report-search');
  var query = document.getElementById('issue-report-query');
  var after = document.getElementById('issue-report-after');
  var before = document.getElementById('issue-report-before');
  var BASE = '/api/v1/support/issue-report';
  var next = null, task = null, approved = null, busy = false, generation = 0;
  var selected = new URLSearchParams(window.location.search).get('task');
  function say(text) { status.textContent = text || ''; }
  function clear() { task = null; approved = null; title.value = ''; body.value = ''; confirm.checked = false; panel.hidden = true; }
  function ready() { return approved && confirm.checked && approved.task === task && approved.title === title.value && approved.body === body.value; }
  function controls() {
    form.querySelector('button[type="submit"]').disabled = busy || !select.value;
    search.querySelector('button').disabled = busy;
    select.disabled = busy;
    more.disabled = busy || next === null;
    confirm.disabled = busy || !task;
    copy.disabled = busy || !ready();
    download.disabled = busy || !ready();
  }
  async function call(path, payload) {
    return window.mvFetchJSON(BASE + path, {method: payload ? 'POST' : 'GET', cache: 'no-store', timeout: 20000, body: payload,
      plainDetail: true, onAccess: clear, accessStatuses: [401, 403, 404]}, payload ? 'finish the support step' : 'load support');
  }
  function option(t) {
    var node = document.createElement('option'); node.value = String(t.id);
    var when = window.mvWhen ? window.mvWhen(t.when) : '';
    node.textContent = t.title + (when ? ' (' + when + ')' : ''); return node;
  }
  async function load(append) {
    if (busy) return;
    busy = true; controls(); var token = ++generation;
    var params = new URLSearchParams({query: query.value, after: after.value, before: before.value, offset: String(append ? next : 0)});
    if (selected && /^[1-9][0-9]*$/.test(selected)) params.set('selected', selected);
    say('Looking for accessible tasks that stopped with a problem.');
    try {
      var data = await call('/tasks?' + params.toString());
      if (token !== generation) return;
      if (!append) { select.replaceChildren(); select.appendChild(option({id: '', title: 'Choose a task'})); }
      data.tasks.forEach(function (t) { if (!Array.from(select.options).some(function (o) { return o.value === String(t.id); })) select.appendChild(option(t)); });
      if (data.selected) {
        if (!Array.from(select.options).some(function (o) { return o.value === String(data.selected.id); })) select.appendChild(option(data.selected));
        select.value = String(data.selected.id); selected = null;
      }
      next = data.next_offset;
      say(select.options.length > 1 ? (next === null ? 'All matching tasks are shown.' : 'More history is available. Use Find older tasks to continue.') :
        (next === null ? 'No accessible stopped tasks match this search.' : 'No matches in this part of history. Continue with Find older tasks.'));
    } catch (error) { say(error.name === 'AbortError' ? 'The task search took too long. Retry the search.' : error.message); }
    finally { busy = false; controls(); }
  }
  search.addEventListener('submit', function (event) { event.preventDefault(); clear(); load(false); });
  more.addEventListener('click', function () { if (next !== null) load(true); });
  select.addEventListener('change', function () { clear(); controls(); });
  form.addEventListener('submit', async function (event) {
    event.preventDefault(); if (busy || !select.value) return;
    clear(); busy = true; controls(); say('Preparing a local draft.');
    var chosen = Number(select.value);
    try {
      var data = await call('', {task: chosen});
      task = chosen; title.value = data.title; body.value = data.body; panel.hidden = false;
      say(data.summary); title.focus();
    } catch (error) { say(error.name === 'AbortError' ? 'Draft preparation timed out. Retry when ready.' : error.message); }
    finally { busy = false; controls(); }
  });
  [title, body].forEach(function (input) { input.addEventListener('input', function () {
    approved = null; confirm.checked = false; controls(); say('Draft changed. Review the exact fields again before copying or downloading.');
  }); });
  function payload() { return {task: task, title: title.value, body: body.value}; }
  async function reviewed() {
    var data = await call('/review', payload());
    if (!data || typeof data.title !== 'string' || typeof data.body !== 'string') throw new Error('The draft could not be checked. Try reviewing it again.');
    if (data.title !== title.value || data.body !== body.value) {
      title.value = data.title; body.value = data.body; confirm.checked = false;
      throw new Error('Additional secret text was removed. Review the updated draft before continuing.');
    }
    return data;
  }
  confirm.addEventListener('change', async function () {
    approved = null;
    if (!confirm.checked || !task || busy) { controls(); return; }
    busy = true; controls(); say('Checking the exact draft for sharing…');
    try {
      var data = await reviewed();
      approved = {task: task, title: data.title, body: data.body};
      say('Draft checked. Copy or download it, then use your organization’s support channel. Nothing was sent.');
    } catch (error) { confirm.checked = false; say(error.name === 'AbortError' ? 'Draft review timed out. Try again.' : error.message); }
    finally { busy = false; controls(); }
  });
  async function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* try the browser's older copy command */ }
    }
    var field, previous = document.activeElement;
    try {
      field = document.createElement('textarea'); field.value = text; field.style.position = 'fixed'; field.style.opacity = '0';
      document.body.appendChild(field); field.select(); return !!(document.execCommand && document.execCommand('copy'));
    } catch (_) { return false; }
    finally { if (field) field.remove(); if (previous && previous.isConnected && previous.focus) previous.focus(); }
  }
  copy.addEventListener('click', async function () {
    if (busy || !ready()) return;
    busy = true; controls();
    try {
      var copied = await copyText(approved.title + '\n\n' + approved.body);
      say(copied ? 'Reviewed draft copied. Nothing was sent. Share it through your organization’s support channel.' :
        'Your browser blocked copying. The draft is still here; download it or select and copy the text above.');
    } finally { busy = false; controls(); }
  });
  download.addEventListener('click', function () {
    if (busy || !ready()) return;
    try {
      var url = URL.createObjectURL(new Blob([approved.title + '\n\n' + approved.body], {type: 'text/plain;charset=utf-8'}));
      var link = document.createElement('a'); link.href = url; link.download = 'Lightwork-task-support.txt'; link.click();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      say('Reviewed draft downloaded. Nothing was sent. Share the file through your organization’s support channel.');
    } catch (_) { say('The download could not start. The draft is still here; try copying it instead.'); }
  });
  window.addEventListener('pagehide', function () { ++generation; clear(); });
  controls(); load(false);
})();
