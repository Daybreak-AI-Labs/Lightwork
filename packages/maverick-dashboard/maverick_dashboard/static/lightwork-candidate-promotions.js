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
      .forEach(control => { control.disabled = value || control.dataset.unavailable === 'true'; });
  }

  async function api(path, method = 'GET', body, action = 'load candidate promotions') {
    return readJSON(BASE + path, method, body, action);
  }

  async function readJSON(url, method = 'GET', body, action = 'read the saved release') {
    let response;
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 20000);
    try {
      response = await fetch(url, {
        method,
        cache: 'no-store',
        signal: controller.signal,
        headers: body ? {'Accept':'application/json', 'Content-Type':'application/json'} : {'Accept':'application/json'},
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw new Error(typeof window.mvErrorText === 'function'
        ? window.mvErrorText(response, action) : 'Could not ' + action + '. Refresh before continuing.');
      return await response.json();
    } catch (_) { throw new Error('Lightwork could not confirm this reply. Refresh saved release decisions before continuing.'); }
    finally { clearTimeout(deadline); }
  }

  // The shared in-page dialog; the browser's own box only when it is missing.
  function confirmAction(prompt, okText) {
    return typeof window.mvConfirm === 'function'
      ? window.mvConfirm(prompt, {okText})
      : Promise.resolve(window.confirm(prompt));
  }

  function shortDigest(value) {
    const text = typeof value === 'string' ? value : '';
    return /^[a-f0-9]{64}$/.test(text) ? text.slice(0, 12) + '…' : 'unavailable';
  }

  function label(value) {
    const states = {awaiting_review: 'Waiting for independent review', authorized: 'Approved for preparation',
      staged: 'Prepared for publication', canary: 'Published for a controlled trial', active: 'Published; execution check passed',
      rolled_back: 'Previous published rules restored', uncertain: 'Saved outcome needs checking', denied: 'Review declined'};
    if (states[value]) return states[value];
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
    if (busy || !(await confirmAction(prompt, 'Continue'))) return;
    if (busy) return;
    setBusy(true);
    try {
      const result = await api('/' + encodeURIComponent(candidate.candidate_id) + '/' + action, 'POST', {
        context,
        confirm: true,
        expected_revision: candidate.revision,
        ...extra,
      }, 'update this candidate');
      await refresh();
      message(candidate.local_release ? (candidate.local_release.name || 'Selected workflow') + ': ' + label(result.state) + '.' : 'Candidate ' + candidate.candidate_id + ' is now updated.');
    } catch (error) {
      try { await refresh(); message('The reply could not be confirmed. Current release decisions were refreshed; inspect the saved result before trying another action.', true); }
      catch (_) { message(error.message || 'Candidate promotion action failed. Refresh before trying again.', true); }
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
    const approve = element('button', candidate.local_release ? 'Approve exact workflow version' : 'Approve exact candidate', 'btn btn--primary');
    const reject = element('button', candidate.local_release ? 'Decline this workflow release' : 'Reject candidate', 'btn');
    approve.type = reject.type = 'button';
    async function submit(decision) {
      if (!form.reportValidity()) return;
      await transition(
        candidate,
        'review',
        candidate.local_release ? decision
          ? 'Approve this exact saved workflow version for preparation? It will be published only after a separate publication decision.'
          : 'Decline this exact workflow version and keep your decision in its history?'
        : decision
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
      candidate.local_release ? candidate.local_release.rollback : 'Roll back this candidate and reconcile application state, pending work, and external state?',
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
    line(item, candidate.local_release ? 'Execution readiness' : 'Study',
      candidate.local_release ? 'Successful exact-version practice; independent approval required' : label(candidate.study?.conclusion));
    if (!candidate.local_release) {
      line(item, 'Run', candidate.run?.run_id || 'unavailable');
      line(item, 'Evaluator', [candidate.study?.evaluator_id, candidate.study?.evaluator_version].filter(Boolean).join(' ') || 'unavailable');
      line(item, 'Deployment target', candidate.target?.target_id || 'unavailable');
    }
    if (candidate.local_release) {
      const release = candidate.local_release;
      line(item, 'Taught workflow', (release.name || 'Selected workflow') + ' · saved version ' + (release.version || 'unavailable'));
      line(item, 'Actual finished release runs', release.observed_runs || 0);
      line(item, 'Evidence limit', release.evidence);
      line(item, 'Restore behavior', release.rollback || 'Retained release must be checked before restoration.');
      const known = release.known_case_comparison;
      if (known?.cases) {
        line(item, 'Selected known cases', known.cases);
        line(item, 'Expected answers matched', 'Published rules: ' + known.baseline_matches + ' · proposed rules: ' + known.candidate_matches);
        line(item, 'Local replay time', 'Published rules: ' + known.baseline_elapsed_ms + ' ms · proposed rules: ' + known.candidate_elapsed_ms + ' ms');
      }
      if (known?.scope) line(item, 'Comparison scope', known.scope);
      if (canManage) {
        const review = element('button', 'Read exact workflow instructions and examples', 'btn btn--ghost');
        review.type = 'button';
        review.addEventListener('click', async () => {
          if (busy) return;
          setBusy(true);
          try {
            const report = await readJSON('/api/v1/research/local-flow-release/review/' + encodeURIComponent(candidate.candidate_id));
            const previous = item.querySelector('[data-local-release-review]'); if (previous) previous.remove();
            const panel = element('div', undefined, 'research-advanced'); panel.dataset.localReleaseReview = 'true';
            (report.known_case_comparison?.reviewed_cases || []).forEach((example, index) => {
              const section = element('section'); section.appendChild(element('strong', 'Selected real case ' + (index + 1)));
              line(section, 'Expected final answer', example.expected);
              line(section, 'Published-rule answer', example.baseline + ' · ' + (example.baseline_matched ? 'Matches expected answer' : 'Does not match'));
              line(section, 'Proposed-rule answer', example.candidate + ' · ' + (example.candidate_matched ? 'Matches expected answer' : 'Does not match'));
              Object.entries(example.inputs || {}).forEach(([name, value]) => line(section, name.replace(/_/g, ' '), typeof value === 'object' ? Object.entries(value).map(([key, item]) => key.replace(/_/g, ' ') + ': ' + String(item)).join(' · ') : String(value)));
              panel.appendChild(section);
            });
            (report.steps || []).forEach(step => {
              const section = element('section'); section.appendChild(element('strong', step.label));
              if (step.instructions) line(section, 'Work instructions', step.instructions);
              if (step.fallback) line(section, 'When rules cannot answer', step.fallback);
              Object.entries(step.questions || {}).forEach(([name, limits]) => line(section, 'Input ' + name,
                ({integer: 'Whole number', string: 'Text', boolean: 'Yes or no'}[limits.type] || 'Value') + (limits.min !== undefined ? ' · from ' + limits.min + ' to ' + limits.max : limits.max_length ? ' · up to ' + limits.max_length + ' characters' : '')));
              (step.examples || []).forEach((example, index) => line(section, 'Teaching example ' + (index + 1),
                Object.entries(example.inputs || {}).map(([key, value]) => key + ': ' + String(value)).join(' · ') + ' → ' + String(example.output)));
              if (step.condition) line(section, 'Condition', step.condition);
              Object.entries(step.assignments || {}).forEach(([name, value]) => line(section, 'Set ' + name, value));
              if (step.output) line(section, 'Result name', step.output);
              if (step.next) line(section, 'Next step', step.next);
              if (step.if_true) line(section, 'When true', step.if_true);
              if (step.if_false) line(section, 'When false', step.if_false);
              (step.cases || []).forEach(choice => line(section, 'Route', (choice.condition || '') + ' → ' + (choice.next || '')));
              panel.appendChild(section);
            });
            item.appendChild(panel);
          } catch (error) { message(error.message, true); }
          finally { setBusy(false); }
        });
        item.appendChild(review);
      }
    }

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
      line(item, 'Independent review', `${candidate.review.approved ? 'approved' : 'rejected'} by ${String(candidate.review.reviewed_by || 'unknown').replace(/^user:/, '')} · ${label(candidate.review.reason_code)}`);
      if (candidate.review.note) line(item, 'Review note', candidate.review.note);
    }

    if (!candidate.local_release) {
    const detail = element('details', undefined, 'research-advanced');
    detail.appendChild(element('summary', 'Evidence and compatibility'));
    line(detail, 'Candidate record fingerprint', shortDigest(candidate.manifest_sha256));
    line(detail, 'Policy fingerprint', shortDigest(candidate.policy_sha256));
    line(detail, 'Evaluated output', shortDigest(candidate.run?.output_artifact_sha256));
    line(detail, 'Expected current version', shortDigest(candidate.target?.expected_active_sha256));
    const lineage = Array.isArray(candidate.lineage) ? candidate.lineage : [];
    line(detail, 'Current data and model lineage', lineage.length ? lineage.map(value => `${value.kind} ${value.asset_id} ${value.version}`).join(', ') : 'unavailable');
    item.appendChild(detail);
    }

    if (!canManage) return item;
    if (candidate.state === 'awaiting_review') item.appendChild(reviewForm(candidate));
    if (candidate.state === 'authorized') item.appendChild(actionButton(
      candidate.local_release ? 'Prepare approved workflow' : 'Stage candidate', candidate, 'stage',
      candidate.local_release ? 'Prepare this approved workflow version and keep its exact previous published rules available for restoration? Nothing is published yet.' : 'Stage this exact candidate after another current rights and artifact check?', 'btn btn--primary'));
    if (candidate.state === 'staged') item.appendChild(actionButton(
      candidate.local_release ? 'Publish for a controlled trial' : 'Start canary', candidate, 'activate',
      candidate.local_release ? 'Publish this exact prepared workflow version for a controlled trial? New runs will use these rules. The previous published rules remain available for restoration.' : 'Activate this staged candidate as a canary? Lightwork will retain the compatible rollback target.', 'btn btn--primary'));
    if (candidate.state === 'canary') {
      const assess = actionButton(
        candidate.local_release ? 'Check actual trial runs' : 'Assess canary and apply rollback rules', candidate, 'canary',
        candidate.local_release ? 'Check the recorded failures of actual runs from this publication? If its acceptance rules fail, Lightwork restores the retained previous rules. This check does not establish answer accuracy or business improvement.' : 'Read the current canary observations and immediately roll back if a declared invariant fails?', 'btn btn--primary');
      if (candidate.local_release && !candidate.local_release.can_assess) {
        assess.disabled = true;
        assess.dataset.unavailable = 'true';
        line(item, 'Next step', 'The workflow owner must finish a real run of this published release. Refresh afterward to assess its observed failure rate.');
      }
      item.appendChild(assess);
      item.appendChild(rollbackForm(candidate));
    }
    if (candidate.state === 'active') item.appendChild(rollbackForm(candidate));
    if (candidate.state === 'uncertain') item.appendChild(actionButton(
      candidate.local_release ? 'Check the uncertain saved result' : 'Reconcile uncertain deployment', candidate, 'reconcile',
      candidate.local_release ? 'Read the actual published rules and saved decision without repeating the uncertain action?' : 'Read the authoritative deployment state without replaying the uncertain action?', 'btn btn--primary'));
    return item;
  }

  function render(value) {
    if (!value || typeof value !== 'object' || !Array.isArray(value.items) ||
        !Array.isArray(value.completed_runs) || !Array.isArray(value.target_presets) ||
        typeof value.available !== 'boolean' || typeof value.can_manage !== 'boolean' ||
        typeof value.can_propose !== 'boolean') {
      throw new Error('The candidate list could not be read. Refresh and try again.');
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
        const named = (value.completed_run_choices || []).find(choice => choice.value === runId);
        runChoice.appendChild(new Option(named?.label || runId, runId, false, runId === selectedRun));
      });
      presetChoice.replaceChildren(new Option('Choose a deployment target', ''));
      value.target_presets.forEach(raw => {
        const preset = raw && typeof raw === 'object' ? raw : {};
        if (!ID.test(String(preset.preset_id || '')) ||
            !ID.test(String(preset.target_id || ''))) return;
        presetChoice.appendChild(new Option(
          preset.target_id === 'installed-lightwork' ? 'This Lightwork installation' : `${label(preset.preset_id)} · ${label(preset.target_id)}`,
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
      const item = renderCandidate(candidate, value.can_manage && candidate.can_decide !== false);
      if (item) list.appendChild(item);
    });
    if (!list.children.length) list.appendChild(element('li', 'No candidates have been proposed for deployment.'));
    message(`Loaded ${list.children.length === 1 && !value.items.length ? 0 : value.items.length} candidate promotion record${value.items.length === 1 ? '' : 's'}.`);
  }

  async function refresh() {
    const value = await api('');
    render(value);
  }

  let shown = false;
  async function runRefresh() {
    if (busy) return;
    setBusy(true);
    try { await refresh(); shown = true; }
    catch (error) {
      message(error.message || 'Candidate promotions could not be loaded.', true);
      // Nothing was ever shown: clear the list's "Loading…"; the status line
      // above it says what happened and what to do.
      if (!shown) list.replaceChildren();
    }
    finally { setBusy(false); }
  }

  refreshButton.addEventListener('click', runRefresh);
  if (proposalForm) proposalForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !proposalForm.reportValidity()) return;
    if (!(await confirmAction('Create a review request from this completed run and reviewed target preset? This does not stage or activate anything.', 'Create request'))) return;
    if (busy) return;
    setBusy(true);
    try {
      const value = await api('', 'POST', {
        context,
        confirm: true,
        candidate_id: field(proposalForm, 'candidate_id'),
        run_id: field(proposalForm, 'run_id'),
        target_preset: field(proposalForm, 'target_preset'),
      }, 'create the review request');
      message(
        value.state === 'not_advancing'
          ? 'Candidate ' + value.candidate_id + ' was retained as a non-advancing study result.'
          : 'Candidate ' + value.candidate_id + ' was retained for independent review.'
      );
      proposalForm.reset();
      await refresh();
    } catch (error) {
      try { await refresh(); message('The reply could not be confirmed. Current release requests were refreshed; inspect the saved result before trying another action.', true); }
      catch (_) { message('The saved request could not be checked. Refresh before trying again.', true); }
    } finally {
      setBusy(false);
    }
  });
  runRefresh();
})();
