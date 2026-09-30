(function () {
  'use strict';
  var root = document.getElementById('budget-guidance');
  var launch = document.querySelector('[data-budget-launch]');
  if (!root && !launch) return;
  var host = root || launch, generation = 0, controller = null, context = '', current = null;
  var status = root ? document.getElementById('budget-status') : launch.querySelector('[role="status"]');
  function node(tag, text) { var e = document.createElement(tag); if (text !== undefined) e.textContent = text; return e; }
  function money(v) { return v === null || v === undefined ? 'Unavailable or uncapped (see basis)' : '$' + Number(v).toFixed(4); }
  function link(label, href) { var a = node('a', label); a.href = href; return a; }
  function errorMessage(error) { return window.mvBusinessError ? window.mvBusinessError(error).message : (error.message || 'Budget details could not be refreshed.'); }
  async function call(url, options, signal) {
    var response = await fetch(url, Object.assign({credentials: 'same-origin', cache: 'no-store', signal: signal}, options || {}));
    var value = await response.json();
    if (!response.ok) { var error = new Error(value.detail && (value.detail.message || value.detail) || 'Budget request failed.'); error.detail = value.detail; error.status = response.status; throw error; }
    return value;
  }
  function paragraph(parent, text) { parent.appendChild(node('p', text)); }
  function render(value) {
    current = value; context = value.context;
    var out = root ? document.getElementById('budget-preview') : launch.querySelector('[data-budget-summary]');
    out.replaceChildren();
    paragraph(out, 'Effective task ceiling: ' + money(value.effective.max_dollars) + ' USD; time ceiling: ' + (value.effective.max_wall_seconds === null ? 'not configured' : value.effective.max_wall_seconds + ' seconds') + '.');
    paragraph(out, (value.headroom_complete ? 'Current usable headroom: ' + money(value.headroom) : 'Headroom is unavailable because at least one required ledger or identity is unknown.') + (value.binding_ceiling ? ' Binding ceiling: ' + value.binding_ceiling + '.' : ''));
    paragraph(out, value.admission_allowed ? 'Dispatch will recheck authority and allowance before paid work.' : 'A current limit or accounting uncertainty may block paid work. Review the details before starting.');
    var reset = new Date(value.reset_at);
    paragraph(out, 'Daily reset: ' + reset.toLocaleString() + ' (' + Intl.DateTimeFormat().resolvedOptions().timeZone + '); ' + reset.toISOString() + ' UTC. Provider periods are shown separately.');
    if (!root) { out.appendChild(link('Review reservations, schedules and accounting help', '/budget' + (value.goal_id ? '?goal_id=' + value.goal_id : ''))); return; }
    paragraph(out, value.notice); paragraph(out, value.increase_owner);
    ['person', 'workspace'].forEach(function (name) {
      var row = value[name], section = node('section'); section.className = 'panel'; section.appendChild(node('h2', name === 'person' ? 'Your daily allowance' : 'Workspace daily allowance'));
      paragraph(section, row.message || ('Cap ' + money(row.cap) + '; acknowledged ' + money(row.confirmed) + '; reserved ' + money(row.reserved) + '; unknown reservations ' + (row.unknown_amount ? 'amount unavailable' : money(row.unknown)) + '; remaining ' + money(row.remaining) + '.'));
      if (row.basis) paragraph(section, row.basis); out.appendChild(section);
    });
    paragraph(out, value.provider_coverage || 'Provider coverage unavailable.');
    (value.providers || []).forEach(function (row) {
      var section = node('section'); section.className = 'panel'; section.appendChild(node('h2', row.provider));
      paragraph(section, 'Period ' + row.period + '; ' + row.state + '; ceiling ' + money(row.cap) + '; remaining ' + money(row.remaining) + '.');
      if (row.basis) paragraph(section, row.basis);
      if (row.state === 'blocked' || row.state === 'unavailable') {
        var button = node('button', 'Request accounting help'); button.type = 'button'; button.className = 'btn btn--ghost';
        button.addEventListener('click', function () { mutate('/api/v1/budget/cases', {provider: row.provider, period: row.period, goal_id: value.goal_id}, button); }); section.appendChild(button);
      }
      out.appendChild(section);
    });
    if (value.task_receipts && value.task_receipts.length) {
      out.appendChild(node('h2', 'This task’s provider receipts'));
      value.task_receipts.forEach(function (row) { paragraph(out, row.reference + ': ' + row.provider + ', ' + row.period + ', ' + row.state + '; projected ' + money(row.projected) + ', recorded ' + money(row.actual) + ' ' + row.currency + '; basis ' + row.cost_basis + '.'); });
    }
    var forecast = document.getElementById('budget-forecast'); forecast.replaceChildren();
    paragraph(forecast, value.forecast.message || value.forecast.price_basis || value.forecast.state);
    if (value.forecast.state === 'available' && !value.forecast.items.length) paragraph(forecast, 'No owned active schedule is currently due before the daily reset.');
    value.forecast.items.forEach(function (row) { var item = node('p'); item.appendChild(link(row.title, row.href)); item.appendChild(document.createTextNode(' — ' + new Date(row.due_at * 1000).toLocaleString() + ': ' + row.risk + '. ' + row.detail)); forecast.appendChild(item); });
    if (value.warnings_state !== 'available') paragraph(out, 'Budget notices could not be refreshed; the ledger preview remains separate.');
    (value.warnings || []).forEach(function (row) { paragraph(out, row.label); });
  }
  async function renderCases(signal) {
    if (!root) return;
    var out = document.getElementById('budget-cases');
    try {
      var data = await call('/api/v1/budget/cases', null, signal);
      if (signal.aborted || data.context !== context) return;
      out.replaceChildren();
      if (!data.items.length) paragraph(out, 'No accounting help requests in your current scope.');
      data.items.forEach(function (row) {
        var card = node('section'); card.className = 'panel'; card.appendChild(node('h3', row.provider + ' · ' + row.period));
        paragraph(card, row.id + ': ' + row.state + '. Assigned to: ' + (row.assigned_to || 'installation administrator queue'));
        paragraph(card, row.verification); if (row._audit_pending) paragraph(card, 'Audit delivery is pending; this is not a completed audited review.');
        if (data.admin) {
          [['claim','Assign to me'],['request_evidence','Request final receipt evidence'],['verify','Verify original-period accounting']].forEach(function (action) {
            var button = node('button', action[1]); button.type = 'button'; button.className = 'btn btn--ghost';
            button.addEventListener('click', function () { mutate('/api/v1/budget/cases/' + encodeURIComponent(row.id), {expected_revision: row.revision, action: action[0]}, button); }); card.appendChild(button);
          }); card.appendChild(link('Open governed provider reconciliation', '/provider-spending'));
        }
        out.appendChild(card);
      });
    } catch (error) { if (!signal.aborted) { out.replaceChildren(); paragraph(out, errorMessage(error)); } }
  }
  async function mutate(url, body, button) {
    if (!current || !context) return;
    var submitted = context, c = new AbortController(), timer = setTimeout(function () { c.abort(); }, 20000);
    button.disabled = true;
    try {
      await call(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(Object.assign({}, body, {context:submitted}))}, c.signal);
      if (context === submitted) await refresh();
    } catch (error) { status.textContent = c.signal.aborted ? 'The response was interrupted. Refresh the durable request before trying again; it may already be recorded.' : errorMessage(error); }
    finally { clearTimeout(timer); button.disabled = false; }
  }
  async function refresh() {
    var token = ++generation;
    if (controller) controller.abort(); controller = new AbortController(); var own = controller, timer = setTimeout(function () { own.abort(); }, 20000);
    var params = new URLSearchParams(), goal = host.dataset.goal;
    if (goal) params.set('goal_id', goal);
    if (root) new FormData(document.getElementById('budget-preview-form')).forEach(function (v,k) { if (String(v).trim()) params.set(k,v); });
    status.textContent = 'Checking current ceilings and reservations…';
    try {
      var value = await call('/api/v1/budget/preview?' + params.toString(), null, own.signal);
      if (token !== generation || own.signal.aborted) return;
      render(value); status.textContent = 'Observed ' + new Date(value.observed_at * 1000).toLocaleString() + '. No funds reserved and no paid request started.';
      await renderCases(own.signal);
    } catch (error) {
      if (token !== generation) return;
      status.textContent = own.signal.aborted ? 'Budget preview timed out. Refresh before relying on these amounts.' : errorMessage(error);
      if ([401,403,404].indexOf(error.status) >= 0) { current = null; context = ''; (root ? document.getElementById('budget-preview') : launch.querySelector('[data-budget-summary]')).replaceChildren(); if(root) {document.getElementById('budget-cases').replaceChildren();document.getElementById('budget-forecast').replaceChildren();} }
    } finally { clearTimeout(timer); }
  }
  if (root) document.getElementById('budget-preview-form').addEventListener('submit', function (event) {event.preventDefault(); refresh();});
  else launch.querySelector('button').addEventListener('click', refresh);
  window.addEventListener('pagehide', function () { generation++; if(controller)controller.abort(); current=null; context=''; });
  window.addEventListener('pageshow', function (event) {if(event.persisted)refresh();});
  refresh();
}());
