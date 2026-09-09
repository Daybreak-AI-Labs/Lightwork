'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const CONTEXT = 'a'.repeat(64);
const OTHER_CONTEXT = 'b'.repeat(64);
const TENANT = 'tenant-a';
const ACTOR = 'user:admin@example.test';
const script = fs.readFileSync(
  path.resolve(__dirname, '../../maverick_dashboard/static/lightwork-knowledge-access.js'),
  'utf8'
);
const template = fs.readFileSync(
  path.resolve(__dirname, '../../maverick_dashboard/templates/knowledge_access.html'),
  'utf8'
).replace(/{%[\s\S]*?%}/g, '')
  .replaceAll('{{ knowledge_context }}', CONTEXT)
  .replaceAll('{{ knowledge_tenant }}', TENANT)
  .replaceAll('{{ knowledge_actor }}', ACTOR)
  .replaceAll('{{ knowledge_api_base }}', '/api/v1/knowledge-access')
  .replaceAll("{{ 'true' if knowledge_can_review else 'false' }}", 'true');

const tick = () => new Promise(resolve => setImmediate(resolve));
const ref = (asset = 'customer-source', manifest = '1'.repeat(64), kind = 'dataset') => ({
  kind, asset_id: asset, version: 'v1', manifest_sha256: manifest,
  schema: 'lightwork.research-registry-ref.v1'
});

function rights(overrides = {}) {
  return {
    schema: 'lightwork.knowledge-rights-request.v1',
    id: 'knowledge-req-' + '2'.repeat(40), revision: 1,
    request_type: 'deletion', status: 'submitted', source_reference: ref(),
    requested_by: 'user:admin@example.test', reason: 'Customer asked to remove this source.',
    proposed_correction: null, source_use_status: 'unchanged_pending_review',
    raw_record_status: 'pending_source_review', retention_review_status: 'required',
    decision: null, decided_by: null,
    decision_evidence_sha256: null,
    derivatives: [{reference: ref('candidate-model', '3'.repeat(64), 'model'),
      current_state: 'approved', required_action: 'retire_or_retrain_model',
      authority_owner: 'research_registry_lineage', workflow_state: 'pending_source_review'}],
    derivatives_at_request: [{reference: ref('candidate-model', '3'.repeat(64), 'model'),
      current_state: 'approved', required_action: 'retire_or_retrain_model',
      authority_owner: 'research_registry_lineage', workflow_state: 'pending_source_review'}],
    lineage_revalidated_before_return: true,
    weight_influence_notice: 'Deleting or excluding a raw source does not erase its influence from learned weights. Affected models require a documented retirement or retraining assessment.',
    training_permission_notice: 'Access to search, deploy, or export a source does not grant permission to train on it.',
    created_at: 1700000000, updated_at: 1700000001,
    ...overrides
  };
}

function item(overrides = {}) {
  return {
    schema: 'lightwork.knowledge-detail.v1', reference: ref(),
    title: '<script>Customer source</script>',
    summary: 'A governed summary with a very-long-identifier-' + 'x'.repeat(180),
    content_available: true, claim_state: 'verified',
    observed_at: 1700000000, verified_at: 1700000100,
    source: {authority_owner: 'research_registry', source_ref: 'catalog:tenant/customer-source/v1', version: 'v1',
      content_sha256: '4'.repeat(64), provenance_kind: 'source',
      license_id: 'customer-authorized', usage_constraints: ['tenant_only']},
    eligibility: {state: 'eligible', checked_at: 1700000200, purpose: 'product_research',
      permitted_action: 'export', fresh_until: 4102444800, consent_until: null,
      approval_sha256: '5'.repeat(64), requested_action: 'read',
      actor: 'user:admin@example.test', actor_authorization_sha256: '6'.repeat(64)},
    cache: {owner: 'knowledge_access_process_projection', hit: true, content_revalidated_before_return: true},
    correction_history: [],
    training_permission_notice: 'Access to search, deploy, or export a source does not grant permission to train on it.',
    ...overrides
  };
}

