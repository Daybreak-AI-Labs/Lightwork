(() => {
  'use strict';
  const root = document.getElementById('research-lab');
  const status = document.getElementById('research-governed-status');
  if (!root || !status) return;

  const context = root.dataset.context || '';
  const byId = id => document.getElementById(id);
  const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const identifier = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value);
  let workspace = null;
  let busy = false;

  function element(tag, text, className) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function message(text, error) {
    status.textContent = text;
    status.classList.toggle('error', !!error);
  }

  function setBusy(value) {
    busy = value;
    root.querySelectorAll('.research-workspace button, .research-workspace input, .research-workspace select, .research-workspace textarea')
      .forEach(control => { control.disabled = value || control.dataset.permanentDisabled === 'true'; });
    if (!value) syncConditionalFields();
  }

  async function api(path, method, body) {
    const options = {method: method || 'GET', cache: 'no-store', headers: {'Accept': 'application/json'}};
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/api/v1/research' + path, options);
    let value = null;
    try { value = await response.json(); } catch (_) { value = null; }
    if (!response.ok) {
      const detail = value && typeof value.detail === 'string' && value.detail.length <= 512 ? value.detail : '';
      const error = new Error(response.status === 409 ? (detail || 'This research record changed. Refresh and review it again.') :
        response.status === 403 ? 'Your current account cannot perform that research action.' :
        response.status === 422 ? (detail || 'Review the governed research fields and try again.') :
        response.status === 404 ? 'This research record is not available in the current workspace.' :
        (detail || 'Governed research is temporarily unavailable.'));
      error.status = response.status;
      throw error;
    }
    if (value && value.context && value.context !== context) {
      throw new Error('The account or workspace changed. Reload this page.');
    }
    return value;
  }

  function validReference(value, kind) {
    return value && (value.kind === 'dataset' || value.kind === 'model') &&
      (!kind || value.kind === kind) && identifier(value.asset_id) &&
      identifier(value.version) && sha(value.manifest_sha256) &&
      value.schema === 'lightwork.research-registry-ref.v1';
  }

  function validAsset(value, kind) {
    return value && validReference(value.reference, kind) && value.kind === kind &&
      ['draft', 'approved', 'revoked'].includes(value.state) &&
      Number.isSafeInteger(value.revision) && value.revision >= 1 &&
      Array.isArray(value.allowed_purposes) && Array.isArray(value.usage_constraints) &&
      value.eligibility && ['eligible', 'blocked', 'unavailable', 'awaiting_approval', 'revoked']
        .includes(value.eligibility.state);
  }

  function validRun(value) {
    return value && identifier(value.run_id) && typeof value.status === 'string' &&
      Number.isSafeInteger(value.revision) && value.revision >= 1 &&
      value.manifest && Array.isArray(value.manifest.dataset_refs) &&
      Array.isArray(value.attempts) && value.cost_summary &&
      typeof value.cost_summary.incomplete === 'boolean';
  }

  function validImpact(value) {
    return value && validReference(value.source) &&
      ['draft', 'approved', 'revoked'].includes(value.source_state) &&
      value.raw_deletion_removes_weight_influence === false && Array.isArray(value.derivatives) &&
      value.derivatives.every(row => validReference(row.reference) &&
        ['invalidate_dataset', 'retire_or_retrain_model'].includes(row.required_action));
  }

  function validate(value) {
    if (!value || value.context !== context || !value.registry ||
        !Array.isArray(value.registry.datasets) || value.registry.datasets.some(row => !validAsset(row, 'dataset')) ||
        !Array.isArray(value.registry.models) || value.registry.models.some(row => !validAsset(row, 'model')) ||
        typeof value.registry.can_review !== 'boolean' || !Array.isArray(value.governed_runs) ||
        value.governed_runs.some(row => !validRun(row)) || !value.governed_execution ||
        typeof value.governed_execution.available !== 'boolean') {
      throw new Error('The governed research overview could not be verified. Refresh before taking action.');
    }
    return value;
  }

  function sortedUnique(values) {
    return [...new Set(values.map(value => value.trim()).filter(Boolean))].sort();
  }

  function commaValues(value) {
    return sortedUnique(value.split(','));
  }

  function lineValues(value) {
    return sortedUnique(value.split(/\r?\n/));
  }

  function refKey(reference) {
    return `${reference.kind}:${reference.asset_id}:${reference.version}:${reference.manifest_sha256}`;
  }

  function sortRefs(values) {
    return values.sort((left, right) =>
      [left.asset_id, left.version, left.manifest_sha256].join('\0')
        .localeCompare([right.asset_id, right.version, right.manifest_sha256].join('\0')));
  }

  function selectedRefs(select, kind) {
    return sortRefs([...select.selectedOptions].map(option => JSON.parse(option.value)))
      .filter(reference => validReference(reference, kind));
  }

  function optional(form, name) {
    const value = form.elements[name].value.trim();
    return value || null;
  }

  function numberOrNull(form, name) {
    const value = form.elements[name].value.trim();
    return value === '' ? null : Number(value);
  }

  function refLabel(reference) {
    return `${reference.asset_id} · ${reference.version} · ${reference.manifest_sha256.slice(0, 12)}…`;
  }

  function replaceOptions(select, assets, {multiple = true, blank = ''} = {}) {
    const selected = new Set([...select.selectedOptions].map(option => option.value));
    select.replaceChildren();
    if (!multiple) select.appendChild(new Option(blank, ''));
    assets.forEach(asset => {
      const value = JSON.stringify(asset.reference);
      const option = new Option(refLabel(asset.reference), value);
      option.selected = selected.has(value);
      select.appendChild(option);
    });
  }

  function renderSelectors() {
    replaceOptions(byId('research-dataset-form').elements.parent_datasets, workspace.registry.datasets);
    replaceOptions(byId('research-model-form').elements.dataset_lineage, workspace.registry.datasets);
    replaceOptions(byId('research-model-form').elements.model_lineage, workspace.registry.models);
    renderRunSelectors();
    const decisionSelect = byId('research-dataset-form').elements.data_use_decision_choice;
    decisionSelect.replaceChildren(new Option('Choose an authorized allow decision or enter one below', ''));
    const decisions = workspace.registry.data_use_decisions?.items || [];
    decisions.forEach(decision => {
      if (!identifier(decision.decision_id) || !sha(decision.decision_sha256) ||
          !identifier(decision.asset_id) || !['analyze', 'export', 'read', 'train'].includes(decision.action) ||
          !Array.isArray(decision.purpose_codes)) return;
      decisionSelect.appendChild(new Option(
        `${decision.asset_id} · ${decision.action} · ${decision.decision_id}`,
        JSON.stringify(decision),
      ));
    });
  }

  function renderRunSelectors() {
    const form = byId('research-run-form');
    const purpose = form.elements.purpose.value.trim();
    const action = form.elements.action.value;
    const current = row => row.state === 'approved' && row.eligibility.state === 'eligible' &&
      (!purpose || row.allowed_purposes.includes(purpose));
    const datasets = workspace.registry.datasets.filter(row =>
      current(row) && row.data_use_action === action);
    const models = workspace.registry.models.filter(current);
    replaceOptions(form.elements.dataset_refs, datasets);
    replaceOptions(form.elements.model_ref, models, {multiple:false, blank:'No model'});
  }

  function appendLineage(item, title, references) {
    if (!Array.isArray(references) || !references.length) return;
    item.appendChild(element('p', title, 'muted'));
    const list = element('ul', undefined, 'research-lineage');
    references.forEach(reference => list.appendChild(element('li', refLabel(reference))));
    item.appendChild(list);
  }

  function actionButton(label, work, className = 'btn') {
    const button = element('button', label, className);
    button.type = 'button';
    button.addEventListener('click', () => act(work));
    return button;
  }

  function assetActions(item, asset) {
    const actions = element('div', undefined, 'research-actions');
    actions.appendChild(actionButton('View revocation impact', async () => {
      const ref = asset.reference;
      const value = await api(`/registry/${encodeURIComponent(ref.kind)}/${encodeURIComponent(ref.asset_id)}/${encodeURIComponent(ref.version)}/impact?manifest_sha256=${encodeURIComponent(ref.manifest_sha256)}`);
      if (!validImpact(value)) throw new Error('The derivative impact response could not be verified.');
      renderImpact(value);
    }));
    if (workspace.registry.can_review && asset.state === 'draft') {
      actions.appendChild(actionButton('Approve version', async () => {
        await api('/registry/approve', 'POST', {context, confirm:true,
          reference:asset.reference, expected_revision:asset.revision});
        message('Registry version approved after current rights and lineage checks.');
        await refresh();
      }, 'btn btn--primary'));
    }
    item.appendChild(actions);
    if (workspace.registry.can_review && asset.state === 'approved') {
      const details = element('details', undefined, 'research-advanced');
      details.appendChild(element('summary', 'Revoke this version'));
      const form = element('form', undefined, 'research-form');
      const reasonLabel = element('label', 'Reason code');
      const reason = element('input');
      reason.className = 'input'; reason.name = 'reason_code'; reason.required = true;
      reason.pattern = '[a-z][a-z0-9_.-]{0,63}'; reason.maxLength = 64;
      reasonLabel.appendChild(reason);
      const evidenceLabel = element('label', 'Evidence SHA-256');
      const evidence = element('input');
      evidence.className = 'input'; evidence.name = 'evidence_sha256'; evidence.required = true;
      evidence.pattern = '[a-f0-9]{64}'; evidence.maxLength = 64;
      evidenceLabel.appendChild(evidence);
      const confirmLabel = element('label', undefined, 'check');
      const confirm = element('input'); confirm.type = 'checkbox'; confirm.required = true;
      confirmLabel.append(confirm, document.createTextNode('I reviewed the affected future use and derivatives.'));
      const submit = element('button', 'Revoke version', 'btn'); submit.type = 'submit';
      form.append(reasonLabel, evidenceLabel, confirmLabel, submit);
      form.addEventListener('submit', event => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        act(async () => {
          const impact = await api(`/registry/${encodeURIComponent(asset.kind)}/${encodeURIComponent(asset.reference.asset_id)}/${encodeURIComponent(asset.reference.version)}/impact?manifest_sha256=${encodeURIComponent(asset.reference.manifest_sha256)}`);
          if (!validImpact(impact)) throw new Error('The derivative impact response could not be verified.');
          renderImpact(impact);
          await api('/registry/revoke', 'POST', {context, confirm:true, reference:asset.reference,
            expected_revision:asset.revision, reason_code:reason.value.trim(),
            evidence_sha256:evidence.value.trim()});
          message('Registry version revoked. Future admission is blocked; affected derivatives are listed below.');
          await refresh();
        });
      });
      details.appendChild(form);
      item.appendChild(details);
    }
  }

  function renderAssets() {
    const list = byId('research-registry-items');
    list.replaceChildren();
    const assets = [...workspace.registry.datasets, ...workspace.registry.models];
    if (!assets.length) list.appendChild(element('li', 'No governed dataset or model versions yet.'));
    assets.forEach(asset => {
      const item = element('li', undefined, 'research-item');
      item.appendChild(element('strong', `${asset.kind === 'dataset' ? 'Dataset' : 'Model'} · ${asset.reference.asset_id} · ${asset.reference.version}`));
      const badge = element('span', asset.state, 'research-badge'); badge.dataset.state = asset.state;
      item.append(' ', badge);
      if (asset.provenance_kind === 'synthetic') item.append(' ', element('span', 'Synthetic fixture', 'research-badge'));
      item.appendChild(element('p', `Source: ${asset.source_ref}`));
      item.appendChild(element('p', `Manifest: ${asset.reference.manifest_sha256}`, 'muted'));
      item.appendChild(element('p', `License: ${asset.license_id} · Allowed purposes: ${asset.allowed_purposes.join(', ')}`));
      item.appendChild(element('p', `Usage constraints: ${asset.usage_constraints.join('; ')}`));
      item.appendChild(element('p', `Current use eligibility: ${asset.eligibility.state.replaceAll('_', ' ')}${asset.eligibility.detail ? ` · ${asset.eligibility.detail}` : ''}`));
      if (asset.kind === 'dataset') {
        item.appendChild(element('p', `Content SHA-256: ${asset.content_sha256}`));
        item.appendChild(element('p', `Data-use decision: ${asset.data_use_decision_id} · ${asset.data_use_action}`));
        if (asset.consent_record_id) item.appendChild(element('p', `Consent record: ${asset.consent_record_id}`));
        appendLineage(item, 'Parent datasets', asset.parent_datasets);
        if (asset.synthetic_provenance) {
          item.appendChild(element('p', `Synthetic generator: ${asset.synthetic_provenance.generator_id} ${asset.synthetic_provenance.generator_version} · seed ${asset.synthetic_provenance.seed}`));
        }
      } else {
        item.appendChild(element('p', `Artifact SHA-256: ${asset.artifact_sha256}`));
        const revision = asset.immutable_provider_revision ||
          `unpinnable; observed ${asset.observed_from}–${asset.observed_until}`;
        item.appendChild(element('p', `Provider identity: ${asset.provider_model_identity} · ${revision}`));
        if (asset.reproducibility_limitation) item.appendChild(element('p', `Reproducibility limit: ${asset.reproducibility_limitation}`));
        appendLineage(item, 'Dataset lineage', asset.dataset_lineage);
        appendLineage(item, 'Model lineage', asset.model_lineage);
      }
      if (asset.approval) item.appendChild(element('p', `Approved by ${asset.approval.approved_by}`, 'muted'));
      if (asset.revocation) item.appendChild(element('p', `Revoked: ${asset.revocation.reason_code}`, 'error'));
      assetActions(item, asset);
      list.appendChild(item);
    });
  }

  function renderImpact(value) {
    const host = byId('research-impact');
    host.replaceChildren();
    host.appendChild(element('h4', 'Current derivative impact'));
    host.appendChild(element('p', `${value.derivatives.length} affected derivative version${value.derivatives.length === 1 ? '' : 's'}.`));
    const list = element('ul', undefined, 'research-lineage');
    value.derivatives.forEach(row => list.appendChild(element('li',
      `${row.reference.kind} ${row.reference.asset_id} ${row.reference.version}: ${row.required_action.replaceAll('_', ' ')}`)));
    host.appendChild(list);
    host.appendChild(element('p', 'Deleting or excluding raw data does not prove that learned weight influence was removed. Affected models must be retired, reviewed, or retrained.', 'muted'));
  }

  function money(summary) {
    const values = Object.entries(summary.effective_amounts || {});
    const known = values.length ? values.map(([currency, amount]) => `${currency} ${amount}`).join(', ') : 'no recorded amount';
    return `${known}${summary.incomplete ? ' · incomplete cost evidence' : ' · cost evidence complete for recorded attempts'}`;
  }

  function renderAttempt(run, attempt, item) {
    const details = element('details', undefined, 'research-costs');
    details.appendChild(element('summary', `Attempt ${attempt.number} · ${attempt.status} · ${attempt.costs.length} cost event${attempt.costs.length === 1 ? '' : 's'}`));
    if (!attempt.costs.length) details.appendChild(element('p', 'No resource cost events recorded for this attempt.', 'muted'));
    attempt.costs.forEach(event => {
      const cost = event.cost;
      const known = cost.amount === null ? `unknown: ${cost.incomplete_reason}` : `${cost.currency} ${cost.amount}`;
      details.appendChild(element('p', `${cost.category} · ${cost.basis} · ${known} · evidence ${cost.evidence_sha256}`));
    });
    if (attempt.metrics.length) {
      appendLineage(details, 'Metrics', attempt.metrics.map(metric => ({
        asset_id: `${metric.name}=${metric.value}`, version: metric.evaluator_version,
        manifest_sha256: metric.lineage_sha256,
      })));
    }
    item.appendChild(details);
  }

  function runAction(item, run, label, suffix, allowed, className = 'btn') {
    if (!allowed) return;
    const button = actionButton(label, async () => {
      await api(`/governed-runs/${encodeURIComponent(run.run_id)}/${suffix}`, 'POST',
        {context, confirm:true, expected_revision:run.revision});
      message(`${label} completed for ${run.run_id}.`);
      await refresh();
    }, className);
    if (suffix === 'start' && !workspace.governed_execution.available) {
      button.disabled = true;
      button.dataset.permanentDisabled = 'true';
      button.title = workspace.governed_execution.detail;
    }
    item.appendChild(button);
  }

  function cancellationForm(run) {
    const details = element('details', undefined, 'research-advanced');
    details.appendChild(element('summary', 'Cancel before dispatch'));
    const form = element('form', undefined, 'research-form');
    const reason = element('input'); reason.className = 'input'; reason.required = true;
    reason.pattern = '[a-z][a-z0-9_.-]{0,63}'; reason.placeholder = 'operator_cancelled';
    const evidence = element('input'); evidence.className = 'input'; evidence.required = true;
    evidence.pattern = '[a-f0-9]{64}'; evidence.placeholder = 'Cancellation evidence SHA-256';
    const confirmLabel = element('label', undefined, 'check');
    const confirm = element('input'); confirm.type = 'checkbox'; confirm.required = true;
    confirmLabel.append(confirm, document.createTextNode('Cancel this exact pre-dispatch attempt.'));
    const submit = element('button', 'Cancel run', 'btn'); submit.type = 'submit';
    form.append(reason, evidence, confirmLabel, submit);
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      act(async () => {
        await api(`/governed-runs/${encodeURIComponent(run.run_id)}/cancel`, 'POST', {context,
          confirm:true, expected_revision:run.revision, reason_code:reason.value.trim(),
          evidence_sha256:evidence.value.trim()});
        message(`Run ${run.run_id} cancelled before dispatch.`);
        await refresh();
      });
    });
    details.appendChild(form);
    return details;
  }

  function renderRuns() {
    const list = byId('research-governed-runs');
    list.replaceChildren();
    if (!workspace.governed_runs.length) list.appendChild(element('li', 'No governed runs yet.'));
    workspace.governed_runs.forEach(run => {
      const item = element('li', undefined, 'research-item');
      item.appendChild(element('strong', run.run_id));
      const badge = element('span', run.status.replaceAll('_', ' '), 'research-badge');
      badge.dataset.state = run.status; item.append(' ', badge);
      item.appendChild(element('p', `Experiment: ${run.manifest.experiment_id} ${run.manifest.experiment_version} · ${run.manifest.action} for ${run.manifest.purpose}`));
      item.appendChild(element('p', `Seed ${run.manifest.seed} · code ${run.manifest.code_revision} · environment ${run.manifest.environment_id} ${run.manifest.environment_version}`));
      const caps = run.manifest.resource_caps;
      item.appendChild(element('p', `Caps: ${caps.max_wall_seconds}s · ${caps.max_provider_calls} provider calls · ${caps.currency} ${caps.max_cost} · ${caps.max_storage_bytes} storage bytes`));
      item.appendChild(element('p', `Effective cost: ${money(run.cost_summary)}`));
      run.attempts.forEach(attempt => renderAttempt(run, attempt, item));
      const actions = element('div', undefined, 'research-actions');
      runAction(actions, run, 'Queue run', 'queue', run.status === 'planned', 'btn btn--primary');
      runAction(actions, run, 'Start run', 'start', run.status === 'queued', 'btn btn--primary');
      runAction(actions, run, 'Retry failed run', 'retry', run.status === 'failed');
      if (run.status !== 'planned') actions.appendChild(actionButton('Export deterministic evidence',
        () => downloadRun(run.run_id)));
      item.appendChild(actions);
      if (['planned', 'queued'].includes(run.status)) item.appendChild(cancellationForm(run));
      list.appendChild(item);
    });
  }

  async function downloadRun(runId) {
    const response = await fetch(`/api/v1/research/governed-runs/${encodeURIComponent(runId)}/export`,
      {method:'GET', cache:'no-store', headers:{'Accept':'application/json'}});
    if (!response.ok) {
      let detail = '';
      try { detail = (await response.json()).detail || ''; } catch (_) { detail = ''; }
      throw new Error(detail || 'The deterministic evidence export is unavailable.');
    }
    const bytes = await boundedBytes(response, 2200000);
    let evidence;
    try { evidence = JSON.parse(new TextDecoder().decode(bytes)); }
    catch (_) { throw new Error('The evidence export was not valid JSON.'); }
    if (!evidence || evidence.schema !== 'lightwork.research-run-export.v1' ||
        evidence.run?.run_id !== runId || !evidence.cost_summary ||
        evidence.data_statement?.contains_raw_customer_data !== false ||
        evidence.projection_statement?.contains_authority_credentials !== false ||
        evidence.projection_statement?.contains_resource_cost_source_locations !== false) {
      throw new Error('The evidence export did not match this exact safe run projection.');
    }
    const blob = new Blob([bytes], {type:'application/json'});
    const url = URL.createObjectURL(blob);
    const link = element('a'); link.href = url; link.download = `research-run-${runId}.json`;
    document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    message(`Deterministic evidence exported for ${runId}.`);
  }

  async function boundedBytes(response, maximum) {
    const declared = Number(response.headers?.get?.('content-length'));
    if (Number.isFinite(declared) && declared > maximum) {
      throw new Error('The evidence export exceeded its size boundary.');
    }
    if (!response.body?.getReader) {
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength > maximum) throw new Error('The evidence export exceeded its size boundary.');
      return bytes;
    }
    const reader = response.body.getReader();
    const chunks = [];
    let length = 0;
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maximum) {
        await reader.cancel();
        throw new Error('The evidence export exceeded its size boundary.');
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    chunks.forEach(chunk => { bytes.set(chunk, offset); offset += chunk.byteLength; });
    return bytes;
  }

  function render(value) {
    workspace = validate(value);
    renderSelectors();
    renderAssets();
    renderRuns();
    const execution = workspace.governed_execution;
    byId('research-materialization-state').textContent = execution.available ?
      'Trusted materialization adapter configured.' :
      'Execution adapter unavailable. Plans can be reviewed and queued; start is blocked before data access.';
    const dataUseState = workspace.registry.data_use_decisions?.state || 'unavailable';
    byId('research-registry-authority-state').textContent =
      `Data-use decision picker: ${dataUseState.replaceAll('_', ' ')}. ` +
      (workspace.registry.consent_authority_available ?
        'Consent-bound dataset checks are configured.' :
        'Consent-bound dataset approval is unavailable until a trusted consent authority adapter is configured. ') +
      'Signed training receipts are verified against server trust policy when used.';
    message(`Loaded ${workspace.registry.datasets.length} dataset version${workspace.registry.datasets.length === 1 ? '' : 's'}, ${workspace.registry.models.length} model version${workspace.registry.models.length === 1 ? '' : 's'}, and ${workspace.governed_runs.length} governed run${workspace.governed_runs.length === 1 ? '' : 's'}.`);
    syncConditionalFields();
  }

  async function refresh() {
    render(await api(''));
  }

  async function act(work) {
    if (busy) return;
    setBusy(true);
    try { await work(); }
    catch (error) { message(error.message || 'Governed research action failed.', true); }
    finally { setBusy(false); }
  }

  function syncConditionalFields() {
    const dataset = byId('research-dataset-form');
    const synthetic = dataset.elements.provenance_kind.value === 'synthetic';
    byId('research-synthetic-fields').hidden = !synthetic;
    ['synthetic_generator_id', 'synthetic_generator_version', 'synthetic_generator_sha256',
      'synthetic_config_sha256', 'synthetic_seed'].forEach(name => {
      dataset.elements[name].required = synthetic;
    });
    dataset.elements.parent_datasets.required = dataset.elements.provenance_kind.value === 'derived';
    const consentBound = dataset.elements.consent_record_id.value.trim() !== '' ||
      dataset.elements.consent_evidence_sha256.value.trim() !== '';
    dataset.elements.consent_record_id.required = consentBound;
    dataset.elements.consent_evidence_sha256.required = consentBound;

    const model = byId('research-model-form');
    const trained = model.elements.provenance_kind.value === 'trained';
    byId('research-training-receipt-fields').hidden = !trained;
    ['training_receipt_id', 'training_receipt_sha256', 'training_dataset_sha256'].forEach(name => {
      model.elements[name].required = trained;
    });
    model.elements.dataset_lineage.required = trained;
    model.elements.model_lineage.required = trained;
    model.elements.dataset_lineage_purpose.required = trained ||
      model.elements.dataset_lineage.selectedOptions.length > 0;
    const pinned = model.elements.immutable_provider_revision.value.trim() !== '';
    model.elements.observed_from.required = !pinned;
    model.elements.observed_until.required = !pinned;
    model.elements.reproducibility_limitation.required = !pinned;
  }

  byId('research-governed-refresh').addEventListener('click', () => act(refresh));
  byId('research-dataset-form').elements.data_use_decision_choice.addEventListener('change', event => {
    if (!event.currentTarget.value) return;
    const decision = JSON.parse(event.currentTarget.value);
    const form = byId('research-dataset-form');
    form.elements.data_use_decision_id.value = decision.decision_id;
    form.elements.data_use_decision_sha256.value = decision.decision_sha256;
    form.elements.data_use_action.value = decision.action;
    if (!form.elements.dataset_id.value) form.elements.dataset_id.value = decision.asset_id;
    if (!form.elements.allowed_purposes.value) {
      form.elements.allowed_purposes.value = decision.purpose_codes.join(', ');
    }
  });
  byId('research-dataset-form').elements.provenance_kind.addEventListener('change', syncConditionalFields);
  byId('research-dataset-form').elements.consent_record_id.addEventListener('input', syncConditionalFields);
  byId('research-dataset-form').elements.consent_evidence_sha256.addEventListener('input', syncConditionalFields);
  byId('research-model-form').elements.provenance_kind.addEventListener('change', syncConditionalFields);
  byId('research-model-form').elements.dataset_lineage.addEventListener('change', syncConditionalFields);
  byId('research-model-form').elements.immutable_provider_revision.addEventListener('input', syncConditionalFields);
  byId('research-run-form').elements.purpose.addEventListener('input', renderRunSelectors);
  byId('research-run-form').elements.action.addEventListener('change', renderRunSelectors);

  byId('research-dataset-form').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const provenance = form.elements.provenance_kind.value;
    const payload = {context, confirm:true, dataset_id:form.elements.dataset_id.value.trim(),
      version:form.elements.version.value.trim(), source_ref:form.elements.source_ref.value.trim(),
      content_sha256:optional(form, 'content_sha256'), license_id:form.elements.license_id.value.trim(),
      license_evidence_sha256:form.elements.license_evidence_sha256.value.trim(),
      allowed_purposes:commaValues(form.elements.allowed_purposes.value),
      usage_constraints:lineValues(form.elements.usage_constraints.value),
      data_use_action:form.elements.data_use_action.value,
      data_use_decision_id:form.elements.data_use_decision_id.value.trim(),
      data_use_decision_sha256:form.elements.data_use_decision_sha256.value.trim(),
      consent_record_id:optional(form, 'consent_record_id'),
      consent_evidence_sha256:optional(form, 'consent_evidence_sha256'),
      parent_datasets:selectedRefs(form.elements.parent_datasets, 'dataset'),
      provenance_kind:provenance,
      synthetic_generator_id:syntheticValue(form, provenance, 'synthetic_generator_id'),
      synthetic_generator_version:syntheticValue(form, provenance, 'synthetic_generator_version'),
      synthetic_generator_sha256:syntheticValue(form, provenance, 'synthetic_generator_sha256'),
      synthetic_config_sha256:syntheticValue(form, provenance, 'synthetic_config_sha256'),
      synthetic_seed:provenance === 'synthetic' ? Number(form.elements.synthetic_seed.value) : null};
    act(async () => {
      await api('/registry/datasets', 'POST', payload);
      form.reset(); syncConditionalFields();
      message('Immutable dataset version registered as a draft for separate review.');
      await refresh();
    });
  });

  function syntheticValue(form, provenance, name) {
    return provenance === 'synthetic' ? form.elements[name].value.trim() : null;
  }

  byId('research-model-form').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const trained = form.elements.provenance_kind.value === 'trained';
    const revision = optional(form, 'immutable_provider_revision');
    const payload = {context, confirm:true, model_id:form.elements.model_id.value.trim(),
      version:form.elements.version.value.trim(), source_ref:form.elements.source_ref.value.trim(),
      artifact_sha256:form.elements.artifact_sha256.value.trim(), license_id:form.elements.license_id.value.trim(),
      license_evidence_sha256:form.elements.license_evidence_sha256.value.trim(),
      allowed_purposes:commaValues(form.elements.allowed_purposes.value),
      usage_constraints:lineValues(form.elements.usage_constraints.value),
      provider_model_identity:form.elements.provider_model_identity.value.trim(),
      immutable_provider_revision:revision,
      observed_from:revision ? null : numberOrNull(form, 'observed_from'),
      observed_until:revision ? null : numberOrNull(form, 'observed_until'),
      reproducibility_limitation:revision ? null : optional(form, 'reproducibility_limitation'),
      dataset_lineage:selectedRefs(form.elements.dataset_lineage, 'dataset'),
      dataset_lineage_purpose:optional(form, 'dataset_lineage_purpose'),
      model_lineage:selectedRefs(form.elements.model_lineage, 'model'),
      provenance_kind:form.elements.provenance_kind.value,
      training_receipt_id:trained ? optional(form, 'training_receipt_id') : null,
      training_receipt_sha256:trained ? optional(form, 'training_receipt_sha256') : null,
      training_dataset_sha256:trained ? optional(form, 'training_dataset_sha256') : null};
    act(async () => {
      await api('/registry/models', 'POST', payload);
      form.reset(); syncConditionalFields();
      message('Immutable model version registered as a draft for separate review.');
      await refresh();
    });
  });

  byId('research-run-form').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const modelValue = form.elements.model_ref.value;
    const payload = {context, confirm:true, run_id:form.elements.run_id.value.trim(),
      run_version:form.elements.run_version.value.trim(), experiment_id:form.elements.experiment_id.value.trim(),
      experiment_version:form.elements.experiment_version.value.trim(), purpose:form.elements.purpose.value.trim(),
      action:form.elements.action.value, code_revision:form.elements.code_revision.value.trim(),
      code_sha256:form.elements.code_sha256.value.trim(), environment_id:form.elements.environment_id.value.trim(),
      environment_version:form.elements.environment_version.value.trim(),
      environment_sha256:form.elements.environment_sha256.value.trim(), seed:Number(form.elements.seed.value),
      dataset_refs:selectedRefs(form.elements.dataset_refs, 'dataset'),
      model_ref:modelValue ? JSON.parse(modelValue) : null,
      resource_caps:{max_wall_seconds:Number(form.elements.max_wall_seconds.value),
        max_provider_calls:Number(form.elements.max_provider_calls.value),
        max_storage_bytes:Number(form.elements.max_storage_bytes.value), max_cost:form.elements.max_cost.value.trim(),
        currency:form.elements.currency.value.trim()},
      metric_protocol_sha256:form.elements.metric_protocol_sha256.value.trim()};
    act(async () => {
      const planned = await api('/governed-runs', 'POST', payload);
      await api(`/governed-runs/${encodeURIComponent(planned.run_id)}/queue`, 'POST',
        {context, confirm:true, expected_revision:planned.revision});
      form.reset(); form.elements.run_version.value = 'v1'; form.elements.experiment_version.value = 'v1';
      form.elements.max_wall_seconds.value = '300'; form.elements.max_provider_calls.value = '0';
      form.elements.max_storage_bytes.value = '104857600'; form.elements.max_cost.value = '0';
      form.elements.currency.value = 'USD';
      message('Governed run planned and queued after current rights checks.');
      await refresh();
    });
  });

  if (!byId('research-run-form').elements.run_id.value) {
    byId('research-run-form').elements.run_id.value = `run-${crypto.randomUUID()}`;
  }
  syncConditionalFields();
  act(refresh);
})();
