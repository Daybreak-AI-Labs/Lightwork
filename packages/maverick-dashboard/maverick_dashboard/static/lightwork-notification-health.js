(function () {
  'use strict';
  const summary = document.getElementById('notification-health-summary');
  if (!summary) return;
  const errors = document.getElementById('notification-health-error');
  const list = document.getElementById('notification-health-list');
  const test = document.getElementById('notification-test');
  const refresh = document.getElementById('notification-refresh');
  let busy = false, enabled = false, requestId = null;
  function setBusy(value) { busy = value; test.disabled = value || !enabled; refresh.disabled = value; }
  function render(state) {
    enabled = state.enabled && ['ntfy', 'pushover', 'slack', 'discord'].includes(state.backend);
    const last = state.last_success_at ? new Date(state.last_success_at * 1000).toLocaleString() : 'No confirmed delivery in recent history';
    summary.textContent = state.enabled ? 'Current channel: ' + state.backend + '. Last accepted delivery: ' + last + '. Seen by a person: unknown.' : 'Delivery history is turned off. Enable it in All settings before sending a test.';
    list.replaceChildren();
    if (!state.receipts.length) list.textContent = 'No delivery receipts yet. A saved configuration does not confirm delivery.';
    state.receipts.forEach(function (row) {
      const article = document.createElement('article');
      const title = document.createElement('h3');
      const labels = {queued:'Queued', sending:'Sending', delivered:'Accepted by service', failed:'Failed', unknown:'Unknown result'};
      title.textContent = row.backend + ' - ' + (labels[row.state] || 'Unknown result');
      const description = document.createElement('p'); description.textContent = row.message;
      const time = document.createElement('p'); time.className = 'muted'; time.textContent = new Date(row.updated_at * 1000).toLocaleString() + ' · Attempt ' + row.attempts + ' · ' + (row.kind === 'test' ? 'Delivery test' : 'Organization alert');
      article.append(title, description, time);
      if (row.kind === 'test' && row.state === 'failed' && row.safe_to_retry && row.attempts < 3) {
        const retry = document.createElement('button'); retry.className = 'btn btn--ghost'; retry.textContent = 'Retry this refused test';
        retry.addEventListener('click', function () { if (!busy) request('/api/v1/notifications/retry', {receipt_id:row.id}); });
        article.appendChild(retry);
      }
      list.appendChild(article);
    });
  }
  async function request(url, body) {
    if (busy) return;
    setBusy(true); errors.hidden = true;
    try {
      const response = await fetch(url, {method: body ? 'POST':'GET', credentials:'same-origin', headers:body ? {'Content-Type':'application/json'}:{}, body:body ? JSON.stringify(body):undefined});
      let result; try { result = await response.json(); } catch (_) { throw new Error('The delivery result could not be read. Refresh status before sending again.'); }
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) { enabled = false; list.replaceChildren(); }
        throw new Error(typeof result.detail === 'string' ? result.detail : 'Delivery status is unavailable. Refresh before sending again.');
      }
      render(result);
      // A lost response keeps the same request ID for safe recovery.
      if (body && body.request_id) requestId = null;
    } catch (error) { errors.textContent = error.message || 'Could not check delivery. Refresh before sending again.'; errors.hidden = false; }
    finally { setBusy(false); }
  }
  test.addEventListener('click', function () {
    if (!requestId) requestId = crypto.randomUUID();
    request('/api/v1/notifications/test', {request_id:requestId});
  });
  refresh.addEventListener('click', function () { request('/api/v1/notifications/health'); });
  request('/api/v1/notifications/health');
})();