function response(payload, status = 200) {
  const body = typeof payload === 'string' ? payload : JSON.stringify(payload);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {get: name => name.toLowerCase() === 'content-length' ? String(body.length) : null},
    text: async () => body
  };
}

async function setup(queue) {
  const dom = new JSDOM(template, {url: 'https://lightwork.test/knowledge-access', runScripts: 'outside-only'});
  const window = dom.window;
  window.TextEncoder = global.TextEncoder;
  const calls = [];
  let downloads = 0;
  Object.defineProperty(window, 'crypto', {value: {
    randomUUID: () => '11111111-2222-4333-8444-555555555555',
    subtle: {digest: async () => new Uint8Array(32).fill(1).buffer}
  }});
  window.URL.createObjectURL = () => 'blob:knowledge-export';
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { downloads += 1; };
  window.fetch = async (url, options = {}) => {
    calls.push({url: String(url), options});
    const next = queue.shift();
    if (next instanceof Error) { throw next; }
    if (typeof next === 'function') { return next(url, options); }
    return response(next);
  };
  window.eval(script);
  await tick();
  return {dom, window, document: window.document, calls, downloads: () => downloads};
}

function searchPayload(records = [item()]) {
  return {context: CONTEXT, schema: 'lightwork.knowledge-search.v1', query: '',
    tenant: TENANT, actor: ACTOR, actor_authorization_sha256: '6'.repeat(64),
    purpose: 'product_research', items: records, count: records.length, matched: records.length,
    omitted_ineligible: 0, truncated: false, authority_checks: 'Current checks passed.',
    training_permission_notice: 'Access does not grant training permission.'};
}

function requestList(records = []) {
  return {context: CONTEXT, schema: 'lightwork.knowledge-rights-requests.v1',
    items: records, count: records.length, can_review: true};
}

function exportPayload(record = item()) {
  const exported = item({...record, eligibility: {...record.eligibility,
    requested_action: 'export', actor_authorization_sha256: '7'.repeat(64)}});
  return {context: CONTEXT, schema: 'lightwork.knowledge-export.v1', tenant: TENANT,
    actor: ACTOR, actor_authorization_sha256: '7'.repeat(64),
    purpose: 'product_research', items: [exported], count: 1, complete: true,
    authority_evidence: {schema: 'lightwork.knowledge-export-authority-evidence.v1',
      tenant: 'tenant-a', actor: 'user:admin@example.test',
      actor_authorization_sha256: '7'.repeat(64), purpose: 'product_research', action: 'export',
      sources: [{reference: exported.reference, content_sha256: exported.source.content_sha256,
        approval_sha256: exported.eligibility.approval_sha256}], binding_sha256: '01'.repeat(32)},
    qualification: {local_control_validation: 'passed_at_request_time',
      external_store_and_customer_qualification: 'qualification_pending'},
    training_permission_notice: 'Separate permission.'};
}

test('search and detail render untrusted source text without HTML and expose an accessible control', async () => {
  const ui = await setup([requestList(), searchPayload(), {context: CONTEXT, ...item()}]);
  ui.document.getElementById('ka-search-form').dispatchEvent(
    new ui.window.Event('submit', {bubbles: true, cancelable: true})
  );
  await tick();
  assert.equal(ui.document.querySelector('#ka-results script'), null);
  assert.match(ui.document.getElementById('ka-results').textContent, /<script>Customer source<\/script>/);
  const button = ui.document.querySelector('#ka-results button');
  assert.equal(button.getAttribute('aria-label'), 'View details for <script>Customer source</script>');
  button.click();
  await tick();
  assert.equal(ui.document.querySelector('#ka-detail script'), null);
  assert.match(ui.document.getElementById('ka-detail').textContent, /revalidated cache hit/);
  assert.match(ui.document.getElementById('ka-detail').textContent, /Temporary in-app view/);
  assert.match(ui.document.getElementById('ka-detail').textContent, /does not grant permission to train/);
  assert.equal(ui.document.getElementById('ka-submit-request').disabled, false);
  assert.equal(ui.document.activeElement, ui.document.getElementById('ka-detail-heading'));
  ui.dom.window.close();
});

