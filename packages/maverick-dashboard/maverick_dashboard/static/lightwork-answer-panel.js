// Saved result projection; the composer and conversation history are never replaced.
(function () {
  'use strict';
  var panel = document.getElementById('answer-panel'); if (!panel) return;
  var goalId = panel.dataset.goal, context = panel.dataset.context;
  var message = document.getElementById('answer-refresh-status'), retry = document.getElementById('answer-refresh');
  var inFlight = null, generation = 0, lastStatus = panel.dataset.status, lastResult = null;
  var denied = false, forceAgain = false;
  function say(node, source, values) {
    if (window.LightworkI18n) window.LightworkI18n.set(node, source, values);
    else node.textContent = source.replace(/\{(\w+)\}/g, function (_, key) { return values && values[key] !== undefined ? values[key] : _; });
  }
  function sourceText(node, value) { node.textContent = value; node.lang = 'und'; node.dir = 'auto'; node.dataset.userContent = '1'; }
  function cgErr(failure, action) { return window.mvErrorText(failure, action); }
  function dirty() {
    var editor = panel.querySelector('#edit-text'), note = panel.querySelector('#signoff-note');
    return !!((editor && (editor.readOnly || editor.value !== editor.dataset.savedText)) || (note && note.value.trim()));
  }
  function clearPrivate() {
    denied = true; generation++;
    if (inFlight) inFlight.abort();
    document.dispatchEvent(new CustomEvent('lightwork:answer-panel-before-update'));
    panel.replaceChildren(); panel.dataset.version = '';
    say(message, 'Access to the saved result changed. Sign in or reopen the authorized task to continue.');
    retry.hidden = true;
  }
  function bind(panel) {
    if (panel.dataset.bound === '1') return; panel.dataset.bound = '1';
    // Deliverable sign-off: record the human certify/reject decision, then
    // reload to show the signed-off state (and the hand-off download).
    (function () {
      panel.querySelectorAll('.signoff [data-when]').forEach(function (el) {
        var ts = parseFloat(el.getAttribute('data-when'));
        if (ts) el.textContent = window.mvWhen(ts);
      });
      var approve = panel.querySelector("#" + 'signoff-approve');
      var reject = panel.querySelector("#" + 'signoff-reject');
      if (!approve && !reject) return;
      var note = panel.querySelector("#" + 'signoff-note');
      var msg = panel.querySelector("#" + 'signoff-msg');
      var goalId = Number(panel.dataset.goal);
      // Bind the decision to the exact deliverable this page rendered. The
      // server rejects this version if a result or artifact changed meanwhile.
      var reviewedUpdatedAt = Number(panel.dataset.updatedAt);
      var btns = [approve, reject];
      function submit(decision) {
        btns.forEach(function (b) { if (b) b.disabled = true; });
        msg.style.color = 'var(--muted)';
        say(msg, 'Recording…');
        fetch('/api/v1/goals/' + goalId + '/signoff', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            decision: decision,
            expected_updated_at: reviewedUpdatedAt,
            note: (note && note.value.trim()) || null,
          }),
        }).then(function (r) {
          if (r.ok) { panel.querySelector('#signoff-note').value = ''; window.mvRefreshAnswerPanel(); return; }
          return r.json().catch(function () { return {}; }).then(function (j) {
            msg.style.color = 'var(--danger)';
            window.mvRenderError(msg, r, 'record the sign-off', j);
            btns.forEach(function (b) { if (b) b.disabled = false; });
          });
        }).catch(function (e) {
          msg.style.color = 'var(--danger)';
          say(msg, cgErr(e, 'record the sign-off'));
          btns.forEach(function (b) { if (b) b.disabled = false; });
        });
      }
      if (approve) approve.addEventListener('click', function () { submit('approved'); });
      if (reject) reject.addEventListener('click', function () { submit('rejected'); });
    })();

    // Thumbs up/down feedback: grounds a real outcome into the learning loop for
    // any finished result. Reflects the current rating on load; textContent only.
    (function () {
      var box = panel.querySelector("#" + 'feedback');
      if (!box) return;
      var up = panel.querySelector("#" + 'feedback-up');
      var down = panel.querySelector("#" + 'feedback-down');
      var msg = panel.querySelector("#" + 'feedback-msg');
      var goalId = Number(panel.dataset.goal);
      function show(rating) {
        [up, down].forEach(function (b) { if (b) b.classList.remove('is-active'); });
        if (rating === 'up' && up) up.classList.add('is-active');
        if (rating === 'down' && down) down.classList.add('is-active');
      }
      fetch('/api/v1/goals/' + goalId + '/feedback').then(function (r) {
        return r.ok ? r.json() : {};
      }).then(function (j) {
        if (j && j.feedback && j.feedback.rating) show(j.feedback.rating);
      }).catch(function () {});
      function submit(rating) {
        [up, down].forEach(function (b) { if (b) b.disabled = true; });
        msg.style.color = 'var(--muted)';
        say(msg, 'Recording…');
        fetch('/api/v1/goals/' + goalId + '/feedback', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({rating: rating}),
        }).then(function (r) {
          [up, down].forEach(function (b) { if (b) b.disabled = false; });
          if (r.ok) { show(rating); say(msg, "Feedback saved. Learning use follows your organization's policy."); return; }
          return r.json().catch(function () { return {}; }).then(function (j) {
            msg.style.color = 'var(--danger)';
            window.mvRenderError(msg, r, 'save your feedback', j);
          });
        }).catch(function (e) {
          [up, down].forEach(function (b) { if (b) b.disabled = false; });
          msg.style.color = 'var(--danger)';
          say(msg, cgErr(e, 'save your feedback'));
        });
      }
      if (up) up.addEventListener('click', function () { submit('up'); });
      if (down) down.addEventListener('click', function () { submit('down'); });
      var copy = panel.querySelector("#" + 'copy-result');
      if (copy) copy.addEventListener('click', function () {
        var src = panel.querySelector('#result') || panel.querySelector('.deliverable');
        var text = src ? (src.innerText || src.textContent || '').trim() : '';
        if (!text) return;
        if (window.mvCopy) { window.mvCopy(text); return; }
        if (!navigator.clipboard) return;
        navigator.clipboard.writeText(text).then(function () {
          msg.style.color = 'var(--muted)'; say(msg, 'Copied.');
        }).catch(function () {
          msg.style.color = 'var(--danger)'; say(msg, 'Could not copy.');
        });
      });
    })();

    // Edit & save the deliverable: the human's revision replaces the result and
    // its distance from the agent's draft grounds a graded learning signal.
    (function () {
      var toggle = panel.querySelector("#" + 'edit-toggle');
      if (!toggle) return;
      var box = panel.querySelector("#" + 'edit-box');
      var textEl = panel.querySelector("#" + 'edit-text');
      var saveBtn = panel.querySelector("#" + 'edit-save');
      var editMsg = panel.querySelector("#" + 'edit-msg');
      var goalId = Number(panel.dataset.goal);
      toggle.addEventListener('click', function () {
        box.style.display = box.style.display === 'none' ? 'block' : 'none';
        if (box.style.display === 'block') textEl.focus();
      });
      var reviewedAt = Number(panel.dataset.updatedAt);
      var pendingEdit = null;
      var savedText = textEl.value;
      textEl.dataset.savedText = savedText;
      saveBtn.addEventListener('click', async function () {
        if (saveBtn.disabled) return;
        if (!pendingEdit || pendingEdit.text !== textEl.value || pendingEdit.expected_updated_at !== reviewedAt) {
          pendingEdit = {text: textEl.value, expected_updated_at: reviewedAt, operation_id: crypto.randomUUID()};
        }
        saveBtn.disabled = true; textEl.readOnly = true;
        editMsg.style.color = 'var(--muted)'; say(editMsg, 'Saving.');
        try {
          var response = await fetch('/api/v1/goals/' + goalId + '/deliverable/edit', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(pendingEdit)
          });
          var receipt = await response.json();
          if (response.status === 409 && receipt.detail && receipt.detail.current_updated_at) {
            var compare = document.createElement('details');
            var heading = document.createElement('summary'); say(heading, 'Compare with the current saved result');
            var current = document.createElement('pre');
            if (receipt.detail.current_text) sourceText(current, receipt.detail.current_text); else say(current, '(empty)');
            var review = document.createElement('button'); review.type = 'button'; review.className = 'btn btn--ghost';
            say(review, 'Use this current version as the basis for my draft');
            review.addEventListener('click', function () {
              reviewedAt = receipt.detail.current_updated_at; pendingEdit = null; compare.remove();
              say(editMsg, 'Draft kept. Review it, then choose Save to apply it to the current result.');
            });
            compare.append(heading, current, review); box.append(compare); compare.open = true;
            say(editMsg, 'The result changed. Your draft is kept; compare before saving again.');
            return;
          }
          if (!response.ok || !receipt.revision) throw response;
          reviewedAt = receipt.revision; savedText = textEl.value; textEl.dataset.savedText = savedText; pendingEdit = null;
          setTimeout(function () { window.mvRefreshAnswerPanel(); }, 0);
          say(editMsg, 'Revision saved. Previous versions are kept; any earlier sign-off was cleared. Learning is a separate, policy-governed process.');
        } catch (failure) {
          editMsg.style.color = 'var(--danger)';
          say(editMsg, '{error} Your draft is kept. Retry uses the same save reference.', {error:cgErr(failure, 'save your edit')});
        } finally { saveBtn.disabled = false; textEl.readOnly = false; }
      });
    })();

    // Artifact version history: fetch on first expand, render newest-first with
    // a unified diff per version (server-computed). textContent throughout — no
    // untrusted content reaches innerHTML.
    panel.querySelectorAll('.artifact__history').forEach(function (d) {
      d.addEventListener('toggle', function () {
        if (!d.open || d.dataset.loaded) return;
        d.dataset.loaded = '1';
        const body = d.querySelector('.artifact__history-body');
        say(body, 'Loading…');
        body.classList.add('muted');
        const url = '/api/v1/goals/' + encodeURIComponent(d.dataset.goal) +
                    '/artifacts/history?title=' + encodeURIComponent(d.dataset.title);
        // W3-75: a non-2xx (auth lapsed, goal gone, server error) used to
        // render as "No history." or leave "Loading…" forever, and the loaded
        // flag meant re-opening never retried. Check the status, and on any
        // failure show why and clear the flag so the next toggle retries.
        fetch(url).then(function (r) {
          if (!r.ok) {
            return r.json().catch(function () { return {}; }).then(function (j) {
              throw new Error(cgErr(r, 'load earlier versions'));
            });
          }
          return r.json();
        }).then(function (j) {
          body.textContent = '';
          body.classList.remove('muted');
          const versions = (j.versions || []).slice().reverse();  // newest first
          if (!versions.length) { say(body, 'No history.'); return; }
          versions.forEach(function (v) {
            const wrap = document.createElement('div');
            wrap.className = 'artifact__version';
            const head = document.createElement('div');
            head.className = 'artifact__version-head';
            say(head, v.created_at ? 'Version {version} · {date}' : 'Version {version}', {version:v.version,
              date:v.created_at ? (window.LightworkI18n ? window.LightworkI18n.formatDate(v.created_at) : new Date(v.created_at * 1000).toLocaleString()) : ''});
            wrap.appendChild(head);
            if (v.diff) {
              const pre = document.createElement('pre');
              pre.className = 'artifact__diff'; pre.lang = 'und'; pre.dir = 'auto'; pre.dataset.userContent = '1';
              v.diff.split('\n').forEach(function (line) {
                const span = document.createElement('span');
                if (line[0] === '+') span.className = 'add';
                else if (line[0] === '-') span.className = 'del';
                span.textContent = line + '\n';
                pre.appendChild(span);
              });
              wrap.appendChild(pre);
            } else {
              const p = document.createElement('div');
              p.className = 'muted';
              say(p, '(initial version)');
              wrap.appendChild(p);
            }
            body.appendChild(wrap);
          });
        }).catch(function (e) {
          body.classList.add('muted');
          say(body, 'Could not load history: {error}. Close and reopen to retry.', {error:String(e && e.message || e)});
          delete d.dataset.loaded;
        });
      });
    });

  }
  async function refresh(force) {
    if (denied || document.hidden) return;
    if (inFlight) { forceAgain = true; return; }
    var ticket = ++generation, controller = new AbortController(); inFlight = controller;
    var timer = setTimeout(function () { controller.abort(); }, 12000);
    try {
      var response = await fetch('/chat/goal/' + goalId + '/answer-panel',
        {credentials:'same-origin', cache:'no-store', signal:controller.signal});
      if (ticket !== generation) return;
      if (response.redirected || [401,403,404].includes(response.status) ||
          (response.ok && response.headers.get('X-Lightwork-Context') !== context)) { clearPrivate(); return; }
      if (!response.ok) { var failure = await window.mvErrorResponse(response); throw failure; }
      var source = await response.text(); if (ticket !== generation) return;
      var parsed = new DOMParser().parseFromString(source, 'text/html');
      var next = parsed.getElementById('answer-panel');
      if (!next || next.dataset.goal !== goalId || next.dataset.context !== context ||
          parsed.querySelectorAll('#answer-panel').length !== 1 || next.querySelector('script')) throw new Error('projection unavailable');
      if (next.dataset.version === panel.dataset.version && next.dataset.operate === panel.dataset.operate) {
        retry.hidden = true; message.textContent = ''; return;
      }
      if (!force && dirty()) {
        retry.hidden = false;
        say(message, 'The saved result changed. Your unsaved edit or review note is kept. Refresh when you are ready to review the current version.');
        // A superseded result cannot be signed off through stale controls.
        panel.querySelectorAll('#signoff-approve,#signoff-reject').forEach(function (node) { node.disabled = true; });
        if (next.dataset.operate !== '1') panel.querySelectorAll('button,input,textarea').forEach(function (node) { node.disabled = true; });
        return;
      }
      var focused = document.activeElement, inside = panel.contains(focused), focusId = inside ? focused.id : '';
      var scroller = document.getElementById('cs-scroll'), scrollTop = scroller ? scroller.scrollTop : 0;
      document.dispatchEvent(new CustomEvent('lightwork:answer-panel-before-update'));
      panel.replaceWith(next); panel = next;
      bind(panel); if (window.lwMarkdown) window.lwMarkdown.upgrade(panel);
      document.dispatchEvent(new CustomEvent('lightwork:answer-panel-updated', {detail:{goalId:goalId, version:panel.dataset.version}}));
      if (inside) { var target = focusId && panel.querySelector('#' + CSS.escape(focusId));
        if (target && !target.disabled) target.focus({preventScroll:true}); }
      if (scroller) scroller.scrollTop = scrollTop;
      retry.hidden = true; say(message, 'Saved result and controls updated.');
    } catch (failure) {
      if (ticket !== generation || denied) return;
      window.mvRenderError(message, failure, 'refresh the saved result');
      var kept = document.createElement('span'); say(kept, ' Your draft has been kept.'); message.append(kept); retry.hidden = false;
    } finally {
      clearTimeout(timer); if (inFlight === controller) inFlight = null;
      if (forceAgain && !denied) { forceAgain = false; setTimeout(function () { refresh(false); }, 0); }
    }
  }
  window.mvRefreshAnswerPanel = function () { return refresh(false); };
  retry.addEventListener('click', async function () {
    if (dirty() && !(await window.mvConfirm('Replace the unsaved result edit or review note with the current saved version? Your follow-up message will be kept.', {okText:'Refresh saved result', productCopy:true}))) return;
    refresh(true);
  });
  document.addEventListener('lightwork:task-projection', function (event) {
    var d = event.detail || {}, changed = d.status !== lastStatus || d.result !== lastResult;
    lastStatus = d.status || lastStatus; lastResult = d.result;
    if (d.artifactsChanged || (d.terminal && changed)) refresh(false);
  });
  document.addEventListener('lightwork:task-access-lost', clearPrivate);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(false); });
  window.addEventListener('focus', function () { refresh(false); });
  window.addEventListener('beforeunload', function (event) { if (!denied && dirty()) { event.preventDefault(); event.returnValue = ''; } });
  var interval = setInterval(function () { if (['done','blocked','failed','cancelled'].includes(lastStatus)) refresh(false); }, 30000);
  window.addEventListener('pagehide', function () { generation++; if (inFlight) inFlight.abort(); clearInterval(interval); });
  bind(panel); refresh(false);
}());
