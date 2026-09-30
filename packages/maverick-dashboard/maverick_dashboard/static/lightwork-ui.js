/* Lightwork dashboard shell behaviors. Extracted from templates/base.html;
   served by GET /static/lightwork-ui.js. Loaded at the end of <body>, so
   the DOM is ready without defer. First section: reusable feedback
   primitives (mvToast/mvConfirm/mvForm/mvCopy). Second: halt pill, goal
   form, sidebar + preferences behaviors. */
    // Bind every same-origin browser mutation to the workspace this document
    // was rendered under. A selection changed in another tab then receives a
    // server-side 409 instead of writing into the newly selected tenant.
    (function () {
      var meta = document.querySelector('meta[name="lightwork-workspace-tenant"]');
      var expected = meta ? String(meta.getAttribute('content') || '').trim() : '';
      window.mvWorkspaceTenant = expected;
      if (!expected) return;

      var contextChanged = false;
      function invalidateWorkspace() {
        if (contextChanged) return;
        contextChanged = true;
        var notice = document.createElement('section');
        notice.className = 'panel'; notice.setAttribute('role', 'alert');
        notice.textContent = 'Your workspace changed in another tab. Changes here are paused. Keep any draft you need, then reopen this page in the current workspace. ';
        var reopen = document.createElement('a'); reopen.href = window.location.href;
        reopen.textContent = 'Reopen in current workspace'; notice.appendChild(reopen);
        document.body.insertBefore(notice, document.body.firstChild);
        window.dispatchEvent(new CustomEvent('lightwork:context-changed'));
      }
      window.addEventListener('storage', function (event) {
        if (event.key === 'lightwork-workspace-changed') invalidateWorkspace();
      });
      var nativeFetch = window.fetch.bind(window);
      window.fetch = function (input, init) {
        var opts = init ? Object.assign({}, init) : {};
        var method = String(opts.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        var unsafe = !['GET', 'HEAD', 'OPTIONS'].includes(method);
        var rawUrl = input instanceof Request ? input.url : input;
        var sameOrigin = false;
        try { sameOrigin = new URL(String(rawUrl), window.location.href).origin === window.location.origin; }
        catch (e) { sameOrigin = false; }
        if (!unsafe || !sameOrigin) return nativeFetch(input, init);
        if (contextChanged) return Promise.reject(new Error('Workspace changed. Reopen this page before saving.'));

        var headers = new Headers(opts.headers || (input instanceof Request ? input.headers : undefined));
        if (!headers.has('X-Lightwork-Expected-Tenant')) {
          headers.set('X-Lightwork-Expected-Tenant', expected);
        }
        opts.headers = headers;
        if (input instanceof Request) return nativeFetch(new Request(input, opts));
        return nativeFetch(input, opts);
      };

      document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        var method = String(form.method || 'get').toUpperCase();
        if (['GET', 'HEAD', 'OPTIONS'].includes(method)) return;
        var sameOrigin = false;
        try { sameOrigin = new URL(form.action || window.location.href, window.location.href).origin === window.location.origin; }
        catch (e) { sameOrigin = false; }
        if (!sameOrigin) return;
        if (contextChanged) { event.preventDefault(); event.stopImmediatePropagation(); return; }
        var field = form.querySelector('input[name="_expected_tenant"]');
        if (!field) {
          field = document.createElement('input');
          field.type = 'hidden'; field.name = '_expected_tenant';
          form.appendChild(field);
        }
        field.value = expected;
      }, true);
    })();

    // Reusable feedback primitives, on every page that extends base.html.
    // Prefer these over the browser's native dialogs. Contract: docs/ui-primitives.md.
    //   window.mvToast(message, {error}) -> transient status/alert toast
    //   window.mvConfirm(message, {okText}) -> Promise<bool> (native <dialog>;
    //     focus-returning, not re-entrant; falls back to window.confirm)
    //   window.mvCopy(text) -> copy to clipboard with a toast (+ fallback)
    (function () {
      var region = document.getElementById('mv-toasts');
      var alertRegion = document.getElementById('mv-toasts-alert');
      window.mvToast = function (message, opts) {
        opts = opts || {};
        // Route to a region whose live politeness is fixed at load (an injected
        // role can't reliably upgrade a polite region to assertive); prefix
        // errors so the state survives grayscale / color-blindness.
        var target = opts.error ? alertRegion : region;
        if (!target) return;
        var t = document.createElement('div');
        t.className = 'mv-toast' + (opts.error ? ' mv-toast--error' : '');
        t.textContent = (opts.error ? '⚠ ' : '') + String(message);
        target.appendChild(t);
        setTimeout(function () {
          t.style.transition = 'opacity 200ms'; t.style.opacity = '0';
          setTimeout(function () { t.remove(); }, 220);
        }, opts.error ? 5000 : 3200);
      };
      var dlg = document.getElementById('mv-confirm');
      var msgEl = document.getElementById('mv-confirm-msg');
      var okBtn = document.getElementById('mv-confirm-ok');
      var cancelBtn = document.getElementById('mv-confirm-cancel');
      window.mvConfirm = function (message, opts) {
        opts = opts || {};
        if (!dlg || typeof dlg.showModal !== 'function') {
          return Promise.resolve(window.confirm(String(message)));
        }
        if (dlg.open) return Promise.resolve(false);   // not re-entrant (shared dialog)
        var opener = document.activeElement;           // restore focus on close
        msgEl.textContent = String(message);
        okBtn.textContent = opts.okText || 'Confirm';
        return new Promise(function (resolve) {
          function done(val) {
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            dlg.removeEventListener('cancel', onCancel);
            if (dlg.open) dlg.close();
            // Return focus to the opener when it survives; a delete that removed
            // its own row leaves the caller to re-home focus to a stable spot.
            if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
            resolve(val);
          }
          function onOk() { done(true); }
          function onCancel(e) { if (e) e.preventDefault(); done(false); }
          okBtn.addEventListener('click', onOk);
          cancelBtn.addEventListener('click', onCancel);
          dlg.addEventListener('cancel', onCancel);   // Esc / backdrop
          dlg.showModal();
          cancelBtn.focus();
        });
      };
      // Collect a small set of typed fields in a modal form (replaces per-value
      // window.prompt chains). fields: [{key,label,type,required,default,options,
      // placeholder,help}] where type is text|number|date|bool|select. Resolves to
      // a {key:value} object, or null if cancelled. Falls back to sequential
      // prompts where <dialog> is unsupported.
      var fdlg = document.getElementById('mv-form');
      function booleanDefault(value) {
        // Imported fields can carry serialized booleans. Only an explicit
        // affirmative value may preselect a boolean; "false" is not truthy here.
        return value === true || value === 1 ||
          (typeof value === 'string' && /^(true|yes|1)$/i.test(value.trim()));
      }
      window.mvForm = function (title, fields, opts) {
        opts = opts || {};
        fields = fields || [];
        if (!fdlg || typeof fdlg.showModal !== 'function') {
          var out0 = {};
          for (var i = 0; i < fields.length; i++) {
            var s = fields[i];
            var promptLabel = (s.label || s.key) + (s.required ? ' *' : '');
            var boolField = s.type === 'bool' || s.type === 'checkbox';
            var choices = (s.options || []).map(function (o) {
              return String(o && o.value != null ? o.value : o);
            });
            if (boolField) promptLabel += ' (true or false)';
            if (s.type === 'select') promptLabel += ' (' + choices.join(', ') + ')';
            var problem = '';
            while (true) {
              var v0 = window.prompt((problem ? problem + '\n' : '') + promptLabel,
                boolField ? String(booleanDefault(s.default)) : s.default != null ? String(s.default) : '');
              if (v0 === null) return Promise.resolve(null);
              v0 = v0.trim();
              if (!v0 && s.required) { problem = 'This field is required.'; continue; }
              if (boolField) {
                if (!/^(true|false|yes|no|1|0)$/i.test(v0)) {
                  problem = 'Enter true or false.'; continue;
                }
                out0[s.key] = /^(true|yes|1)$/i.test(v0);
              } else if (v0) {
                if (s.type === 'number' && !Number.isFinite(Number(v0))) {
                  problem = 'Enter a valid number.'; continue;
                }
                if (s.type === 'select' && choices.indexOf(v0) < 0) {
                  problem = 'Choose one of the listed values.'; continue;
                }
                out0[s.key] = v0;
              }
              break;
            }
          }
          return Promise.resolve(out0);
        }
        if (fdlg.open) return Promise.resolve(null);   // shared dialog, not re-entrant
        var opener = document.activeElement;
        var form = document.getElementById('mv-form-form');
        var body = document.getElementById('mv-form-fields');
        var errEl = document.getElementById('mv-form-err');
        var cancelBtn = document.getElementById('mv-form-cancel');
        document.getElementById('mv-form-title').textContent = String(title || 'Details');
        document.getElementById('mv-form-ok').textContent = opts.okText || 'OK';
        body.textContent = ''; errEl.textContent = '';
        var controls = [];
        fields.forEach(function (f) {
          var id = 'mvf-' + f.key;
          var wrap = document.createElement('div'); wrap.className = 'mv-form__field';
          var ctl;
          if (f.type === 'bool' || f.type === 'checkbox') {
            wrap.className += ' mv-form__check';
            ctl = document.createElement('input'); ctl.type = 'checkbox'; ctl.checked = booleanDefault(f.default); ctl.id = id;
            var l1 = document.createElement('label'); l1.setAttribute('for', id); l1.textContent = f.label || f.key;
            wrap.appendChild(ctl); wrap.appendChild(l1);
          } else {
            var lab = document.createElement('label'); lab.setAttribute('for', id);
            lab.textContent = (f.label || f.key) + (f.required ? ' *' : '');
            wrap.appendChild(lab);
            if (f.type === 'select') {
              ctl = document.createElement('select');
              (f.options || []).forEach(function (o) {
                var op = document.createElement('option');
                op.value = (o && o.value != null) ? o.value : o;
                op.textContent = (o && o.label != null) ? o.label : o;
                ctl.appendChild(op);
              });
              if (f.default != null) ctl.value = String(f.default);
            } else {
              ctl = document.createElement('input');
              ctl.type = f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text';
              if (f.type === 'number') ctl.step = 'any';
              ctl.className = 'input';
              if (f.default != null) ctl.value = String(f.default);
              if (f.placeholder) ctl.placeholder = f.placeholder;
            }
            ctl.id = id;
            ctl.required = !!f.required;
            wrap.appendChild(ctl);
          }
          if (f.help) {
            var h = document.createElement('div'); h.className = 'mv-form__help';
            h.id = id + '-help'; h.textContent = f.help;
            ctl.setAttribute('aria-describedby', h.id); wrap.appendChild(h);
          }
          controls.push({ f: f, ctl: ctl });
          body.appendChild(wrap);
        });
        return new Promise(function (resolve) {
          function collect() {
            var out = {}, missing = [], firstMissing = null;
            controls.forEach(function (c) {
              c.ctl.removeAttribute('aria-invalid');
              if (c.f.type === 'bool' || c.f.type === 'checkbox') { out[c.f.key] = c.ctl.checked; return; }
              var val = (c.ctl.value || '').trim();
              if (!val && c.f.required) {
                missing.push(c.f.label || c.f.key); c.ctl.setAttribute('aria-invalid', 'true');
                if (!firstMissing) firstMissing = c.ctl;
                return;
              }
              if (val) out[c.f.key] = val;
            });
            if (missing.length) {
              errEl.textContent = 'Required: ' + missing.join(', ');
              firstMissing.focus(); return null;
            }
            return out;
          }
          function done(val) {
            form.removeEventListener('submit', onSubmit);
            cancelBtn.removeEventListener('click', onCancel);
            fdlg.removeEventListener('cancel', onCancel);
            if (fdlg.open) fdlg.close();
            if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
            resolve(val);
          }
          function onSubmit(e) { e.preventDefault(); var v = collect(); if (v !== null) done(v); }
          function onCancel(e) { if (e) e.preventDefault(); done(null); }
          form.addEventListener('submit', onSubmit);
          cancelBtn.addEventListener('click', onCancel);
          fdlg.addEventListener('cancel', onCancel);   // Esc / backdrop
          fdlg.showModal();
          var first = body.querySelector('input, select'); if (first) first.focus();
        });
      };
      // Copy text to the clipboard with a toast; falls back for old browsers.
      window.mvCopy = function (text) {
        function ok() { mvToast('Copied to clipboard'); }
        function fail() { mvToast('Copy failed — select and copy manually', { error: true }); }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(String(text)).then(ok, fail);
          return;
        }
        var ta, opener = document.activeElement;
        try {
          ta = document.createElement('textarea');
          ta.value = String(text); ta.style.position = 'fixed'; ta.style.opacity = '0';
          document.body.appendChild(ta); ta.select();
          if (document.execCommand('copy')) ok(); else fail();
        } catch (e) { fail(); }
        finally {
          if (ta) ta.remove();
          if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
        }
      };
      // Small DOM builder + a uniform timestamp formatter, shared by the
      // list-rendering pages (was reimplemented per page).
      window.mvEl = function (tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
      };
      // Plain-language error text for a failed request. Pass the fetch
      // Response (or a thrown error) and, optionally, what the person was
      // trying to do ("save the policy"). Never shows HTTP codes, exception
      // class names or server internals.
      window.mvErrorText = function (failure, action) {
        var what = action ? ('Could not ' + action + '. ') : '';
        var status = failure && typeof failure.status === 'number' ? failure.status : 0;
        if (!status) {
          var name = failure && failure.name;
          if (name === 'AbortError') return what + 'It took too long. Please try again.';
          if (name === 'TypeError' || (failure && /fetch|network/i.test(String(failure.message || failure)))) {
            return what + 'Lightwork could not be reached. Check your connection and try again.';
          }
          return what + 'Something went wrong. Please try again.';
        }
        if (status === 400 || status === 422) return what + 'Some of the details need fixing. Check what you entered and try again.';
        if (status === 401) return what + 'Your sign-in has expired. Sign in again to continue.';
        if (status === 403) return what + 'You do not have permission to do this. Ask an administrator.';
        if (status === 404) return what + 'That item no longer exists. Refresh the page to see the latest.';
        if (status === 409) return what + 'This was changed since you opened it. Refresh the page and try again.';
        if (status === 413) return what + 'That is too large. Try a smaller file or fewer items.';
        if (status === 429) return what + 'Too many requests right now. Wait a moment and try again.';
        if (status === 503) {
          // Only say "turned off" when the caller knows the server said so
          // (featureOff flag or its detail text); a plain 503 is temporary.
          var off = failure.featureOff === true ||
            /\b(turned off|is disabled|not enabled)\b/i.test(typeof failure.detail === 'string' ? failure.detail : '');
          return what + (off
            ? 'This feature is turned off. An administrator can turn it on in Settings.'
            : 'This is temporarily unavailable. Please try again in a few minutes.');
        }
        if (status >= 500) return what + 'Something went wrong on our side. Please try again in a minute.';
        return what + 'Something went wrong. Please try again.';
      };
      // A server message is shown only when it is already a plain sentence
      // written for people ("Pick a later date."). Codes, snake_case names,
      // paths, data and developer words return '' so the caller falls back
      // to mvErrorText.
      var JARGON = /\b(tenant|principal|payload|endpoint|schema|uuid|sha-?\d*|digest|idempotency|revision|lease|json|yaml|toml|http|https|api|config|configuration|token|traceback|exception|null|undefined|nan|maverick|sqlite|postgres|stdout|stderr|regex|cas|etag|manifest|runtime|webhook)\b/i;
      window.mvPlainDetail = function (detail) {
        if (typeof detail !== 'string') return '';
        var text = detail.trim();
        if (text.length < 8 || text.length > 300) return '';
        if (!/^[A-Z]/.test(text) || !/[.!?]$/.test(text)) return '';
        if (/[_{}<>\[\]`\\|=]|\/\w|\w\(|\b[a-z]+[A-Z]\w*\b|(?:^|[^\d,.])[45]\d\d(?![\d,])/.test(text)) return '';
        if (JARGON.test(text)) return '';
        return text;
      };
      // Show that error as a toast; returns the text for callers that also
      // write it into the page.
      window.mvFail = function (failure, action) {
        var text = window.mvErrorText(failure, action);
        mvToast(text, { error: true });
        return text;
      };
      // A stored value (status, kind, slug) as a readable label:
      // "pending_review" -> "Pending review", "ai_act" -> "AI Act".
      var WORDS = { ai: 'AI', api: 'API', id: 'ID', ids: 'IDs', url: 'URL', sso: 'SSO',
        dsar: 'DSAR', gdpr: 'GDPR', ccpa: 'CCPA', hipaa: 'HIPAA', soc2: 'SOC 2', pii: 'personal data',
        sla: 'SLA', mfa: 'MFA', eu: 'EU', us: 'US', uk: 'UK', llm: 'AI model', mcp: 'MCP',
        sha256: 'fingerprint', csv: 'CSV', pdf: 'PDF', iso: 'ISO', nist: 'NIST', dpia: 'DPIA',
        ropa: 'record of processing', cmp: 'consent banner', ok: 'OK' };
      window.mvHuman = function (value) {
        if (value === null || value === undefined) return '';
        var words = String(value).trim().replace(/[_-]+/g, ' ').split(/\s+/).filter(Boolean);
        if (!words.length) return '';
        var out = words.map(function (w) {
          var key = w.toLowerCase();
          return Object.prototype.hasOwnProperty.call(WORDS, key) ? WORDS[key] : key;
        }).join(' ');
        return out.charAt(0).toUpperCase() + out.slice(1);
      };
      window.mvWhen = function (ts) {
        // Return '' for missing/invalid timestamps rather than formatting the
        // Unix epoch: (ts || 0) would turn 0/null/undefined/NaN into 1/1/1970,
        // which then leaks into visible text and aria-labels.
        if (!ts || !isFinite(ts)) return '';
        try { return new Date(ts * 1000).toLocaleString(); } catch (e) { return ''; }
      };
    })();

    // Halt indicator + arm/clear via /api/v1/halt.
    (function() {
      const pill = document.getElementById('halt-pill');
      const label = document.getElementById('halt-label');
      if (!pill || !label) return;
      const canHalt = pill.dataset.canHalt === 'true';
      const configuredTimeout = Number(pill.dataset.haltTimeoutMs);
      const haltTimeoutMs = Number.isFinite(configuredTimeout) && configuredTimeout > 0
        ? Math.min(configuredTimeout, 10000)
        : 10000;
      // A failed read is not equivalent to a cleared stop.  Keep an explicit
      // unknown state so the shell can never invite a mutation based on a
      // fabricated answer.
      let haltState = 'unknown';
      // The shell learns its configured scope from the first complete status
      // response and then requires every later read to agree.  A scope change
      // needs a page reload; silently switching authorities in a live tab would
      // make the same control mean two different things.
      let haltScope = null;
      let pending = false;
      // Every status read owns a generation.  A later-started poll or the
      // post-mutation verification supersedes every older in-flight read, so a
      // slow interval request cannot repaint stale state over newer evidence.
      let refreshGeneration = 0;
      function knownState() {
        return haltState === 'armed' || haltState === 'clear';
      }
      function render(state, detail) {
        haltState = state;
        const armed = state === 'armed';
        const known = state === 'armed' || state === 'clear';
        pill.classList.toggle('armed', armed);
        pill.classList.toggle('unavailable', !known);
        label.textContent = detail || (armed
          ? (canHalt ? 'STOPPED — click to resume' : 'STOPPED')
          : state === 'clear'
            ? (canHalt ? 'Emergency stop' : 'Work is not stopped')
            : 'Stop status unavailable');
        if (canHalt) {
          pill.setAttribute('aria-pressed', known ? (armed ? 'true' : 'false') : 'mixed');
        } else {
          pill.removeAttribute('aria-pressed');
        }
        pill.setAttribute('aria-disabled', (!canHalt || !known || pending) ? 'true' : 'false');
      }
      function renderVerifiedPayload(payload) {
        if (!payload || typeof payload.active !== 'boolean' ||
            (payload.scope !== 'local' && payload.scope !== 'cluster') ||
            (payload.scope === 'cluster' && typeof payload.cluster_halt !== 'boolean')) {
          render('unknown');
          return false;
        }
        if (haltScope === null) haltScope = payload.scope;
        if (payload.scope !== haltScope) {
          render('unknown', 'Stop settings changed — reload to verify status');
          return false;
        }
        if (haltScope === 'cluster' && payload.active !== payload.cluster_halt) {
          // `active` includes the local file while `cluster_halt` is the shared
          // authority.  Disagreement is a partial/unverified stop, not proof
          // that the configured cluster scope is stopped or clear.
          render('partial', 'Partial stop — some workers are not confirmed stopped');
          return false;
        }
        render(payload.active ? 'armed' : 'clear');
        return true;
      }
      async function requestHalt(options, readBody) {
        const controller = typeof AbortController === 'function'
          ? new AbortController()
          : null;
        const requestOptions = Object.assign({cache: 'no-store'}, options || {});
        if (controller) requestOptions.signal = controller.signal;
        let timer;
        const timeout = new Promise(function(_resolve, reject) {
          timer = setTimeout(function() {
            if (controller) controller.abort();
            reject(new Error('halt request timed out'));
          }, haltTimeoutMs);
        });
        const request = (async function() {
          const response = await fetch('/api/v1/halt', requestOptions);
          const body = readBody ? await readBody(response) : undefined;
          return {response: response, body: body};
        })();
        try {
          return await Promise.race([request, timeout]);
        } finally {
          clearTimeout(timer);
        }
      }
      render('unknown', 'Checking stop status…');
      async function refresh() {
        if (pending) return false;
        const generation = ++refreshGeneration;
        try {
          const result = await requestHalt({}, function(response) {
            return response.json();
          });
          if (generation !== refreshGeneration) return false;
          const r = result.response;
          if (!r.ok) throw new Error('halt status ' + r.status);
          return renderVerifiedPayload(result.body);
        } catch (e) {
          if (generation !== refreshGeneration) return false;
          // A stale known state is unsafe after any failed, non-OK, timed-out,
          // or invalid read.  Only a complete current response may re-enable
          // the mutation control.
          render('unknown');
          return false;
        }
      }
      pill.addEventListener('click', async function(ev) {
        ev.preventDefault();
        if (!canHalt) {
          mvToast('You do not have permission to stop or resume this work.', { error: true });
          return;
        }
        if (pending || !knownState()) {
          mvToast('Emergency stop status is unavailable. Retry after the dashboard can verify it.', { error: true });
          return;
        }
        const wasArmed = haltState === 'armed';
        let responseCompleted = false;
        try {
          let requestOptions;
          if (wasArmed) {
            pending = true;
            refreshGeneration += 1;
            render(haltState, 'Resuming…');
            requestOptions = {method: 'DELETE'};
          } else {
            const coverage = haltScope === 'cluster' ? 'across connected workers' : 'on this server';
            if (!(await mvConfirm('Stop work ' + coverage + '? Work covered by this emergency stop pauses at its next step and waits for an authorized resume.', { okText: 'Stop work' }))) return;
            pending = true;
            refreshGeneration += 1;
            render(haltState, 'Stopping…');
            requestOptions = {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({reason: 'manual via dashboard'}),
            };
          }
          const result = await requestHalt(requestOptions, async function(response) {
            if (response.ok) return null;
            try { return await response.json(); } catch (parseError) { return null; }
          });
          responseCompleted = true;
          const response = result.response;
          if (!response.ok) {
            // A failed arm may still have stopped this replica, and a failed
            // clear may still have changed one backend.  Do not display the
            // pre-request state as verified until a fresh read succeeds.
            render('unknown');
            mvToast(typeof mvErrorText === 'function'
              ? mvErrorText(response, 'change the emergency stop')
              : 'Emergency stop change failed. Please try again.', { error: true });
          } else {
            // A 204 only acknowledges the mutation request.  It is not a
            // status document and cannot prove either local or cluster state.
            render('unknown', wasArmed
              ? 'Resume requested — checking work status…'
              : 'Stop requested — checking work status…');
          }
        } catch (e) {
          render('unknown');
          mvToast('Emergency stop change failed. Lightwork is not responding. Check your connection and try again.', { error: true });
        } finally {
          pending = false;
        }
        // A transport timeout has an ambiguous server outcome.  Do not let an
        // immediate read certify the old pre-request state; a later independent
        // poll must establish current truth after the request has settled.
        if (!responseCompleted) return;
        const verified = await refresh();
        // `refresh` renders every verified, partial, or unavailable outcome;
        // retaining its detail keeps scope disagreement distinguishable from
        // an ordinary transport outage.
        if (!verified) return;
      });
      pill.addEventListener('keydown', function(ev) {
        // role="button" must activate on Space; the native <a href>
        // already handles Enter. Stop Space from scrolling the page.
        if (canHalt && (ev.key === ' ' || ev.key === 'Spacebar')) {
          ev.preventDefault();
          pill.click();
        }
      });
      refresh();
      setInterval(refresh, 15000);
    })();

    // Shared goal-create form handler. The form appears on both the
    // overview (first run) and chat pages; submit via fetch so a
    // 400/403/429 shows the server message inline instead of a raw JSON
    // page. No-ops on pages without the form + its error region.
    (function() {
      const form = document.querySelector('form[action="/chat/send"]');
      const errBox = document.getElementById('goal-error');
      if (!form || !errBox) return;
      const btn = form.querySelector('button[type="submit"]');
      let authoringPending = false;
      form.addEventListener('submit', async function(ev) {
        ev.preventDefault();
        if (authoringPending) return;
        errBox.style.display = 'none';
        const kindEl = form.querySelector('[name="authoring_kind"]');
        const kind = kindEl ? kindEl.value : 'goal';
        const files = form.querySelector('input[type="file"]');
        // Stage an unsaved brief in the selected editor; never silently execute
        // a goal when the selected authoring action cannot accept attachments.
        if ((kind === 'flow' || kind === 'agent') && files && files.files.length) {
          errBox.textContent = 'Workflow and agent drafts do not accept attachments yet. Remove the files to open the editor, or choose Start a task to use them. Your text and files are still here.';
          errBox.style.display = 'block';
          return;
        }
        if ((kind === 'flow' || kind === 'agent') && !(files && files.files.length)) {
          const title = (form.querySelector('[name="title"]') || {}).value || '';
          const details = (form.querySelector('[name="description"]') || {}).value || '';
          const brief = title.trim() + (details.trim() ? '\n\n' + details : '');
          if (!brief) {
            errBox.textContent = 'Describe what you want Lightwork to draft.';
            errBox.style.display = 'block';
            return;
          }
          if (Array.from(brief).length > 8000) {
            errBox.textContent = 'Workflow briefs can contain up to 8,000 characters. Shorten the brief and try again; your text is still here.';
            errBox.style.display = 'block';
            return;
          }
          authoringPending = true;
          if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
          try {
            const contextResponse = await fetch('/api/v1/templates/draft-context', {credentials: 'same-origin', cache: 'no-store'});
            if (!contextResponse.ok) throw new Error('Could not verify your workspace. Your brief is still here; try again.');
            const contextData = await contextResponse.json();
            if (!contextData || typeof contextData.context !== 'string' || !contextData.context) {
              throw new Error('Could not verify your workspace. Your brief is still here; try again.');
            }
            if (title !== ((form.querySelector('[name="title"]') || {}).value || '') ||
                details !== ((form.querySelector('[name="description"]') || {}).value || '') ||
                kind !== (kindEl ? kindEl.value : 'goal') || (files && files.files.length)) {
              throw new Error('Your brief changed while the editor was opening. Review it and continue again.');
            }
            sessionStorage.setItem('lightwork.authoring-handoff', JSON.stringify({
              kind: kind, brief: brief, context: contextData.context, created_at: Date.now()
            }));
            window.location.href = kind === 'flow' ? '/flows/designer' : '/workflow-builder';
          } catch (e) {
            errBox.textContent = e.message || 'Could not open the editor. Your brief is still here; try again.';
            errBox.style.display = 'block';
          } finally {
            authoringPending = false;
            if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
          }
          return;
        }
        if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
        try {
          const resp = await fetch(form.action, { method: 'POST', body: new FormData(form) });
          if (resp.redirected) { window.location.href = resp.url; return; }
          if (resp.ok) { window.location.reload(); return; }
          let serverDetail = '';
          try { const j = await resp.json(); if (j && typeof j.detail === 'string') serverDetail = j.detail; } catch (e) {}
          let detail = window.mvErrorText(resp, 'start the task');
          if (resp.status === 429) {
            const ra = parseInt(resp.headers.get('Retry-After') || '', 10);
            detail = 'Too many requests right now. ' + (ra > 0
              ? 'Try again in ' + ra + (ra === 1 ? ' second.' : ' seconds.')
              : 'Wait a moment and try again.');
          } else if (resp.status === 400 && /model route/i.test(serverDetail)) {
            detail = 'Could not start the task. An AI model needs to be set up first. Ask an administrator to finish setup in Settings.';
          } else if ((resp.status === 400 || resp.status === 422) && window.mvPlainDetail(serverDetail)) {
            // Messages such as length limits are written for people.
            detail = window.mvPlainDetail(serverDetail);
          }
          errBox.textContent = detail;
          errBox.style.display = 'block';
        } catch (e) {
          errBox.textContent = 'Lightwork is not responding. Check your connection and try again.';
          errBox.style.display = 'block';
        } finally {
          if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
        }
      });
    })();

    // Reveal invalid fields before native form validation attempts to focus
    // them, including fields nested inside more than one closed disclosure.
    document.addEventListener('invalid', function (event) {
      var field = event.target;
      if (!field || typeof field.closest !== 'function') return;
      var disclosure = field.closest('details');
      while (disclosure) {
        disclosure.open = true;
        disclosure = disclosure.parentElement && disclosure.parentElement.closest('details');
      }
    }, true);

    // Collapsible sidebar groups. Chat-first: groups start COLLAPSED so the
    // nav isn't a wall of every page — the group holding the active page is
    // always forced open, and any group the user pins open persists per-group
    // in localStorage. Progressive enhancement: with no JS the markup leaves
    // every group open (aria-expanded="true"), so nothing is hidden without JS.
    (function() {
      const KEY = 'mvk_nav_expanded';
      let expanded = {};
      try { expanded = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) {}
      document.querySelectorAll('.navgrp__toggle').forEach(function(btn) {
        const grp = btn.getAttribute('data-grp');
        const hasActive = btn.parentElement.querySelector('a.active');
        const open = !!expanded[grp] || !!hasActive;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.addEventListener('click', function() {
          const nowOpen = btn.getAttribute('aria-expanded') === 'true';
          btn.setAttribute('aria-expanded', nowOpen ? 'false' : 'true');
          if (nowOpen) delete expanded[grp]; else expanded[grp] = true;
          try { localStorage.setItem(KEY, JSON.stringify(expanded)); } catch (e) {}
        });
      });
    })();

    // Feature-gated workbenches must not advertise executable controls while
    // their server capability is disabled. Templates expose only the final
    // boolean; no client value can enable a server-side feature.
    (function () {
      document.querySelectorAll('[data-feature-enabled="false"]').forEach(function (root) {
        root.setAttribute('aria-disabled', 'true');
        root.querySelectorAll('form, fieldset').forEach(function (container) {
          container.setAttribute('aria-disabled', 'true');
        });
        root.querySelectorAll('button, input, select, textarea').forEach(function (control) {
          control.disabled = true;
          if (!control.title) {
            control.title = 'This feature is turned off. Ask your administrator to enable it.';
          }
        });
      });
    })();

    // Sidebar toggle. Wide screens: the hamburger collapses/expands the
    // persistent sidebar (preference persisted). Narrow screens: it opens the
    // off-canvas drawer, closed by the backdrop, Escape, or following a link.
    (function() {
      const body = document.body;
      const btn = document.getElementById('nav-toggle');
      const backdrop = document.getElementById('nav-backdrop');
      const sidebar = document.getElementById('sidebar');
      if (!btn) return;
      const KEY = 'mvk_sidebar_collapsed';
      const mobile = window.matchMedia('(max-width: 880px)');
      const covered = Array.from(document.querySelectorAll('main, body > footer')).map(function(element) {
        return {element: element, wasInert: element.hasAttribute('inert')};
      });
      const topbar = document.querySelector('.topbar');
      function syncHeaderHeight() {
        if (topbar) body.style.setProperty('--shell-topbar-h', Math.ceil(topbar.getBoundingClientRect().height) + 'px');
      }
      // Status messages, display fonts and phone wrapping can grow the header.
      // Position navigation beneath its real height instead of covering controls.
      if (topbar && typeof ResizeObserver === 'function') {
        new ResizeObserver(syncHeaderHeight).observe(topbar);
      } else {
        window.addEventListener('resize', syncHeaderHeight);
      }
      syncHeaderHeight();
      try { if (localStorage.getItem(KEY) === '1') body.classList.add('nav-collapsed'); } catch (e) {}
      function syncAria() {
        const shown = mobile.matches ? body.classList.contains('nav-open')
                                     : !body.classList.contains('nav-collapsed');
        btn.setAttribute('aria-expanded', shown ? 'true' : 'false');
        // The mobile backdrop covers page controls. Keep keyboard and assistive
        // technology users in the visible shell until the drawer closes.
        // Header controls remain reachable, including the emergency stop.
        covered.forEach(function(item) {
          const inert = item.wasInert || (mobile.matches && shown);
          item.element.inert = inert;
          item.element.toggleAttribute('inert', inert);
        });
        // A closed drawer is only translated off-screen on mobile, so it can
        // otherwise remain in the accessibility tree and keyboard tab order.
        // Use both the reflected property and the content attribute: the
        // latter is required by browser engines that do not expose HTMLElement
        // `.inert`, while aria-hidden gives assistive technology an explicit
        // state. The same state is harmless and accurate for desktop collapse.
        if (sidebar) {
          const hidden = !shown;
          sidebar.inert = hidden;
          sidebar.toggleAttribute('inert', hidden);
          if (hidden) {
            sidebar.setAttribute('aria-hidden', 'true');
            if (sidebar.contains(document.activeElement)) btn.focus();
          } else {
            sidebar.removeAttribute('aria-hidden');
          }
        }
      }
      function closeDrawer() { body.classList.remove('nav-open'); syncAria(); }
      btn.addEventListener('click', function() {
        if (mobile.matches) {
          body.classList.toggle('nav-open');
        } else {
          const collapsed = body.classList.toggle('nav-collapsed');
          try { localStorage.setItem(KEY, collapsed ? '1' : ''); } catch (e) {}
        }
        syncAria();
        if (mobile.matches && body.classList.contains('nav-open') && sidebar) {
          const first = sidebar.querySelector('a[href], button:not([disabled])');
          if (first) first.focus();
        }
      });
      if (backdrop) backdrop.addEventListener('click', closeDrawer);
      document.addEventListener('keydown', function(ev) { if (ev.key === 'Escape') closeDrawer(); });
      document.querySelectorAll('.sidebar a, .skip-link').forEach(function(a) { a.addEventListener('click', closeDrawer); });
      if (mobile.addEventListener) mobile.addEventListener('change', function() {
        // An open mobile drawer must not silently reopen after a desktop visit.
        if (!mobile.matches) body.classList.remove('nav-open');
        syncAria();
      });
      syncAria();
    })();

    // Preferences popover: close on outside-click and Escape (it is a native
    // <details>, so choosing an option already reloads the page and closes it).
    (function () {
      var prefs = document.getElementById('prefs-menu');
      if (!prefs) return;
      prefs.querySelectorAll('[data-ui-preference]').forEach(function (select) {
        select.addEventListener('change', function () {
          var key = select.getAttribute('data-ui-preference');
          if (!key || !select.value) return;
          var target = new URL(window.location.href);
          target.searchParams.set(key, select.value);
          window.location.assign(target.toString());
        });
      });
      document.addEventListener('click', function (ev) {
        if (prefs.open && !prefs.contains(ev.target)) prefs.open = false;
      });
      document.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape' && prefs.open) {
          prefs.open = false;
          var s = prefs.querySelector('summary'); if (s) s.focus();
        }
      });
    })();

    // Module-gate banners (_module_gate.html): one delegated handler turns a
    // disabled governance module on in-app via /api/v1/features/modules.
    // Failures are LOUD -- the exact server reason lands in the banner's
    // role="alert" line (a quiet failure here reads as "I clicked Turn on
    // and nothing happened", a real field complaint).
    (function () {
      document.addEventListener('click', function (ev) {
        var btn = ev.target.closest && ev.target.closest('.mg-on');
        if (!btn) return;
        var gate = btn.closest('[data-module-gate]');
        if (!gate) return;
        var err = gate.querySelector('.mg-err');
        var section = gate.getAttribute('data-module-gate');
        btn.disabled = true;
        if (err) err.textContent = '';
        fetch('/api/v1/features/modules', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ section: section, enabled: true }),
        }).then(function (r) {
          if (r.status === 403) {
            var denied = new Error('Only an administrator can turn this on. Ask yours.');
            denied.plain = true; throw denied;
          }
          if (!r.ok) throw r;
          location.reload();   // re-render with the module live
        }).catch(function (e) {
          if (err) err.textContent = (e && e.plain) ? e.message : window.mvErrorText(e, 'turn this on');
          btn.disabled = false;
        });
      });
    })();

// Request references and idempotency keys are protocol plumbing, not something
// a person should type. Inputs marked data-auto-reference are hidden by their
// pages and filled here: one fresh reference per operation, kept unchanged
// while the same operation is retried, replaced as soon as any other field in
// the same form or section changes (that is a new operation).
(function () {
  'use strict';
  function fresh() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    var bytes = new Uint8Array(16), out = '';
    if (window.crypto && window.crypto.getRandomValues) window.crypto.getRandomValues(bytes);
    else for (var j = 0; j < bytes.length; j++) bytes[j] = Math.floor(Math.random() * 256);
    for (var i = 0; i < bytes.length; i++) out += ('0' + bytes[i].toString(16)).slice(-2);
    return out;
  }
  function scopeOf(input) {
    return input.closest('form, fieldset, section, .panel, .card') || document.body;
  }
  var refs = Array.prototype.slice.call(document.querySelectorAll('[data-auto-reference]'));
  if (!refs.length) return;
  refs.forEach(function (input) { if (!input.value) input.value = fresh(); });
  function renew(ev) {
    var t = ev.target;
    // Only a named field of the operation counts as a change; a record
    // picker's own search box and lists live inside the form but are not
    // part of what gets submitted, and the picker refuses to apply a
    // selection when the draft changed underneath it.
    if (!t || !t.name || !t.hasAttribute || t.hasAttribute('data-auto-reference')) return;
    refs.forEach(function (input) { if (scopeOf(input).contains(t)) input.value = fresh(); });
  }
  // 'input' only: a 'change' event also fires when focus leaves an edited
  // field, which would renew the reference on the click that submits it.
  document.addEventListener('input', renew, true);
})();

// Shared reveal helper respects the person's motion setting on every workspace.
window.mvReveal = function (element, options) {
  if (!element) return;
  var opts = Object.assign({block: 'nearest'}, options || {});
  opts.behavior = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
  element.scrollIntoView(opts);
  // Only explicit reveal actions use this helper, never background polling.
  if (!element.matches('input, button, select, textarea, a[href], [tabindex]')) element.tabIndex = -1;
  element.focus({preventScroll: true});
};
