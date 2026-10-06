// Learning page: run, check and review controls for each learning system
// (improvement flywheel, agent-building guidance, practising code changes,
// specialist model planning, the reward model, the agents' shorthand, and
// task outlines waiting to be shared). Long work runs as a background job.
(function () {
  'use strict';
  var root = document.getElementById('lo-root');
  if (!root || !window.LWJobs) return;
  var J = window.LWJobs;
  var el = J.el, plural = J.plural;
  var admin = root.getAttribute('data-admin') === '1';
  var LO = '/api/v1/learning-ops', TO = '/api/v1/training-ops';
  function $(id) { return document.getElementById(id); }
  function ui(prefix, action, render, after) {
    return { status: $(prefix + '-status'), out: $(prefix + '-out'), action: action, render: render, after: after };
  }
  function fill(id, nodes) {
    var box = $(id);
    if (!box) return;
    box.textContent = '';
    (Array.isArray(nodes) ? nodes : [nodes]).forEach(function (n) { if (n) box.appendChild(n); });
  }
  function list(items) {
    var ul = el('ul');
    items.forEach(function (t) { ul.appendChild(el('li', null, t)); });
    return ul;
  }
  function options(select, items, chosen) {
    if (!select) return;
    select.textContent = '';
    items.forEach(function (it) {
      var o = el('option', null, it.label);
      o.value = String(it.value);
      if (chosen != null && String(it.value) === String(chosen)) o.selected = true;
      select.appendChild(o);
    });
  }
  function radio(name) {
    var picked = root.querySelector('input[name="' + name + '"]:checked');
    return picked ? picked.value : '';
  }
  async function fileBody(input, missing) {
    var file = input && input.files && input.files[0];
    if (!file) { window.mvToast(missing, { error: true }); return null; }
    return J.readFile(file);
  }

  // ---- improvement flywheel ------------------------------------------------
  J.wire($('lo-fw-run'), async function () {
    if (!(await window.mvConfirm('Turn the improvement flywheel now? It updates what agents avoid and the habits they keep, for the whole installation.',
      { okText: 'Turn now' }))) return null;
    return { url: LO + '/flywheel/turn', init: { method: 'POST', json: {} } };
  }, ui('lo-fw', 'turn the flywheel', function (d) {
    if (!d.acted) return el('p', 'muted', 'Nothing to learn yet. It needs more finished tasks to look at.');
    var out = [el('p', null, 'Looked at ' + plural(d.tasks, 'task') + '. Learned ' + plural(d.counts.avoid, 'thing') +
      ' to avoid and ' + plural(d.counts.habits, 'habit') + ' that help, and suggested ' + plural(d.counts.ideas, 'better step') + ' to test.')];
    if (d.avoid.length) { out.push(el('h4', null, 'Now avoids')); out.push(list(d.avoid.map(window.mvHuman || String))); }
    if (d.habits.length) { out.push(el('h4', null, 'Habits that help')); out.push(list(d.habits.map(window.mvHuman || String))); }
    if (d.ideas.length) {
      out.push(el('h4', null, 'Better steps to test'));
      out.push(list(d.ideas.map(function (i) { return 'Instead of “' + i.instead_of + '”, try “' + i.try + '”'; })));
    }
    return out;
  }));

  // ---- improve how new agents are built -----------------------------------------
  var faPreview = $('lo-fa-preview');
  if (faPreview) faPreview.addEventListener('click', async function () {
    faPreview.disabled = true;
    $('lo-fa-status').textContent = 'Looking…';
    $('lo-fa-out').textContent = '';
    try {
      var d = await J.request(LO + '/factory/preview?min_support=' + encodeURIComponent($('lo-fa-support').value));
      $('lo-fa-status').textContent = d.corrections.length
        ? plural(d.corrections.length, 'problem keeps', 'problems keep') + ' showing up. Nothing was changed.'
        : 'No problem shows up often enough yet. Nothing would change.';
      if (d.corrections.length) fill('lo-fa-out', J.table(['Suggested guidance', 'Seen in', 'Area'], d.corrections.map(function (c) {
        return [c.guidance, plural(c.seen_in, 'agent'), window.mvHuman ? window.mvHuman(c.area) : c.area];
      })));
    } catch (failure) {
      $('lo-fa-status').textContent = await J.problem(failure, 'look for repeated problems');
    } finally { faPreview.disabled = false; }
  });
  var faAdopt = $('lo-fa-adopt');
  if (faAdopt) faAdopt.addEventListener('click', async function () {
    var body = await fileBody($('lo-fa-file'), 'Choose the results file first.');
    if (!body) return;
    if (!(await window.mvConfirm('Apply the guidance that passes the paired comparison checks? New agents will be built with it. You are responsible for reviewing the source of the uploaded results; a file check alone does not establish their accuracy or business value.',
      { okText: 'Adopt guidance' }))) return;
    faAdopt.disabled = true;
    $('lo-fa-status').textContent = 'Checking the results file…';
    try {
      var d = await J.request(LO + '/factory/promote?confirm=true&min_support=' + encodeURIComponent($('lo-fa-support').value),
        { method: 'POST', body: body, type: 'application/json' });
      $('lo-fa-status').textContent = d.adopted ? 'Adopted ' + plural(d.adopted, 'piece') + ' of guidance.'
        : 'Nothing passed the checks this time, so nothing was adopted.';
    } catch (failure) {
      $('lo-fa-status').textContent = await J.problem(failure, 'adopt the guidance');
    } finally { faAdopt.disabled = false; }
  });

  // ---- practise changes to its own code -------------------------------------------
  function loadModify() {
    if (!$('lo-sm-state')) return;
    J.request(LO + '/self-modify').then(function (d) {
      var nodes = [];
      var badge = el('span', 'badge' + (d.state === 'ready' ? ' active' : ''), d.state === 'ready' ? 'On and ready' : (d.state === 'blocked' ? 'On, but something is blocking it' : 'Off'));
      var head = el('p');
      head.appendChild(badge);
      nodes.push(head);
      if (d.blockers.length) { nodes.push(el('p', null, 'Before it can run:')); nodes.push(list(d.blockers)); }
      nodes.push(el('p', null, 'It may practise on ' + plural(d.areas, 'area') + ' of its own code' +
        (d.level ? ' (level: ' + d.level + ')' : '') + '. ' + plural(d.results, 'result') + ' kept for review.'));
      nodes.push(el('p', 'muted', d.spending_limit != null ? 'Each run can spend up to ' + J.money(d.spending_limit) + '.'
        : 'No spending limit is set for this experiment, so the general task limits apply.'));
      nodes.push(J.technical(d.technical.areas, 'Technical details: areas it may change'));
      fill('lo-sm-state', nodes);
      var form = $('lo-sm-form');
      if (form) form.dataset.limit = d.spending_limit != null ? String(d.spending_limit) : '';
    }).catch(async function (failure) {
      fill('lo-sm-state', el('p', null, await J.problem(failure, 'load the experiment')));
    });
    J.request(LO + '/self-modify/results?limit=20').then(function (d) {
      if (!d.results.length) { fill('lo-sm-results', el('p', 'muted', 'No results yet. Start practice rounds below.')); return; }
      fill('lo-sm-results', J.table(['When', 'Round', 'Score before', 'Score after', 'What it tried', 'Notes'], d.results.map(function (r) {
        var notes = [];
        if (r.undone) notes.push('undone');
        if (r.widens) notes.push('would need more access');
        return [J.when(r.when), String(r.round), r.before.toFixed(3), r.score.toFixed(3), r.summary, notes.join(', ') || '—'];
      })));
    }).catch(async function (failure) {
      fill('lo-sm-results', el('p', null, await J.problem(failure, 'load the results')));
    });
  }
  var smForm = $('lo-sm-form');
  if (smForm) smForm.addEventListener('submit', async function (ev) {
    ev.preventDefault();
    var objective = $('lo-sm-objective').value.trim();
    if (objective.length < 10) { $('lo-sm-status').textContent = 'Describe what it should try to improve, in a sentence.'; return; }
    var cycles = Number($('lo-sm-cycles').value);
    var limit = smForm.dataset.limit;
    var cost = limit ? ' It can spend up to ' + J.money(Number(limit)) + '.' : '';
    if (!(await window.mvConfirm('Start ' + plural(cycles, 'practice round') + '? Lightwork writes changes to a copy of its own code, tests them in the practice area and keeps the results for review. Nothing is installed.' + cost,
      { okText: 'Start rounds' }))) return;
    var btn = $('lo-sm-run');
    btn.disabled = true;
    var job = await J.run(LO + '/self-modify/run', { method: 'POST', json: { objective: objective, cycles: cycles, confirm: true } },
      ui('lo-sm', 'start the practice rounds', function (d) {
        var out = [];
        if (!d.ran) out.push(el('p', null, d.problem || 'The experiment could not run. Nothing was changed. Try again.'));
        else out.push(el('p', null, 'Tested ' + plural(d.tested, 'change') + ' across ' + plural(d.rounds.length, 'round') + '. Spent ' + J.money(d.spent) + '.'));
        if (d.rounds.length) out.push(J.technical(d.rounds.map(function (r, i) {
          return 'Round ' + (i + 1) + ': ' + r.ideas + ' idea(s), ' + r.tested + ' tested' + (r.note ? ' — ' + r.note : '');
        })));
        return out;
      }));
    btn.disabled = false;
    if (job) loadModify();
  });

  // ---- plan a specialist model ------------------------------------------------------
  function loadPlanning() {
    J.request(TO + '/model-improvement').then(function (d) {
      var nodes = [el('p', null, (d.on ? 'Model improvement is on.' : 'Model improvement is off; planning still works.') +
        ' The model list is from ' + d.catalog.as_of + ' and has ' + plural(d.catalog.candidates, 'model') + '.')];
      if (d.task_sets.length) nodes.push(J.table(['Task set', 'Ready to prove a model', 'Practice groups', 'Kept-aside groups'],
        d.task_sets.map(function (t) {
          return [t.label, t.ready ? 'Yes' : 'Not yet: ' + t.why_not.join(' '), String(t.practice_groups), String(t.kept_aside_groups)];
        })));
      fill('lo-mi-state', nodes);
      var c = d.choices;
      options($('lo-mi-role'), c.roles);
      options($('lo-mi-memory'), c.memory_gib.map(function (v) { return { value: v, label: v + ' GB' }; }), 32);
      options($('lo-mi-context'), c.context_tokens.map(function (v) { return { value: v, label: Math.round(v * 0.75).toLocaleString() + ' words' }; }), 16384);
      options($('lo-mi-concurrency'), c.concurrency.map(function (v) { return { value: v, label: String(v) }; }), 1);
      options($('lo-mi-set'), d.task_sets.map(function (t) { return { value: t.value, label: t.label }; }));
      options($('lo-mi-split'), c.splits);
    }).catch(async function (failure) {
      fill('lo-mi-state', el('p', null, await J.problem(failure, 'load model planning')));
    });
  }
  var find = $('lo-mi-find');
  if (find) find.addEventListener('click', async function () {
    find.disabled = true;
    fill('lo-mi-models', el('p', 'muted', 'Looking…'));
    try {
      var q = '?role=' + encodeURIComponent($('lo-mi-role').value) + '&memory_gib=' + encodeURIComponent($('lo-mi-memory').value) +
        '&context_tokens=' + encodeURIComponent($('lo-mi-context').value) + '&concurrency=' + encodeURIComponent($('lo-mi-concurrency').value);
      var d = await J.request(TO + '/model-improvement/models' + q);
      if (!d.models.length) { fill('lo-mi-models', el('p', 'muted', 'No model in the list is meant for this role.')); return; }
      fill('lo-mi-models', [J.table(['Model', 'Fits', 'Memory it needs', 'Licence', 'Status'], d.models.map(function (m) {
        return [m.name, m.fits ? 'Yes' : 'No', m.memory_needed_gib + ' GB', m.licence, m.status];
      })), el('p', 'muted', 'For planning only. No model is switched.')]);
    } catch (failure) {
      fill('lo-mi-models', el('p', null, await J.problem(failure, 'find models')));
    } finally { find.disabled = false; }
  });
  J.wire($('lo-mi-score'), async function () {
    var body = await fileBody($('lo-mi-file'), 'Choose the answers file first.');
    if (!body) return null;
    return { url: TO + '/model-improvement/score?task_set=' + encodeURIComponent($('lo-mi-set').value) + '&split=' + encodeURIComponent($('lo-mi-split').value),
      init: { method: 'POST', body: body, type: 'application/json' } };
  }, ui('lo-mi', 'score the answers', function (d) {
    return el('p', null, d.passed + ' of ' + plural(d.questions, 'question') + ' passed (' + Math.round(d.pass_rate * 100) + '%). Score ' +
      Math.round(d.score * 100) + '%.' + (d.unanswered ? ' ' + plural(d.unanswered, 'question') + ' had no answer.' : '') +
      (d.not_in_set ? ' ' + plural(d.not_in_set, 'answer') + ' did not match a question.' : ''));
  }));

  // ---- reward model ------------------------------------------------------------------
  function loadRewardModels() {
    J.request(TO + '/reward-models').then(function (d) {
      var nodes = [el('p', 'muted', plural(d.waiting_outlines, 'task outline') + ' waiting on this server to learn from.')];
      if (!d.models.length) nodes.push(el('p', 'muted', admin ? 'No reward model yet. Train one below.' : 'No reward model yet.'));
      else nodes.push(J.table(['Trained', 'Comparisons', 'Agreed with the checker', ''], d.models.map(function (m) {
        var link = el('a', 'btn btn--ghost', 'Download');
        link.href = TO + '/reward-models/' + encodeURIComponent(m.name) + '/download';
        link.setAttribute('download', '');
        return [J.when(m.created_at), String(m.pairs), Math.round(m.accuracy * 100) + '%', admin ? link : ''];
      })));
      fill('lo-rm-list', nodes);
      options($('lo-rm-model'), d.models.map(function (m) { return { value: m.name, label: 'Trained ' + J.when(m.created_at) }; }));
    }).catch(async function (failure) {
      fill('lo-rm-list', el('p', null, await J.problem(failure, 'load the reward models')));
    });
  }
  async function rewardSource() {
    var source = radio('lo-rm-source');
    if (source !== 'file') return { source: 'outbox', init: { method: 'POST', json: {} } };
    var body = await fileBody($('lo-rm-file'), 'Choose the task outline file first.');
    return body ? { source: 'file', init: { method: 'POST', body: body, type: 'application/octet-stream' } } : null;
  }
  J.wire($('lo-rm-train'), async function () {
    var src = await rewardSource();
    if (!src) return null;
    var q = '?source=' + src.source + '&min_margin=' + $('lo-rm-margin').value + '&max_pairs=' + $('lo-rm-pairs').value +
      '&epochs=' + $('lo-rm-epochs').value + '&lr=' + $('lo-rm-lr').value;
    return { url: TO + '/reward-models/train' + q, init: src.init };
  }, ui('lo-rm', 'train the reward model', function (d) {
    if (!d.trained) return el('p', null, d.why);
    return el('p', null, 'Trained on ' + plural(d.pairs, 'comparison') + ' from ' + plural(d.outlines, 'task outline') + '. It agrees with the answer checker ' +
      Math.round(d.accuracy * 100) + '% of the time.');
  }, loadRewardModels));
  J.wire($('lo-rm-score'), async function () {
    var model = $('lo-rm-model').value;
    if (!model) { window.mvToast('Train a reward model first.', { error: true }); return null; }
    var src = await rewardSource();
    if (!src) return null;
    return { url: TO + '/reward-models/' + encodeURIComponent(model) + '/score?source=' + src.source, init: src.init };
  }, ui('lo-rm', 'score the task outlines', function (d) {
    if (!d.scored) return el('p', 'muted', 'There are no task outlines to score.');
    return [el('p', null, 'Scored ' + plural(d.scored, 'task outline') + '. Highest first:'),
      J.table(['Rank', 'Result', 'Steps', 'Score'], d.top.map(function (r, i) {
        return [String(i + 1), r.outcome ? (window.mvHuman ? window.mvHuman(r.outcome) : r.outcome) : '—', String(r.steps), r.score.toFixed(3)];
      })), J.technical(d.top.map(function (r, i) { return (i + 1) + ': ' + r.reference; }))];
  }));

  // ---- shorthand between agents --------------------------------------------------------
  function loadCodec() {
    J.request(TO + '/codec').then(function (d) {
      var nodes = [el('p', null, 'Shorthand: ' + plural(d.shorthand.codes, 'code') + (d.shorthand.on ? ' (in use).' : ' (not in use).') +
        ' Token-saving codes: ' + plural(d.token_codes.codes, 'code') + (d.token_codes.on ? ' (being measured live).' : '.'))];
      if (d.shorthand.phrases.length) { nodes.push(el('h4', null, 'Phrases with a code')); nodes.push(list(d.shorthand.phrases)); }
      fill('lo-cd-state', nodes);
      var exact = $('lo-cd-exact');
      if (exact) Array.from(exact.options).filter(function (option) { return option.value !== '0'; }).forEach(function (option) { option.remove(); });
      if (exact && d.exact_count_available && exact.options.length === 1) {
        var o = el('option', null, 'Anthropic count estimate (sends these messages to your connected service)');
        o.value = '1';
        exact.appendChild(o);
      }
    }).catch(async function (failure) {
      fill('lo-cd-state', el('p', null, await J.problem(failure, 'load the shorthand')));
    });
  }
  function codecBody() { return { how_many: Number($('lo-cd-count').value), exact: $('lo-cd-exact').value === '1' }; }
  async function exactOk(body) {
    return !body.exact || window.mvConfirm('Ask your connected Anthropic service to estimate token counts? Recent messages between agents are sent to it. This does not generate an answer or establish the cost of future work.',
      { okText: 'Use provider count' });
  }
  J.wire($('lo-cd-learn'), async function () {
    if (!(await window.mvConfirm('Relearn the shorthand from recent messages between agents? The new codes replace the old ones.',
      { okText: 'Relearn' }))) return null;
    return { url: TO + '/codec/shorthand/learn', init: { method: 'POST', json: codecBody() } };
  }, ui('lo-cd', 'relearn the shorthand', function (d) {
    return [el('p', null, 'Learned ' + plural(d.codes, 'code') + ' from ' + plural(d.messages, 'message') + '. Messages would be ' + d.smaller_pct + '% shorter.'),
      d.phrases.length ? list(d.phrases) : null];
  }, loadCodec));
  J.wire($('lo-cd-tokens'), async function () {
    var body = codecBody();
    if (!(await exactOk(body))) return null;
    if (!(await window.mvConfirm('Learn token-saving codes from recent messages and keep them? They replace the current ones.',
      { okText: 'Learn codes' }))) return null;
    return { url: TO + '/codec/token-codes/learn', init: { method: 'POST', json: body } };
  }, ui('lo-cd', 'learn token-saving codes', function (d) {
    if (!d.learned) return el('p', null, d.why);
    return el('p', null, 'Learned ' + plural(d.codes, 'code') + ' from ' + plural(d.messages, 'message') + '. On those messages they would save about ' +
      d.saved_pct + '% of tokens.');
  }, loadCodec));
  J.wire($('lo-cd-measure'), async function () {
    var body = codecBody();
    if (!(await exactOk(body))) return null;
    return { url: TO + '/codec/measure', init: { method: 'POST', json: body } };
  }, ui('lo-cd', 'measure the savings', function (d) {
    if (!d.measured) return el('p', null, d.why);
    var lines = [(d.pays_off ? 'It saves tokens: ' : 'It costs more tokens: ') + d.tokens_saved_pct + '% tokens, ' + d.characters_saved_pct +
      '% characters, over ' + plural(d.messages, 'message') + ' with ' + plural(d.codes, 'code') + '.'];
    lines.push(d.break_even_after == null ? 'Explaining the codes to a model would never pay for itself.'
      : 'Explaining the codes costs ' + plural(d.codebook_tokens, 'token') + ', repaid after about ' + plural(d.break_even_after, 'message') + '.');
    return lines.map(function (t) { return el('p', null, t); });
  }));

  // ---- task outlines waiting to be shared ---------------------------------------------------
  function loadDonations() {
    J.request(LO + '/donations').then(function (d) {
      var nodes = [el('p', null, (d.sharing ? 'Sharing is on' + (d.with_text ? ', including task text.' : ', without task text.') : 'Sharing is off.') +
        ' ' + (d.waiting ? plural(d.waiting, 'outline') + ' waiting, not yet sent.' : 'Nothing is waiting.'))];
      if (d.waiting && d.can_clear) {
        var btn = el('button', 'btn btn--danger', 'Delete waiting outlines');
        btn.type = 'button';
        btn.addEventListener('click', async function () {
          if (!(await window.mvConfirm('Delete ' + plural(d.waiting, 'waiting outline') + '? They are removed without being sent and cannot be recovered.',
            { okText: 'Delete outlines' }))) return;
          btn.disabled = true;
          try {
            var r = await J.request(LO + '/donations/clear', { method: 'POST', json: { confirm: true } });
            $('lo-dn-status').textContent = 'Deleted ' + plural(r.cleared, 'outline') + '.';
            loadDonations();
          } catch (failure) {
            $('lo-dn-status').textContent = await J.problem(failure, 'delete the outlines');
            btn.disabled = false;
          }
        });
        nodes.push(btn);
      }
      fill('lo-dn-state', nodes);
    }).catch(async function (failure) {
      fill('lo-dn-state', el('p', null, await J.problem(failure, 'load the waiting outlines')));
    });
  }

  // ---- recent background jobs ----------------------------------------------------------------
  var STATES = { running: 'Running', stopping: 'Stopping', done: 'Finished', stopped: 'Stopped early', failed: 'Could not finish' };
  function loadJobs() {
    J.request(LO + '/jobs').then(function (d) {
      if (!d.jobs.length) { fill('lo-jobs', el('p', 'muted', 'No background tasks since Lightwork last started.')); return; }
      fill('lo-jobs', J.table(['Task', 'Started', 'Status'], d.jobs.map(function (j) {
        return [j.label, J.when(j.started_at), STATES[j.state] + (j.problem ? ': ' + j.problem : '')];
      })));
    }).catch(async function (failure) {
      fill('lo-jobs', el('p', null, await J.problem(failure, 'load the background tasks')));
    });
  }
  var refresh = $('lo-jobs-refresh');
  if (refresh) refresh.addEventListener('click', loadJobs);

  if (admin) loadModify();
  loadPlanning();
  loadRewardModels();
  loadCodec();
  loadDonations();
  loadJobs();
})();
