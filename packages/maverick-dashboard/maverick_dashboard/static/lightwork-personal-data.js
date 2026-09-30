(function () {
  'use strict';
  const el = id => document.getElementById(id);
  let state = JSON.parse(el('pd-bootstrap').textContent), busy = false, dirty = false;
  function say(message) { el('pd-status').textContent = message; }
  function text(parent, value, tag = 'p') { const node = document.createElement(tag); node.textContent = value; parent.appendChild(node); return node; }
  function button(parent, label, handler) { const node = text(parent, label, 'button'); node.type = 'button'; node.className = 'btn'; node.addEventListener('click', handler); return node; }
  async function call(path, data) {
    const response = await fetch('/api/v1/my-data' + path, {method: data ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json'}, body: data ? JSON.stringify({...data, context: state.context}) : undefined});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Access changed or this operation is unavailable. Your draft is kept. Refresh saved state before retrying.');
    return result;
  }
  async function action(work, refresh = true) {
    if (busy) return;
    busy = true;
    try { const result = await work(); if (refresh) { state = await call(''); dirty = false; render(); } say(result && result.message ? result.message : 'Saved state confirmed.'); return result; }
    catch (error) { say(error.message + ' Current editor text has been kept.'); }
    finally { busy = false; }
  }
  function scopeBody(scope) { return {channel: scope.channel, user_id: scope.user_id, expected_revision: scope.revision}; }
  function render() {
    el('pd-channel').replaceChildren(new Option('Choose a channel', ''));
    state.identity.channels.forEach(channel => el('pd-channel').appendChild(new Option(channel, channel)));
    el('pd-links').replaceChildren(); el('pd-proofs').replaceChildren();
    state.identity.links.forEach(link => {
      const card = text(el('pd-links'), '', 'article');
      text(card, link.channel + ' sender ' + (link.sender_id || link.user_id) + ' - verified ' + new Date(link.verified_at * 1000).toLocaleString());
      button(card, 'Unlink and stop future sharing', () => action(() => call('/links/' + encodeURIComponent(link.id) + '/unlink', {expected_revision: state.identity.revision})));
    });
    state.identity.challenges.forEach(proof => {
      const card = text(el('pd-proofs'), '', 'article');
      text(card, proof.channel + ': ' + proof.status.replaceAll('_', ' ') + (proof.sender_id ? '; sender ' + proof.sender_id : '') + '; expires ' + new Date(proof.expires_at * 1000).toLocaleString());
      if (proof.status === 'proof_received') button(card, 'Confirm this is my channel account', () => action(() => call('/links/' + encodeURIComponent(proof.id) + '/confirm', {expected_revision: state.identity.revision})));
    });
    el('pd-ceiling').textContent = state.policy.personal_recall ? 'Administrator policy permits personal preference recall. Your per-channel restrictions apply below.' : 'Administrator policy disables personal preference recall. Stored notes can still be inspected and restricted; turning on your preference does not override this ceiling.';
    el('pd-source').replaceChildren(new Option('Channel-only recall', ''));
    state.scopes.forEach(scope => el('pd-source').appendChild(new Option('Use ' + scope.channel + ' preferences across linked channels', scope.id)));
    el('pd-source').value = state.sharing.shared_source || '';
    el('pd-delete-scope').replaceChildren(...state.scopes.map(scope => new Option(scope.channel, scope.id)));
    el('pd-delete-review').hidden = !state.can_review_deletion;
    el('pd-memory').replaceChildren();
    state.scopes.forEach(scope => {
      const card = text(el('pd-memory'), '', 'section'); card.className = 'panel';
      text(card, scope.channel + ' personal memory', 'h3');
      text(card, scope.recall ? 'Your personal recall preference is on; administrator ceiling still applies.' : 'Personal recall and future preference collection are off for this scope.');
      button(card, scope.recall ? 'Turn off personal recall' : 'Allow recall within policy', () => action(() => call('/memory', {...scopeBody(scope), recall: !scope.recall})));
      button(card, 'Export this verified conversation scope', () => action(async () => {
        const result = await call('/export', {channel: scope.channel, user_id: scope.user_id});
        const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type: 'application/json'}));
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'lightwork-my-data.json'; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        return {message: 'Selected-scope export created. Separate stores and external copies are listed in the inventory.'};
      }, false));
      if (!scope.notes.length) text(card, 'No recalled preference notes in this scope.');
      scope.notes.forEach(note => {
        const row = text(card, '', 'article');
        const label = text(row, 'Remembered preference', 'label');
        const input = document.createElement('textarea'); input.value = note.note; input.maxLength = 160; input.rows = 2; input.addEventListener('input', () => { dirty = true; }); label.appendChild(input);
        text(row, note.source_state + (note.source_ts ? '; ' + new Date(note.source_ts * 1000).toLocaleString() : ''));
        if (note.conversation_id && note.turn_id) button(row, 'Read original source', () => action(async () => {
          const source = await call('/source/' + note.conversation_id + '/' + note.turn_id);
          el('pd-source-text').textContent = source.text + '\n\nSource revision: ' + source.revision;
          return {message: source.revision === note.source_revision ? 'Authorized source loaded.' : 'The original turn changed after this note was consolidated. Review the current source before editing.'};
        }, false));
        ['edit', 'delete', 'suppress'].forEach(operation => button(row, {edit: 'Save correction', delete: 'Delete this preference', suppress: 'Do not remember this topic again'}[operation], () => action(() => call('/memory', {...scopeBody(scope), note: note.id, operation, replacement: operation === 'edit' ? input.value : null}))));
      });
    });
    el('pd-policy').replaceChildren();
    text(el('pd-policy'), 'Organization lesson promotion for eligible shared sources: ' + (state.policy.organization_lesson_promotion ? 'enabled by administrator policy' : 'disabled by administrator policy') + '. Private task reflections are excluded from organization-wide lesson promotion. Shared knowledge requires its own source purpose and authorization.');
    Object.entries(state.policy.effective_learning || {}).forEach(([name, enabled]) => text(el('pd-policy'), name.replaceAll('_', ' ') + ': ' + (enabled ? 'administrator policy permits; source restrictions still apply' : 'disabled by administrator policy')));
    text(el('pd-policy'), state.policy.model_skill_learning); text(el('pd-policy'), state.policy.companion);
  }
  el('pd-refresh').addEventListener('click', () => {
    if (dirty && !window.confirm('Refresh saved state and discard the unsaved note edits shown here?')) return;
    action(async () => ({}));
  });
  el('pd-link').addEventListener('submit', event => { event.preventDefault(); action(async () => {
    const result = await call('/links', {channel: el('pd-channel').value});
    el('pd-command').textContent = 'Send this text to Lightwork in a private conversation, then refresh this page: ' + result.command;
    return {message: 'Linking text created. It expires in ten minutes. Sharing is still off.'};
  }); });
  el('pd-share').addEventListener('submit', event => { event.preventDefault(); action(() => call('/sharing', {source: el('pd-source').value || null, expected_revision: state.sharing.revision})); });
  const deletionKinds = {preference_notes: 'Remembered preference notes (also stops personal recall)', conversation_history: 'Selected conversation closure and its task/attachment data', recovery_drafts: 'All my private editor recovery copies', companion_history: 'My employee companion history', experience_preferences: 'My account and thread display/style preferences', model_memory: 'Selected scope model-managed memory files', notification_receipts: 'My notification delivery receipts', diagnostic_reports: 'My diagnostic reports'};
  Object.entries(deletionKinds).forEach(([key, label]) => {
    const row = text(el('pd-delete-stores'), '', 'label'); row.style.display = 'block';
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = key;
    row.append(input, document.createTextNode(' ' + label));
  });
  function deletionSummary(parent, record) {
    text(parent, (record.id || 'Preview') + ': ' + (record.status || (record.holds.blocked ? 'held' : 'review before requesting')), 'h3');
    record.targets.forEach(target => {
      text(parent, deletionKinds[target.store] + ' — ' + target.state);
      if (target.count !== undefined) text(parent, target.count + ' retained items');
      if (target.scope) text(parent, target.scope.conversation_ids.length + ' conversations; ' + target.scope.goal_ids.length + ' associated tasks; ' + target.scope.episode_ids.length + ' task episodes.');
      ['message', 'retained', 'use', 'receipt_id'].forEach(key => { if (target[key]) text(parent, key.replaceAll('_', ' ') + ': ' + target[key]); });
      if (target.stores) Object.entries(target.stores).forEach(([name, value]) => text(parent, name + ': ' + value.state + '; remaining ' + value.remaining));
      if (target.attachment_files) text(parent, 'Attachment files: ' + target.attachment_files.state + '; failed removals ' + target.attachment_files.failures);
    });
    record.exceptions.forEach(exception => text(parent, exception.store.replaceAll('_', ' ') + ': ' + exception.state + '. ' + exception.reason));
    if (record._audit_pending) text(parent, 'Activity receipt is pending. Nothing further may be dispatched until it is acknowledged.');
  }
  el('pd-delete').addEventListener('submit', event => { event.preventDefault(); action(async () => {
    const scope = state.scopes.find(value => value.id === el('pd-delete-scope').value);
    const selected = Array.from(el('pd-delete-stores').querySelectorAll('input:checked')).map(input => input.value);
    const body = {channel: scope.channel, user_id: scope.user_id, stores: selected};
    const preview = await call('/deletion-preview', body);
    el('pd-delete-preview').replaceChildren(); deletionSummary(el('pd-delete-preview'), preview);
    button(el('pd-delete-preview'), 'Request this exact deletion', () => action(async () => {
      const request = await call('/deletion-requests', {...body, snapshot: preview.snapshot});
      el('pd-delete-preview').replaceChildren(); deletionSummary(el('pd-delete-preview'), request);
      return {message: 'Deletion request saved. Its displayed status is authoritative; requested or held does not mean deleted.'};
    }, false));
    return {message: 'Exact scope prepared. Review every retained exception before requesting deletion.'};
  }, false); });
  async function deletionRequests(review) {
    const result = await call('/deletion-requests' + (review ? '?review=true' : ''));
    el('pd-delete-requests').replaceChildren();
    result.requests.forEach(record => {
      const card = text(el('pd-delete-requests'), '', 'article'); deletionSummary(card, record);
      if (review && ['requested', 'held'].includes(record.status)) {
        text(card, 'Requested by ' + record.owner + '. The source owner must remain a current member; changed source scope requires a new request.');
        const label = text(card, '', 'label'); const confirm = document.createElement('input'); confirm.type = 'checkbox'; label.append(confirm, document.createTextNode(' I checked external preservation obligations with the records owner and confirm no external hold prevents these deletions.'));
        button(card, 'Apply reviewed deletion', () => action(async () => {
          const receipt = await call('/deletion-requests/' + encodeURIComponent(record.id) + '/apply', {expected_revision: record.revision, external_holds_clear: confirm.checked});
          card.replaceChildren(); deletionSummary(card, receipt);
          return {message: 'Saved per-store deletion outcome loaded. Retained exceptions remain explicit.'};
        }, false));
      }
    });
    return {message: result.requests.length ? 'Current deletion requests loaded.' : 'No deletion requests in this authorized view.'};
  }
  el('pd-delete-status').addEventListener('click', () => action(() => deletionRequests(false), false));
  el('pd-delete-review').addEventListener('click', () => action(() => deletionRequests(true), false));
  el('pd-inventory').addEventListener('click', () => action(async () => {
    const inventory = await call('/inventory'); el('pd-stores').replaceChildren(); el('pd-tasks').replaceChildren();
    text(el('pd-stores'), inventory.holds.blocked ? 'Preservation holds are active. A held record is not deleted; ask the records owner about permitted correction and retirement.' : 'No recorded workspace privacy-rights hold. External holds still need records-owner review.');
    text(el('pd-stores'), inventory.limits);
    inventory.stores.forEach(store => {
      const row = text(el('pd-stores'), '', 'article'); text(row, store.name + ' - ' + store.state, 'h3');
      ['use', 'expiry', 'action'].forEach(key => { if (store[key]) text(row, store[key]); });
      if (store.stores) text(row, 'Included stores: ' + store.stores.join(', '));
      if (store.inventory) text(row, store.inventory.saved + ' saved private drafts; ' + store.inventory.discarded + ' discarded copies awaiting retirement.');
      if (store.count !== undefined) text(row, store.count + ' retained entries.');
      if (store.href) { const link = text(row, 'Open its governed controls', 'a'); link.href = store.href; }
    });
    text(el('pd-tasks'), 'Task-specific local learning', 'h3');
    if (inventory.tasks_truncated) text(el('pd-tasks'), 'Showing the latest 5,000 owned tasks; older tasks remain outside this view.');
    inventory.tasks.forEach(task => {
      const row = text(el('pd-tasks'), '', 'p'); text(row, 'Task ' + task.id + ': ' + task.title + ' - ' + ({excluded: 'future local learning excluded', permitted: 'existing learning policy applies', unavailable: 'policy unavailable; learning blocked, exclusion receipt unconfirmed'}[task.learning_state] || 'learning policy unconfirmed'), 'span');
      if (task.learning_state === 'permitted') button(row, 'Exclude future local learning', () => action(() => call('/tasks/' + task.id + '/exclude-learning', {}), false));
    });
    return {message: 'Current retained-store inventory loaded; unavailable and external-unverified stores are explicitly marked.'};
  }, false));
  render();
}());
