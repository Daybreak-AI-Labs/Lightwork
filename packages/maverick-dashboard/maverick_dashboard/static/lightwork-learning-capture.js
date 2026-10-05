// Flow run page: "Keep this run as a learning example". Enrolls the run at
// its first approval with plain, editable descriptions of its data, then
// lets the owner review the finished example and allow learning from it.
// Every rule (owner, workspace, pause, retention, review before use) is
// checked by the server; this script only guides and shows results.
(() => {
  'use strict';
  const card = document.getElementById('lx-card');
  if (!card) return;
  const enc = encodeURIComponent;
  const base = '/api/v1/flows/' + enc(card.dataset.flowId || '') + '/runs/' + enc(card.dataset.runId || '') + '/episode-capture';
  const $ = id => document.getElementById('lx-' + id);
  const KINDS = { string: 'Text', integer: 'Whole number', number: 'Number', boolean: 'Yes or no', null: 'Nothing',
    object: 'Group of details', array: 'List' };
  const DAY = 86400;
  let prepared = null, model = null, record = null, busy = false;

  function make(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function human(value) { return typeof window.mvHuman === 'function' ? window.mvHuman(String(value)) : String(value).replace(/[_.-]+/g, ' '); }
  function say(text) { $('status').textContent = text; }
  async function api(path, body) {
    return window.mvFetchJSON(base + path, {method: body === undefined ? 'GET' : 'POST', cache: 'no-store', body: body});
}
  function isoDay(epoch) { const d = new Date(epoch * 1000); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function endOfDay(value) { const t = Date.parse(value + 'T23:59:00'); return Number.isFinite(t) ? t / 1000 : NaN; }
  function show(section) { ['enroll', 'review', 'grant'].forEach(name => { $(name).hidden = name !== section; }); $('next').hidden = true; }
  function clearCurrentState() {
    prepared = null; model = null; record = null; show(null);
    $('describe').replaceChildren(); $('evidence').replaceChildren();
    $('review-reason').value = ''; $('grant-reason').value = ''; $('grant-copies').checked = false;
  }

  // ---- plain editor for a data description ------------------------------------------
  function blank(kind) {
    if (kind === 'string') return { type: 'string', maxLength: 1024 };
    if (kind === 'integer' || kind === 'number') return { type: kind, minimum: -1000000000000, maximum: 1000000000000 };
    if (kind === 'object') return { type: 'object', properties: {}, required: [], additionalProperties: false };
    if (kind === 'array') return { type: 'array', items: { type: 'string', maxLength: 1024 }, minItems: 1, maxItems: 1 };
    return { type: kind };
  }
  function numberField(label, value, onChange, extra) {
    const wrap = make('label', 'mv-form__field', label);
    const input = make('input', 'input');
    input.type = 'number';
    Object.assign(input, extra || {});
    input.value = value;
    input.addEventListener('change', () => onChange(Number(input.value)));
    wrap.append(input);
    return wrap;
  }
  function editor(schema, label, replace, removable) {
    const box = make(schema.type === 'object' ? 'fieldset' : 'div', 'lx-field');
    const head = make(schema.type === 'object' ? 'legend' : 'div', 'lx-field__head');
    const kind = make('select', 'select');
    kind.setAttribute('aria-label', 'Kind of value for ' + label);
    Object.keys(KINDS).forEach(k => { const o = make('option', null, KINDS[k]); o.value = k; kind.append(o); });
    kind.value = schema.type;
    kind.addEventListener('change', () => { replace(blank(kind.value)); render(); });
    head.append(make('strong', null, label + ' '), kind);
    if (removable) {
      const remove = make('button', 'btn btn--ghost', 'Remove'); remove.type = 'button';
      remove.setAttribute('aria-label', 'Remove ' + label);
      remove.addEventListener('click', () => { removable(); render(); });
      head.append(remove);
    }
    box.append(head);
    if (schema.type === 'string') {
      box.append(numberField('Longest text (characters)', schema.maxLength, v => { schema.maxLength = Math.min(1024, Math.max(1, v || 1)); }, { min: 1, max: 1024 }));
    } else if (schema.type === 'integer' || schema.type === 'number') {
      box.append(numberField('Smallest', schema.minimum, v => { schema.minimum = v; }),
        numberField('Largest', schema.maximum, v => { schema.maximum = v; }));
    } else if (schema.type === 'array') {
      box.append(numberField('Exactly how many items', schema.minItems, v => { schema.minItems = schema.maxItems = Math.min(64, Math.max(0, v || 0)); }, { min: 0, max: 64 }));
      box.append(editor(schema.items, 'Each item', next => { schema.items = next; }));
    } else if (schema.type === 'object') {
      Object.keys(schema.properties).forEach(key => {
        box.append(editor(schema.properties[key], human(key), next => { schema.properties[key] = next; }, () => {
          delete schema.properties[key];
          schema.required = schema.required.filter(k => k !== key);
        }));
      });
      const addRow = make('div', 'fr__actions');
      const name = make('input', 'input');
      name.type = 'text'; name.maxLength = 200;
      name.setAttribute('aria-label', 'Name of a new detail in ' + label + ', exactly as it appears');
      name.placeholder = 'Name of a detail, exactly as it appears';
      const add = make('button', 'btn btn--ghost', 'Add a detail'); add.type = 'button';
      add.addEventListener('click', () => {
        const key = name.value.trim();
        if (!key || Object.prototype.hasOwnProperty.call(schema.properties, key)) return;
        schema.properties[key] = blank('string');
        schema.required.push(key);
        render();
      });
      addRow.append(name, add);
      box.append(addRow);
    }
    return box;
  }
  function render() {
    const host = $('describe');
    host.replaceChildren();
    host.append(editor(model.input_schema, 'What the run starts with', next => { model.input_schema = next; }));
    Object.keys(model.tool_data_schemas).forEach(tool => {
      const pair = model.tool_data_schemas[tool];
      host.append(editor(pair.arguments, 'What “' + human(tool) + '” is sent', next => { pair.arguments = next; }));
      if ((model.unknown_results || []).includes(tool) && !Object.keys(pair.result.properties || {}).length) {
        host.append(make('p', 'fr__msg', 'Lightwork hasn’t seen what “' + human(tool) + '” gives back yet. Add each detail it returns.'));
      }
      host.append(editor(pair.result, 'What “' + human(tool) + '” gives back', next => { pair.result = next; }));
    });
  }

  // ---- showing a recorded example ---------------------------------------------------------
  function rows(value, prefix, out) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      Object.keys(value).forEach(k => rows(value[k], (prefix ? prefix + ' › ' : '') + human(k), out));
    } else if (Array.isArray(value)) {
      value.forEach((item, i) => rows(item, prefix + ' ' + (i + 1), out));
    } else {
      out.push([prefix || 'Value', value === true ? 'Yes' : value === false ? 'No' : value === null ? 'Nothing' : String(value)]);
    }
    return out;
  }
  function table(caption, value) {
    const wrap = make('div');
    wrap.append(make('h3', null, caption));
    const list = make('dl');
    rows(value, '', []).slice(0, 200).forEach(([k, v]) => list.append(make('dt', null, k), make('dd', null, v)));
    wrap.append(list);
    return wrap;
  }
  function evidence(detail) {
    const host = $('evidence');
    host.replaceChildren();
    const ev = detail.evidence || {};
    host.append(table('What the run started with', ev.inputs || {}));
    (ev.slots || []).forEach((slot, i) => {
      host.append(table('Action ' + (i + 1) + ': ' + human(slot.tool) + ', what it was sent', slot.arguments || {}));
      host.append(table('Action ' + (i + 1) + ': what it gave back', slot.result || {}));
    });
  }

  function status(row) {
    record = row;
    const kind = row.status;
    if (kind === 'audit_pending') { show(null); say('Waiting for the activity log to confirm the last change. Refresh the page in a moment.'); return; }
    if (['enrolled', 'capturing', 'paused'].includes(kind)) {
      show(null); say('This run is being kept as a learning example. Approve its actions as usual; review it here when it finishes.'); return;
    }
    if (kind === 'awaiting_review') { show('review'); say('The run finished. Review what it did before it can be used for learning.'); return; }
    if (kind === 'accepted' && row.training_available === true) {
      show(null); $('next').hidden = false; say('Accepted, and new flows may learn from it.'); return;
    }
    if (kind === 'accepted' && row.grant_sha256) {
      show(null); say('Permission to learn from this example has ended. Keep a fresh run as a new example if this work still needs to be taught.');
      return;
    }
    if (kind === 'accepted') {
      show('grant');
      const until = row.retain_until;
      $('grant-until').max = isoDay(until - 60);
      $('grant-until').min = isoDay(Date.now() / 1000);
      $('grant-until').value = isoDay(until - 60);
      say('Accepted as a learning example. Allow learning from it when you’re ready.');
      return;
    }
    show(null);
    say({ rejected: 'You chose not to use this run as an example.', withdrawn: 'This example is no longer used for learning.',
      expired: 'This example has expired.', unusable: 'This run didn’t produce a usable example.' }[kind] || 'This run isn’t a learning example.');
  }

  async function run(action) {
    if (busy) return;
    busy = true; card.setAttribute('aria-busy', 'true'); $('refresh').disabled = true;
    try { await action(); } catch (failure) {
      clearCurrentState();
      say(failure && (failure.status === 401 || failure.status === 403)
        ? 'This learning example is not available to your account right now. Check your access or learning settings. Your unfinished review was cleared.'
        : 'We could not confirm this run’s current learning status. Check current status before trying again. Your unfinished review was cleared.');
      $('status').focus();
    } finally { busy = false; card.setAttribute('aria-busy', 'false'); $('refresh').disabled = false; }
  }
  async function load() {
    clearCurrentState(); say('Checking this run’s learning status…');
    let row = null;
    try { row = await api(''); } catch (failure) {
      if (!(failure && failure.status === 404)) throw failure;
    }
    if (row) {
      status(row);
      if (row.status === 'awaiting_review') evidence(await api('/detail'));
      return;
    }
    try { prepared = await api('/suggestion'); } catch (failure) {
      if (failure && (failure.status === 404 || failure.status === 409)) {
        say('This run can’t become a learning example now. Examples start at the flow’s first approval, before any action runs, and example collection must be on.');
        return;
      }
      throw failure;
    }
    model = prepared.suggestion;
    const now = Date.now() / 1000;
    $('until').min = isoDay(now);
    $('until').max = isoDay(now + (prepared.max_days || 7) * DAY - 60);
    $('until').value = $('until').max;
    render();
    show('enroll');
    say('This run is waiting at its first approval, so it can be kept as a learning example.');
  }

  $('enroll-go').addEventListener('click', () => run(async () => {
    if (!prepared || !model) return;
    const now = Date.now() / 1000;
    const until = Math.min(endOfDay($('until').value), now + (prepared.max_days || 7) * DAY - 60);
    if (!(until > now)) { say('Choose a date from today up to a week ahead.'); return; }
    const ok = typeof window.mvConfirm === 'function' && await window.mvConfirm(
      'Keep this run’s details as a learning example until ' + (window.mvDateAbsolute ? window.mvDateAbsolute(until) : new Date(until * 1000).toLocaleDateString()) + '? It’s encrypted and only you can review it.',
      { okText: 'Keep as example' });
    if (!ok) return;
    const row = await api('', { expected_pause_binding: prepared.expected_pause_binding, allowed_tools: prepared.allowed_tools,
      input_schema: model.input_schema, tool_data_schemas: model.tool_data_schemas, retain_until: until });
    status(row);
  }));
  function review(accepted) {
    return run(async () => {
      if (!record) return;
      const reason = $('review-reason').value.trim();
      if (!reason) { say('Say briefly why, then choose again.'); $('review-reason').focus(); return; }
      const row = await api('/review', { expected_revision: record.revision, expected_episode_sha256: record.episode_sha256,
        reviewed: true, accepted: accepted, rationale: reason });
      status(row);
    });
  }
  $('accept').addEventListener('click', () => review(true));
  $('reject').addEventListener('click', () => review(false));
  $('grant-go').addEventListener('click', () => run(async () => {
    if (!record) return;
    const reason = $('grant-reason').value.trim();
    if (!reason) { say('Say briefly why it may be used for learning.'); $('grant-reason').focus(); return; }
    if (!$('grant-copies').checked) { say('Tick the box to keep the copies made while learning.'); return; }
    const until = Math.min(endOfDay($('grant-until').value), record.retain_until - 60);
    if (!(until > Date.now() / 1000)) { say('Choose a date before the example expires.'); return; }
    const ok = typeof window.mvConfirm === 'function' && await window.mvConfirm(
      'Allow new flows to learn from this example until ' + (window.mvDateAbsolute ? window.mvDateAbsolute(until) : new Date(until * 1000).toLocaleDateString()) + '? Copies made while learning are kept until then, even if you withdraw it later.',
      { okText: 'Allow learning' });
    if (!ok) return;
    const row = await api('/training-grant', { expected_revision: record.revision, expected_episode_sha256: record.episode_sha256,
      use_until: until, retain_derived_copies: true, rationale: reason });
    status(row);
  }));
  $('refresh').addEventListener('click', () => run(load));
  run(load);
})();
