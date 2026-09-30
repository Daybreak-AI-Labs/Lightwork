// "Get two viewpoints": one side argues for, one against, a neutral judge
// weighs them. The server admits the request like a task start (spending
// limits, caps, activity log); this script sends the question and shows the
// answer in plain words.
(() => {
  'use strict';
  const form = document.getElementById('vp-form');
  if (!form) return;
  const question = document.getElementById('vp-question');
  const rounds = document.getElementById('vp-rounds');
  const button = document.getElementById('vp-run');
  const status = document.getElementById('vp-status');
  const result = document.getElementById('vp-result');
  const SIDE = { for: 'The case for', against: 'The case against' };
  const VERDICT = {
    for: 'The judge found the case for stronger.',
    against: 'The judge found the case against stronger.',
    draw: 'The judge found both sides equally strong.'
  };
  let busy = false;

  function product(node, text, values) {
    if (window.LightworkI18n) window.LightworkI18n.set(node, text, values);
    else node.textContent = text.replace(/\{(\w+)\}/g, (_, key) => values && values[key] !== undefined ? values[key] : _);
  }
  function translated(text) { return window.LightworkI18n ? window.LightworkI18n.text(text) : text; }
  function make(tag, cls, text, source = false) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) {
      if (source) { node.textContent = text; node.lang = 'und'; node.dir = 'auto'; node.dataset.userContent = '1'; }
      else product(node, text);
    }
    return node;
  }
  function say(text) { product(status, text); }
  function failText(failure) {
    return typeof window.mvErrorText === 'function'
      ? window.mvErrorText(failure, 'get two viewpoints')
      : 'Could not get two viewpoints. Please try again.';
  }
  function valid(data) {
    if (!data || typeof data !== 'object') return false;
    if (data.ran === false) return typeof data.message === 'string';
    return data.ran === true && Array.isArray(data.turns) && data.turns.length <= 6 &&
      data.turns.every(turn => turn && Object.prototype.hasOwnProperty.call(SIDE, turn.side) && typeof turn.text === 'string') &&
      Object.prototype.hasOwnProperty.call(VERDICT, data.verdict) && typeof data.reason === 'string' &&
      typeof data.key_point === 'string' && typeof data.cost === 'number' && Number.isFinite(data.cost);
  }
  function money(value) {
    return value < 0.01 ? translated('less than one cent') : window.LightworkI18n
      ? window.LightworkI18n.number(value, {style:'currency', currency:'USD'}) : '$' + value.toFixed(2);
  }
  function show(data) {
    result.replaceChildren();
    const verdict = make('section', 'cs-viewpoints__verdict');
    verdict.append(make('h3', null, 'Verdict'), make('p', null, VERDICT[data.verdict]));
    if (data.reason) { const reason = make('p', null, 'Why:'); reason.append(document.createTextNode(' '), make('span', null, data.reason, true)); verdict.append(reason); }
    if (data.key_point) { const point = make('p', null, 'The point that decided it:'); point.append(document.createTextNode(' '), make('span', null, data.key_point, true)); verdict.append(point); }
    result.append(verdict);
    for (const side of ['for', 'against']) {
      const turns = data.turns.filter(turn => turn.side === side);
      if (!turns.length) continue;
      const block = make('section', 'cs-viewpoints__side');
      block.append(make('h3', null, SIDE[side]));
      const list = make('ol');
      turns.forEach(turn => list.append(make('li', null, turn.text, true)));
      block.append(list);
      result.append(block);
    }
    const summary = make('p', 'muted');
    product(summary, 'This cost {cost}. It is advice to help you decide; nothing was started or changed.', {cost:money(data.cost)});
    result.append(summary);
    result.hidden = false;
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    const text = question.value.trim();
    if (text.length < 3) {
      say('Type a question or decision to weigh up.');
      question.focus();
      return;
    }
    busy = true;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    result.hidden = true;
    result.replaceChildren();
    say('Getting two viewpoints. This can take a minute; you can keep using the page…');
    try {
      const sides = {};
      const forSide = document.getElementById('vp-for'), againstSide = document.getElementById('vp-against');
      if (forSide && forSide.value.trim()) sides.for_side = forSide.value.trim().slice(0, 300);
      if (againstSide && againstSide.value.trim()) sides.against_side = againstSide.value.trim().slice(0, 300);
      const response = await fetch('/api/v1/viewpoints/jobs', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(Object.assign({ question: text.slice(0, 2000), rounds: Number(rounds.value) || 2 }, sides))
      });
      if (!response.ok) { say(failText(response)); return; }
      const started = await response.json();
      if (!started || typeof started.job !== 'string' || !/^[a-f0-9]{32}$/.test(started.job)) {
        say('The request couldn’t be checked. Please try again.'); return;
      }
      let data = null;
      for (let tries = 0; tries < 300; tries += 1) {
        await new Promise(resolve => setTimeout(resolve, tries ? 2000 : 500));
        const poll = await fetch('/api/v1/viewpoints/jobs/' + started.job, {
          credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }
        });
        if (!poll.ok) { say(failText(poll)); return; }
        const job = await poll.json();
        if (job && job.state === 'running') continue;
        if (job && job.state === 'failed') { say((job.result && job.result.message) || 'This couldn’t be finished. Try again.'); return; }
        data = job && job.result;
        break;
      }
      if (!valid(data)) { say('The answer couldn’t be checked. Please try again.'); return; }
      if (data.ran === false) { say(data.message || 'This couldn’t be started. Please try again.'); return; }
      show(data);
      say('Two viewpoints are ready below.');
    } catch (error) {
      say(failText(error));
    } finally {
      busy = false;
      button.disabled = false;
      button.removeAttribute('aria-busy');
      status.focus();
    }
  });
})();
