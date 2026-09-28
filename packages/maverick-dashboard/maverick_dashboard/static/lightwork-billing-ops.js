/* Billing page: what a plan includes, and Stripe billing (status, preview, send). */
(function () {
  'use strict';
  var BASE = '/api/v1/admin/billing-ops';
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  async function failText(failure, action) {
    try {
      if (failure && typeof failure.clone === 'function') {
        var body = await failure.clone().json();
        var plain = window.mvPlainDetail ? window.mvPlainDetail(body && body.detail) : '';
        if (plain) return plain;
      }
    } catch (ignored) { /* not a readable body */ }
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.';
  }
  async function call(path, method, body) {
    var init = {method: method || 'GET', credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}};
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    var response = await window.fetch(path, init);
    if (!response.ok) throw response;
    return response.json();
  }
  function facts(rows) {
    var dl = el('dl');
    rows.forEach(function (row) { dl.appendChild(el('dt', null, row.label)); dl.appendChild(el('dd', null, row.value)); });
    return dl;
  }

  var planForm = $('plan-form');
  if (planForm) planForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    var out = $('plan-out'), msg = $('plan-msg');
    out.textContent = '';
    msg.textContent = 'Reading the plan…';
    try {
      var data = await call(BASE + '/plan?' + new URLSearchParams({workspace: planForm.elements.workspace.value}).toString());
      out.appendChild(facts([
        {label: 'Plan', value: data.plan},
        {label: 'Includes', value: data.features.join(', ') || 'Nothing extra'},
        {label: 'Daily spending limit', value: data.daily_limit},
        {label: 'Tasks at the same time', value: data.tasks_at_once}
      ]));
      msg.textContent = '';
    } catch (failure) { msg.textContent = await failText(failure, 'read the plan'); }
  });

  var statusRun = $('stripe-status-run');
  if (statusRun) statusRun.addEventListener('click', async function () {
    var out = $('stripe-status-out');
    out.textContent = '';
    try {
      var data = await call(BASE + '/stripe');
      out.appendChild(el('p', null, data.summary));
      out.appendChild(facts(data.facts));
    } catch (failure) { out.appendChild(el('p', null, await failText(failure, 'check Stripe billing'))); }
  });

  var form = $('stripe-form');
  if (!form) return;
  var sync = $('stripe-sync');
  function fields() {
    var data = new FormData(form), body = {};
    ['workspace', 'since', 'until', 'markup', 'minimum'].forEach(function (k) {
      var v = String(data.get(k) || '').trim();
      if (v) body[k] = v;
    });
    return body;
  }
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    var out = $('stripe-out'), msg = $('stripe-msg');
    out.textContent = ''; sync.hidden = true;
    msg.textContent = 'Working out the charge…';
    try {
      var data = await call(BASE + '/stripe/preview?' + new URLSearchParams(fields()).toString());
      out.appendChild(el('p', null, data.summary));
      sync.hidden = !data.can_sync;
      msg.textContent = '';
    } catch (failure) { msg.textContent = await failText(failure, 'preview the charge'); }
  });
  sync.addEventListener('click', async function () {
    var body = fields();
    var select = form.elements.workspace;
    var name = select.options[select.selectedIndex] ? select.options[select.selectedIndex].textContent : 'this workspace';
    var ok = window.mvConfirm ? await window.mvConfirm('Send ' + name + ' to Stripe? Stripe sets up its customer and subscription and may start charging the plan fee.', {okText: 'Send to Stripe'}) : false;
    if (!ok) return;
    body.confirm = true;
    if (body.markup) body.markup = Number(body.markup);
    if (body.minimum) body.minimum = Number(body.minimum);
    var msg = $('stripe-msg'), out = $('stripe-out');
    sync.hidden = true;
    msg.textContent = 'Sending to Stripe…';
    try {
      var data = await call(BASE + '/stripe/sync', 'POST', body);
      out.textContent = '';
      out.appendChild(el('p', null, data.summary));
      if (data.technical && data.technical.length) {
        var details = el('details'); details.appendChild(el('summary', null, 'Technical details'));
        data.technical.forEach(function (t) { details.appendChild(el('p', 'muted', t.text)); });
        out.appendChild(details);
      }
      msg.textContent = 'Sent.';
    } catch (failure) { msg.textContent = await failText(failure, 'send to Stripe'); }
  });
})();