test('cross-context detail clears prior results, identifiers, query, and prepared request state', async () => {
  const ui = await setup([requestList(), searchPayload(), {context: OTHER_CONTEXT, ...item()}]);
  ui.document.getElementById('ka-query').value = 'private customer phrase';
  ui.document.getElementById('ka-search-form').dispatchEvent(
    new ui.window.Event('submit', {bubbles: true, cancelable: true})
  );
  await tick();
  ui.document.querySelector('#ka-results button').click();
  await tick();
  assert.equal(ui.document.getElementById('ka-results').textContent, '');
  assert.equal(ui.document.getElementById('ka-detail-panel').hidden, true);
  assert.equal(ui.document.getElementById('ka-query').value, '');
  assert.equal(ui.document.getElementById('ka-submit-request').disabled, true);
  assert.match(ui.document.getElementById('ka-status').textContent, /account or workspace changed/i);
  ui.dom.window.close();
});

test('search withholds content when cache, purpose, or actor authority crosses the request', async () => {
  const variants = [
    item({cache: {...item().cache, content_revalidated_before_return: false}}),
    item({eligibility: {...item().eligibility, purpose: 'different_purpose'}}),
    item({eligibility: {...item().eligibility, actor: 'user:other-user'}})
  ];
  for (const record of variants) {
    const ui = await setup([requestList(), searchPayload([record])]);
    ui.document.getElementById('ka-search-form').dispatchEvent(
      new ui.window.Event('submit', {bubbles: true, cancelable: true})
    );
    await tick();
    assert.equal(ui.document.getElementById('ka-results').textContent, '');
    assert.equal(ui.document.getElementById('ka-export').disabled, true);
    assert.match(ui.document.getElementById('ka-status').textContent, /failed its source eligibility contract/i);
    ui.dom.window.close();
  }
});

test('search rejects a response bound to another requested purpose or actor', async () => {
  const wrongPurpose = searchPayload();
  wrongPurpose.purpose = 'different_purpose';
  const wrongActor = searchPayload();
  wrongActor.actor = 'user:other-user';
  for (const payload of [wrongPurpose, wrongActor]) {
    const ui = await setup([requestList(), payload]);
    ui.document.getElementById('ka-search-form').dispatchEvent(
      new ui.window.Event('submit', {bubbles: true, cancelable: true})
    );
    await tick();
    assert.equal(ui.document.getElementById('ka-results').textContent, '');
    assert.equal(ui.document.getElementById('ka-export').disabled, true);
    assert.match(ui.document.getElementById('ka-status').textContent, /search response failed validation/i);
    ui.dom.window.close();
  }
});

test('export refuses a same-context response for a different exact source version', async () => {
  const foreign = item({reference: ref('foreign-source', '9'.repeat(64)),
    title: 'Foreign source', summary: 'Other tenant summary',
    source: {...item().source, source_ref: 'catalog:other/foreign-source/v1'},
    eligibility: {...item().eligibility, requested_action: 'export',
      actor_authorization_sha256: '7'.repeat(64)}});
  const ui = await setup([requestList(), searchPayload(), {
    context: CONTEXT, schema: 'lightwork.knowledge-export.v1', tenant: TENANT,
    actor: ACTOR, actor_authorization_sha256: '7'.repeat(64),
    purpose: 'product_research', items: [foreign], count: 1, complete: true,
    training_permission_notice: 'Separate permission.'
  }]);
  ui.document.getElementById('ka-search-form').dispatchEvent(
    new ui.window.Event('submit', {bubbles: true, cancelable: true})
  );
  await tick();
  ui.document.getElementById('ka-export').click();
  await tick();
  assert.equal(ui.downloads(), 0);
  assert.equal(ui.document.getElementById('ka-results').textContent, '');
  assert.equal(ui.document.getElementById('ka-detail-panel').hidden, true);
  assert.equal(ui.document.getElementById('ka-submit-request').disabled, true);
  assert.match(ui.document.getElementById('ka-status').textContent, /did not match the selected source versions/);
  assert.doesNotMatch(ui.document.getElementById('ka-status').textContent, /Other tenant summary/);
  ui.dom.window.close();
});

