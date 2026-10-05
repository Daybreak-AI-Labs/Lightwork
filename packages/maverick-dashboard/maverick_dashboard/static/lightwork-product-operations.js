/* Registered native controls: fresh source -> reviewed input -> explicit confirmation. */
(function () {
  'use strict';
  var panel = document.getElementById('mv-copilot'), host = document.getElementById('mv-product-operations');
  var status = document.getElementById('mv-product-operation-status'), buttons = document.getElementById('mv-product-operation-buttons');
  var formHost = document.getElementById('mv-product-operation-form');
  var search = document.getElementById('mv-product-operation-search'), noMatches = document.getElementById('mv-product-operation-empty');
  if (!panel || !host || !status || !buttons || !formHost) return;
  var root = '/api/v1/copilot/product-operations', generation = 0, busy = false, catalog = null, selected = null;
  var uncertainty = '', overrideSelection = null, pendingRequests = new Set();
  function cancelRequests() { pendingRequests.forEach(function (controller) { controller.abort(); }); pendingRequests.clear(); }
  function node(tag, text, parent) { var element = document.createElement(tag); if (text !== undefined) element.textContent = String(text); if (parent) parent.appendChild(element); return element; }
  function clear(element) { element.replaceChildren(); }
  function filterControls() {
    var query = search ? search.value.trim().toLocaleLowerCase() : '', found = 0;
    buttons.querySelectorAll('button').forEach(function (control) {
      control.hidden = !control.textContent.toLocaleLowerCase().includes(query);
      if (!control.hidden) found += 1;
    });
    if (noMatches) noMatches.hidden = !query || found > 0;
  }
  if (search) search.addEventListener('input', filterControls);
  function button(text, parent, handler) { var control = node('button', text, parent); control.type = 'button'; control.className = 'btn btn--ghost'; control.addEventListener('click', handler); return control; }
  function context() {
    if (overrideSelection) return overrideSelection;
    var approval = window.location.hash.match(/^#approval-([1-9]\d*)$/);
    if (approval && ['/approvals', '/oversight'].includes(window.location.pathname) && Number.isSafeInteger(Number(approval[1]))) return {kind: 'approval', id: Number(approval[1])};
    var goal = window.location.pathname.match(/^\/(?:chat\/goal|tasks)\/(\d+)(?:\/|$)/);
    if (goal) return {kind: 'goal', id: Number(goal[1]), goal_id: Number(goal[1])};
    var dsar = window.location.pathname.match(/^\/privacy\/dsar\/(DSAR-[0-9a-f]{10})$/);
    if (dsar) return {kind: 'dsar', id: dsar[1]};
    var run = window.location.pathname.match(/^\/flows\/([^/]+)\/runs\/([^/]+)$/);
    if (run) {
      try { return {kind: 'flow_run', id: decodeURIComponent(run[2]), flow_id: decodeURIComponent(run[1])}; }
      catch (_) { return {}; }
    }
    var flow = window.location.pathname.match(/^\/flows\/designer\/([^/]+)$/) ||
      window.location.pathname.match(/^\/flows\/([^/]+)\/teach$/);
    if (flow) {
      try { return {kind: 'flow', id: decodeURIComponent(flow[1])}; } catch (_) { return {}; }
    }
    var query = new URLSearchParams(window.location.search);
    var selectedGoal = query.get('goal_id');
    if (window.location.pathname === '/budget' && selectedGoal && /^[1-9]\d*$/.test(selectedGoal)) return {goal_id: Number(selectedGoal)};
    var thread = query.get('thread');
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
    var controller = new AbortController(); pendingRequests.add(controller);
    var timeout = setTimeout(function () { controller.abort(); }, route === '/propose' ? 45000 : 30000);
    try {
      var response = await fetch(root + route, {method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify(body)});
      var value = null; try { value = await response.json(); } catch (_) { /* No raw server responses. */ }
      if (!response.ok) { var error = new Error(errorMessage(value, 'The current control is unavailable. Open its native page to check status.')); error.status = response.status; throw error; }
      if (!value || typeof value !== 'object') throw new Error('The response was unreadable. Check native status before another attempt.');
      return value;
    } catch (error) {
      if (controller.signal.aborted) throw new Error(route === '/execute'
        ? 'The wait ended before the result was confirmed. Check native status before another attempt.'
        : 'This check timed out or was cancelled. Reopen the control to read current status.');
      if (error && error.status) throw error;
      throw new Error(route === '/execute'
        ? 'The result could not be confirmed. Check native status before another attempt.'
        : 'This check could not be completed. Reopen the control to read current status.');
    } finally { clearTimeout(timeout); pendingRequests.delete(controller); }
  }
  function renderData(parent, value, options) {
    var complete = true, fullReview = !!(options && options.complete), remaining = fullReview ? 8192 : 160;
    var maximumEntries = fullReview ? 2048 : 35, maximumDepth = fullReview ? 12 : 6;
    function omitted(target) { complete = false; node('p', 'The complete details are available in the full controls.', target); }
    function append(target, item, depth) {
      if (--remaining < 0 || depth > maximumDepth) { omitted(target); return; }
      if (item === null || item === undefined) { node('span', 'Not specified', target); return; }
      if (typeof item !== 'object') {
        var text = node('span', typeof item === 'boolean' ? (item ? 'Yes' : 'No') : item, target);
        text.style.whiteSpace = 'pre-wrap'; text.style.overflowWrap = 'anywhere'; return;
      }
      if (typeof item.href === 'string' && typeof item.label === 'string' && safeLocalHref(item.href)) {
        var local = node('a', item.label, target); local.href = item.href; local.className = 'btn btn--ghost'; return;
      }
      if (Array.isArray(item)) {
        var ordered = node('ol', undefined, target);
        item.slice(0, maximumEntries).forEach(function (entry) { append(node('li', undefined, ordered), entry, depth + 1); });
        if (item.length > maximumEntries) omitted(target);
        return;
      }
      var list = node('dl', undefined, target);
      var entries = Object.entries(item);
      if (entries.length > maximumEntries) omitted(target);
      entries.slice(0, maximumEntries).forEach(function (entry) {
        if (entry[0] === 'learning_saved_point') {
          var saved = entry[1], savedStarted = generation;
          if (!saved || Array.isArray(saved) || Object.keys(saved).sort().join(',') !== 'name,revision' ||
              !/^[A-Za-z0-9._-]{1,80}$/.test(saved.name || '') || !/^[a-f0-9]{64}$/.test(saved.revision || '')) return;
          var savedDestination = node('dd', undefined, list);
          if (window.LightworkLearningSavedPoints) button('Open this saved point', savedDestination, async function (event) {
            if (!active(savedStarted) || busy) return;
            event.currentTarget.disabled = true;
            try {
              var opened = await window.LightworkLearningSavedPoints.open(saved.name, saved.revision);
              if (!active(savedStarted)) return;
              if (opened) {
                var close = document.getElementById('mv-copilot-close'); if (close) close.click();
                var pointSelect = document.getElementById('lrn-snapshot'); if (pointSelect) pointSelect.focus({preventScroll: true});
              } else status.textContent = 'This saved point changed or could not be opened. Review current saved points in Learning.';
            } catch (_) { if (active(savedStarted)) status.textContent = 'This saved point could not be opened. Check current saved points in Learning.'; }
            finally { if (active(savedStarted)) event.currentTarget.disabled = false; }
          });
          else { var savedLink = node('a', 'Open saved learning points', savedDestination); savedLink.href = '/learning#learning-review'; }
          return;
        }
        if (entry[0] === 'knowledge_request_page') {
          var page = entry[1], started = generation;
          if (!page || Array.isArray(page) || Object.keys(page).sort().join(',') !== 'cursor,view' ||
              !['active', 'archive', 'ready'].includes(page.view) || typeof page.cursor !== 'string' ||
              page.cursor.length > 2048 || (page.cursor && !/^[A-Za-z0-9_-]+$/.test(page.cursor))) return;
          node('dt', 'Saved requests', list);
          var destination = node('dd', undefined, list);
          if (window.LightworkKnowledgeRequests && typeof window.LightworkKnowledgeRequests.openBatch === 'function') {
            button('Open these requests', destination, async function (event) {
              if (!active(started) || busy) return;
              var control = event.currentTarget; control.disabled = true;
              try {
                var opened = await window.LightworkKnowledgeRequests.openBatch(page.view, page.cursor);
                if (active(started)) status.textContent = opened ? 'These requests are open in Knowledge.' : 'This batch could not be opened. Check your current access in Knowledge.';
                if (opened && active(started)) {
                  var close = document.getElementById('mv-copilot-close');
                  if (close) close.click();
                  document.dispatchEvent(new CustomEvent('lightwork:copilot-selection', {
                    detail: {selection: {kind: 'knowledge_request_batch', view: page.view, cursor: page.cursor}}
                  }));
                  var heading = document.getElementById('ka-requests-heading');
                  if (heading) heading.focus({preventScroll: true});
                }
              } catch (_) {
                if (active(started)) status.textContent = 'This batch could not be opened. Check your current access in Knowledge.';
              } finally { if (active(started)) control.disabled = false; }
            });
          } else {
            var knowledge = node('a', 'Open Knowledge requests', destination);
            knowledge.href = '/knowledge-access'; knowledge.className = 'btn btn--ghost';
          }
          return;
        }
        var label = entry[0].replace(/_/g, ' ');
        node('dt', label.charAt(0).toUpperCase() + label.slice(1), list);
        append(node('dd', undefined, list), entry[1], depth + 1);
      });
    }
    append(parent, value, 0);
    return complete;
  }
  function safeLocalHref(href) {
    if (typeof href !== 'string' || !href.startsWith('/') || href.startsWith('//') || /[\\\u0000-\u0020]/.test(href)) return false;
    try { return new URL(href, window.location.origin).origin === window.location.origin; } catch (_) { return false; }
  }
  function nativeLink(item, parent) {
    if (!safeLocalHref(item.direct_ui)) return;
    var link = node('a', 'Open full controls', parent); link.className = 'btn btn--ghost'; link.href = item.direct_ui;
  }
  async function refresh(options) {
    var keepResult = options && options.keepResult === true;
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
      filterControls();
      status.textContent = uncertainty || 'Current controls for this page. Changes require a separate review and confirmation.';
      return fresh;
    } catch (error) {
      if (active(started)) {
        catalog = null; clear(buttons); selected = null; host.hidden = false;
        if (!keepResult) clear(formHost);
        if (noMatches) noMatches.hidden = true;
        status.textContent = keepResult ? 'Your saved result is below. Current controls could not be refreshed. ' + error.message : error.message;
      }
      return null;
    }
  }
  function createField(spec, parent, controls) {
    var row = node('div', undefined, parent), id = 'mv-operation-' + spec.name;
    var label = node('label', spec.label || spec.name, row); label.htmlFor = id;
    var control;
    function install(choices) {
      if (control) control.remove();
      if (choices && spec.kind === 'multiselect' && spec.presentation === 'checkboxes') {
        control = node('fieldset', undefined, row); control.id = id; control.name = spec.name; control.className = 'input';
        label.id = id + '-label'; control.setAttribute('aria-labelledby', label.id);
        control.style.minInlineSize = '0'; control.style.maxHeight = '18rem'; control.style.overflowY = 'auto';
        var note = node('p', undefined, control), boxes = [], limit = Number(spec.max_selections) || choices.length;
        note.className = 'muted'; note.setAttribute('role', 'status'); note.setAttribute('aria-live', 'polite');
        function chosen() { return boxes.filter(function (box) { return box.checked; }); }
        function updateChoices() {
          var count = chosen().length;
          boxes.forEach(function (box) { box.disabled = busy || !box.checked && count >= limit; });
          if (boxes[0]) boxes[0].setCustomValidity(spec.required && !count ? 'Choose at least one saved question.' :
            count > limit ? 'Choose no more than ' + limit + ' saved questions.' : '');
          note.textContent = count + ' selected. Choose up to ' + limit + '.' +
            (count >= limit ? ' Uncheck a question before choosing another.' : '');
        }
        choices.forEach(function (choice) {
          var option = node('label', undefined, control), box = node('input', undefined, option);
          option.style.display = 'flex'; option.style.alignItems = 'flex-start'; option.style.gap = 'var(--space-2)';
          option.style.paddingBlock = 'var(--space-2)';
          box.type = 'checkbox'; box.name = spec.name + '_choice'; box._typedValue = choice.value;
          box.style.flexShrink = '0'; box.checked = Array.isArray(spec.value) && spec.value.indexOf(choice.value) >= 0;
          var text = node('span', choice.label, option); text.style.minWidth = '0'; text.style.overflowWrap = 'anywhere';
          box.addEventListener('change', updateChoices); boxes.push(box);
        });
        controls[spec.name] = function () { return chosen().map(function (box) { return box._typedValue; }); };
        updateChoices(); return;
      }
      control = node(choices ? 'select' : spec.kind === 'textarea' ? 'textarea' : 'input', undefined, row);
      control.id = id; control.name = spec.name; control.className = 'input'; control.required = spec.required === true;
      if (!choices && spec.kind === 'boolean') control.classList.remove('input');
      if (choices && spec.kind === 'multiselect') control.multiple = true;
      if (choices) choices.forEach(function (choice, index) { var option = node('option', choice.label, control); option.value = String(index); option._typedValue = choice.value; if (spec.kind === 'multiselect' ? Array.isArray(spec.value) && spec.value.indexOf(choice.value) >= 0 : choice.value === spec.value) option.selected = true; });
      else { if (control.tagName !== 'TEXTAREA') control.type = spec.kind === 'boolean' ? 'checkbox' : spec.kind === 'number' ? 'number' : spec.kind === 'datetime-local' ? 'datetime-local' : 'text'; if (spec.kind === 'number') { control.step = 'any'; control.min = '0'; } control.maxLength = spec.max_length || 2000; control.value = spec.value === null || spec.value === undefined ? '' : String(spec.value); if (spec.kind === 'boolean') control.checked = spec.value === true; }
      controls[spec.name] = function () { if (choices && spec.kind === 'multiselect') return Array.from(control.selectedOptions, function (option) { return option._typedValue; }); if (choices) return control.selectedOptions[0] ? control.selectedOptions[0]._typedValue : ''; if (spec.kind === 'boolean') return control.checked; if (spec.kind === 'number') return control.value === '' ? null : Number(control.value); if (spec.kind === 'datetime-local') { var date = new Date(control.value); return Number.isNaN(date.getTime()) ? control.value : date.toISOString(); } return control.value; };
    }
    install(Array.isArray(spec.choices) ? spec.choices : null);
    if (spec.depends_on && spec.choices_by) return function () {
      var related = parent.elements.namedItem(spec.depends_on);
      function update() { install(spec.choices_by[controls[spec.depends_on]()] || null); }
      if (related) { related.addEventListener('change', update); update(); }
    };
    return null;
  }
  async function openWorkflowRun(item, fresh, started) {
    var selection = context();
    var review = await request('/prepare', Object.assign(payload(), {
      operation: item.id, context: fresh.context, revision: item.revision, arguments: {}
    }));
    if (!active(started)) return;
    var details = review.details || {};
    if (review.read_only !== true || review.operation !== item.id ||
        details.flow_id !== selection.id || !/^[0-9a-f]{64}$/.test(details.release_id || '') ||
        !window.LightworkFlowRun || typeof window.LightworkFlowRun.open !== 'function')
      throw new Error('The published workflow could not be confirmed. Reopen it before running.');
    clear(formHost); node('h3', item.label, formHost); node('p', review.summary, formHost);
    node('p', 'The workflow form will check this published version again. Nothing starts until you enter its values and confirm.', formHost);
    var launch = button('Enter workflow inputs', formHost, async function () {
      if (busy || !active(started)) return;
      if (JSON.stringify(context()) !== JSON.stringify(selection)) {
        status.textContent = 'The selected workflow changed. Refresh the current controls.'; return;
      }
      lock(true);
      try {
        var outcome = await window.LightworkFlowRun.open(selection.id, details.release_id);
        if (!active(started)) return;
        clear(formHost);
        if (outcome && outcome.status === 'started' && typeof outcome.run_id === 'string') {
          node('p', 'The workflow run was accepted. Check its progress and any required approvals.', formHost);
          var link = node('a', 'Open run progress', formHost);
          link.href = '/flows/' + encodeURIComponent(selection.id) + '/runs/' + encodeURIComponent(outcome.run_id);
          link.className = 'btn btn--ghost';
          status.textContent = 'Run accepted. This is not a completed business outcome.';
        } else {
          node('p', outcome && outcome.status === 'cancelled' ? 'Run cancelled. Nothing was submitted.' :
            'A new run was not confirmed. Check the workflow status before trying again.', formHost);
          status.textContent = outcome && outcome.status === 'cancelled' ? 'Run cancelled.' :
            'No new run was confirmed.';
        }
        button('Refresh current controls', formHost, function () { clear(formHost); selected = null; refresh(); });
      } catch (_) {
        if (active(started)) status.textContent = 'The run result could not be confirmed. Check workflow progress before trying again.';
      } finally { lock(false); }
    });
    launch.className = 'btn btn--primary'; launch.focus();
    button('Cancel', formHost, function () { clear(formHost); selected = null; status.textContent = 'Run review cancelled.'; });
  }
  async function openTeachingForm(item, fresh, started) {
    var selection = context(), teaching = window.LightworkFlowTeach;
    var review = await request('/prepare', Object.assign(payload(), {
      operation: item.id, context: fresh.context, revision: item.revision, arguments: {}
    }));
    if (!active(started)) return;
    var details = review.details || {}, modes = {
      'learning.workflow_refine': 'recipe_refine', 'learning.rule_teach': 'rule_teach', 'learning.rule_refine': 'rule_refine'
    };
    if (review.read_only !== true || review.operation !== item.id || details.flow_id !== selection.id ||
        details.mode !== modes[item.id] || !teaching || typeof teaching.perform !== 'function')
      throw new Error('The teaching form could not be confirmed. Reopen Teach before continuing.');
    clear(formHost); node('h3', item.label, formHost); node('p', review.summary, formHost);
    button('Review examples on Teach', formHost, function () { teaching.showTeachingInputs(details.mode); });
    var use = button('Use the reviewed teaching examples', formHost, async function () {
      if (busy || !active(started)) return;
      if (JSON.stringify(context()) !== JSON.stringify(selection)) {
        status.textContent = 'The selected flow or step changed. Refresh Copilot.'; return;
      }
      lock(true);
      try {
        var result = await teaching.perform(details);
        if (!active(started)) return;
        status.textContent = result && result.status === 'learned'
          ? 'The learning result is ready on Teach. Review and save it when you are ready; it has not been published or run.'
          : result && result.status === 'cancelled' ? 'Learning cancelled. Nothing was submitted.'
          : result && result.message || 'The learning result was not confirmed. Check the message on Teach before trying again.';
        if (result && result.status === 'learned') {
          clear(formHost); node('p', status.textContent, formHost);
          button('Review the learning result', formHost, function () {
            if (details.mode === 'recipe_refine') teaching.showRecipeReview(); else teaching.showRuleReview();
          });
        }
      } finally { lock(false); }
    });
    use.className = 'btn btn--primary'; use.focus();
    button('Cancel', formHost, function () { clear(formHost); selected = null; status.textContent = 'Teaching review cancelled.'; });
  }
  async function openConsentJobForm(item, fresh, started) {
    var selection = context(), native = window.LightworkConsentBulkJobs;
    var review = await request('/prepare', Object.assign(payload(), {
      operation: item.id, context: fresh.context, revision: item.revision, arguments: {}
    }));
    if (!active(started)) return;
    var details = review.details || {};
    if (review.read_only !== true || review.operation !== item.id || details.form !== 'consent_bulk_job' ||
        typeof details.context !== 'string' || !/^[a-f0-9]{64}$/.test(details.context) ||
        !native || typeof native.openCreate !== 'function')
      throw new Error('The request form is unavailable. Open Consent requests to continue.');
    clear(formHost); node('h3', item.label, formHost); node('p', review.summary, formHost);
    var launch = button('Choose and review the request', formHost, async function () {
      if (busy || !active(started)) return;
      if (JSON.stringify(context()) !== JSON.stringify(selection)) {
        status.textContent = 'Your selection changed. Refresh current controls before preparing a request.'; return;
      }
      lock(true);
      try {
        var result = await native.openCreate({host: formHost, context: details.context,
          active: function () { return active(started) && JSON.stringify(context()) === JSON.stringify(selection); }});
        if (!active(started)) return;
        clear(formHost);
        uncertainty = result && result.status === 'submitted' || result && result.status === 'cancelled' ? '' :
          'This request still needs confirmation. Read the original request in Consent requests before another submission.';
        node('p', result && result.status === 'submitted' ? 'The request was accepted. Its processing result is recorded separately.' :
          result && result.status === 'cancelled' ? 'The form was closed. No new request was submitted.' : uncertainty, formHost);
        if (result && result.status === 'submitted' && result.job) renderData(formHost, result.job);
        nativeLink(item, formHost); status.textContent = uncertainty || 'The request form is finished.';
        if (result && result.status === 'submitted') await refresh({keepResult: true});
      } catch (_) {
        if (active(started)) {
          uncertainty = 'The request result could not be confirmed. Open Consent requests and read the original submission before another attempt.';
          status.textContent = uncertainty; nativeLink(item, formHost);
        }
      } finally { lock(false); }
    });
    launch.className = 'btn btn--primary'; launch.focus(); nativeLink(item, formHost);
  }
  async function openOperation(id) {
    if (busy) return false;
    var started = generation; lock(true);
    try {
      var fresh = await refresh();
      if (!fresh || !active(started)) return false;
      var item = fresh.operations.find(function (entry) { return entry.id === id && entry.available; });
      if (!item) return false;
      if (item.id === 'learning.workflow_run') {
        await openWorkflowRun(item, fresh, started);
        return true;
      }
      if (item.id === 'consent.jobs.create') {
        await openConsentJobForm(item, fresh, started);
        return true;
      }
      if (['learning.workflow_refine', 'learning.rule_teach', 'learning.rule_refine'].includes(item.id)) {
        await openTeachingForm(item, fresh, started);
        return true;
      }
      selected = item; clear(formHost); node('h3', item.label, formHost); node('p', item.summary, formHost);
      node('p', 'Source: ' + item.source + '. Checked ' + (window.mvTimeAbsolute ? window.mvTimeAbsolute(fresh.checked_at) : new Date(fresh.checked_at * 1000).toLocaleString()) + '.', formHost);
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
          node('p', 'Source: ' + review.source + '. Checked ' + (window.mvTimeAbsolute ? window.mvTimeAbsolute(review.checked_at) : new Date(review.checked_at * 1000).toLocaleString()) + '.', formHost);
          var completeReview = true;
          if (review.changes && review.changes.length) {
            completeReview = item.id === 'teams.department.update'
              ? !!(window.LWDepartmentTeamUpdates && window.LWDepartmentTeamUpdates.renderReview &&
                   window.LWDepartmentTeamUpdates.renderReview(formHost, review.changes) === true)
              : renderData(formHost, review.changes, {complete: review.read_only !== true});
          }
          if (review.read_only) { renderData(formHost, review.details); status.textContent = 'Current information checked. Nothing was changed.'; }
          else if (completeReview) renderConfirmation(item, review, started);
          else {
            node('p', 'This complete review could not be displayed here. Open the full controls to review and confirm it; nothing was submitted.', formHost);
            status.textContent = 'Open the full controls for the complete review. Nothing was submitted.';
          }
          nativeLink(item, formHost);
          button('Refresh current controls', formHost, function () { clear(formHost); selected = null; refresh(); });
        } catch (error) { if (active(started)) status.textContent = error.message; }
        finally { lock(false); }
      });
      var focus = form.querySelector('select,input,textarea,button'); if (focus) focus.focus();
      return true;
    } catch (error) {
      if (active(started)) status.textContent = error.message;
      return false;
    } finally { lock(false); }
  }
  function renderConfirmation(item, review, started) {
    function originalOutcomeButton() {
      if (review.recovery_available !== true || Date.now() >= review.expires_at * 1000) return;
      button('Check original outcome', formHost, async function () {
        if (busy || !active(started)) return;
        lock(true);
        try {
          var original = await request('/recover', {token: review.token});
          if (!active(started)) return;
          if (original.operation !== item.id || !['confirmed', 'replayed', 'queued', 'unconfirmed'].includes(original.status))
            throw new Error('The original result could not be verified. Open the full controls.');
          clear(formHost); node('h3', item.label, formHost); node('p', original.message, formHost);
          if (original.details) renderData(formHost, original.details);
          uncertainty = ['confirmed', 'replayed'].includes(original.status) ? '' :
            'The original outcome is still unconfirmed. No action was repeated.';
          status.textContent = uncertainty || 'The original saved outcome was checked. No action was repeated.';
          nativeLink(item, formHost);
          if (uncertainty) originalOutcomeButton();
          else button('Refresh current controls', formHost, function () { refresh({keepResult: true}); });
        } catch (error) { if (active(started)) status.textContent = error.message; }
        finally { lock(false); }
      });
    }
    var line = node('label', undefined, formHost), consent = node('input', undefined, line); consent.type = 'checkbox';
    line.appendChild(document.createTextNode(' I reviewed these exact changes and want to apply them.'));
    var apply = button('Apply reviewed change', formHost, async function () {
      if (busy || !consent.checked) { status.textContent = 'Review the changes and select the confirmation box first.'; return; }
      if (Date.now() >= review.expires_at * 1000) { status.textContent = 'This review expired. Refresh controls and review again.'; return; }
      lock(true); uncertainty = 'A submitted action needs a status check before another attempt.';
      var submitted = false;
      try {
        if (item.id === 'schedules.repair') {
          var recovery = window.LightworkScheduleRecovery;
          if (!recovery || typeof recovery.rememberReview !== 'function') {
            uncertainty = 'Nothing was submitted. Open this schedule in Automations to keep its recovery reference.';
            throw new Error(uncertainty);
          }
          try { await recovery.rememberReview(review.token); }
          catch (error) { uncertainty = 'Nothing was submitted. The recovery reference could not be saved.'; throw error; }
          if (!active(started)) return;
        }
        submitted = true;
        var result = await request('/execute', {token: review.token, confirmed: true});
        if (!active(started)) return;
        clear(formHost); node('h3', item.label, formHost); node('p', result.message, formHost);
        uncertainty = result.status === 'confirmed' || result.status === 'replayed' ? '' : 'This outcome still needs confirmation. Check native status; do not submit it again automatically.';
        if (result.operation === 'learning.workflow_teach') {
          var teaching = window.LightworkFlowTeach;
          var delivered = result.status === 'confirmed' && teaching && typeof teaching.acceptRecipe === 'function' &&
            await teaching.acceptRecipe(result.details && result.details.flow_id, result.details && result.details.recipe);
          if (delivered) {
            button('Review the learned procedure', formHost, function () { teaching.showRecipeReview(); });
          } else {
            uncertainty = 'The procedure was learned; your current draft is kept. Finish or cancel that draft, then open this learned result.';
            if (teaching && typeof teaching.acceptRecipe === 'function') button('Open this learned result', formHost, async function () {
              if (await teaching.acceptRecipe(result.details.flow_id, result.details.recipe)) {
                uncertainty = ''; status.textContent = 'The learned procedure is ready on Teach.';
                teaching.showRecipeReview();
              }
            });
          }
        } else if (result.details) renderData(formHost, result.details);
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
        if (uncertainty) originalOutcomeButton();
        document.dispatchEvent(new CustomEvent('lightwork:product-operation-complete', {detail: {operation: result.operation, status: result.status}}));
        if (result.status === 'confirmed' || result.status === 'replayed') {
          button('Refresh current controls', formHost, function () { refresh({keepResult: true}); });
          await refresh({keepResult: true});
        }
      } catch (error) {
        if (active(started)) {
          clear(formHost); node('p', error.message, formHost); nativeLink(item, formHost); status.textContent = uncertainty;
          if (submitted) originalOutcomeButton();
        }
      } finally { lock(false); }
    });
    apply.className = 'btn btn--primary';
    button('Cancel this review', formHost, function () { clear(formHost); selected = null; status.textContent = 'Review cancelled. Nothing was submitted.'; });
  }
  window.LightworkProductOperations = {
    open: function () { generation += 1; refresh(); },
    close: function () { generation += 1; cancelRequests(); catalog = null; selected = null; clear(formHost); clear(buttons); },
    isBusy: function () { return busy; },
    propose: async function (message) {
      if (busy || !catalog || !catalog.operations.some(function (item) { return item.available; })) return false;
      var started = generation; lock(true);
      try {
        var result = await request('/propose', Object.assign(payload(), {message: message, context: catalog.context}));
        if (!active(started)) return true;
        if (result.context !== catalog.context) {
          clear(formHost); catalog = null; status.textContent = 'Your context changed. Reopen Copilot.';
          document.dispatchEvent(new CustomEvent('lightwork:copilot-context-changed'));
          return true;
        }
        if (!result.operation) return false;
        lock(false); return await openOperation(result.operation);
      } catch (error) { if (active(started)) status.textContent = error.message; return 'unavailable'; }
      finally { lock(false); }
    }
  };
  window.addEventListener('pagehide', function () { generation += 1; cancelRequests(); });
  document.addEventListener('lightwork:copilot-selection', function (event) {
    generation += 1; cancelRequests(); overrideSelection = event.detail && event.detail.selection || null; catalog = null; selected = null; clear(formHost); clear(buttons);
    document.dispatchEvent(new CustomEvent('lightwork:copilot-context-changed'));
    if (!panel.hidden) refresh();
  });
  if (['/approvals', '/oversight'].includes(window.location.pathname)) window.addEventListener('hashchange', function () {
    document.dispatchEvent(new CustomEvent('lightwork:copilot-selection', {detail: {selection: null}}));
  });
}());
