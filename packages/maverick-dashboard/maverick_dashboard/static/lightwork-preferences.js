(function () {
  'use strict';
  var form = document.getElementById('preference-form'); if (!form) return;
  var model = JSON.parse(document.getElementById('preference-data').textContent);
  var thread = JSON.parse(document.getElementById('preference-thread').textContent);
  var styles = JSON.parse(document.getElementById('preference-styles').textContent);
  var scope = document.getElementById('preference-scope'), status = document.getElementById('preference-status');
  var changes = {}, pins = [], busy = false;
  function renderPins() {
    var list = document.getElementById('preference-pins'); if (!list) return;
    list.replaceChildren();
    pins.forEach(function (href, index) {
      var row = document.createElement('li'); row.textContent = model.destinations[href] || 'Unavailable page';
      [['Move up', -1], ['Move down', 1], ['Remove', 0]].forEach(function (action) {
        var button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn--ghost'; button.textContent = action[0];
        button.disabled = busy || scope.value !== 'personal' || (action[1] === -1 && !index) || (action[1] === 1 && index === pins.length - 1);
        button.addEventListener('click', function () {
          if (action[1] === 0) pins.splice(index, 1);
          else { var swap = pins[index + action[1]]; pins[index + action[1]] = href; pins[index] = swap; }
          changes.favorites = pins.slice(); renderPins();
        }); row.appendChild(button);
      }); list.appendChild(row);
    });
  }
  function preview() {
    var field = form.querySelector('[data-pref="output_style"]'), custom = form.querySelector('[data-pref="custom_style"]');
    document.getElementById('preference-preview').textContent = field.value === 'custom' ? custom.value : (styles[field.value] || 'Ordinary response style.');
  }
  function render() {
    var effective = model.effective_by_scope[thread ? "conversation" : scope.value] || model;
    form.querySelectorAll('[data-pref]').forEach(function (field) {
      var key = field.dataset.pref;
      field.value = effective.values[key];
      field.disabled = model.locked.indexOf(key) !== -1 || (scope.value !== 'personal' && ['preferred_name','start_page'].indexOf(key) !== -1);
      var source = form.querySelector('[data-source-for="' + key + '"]');
      if (source) source.textContent = 'Effective source: ' + effective.sources[key] + (model.locked.indexOf(key) !== -1 ? ' · Set by organization policy' : '');
    });
    form.querySelectorAll('[data-inherit-pref]').forEach(function (box) { box.checked = false; box.disabled = model.locked.indexOf(box.dataset.inheritPref) !== -1; });
    form.querySelectorAll('[data-today-card]').forEach(function (box) { box.checked = model.values.today_cards.indexOf(box.dataset.todayCard) !== -1; box.disabled = scope.value !== 'personal'; });
    pins = model.values.favorites.slice(); renderPins(); preview(); changes = {};
  }
  form.addEventListener('input', function (event) { if (event.target.dataset.pref) { changes[event.target.dataset.pref] = event.target.value; preview(); } });
  form.addEventListener('change', function (event) {
    var target = event.target;
    if (target.dataset.pref) changes[target.dataset.pref] = target.value;
    if (target.dataset.inheritPref) {
      var key = target.dataset.inheritPref, field = form.querySelector('[data-pref="' + key + '"]');
      changes[key] = target.checked ? null : field.value;
    }
    if (target.dataset.todayCard) changes.today_cards = Array.from(form.querySelectorAll('[data-today-card]:checked')).map(function (box) { return box.dataset.todayCard; });
  });
  var previousScope = scope.value;
  scope.addEventListener('change', async function () {
    if (Object.keys(changes).length && !(await window.mvConfirm('Discard these preference drafts before changing scope?'))) { scope.value = previousScope; return; }
    previousScope = scope.value; render();
  });
  var add = document.getElementById('pin-add'); if (add) add.addEventListener('click', function () {
    if (scope.value !== 'personal') { status.textContent = 'Choose Personal to edit your pinned pages.'; return; }
    var href = document.getElementById('pin-choice').value;
    if (pins.indexOf(href) === -1) pins.push(href);
    changes.favorites = pins.slice(); renderPins();
  });
  var migration = document.getElementById('migrate-name'); if (migration) migration.addEventListener('click', function () {
    if (scope.value !== 'personal') return;
    form.querySelector('[data-pref="preferred_name"]').value = migration.dataset.name;
    changes.preferred_name = migration.dataset.name; status.textContent = 'Name filled for your account. Review and save to migrate it.';
  });
  form.addEventListener('submit', async function (event) {
    event.preventDefault(); if (busy || !Object.keys(changes).length) return;
    var review = Object.keys(changes).map(function (key) { return key.replace(/_/g, ' ') + ': ' + (changes[key] === null ? 'Use inherited value' : JSON.stringify(changes[key])); }).join('\n');
    if (!(await window.mvConfirm('Apply to ' + scope.value + ':\n' + review, {okText:'Save preferences'}))) return;
    busy = true; status.textContent = 'Saving preferences.';
    var controls = Array.from(form.querySelectorAll('input,select,textarea,button')).map(function (field) { var old = field.disabled; field.disabled = true; return [field,old]; });
    try {
      var response = await fetch('/api/v1/preferences', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({scope:scope.value, thread:thread, context:model.context, expected_revision:model.revision, changes:changes})});
      var result = await response.json();
      if (!response.ok) { status.textContent = response.status === 409 ? 'Preferences changed. Your draft is kept. Open Preferences in another tab to compare; nothing was overwritten.' : (typeof result.detail === 'string' ? result.detail : 'Preferences were not saved. Your draft is kept.'); return; }
      model = result; render(); status.textContent = 'Preferences saved for ' + scope.value + '. New work uses this style; running work is unchanged.';
    } catch (error) { status.textContent = 'Could not confirm this save. Your draft is kept. Check your connection and review current preferences before retrying.'; }
    finally { busy = false; controls.forEach(function (item) { item[0].disabled = item[1]; }); }
  });
  window.addEventListener('beforeunload', function (event) { if (Object.keys(changes).length) { event.preventDefault(); event.returnValue = ''; } });
  render();
})();