test('export downloads only exact action-bound evidence and clears on malformed evidence', async () => {
  const valid = await setup([requestList(), searchPayload(), exportPayload()]);
  valid.document.getElementById('ka-search-form').dispatchEvent(
    new valid.window.Event('submit', {bubbles: true, cancelable: true})
  );
  await tick();
  valid.document.getElementById('ka-export').click();
  await tick();
  assert.equal(valid.downloads(), 1);
  assert.match(valid.document.getElementById('ka-status').textContent, /Exported 1/);
  valid.dom.window.close();

  const malformed = exportPayload();
  malformed.authority_evidence.binding_sha256 = 'not-a-digest';
  const denied = await setup([requestList(), searchPayload(), malformed]);
  denied.document.getElementById('ka-search-form').dispatchEvent(
    new denied.window.Event('submit', {bubbles: true, cancelable: true})
  );
  await tick();
  denied.document.getElementById('ka-export').click();
  await tick();
  assert.equal(denied.downloads(), 0);
  assert.equal(denied.document.getElementById('ka-results').textContent, '');
  assert.match(denied.document.getElementById('ka-status').textContent, /authority evidence was malformed/);
  denied.dom.window.close();
});

test('export refuses a well-formed digest that does not bind its authority evidence', async () => {
  const tampered = exportPayload();
  tampered.authority_evidence.binding_sha256 = '8'.repeat(64);
  const ui = await setup([requestList(), searchPayload(), tampered]);
  ui.document.getElementById('ka-search-form').dispatchEvent(
    new ui.window.Event('submit', {bubbles: true, cancelable: true})
  );
  await tick();
  ui.document.getElementById('ka-export').click();
  await tick();
  assert.equal(ui.downloads(), 0);
  assert.equal(ui.document.getElementById('ka-results').textContent, '');
  assert.match(ui.document.getElementById('ka-status').textContent, /binding did not match/i);
  ui.dom.window.close();
});

test('failed request preserves inputs and exact retry key, then refreshes status after success', async () => {
  const created = rights({request_type: 'correction', proposed_correction: 'Corrected text.'});
  const ui = await setup([
    requestList(), searchPayload(), {context: CONTEXT, ...item()},
    new Error('Connection interrupted'), {context: CONTEXT, ...created}, requestList([created])
  ]);
  ui.document.getElementById('ka-search-form').dispatchEvent(
    new ui.window.Event('submit', {bubbles: true, cancelable: true})
  );
  await tick();
  ui.document.querySelector('#ka-results button').click();
  await tick();
  const form = ui.document.getElementById('ka-request-form');
  form.elements.request_type.value = 'correction';
  form.elements.request_type.dispatchEvent(new ui.window.Event('change'));
  form.elements.reason.value = 'The customer found an error.';
  form.elements.proposed_correction.value = 'Corrected text.';
  form.elements.confirm.checked = true;
  form.dispatchEvent(new ui.window.Event('submit', {bubbles: true, cancelable: true}));
  await tick();
  const retryKey = form.dataset.requestKey;
  assert.equal(form.elements.reason.value, 'The customer found an error.');
  assert.match(ui.document.getElementById('ka-status').textContent, /inputs are still here/);
  form.dispatchEvent(new ui.window.Event('submit', {bubbles: true, cancelable: true}));
  await tick(); await tick();
  const first = JSON.parse(ui.calls[3].options.body);
  const second = JSON.parse(ui.calls[4].options.body);
  assert.equal(first.request_key, retryKey);
  assert.equal(second.request_key, retryKey);
  assert.match(ui.document.getElementById('ka-requests').textContent, /Corrected text/);
  assert.equal(form.dataset.requestKey, undefined);
  ui.dom.window.close();
});

