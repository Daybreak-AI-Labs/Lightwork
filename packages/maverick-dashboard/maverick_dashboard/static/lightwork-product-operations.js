/* Registered native controls: fresh source -> reviewed input -> explicit confirmation. */
(function () {
  'use strict';
  var panel = document.getElementById('mv-copilot'), host = document.getElementById('mv-product-operations');
  var status = document.getElementById('mv-product-operation-status'), buttons = document.getElementById('mv-product-operation-buttons');
  var formHost = document.getElementById('mv-product-operation-form');
  if (!panel || !host || !status || !buttons || !formHost) return;
  var root = '/api/v1/copilot/product-operations', generation = 0, busy = false, catalog = null, selected = null;
  var uncertainty = '', overrideSelection = null;
  function node(tag, text, parent) { var element = document.createElement(tag); if (text !== undefined) element.textContent = String(text); if (parent) parent.appendChild(element); return element; }
  function clear(element) { element.replaceChildren(); }
  function button(text, parent, handler) { var control = node('button', text, parent); control.type = 'button'; control.className = 'btn btn--ghost'; control.addEventListener('click', handler); return control; }
  function context() {
    if (overrideSelection) return overrideSelection;
    var goal = window.location.pathname.match(/^\/chat\/goal\/(\d+)(?:\/|$)/);
    if (goal) return {kind: 'goal', id: Number(goal[1]), goal_id: Number(goal[1])};
    var thread = new URLSearchParams(window.location.search).get('thread');
    if (window.location.pathname === '/preferences' && thread && /^\d+$/.test(thread)) return {kind: 'preferences', thread: Number(thread)};
    return {};
  }
  function payload() { return {page: window.location.pathname, selection: context()}; }
  function active(started) { return generation === started && !panel.hidden; }
  function lock(value) {
    busy = value;
    buttons.querySelectorAll('button').forEach(function (element) { element.disabled = value || element.dataset.available !== 'true'; });
    formHost.querySelectorAll('input,select,textarea,button').forEach(function (element) { element.disabled = value; });
  }
  function errorMessage(result, fallback) {
    var detail = result && result.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    return fallback;
  }
  async function request(route, body) {
    var response = await fetch(root + route, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify(body)});
    var value = null; try { value = await response.json(); } catch (_) { /* No raw server responses. */ }
    if (!response.ok) { var error = new Error(errorMessage(value, 'The current control is unavailable. Open its native page to check status.')); error.status = response.status; throw error; }
    if (!value || typeof value !== 'object') throw new Error('The response was unreadable. Check native status before another attempt.');
    return value;
  }
  function renderData(parent, value) {
    var remaining = 160;
    function append(target, item, depth) {
      if (--remaining < 0 || depth > 6) { node('p', 'More details are available on the native page.', target); return; }
      if (item === null || item === undefined) { node('span', 'Not specified', target); return; }
      if (typeof item !== 'object') { node('span', typeof item === 'boolean' ? (item ? 'Yes' : 'No') : item, target); return; }
      var list = node('dl', undefined, target);
      Object.entries(item).slice(0, 35).forEach(function (entry) {
        node('dt', Array.isArray(item) ? String(Number(entry[0]) + 1) : entry[0].replace(/_/g, ' '), list);
        append(node('dd', undefined, list), entry[1], depth + 1);
      });
    }
    append(parent, value, 0);
  }
  function nativeLink(item, parent) {
    if (typeof item.direct_ui !== 'string' || !item.direct_ui.startsWith('/') || item.direct_ui.startsWith('//')) return;
    var link = node('a', 'Open native controls', parent); link.className = 'btn btn--ghost'; link.href = item.direct_ui;
  }
  async function refresh() {
    var started = generation, previous = catalog;
    status.textContent = 'Checking your current controls…';
    try {
      var fresh = await request('/catalog', payload());
      if (!active(started)) return null;
      if (fresh.schema !== 1 || !Array.isArray(fresh.operations) || JSON.stringify(fresh.selection) !== JSON.stringify(context())) throw new Error('The current record could not be confirmed.');
      if (previous && previous.context !== fresh.context) {
        clear(formHost); selected = null;
        document.dispatchEvent(new CustomEvent('lightwork:copilot-context-changed'));
      }
      catalog = fresh; host.hidden = fresh.operations.length === 0; clear(buttons);
      fresh.operations.forEach(function (item) {
        var control = button(item.label, buttons, function () { openOperation(item.id); });
        control.dataset.available = item.available === true ? 'true' : 'false'; control.disabled = busy || !item.available;
        if (item.blocked) control.title = item.blocked;
      });
      status.textContent = uncertainty || 'Current controls for this page. Changes require a separate review and confirmation.';
      return fresh;
    } catch (error) {
      if (active(started)) { catalog = null; clear(buttons); clear(formHost); selected = null; host.hidden = false; status.textContent = error.message; }
      return null;
    }
  }
  function createField(spec, parent, controls) {
    var row = node('div', undefined, parent), id = 'mv-operation-' + spec.name;
    var label = node('label', spec.label || spec.name, row); label.htmlFor = id;
    var control;
    function install(choices) {
      if (control) control.remove();
      control = node(choices ? 'select' : spec.kind === 'textarea' ? 'textarea' : 'input', undefined, row);
      control.id = id; control.name = spec.name; control.className = 'input'; control.required = spec.required === true;
      if (choices) choices.forEach(function (choice, index) { var option = node('option', choice.label, control); option.value = String(index); option._typedValue = choice.value; if (choice.value === spec.value) option.selected = true; });
      else { control.type = spec.kind === 'boolean' ? 'checkbox' : spec.kind === 'number' ? 'number' : 'text'; if (spec.kind === 'number') { control.step = 'any'; control.min = '0'; } control.maxLength = spec.max_length || 2000; control.value = spec.value === null || spec.value === undefined ? '' : String(spec.value); if (spec.kind === 'boolean') control.checked = spec.value === true; }
      controls[spec.name] = function () { if (choices) return control.selectedOptions[0] ? control.selectedOptions[0]._typedValue : ''; if (spec.kind === 'boolean') return control.checked; if (spec.kind === 'number') return control.value === '' ? null : Number(control.value); return control.value; };
    }
    install(Array.isArray(spec.choices) ? spec.choices : null);
    if (spec.depends_on && spec.choices_by) return function () {
      var related = parent.elements.namedItem(spec.depends_on);
      function update() { install(spec.choices_by[controls[spec.depends_on]()] || null); }
      if (related) { related.addEventListener('change', update); update(); }
    };
    return null;
  }
  async function openOperation(id) {
    if (busy) return false;
    var started = generation; lock(true);
    try {
      var fresh = await refresh();
      if (!fresh || !active(started)) return false;
      var item = fresh.operations.find(function (entry) { return entry.id === id && entry.available; });
      if (!item) return false;
      selected = item; clear(formHost); node('h3', item.label, formHost); node('p', item.summary, formHost);
      node('p', 'Source: ' + item.source + '. Checked ' + new Date(fresh.checked_at * 1000).toLocaleString() + '.', formHost);
      nativeLink(item, formHost);
      var form = node('form', undefined, formHost), controls = {}, dependents = [];
      (item.fields || []).forEach(function (spec) { var setup = createField(spec, form, controls); if (setup) dependents.push(setup); });
      dependents.forEach(function (setup) { setup(); });
      var submit = node('button', item.confirmation_required ? 'Review change' : 'Check current status', form); submit.type = 'submit'; submit.className = 'btn btn--primary';
      form.addEventListener('submit', async function (event) {
        event.preventDefault(); if (busy || !form.reportValidity()) return;
        var args = {}; Object.keys(controls).forEach(function (key) { args[key] = controls[key](); });
        var requested = Object.assign(payload(), {operation: item.id, context: fresh.context, revision: item.revision, arguments: args});
        lock(true);
        try {
          var review = await request('/prepare', requested);
          if (!active(started)) return;
          clear(formHost); node('h3', item.label, formHost); node('p', review.summary, formHost);
          node('p', 'Source: ' + review.source + '. Checked ' + new Date(review.checked_at * 1000).toLocaleString() + '.', formHost);
          if (review.changes && review.changes.length) renderData(formHost, review.changes);
          if (review.read_only) { renderData(formHost, review.details); status.textContent = 'Current information checked. Nothing was changed.'; }
          else renderConfirmation(item, review, started);
          nativeLink(item, formHost);
          button('Refresh current controls', formHost, function () { clear(formHost); selected = null; refresh(); });
        } catch (error) { if (active(started)) status.textContent = error.message; }
        finally { lock(false); }
      });
      var focus = form.querySelector('select,input,textarea,button'); if (focus) focus.focus();
      return true;
    } finally { lock(false); }
  }
  function renderConfirmation(item, review, started) {
    var line = node('label', undefined, formHost), consent = node('input', undefined, line); consent.type = 'checkbox';
    line.appendChild(document.createTextNode(' I reviewed these exact changes and want to apply them.'));
    var apply = button('Apply reviewed change', formHost, async function () {
      if (busy || !consent.checked) { status.textContent = 'Review the changes and select the confirmation box first.'; return; }
      if (Date.now() >= review.expires_at * 1000) { status.textContent = 'This review expired. Refresh controls and review again.'; return; }
      lock(true); uncertainty = 'A submitted action needs a status check before another attempt.';
      try {
        var result = await request('/execute', {token: review.token, confirmed: true});
        if (!active(started)) return;
        clear(formHost); node('h3', item.label, formHost); node('p', result.message, formHost);
        uncertainty = result.status === 'confirmed' || result.status === 'replayed' ? '' : 'This outcome still needs confirmation. Check native status; do not submit it again automatically.';
        if (result.details) renderData(formHost, result.details);
        if (result.operation === 'preferences.update' && result.status === 'confirmed' && result.details) {
          var revision = document.querySelector('meta[name="lightwork-preference-revision"]');
          if (revision && result.details.revision) revision.content = result.details.revision;
          Object.entries(result.details.values || {}).forEach(function (entry) {
            if (window.mvApplyDisplayPreference) window.mvApplyDisplayPreference(entry[0], entry[1]);
            var picker = document.querySelector('[data-ui-preference="' + entry[0] + '"]');
            if (picker) picker.value = entry[1];
          });
        }
        nativeLink(item, formHost); status.textContent = uncertainty || 'The server confirmed the result.';
        document.dispatchEvent(new CustomEvent('lightwork:product-operation-complete', {detail: {operation: result.operation, status: result.status}}));
      } catch (error) {
        if (active(started)) { clear(formHost); node('p', error.message, formHost); nativeLink(item, formHost); status.textContent = uncertainty; }
      } finally { lock(false); }
    });
    apply.className = 'btn btn--primary';
    button('Cancel this review', formHost, function () { clear(formHost); selected = null; status.textContent = 'Review cancelled. Nothing was submitted.'; });
  }
  window.LightworkProductOperations = {
    open: function () { generation += 1; refresh(); },
    close: function () { generation += 1; catalog = null; selected = null; clear(formHost); clear(buttons); },
    isBusy: function () { return busy; },
    propose: async function (message) {
      if (busy || !catalog || !catalog.operations.some(function (item) { return item.available; })) return false;
      var started = generation; lock(true);
      try {
        var result = await request('/propose', Object.assign(payload(), {message: message, context: catalog.context}));
        if (!active(started)) return true;
        if (result.context !== catalog.context) { clear(formHost); catalog = null; status.textContent = 'Your context changed. Reopen Copilot.'; return true; }
        if (!result.operation) return false;
        lock(false); return await openOperation(result.operation);
      } catch (error) { if (active(started)) status.textContent = error.message; return true; }
      finally { lock(false); }
    }
  };
  document.addEventListener('lightwork:copilot-selection', function (event) {
    generation += 1; overrideSelection = event.detail && event.detail.selection || null; catalog = null; selected = null; clear(formHost); clear(buttons);
    document.dispatchEvent(new CustomEvent('lightwork:copilot-context-changed'));
    if (!panel.hidden) refresh();
  });
}());
