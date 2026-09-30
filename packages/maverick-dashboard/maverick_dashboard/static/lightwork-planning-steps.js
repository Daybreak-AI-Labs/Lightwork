// "Show planning steps": runs the plan, work and review loop on this task's
// request in the background and shows the trace. The task is never changed.
(() => {
  'use strict';
  const root = document.getElementById('planning-steps');
  if (!root) return;
  const goal = root.dataset.goal || '';
  if (!/^\d+$/.test(goal)) return;
  const $ = id => document.getElementById('ps-' + id);
  const STATUS = {
    done: 'The review found the request met.',
    max_iterations: 'It stopped after the rounds you chose.',
    stalled: 'The review asked to keep going without a new plan, so it stopped there.'
  };
  const VERDICT = { done: 'Met', revise: 'Plan changed', 'continue': 'Keep going' };
  let busy = false;
  function make(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function say(text) { $('status').textContent = text; }
  function failText(failure) {
    return typeof window.mvErrorText === 'function' ? window.mvErrorText(failure, 'show the planning steps')
      : 'Could not show the planning steps. Please try again.';
  }
  function valid(data) {
    return data && data.ran === true && Array.isArray(data.plan) && Array.isArray(data.steps) && Array.isArray(data.reviews)
      && data.plan.every(s => typeof s === 'string') && data.steps.every(s => s && typeof s.step === 'string' && typeof s.output === 'string')
      && data.reviews.every(r => r && typeof r.verdict === 'string' && typeof r.notes === 'string') && typeof data.cost === 'number';
  }
  function show(data) {
    const out = $('result');
    out.replaceChildren();
    const plan = make('section');
    plan.append(make('h3', null, 'The plan'));
    const list = make('ol');
    data.plan.forEach(step => list.append(make('li', null, step)));
    plan.append(list);
    out.append(plan);
    if (data.steps.length) {
      const work = make('section');
      work.append(make('h3', null, 'What each step produced'));
      data.steps.forEach(step => {
        const block = make('details');
        block.append(make('summary', null, step.step), make('p', null, step.output || 'No output.'));
        work.append(block);
      });
      out.append(work);
    }
    if (data.reviews.length) {
      const reviews = make('section');
      reviews.append(make('h3', null, 'Reviews'));
      const items = make('ol');
      data.reviews.forEach(r => items.append(make('li', null, (VERDICT[r.verdict] || 'Reviewed') + (r.notes ? ': ' + r.notes : ''))));
      reviews.append(items);
      out.append(reviews);
    }
    out.append(make('p', 'muted', (STATUS[data.status] || 'Finished.') + ' This cost '
      + (data.cost < 0.01 ? 'less than one cent' : '$' + data.cost.toFixed(2)) + '. Nothing about the task was changed.'));
    out.hidden = false;
  }
  $('run').addEventListener('click', async () => {
    if (busy) return;
    busy = true; $('run').disabled = true; $('result').hidden = true;
    say('Working out the planning steps. This can take a few minutes…');
    try {
      const response = await fetch('/api/v1/goals/' + goal + '/planning-steps', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ passes: Number($('passes').value) || 2 })
      });
      if (!response.ok) { say(failText(response)); return; }
      const started = await response.json();
      if (!started || !/^[a-f0-9]{32}$/.test(String(started.job))) { say('The request couldn’t be checked. Please try again.'); return; }
      for (let tries = 0; tries < 600; tries += 1) {
        await new Promise(resolve => setTimeout(resolve, tries ? 2000 : 500));
        const poll = await fetch('/api/v1/goals/' + goal + '/planning-steps/' + started.job, {
          credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }
        });
        if (!poll.ok) { say(failText(poll)); return; }
        const job = await poll.json();
        if (job && job.state === 'running') continue;
        const data = job && job.result;
        if (data && data.ran === false) { say(typeof data.message === 'string' ? data.message : 'This couldn’t be started.'); return; }
        if (!valid(data)) { say((data && typeof data.message === 'string' && data.message) || 'The planning steps couldn’t be checked. Please try again.'); return; }
        show(data);
        say('The planning steps are ready below.');
        return;
      }
      say('This is taking longer than expected. Check back later.');
    } catch (error) {
      say(failText(error));
    } finally {
      busy = false; $('run').disabled = false; $('status').focus();
    }
  });
})();