test('review shows derivative retirement, hashes plain-language rationale, and keeps deletion limits honest', async () => {
  const submitted = rights();
  const approved = rights({status: 'approved', revision: 3, decision: 'approve',
    decided_by: 'user:admin@example.test', decision_evidence_sha256: '0'.repeat(64),
    source_use_status: 'revoked_for_new_use', raw_record_status: 'external_source_deletion_unverified',
    retention_review_status: 'external_retention_and_deletion_unverified',
    derivatives: [{reference: ref('candidate-model', '3'.repeat(64), 'model'), current_state: 'approved',
      required_action: 'retire_or_retrain_model', authority_owner: 'research_registry_lineage',
      workflow_state: 'retire_or_retrain_model_required'}]});
  const ui = await setup([requestList([submitted]), {context: CONTEXT, ...approved}, requestList([approved])]);
  const card = ui.document.querySelector('.ka-request');
  assert.match(card.textContent, /retire or retrain model/);
  assert.match(card.textContent, /does not erase its influence from learned weights/);
  card.querySelector('textarea').value = 'I checked the exact customer request and derivatives.';
  card.querySelector('input[type="checkbox"]').checked = true;
  [...card.querySelectorAll('button')].find(button => /Approve/.test(button.textContent)).click();
  await tick(); await tick();
  const body = JSON.parse(ui.calls[1].options.body);
  assert.equal(body.evidence_sha256, '01'.repeat(32));
  assert.equal(body.confirm, true);
  assert.match(ui.document.getElementById('ka-requests').textContent, /external source deletion unverified/);
  assert.match(ui.document.getElementById('ka-requests').textContent, /retire or retrain model required/);
  ui.dom.window.close();
});

test('an interrupted approval exposes an exact in-app recovery action', async () => {
  const applying = rights({status: 'applying', revision: 2, decision: 'approve',
    decided_by: 'user:admin@example.test', decision_evidence_sha256: '01'.repeat(32)});
  const approved = rights({...applying, status: 'approved', revision: 3,
    source_use_status: 'revoked_for_new_use', raw_record_status: 'source_version_retired'});
  const ui = await setup([requestList([applying]), {context: CONTEXT, ...approved},
    requestList([approved])]);
  const card = ui.document.querySelector('.ka-request');
  assert.match(card.textContent, /Recovery is waiting/);
  assert.equal([...card.querySelectorAll('button')].some(button => /Deny/.test(button.textContent)), false);
  card.querySelector('textarea').value = 'Exact prior decision rationale';
  card.querySelector('input[type="checkbox"]').checked = true;
  card.querySelector('button').click();
  await tick(); await tick();
  const body = JSON.parse(ui.calls[1].options.body);
  assert.equal(body.expected_revision, 2);
  assert.equal(body.decision, 'approve');
  assert.equal(body.evidence_sha256, '01'.repeat(32));
  assert.match(ui.document.getElementById('ka-requests').textContent, /approved/);
  ui.dom.window.close();
});

test('an approved correction shows its exact approved replacement without a pending state', async () => {
  const corrected = rights({
    request_type: 'correction', status: 'approved', revision: 4,
    proposed_correction: 'Controller approval is required before release.',
    source_use_status: 'prior_version_revoked_replacement_approved',
    raw_record_status: 'prior_source_version_retired',
    retention_review_status: 'corrected_replacement_approved_raw_prior_retained',
    replacement_reference: ref('customer-source', '8'.repeat(64)),
    replacement_status: 'approved', decision: 'approve',
    decided_by: 'user:independent-reviewer', decision_evidence_sha256: '9'.repeat(64)
  });
  const ui = await setup([requestList([corrected])]);
  const rendered = ui.document.getElementById('ka-requests').textContent;
  assert.match(rendered, /Corrected replacement: customer-source/);
  assert.match(rendered, /approved/);
  assert.doesNotMatch(rendered, /replacement pending/i);
  ui.dom.window.close();
});

