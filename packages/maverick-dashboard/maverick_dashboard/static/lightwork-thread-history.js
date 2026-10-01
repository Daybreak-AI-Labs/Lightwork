(function () {
  'use strict';
  var root = document.getElementById('earlier-history'); if (!root) return;
  var load = document.getElementById('history-load'), status = document.getElementById('history-status');
  var host = document.getElementById('history-turns'), before = root.dataset.before, seen = new Set();
  load.addEventListener('click', async function () {
    if (load.disabled) return;
    load.disabled = true; status.textContent = 'Loading earlier conversation…';
    var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, 12000);
    try {
      var response = await fetch('/api/v1/goals/' + root.dataset.goal + '/thread-history' + (before ? '?before_id=' + before : ''), {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      if (!response.ok) {
        if ([401, 403, 404].includes(response.status)) host.replaceChildren();
        throw new Error('unavailable');
      }
      var data = await response.json();
      if (!Array.isArray(data.turns)) throw new Error('unreadable');
      var batch = document.createDocumentFragment();
      data.turns.forEach(function (turn) {
        if (seen.has(turn.id)) return; seen.add(turn.id);
        var item = document.createElement('article'); item.className = 'msg msg--' + (turn.role === 'user' ? 'user' : 'agent');
        var label = document.createElement('strong'); label.textContent = turn.role === 'user' ? 'You' : 'Lightwork';
        var text = document.createElement('div'); text.className = 'prose'; text.textContent = turn.content;
        var link = document.createElement('a'); link.href = '/chat/goal/' + turn.goal_id; link.textContent = 'Open this turn';
        item.append(label, text, link); batch.append(item);
      });
      host.prepend(batch); before = data.before_id; load.hidden = !data.has_more;
      status.textContent = data.has_more ? 'Earlier messages loaded. More history is available.' : 'Beginning of this conversation.';
    } catch (_) { status.textContent = 'Earlier history could not be loaded. Retry; the current conversation and draft have been kept.'; }
    finally { clearTimeout(timer); load.disabled = false; }
  });
}());
