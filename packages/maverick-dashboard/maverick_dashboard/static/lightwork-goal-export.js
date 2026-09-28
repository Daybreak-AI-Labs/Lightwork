// Step-by-step history: share a scrubbed private copy of this task on GitHub.
// The download link needs no script; this only handles the confirmed upload.
(() => {
  'use strict';
  const root = document.getElementById('goal-export');
  const button = document.getElementById('goal-gist');
  if (!root || !button) return;
  const goal = root.dataset.goal || '';
  if (!/^\d+$/.test(goal)) return;
  const msg = document.getElementById('goal-export-msg');
  function say(text) { msg.textContent = text; }
  button.addEventListener('click', async () => {
    if (button.disabled) return;
    const ok = typeof window.mvConfirm === 'function' && await window.mvConfirm(
      'Upload a copy of this task’s history to GitHub as a private gist? Secrets are removed first, but anyone you give the link to can read it.',
      { okText: 'Upload to GitHub' });
    if (!ok) return;
    button.disabled = true;
    say('Uploading…');
    try {
      const response = await fetch('/api/v1/goals/' + goal + '/share/gist', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: true })
      });
      if (!response.ok) { say(window.mvErrorText ? window.mvErrorText(response, 'share this task') : 'Could not share this task.'); return; }
      const data = await response.json();
      if (!data || data.shared !== true || typeof data.url !== 'string' || !/^https:\/\//.test(data.url)) {
        say(data && typeof data.message === 'string' ? data.message : 'Nothing was shared.');
        return;
      }
      msg.replaceChildren('Shared as a private gist: ');
      const link = document.createElement('a');
      link.href = data.url; link.rel = 'noreferrer noopener'; link.target = '_blank'; link.textContent = 'open it on GitHub';
      msg.append(link, '. Only people with the link can see it.');
    } catch (error) {
      say(window.mvErrorText ? window.mvErrorText(error, 'share this task') : 'Could not share this task.');
    } finally { button.disabled = false; }
  });
})();
