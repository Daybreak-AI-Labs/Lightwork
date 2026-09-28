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

  function make(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function say(text) { status.textContent = text; }
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
    return value < 0.01 ? 'less than one cent' : '$' + value.toFixed(2);
  }
  function show(data) {
    result.replaceChildren();
    const verdict = make('section', 'cs-viewpoints__verdict');
    verdict.append(make('h3', null, 'Verdict'), make('p', null, VERDICT[data.verdict]));
    if (data.reason) verdict.append(make('p', null, 'Why: ' + data.reason));
    if (data.key_point) verdict.append(make('p', null, 'The point that decided it: ' + data.key_point));
    result.append(verdict);
    for (const side of ['for', 'against']) {
      const turns = data.turns.filter(turn => turn.side === side);
      if (!turns.length) continue;
      const block = make('section', 'cs-viewpoints__side');
      block.append(make('h3', null, SIDE[side]));
      const list = make('ol');
      turns.forEach(turn => list.append(make('li', null, turn.text)));
      block.append(list);
      result.append(block);
    }
    result.append(make('p', 'muted', 'This cost ' + money(data.cost) + '. It is advice to help you decide; nothing was started or changed.'));
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
    say('Getting two viewpoints. This can take a minute…');
    try {
      const response = await fetch('/api/v1/viewpoints', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ question: text.slice(0, 2000), rounds: Number(rounds.value) || 2 })
      });
      if (!response.ok) { say(failText(response)); return; }
      const raw = await response.text();
      let data = null;
      try { data = raw.length <= 256 * 1024 ? JSON.parse(raw) : null; } catch (_) { data = null; }
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
