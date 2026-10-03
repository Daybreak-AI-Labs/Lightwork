// "Teach this flow": teach an AI step from examples, build a flow from reviewed
// runs, and learn from signed examples. Every learning call is checked on the
// server; this page only collects choices, shows plain results and saves the
// resulting draft through the normal flow save when the person asks.
(() => {
  'use strict';
  const root = document.getElementById('flow-teach');
  if (!root) return;
  const flowId = root.dataset.flow || '';
  const enc = encodeURIComponent(flowId);
  const $ = id => document.getElementById(id);
  const KIND = { integer: 'Whole number', string: 'Text', boolean: 'Yes or no' };
  const STATUS = { accepted: 'Reviewed and accepted', awaiting_review: 'Waiting for review', rejected: 'Not accepted',
    withdrawn: 'No longer used', expired: 'Expired', none: 'Not collected as an example' };
  const state = { overview: null, flow: null, artifact: null, recipe: null, pkg: null, bundles: [], trust: null,
    question: null, draft: null, draftMode: null, busy: false };

  function make(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function say(text, id) { const node = $(id || 'ft-status'); if (node) node.textContent = text; }
  function human(value) { return typeof window.mvHuman === 'function' ? window.mvHuman(value) : String(value).replace(/[_.-]+/g, ' '); }
  function failText(failure, what) {
    return typeof window.mvErrorText === 'function' ? window.mvErrorText(failure, what) : 'Could not ' + what + '. Please try again.';
  }
  async function api(path, body, method) {
    const response = await fetch(path, {
      method: method || (body === undefined ? 'GET' : 'POST'), credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (!response.ok) throw response;
    return response.json();
  }
  function syncNextActions() {
    const readyRecipe = Boolean(state.recipe) && !state.busy;
    const readyPackage = Boolean(state.pkg) && !state.busy;
    const signedReady = root.dataset.lineage === '1';
    if ($('rc-more')) $('rc-more').disabled = !readyRecipe;
    if ($('rc-draft')) $('rc-draft').disabled = !readyRecipe;
    $('sg-refine').disabled = !readyPackage || !signedReady;
    $('sg-inspect').disabled = !readyPackage;
    $('sg-download').disabled = !readyPackage;
    $('sg-approve').disabled = !readyPackage || !signedReady;
    $('ft-save-go').disabled = !state.draft || state.busy;
    $('ft-save-cancel').disabled = state.busy;
  }
  async function guarded(what, action, statusId) {
    if (state.busy) { say('Still finishing the last step. Please try again in a moment.', statusId); return; }
    state.busy = true;
    root.setAttribute('aria-busy', 'true');
    syncNextActions();
    try { await action(); } catch (failure) { say(failText(failure, what), statusId); }
    finally { state.busy = false; root.setAttribute('aria-busy', 'false'); syncNextActions(); }
  }
  function readJsonFile(file) {
    return new Promise((resolve, reject) => {
      if (!file || file.size > 8 * 1024 * 1024) { reject(new Error('too large')); return; }
      const reader = new FileReader();
      reader.onload = () => { try { resolve(JSON.parse(String(reader.result || ''))); } catch (e) { reject(e); } };
      reader.onerror = () => reject(reader.error);
      reader.readAsText(file);
    });
  }
  function download(name, value) {
    const blob = new Blob([JSON.stringify(value)], { type: 'application/json' });
    const link = make('a');
    link.href = URL.createObjectURL(blob); link.download = name;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  // ---- saving drafts through the normal flow save --------------------------------
  function offerSave(draft, note, mode) {
    state.draft = draft; state.draftMode = mode;
    $('ft-save-note').textContent = note;
    $('ft-save-go').textContent = mode === 'new' ? 'Save as a new flow' : 'Save as the flow’s next version';
    $('ft-save').hidden = false;
    syncNextActions();
    if (!state.busy) $('ft-save-go').focus();
  }
  $('ft-save-cancel').addEventListener('click', () => { state.draft = null; $('ft-save').hidden = true; syncNextActions(); say('Nothing was saved.'); });
  $('ft-save-go').addEventListener('click', () => guarded('save the flow', async () => {
    if (!state.draft) return;
    const ok = typeof window.mvConfirm === 'function' && await window.mvConfirm(state.draftMode === 'new'
      ? 'Save this as a new flow? It is not published or run until you choose to.'
      : 'Save this change as the flow’s next version? The published version keeps running until you publish again.',
      { okText: 'Save' });
    if (!ok) return;
    const saved = await api('/api/v1/flows', { flow: state.draft });
    $('ft-save').hidden = true;
    state.draft = null;
    const link = make('a', null, 'Open it in the flow designer');
    link.href = '/flows/designer/' + encodeURIComponent(saved.id);
    $('ft-status').replaceChildren('Saved (version ' + saved.version + '). Publishing stays a separate step. ', link);
    if (state.draftMode !== 'new') await loadOverview();
  }));

  // ---- rules for a step ------------------------------------------------------------
  function inputRow(decl) {
    const row = make('div');
    const n = $('tr-inputs').children.length;
    const field = (label, control) => { const l = make('label', 'ft__cell', label); l.append(control); return l; };
    const name = make('input', 'input'); name.type = 'text'; name.maxLength = 64; name.value = decl.name || ('input_' + (n + 1));
    name.dataset.role = 'name';
    const kind = make('select', 'select'); kind.dataset.role = 'kind';
    Object.keys(KIND).forEach(k => { const o = make('option', null, KIND[k]); o.value = k; kind.append(o); });
    kind.value = decl.kind || 'string';
    const min = make('input', 'input'); min.type = 'number'; min.step = '1'; min.value = decl.min != null ? decl.min : 0; min.dataset.role = 'min';
    const max = make('input', 'input'); max.type = 'number'; max.step = '1'; max.value = decl.max != null ? decl.max : 100; max.dataset.role = 'max';
    const len = make('input', 'input'); len.type = 'number'; len.min = '1'; len.max = '1024'; len.value = decl.max_length || 200; len.dataset.role = 'len';
    const minL = field('Smallest', min), maxL = field('Largest', max), lenL = field('Longest text', len);
    const remove = make('button', 'btn btn--ghost', 'Remove'); remove.type = 'button';
    remove.setAttribute('aria-label', 'Remove this input');
    remove.addEventListener('click', () => { if ($('tr-inputs').children.length > 1) { row.remove(); renderExamples(); } });
    const sync = () => { minL.hidden = maxL.hidden = kind.value !== 'integer'; lenL.hidden = kind.value !== 'string'; renderExamples(); };
    kind.addEventListener('change', sync); name.addEventListener('change', renderExamples);
    row.append(field('Name', name), field('Kind', kind), minL, maxL, lenL, remove);
    $('tr-inputs').append(row);
    minL.hidden = maxL.hidden = kind.value !== 'integer'; lenL.hidden = kind.value !== 'string';
  }
  function declarations() {
    return Array.from($('tr-inputs').children).map(row => {
      const get = role => row.querySelector('[data-role="' + role + '"]');
      const decl = { name: get('name').value.trim(), kind: get('kind').value };
      if (decl.kind === 'integer') { decl.min = parseInt(get('min').value, 10) || 0; decl.max = parseInt(get('max').value, 10) || 0; }
      if (decl.kind === 'string') decl.max_length = Math.min(1024, Math.max(1, parseInt(get('len').value, 10) || 200));
      return decl;
    });
  }
  function valueControl(kind, value, label) {
    let control;
    if (kind === 'boolean') {
      control = make('select', 'select');
      [['true', 'Yes'], ['false', 'No']].forEach(([v, t]) => { const o = make('option', null, t); o.value = v; control.append(o); });
      control.value = value === false ? 'false' : 'true';
    } else {
      control = make('input', 'input');
      control.type = kind === 'integer' ? 'number' : 'text';
      if (kind === 'integer') control.step = '1';
      control.value = value == null ? '' : String(value);
    }
    control.dataset.kind = kind;
    const wrap = make('label', 'ft__cell', label);
    wrap.append(control);
    return wrap;
  }
  function readValue(control) {
    if (control.dataset.kind === 'boolean') return control.value === 'true';
    if (control.dataset.kind === 'integer') { const n = Number(control.value); return Number.isSafeInteger(n) ? n : null; }
    return control.value;
  }
  function exampleRow(values) {
    const row = make('div');
    declarations().forEach(decl => {
      const cell = valueControl(decl.kind, values ? values.inputs[decl.name] : null, human(decl.name));
      cell.querySelector('[data-kind]').dataset.input = decl.name;
      row.append(cell);
    });
    const answer = valueControl($('tr-output').value, values ? values.output : null, 'Right answer');
    answer.querySelector('[data-kind]').dataset.answer = '1';
    const remove = make('button', 'btn btn--ghost', 'Remove'); remove.type = 'button';
    remove.setAttribute('aria-label', 'Remove this example');
    remove.addEventListener('click', () => row.remove());
    row.append(answer, remove);
    $('tr-examples').append(row);
  }
  function examples() {
    return Array.from($('tr-examples').children).map(row => {
      const inputs = {};
      row.querySelectorAll('[data-input]').forEach(c => { inputs[c.dataset.input] = readValue(c); });
      return { inputs: inputs, output: readValue(row.querySelector('[data-answer]')) };
    });
  }
  function renderExamples() {
    const kept = examples();
    $('tr-examples').replaceChildren();
    (kept.length ? kept : [null, null]).forEach(v => exampleRow(v));
  }
  function showRule(artifact, summary) {
    state.artifact = artifact;
    $('tr-summary').textContent = summary.rules + (summary.rules === 1 ? ' rule fits' : ' rules fit') + ' your '
      + summary.examples + (summary.examples === 1 ? ' example.' : ' examples.')
      + (summary.rules > 1 ? ' Where they disagree, the step asks its assistant.' : '');
    const box = $('tr-try');
    box.replaceChildren();
    const row = make('div');
    (summary.inputs || []).forEach(i => {
      const cell = valueControl(i.kind, null, human(i.name));
      cell.querySelector('[data-kind]').dataset.input = i.name;
      row.append(cell);
    });
    box.append(row);
    $('tr-result').hidden = false;
    $('tr-more').hidden = false;
    $('tr-try-out').textContent = '';
  }
  function constants() {
    return $('tr-constants').value.split(',').map(v => v.trim()).filter(Boolean).slice(0, 8).map(v => {
      if (/^-?\d{1,12}$/.test(v)) return Number(v);
      if (/^(yes|true)$/i.test(v)) return true;
      if (/^(no|false)$/i.test(v)) return false;
      return v.slice(0, 200);
    });
  }
  function selectedStep() { return $('tr-step').value; }
  function stepInfo() { return (state.overview && state.overview.steps || []).find(s => s.id === selectedStep()); }
  function showCurrent() {
    const step = stepInfo();
    $('tr-current-actions').hidden = !(step && step.rule);
    if (!step) { $('tr-current').textContent = ''; return; }
    $('tr-current').textContent = !step.ready ? 'This step needs instructions and a place to keep its answer before it can learn a rule.'
      : step.rule ? 'This step has a learned rule from ' + step.rule.examples + ' examples.' : 'This step always asks its assistant.';
  }
  async function loadOverview() {
    state.overview = await api('/api/v1/flows/' + enc + '/teach');
    const select = $('tr-step');
    const before = select.value;
    select.replaceChildren();
    const steps = state.overview.steps || [];
    if (!steps.length) { const o = make('option', null, 'This flow has no AI steps'); o.value = ''; select.append(o); }
    steps.forEach(s => { const o = make('option', null, s.label + (s.rule ? ' (has a learned rule)' : '')); o.value = s.id; select.append(o); });
    if (before && steps.some(s => s.id === before)) select.value = before;
    showCurrent();
    const tools = $('rc-tools');
    if (tools) {
      tools.replaceChildren();
      (state.overview.tools || []).forEach(t => {
        const label = make('label');
        const box = make('input'); box.type = 'checkbox'; box.value = t; box.checked = true;
        label.append(box, ' ' + human(t));
        tools.append(label);
      });
      if (!(state.overview.tools || []).length) tools.append(make('p', 'muted', 'This flow has no actions yet.'));
    }
  }
  $('tr-step').addEventListener('change', showCurrent);
  $('tr-add-input').addEventListener('click', () => { if ($('tr-inputs').children.length < 6) { inputRow({}); renderExamples(); } });
  $('tr-add-example').addEventListener('click', () => { if ($('tr-examples').children.length < 64) exampleRow(null); });
  $('tr-output').addEventListener('change', renderExamples);
  $('tr-learn').addEventListener('click', () => guarded('learn from these examples', async () => {
    const style = document.querySelector('input[name="tr-style"]:checked').value;
    const result = await api('/api/v1/flows/' + enc + '/teach/rule/learn', {
      inputs: declarations(), output_kind: $('tr-output').value, examples: examples(), constants: constants(),
      combined: style === 'combined', max_operations: Number($('tr-ops').value) || 3
    });
    showRule(result.artifact, result.summary);
    say('Learned. Try some inputs, then use the rule in the step.');
  }));
  $('tr-more').addEventListener('click', () => guarded('add these examples', async () => {
    if (!state.artifact) return;
    const result = await api('/api/v1/flows/' + enc + '/teach/rule/add-examples', { artifact: state.artifact, examples: examples() });
    showRule(result.artifact, result.summary);
    say('Added. The earlier examples were kept.');
  }));
  $('tr-try-go').addEventListener('click', () => guarded('try the rule', async () => {
    if (!state.artifact) return;
    const inputs = {};
    $('tr-try').querySelectorAll('[data-input]').forEach(c => { inputs[c.dataset.input] = readValue(c); });
    const result = await api('/api/v1/flows/' + enc + '/teach/rule/try', { artifact: state.artifact, inputs: inputs });
    const value = result.value === true ? 'Yes' : result.value === false ? 'No' : result.value;
    $('tr-try-out').textContent = (result.answered ? 'Answer: ' + value + '. ' : '') + result.explanation;
  }, 'tr-try-out'));
  $('tr-attach').addEventListener('click', () => guarded('use the rule in this step', async () => {
    if (!state.artifact || !selectedStep()) { say('Choose a step first.'); return; }
    const result = await api('/api/v1/flows/' + enc + '/teach/rule/attach', { artifact: state.artifact, node_id: selectedStep() });
    offerSave(result.draft, result.note, 'update');
  }));
  $('tr-remove').addEventListener('click', () => guarded('remove the learned rule', async () => {
    const result = await api('/api/v1/flows/' + enc + '/teach/rule/remove', { node_id: selectedStep() });
    offerSave(result.draft, result.note, 'update');
  }));
  $('tr-use-current').addEventListener('click', () => guarded('load the current rule', async () => {
    const flow = await api('/api/v1/flows/' + enc);
    const node = (flow.nodes || []).find ? (flow.nodes || []).find(n => n.id === selectedStep()) : (flow.nodes || {})[selectedStep()];
    const artifact = node && node.learned_operator && node.learned_operator.artifact;
    if (!artifact) { say('This step has no learned rule.'); return; }
    const spec = artifact.spec || {};
    $('tr-inputs').replaceChildren();
    Object.keys(spec.inputs || {}).sort().forEach(name => inputRow(Object.assign({ name: name, kind: spec.inputs[name].type }, spec.inputs[name])));
    $('tr-output').value = spec.output_type || 'string';
    $('tr-examples').replaceChildren();
    exampleRow(null);
    showRule(artifact, { rules: (artifact.candidates || []).length || 1, examples: artifact.example_count || 0,
      inputs: Object.keys(spec.inputs || {}).sort().map(n => ({ name: n, kind: spec.inputs[n].type })) });
    say('The current rule is loaded. Add examples to improve it, or try it.');
  }));

  // ---- questions about unclear cases (existing reviewed-question routes) -----------
  if ($('tq-ask')) {
    $('tq-ask').addEventListener('click', () => guarded('find an unclear case', async () => {
      const step = stepInfo();
      if (!step || !step.rule) { say('Choose a step with a saved learned rule first.', 'tq-status'); return; }
      const f = state.overview.flow;
      const record = await api('/api/v1/flows/' + enc + '/operator-questions', { node_id: step.id, version: f.version, revision: f.revision });
      state.question = record;
      if (record.status === 'draft_ready') { await reviewedDraft(record.id); return; }
      if (record.status !== 'awaiting_answer' || !record.question || !record.question.inputs) {
        $('tq-case').hidden = true;
        say(record.status === 'equivalent' ? 'Every rule gives the same answer for every possible input. There is nothing to ask.'
          : 'No unclear case was found in the time allowed. The rules may still disagree on inputs not checked.', 'tq-status');
        return;
      }
      const inputs = record.question.inputs;
      $('tq-inputs').textContent = 'For ' + Object.keys(inputs).map(k => human(k) + ' = ' + inputs[k]).join(', ') + ', the rules answer: '
        + (record.question.predictions || []).map(p => String(p.output) + ' (' + p.candidate_count + ')').join(' or ') + '.';
      const kind = (step.rule && step.rule.output_kind) || 'string';
      const wrap = $('tq-answer-wrap');
      wrap.replaceChildren(valueControl(kind, null, 'The right answer'));
      $('tq-case').hidden = false;
      say('What should the answer be?', 'tq-status');
    }, 'tq-status'));
    $('tq-answer').addEventListener('click', () => guarded('record your answer', async () => {
      const record = state.question;
      if (!record) return;
      const reason = $('tq-reason').value.trim();
      if (!reason) { say('Say briefly why this is the right answer.', 'tq-status'); return; }
      const output = readValue($('tq-answer-wrap').querySelector('[data-kind]'));
      const answered = await api('/api/v1/flows/' + enc + '/operator-questions/' + record.id + '/answer',
        { expected_revision: record.revision, output: output, reviewed: true, rationale: reason });
      $('tq-case').hidden = true;
      if (answered.status === 'draft_ready') await reviewedDraft(record.id);
      else say('Your answer was recorded.', 'tq-status');
    }, 'tq-status'));
  }
  async function reviewedDraft(questionId) {
    const result = await api('/api/v1/flows/' + enc + '/operator-questions/' + questionId + '/draft');
    offerSave(result.flow, 'Your answer was added to the step’s rule. Save to keep it; publishing stays separate.', 'update');
    say('Your answer is ready to save.', 'tq-status');
  }

  // ---- flows from reviewed runs ------------------------------------------------------
  function runRow(run, name, eligible) {
    const item = make('div');
    const label = make('label');
    const box = make('input'); box.type = 'checkbox'; box.value = run.run_id; box.name = name; box.disabled = !eligible;
    const when = run.started && typeof window.mvWhen === 'function' ? window.mvWhen(run.started) : 'Run';
    const permission = run.status === 'accepted' && !run.can_learn
      ? (run.grant ? ' · Learning permission ended' : ' · Allow learning from its run page first') : '';
    label.append(box, ' ' + when + ' · ' + (STATUS[run.status] || human(run.status)) + permission);
    item.append(label);
    return item;
  }
  function checked(name) { return Array.from(document.querySelectorAll('input[name="' + name + '"]:checked')).map(b => b.value); }
  async function loadRuns() {
    const data = await api('/api/v1/flows/' + enc + '/teach/examples');
    state.runs = data.runs || [];
    if ($('rc-runs')) {
      $('rc-runs').replaceChildren();
      if (!data.available) $('rc-runs').append(make('p', 'muted', data.message || 'Reviewed examples aren’t available.'));
      else if (!state.runs.length) $('rc-runs').append(make('p', 'muted', 'This flow has no runs yet. Run it, then review runs as examples.'));
      state.runs.forEach(run => $('rc-runs').append(runRow(run, 'rc-run', run.can_learn)));
    }
    $('sg-runs').replaceChildren();
    const signable = root.dataset.lineage === '1' ? state.runs.filter(r => r.can_sign) : [];
    if (!signable.length) $('sg-runs').append(make('p', 'muted', root.dataset.lineage === '1'
      ? 'No runs are allowed for learning right now. Review a run and grant learning permission from its run page.'
      : 'Signed learning is not ready. An administrator can check it in Settings.'));
    signable.forEach(run => {
      const row = runRow(run, 'sg-run', true);
      const button = make('button', 'btn btn--ghost', 'Make a signed copy'); button.type = 'button';
      button.addEventListener('click', () => guarded('make a signed copy', async () => {
        const bundle = await api('/api/v1/flows/' + enc + '/runs/' + encodeURIComponent(run.run_id) + '/episode-capture/signed-export',
          { expected_revision: run.revision, expected_episode_sha256: run.episode, expected_grant_sha256: run.grant });
        addBundle(bundle, 'Signed copy of a run');
      }));
      row.append(button);
      $('sg-runs').append(row);
    });
  }
  function syncRecipeStyle() {
    if (!$('rc-style')) return;
    $('rc-ops-wrap').hidden = $('rc-style').value === 'plain';
  }
  if ($('rc-learn')) {
    $('rc-style').addEventListener('change', syncRecipeStyle);
    $('rc-learn').addEventListener('click', () => guarded('learn from these runs', async () => {
      const runs = checked('rc-run');
      if (runs.length < 2) { say('Choose at least two reviewed runs.'); return; }
      const allowed = Array.from($('rc-tools').querySelectorAll('input:checked')).map(b => b.value);
      if (!allowed.length) { say('Choose at least one action the new flow may use.'); return; }
      const result = await api('/api/v1/flows/' + enc + '/teach/recipe/learn', { run_ids: runs, tools: allowed,
        style: $('rc-style').value, max_operations: Number($('rc-ops').value) || 2 });
      showRecipe(result);
    }));
    $('rc-more').addEventListener('click', () => guarded('add these runs', async () => {
      const runs = checked('rc-run');
      if (!state.recipe || !runs.length) { say('Choose the runs to add.'); return; }
      showRecipe(await api('/api/v1/flows/' + enc + '/teach/recipe/refine', { package: state.recipe, run_ids: runs }));
    }));
    $('rc-draft').addEventListener('click', () => guarded('make the new flow', async () => {
      const name = $('rc-name').value.trim();
      if (!name) { say('Name the new flow first.'); $('rc-name').focus(); return; }
      if (!state.recipe) { say('Choose reviewed runs and learn from them first.'); return; }
      const ok = typeof window.mvConfirm === 'function' && await window.mvConfirm(
        'Approve the signed examples for this new flow? Future actions will check that each example is still allowed for learning.',
        { okText: 'Approve and prepare flow' });
      if (!ok) return;
      const result = await api('/api/v1/flows/' + enc + '/teach/recipe/draft', { package: state.recipe, name: name, teaching: $('rc-teach').checked });
      offerSave(result.draft, result.note, 'new');
    }));
  }
  function describeLearned(summary) {
    return 'Learned from ' + summary.runs + (summary.runs === 1 ? ' run' : ' runs') + ': '
      + (summary.actions || []).map(human).join(', then ') + '.'
      + (summary.choice ? ' The last action is a choice between two actions.' : '')
      + (summary.alternatives > 1 ? ' Some details can be worked out in more than one way, so the new flow asks before each action.' : '');
  }
  function showRecipe(result) {
    state.recipe = result.package;
    document.querySelectorAll('input[name="rc-run"]:checked').forEach(box => { box.checked = false; });
    $('rc-summary').textContent = describeLearned(result.summary);
    $('rc-teach-wrap').hidden = !result.summary.choice;
    $('rc-result').hidden = false; $('rc-more').hidden = false;
    syncNextActions();
    if (!$('rc-name').value) $('rc-name').value = (state.overview && state.overview.flow.name ? state.overview.flow.name + ' (learned)' : 'Learned flow');
    say('Learned. Name the new flow and make it, or add more runs.');
  }

  // ---- signed examples ------------------------------------------------------------------
  async function addBundle(bundle, label) {
    const check = await api('/api/v1/flow-teach/signed-copy/check', { bundle: bundle, trust: state.trust || undefined });
    const item = make('li', 'mv-row');
    item.append(make('span', 'mv-row__title', label), make('span', 'muted', check.genuine
      ? ' · Genuine and unchanged. ' + check.withdrawal : ' · ' + check.message));
    if (check.genuine) {
      state.bundles.push(bundle);
      const save = make('button', 'btn btn--ghost', 'Download'); save.type = 'button';
      save.addEventListener('click', () => download('signed-example-' + state.bundles.length + '.json', bundle));
      item.append(save);
    }
    $('sg-set').append(item);
    say(check.genuine ? 'Signed example added.' : 'That signed example can’t be used.');
  }
  $('sg-files').addEventListener('change', () => guarded('read the signed examples', async () => {
    for (const file of Array.from($('sg-files').files || []).slice(0, 32)) {
      const value = await readJsonFile(file);
      const list = Array.isArray(value) ? value : [value];
      for (const bundle of list.slice(0, 32)) await addBundle(bundle, file.name);
    }
    $('sg-files').value = '';
  }));
  $('sg-trust').addEventListener('change', () => guarded('read the trust file', async () => {
    const file = $('sg-trust').files && $('sg-trust').files[0];
    state.trust = file ? await readJsonFile(file) : null;
    say(file ? 'The chosen trust file only previews who signed an example. Learning uses this workspace’s keys.' : 'Signed examples are checked against this workspace’s keys.');
  }));
  $('sg-package').addEventListener('change', () => guarded('open the learned file', async () => {
    const file = $('sg-package').files && $('sg-package').files[0];
    if (!file) return;
    const pkg = await readJsonFile(file);
    showPackage({ package: pkg, summary: { runs: (pkg.artifact && pkg.artifact.episode_count) || 0, actions: [], alternatives: 0 } });
  }));
  function showPackage(result) {
    state.pkg = result.package;
    $('sg-summary').textContent = describeLearned(result.summary) + ' It isn’t approved for governed use yet.';
    $('sg-result').hidden = false; $('sg-refine').hidden = false; $('sg-steps').replaceChildren();
    syncNextActions();
  }
  $('sg-learn').addEventListener('click', () => guarded('learn from the signed examples', async () => {
    if (state.bundles.length < 2) { say('Add at least two signed examples.'); return; }
    const style = $('sg-style').value;
    const tools = Array.from(new Set(state.bundles.flatMap(b => ((b.body || {}).contract || {}).allowed_tools || [])));
    if (style === 'workflow' || style === 'memory') {
      const ok = typeof window.mvConfirm === 'function' && await window.mvConfirm(
        'Learn from these signed examples and approve the result for governed use now? Withdrawing any of the examples later stops it.',
        { okText: 'Learn and approve' });
      if (!ok) return;
    }
    const result = await api('/api/v1/flow-teach/signed/learn', { bundles: state.bundles, tools: tools.slice(0, 8), style: style });
    if (result.package) showPackage(result);
    else { say('Learned and approved for governed use.'); await loadApproved(); }
  }));
  $('sg-refine').addEventListener('click', () => guarded('add the signed examples', async () => {
    if (!state.pkg || !state.bundles.length) { say('Add signed examples first.'); return; }
    showPackage(await api('/api/v1/flow-teach/signed/refine', { package: state.pkg, bundles: state.bundles }));
  }));
  $('sg-inspect').addEventListener('click', () => guarded('show it as a flow', async () => {
    if (!state.pkg) return;
    const result = await api('/api/v1/flow-teach/signed/inspect', { package: state.pkg, name: 'Learned flow' });
    const list = $('sg-steps');
    list.replaceChildren();
    (result.steps || []).forEach(s => list.append(make('li', null, human(s.kind) + ': ' + human(s.label))));
    say(result.note);
  }));
  $('sg-download').addEventListener('click', () => { if (state.pkg) download('learned-recipe.json', state.pkg); });
  $('sg-approve').addEventListener('click', () => guarded('approve it for governed use', async () => {
    if (!state.pkg) return;
    const ok = typeof window.mvConfirm === 'function' && await window.mvConfirm(
      'Approve this learned recipe for governed use in this workspace? Withdrawing any of its signed examples later stops it.',
      { okText: 'Approve' });
    if (!ok) return;
    await api('/api/v1/flows/recipe-lineage', { package: state.pkg });
    say('Approved. Make a flow from it in the list below.');
    await loadApproved();
  }));
  async function loadApproved() {
    let data;
    try { data = await api('/api/v1/flow-teach/approved'); } catch (_) { return; }
    const list = $('sg-approved');
    list.replaceChildren();
    if (!(data.recipes || []).length) { list.append(make('li', 'muted', 'No approved recipes yet.')); return; }
    data.recipes.forEach(r => {
      const item = make('li', 'mv-row');
      const when = r.approved_at && typeof window.mvWhen === 'function' ? window.mvWhen(r.approved_at) : '';
      item.append(make('span', 'mv-row__title', 'Recipe approved ' + when), make('span', 'badge', human(r.status)));
      if (r.status === 'admitted') {
        const button = make('button', 'btn btn--ghost', 'Make a flow from it'); button.type = 'button';
        button.addEventListener('click', () => guarded('make a flow from it', async () => {
          const name = (state.overview && state.overview.flow.name ? state.overview.flow.name : 'Learned') + ' (approved recipe)';
          const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) + '-' + Math.random().toString(16).slice(2, 8);
          const draft = await api('/api/v1/flows/recipe-lineage/' + encodeURIComponent(r.id) + '/draft', { flow_id: id, name: name, teaching: false });
          offerSave(draft, 'A new flow from the approved recipe. Every action waits for approval. Save to keep it.', 'new');
        }));
        item.append(button);
      }
      list.append(item);
    });
  }

  // ---- start ---------------------------------------------------------------------------
  inputRow({ name: 'amount', kind: 'integer', min: 0, max: 1000 });
  renderExamples();
  syncRecipeStyle();
  syncNextActions();
  guarded('load this flow', async () => {
    await loadOverview();
    await loadRuns();
    await loadApproved();
    say('');
  });
})();
