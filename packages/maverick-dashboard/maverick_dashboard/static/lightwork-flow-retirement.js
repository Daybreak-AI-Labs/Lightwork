(function () {
  'use strict';
  var busy = false;
  function count(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  function error(response) {
    if (response.headers && response.headers.get('X-Lightwork-Pending-Forms') === 'true')
      return 'Received form submissions are still waiting. Review them in Automations before deleting this flow.';
    if (response.status === 409) return 'This flow changed while you were reviewing it. Open it again before trying.';
    if (response.status === 404) return 'This flow is no longer here. Refresh the page.';
    return window.mvErrorText ? window.mvErrorText(response, 'change this flow') : 'Could not change this flow. Try again.';
  }
  async function change(flowId, action, options) {
    options = options || {};
    if (busy || !flowId) return false;
    busy = true;
    var button = options.button;
    if (button) button.disabled = true;
    try {
      var url = '/api/v1/flows/' + encodeURIComponent(flowId);
      var response = await fetch(url + '/retirement');
      if (!response.ok) throw new Error(error(response));
      var preview = await response.json();
      if (preview.id !== flowId || !Number.isInteger(preview.version) || preview.version < 0 ||
          typeof preview.revision !== 'string' || typeof preview.release_id !== 'string') {
        throw new Error('The current flow could not be confirmed. Refresh and try again.');
      }
      if (action === 'off' && !preview.published) {
        if (window.mvToast) window.mvToast('This flow is already turned off.');
        if (options.onDone) options.onDone(action, preview);
        return true;
      }
      var name = preview.name || options.name || 'this flow';
      if (action === 'delete' && preview.pending_forms) {
        var open = await window.mvConfirm(
          count(preview.pending_forms, 'received form submission is', 'received form submissions are') +
          ' still waiting. Review them in Automations and choose Check now or Do not run before deleting “' + name + '”.',
          {okText: 'Open Automations'}
        );
        if (open) window.location.assign('/automations');
        return false;
      }
      var effects = [];
      if (preview.scheduled) effects.push('Its schedule will stop.');
      if (preview.connected_starts) effects.push(count(preview.connected_starts, 'connected start remains', 'connected starts remain') + ' set up, but cannot start this flow. Reconnect them in Automations if you publish it again.');
      if (preview.active_runs) effects.push(count(preview.active_runs, 'run has', 'runs have') + ' already started or paused and may continue. Review them in Recent runs.');
      if (preview.pending_forms) effects.push(count(preview.pending_forms, 'received form submission will', 'received form submissions will') + ' wait for an explicit Do not run decision in Automations.');
      if (preview.published_parent_flows) effects.push(count(preview.published_parent_flows, 'other published flow contains', 'other published flows contain') + ' a saved copy of this flow. Turn those off separately to stop their future runs.');
      if (options.dirty) effects.push('Unsaved changes in this editor will be lost if you delete it.');
      var message = action === 'delete'
        ? 'Delete “' + name + '” and its saved versions? This cannot be undone. Past run records stay available. '
        : 'Turn off “' + name + '”? New scheduled and connected starts will stop. You can publish it again later. ';
      var confirmed = await window.mvConfirm(message + effects.join(' '), {
        okText: action === 'delete' ? 'Delete flow' : 'Turn off flow'
      });
      if (!confirmed) return false;
      if (action === 'delete') {
        var params = new URLSearchParams({version: String(preview.version), revision: preview.revision,
          expected_release_id: preview.release_id});
        response = await fetch(url + '?' + params, {method: 'DELETE'});
        if (!response.ok) throw new Error(error(response));
        if (response.headers.get('X-Lightwork-Recovery-Copy') === 'needs-review') {
          try { window.sessionStorage.setItem('lightwork-flow-retirement-notice',
            'Flow deleted, but its private editor recovery copy could not be cleared. Ask an administrator to review recovery storage before reusing this flow name.'); }
          catch (_) { if (window.mvToast) window.mvToast('Flow deleted, but its editor recovery copy needs review.', {error: true}); }
        }
      } else {
        response = await fetch(url + '/unpublish', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({version: preview.version, revision: preview.revision,
            expected_release_id: preview.release_id})
        });
        if (!response.ok) throw new Error(error(response));
        var result = await response.json();
        if (result.id !== flowId || result.published !== false) {
          throw new Error('The change could not be confirmed. Refresh this flow before trying again.');
        }
      }
      if (options.onDone) options.onDone(action, preview);
      else if (window.mvToast) window.mvToast(action === 'delete' ? 'Flow deleted.' : 'Flow turned off.');
      return true;
    } catch (e) {
      if (window.mvToast) window.mvToast(e.message || 'Could not change this flow.', {error: true});
      return false;
    } finally {
      busy = false;
      if (button && button.isConnected) button.disabled = false;
    }
  }
  window.addEventListener('load', function () {
    try {
      var notice = window.sessionStorage.getItem('lightwork-flow-retirement-notice');
      if (notice) {
        window.sessionStorage.removeItem('lightwork-flow-retirement-notice');
        if (window.mvToast) window.mvToast(notice, {error: true});
      }
    } catch (_) { /* The deletion receipt is still authoritative. */ }
  });
  window.LightworkFlowRetirement = {change: change};
})();
