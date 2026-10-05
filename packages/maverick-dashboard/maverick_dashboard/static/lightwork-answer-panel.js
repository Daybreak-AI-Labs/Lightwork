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
    var comment = panel.querySelector('#feedback-note');
    return !!((editor && (editor.readOnly || editor.value !== editor.dataset.savedText)) || (note && note.value.trim()) ||
      (comment && comment.value.trim()));
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
        window.mvFetchJSON('/api/v1/goals/' + goalId + '/signoff', {
          method: 'POST',
          body: {
            decision: decision,
            expected_updated_at: reviewedUpdatedAt,
            note: (note && note.value.trim()) || null,
          },
        }, 'record the approval').then(function () {
          panel.querySelector('#signoff-note').value = ''; window.mvRefreshAnswerPanel();
        }).catch(function (e) {
          msg.style.color = 'var(--danger)';
          if (e.status) window.mvRenderError(msg, e, 'record the approval', e.payload || {});
          else say(msg, cgErr(e, 'record the approval'));
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
      // A thumbs-down offers an optional reason (chips plus a private comment)
      // that is one tap to skip; the server says where the feedback went.
      var reasonForm = panel.querySelector('#feedback-reason');
      var reasonQ = panel.querySelector('#feedback-reason-q');
      var note = panel.querySelector('#feedback-note');
      var chosen = '';
      function closeReasons() { if (reasonForm) reasonForm.hidden = true; chosen = ''; }
      function openReasons(followUp) {
        if (!reasonForm) return;
        reasonForm.hidden = false;
        reasonForm.querySelectorAll('[data-reason]').forEach(function (chip) { chip.setAttribute('aria-pressed', 'false'); });
        chosen = '';
        // After repeated rejections of this agent, one short open question instead.
        if (followUp && reasonQ) { say(reasonQ, followUp.question); if (note) note.focus(); }
        else { var first = reasonForm.querySelector('[data-reason]'); if (first) first.focus(); }
      }
      if (reasonForm) {
        reasonForm.querySelectorAll('[data-reason]').forEach(function (chip) {
          chip.addEventListener('click', function () {
            chosen = chip.getAttribute('aria-pressed') === 'true' ? '' : chip.dataset.reason;
            reasonForm.querySelectorAll('[data-reason]').forEach(function (other) {
              other.setAttribute('aria-pressed', other.dataset.reason === chosen ? 'true' : 'false');
            });
          });
        });
        var skip = reasonForm.querySelector('#feedback-reason-skip');
        if (skip) skip.addEventListener('click', function () { closeReasons(); if (down) down.focus(); });
        reasonForm.addEventListener('submit', function (event) {
          event.preventDefault();
          var text = note ? note.value.trim() : '';
          if (!chosen && !text) { closeReasons(); if (down) down.focus(); return; }
          submit('down', {reason: chosen || null, note: text || null}, true);
        });
      }
      function submit(rating, extra, explaining) {
        [up, down].forEach(function (b) { if (b) b.disabled = true; });
        msg.style.color = 'var(--muted)';
        say(msg, 'Recording…');
        window.mvFetchJSON('/api/v1/goals/' + goalId + '/feedback', {
          method: 'POST', body: Object.assign({rating: rating}, extra || {}),
        }, 'save your feedback').then(function (j) {
          [up, down].forEach(function (b) { if (b) b.disabled = false; });
          show(rating);
          say(msg, (j && typeof j.acknowledgement === 'string' && j.acknowledgement) ||
            "Thanks. Your workspace's learning policy decides whether this is used to improve this agent.");
          if (explaining) { if (note) note.value = ''; closeReasons(); if (down) down.focus(); }
          else if (rating === 'down') openReasons(j && j.follow_up);
          else closeReasons();
        }).catch(function (e) {
          [up, down].forEach(function (b) { if (b) b.disabled = false; });
          msg.style.color = 'var(--danger)';
          if (e.status) window.mvRenderError(msg, e, 'save your feedback', e.payload || {});
          else say(msg, cgErr(e, 'save your feedback'));
        });
      }
      if (up) up.addEventListener('click', function () { submit('up'); });
      if (down) down.addEventListener('click', function () { submit('down'); });
    })();

    // One source and one handler for current-answer actions, including a live refresh.
    (function () {
      var copy = panel.querySelector('#copy-result');
      var print = panel.querySelector('#print-result');
      var share = panel.querySelector('#share-open');
      var saved = panel.querySelector('#saved-answer-source');
      var status = panel.querySelector('#answer-action-msg');
      function selectSavedAnswer() {
        saved.hidden = false; saved.focus(); saved.select();
        say(status, 'Copy was unavailable. The saved answer is selected; press Ctrl+C or Command+C.');
      }
      if (copy) copy.addEventListener('click', function () {
        var text = saved && saved.value;
        if (!text) { say(status, 'There is no saved answer to copy yet.'); return; }
        if (!navigator.clipboard || !navigator.clipboard.writeText) {
          selectSavedAnswer();
          return;
        }
        navigator.clipboard.writeText(text).then(function () { say(status, 'Copied.'); })
          .catch(selectSavedAnswer);
      });
      if (print) print.addEventListener('click', function () {
        if (window.LightworkThreadHistory) window.LightworkThreadHistory.printConversation(status);
        else say(status, 'Printing is still loading. Try again in a moment, or reload this conversation.');
      });
      if (share) share.addEventListener('click', function () {
        var details = document.getElementById('share-panel');
        if (!details) { say(status, 'Sharing is unavailable. Reload this conversation and try again.'); return; }
        details.open = true; share.setAttribute('aria-expanded', 'true');
        details.scrollIntoView({block:'nearest'});
        var create = details.querySelector('#share-create');
        if (create && !create.disabled) create.focus();
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
          say(editMsg, 'Revision saved. Previous versions are kept; any earlier approval was cleared. Learning is a separate, policy-governed process.');
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
    var d = event.detail || {}, statusChanged = !!d.status && d.status !== lastStatus;
    var changed = statusChanged || d.result !== lastResult;
    lastStatus = d.status || lastStatus; lastResult = d.result;
    if (statusChanged && !d.terminal) {
      // A restarted task invalidates an older terminal fetch before it can
      // restore stale answer actions over the running conversation.
      generation++; if (inFlight) inFlight.abort(); inFlight = null; forceAgain = false;
      refresh(false); return;
    }
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
