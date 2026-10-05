(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let current = null, model = null, busy = false;
  const node = (tag, text) => { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; return item; };
  const say = text => { $('par-status').textContent = text; };
  async function call(path, body) {
    return window.mvFetchJSON('/api/v1/access-requests' + path, {method: body ? 'POST' : 'GET', body, plainDetail: true},
      body ? 'save the access request' : 'load the access requests');
  }
  function explanation(parent, value) {
    parent.appendChild(node('p', value.capability + ': ' + (value.allowed ? 'Allowed by the current root grant.' : 'Blocked by the current root grant.') + ' Policy owner: ' + value.owner + '.'));
    const list = node('ul'); (value.layers || []).forEach(layer => list.appendChild(node('li', layer.source + ': ' + (layer.reason || layer.role || 'Current policy')))); parent.appendChild(list); parent.appendChild(node('p', value.limit));
  }
  async function load() {
    current = null; $('par-submit').hidden = true;
    model = await call(''); say(model.enabled ? 'Current policy loaded. Reviews do not change permissions automatically.' : 'New permission requests are disabled. Current explanations and saved cases remain available.');
    const cases = $('par-cases'); cases.replaceChildren();
    if (!model.records.length) cases.appendChild(node('p', 'No visible permission requests.'));
    model.records.forEach(row => {
      const article = node('article'); article.className = 'panel'; article.appendChild(node('h3', row.capability + ' — ' + row.status)); article.appendChild(node('p', row.reason)); explanation(article, row.current);
      if (row.decision_note) article.appendChild(node('p', 'Reviewer: ' + row.decision_note));
      if (row.audit_pending) article.appendChild(node('p', 'The activity receipt is pending. Refresh before relying on this outcome.'));
      if (row.can_review && model.enabled) {
        const form = node('form'); form.dataset.draftRecovery = 'off'; const select = node('select'); select.className = 'select'; select.setAttribute('aria-label', 'Review result'); [['reviewing', 'Under review'], ['deny', 'Decline request'], ['confirm_effective', 'Confirm actual permission is effective']].forEach(choice => select.appendChild(new Option(choice[1], choice[0])));
        const label = node('label', 'Review explanation (shared with employee) '), note = node('textarea'); note.required = true; note.minLength = 10; note.maxLength = 1000; label.appendChild(note);
        const button = node('button', 'Record reviewed result'); button.type = 'submit'; button.className = 'btn'; form.append(select, label, button); form.onsubmit = event => { event.preventDefault(); act(() => call('/' + encodeURIComponent(row.id) + '/decision', {expected_revision: row.revision, decision: select.value, note: note.value})); }; article.appendChild(form);
      }
      cases.appendChild(article);
    });
  }
  async function act(operation) { if (busy) return; busy = true; try { await operation(); await load(); } catch (error) { say(error.message); } finally { busy = false; } }
  function clearReview() { current = null; $('par-submit').hidden = true; $('par-explanation').replaceChildren(); }
  $('par-kind').onchange = clearReview; $('par-capability').oninput = clearReview;
  $('par-explain').onsubmit = async event => { event.preventDefault(); if (busy) return; busy = true; clearReview(); try { current = await call('/explain', {kind: $('par-kind').value, capability: $('par-capability').value}); explanation($('par-explanation'), current); $('par-submit').hidden = !model?.enabled || current.allowed; $('par-confirm').checked = false; } catch (error) { say(error.message); } finally { busy = false; } };
  $('par-submit').onsubmit = event => { event.preventDefault(); if (!current || !$('par-confirm').checked) return; act(() => call('', {kind: current.kind, capability: current.capability, snapshot: current.snapshot, reason: $('par-reason').value, confirm_shared_with_reviewer: true})); };
  $('par-refresh').onclick = () => load().catch(error => say(error.message)); load().catch(error => say(error.message));
})();
