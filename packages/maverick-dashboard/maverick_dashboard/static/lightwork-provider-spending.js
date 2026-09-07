/* Provider accounting: explicit review, no provider dispatch or automatic retry. */
(function () {
  'use strict';
  const root = document.getElementById('provider-spending');
  if (!root) return;
  const el = id => document.getElementById(id);
  const base = '/api/v1/recovery/provider-spending';
  let state = null, review = null, imported = null, holds = [], totals = [], busy = false, offset = 0;
  const message = text => { el('spend-status').textContent = text; };
  const dollars = value => new Intl.NumberFormat(undefined, {style: 'currency', currency: 'USD', maximumFractionDigits: 6}).format(value);
  function retainedReview(value) {
    if (!state) return null;
    try {
      const key = 'lightwork-spending-review:' + state.context;
      if (value === null) sessionStorage.removeItem(key);
      else if (value !== undefined) sessionStorage.setItem(key, value);
      return sessionStorage.getItem(key);
    } catch (_) { return null; }
  }
  function clear() {
    state = review = imported = null;
    holds = []; totals = [];
    ['spend-balances', 'spend-hold', 'spend-totals', 'spend-review-detail', 'spend-page', 'spend-export-date'].forEach(id => el(id).replaceChildren());
    ['spend-evidence', 'spend-actual', 'spend-repair-evidence', 'spend-import', 'spend-review-id'].forEach(id => { el(id).value = ''; });
    el('spend-confirm').checked = false;
    el('spend-receipts-complete').checked = false;
    el('spend-review').hidden = el('spend-repair-form').hidden = true;
  }
  function controls() {
    root.querySelectorAll('button,input,select,textarea').forEach(node => { node.disabled = busy || !state; });
    el('spend-refresh').disabled = busy;
    el('spend-export').disabled = busy || !state || !state.available || state.shared;
    ['spend-import', 'spend-inspect'].forEach(id => { el(id).disabled = busy || !state || !state.isolated || state.shared; });
    el('spend-hold-form').querySelectorAll('input,select,textarea,button').forEach(node => { node.disabled = busy || !holds.length; });
    el('spend-apply').disabled = busy || !state || !review || !el('spend-confirm').checked;
    el('spend-prev').disabled = busy || !state || !state.offset;
    el('spend-next').disabled = busy || !state || !state.providers.some(row => row.holds_next_offset !== null && row.holds_next_offset !== undefined);
  }
  async function request(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (state) {
      headers['X-Lightwork-Spending-Context'] = state.context;
      headers['X-Lightwork-Expected-Tenant'] = state.expected_tenant;
    }
    const response = await fetch(base + path, {...options, headers, credentials: 'same-origin'});
    if ([401, 403, 409].includes(response.status)) {
      retainedReview(null); clear();
    }
    if (!response.ok) {
      let detail = 'This step could not be completed. Refresh accounting before retrying.';
      try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail; } catch (_) { /* HTML errors remain generic. */ }
      throw new Error(detail);
    }
    return response;
  }
  async function json(path, body) {
    return (await request(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})).json();
  }
  async function run(action) {
    if (busy) return;
    busy = true; controls();
    try { await action(); } catch (error) { message(error.message || 'This step could not be completed.'); }
    finally { busy = false; controls(); }
  }
  function paragraph(parent, text) { const p = document.createElement('p'); p.textContent = text; parent.append(p); }
  async function refresh() {
    const period = el('spend-period').value.trim();
    const provider = el('spend-provider').value.trim();
    if (period && !el('spend-period').checkValidity()) throw new Error('Enter a UTC day (YYYY-MM-DD) or month (YYYY-MM).');
    if (provider && !el('spend-provider').checkValidity()) throw new Error('Enter a valid lowercase provider name.');
    clear();
    const params = new URLSearchParams({offset: String(offset), limit: '100'});
    if (period) params.set('period', period);
    if (provider) params.set('provider', provider);
    const response = await request('?' + params.toString());
    state = await response.json();
    message(state.available ? 'Provider accounting is current. No calls were retried.' : state.message);
    if (!state.providers.length && state.available) paragraph(el('spend-balances'), 'No provider spending caps are configured.');
    state.providers.forEach(row => {
      paragraph(el('spend-balances'), `${row.provider} · ${state.period}: ${dollars(row.spent)} confirmed, ${dollars(row.reserved)} reserved. ` + (row.cap === null ? 'No current cap is configured for this provider.' : `${dollars(row.remaining)} available under today’s ${dollars(row.cap)} configured cap.`));
      paragraph(el('spend-page'), `${row.provider}: ${row.holds.length} shown from position ${state.offset + 1}; ${row.holds_total} unresolved reservations in this period.`);
      row.holds.forEach(hold => {
        const choice = {...hold, provider: row.provider, period: state.period};
        const option = document.createElement('option'); option.value = String(holds.length);
        option.textContent = `${row.provider} · ${hold.id} · ${dollars(hold.projected)} · ${hold.state === 'unknown' ? 'final cost unknown' : 'may still be running'}`;
        holds.push(choice); el('spend-hold').append(option);
      });
    });
    const retained = retainedReview();
    if (retained) {
      const previous = await (await request('/review/' + encodeURIComponent(retained))).json();
      if (previous.state === 'completed') retainedReview(null);
      else showReview(previous);
    }
  }
  function showReview(value) {
    review = value; el('spend-confirm').checked = false;
    retainedReview(value.id);
    el('spend-review-detail').replaceChildren();
    paragraph(el('spend-review-detail'), `Review ${value.id}. Keep this reference until completion.`);
    if (value.kind === 'hold') paragraph(el('spend-review-detail'), `${value.provider} · ${value.period} · ${value.receipt}: settle this reservation at ${dollars(value.actual)}.`);
    else value.totals.forEach(row => paragraph(el('spend-review-detail'), `${row.provider} · ${row.period}: ${dollars(row.confirmed)} confirmed spending. Unresolved reservations remain charged separately.`));
    paragraph(el('spend-review-detail'), value.evidence);
    if (value.kind === 'repair') paragraph(el('spend-review-detail'), `Final receipts confirmed for every call since ${new Date(value.exported_at * 1000).toISOString()}, including later providers and periods.`);
    el('spend-review').hidden = false;
    message(value.state === 'applying' ? 'This review was interrupted. Confirm again to finish its exact retained correction; no provider call will be retried.' : 'Review the exact costs, then explicitly confirm. Accounting has not changed.');
  }
  async function download(path, name, method = 'POST') {
    const response = await request(path, {method});
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message(method === 'POST' ? 'Verified accounting copy downloaded. Keep it separately from its recovery key.' : 'Accounting diagnostics downloaded. Failed accounting bytes were not included.');
  }
  el('spend-refresh').addEventListener('click', () => { offset = 0; run(refresh); });
  el('spend-load-period').addEventListener('click', () => { offset = 0; run(refresh); });
  el('spend-prev').addEventListener('click', () => { offset = Math.max(0, offset - 100); run(refresh); });
  el('spend-next').addEventListener('click', () => { offset += 100; run(refresh); });
  el('spend-export').addEventListener('click', () => run(() => download('/export', 'Lightwork-provider-accounting.json')));
  el('spend-diagnostics').addEventListener('click', () => run(() => download('/diagnostics', 'Lightwork-spending-diagnostics.json', 'GET')));
  el('spend-confirm').addEventListener('change', controls);
  el('spend-open-review').addEventListener('click', () => run(async () => {
    const identifier = el('spend-review-id').value.trim();
    if (!/^PAR-[A-Za-z0-9_-]+$/.test(identifier)) throw new Error('Enter the retained accounting review reference.');
    const selected = await (await request('/review/' + encodeURIComponent(identifier))).json();
    if (selected.state === 'completed') { retainedReview(null); review = null; el('spend-review').hidden = true; message('This accounting review is already completed. No correction was repeated.'); }
    else showReview(selected);
  }));
  el('spend-hold-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!el('spend-hold-form').reportValidity()) { message('Complete the final cost and receipt details.'); return; }
    run(async () => {
      const held = holds[Number(el('spend-hold').value)];
      if (!held) throw new Error('Choose a current unresolved reservation.');
      showReview(await json('/hold-review', {provider: held.provider, period: held.period, receipt: held.id,
        actual: Number(el('spend-actual').value), evidence: el('spend-evidence').value}));
    });
  });
  function appendTotal(row, index) {
    if (row.additional) {
      for (const [name, title, pattern] of [['provider', 'Later provider name', '[a-z0-9][a-z0-9_.-]*'], ['period', 'UTC day or month', '[0-9]{4}-[0-9]{2}(-[0-9]{2})?']]) {
        const label = document.createElement('label'); label.htmlFor = `spend-${name}-${index}`; label.textContent = title;
        const input = document.createElement('input'); input.id = label.htmlFor; input.required = true; input.pattern = pattern; input.maxLength = name === 'provider' ? 128 : 10;
        el('spend-totals').append(label, input);
      }
    }
    const label = document.createElement('label'); label.htmlFor = `spend-total-${index}`;
    label.textContent = `${row.additional ? 'Later provider/period' : row.provider + ' · ' + row.period}: confirmed total (at least ${dollars(row.confirmed)}; ${dollars(row.reserved)} reserved separately)`;
    const input = document.createElement('input'); input.id = label.htmlFor; input.type = 'number'; input.step = 'any'; input.min = String(row.confirmed); input.required = true;
    el('spend-totals').append(label, input);
  }
  el('spend-add-total').addEventListener('click', () => {
    if (!imported || busy || totals.length >= 500) return;
    totals.push({additional: true, confirmed: 0, reserved: 0}); appendTotal(totals[totals.length - 1], totals.length - 1);
  });
  el('spend-inspect').addEventListener('click', () => run(async () => {
    const file = el('spend-import').files[0];
    if (!file || file.size > 16 * 1024 * 1024) throw new Error('Select a verified accounting copy no larger than16 MiB.');
    imported = await (await request('/import', {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file})).json();
    totals = imported.totals; el('spend-totals').replaceChildren();
    totals.forEach(appendTotal);
    el('spend-receipts-complete').checked = false;
    el('spend-export-date').textContent = 'Verified export created ' + new Date(imported.exported_at * 1000).toISOString() + '. Account for every subsequent call before confirming.';
    el('spend-repair-form').hidden = false;
    message('The complete accounting copy is verified. Review final receipts and enter every confirmed total; no live accounting has changed.');
  }));
  el('spend-repair-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!el('spend-repair-form').reportValidity()) { message('Complete every total and confirm final receipt coverage.'); return; }
    run(async () => {
      if (!imported) throw new Error('Select and verify the complete accounting copy first.');
      showReview(await json('/repair-review', {imported: imported.id,
        totals: totals.map((row, index) => ({provider: row.additional ? el(`spend-provider-${index}`).value.trim() : row.provider,
          period: row.additional ? el(`spend-period-${index}`).value.trim() : row.period, confirmed: Number(el(`spend-total-${index}`).value)})),
        evidence: el('spend-repair-evidence').value, receipts_complete: el('spend-receipts-complete').checked}));
    });
  });
  el('spend-apply').addEventListener('click', () => run(async () => {
    if (!review || !el('spend-confirm').checked) throw new Error('Review and explicitly confirm the accounting change.');
    const result = await json('/apply', {review: review.id, confirm: true});
    if (result.state !== 'completed') throw new Error('The accounting change is not confirmed. Keep its review and refresh before retrying.');
    retainedReview(null);
    await refresh(); message('Reviewed accounting correction completed. Other reservations were preserved. No provider call was retried.');
  }));
  run(refresh);
})();
