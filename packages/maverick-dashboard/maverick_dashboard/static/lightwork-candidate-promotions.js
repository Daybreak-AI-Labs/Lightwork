'use strict';
(function () {
  const root = document.getElementById('research-lab');
  const list = document.getElementById('research-promotions-list');
  const status = document.getElementById('research-promotions-status');
  const readiness = document.getElementById('research-promotions-readiness');
  const refreshButton = document.getElementById('research-promotions-refresh');
  const proposalPanel = document.getElementById('research-promotion-proposal');
  const proposalForm = document.getElementById('research-promotion-form');
  if (!root || !list || !status || !readiness || !refreshButton) return;

  const BASE = '/api/v1/research/promotions';
  const ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
  let context = root.dataset.context || '';
  let busy = false;

  function element(tag, text, className) {
    const value = document.createElement(tag);
    if (text !== undefined) value.textContent = String(text);
    if (className) value.className = className;
    return value;
  }

  function message(text, failed) {
    status.textContent = text;
    status.dataset.state = failed ? 'error' : 'ready';
  }

  function setBusy(value) {
    busy = value;
    root.querySelectorAll('#research-promotions button, #research-promotions input, #research-promotions select, #research-promotions textarea')
      .forEach(control => { control.disabled = value; });
  }

  async function api(path, method = 'GET', body) {
    const response = await fetch(BASE + path, {
      method,
      cache: 'no-store',
      headers: body ? {'Accept':'application/json', 'Content-Type':'application/json'} : {'Accept':'application/json'},
      body: body ? JSON.stringify(body) : undefined,
    });
    let value = {};
    try { value = await response.json(); } catch (_) { value = {}; }
    if (!response.ok) throw new Error(value.detail || 'Candidate promotion is unavailable.');
    return value;
  }

  function shortDigest(value) {
    const text = typeof value === 'string' ? value : '';
    return /^[a-f0-9]{64}$/.test(text) ? text.slice(0, 12) + '…' : 'unavailable';
  }

  function label(value) {
    return String(value || 'unknown').replaceAll('_', ' ');
  }

  function field(form, name) {
    const control = form.elements.namedItem(name);
    return control ? String(control.value || '').trim() : '';
  }

  function line(parent, title, value) {
    const row = element('p');
    row.append(element('strong', title + ': '), document.createTextNode(String(value)));
    parent.appendChild(row);
  }

  async function transition(candidate, action, prompt, extra = {}) {
    if (busy || !window.confirm(prompt)) return;
    setBusy(true);
    try {
      await api('/' + encodeURIComponent(candidate.candidate_id) + '/' + action, 'POST', {
        context,
        confirm: true,
        expected_revision: candidate.revision,
        ...extra,
      });
      message('Candidate ' + candidate.candidate_id + ' is now updated.');
      await refresh();
    } catch (error) {
      message(error.message || 'Candidate promotion action failed.', true);
    } finally {
      setBusy(false);
    }
  }

  function actionButton(text, candidate, action, prompt, className = 'btn') {
    const button = element('button', text, className);
    button.type = 'button';
    button.addEventListener('click', () => transition(candidate, action, prompt));
    return button;
  }

  function reviewForm(candidate) {
    const form = element('form', undefined, 'research-form research-advanced');
    const heading = element('strong', 'Independent review');
    const reasonLabel = element('label', 'Decision reason');
    const reason = element('select'); reason.className = 'input'; reason.required = true;
    [
      ['reviewed_scope', 'Reviewed and within scope'],
      ['insufficient_evidence', 'Evidence is incomplete'],
      ['incompatible_target', 'Target is incompatible'],
      ['needs_revision', 'Candidate needs changes'],
    ].forEach(([value, text]) => reason.appendChild(new Option(text, value)));
    reasonLabel.appendChild(reason);
    const noteLabel = element('label', 'Review note');
    const note = element('textarea');
    note.className = 'input'; note.required = true; note.maxLength = 1024; note.rows = 3;
    note.placeholder = 'Explain what you verified and any limits.';
    noteLabel.appendChild(note);
    const actions = element('div', undefined, 'research-actions');
    const approve = element('button', 'Approve exact candidate', 'btn btn--primary');
    const reject = element('button', 'Reject candidate', 'btn');
    approve.type = reject.type = 'button';
    async function submit(decision) {
      if (!form.reportValidity()) return;
      await transition(
        candidate,
        'review',
        decision
          ? 'Approve this exact candidate for staged qualification? This does not activate it.'
          : 'Reject this exact candidate and retain the decision in its history?',
        {approve: decision, reason_code: reason.value, note: note.value.trim()},
      );
    }
    approve.addEventListener('click', () => submit(true));
    reject.addEventListener('click', () => submit(false));
    actions.append(approve, reject);
    form.append(heading, reasonLabel, noteLabel, actions);
    return form;
  }

  function rollbackForm(candidate) {
    const form = element('form', undefined, 'research-form research-advanced');
    const labelNode = element('label', 'Rollback reason');
    const reason = element('select'); reason.className = 'input';
    [
      ['operator_requested', 'Operator requested'],
      ['observed_regression', 'Observed regression'],
      ['rights_changed', 'Rights changed'],
      ['target_incompatible', 'Target compatibility changed'],
    ].forEach(([value, text]) => reason.appendChild(new Option(text, value)));
    labelNode.appendChild(reason);
    const button = element('button', 'Roll back to previous compatible version', 'btn');
    button.type = 'button';
    button.addEventListener('click', () => transition(
      candidate,
      'rollback',
      'Roll back this candidate and reconcile application state, pending work, and external state?',
      {reason_code: reason.value},
    ));
    form.append(labelNode, button);
    return form;
  }

  function renderCandidate(candidate, canManage) {
    if (!candidate || !ID.test(String(candidate.candidate_id || '')) ||
        !Number.isInteger(candidate.revision) || candidate.revision < 1) return null;
    const item = element('li', undefined, 'research-item');
    item.appendChild(element('strong', candidate.candidate_id));
    const badge = element('span', label(candidate.state), 'research-badge');
    badge.dataset.state = candidate.state || 'unknown';
    item.append(' ', badge);
    line(item, 'Study', label(candidate.study?.conclusion));
    line(item, 'Run', candidate.run?.run_id || 'unavailable');
    line(item, 'Evaluator', [candidate.study?.evaluator_id, candidate.study?.evaluator_version].filter(Boolean).join(' ') || 'unavailable');
    line(item, 'Deployment target', candidate.target?.target_id || 'unavailable');

    if (candidate.block?.detail) {
      const block = element('div', undefined, 'notice notice--warning');
      block.appendChild(element('strong', 'Promotion stopped'));
      block.appendChild(element('p', candidate.block.detail));
      const remediation = Array.isArray(candidate.block.remediation) ? candidate.block.remediation : [];
      remediation.forEach(value => {
        const row = value && typeof value === 'object' ? value : {};
        block.appendChild(element('p', `${row.kind || 'asset'} ${row.asset_id || ''} ${row.version || ''}: ${label(row.required_action)}`));
      });
      item.appendChild(block);
    }
    if (candidate.review) {
      line(item, 'Independent review', `${candidate.review.approved ? 'approved' : 'rejected'} by ${candidate.review.reviewed_by || 'unknown'} · ${label(candidate.review.reason_code)}`);
      if (candidate.review.note) line(item, 'Review note', candidate.review.note);
    }

    const detail = element('details', undefined, 'research-advanced');
    detail.appendChild(element('summary', 'Evidence and compatibility'));
    line(detail, 'Candidate record fingerprint', shortDigest(candidate.manifest_sha256));
    line(detail, 'Policy fingerprint', shortDigest(candidate.policy_sha256));
    line(detail, 'Evaluated output', shortDigest(candidate.run?.output_artifact_sha256));
    line(detail, 'Expected current version', shortDigest(candidate.target?.expected_active_sha256));
    const lineage = Array.isArray(candidate.lineage) ? candidate.lineage : [];
    line(detail, 'Current data and model lineage', lineage.length ? lineage.map(value => `${value.kind} ${value.asset_id} ${value.version}`).join(', ') : 'unavailable');
    item.appendChild(detail);

    if (!canManage) return item;
    if (candidate.state === 'awaiting_review') item.appendChild(reviewForm(candidate));
    if (candidate.state === 'authorized') item.appendChild(actionButton(
      'Stage candidate', candidate, 'stage',
      'Stage this exact candidate after another current rights and artifact check?', 'btn btn--primary'));
    if (candidate.state === 'staged') item.appendChild(actionButton(
      'Start canary', candidate, 'activate',
      'Activate this staged candidate as a canary? Lightwork will retain the compatible rollback target.', 'btn btn--primary'));
    if (candidate.state === 'canary') {
      item.appendChild(actionButton(
        'Assess canary and apply rollback rules', candidate, 'canary',
        'Read the current canary observations and immediately roll back if a declared invariant fails?', 'btn btn--primary'));
      item.appendChild(rollbackForm(candidate));
    }
    if (candidate.state === 'active') item.appendChild(rollbackForm(candidate));
    if (candidate.state === 'uncertain') item.appendChild(actionButton(
      'Reconcile uncertain deployment', candidate, 'reconcile',
      'Read the authoritative deployment state without replaying the uncertain action?', 'btn btn--primary'));
    return item;
  }

  function render(value) {
    if (!value || typeof value !== 'object' || !Array.isArray(value.items) ||
        !Array.isArray(value.completed_runs) || !Array.isArray(value.target_presets) ||
        typeof value.available !== 'boolean' || typeof value.can_manage !== 'boolean' ||
        typeof value.can_propose !== 'boolean') {
      throw new Error('Candidate promotion response was malformed.');
    }
    if (typeof value.context === 'string') context = value.context;
    list.replaceChildren();
    readiness.textContent = value.detail || '';
    if (proposalPanel) proposalPanel.hidden = !(value.available && value.can_propose);
    if (proposalForm) {
      const runChoice = proposalForm.elements.namedItem('run_id');
      const presetChoice = proposalForm.elements.namedItem('target_preset');
      const selectedRun = field(proposalForm, 'run_id');
      const selectedPreset = field(proposalForm, 'target_preset');
      runChoice.replaceChildren(new Option('Choose a completed run', ''));
      value.completed_runs.filter(runId => ID.test(String(runId || ''))).forEach(runId => {
        runChoice.appendChild(new Option(runId, runId, false, runId === selectedRun));
      });
      presetChoice.replaceChildren(new Option('Choose a deployment target', ''));
      value.target_presets.forEach(raw => {
        const preset = raw && typeof raw === 'object' ? raw : {};
        if (!ID.test(String(preset.preset_id || '')) ||
            !ID.test(String(preset.target_id || ''))) return;
        presetChoice.appendChild(new Option(
          `${label(preset.preset_id)} · ${label(preset.target_id)}`,
          preset.preset_id,
          false,
          preset.preset_id === selectedPreset,
        ));
      });
    }
    if (!value.available) {
      list.appendChild(element('li', 'Candidate promotion is not configured for this installation.'));
      message('Candidate review remains read-only until the trusted deployment controls are configured.');
      return;
    }
    value.items.forEach(candidate => {
      const item = renderCandidate(candidate, value.can_manage);
      if (item) list.appendChild(item);
    });
    if (!list.children.length) list.appendChild(element('li', 'No candidates have been proposed for deployment.'));
    message(`Loaded ${list.children.length === 1 && !value.items.length ? 0 : value.items.length} candidate promotion record${value.items.length === 1 ? '' : 's'}.`);
  }

  async function refresh() {
    const value = await api('');
    render(value);
  }

  async function runRefresh() {
    if (busy) return;
    setBusy(true);
    try { await refresh(); }
    catch (error) { message(error.message || 'Candidate promotions could not be loaded.', true); }
    finally { setBusy(false); }
  }

  refreshButton.addEventListener('click', runRefresh);
  if (proposalForm) proposalForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !proposalForm.reportValidity()) return;
    if (!window.confirm('Create a review request from this completed run and reviewed target preset? This does not stage or activate anything.')) return;
    setBusy(true);
    try {
      const value = await api('', 'POST', {
        context,
        confirm: true,
        candidate_id: field(proposalForm, 'candidate_id'),
        run_id: field(proposalForm, 'run_id'),
        target_preset: field(proposalForm, 'target_preset'),
      });
      message(
        value.state === 'not_advancing'
          ? 'Candidate ' + value.candidate_id + ' was retained as a non-advancing study result.'
          : 'Candidate ' + value.candidate_id + ' was retained for independent review.'
      );
      proposalForm.reset();
      await refresh();
    } catch (error) {
      message(error.message || 'Candidate review request could not be created.', true);
    } finally {
      setBusy(false);
    }
  });
  runRefresh();
})();
