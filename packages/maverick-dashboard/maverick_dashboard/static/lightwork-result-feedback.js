/* One-tap feedback on a finished result outside the task page (AI-10). It
   saves through the task page's own saved-answer feedback
   (/api/v1/goals/{id}/answer-feedback), so the same owner check, receipt and
   learning policy apply. A lost reply is retried only with the same
   operation reference, so one tap is never counted twice. */
(function () {
  'use strict';
  function say(node, text) {
    if (!node) return;
    if (window.LightworkI18n) window.LightworkI18n.set(node, text); else node.textContent = text;
  }
  function bind(box) {
    var goal = Number(box.dataset.goal);
    if (!Number.isSafeInteger(goal) || goal <= 0 || box.dataset.feedbackReady) return;
    box.dataset.feedbackReady = 'true';
    var msg = box.querySelector('[data-feedback-msg]');
    var buttons = Array.prototype.slice.call(box.querySelectorAll('[data-rating]'));
    var url = '/api/v1/goals/' + goal + '/answer-feedback', key = 'lightwork-result-feedback:' + goal;
    function pending() { try { return JSON.parse(window.sessionStorage.getItem(key) || 'null'); } catch (_) { return null; } }
    function keep(value) { try { if (value) window.sessionStorage.setItem(key, JSON.stringify(value)); else window.sessionStorage.removeItem(key); } catch (_) { /* best effort */ } }
    async function rate(rating) {
      buttons.forEach(function (button) { button.disabled = true; });
      say(msg, 'Saving your feedback…');
      try {
        var current = await window.mvFetchJSON(url, {cache: 'no-store'}, 'read this result');
        if (!current || !current.eligible) { say(msg, 'This result cannot be rated here. Open it to give feedback.'); return; }
        var attempt = pending();
        if (!attempt || attempt.rating !== rating || attempt.revision !== current.revision) {
          attempt = {id: window.crypto.randomUUID().replace(/-/g, ''), rating: rating, revision: current.revision};
        }
        keep(attempt);
        var value = await window.mvFetchJSON(url, {method: 'POST', cache: 'no-store', body: {rating: rating, note: '', reason: '',
          revision: current.revision, expected_context: current.context, operation_id: attempt.id}}, 'save your feedback');
        keep(null);
        buttons.forEach(function (button) { button.setAttribute('aria-pressed', String(button.dataset.rating === rating)); });
        if (msg) msg.textContent = (value && value.acknowledgement) || 'Thanks. Saved with this task.';
        if (rating === 'down' && msg) {
          var more = document.createElement('a'); more.href = '/chat/goal/' + goal + '#feedback';
          say(more, 'Say what was wrong');
          msg.append(' ', more);
        }
      } catch (error) {
        say(msg, window.mvErrorText ? window.mvErrorText(error, 'save your feedback') : 'Could not save your feedback. Try again.');
      } finally {
        buttons.forEach(function (button) { button.disabled = false; });
      }
    }
    buttons.forEach(function (button) { button.addEventListener('click', function () { rate(button.dataset.rating); }); });
  }
  function init() { document.querySelectorAll('[data-result-feedback]').forEach(bind); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
}());
