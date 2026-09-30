/* Reviewed task-support drafts stay in memory until an explicit transfer. */
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
  var open = document.getElementById('issue-report-open');
  var more = document.getElementById('issue-report-more');
  var search = document.getElementById('issue-report-search');
  var query = document.getElementById('issue-report-query');
  var after = document.getElementById('issue-report-after');
  var before = document.getElementById('issue-report-before');
  var BASE = '/api/v1/support/issue-report';
  var next = null, task = null, busy = false, generation = 0;
  var selected = new URLSearchParams(window.location.search).get('task');
  function say(text) { status.textContent = text || ''; }
  function clear() { task = null; title.value = ''; body.value = ''; confirm.checked = false; panel.hidden = true; }
  function controls() {
    form.querySelector('button[type="submit"]').disabled = busy || !select.value;
    search.querySelector('button').disabled = busy;
    select.disabled = busy;
    more.disabled = busy || next === null;
    open.disabled = busy || !task || !confirm.checked;
  }
  async function call(path, payload) {
    var controller = new AbortController();
    var timer = window.setTimeout(function () { controller.abort(); }, 20000);
    try {
      var response = await window.fetch(BASE + path, {method: payload ? 'POST' : 'GET',
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: payload ? {'Content-Type': 'application/json'} : {}, body: payload ? JSON.stringify(payload) : undefined});
      if (!response.ok) {
        if (response.status === 401 || response.status === 403 || response.status === 404) clear();
        var error; try { error = await response.json(); } catch (_) { /* no readable error */ }
        throw new Error(typeof (error && error.detail) === 'string' ? error.detail : 'The request could not be completed. Try again.');
      }
      return await response.json();
    } finally { window.clearTimeout(timer); }
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
  [title, body].forEach(function (input) { input.addEventListener('input', function () { confirm.checked = false; controls(); }); });
  confirm.addEventListener('change', controls);
  function payload() { return {task: task, title: title.value, body: body.value}; }
  async function reviewed() {
    var data = await call('/review', payload());
    if (data.title !== title.value || data.body !== body.value) {
      title.value = data.title; body.value = data.body; confirm.checked = false;
      throw new Error('Additional secret text was removed. Review the updated draft before continuing.');
    }
    return data;
  }
  document.getElementById('issue-report-copy').addEventListener('click', async function () {
    if (!task || busy) return; busy = true; controls();
    try { await reviewed(); if (window.mvCopy) await window.mvCopy(title.value + '\n\n' + body.value); else throw new Error('Select the draft text and copy it with your keyboard.'); say('Draft copied. Nothing was sent to GitHub.'); }
    catch (error) { say(error.message); } finally { busy = false; controls(); }
  });
  document.getElementById('issue-report-download').addEventListener('click', async function () {
    if (!task || busy) return; busy = true; controls();
    try {
      await reviewed(); var url = URL.createObjectURL(new Blob([title.value + '\n\n' + body.value], {type: 'text/plain;charset=utf-8'}));
      var link = document.createElement('a'); link.href = url; link.download = 'Lightwork-task-support.txt'; link.click();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000); say('Draft downloaded. Nothing was sent to GitHub.');
    } catch (error) { say(error.message); } finally { busy = false; controls(); }
  });
  open.addEventListener('click', async function () {
    if (!task || busy || !confirm.checked) return; busy = true; controls();
    try {
      var data = await reviewed();
      // Same-tab navigation follows the final authority/scrubbing check and
      // avoids opening an external page before the reviewed payload is ready.
      window.location.assign(data.url);
    } catch (error) { say(error.message); } finally { busy = false; controls(); }
  });
  window.addEventListener('pagehide', function () { ++generation; clear(); });
  controls(); load(false);
})();
