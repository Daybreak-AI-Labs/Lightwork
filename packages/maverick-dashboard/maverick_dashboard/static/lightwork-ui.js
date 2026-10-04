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
        var message = 'Your workspace changed in another tab. Changes here are paused. Keep any draft you need, then reopen this page in the current workspace.';
        if (window.LightworkI18n) window.LightworkI18n.set(notice, message);
        else notice.textContent = message;
        notice.appendChild(document.createTextNode(' '));
        var reopen = document.createElement('a'); reopen.href = window.location.href;
        if (window.LightworkI18n) window.LightworkI18n.set(reopen, 'Reopen in current workspace');
        else reopen.textContent = 'Reopen in current workspace';
        notice.appendChild(reopen);
        // Keep recovery inside the content layout so the fixed sidebar cannot
        // cover the link. The alert announces the change without moving focus
        // away from a draft the person may still need to copy.
        var content = document.querySelector('main') || document.body;
        content.insertBefore(notice, content.firstChild);
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
        if(window.LightworkI18n){t.lang=window.LightworkI18n.languageFor(String(message));t.dir='auto';}
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
          return Promise.resolve(window.confirm(opts.productCopy && window.LightworkI18n ? window.LightworkI18n.text(String(message), opts.values) : String(message)));
        }
        if (dlg.open) return Promise.resolve(false);   // not re-entrant (shared dialog)
        var opener = document.activeElement;           // restore focus on close
        if (opts.productCopy && window.LightworkI18n) window.LightworkI18n.set(msgEl, String(message), opts.values);
        else { msgEl.textContent = String(message); msgEl.lang = 'und'; msgEl.dir = 'auto'; }
        if (window.LightworkI18n) { window.LightworkI18n.set(okBtn, opts.okText || 'Confirm'); window.LightworkI18n.set(cancelBtn, 'Cancel'); }
        else okBtn.textContent = opts.okText || 'Confirm';
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
      function productText(source, values) {
        return window.LightworkI18n ? window.LightworkI18n.text(source, values) : source;
      }
      function productSet(element, source, values) {
        if (window.LightworkI18n) window.LightworkI18n.set(element, source, values);
        else element.textContent = source;
      }
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
            var promptLabel = (s.label || s.key) + (s.required ? ' (required)' : '');
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
              if (!v0 && s.required) { problem = productText('This field is required.'); continue; }
              if (boolField) {
                if (!/^(true|false|yes|no|1|0)$/i.test(v0)) {
                  problem = productText('Enter true or false.'); continue;
                }
                out0[s.key] = /^(true|yes|1)$/i.test(v0);
              } else if (v0) {
                if (s.type === 'number' && !Number.isFinite(Number(v0))) {
                  problem = productText('Enter a valid number.'); continue;
                }
                if (s.type === 'select' && choices.indexOf(v0) < 0) {
                  problem = productText('Choose one of the listed values.'); continue;
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
        var titleElement = document.getElementById('mv-form-title');
        if (!title || opts.productCopy) productSet(titleElement, String(title || 'Details'));
        else { titleElement.textContent = String(title); titleElement.lang = 'und'; titleElement.dir = 'auto'; }
        productSet(document.getElementById('mv-form-ok'), opts.okText || 'OK');
        body.textContent = ''; errEl.textContent = '';
        var controls = [];
        fields.forEach(function (f) {
          var id = 'mvf-' + f.key;
          var wrap = document.createElement('div'); wrap.className = 'mv-form__field';
          var ctl;
          if (f.type === 'bool' || f.type === 'checkbox') {
            wrap.className += ' mv-form__check';
            ctl = document.createElement('input'); ctl.type = 'checkbox'; ctl.checked = booleanDefault(f.default); ctl.id = id;
            var l1 = document.createElement('label'); l1.setAttribute('for', id); l1.textContent = f.label || f.key; l1.lang = 'und'; l1.dir = 'auto';
            wrap.appendChild(ctl); wrap.appendChild(l1);
          } else {
            var lab = document.createElement('label'); lab.setAttribute('for', id);
            lab.textContent = f.label || f.key; lab.lang = 'und'; lab.dir = 'auto';
            wrap.appendChild(lab);
            if (f.type === 'select') {
              ctl = document.createElement('select');
              (f.options || []).forEach(function (o) {
                var op = document.createElement('option');
                op.value = (o && o.value != null) ? o.value : o;
                op.textContent = (o && o.label != null) ? o.label : o; op.lang = 'und'; op.dir = 'auto';
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
            h.id = id + '-help'; h.textContent = f.help; h.lang = 'und'; h.dir = 'auto';
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
              if (window.LightworkI18n) window.LightworkI18n.set(errEl, 'Required: {fields}', {fields: missing.join(', ')});
              else errEl.textContent = 'Required: ' + missing.join(', ');
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
        function ok() { mvToast(productText('Copied to clipboard')); }
        function fail() { mvToast(productText('Copy failed — select and copy manually'), { error: true }); }
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
      // The one empty state, same markup as templates/_empty_state.html:
      // "[What will appear here]. [How to add the first one]." plus at most
      // one next step — {label, href} for a link or {label, onClick} for a
      // button; leave it out when the viewer can't use the destination.
      // opts.compact: the smaller version for panels, tiles and charts.
      window.mvEmpty = function (title, body, action, opts) {
        var box = window.mvEl('div', 'mv-empty' + (opts && opts.compact ? ' mv-empty--compact' : ''));
        box.appendChild(window.mvEl('p', 'mv-empty__title', title));
        if (body) box.appendChild(window.mvEl('p', 'mv-empty__body', body));
        if (action && action.label && (action.href || action.onClick)) {
          var next = window.mvEl(action.href ? 'a' : 'button', 'btn mv-empty__action', action.label);
          if (action.href) next.setAttribute('href', action.href);
          else { next.type = 'button'; next.addEventListener('click', action.onClick); }
          box.appendChild(next);
        }
        return box;
      };
      // Only the business-error envelope carries trusted, authored display copy.
      // Legacy detail strings continue through the restrictive plain-copy filter.
      window.mvBusinessError = function (value) {
        var d = value && (value.detail || value);
        if (!d || d.schema !== 'lightwork.error.v1' || typeof d.code !== 'string' ||
            !/^[a-z][a-z0-9_.-]{0,79}$/.test(d.code) || typeof d.message !== 'string' ||
            !d.message.trim() || d.message.length > 1200 || /[\x00-\x1f]/.test(d.message)) return null;
        return d;
      };
      window.mvErrorResponse = async function (response) {
        var payload = {};
        try { payload = await response.clone().json(); } catch (_) {}
        return {status: response.status, detail: payload.detail};
      };
      window.mvRenderError = function (element, failure, action, payload) {
        var detail = window.mvBusinessError(payload || failure);
        var legacy = payload && window.mvPlainDetail(payload.detail);
        element.replaceChildren(document.createTextNode(detail ? detail.message :
          (legacy || window.mvErrorText(failure, action))));
        if (!detail) return;
        var fields = detail.field_errors;
        if (fields && typeof fields === 'object') Object.keys(fields).slice(0, 30).forEach(function (name) {
          if (!/^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(name) || typeof fields[name] !== 'string' || fields[name].length > 400) return;
          var copy = document.createElement('span'); copy.className = 'business-field-error';
          copy.textContent = ' ' + fields[name]; element.appendChild(copy);
          var form = element.closest('form');
          if (form) Array.from(form.elements).forEach(function (input) {
            if (input.name === name) { input.setAttribute('aria-invalid', 'true');
              if (element.id) input.setAttribute('aria-describedby', element.id); }
          });
        });
        var next = detail.next_action;
        if (!next || typeof next.href !== 'string' || typeof next.label !== 'string' || next.label.length > 120) return;
        try {
          var decoded = decodeURIComponent(next.href), url = new URL(next.href, location.href);
          if (next.href[0] !== '/' || /^\/\//.test(decoded) || /[\\\x00-\x1f]/.test(decoded) || url.origin !== location.origin) return;
          var link = document.createElement('a'); link.href = url.pathname + url.search + url.hash;
          link.textContent = next.label; link.className = 'business-next-action';
          element.appendChild(document.createTextNode(' ')); element.appendChild(link);
        } catch (_) {}
      };
      // Plain-language error text for a failed request. Pass the fetch
      // Response (or a thrown error) and, optionally, what the person was
      // trying to do ("save the policy"). Never shows HTTP codes, exception
      // class names or server internals. One definition: the English sentence
      // comes from plainErrorText and a translated interface localizes it.
      function plainErrorText(failure, action) {
        var business = window.mvBusinessError(failure);
        if (business) return business.message;
        var what = action ? ('Could not ' + action + '. ') : '';
        var status = failure && typeof failure.status === 'number' ? failure.status : 0;
        if (!status) {
          var name = failure && failure.name;
          if (name === 'AbortError') return what + 'It took too long. Try again.';
          if (name === 'TypeError' || (failure && /fetch|network/i.test(String(failure.message || failure)))) {
            return what + 'Lightwork could not be reached. Check your connection and try again.';
          }
          return what + 'Something went wrong. Try again.';
        }
        if (status === 400 || status === 422) return what + 'Some of the details need fixing. Check what you entered and try again.';
        if (status === 401) return what + 'Your sign-in has expired. Sign in again to continue.';
        if (status === 403) {
          // Reason code from the server: a feature that is turned off is not a missing permission.
          var off = typeof window.mvFeatureOff === 'function' && window.mvFeatureOff(failure);
          if (off) return what + window.mvFeatureOffText(off);
          return what + 'You do not have permission to do this. Ask an administrator.';
        }
        if (status === 404) return what + 'That item no longer exists. Refresh the page to see the latest.';
        if (status === 409) {
          // A conflict can mean an existing item, missing setup, or a changed
          // version. Keep a safe server explanation when one was supplied.
          var explanation = typeof window.mvPlainDetail === 'function' &&
            window.mvPlainDetail(failure && failure.detail);
          return what + (explanation || 'This cannot be done right now. Review the item or its settings before trying again.');
        }
        if (status === 413) return what + 'That is too large. Try a smaller file or fewer items.';
        if (status === 429) return what + 'Too many requests right now. Wait a moment and try again.';
        if (status === 503) {
          // Only say "turned off" when the caller knows the server said so
          // (featureOff flag or its detail text); a plain 503 is temporary.
          var off = failure.featureOff === true ||
            /\b(turned off|is disabled|not enabled)\b/i.test(typeof failure.detail === 'string' ? failure.detail : '');
          return what + (off
            ? 'This feature is turned off. An administrator can turn it on in Settings.'
            : 'This is temporarily unavailable. Try again in a few minutes.');
        }
        if (status >= 500) return what + 'Something went wrong on our side. Try again in a minute.';
        return what + 'Something went wrong. Try again.';
      }
      window.mvErrorText = function (failure, action) {
        var value = plainErrorText(failure, action), locale = window.LightworkI18n;
        if (!locale || locale.language === 'en') return value;
        var prefix = action ? 'Could not ' + action + '. ' : '';
        var reason = prefix && value.startsWith(prefix) ? value.slice(prefix.length) : value;
        if (prefix && locale.has(reason)) return locale.text('This action could not be completed. {reason}', {reason: locale.text(reason)});
        return locale.text(value);
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
        return window.mvTimeAbsolute(ts);
      };
      // One shared time formatter for every timestamp people read. Servers
      // send an epoch (seconds) or an ISO string; the viewer sees it in their
      // own time zone. Convention (any page, any markup owner):
      //   <time data-mv-time="1790790000">fallback</time>
      //   data-mv-time-mode="relative" (default: "2 minutes ago", absolute on
      //   hover) | "absolute" ("30 Sep 2026, 17:15 BST") | "both" | "date"
      //   ("30 Sep 2026"; a "2026-09-30" value is a calendar date, not UTC midnight).
      // Elements added later are formatted by calling window.mvFormatTimes(root).
      // English pages read the same in every browser ("4 Oct 2026, 14:01 BST",
      // a 24-hour clock); a translated interface uses its own locale's order.
      function mvFormatLocale() {
        var i18n = window.LightworkI18n;
        return i18n && i18n.language && i18n.language !== 'en' ? (i18n.locale || i18n.language) : 'en-GB';
      }
      function mvParseTime(raw) {
        if (raw === null || raw === undefined || raw === '') return null;
        var n = Number(raw);
        var d = isFinite(n) ? new Date((n > 1e12 ? n : n * 1000)) : new Date(String(raw));
        return isNaN(d.getTime()) || d.getTime() <= 0 ? null : d;
      }
      // An optional IANA zone shows the time in that zone instead (a schedule's own
      // zone); the zone name is part of the text either way.
      window.mvTimeAbsolute = function (raw, zone) {
        var d = mvParseTime(raw);
        if (!d) return '';
        var options = { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' };
        if (zone) options.timeZone = zone;
        try { return new Intl.DateTimeFormat(mvFormatLocale(), options).format(d); }
        catch (e) { return zone ? window.mvTimeAbsolute(raw) : d.toUTCString(); }
      };
      // A calendar day ("2026-10-04" or a moment) reads "4 Oct 2026"; an
      // accounting month ("2026-09") reads "September 2026".
      window.mvDateAbsolute = function (raw) {
        var parts = typeof raw === 'string' && /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(raw.trim());
        var d = parts ? new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3] || 1)) : mvParseTime(raw);
        if (!d || isNaN(d.getTime())) return '';
        try {
          return new Intl.DateTimeFormat(mvFormatLocale(), parts && !parts[3] ? { month: 'long', year: 'numeric' }
            : { day: 'numeric', month: 'short', year: 'numeric' }).format(d);
        } catch (e) { return d.toDateString(); }
      };
      // Money reads "$1,234.56" everywhere (the |money filter matches it):
      // two decimals with thousands separators, and a non-zero amount under a
      // cent reads "under $0.01". { decimals: 4 } is only for a labelled
      // per-unit price ("$0.0030 per 1K tokens"); { currency: 'EUR' } shows the
      // code ("EUR 75.00"). A missing or unreadable amount returns '' so the
      // caller can say why it is unknown instead of showing $0.00.
      window.mvMoney = function (value, options) {
        if (value === null || value === undefined || value === '' || typeof value === 'boolean') return '';
        var amount = Number(value);
        if (!isFinite(amount)) return '';
        var decimals = options && typeof options.decimals === 'number' ? options.decimals : 2;
        var currency = String((options && options.currency) || 'USD').toUpperCase();
        var i18n = window.LightworkI18n;
        var locale = i18n && i18n.language && i18n.language !== 'en' ? (i18n.locale || i18n.language) : 'en-US';
        function format(n) {
          try {
            return new Intl.NumberFormat(locale, { style: 'currency', currency: currency,
              currencyDisplay: currency === 'USD' ? 'symbol' : 'code',
              minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(n);
          } catch (e) { return (n < 0 ? '-' : '') + (currency === 'USD' ? '$' : currency + ' ') + Math.abs(n).toFixed(decimals); }
        }
        var smallest = Math.pow(10, -decimals);
        if (amount !== 0 && Math.abs(amount) < smallest / 2) return 'under ' + format(smallest);
        return format(amount || 0);
      };
      window.mvTimeRelative = function (raw) {
        var d = mvParseTime(raw);
        if (!d) return '';
        var diff = (d.getTime() - Date.now()) / 1000;
        var abs = Math.abs(diff), past = diff <= 0;
        // [seconds in one unit, unit, size of the next unit]. Each value is
        // counted in its own unit; one that rounds up to the next unit
        // ("60 minutes") is shown in that unit instead ("1 hour").
        var units = [[1, 'second', 60], [60, 'minute', 3600], [3600, 'hour', 86400], [86400, 'day', 604800],
          [604800, 'week', 2629800], [2629800, 'month', 31557600], [31557600, 'year', Infinity]];
        if (abs < 45) return past ? 'just now' : 'in a moment';
        var i = 0;
        while (Math.round(abs / units[i][0]) * units[i][0] >= units[i][2]) i++;
        var size = units[i][0], unit = units[i][1], value = Math.round(abs / size);
        if (typeof Intl !== 'undefined' && Intl.RelativeTimeFormat) {
          try { return new Intl.RelativeTimeFormat(mvFormatLocale(), { numeric: 'auto' }).format(past ? -value : value, unit); } catch (e) {}
        }
        var word = value + ' ' + unit + (value === 1 ? '' : 's');
        return past ? word + ' ago' : 'in ' + word;
      };
      window.mvFormatTimes = function (root) {
        var scope = root && root.querySelectorAll ? root : document;
        Array.prototype.forEach.call(scope.querySelectorAll('[data-mv-time]'), function (el) {
          var raw = el.getAttribute('data-mv-time');
          var d = mvParseTime(raw);
          if (!d) return;
          var mode = el.getAttribute('data-mv-time-mode') || 'relative';
          var absolute = window.mvTimeAbsolute(raw);
          var relative = window.mvTimeRelative(raw);
          if (el.tagName === 'TIME' && !el.getAttribute('datetime')) el.setAttribute('dateTime', d.toISOString());
          if (mode === 'date') el.textContent = window.mvDateAbsolute(raw);
          else if (mode === 'absolute') { el.textContent = absolute; el.title = relative; }
          else if (mode === 'both') { el.textContent = absolute + ' (' + relative + ')'; }
          else { el.textContent = relative; el.title = absolute; }
          el.setAttribute('data-mv-time-done', mode);
        });
      };
      // The page's own timestamps, flash notices and reset times are formatted
      // once the full document is available (scripted harnesses stub it).
      var realDocument = typeof document.querySelectorAll === 'function' && typeof document.querySelector === 'function';
      if (realDocument) window.mvFormatTimes(document);
      // Relative labels drift; refresh them once a minute. Only when the page
      // has any: an idle timer would keep a scripted runtime from exiting.
      if (realDocument && document.querySelector('[data-mv-time]')) {
        setInterval(function () {
          Array.prototype.forEach.call(document.querySelectorAll('[data-mv-time-done="relative"]'), function (el) {
            el.textContent = window.mvTimeRelative(el.getAttribute('data-mv-time'));
          });
        }, 60000);
      }
      // "Resets at midnight UTC" is only meaningful next to the viewer's own
      // clock: <span data-mv-utc-midnight></span> becomes ", which is 01:00 for you".
      Array.prototype.forEach.call(realDocument ? document.querySelectorAll('[data-mv-utc-midnight]') : [], function (el) {
        try {
          var next = new Date(); next.setUTCHours(24, 0, 0, 0);
          var local = next.toLocaleTimeString(mvFormatLocale(), { hour: '2-digit', minute: '2-digit' });
          var zone = (Intl.DateTimeFormat().resolvedOptions().timeZone || '').replace(/_/g, ' ');
          if (next.getTimezoneOffset() !== 0) el.textContent = ', which is ' + local + ' in your time zone' + (zone ? ' (' + zone + ')' : '');
        } catch (e) {}
      });
      // A server-rendered "Saved." notice after a redirect also announces as a
      // toast, so feedback lands where the person is looking, not above the
      // fold. <div data-mv-toast="Workspace limit saved." ...>.
      // A page that reloads right after a change hands its toast to the next load.
      var FLASH = 'lightwork.flash-toast';
      window.mvToastAfterReload = function (message) {
        try { sessionStorage.setItem(FLASH, String(message)); } catch (e) { window.mvToast(message); }
      };
      function showInitialToasts() {
        Array.prototype.forEach.call(realDocument ? document.querySelectorAll('[data-mv-toast]') : [], function (el) {
          var text = el.getAttribute('data-mv-toast');
          if (text) window.mvToast(text, { error: el.getAttribute('data-mv-toast-error') === 'true' });
        });
        try {
          var flashed = sessionStorage.getItem(FLASH);
          if (flashed) { sessionStorage.removeItem(FLASH); window.mvToast(flashed); }
        } catch (e) {}
      }
      // Navigation and submit protection initialize before localization downloads;
      // initial announcements wait for their language helpers or honest fallback.
      if (document.readyState === 'loading' && !window.LightworkI18n) {
        document.addEventListener('DOMContentLoaded', showInitialToasts, {once: true});
      } else showInitialToasts();
    })();

    // Halt indicator + arm/clear via /api/v1/halt.
    (function() {
      const pill = document.getElementById('halt-pill');
      const label = document.getElementById('halt-label');
      if (!pill || !label) {
        // Shells without the pill (the chat shell) still show the stop banner,
        // read from the same status document and only when it verifies.
        const soloBanner = document.getElementById('halt-banner');
        if (!soloBanner) return;
        async function readSolo() {
          try {
            const r = await fetch('/api/v1/halt', { cache: 'no-store' });
            if (!r.ok) throw new Error('halt status ' + r.status);
            const body = await r.json();
            const armed = !!(body && body.active === true && (body.scope !== 'cluster' || body.cluster_halt === true));
            soloBanner.hidden = !armed;
            document.body.classList.toggle('halt-armed', armed);
          } catch (e) { soloBanner.hidden = true; }
        }
        readSolo();
        setInterval(readSolo, 15000);
        return;
      }
      const canHalt = pill.dataset.canHalt === 'true';
      function productText(source) { return window.LightworkI18n ? window.LightworkI18n.text(source) : source; }
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
      // The red banner every page shows while work is stopped. The pill alone
      // is easy to miss; the banner names the state and offers the resume
      // action to people allowed to take it.
      const banner = document.getElementById('halt-banner');
      const chatControls = typeof document.querySelector === 'function'
        ? document.querySelector('form.composer[action="/chat/send"] fieldset.composer__controls')
        : null;
      const chatControlsInitiallyDisabled = !!(chatControls && chatControls.disabled);
      const chatStopNote = document.getElementById('chat-stop-note');
      let chatStopped = false;
      function renderChatStop(state) {
        if (state === 'armed' || state === 'partial') chatStopped = true;
        else if (state === 'clear') chatStopped = false;
        if (chatControls) chatControls.disabled = chatControlsInitiallyDisabled || chatStopped;
        if (chatStopNote) chatStopNote.hidden = !chatStopped;
      }
      function renderBanner(show) {
        if (!banner) return;
        banner.hidden = !show;
        document.body.classList.toggle('halt-armed', !!show);
        const resume = banner.querySelector('[data-halt-resume]');
        if (resume) resume.hidden = !canHalt;
      }
      if (banner) {
        const resume = banner.querySelector('[data-halt-resume]');
        if (resume) resume.addEventListener('click', function (ev) { ev.preventDefault(); pill.click(); });
      }
      function render(state, detail) {
        haltState = state;
        const armed = state === 'armed';
        const known = state === 'armed' || state === 'clear';
        pill.classList.toggle('armed', armed);
        pill.classList.toggle('unavailable', !known);
        const copy = detail || (armed
          ? (canHalt ? 'STOPPED — click to resume' : 'STOPPED')
          : state === 'clear'
            ? 'Emergency stop'
            : 'Stop status unavailable');
        // A late catalog translates the current verified state, never an older
        // pending label. Authority checks and status reads stay immediate.
        label.dataset.i18n = copy;
        if (window.LightworkI18n) window.LightworkI18n.set(label, copy);
        else { label.textContent = copy; label.lang = 'en'; label.dir = 'ltr'; }
        renderBanner(armed && !pending);
        renderChatStop(state);
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
          mvToast(productText('You do not have permission to stop or resume this work.'), { error: true });
          return;
        }
        if (pending || !knownState()) {
          mvToast(productText('Emergency stop status is unavailable. Retry after the dashboard can verify it.'), { error: true });
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
            const consequence = haltScope === 'cluster'
              ? 'Stop work across connected workers? Work covered by this emergency stop pauses at its next step and waits for an authorized resume.'
              : 'Stop work on this server? Work covered by this emergency stop pauses at its next step and waits for an authorized resume.';
            if (!(await mvConfirm(consequence, { productCopy: true, okText: 'Stop work' }))) return;
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
              : productText('Emergency stop change failed. Please try again.'), { error: true });
          } else {
            // A 204 only acknowledges the mutation request.  It is not a
            // status document and cannot prove either local or cluster state.
            render('unknown', wasArmed
              ? 'Resume requested — checking work status…'
              : 'Stop requested — checking work status…');
          }
        } catch (e) {
          render('unknown');
          mvToast(productText('Emergency stop change failed. Lightwork is not responding. Check your connection and try again.'), { error: true });
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
        const accountSelection=(form.querySelector('[name="connection_references"]')||{}).value||'[]';
        if(kind!=='goal'&&accountSelection!=='[]'){
          var accountNotice='This draft has selected accounts. Run it as a task, or return to Templates to prepare a new reusable draft without private account selections.';
          if(window.LightworkI18n)window.LightworkI18n.set(errBox,accountNotice);else errBox.textContent=accountNotice;
          errBox.style.display='block';return;
        }

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
        if ((form.querySelector('[name="composer_mode"]') || {}).value === 'message') {
          const message = (form.querySelector('[name="title"]') || {}).value || '';
          const details = (form.querySelector('[name="description"]') || {}).value || '';
          const brief = message + (details.trim() ? '\n\n' + details : '');
          if (Array.from(brief).length > 16000) {
            errBox.textContent = 'Shorten your message and details to 16,000 characters or fewer. Your text is still here.';
            errBox.style.display = 'block';
            return;
          }
        }
        if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
        const submittedForm = new FormData(form);
        const submittedControls = Array.from(form.querySelectorAll('input,textarea,select')).map(function (control) {
          const wasDisabled = control.disabled; control.disabled = true; return [control,wasDisabled];
        });
        try {
          const resp = await fetch(form.action, { method: 'POST', body: submittedForm });
          if (resp.redirected && resp.ok && new URL(resp.url).pathname.startsWith('/chat/goal/')) {
            if (form.mvRecovery) await form.mvRecovery.clear();
            window.dispatchEvent(new CustomEvent('lightwork:composer-sent', {detail: {form: form, recoveryCleared: true}}));
            window.location.href = resp.url; return;
          }
          if (resp.ok && !resp.redirected) {
            if (form.mvRecovery) await form.mvRecovery.clear();
            window.dispatchEvent(new CustomEvent('lightwork:composer-sent', {detail: {form: form, recoveryCleared: true}}));
            window.location.reload(); return;
          }
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
          submittedControls.forEach(function (item) { item[0].disabled = item[1]; });
          if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
        }
      });
    })();

    // Required fields say so beside their label: one small "Required" for
    // every [required] control, on load and for forms added later (dialogs,
    // rows built from data). The required attribute already tells assistive
    // technology, so the marker is aria-hidden. A label that already says it
    // (an asterisk or the word "required") is left as written.
    (function () {
      if (typeof document.querySelectorAll !== 'function' || !document.body) return;
      var CONTROLS = 'input[required], select[required], textarea[required]';
      function labelFor(control) {
        if (control.type === 'hidden') return null;
        if (control.type === 'radio') {
          var group = control.closest('fieldset');
          return group && group.querySelector('legend');
        }
        return control.labels && control.labels.length ? control.labels[0] : null;
      }
      // The label's own words, without the text of a field it wraps.
      function ownText(node) {
        var text = '';
        Array.prototype.forEach.call(node.childNodes, function (child) {
          if (child.nodeType === 3) text += child.nodeValue;
          else if (child.nodeType === 1 && !child.matches('input, select, textarea, button, datalist')) text += ownText(child);
        });
        return text;
      }
      function mark(control) {
        var label = labelFor(control);
        if (!label || label.querySelector('.mv-required') || /(?:^|\s)\*(?:\s|$)|\brequired\b/i.test(ownText(label))) return;
        var marker = document.createElement('span');
        marker.className = 'mv-required'; marker.setAttribute('aria-hidden', 'true');
        marker.setAttribute('data-mv-required', ''); marker.lang = 'en'; marker.textContent = 'Required';
        // Beside the words: inside the element holding them (<span>Person</span>),
        // else just before a field the label wraps, else after a checkbox's words.
        var field = label.contains(control) && !/^(checkbox|radio)$/.test(control.type) ? control : null;
        while (field && field.parentNode !== label) field = field.parentNode;
        var words = field && field.previousSibling;
        while (words && words.nodeType === 3 && !words.nodeValue.trim()) words = words.previousSibling;
        if (words && words.nodeType === 1 && words.textContent.trim() && !words.matches('input, select, textarea, button') &&
            !words.querySelector('input, select, textarea, button')) words.appendChild(marker);
        else if (field) label.insertBefore(marker, field);
        else label.appendChild(marker);
      }
      function unmark(control) {
        var label = labelFor(control);
        var marker = label && label.querySelector('[data-mv-required]');
        if (!marker) return;
        if (control.type === 'radio' && label.parentNode.querySelector('input[type="radio"][required]')) return;
        marker.remove();
      }
      function markWithin(root) {
        if (root.matches && root.matches(CONTROLS)) mark(root);
        if (root.querySelectorAll) Array.prototype.forEach.call(root.querySelectorAll(CONTROLS), mark);
      }
      // A label whose words were rewritten (a translation, a renamed field) gets its marker back.
      function relabel(node) {
        var label = node.nodeType === 1 && node.closest('label, legend');
        if (!label) return;
        var control = label.tagName === 'LEGEND' ? label.parentNode.querySelector('input[type="radio"][required]') : label.control;
        if (control && control.required) mark(control);
      }
      markWithin(document);
      if (typeof MutationObserver !== 'function') return;
      new MutationObserver(function (changes) {
        changes.forEach(function (change) {
          if (change.type === 'attributes') {
            if (change.target.required) mark(change.target); else unmark(change.target);
            return;
          }
          Array.prototype.forEach.call(change.addedNodes, function (node) { if (node.nodeType === 1) markWithin(node); });
          relabel(change.target);
        });
      }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['required'] });
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
      let expanded = null;
      try { expanded = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) {}
      // No remembered choice yet (a fresh sign-in): open the two groups a new
      // administrator needs first, so the rail is never ten closed headers.
      if (!expanded || typeof expanded !== 'object') expanded = { Operate: true, Admin: true };
      document.querySelectorAll('.navgrp__toggle').forEach(function(btn) {
        const grp = btn.getAttribute('data-grp');
        const hasActive = btn.parentElement.querySelector('a.active');
        const open = !!expanded[grp] || !!hasActive;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.addEventListener('click', function() {
          const nowOpen = btn.getAttribute('aria-expanded') === 'true';
          btn.setAttribute('aria-expanded', nowOpen ? 'false' : 'true');
          if (nowOpen) expanded[grp] = false; else expanded[grp] = true;
          try { localStorage.setItem(KEY, JSON.stringify(expanded)); } catch (e) {}
        });
      });
    })();

    // Approvals badge: how many decisions wait for this person. The sidebar
    // renders the link only for people who can decide; the count is fetched
    // after the page has rendered, so it never slows a page, and stays hidden
    // at zero or when it cannot be read.
    (function() {
      const links = Array.from(document.querySelectorAll('a[data-approvals-badge]'));
      if (!links.length || typeof fetch !== 'function') return;
      function show(count) {
        links.forEach(function(link) {
          const badge = link.querySelector('.nav-count');
          const label = link.querySelector('.nav-label');
          if (!badge || !label) return;
          badge.hidden = !(count > 0);
          badge.textContent = count > 99 ? '99+' : String(count);
          badge.setAttribute('aria-hidden', 'true');
          if (count > 0) {
            const i18n = window.LightworkI18n, values = {page: label.textContent.trim(), count: count};
            link.setAttribute('aria-label', i18n ? i18n.text('{page}, {count} waiting', values)
              : values.page + ', ' + count + ' waiting');
          } else {
            link.removeAttribute('aria-label');
          }
        });
      }
      fetch('/api/v1/approvals/waiting-count', {credentials: 'same-origin', cache: 'no-store',
        headers: {Accept: 'application/json'}})
        .then(function(response) { return response.ok ? response.json() : null; })
        .then(function(data) { if (data && Number.isSafeInteger(data.waiting) && data.waiting >= 0) show(data.waiting); })
        .catch(function() {});
    })();

    // Feature-gated workbenches must not advertise executable controls while
    // their server capability is disabled. Templates expose only the final
    // boolean; no client value can enable a server-side feature.
    (function () {
      // A module-gate banner's Turn on button is the way out of a switched-off
      // page; disabling it (or marking an ancestor aria-disabled, which reads
      // as disabling everything inside) left an administrator no way to start.
      function markOff(container) {
        if (!container.querySelector('[data-module-gate]')) container.setAttribute('aria-disabled', 'true');
      }
      document.querySelectorAll('[data-feature-enabled="false"]').forEach(function (root) {
        markOff(root);
        root.querySelectorAll('form, fieldset').forEach(markOff);
        root.querySelectorAll('button, input, select, textarea').forEach(function (control) {
          if (control.closest('[data-module-gate]')) return;
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

    (function () {
      var system = window.matchMedia('(prefers-color-scheme: dark)');
      var themeClass = Array.from(document.body.classList).find(function (name) { return name.startsWith('theme-'); });
      document.body.dataset.themePreference = themeClass ? themeClass.slice(6) : 'dark';
      window.mvApplyDisplayPreference = function (key, value) {
        if (key === 'text_size') {
          document.documentElement.dataset.textSize = value;
          document.documentElement.style.fontSize = value === 'larger' ? '125%' : value === 'large' ? '112.5%' : '';
          return;
        }
        if (['theme', 'font', 'density'].indexOf(key) === -1) return;
        if (key === 'theme') { document.body.dataset.themePreference = value; if (value === 'system') value = system.matches ? 'dark' : 'light'; }
        Array.from(document.body.classList).forEach(function (name) { if (name.startsWith(key + '-')) document.body.classList.remove(name); });
        document.body.classList.add(key + '-' + value);
        window.dispatchEvent(new CustomEvent('lightwork:appearance-changed', {detail:{key:key,value:value}}));
      };
      var size = document.querySelector('meta[name="lightwork-text-size"]');
      window.mvApplyDisplayPreference('text_size', size ? size.content : 'standard');
      window.mvApplyDisplayPreference('theme', document.body.dataset.themePreference);
      function sync() { if (document.body.dataset.themePreference === 'system') window.mvApplyDisplayPreference('theme', 'system'); }
      if (system.addEventListener) system.addEventListener('change', sync); else system.addListener(sync);
    })();

    // Preferences popover: close on outside-click and Escape (it is a native
    // <details>; choosing an option keeps both the page and popover open).
    (function () {
      var prefs = document.getElementById('prefs-menu');
      if (!prefs) return;
      var status = document.getElementById('prefs-status'), undo = document.getElementById('prefs-undo');
      var saving = false, previous = null;
      async function savePreference(key, value, select, isUndo) {
        if (saving) return;
        saving = true;
        var old = key === 'interface_language' ? (select.dataset.savedValue || '') : key === 'text_size' ? (document.documentElement.dataset.textSize || 'standard') :
          key === 'theme' ? (document.body.dataset.themePreference || 'dark') :
          Array.from(document.body.classList).find(function (name) { return name.startsWith(key + '-'); });
        if (old && old.startsWith(key + '-')) old = old.slice(key.length + 1);
        prefs.querySelectorAll('select, button, input').forEach(function (control) { control.disabled = true; });
        window.mvApplyDisplayPreference(key, value);
        if (status) window.LightworkI18n ? window.LightworkI18n.set(status, 'Saving…') : status.textContent = 'Saving…';
        try {
          var context = document.querySelector('meta[name="lightwork-preference-context"]');
          var revision = document.querySelector('meta[name="lightwork-preference-revision"]');
          var device = document.getElementById('prefs-device-only');
          var response = await fetch('/api/v1/preferences/display', {method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key:key, value:value,
              context:context ? context.content : '', expected_revision:revision ? revision.content : null,
              device_only:!!(device && device.checked)})});
          var data = await response.json();
          if (!response.ok) throw response;
          if (revision && data.revision) revision.content = data.revision;
          previous = isUndo ? null : {key:key, value:old, select:select};
          if (undo) undo.hidden = !previous;
          if (key === 'interface_language') select.dataset.savedValue = value;
          var savedMessage = key === 'interface_language' ? 'Language saved. Open another page or reload after saving your draft.' : 'Saved. Your editor and unsaved work remain open.';
          if (status) window.LightworkI18n ? window.LightworkI18n.set(status, savedMessage) : status.textContent = savedMessage;
        } catch (failure) {
          window.mvApplyDisplayPreference(key, old);
          if (select) select.value = old;
          if (status) { var failureMessage = 'Appearance was not saved. The earlier appearance is restored; retry or refresh preferences after saving your work.'; if (window.LightworkI18n) window.LightworkI18n.set(status, failureMessage); else status.textContent = failureMessage; }
        } finally {
          saving = false; prefs.querySelectorAll('select, button, input').forEach(function (control) { control.disabled = false; });
        }
      }
      prefs.querySelectorAll('[data-ui-preference]').forEach(function (select) {
        select.addEventListener('change', function () {
          var key = select.getAttribute('data-ui-preference');
          if (key && (select.value || key === 'interface_language')) savePreference(key, select.value, select, false);
        });
      });
      if (undo) undo.addEventListener('click', function () {
        if (previous) { previous.select.value = previous.value; savePreference(previous.key, previous.value, previous.select, true); }
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

    // "Turned off" versus "not allowed": the server marks a refusal because a
    // feature is off with X-Lightwork-Reason: feature-off (feature_gate.py).
    // mvFeatureOff(failure) reads it from a fetch Response, a Headers-carrying
    // error or a page-supplied {feature: {...}}; mvShowFeatureOff renders the
    // module-gate notice in place, with Turn on for whoever may turn it on.
    (function () {
      function lowerFirst(text) {
        return /^[A-Z][a-z]/.test(text) ? text.charAt(0).toLowerCase() + text.slice(1) : text;
      }
      window.mvFeatureOff = function (failure) {
        if (!failure) return null;
        if (failure.feature && typeof failure.feature === 'object') return failure.feature;
        var h = failure.headers;
        if (!h) return null;
        var get = typeof h.get === 'function' ? function (k) { return h.get(k) || ''; }
          : function (k) { return h[k] || h[k.toLowerCase()] || ''; };
        if (get('X-Lightwork-Reason') !== 'feature-off') return null;
        var label = '';
        try { label = decodeURIComponent(get('X-Lightwork-Feature-Label')); } catch (_) { label = ''; }
        return { section: get('X-Lightwork-Feature'), label: label,
                 switchable: get('X-Lightwork-Feature-Switch') === 'module' };
      };
      window.mvFeatureOffText = function (off) {
        var name = off && off.label ? off.label : '';
        var admin = document.body && document.body.getAttribute('data-global-admin') === '1';
        return (name ? name + ' is turned off.' : 'This feature is turned off.') + ' ' +
          (admin ? 'You can turn it on in Settings.'
            : 'Ask an administrator to turn ' + (name ? 'on ' + lowerFirst(name) : 'it on') + '.');
      };
      window.mvShowFeatureOff = function (element, failure, action) {
        var off = window.mvFeatureOff(failure);
        if (!element || !off) return false;
        var prefix = action ? 'Could not ' + action + '. ' : '';
        if (!off.section || !off.switchable || !off.label) {
          element.textContent = prefix + window.mvFeatureOffText(off);
          return true;
        }
        // A span: the notice lands inside the page's own status line.
        var gate = document.createElement('span');
        gate.className = 'mg-banner';
        gate.setAttribute('data-module-gate', off.section);
        gate.setAttribute('data-module-label', off.label);
        gate.setAttribute('data-mg-inline', '1');
        var head = document.createElement('strong');
        head.textContent = prefix + off.label + ' is turned off.';
        var actions = document.createElement('span');
        actions.className = 'mg-actions mg-admin-only';
        var button = document.createElement('button');
        button.type = 'button'; button.className = 'btn btn--primary mg-on';
        button.textContent = 'Turn on ' + lowerFirst(off.label);
        actions.appendChild(button);
        var note = document.createElement('span');
        note.className = 'mg-actions muted mg-nonadmin-note';
        note.setAttribute('role', 'note');
        note.textContent = 'Ask an administrator to turn on ' + lowerFirst(off.label) + '.';
        var err = document.createElement('span');
        err.className = 'mg-err'; err.setAttribute('role', 'alert'); err.setAttribute('aria-live', 'assertive');
        gate.append(head, actions, note, err);
        element.replaceChildren(gate);
        return true;
      };
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
          if (gate.hasAttribute('data-mg-inline')) {
            // An in-place notice (mvShowFeatureOff) keeps what the person typed:
            // no reload. Other notices for the same module are now stale.
            document.querySelectorAll('[data-module-gate]').forEach(function (other) {
              if (other !== gate && other.getAttribute('data-module-gate') === section) other.hidden = true;
            });
            gate.replaceChildren(document.createTextNode(
              (gate.getAttribute('data-module-label') || 'It') + ' is on now. Try again.'));
            return;
          }
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