test('retention hold identity, basis, expiry, and release are operable in the app', async () => {
  const submitted = rights();
  const hold = {
    schema: 'lightwork.knowledge-retention-hold.v1', hold_id: 'matter-2026-8',
    basis: 'Preserve for the open legal matter.', evidence_sha256: '8'.repeat(64),
    placed_by: ACTOR, placed_at: 1700000100, expires_at: 4102444800,
    status: 'active', effective_status: 'active', released_by: null, released_at: null,
    release_reason: null, release_evidence_sha256: null
  };
  const held = rights({revision: 2, retention_hold: hold,
    retention_review_status: 'retention_hold_active'});
  const released = rights({revision: 3, retention_hold: {...hold, status: 'released',
    effective_status: 'released', released_by: ACTOR, released_at: 1700000200,
    release_reason: 'The preservation duty ended.', release_evidence_sha256: '9'.repeat(64)},
    retention_review_status: 'retention_hold_released'});
  const ui = await setup([
    requestList([submitted]), {context: CONTEXT, ...held}, requestList([held]),
    {context: CONTEXT, ...released}, requestList([released])
  ]);
  let holdBox = [...ui.document.querySelectorAll('.ka-review')].find(box =>
    /Preservation hold/.test(box.textContent)
  );
  const inputs = holdBox.querySelectorAll('input');
  inputs[0].value = 'matter-2026-8';
  holdBox.querySelector('textarea').value = 'Preserve for the open legal matter.';
  inputs[1].value = '2099-01-01T00:00';
  inputs[2].checked = true;
  [...holdBox.querySelectorAll('button')].find(button => /Place retention/.test(button.textContent)).click();
  await tick(); await tick();
  let body = JSON.parse(ui.calls[1].options.body);
  assert.equal(body.hold_id, 'matter-2026-8');
  assert.equal(body.basis, 'Preserve for the open legal matter.');
  assert.equal(body.confirm, true);
  assert.match(ui.document.getElementById('ka-requests').textContent, /matter-2026-8/);

  holdBox = [...ui.document.querySelectorAll('.ka-review')].find(box =>
    /Preservation hold/.test(box.textContent)
  );
  holdBox.querySelector('textarea').value = 'The preservation duty ended.';
  holdBox.querySelector('input[type="checkbox"]').checked = true;
  [...holdBox.querySelectorAll('button')].find(button => /Release retention/.test(button.textContent)).click();
  await tick(); await tick();
  body = JSON.parse(ui.calls[3].options.body);
  assert.equal(body.reason, 'The preservation duty ended.');
  assert.equal(body.expected_revision, 2);
  assert.match(ui.document.getElementById('ka-requests').textContent, /released/);
  ui.dom.window.close();
});

test('a tampered active hold carrying release material fails closed', async () => {
  const tampered = rights({retention_hold: {
    schema: 'lightwork.knowledge-retention-hold.v1', hold_id: 'matter-tampered',
    basis: 'Synthetic malformed hold.', evidence_sha256: '8'.repeat(64),
    placed_by: ACTOR, placed_at: 1700000100, expires_at: 4102444800,
    status: 'active', effective_status: 'active', released_by: 'user:attacker',
    released_at: 1700000200, release_reason: 'Forged release',
    release_evidence_sha256: '9'.repeat(64)
  }});
  const ui = await setup([requestList([tampered])]);
  assert.match(ui.document.getElementById('ka-requests').textContent, /unexpected hold release material/i);
  assert.doesNotMatch(ui.document.getElementById('ka-requests').textContent, /matter-tampered/);
  ui.dom.window.close();
});
