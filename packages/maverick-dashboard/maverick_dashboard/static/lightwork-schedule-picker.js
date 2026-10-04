// Automations page: "Create a schedule". People choose what to run and when
// (every day, weekday, week or month at a time, in a time zone); the server
// turns those choices into the timetable and checks it. A custom timetable is
// accepted only from Technical details, and the server checks it too.
(() => {
  'use strict';
  const form = document.getElementById('auto-new-form');
  if (!form) return;
  const $ = id => document.getElementById('auto-new-' + id);
  let options = null, busy = false, previewTimer = null;
  function make(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function say(text) { $('preview').textContent = text; }
  function failText(failure, what) {
    return typeof window.mvErrorText === 'function' ? window.mvErrorText(failure, what) : 'Could not ' + what + '. Please try again.';
  }
  function human(value) { return typeof window.mvHuman === 'function' ? window.mvHuman(value) : String(value).replace(/[-_]+/g, ' '); }
  function when(epoch) {
    if (typeof window.mvWhen === 'function') return window.mvWhen(epoch);
    return (window.mvTimeAbsolute ? window.mvTimeAbsolute(epoch) : new Date(epoch * 1000).toLocaleString());
  }
  function what() { const pick = form.querySelector('input[name="what"]:checked'); return pick ? pick.value : 'workflow'; }
  function timetable() {
    const repeat = $('repeat').value;
    const days = Array.from($('days').querySelectorAll('input[type=checkbox]')).filter(c => c.checked).map(c => Number(c.value));
    const out = { repeat: repeat, at: /^\d{2}:\d{2}$/.test($('at').value) ? $('at').value : '09:00', days: days,
      day_of_month: Number($('dom').value) || 1, every_hours: Number($('every').value) || 1, zone: $('zone').value || 'UTC' };
    const advanced = $('advanced').value.trim();
    if (advanced) out.advanced = advanced.slice(0, 120);
    return out;
  }
  function sync() {
    const kind = what(), repeat = $('repeat').value;
    $('workflow-wrap').hidden = kind !== 'workflow';
    $('text-wrap').hidden = kind !== 'task';
    $('rerun-wrap').hidden = kind !== 'rerun';
    $('at-wrap').hidden = repeat === 'hourly';
    $('days').hidden = repeat !== 'weekly';
    $('dom-wrap').hidden = repeat !== 'monthly';
    $('every-wrap').hidden = repeat !== 'hourly';
  }
  function params() {
    const box = $('params');
    box.replaceChildren();
    const chosen = (options && options.workflows || []).find(w => w.name === $('workflow').value);
    (chosen ? chosen.params : []).forEach((name, index) => {
      const id = 'auto-new-param-' + index;
      const label = make('label', null, human(name));
      label.htmlFor = id;
      const input = make('input', 'input');
      input.id = id; input.dataset.param = name; input.maxLength = 2000;
      box.append(label, input);
    });
  }
  async function preview() {
    try {
      const response = await fetch('/api/v1/schedules/preview', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timetable: timetable() })
      });
      if (!response.ok) { say(failText(response, 'check the timetable')); return; }
      const data = await response.json();
      const runs = Array.isArray(data.next_runs) ? data.next_runs.filter(n => typeof n === 'number') : [];
      say(runs.length ? 'Next runs: ' + runs.map(when).join(' · ') : 'This timetable has no upcoming runs.');
    } catch (error) { say(failText(error, 'check the timetable')); }
  }
  function schedulePreview() { sync(); clearTimeout(previewTimer); previewTimer = setTimeout(preview, 300); }
  async function load() {
    try {
      const response = await fetch('/api/v1/schedules/options', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) return;
      options = await response.json();
    } catch (_) { return; }
    const workflow = $('workflow');
    workflow.replaceChildren(make('option', null, (options.workflows || []).length ? 'Choose a workflow' : 'No saved workflows yet'));
    workflow.firstChild.value = '';
    (options.workflows || []).forEach(w => { const o = make('option', null, human(w.title || w.name)); o.value = w.name; workflow.append(o); });
    const rerun = $('rerun');
    rerun.replaceChildren(make('option', null, (options.tasks || []).length ? 'Choose a task' : 'No earlier tasks yet'));
    rerun.firstChild.value = '';
    (options.tasks || []).forEach(t => { const o = make('option', null, t.title); o.value = String(t.id); rerun.append(o); });
    const zone = $('zone');
    let local = 'UTC';
    try { local = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch (_) { local = 'UTC'; }
    const zones = Array.isArray(options.zones) ? options.zones : ['UTC'];
    zone.replaceChildren();
    zones.forEach(z => { const o = make('option', null, z.replace(/_/g, ' ')); o.value = z; zone.append(o); });
    zone.value = zones.includes(local) ? local : 'UTC';
    schedulePreview();
  }
  form.addEventListener('change', event => { if (event.target === $('workflow')) params(); schedulePreview(); });
  $('advanced').addEventListener('input', schedulePreview);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    const kind = what();
    const body = { what: kind, timetable: timetable() };
    const title = $('title').value.trim();
    if (title) body.title = title.slice(0, 200);
    if (kind === 'workflow') {
      if (!$('workflow').value) { say('Choose a workflow first.'); $('workflow').focus(); return; }
      body.template = $('workflow').value;
      body.params = {};
      for (const input of $('params').querySelectorAll('input[data-param]')) {
        if (!input.value.trim()) { say('Fill in every answer the workflow needs.'); input.focus(); return; }
        body.params[input.dataset.param] = input.value.trim();
      }
    } else if (kind === 'task') {
      if (!$('text').value.trim()) { say('Describe the task first.'); $('text').focus(); return; }
      body.text = $('text').value.trim();
    } else {
      if (!$('rerun').value) { say('Choose the task to run again.'); $('rerun').focus(); return; }
      body.goal_id = Number($('rerun').value);
    }
    busy = true; $('save').disabled = true;
    try {
      const response = await fetch('/api/v1/schedules/add', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!response.ok) { say(failText(response, 'create the schedule')); return; }
      const data = await response.json();
      say('Schedule created. Next run ' + when(data.next_run) + '.');
      if (typeof window.mvToast === 'function') window.mvToast('Schedule created');
      document.dispatchEvent(new CustomEvent('lightwork:schedules-changed'));
    } catch (error) { say(failText(error, 'create the schedule')); }
    finally { busy = false; $('save').disabled = false; }
  });
  sync();
  document.getElementById('auto-new-sched').addEventListener('toggle', function once() {
    if (this.open && !options) load();
  });
  function fromHash() { if (location.hash === '#auto-new-sched') { document.getElementById('auto-new-sched').open = true; if (!options) load(); } }
  window.addEventListener('hashchange', fromHash);
  fromHash();
})();
