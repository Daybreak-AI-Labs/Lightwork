/* Sign-in timer (WCAG 2.2.1 Timing Adjustable).
 *
 * Two minutes before this browser's sign-in ends, ask whether to stay signed
 * in. A local sign-in is kept going in place (POST /auth/session/renew); a
 * company sign-in signs in again with the identity provider. When the sign-in
 * has ended, say so, keep what was typed on this page (this tab only, never
 * passwords, files or secret-looking fields) and come back here after
 * signing in again, with the typed values put back.
 *
 * The page's <meta name="lightwork-session-*"> tags carry the end time, the
 * server clock, how to stay signed in and where to sign in. Another tab may
 * have kept the session going, so the current end is read again before any
 * warning or notice.
 */
(function () {
  'use strict';
  var WARN_SECONDS = 120, KEEP_MINUTES = 120, LONGEST_WAIT = 6 * 3600 * 1000;
  var SECRET = /pass|secret|token|key|otp|code/i;
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.getAttribute('content') || '' : ''; }
  function t(text) { return window.LightworkI18n ? window.LightworkI18n.text(text) : text; }
  var here = window.location.pathname + window.location.search;
  var KEY = 'lightwork.session-kept:' + here;
  var signIn = meta('lightwork-session-sign-in') || '/auth/local-login';
  var renews = meta('lightwork-session-renew') === 'renew';
  var ends = parseInt(meta('lightwork-session-expires'), 10) || 0;
  // Server clock minus this computer's, so a wrong local clock never warns early or late.
  var skew = (parseInt(meta('lightwork-session-now'), 10) || Date.now() / 1000) - Date.now() / 1000;
  var timer = null, warned = false, notice = null;

  function now() { return Date.now() / 1000 + skew; }
  function signInUrl() {
    return signIn + (signIn.indexOf('?') >= 0 ? '&' : '?') + 'return_to=' + encodeURIComponent(here);
  }

  // ---- keep what was typed ------------------------------------------------
  function keepable(c) {
    return c.name && !/^(password|file|hidden|submit|button|reset|image)$/.test(c.type || '') &&
      !SECRET.test(c.name) && !SECRET.test(c.id || '') && c.autocomplete !== 'one-time-code' &&
      !c.hasAttribute('data-mv-keep-off') && !c.disabled;
  }
  function formKey(form, index) { return form.id ? '#' + form.id : (form.getAttribute('action') || '') + '@' + index; }
  function snapshot() {
    var forms = [];
    Array.prototype.forEach.call(document.forms, function (form, index) {
      var values = {}, any = false;
      Array.prototype.forEach.call(form.elements, function (c) {
        if (!keepable(c)) return;
        if (c.type === 'checkbox' || c.type === 'radio') {
          values[c.name] = values[c.name] || [];
          if (c.checked) { values[c.name].push(c.value); any = true; }
        } else if (c.value) { values[c.name] = c.value; any = true; }
      });
      if (any) forms.push({key: formKey(form, index), values: values});
    });
    try {
      if (forms.length) sessionStorage.setItem(KEY, JSON.stringify({at: Date.now(), forms: forms}));
    } catch (_) { /* private mode: nothing more can be kept */ }
    return forms.length;
  }
  function restore() {
    var saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(KEY) || 'null'); sessionStorage.removeItem(KEY); } catch (_) { return; }
    if (!saved || !Array.isArray(saved.forms) || Date.now() - saved.at > KEEP_MINUTES * 60000) return;
    var put = 0;
    Array.prototype.forEach.call(document.forms, function (form, index) {
      var entry = saved.forms.filter(function (f) { return f.key === formKey(form, index); })[0];
      if (!entry) return;
      Array.prototype.forEach.call(form.elements, function (c) {
        if (!keepable(c) || !(c.name in entry.values)) return;
        var v = entry.values[c.name];
        if (c.type === 'checkbox' || c.type === 'radio') c.checked = Array.isArray(v) && v.indexOf(c.value) >= 0;
        else if (!c.value || c.value === c.defaultValue) c.value = v;
        else return;
        c.dispatchEvent(new Event('input', {bubbles: true}));
        c.dispatchEvent(new Event('change', {bubbles: true}));
        put += 1;
      });
    });
    if (put) say(t('You are signed in again. What you typed before is back on this page. Check it, then save.'), false);
  }

  // ---- notices ---------------------------------------------------------------
  function say(text, ended) {
    if (!notice) {
      notice = document.createElement('div');
      notice.className = 'panel mv-session-notice';
      notice.setAttribute('role', ended ? 'alert' : 'status');
      var main = document.querySelector('main') || document.body;
      main.insertBefore(notice, main.firstChild);
    }
    notice.textContent = '';
    var line = document.createElement('p'); line.textContent = text; notice.appendChild(line);
    if (ended) {
      var again = document.createElement('a');
      again.className = 'btn'; again.href = signInUrl(); again.textContent = t('Sign in again');
      again.addEventListener('click', function () { snapshot(); });
      notice.appendChild(again);
    }
    return notice;
  }
  function ended() {
    clearTimeout(timer);
    var kept = snapshot();
    say(t(kept ? 'Your sign-in has ended. What you typed on this page is kept. Sign in again to come back here and carry on.'
      : 'Your sign-in has ended. Sign in again to come back to this page.'), true);
    var link = notice.querySelector('a'); if (link) link.focus();
  }

  // ---- the timer -----------------------------------------------------------
  async function current() {
    try {
      var r = await fetch('/auth/session/status', {credentials: 'same-origin', cache: 'no-store'});
      var d = await r.json();
      if (!d || d.signed_in !== true) return 0;
      skew = (d.now || Date.now() / 1000) - Date.now() / 1000;
      return d.expires_at;
    } catch (_) { return ends; }   // offline: keep the time the page was given
  }
  function schedule() {
    clearTimeout(timer);
    var left = ends - now();
    var wait = (warned ? left : left - WARN_SECONDS) * 1000;
    timer = setTimeout(check, Math.max(0, Math.min(wait, LONGEST_WAIT)));
  }
  async function stay() {
    if (!renews) { snapshot(); window.location.assign(signInUrl()); return; }
    try {
      var r = await fetch('/auth/session/renew', {method: 'POST', credentials: 'same-origin', headers: {'Accept': 'application/json'}});
      var d = await r.json().catch(function () { return {}; });
      if (r.ok && d.expires_at) {
        ends = d.expires_at; warned = false; schedule();
        if (window.mvToast) window.mvToast(t('You are still signed in.'), {quiet: true});
        return;
      }
    } catch (_) { /* fall through to signing in again */ }
    if (ends - now() > 0) { snapshot(); window.location.assign(signInUrl()); } else ended();
  }
  async function check() {
    ends = await current();
    var left = ends - now();
    if (!ends || left <= 0) { ended(); return; }
    if (left > WARN_SECONDS + 5) { warned = false; schedule(); return; }   // kept going elsewhere
    if (warned) { schedule(); return; }
    warned = true; schedule();
    var minutes = Math.max(1, Math.round(left / 60));
    var message = t('Your sign-in ends in about ' + minutes + (minutes === 1 ? ' minute.' : ' minutes.')) + ' ' +
      t('Stay signed in to keep working. What you typed stays on this page either way.');
    var ok = window.mvConfirm ? await window.mvConfirm(message, {okText: 'Stay signed in', cancelText: 'Not now'}) : false;
    if (ok) await stay();
  }

  restore();
  if (ends) schedule();
  window.LightworkSession = {check: check, snapshot: snapshot, restore: restore};
}());
