/* Settings > License: install a license file, change what this installation is
   licensed for, issue a license for a customer and make a signing key pair.
   The private key from "Make a signing key pair" lives only in this page's
   memory and the element that shows it; nothing here stores it. */
(function () {
  'use strict';

  var API = '/api/v1/admin/license';
  var REPLACE = 'This replaces the license installed now. Paid features change to match the new license straight away.';
  var page = document.getElementById('license-page');
  if (!page) return;

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function say(id, text) { var box = $(id); if (box) box.textContent = text || ''; }
  function when(isoDate) {
    if (!isoDate) return '';
    var parsed = new Date(isoDate + 'T00:00:00');
    return isNaN(parsed.getTime()) ? isoDate : parsed.toLocaleDateString();
  }
  function installed() { return page.dataset.installed === 'true'; }

  async function plainFailure(failure, action) {
    // A server sentence written for people is shown as is; anything else
    // becomes the shared plain message.
    try {
      if (failure && typeof failure.clone === 'function') {
        var body = await failure.clone().json();
        var plain = window.mvPlainDetail ? window.mvPlainDetail(body && body.detail) : '';
        if (plain) return plain;
      }
    } catch (ignored) { /* not a readable body */ }
    return window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.';
  }

  function postJson(path, body) {
    return window.fetch(API + path, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'},
      body: JSON.stringify(body)
    });
  }

  // Replacing an installed license always asks first. The server refuses an
  // unconfirmed replace too (409), e.g. when a license arrived since the page
  // loaded; then the person is asked and the request is sent once more.
  async function installing(send) {
    var confirmed = false;
    if (installed()) {
      if (!(await window.mvConfirm(REPLACE, {okText: 'Replace license'}))) return null;
      confirmed = true;
    }
    var response = await send(confirmed);
    if (response.status === 409 && !confirmed) {
      if (!(await window.mvConfirm(REPLACE, {okText: 'Replace license'}))) return null;
      response = await send(true);
    }
    if (!response.ok) throw response;
    return response.json();
  }

  function facts(rows) {
    var dl = el('dl', 'license-facts');
    rows.forEach(function (row) {
      if (!row[1]) return;
      dl.appendChild(el('dt', null, row[0]));
      dl.appendChild(el('dd', null, row[1]));
    });
    return dl;
  }

  function licenseFacts(lic) {
    return facts([
      ['Licensed to', lic.customer],
      ['Plan', lic.plan],
      ['Add-ons', (lic.add_ons || []).join(', ') || 'None'],
      ['Paid features added on their own', (lic.features || []).join(', ')],
      ['Ends on', lic.expires ? when(lic.expires) : 'Does not end'],
      ['Number of people', lic.seats ? String(lic.seats) : '']
    ]);
  }

  function renderCurrent(summary) {
    var box = $('license-current');
    box.textContent = '';
    box.appendChild(facts([
      ['Status', summary.status_label],
      ['Plan', summary.plan],
      ['Licensed to', summary.customer],
      ['Renews or ends', when(summary.expires)],
      ['Add-ons', (summary.add_ons || []).join(', ') || 'None']
    ]));
    var list = el('ul', 'license-features');
    list.setAttribute('aria-label', 'Paid features');
    (summary.features || []).forEach(function (f) {
      var li = el('li');
      li.appendChild(el('span', null, f.label));
      li.appendChild(el('span', 'badge ' + (f.included ? 'active' : 'blocked'), f.included ? 'On' : 'Off'));
      list.appendChild(li);
    });
    box.appendChild(list);
    say('license-current-summary', summary.summary);
  }

  function prefill(form, current) {
    if (!form || !current) return;
    form.elements.customer.value = current.customer || '';
    form.elements.tier.value = current.tier || 'gold';
    form.elements.expires.value = current.expires || '';
    form.elements.grace_days.value = current.grace_days;
    form.elements.seats.value = current.seats || '';
    form.elements.edition.value = current.edition || 'enterprise';
    form.querySelectorAll('input[name="suites"]').forEach(function (box) {
      box.checked = (current.suites || []).indexOf(box.value) !== -1;
    });
    form.querySelectorAll('input[name="features"]').forEach(function (box) {
      box.checked = (current.features || []).indexOf(box.value) !== -1;
    });
  }

  async function refresh() {
    try {
      var response = await window.fetch(API, {credentials: 'same-origin', cache: 'no-store',
        headers: {Accept: 'application/json'}});
      if (!response.ok) throw response;
      var state = await response.json();
      page.dataset.installed = state.installed ? 'true' : 'false';
      renderCurrent(state.summary);
      prefill($('license-set-form'), state.current);
    } catch (failure) {
      say('license-current-summary', await plainFailure(failure, 'show the installed license'));
    }
  }

  function readTerms(form) {
    var fields = form.elements;
    function ticked(name) {
      return Array.prototype.slice.call(form.querySelectorAll('input[name="' + name + '"]:checked'))
        .map(function (box) { return box.value; });
    }
    var seats = String(fields.seats.value || '').trim();
    return {
      customer: String(fields.customer.value || '').trim(),
      tier: fields.tier.value,
      edition: fields.edition.value || 'enterprise',
      suites: ticked('suites'),
      features: ticked('features'),
      expires: fields.expires.value || null,
      grace_days: Number(fields.grace_days.value),
      seats: seats ? Number(seats) : null
    };
  }

  function warningNote(result) {
    return result.warning ? el('p', 'muted', result.warning) : null;
  }

  function technical(result) {
    var details = el('details', 'license-more');
    details.appendChild(el('summary', null, 'Technical details'));
    details.appendChild(el('p', 'muted', 'The public key that signed this license. Whoever runs your server adds it ' +
      'to the trusted license keys so the license turns on paid features.'));
    details.appendChild(el('code', null, result.signed_with.public_key));
    var copy = el('button', 'btn btn--ghost', 'Copy the public key');
    copy.type = 'button';
    copy.addEventListener('click', function () { window.mvCopy(result.signed_with.public_key); });
    details.appendChild(copy);
    return details;
  }

  function download(file) {
    var blob = new Blob([file.text], {type: 'application/json'});
    var url = URL.createObjectURL(blob);
    var link = el('a');
    link.href = url;
    link.download = file.name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  // ---- Install a license file: check first, then install on request.
  var fileForm = $('license-file-form');
  if (fileForm) {
    fileForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var input = $('license-file');
      var file = input.files && input.files[0];
      var box = $('license-file-result');
      box.textContent = '';
      if (!file) { say('license-file-status', 'Choose a license file first.'); return; }
      if (file.size > 64 * 1024) { say('license-file-status', 'That file is too large to be a license file.'); return; }
      function send(options) {
        var data = new FormData();
        data.append('file', file);
        Object.keys(options).forEach(function (key) { data.append(key, options[key] ? 'true' : 'false'); });
        return window.fetch(API + '/install-file', {method: 'POST', credentials: 'same-origin', body: data});
      }
      say('license-file-status', 'Checking the file…');
      try {
        var response = await send({check_only: true});
        if (!response.ok) throw response;
        var check = await response.json();
        var result = el('div', 'license-result');
        result.appendChild(el('p', null, check.status_sentence));
        result.appendChild(licenseFacts(check.license));
        if (check.note) result.appendChild(el('p', 'muted', check.note));
        var go = el('button', 'btn btn--primary', 'Install this license');
        go.type = 'button';
        go.addEventListener('click', async function () {
          go.disabled = true;
          say('license-file-status', 'Installing…');
          try {
            var done = await installing(function (confirmed) { return send({confirm: confirmed}); });
            if (!done) { say('license-file-status', 'Nothing was installed.'); go.disabled = false; return; }
            say('license-file-status', 'The license is installed. Paid features now follow it.');
            go.remove();
            await refresh();
          } catch (failure) {
            say('license-file-status', await plainFailure(failure, 'install the license'));
            go.disabled = false;
          }
        });
        var actions = el('p', 'license-actions');
        actions.appendChild(go);
        result.appendChild(actions);
        box.appendChild(result);
        say('license-file-status', 'The file checks out. Nothing is installed until you choose Install this license.');
      } catch (failure) {
        say('license-file-status', await plainFailure(failure, 'check the license file'));
      }
    });
  }

  // ---- Change what this installation is licensed for: sign and install.
  var setForm = $('license-set-form');
  if (setForm) {
    setForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var terms = readTerms(setForm);
      var box = $('license-set-result');
      box.textContent = '';
      var button = setForm.querySelector('button[type="submit"]');
      button.disabled = true;
      say('license-set-status', 'Signing…');
      try {
        var result = await installing(function (confirmed) {
          return postJson('/sign', Object.assign({}, terms, {install: true, confirm: confirmed}));
        });
        if (!result) { say('license-set-status', 'Nothing was changed.'); return; }
        say('license-set-status', 'The new license is installed.');
        var note = warningNote(result);
        if (note) box.appendChild(note);
        await refresh();
      } catch (failure) {
        say('license-set-status', await plainFailure(failure, 'change the license'));
      } finally {
        button.disabled = false;
      }
    });
  }

  // ---- Issue a license for a customer: sign, then download or install.
  var issueForm = $('license-issue-form');
  if (issueForm) {
    issueForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var box = $('license-issue-result');
      box.textContent = '';
      var button = issueForm.querySelector('button[type="submit"]');
      button.disabled = true;
      say('license-issue-status', 'Signing…');
      try {
        var response = await postJson('/sign', Object.assign({}, readTerms(issueForm), {install: false}));
        if (!response.ok) throw response;
        var result = await response.json();
        var card = el('div', 'license-result');
        card.appendChild(licenseFacts(result.license));
        var note = warningNote(result);
        if (note) card.appendChild(note);
        var actions = el('p', 'license-actions');
        var save = el('button', 'btn btn--primary', 'Download the signed license');
        save.type = 'button';
        save.addEventListener('click', function () { download(result.file); });
        var install = el('button', 'btn', 'Install on this server');
        install.type = 'button';
        install.addEventListener('click', async function () {
          install.disabled = true;
          say('license-issue-status', 'Installing…');
          try {
            var done = await installing(function (confirmed) {
              return postJson('/install-issued', {license: result.file.text, confirm: confirmed});
            });
            if (!done) { say('license-issue-status', 'Nothing was installed.'); install.disabled = false; return; }
            say('license-issue-status', 'The license is installed on this server.');
            await refresh();
          } catch (failure) {
            say('license-issue-status', await plainFailure(failure, 'install the license'));
            install.disabled = false;
          }
        });
        actions.appendChild(save);
        actions.appendChild(install);
        card.appendChild(actions);
        card.appendChild(technical(result));
        box.appendChild(card);
        say('license-issue-status', 'The license is signed. Download it for the customer, or install it here.');
      } catch (failure) {
        say('license-issue-status', await plainFailure(failure, 'sign the license'));
      } finally {
        button.disabled = false;
      }
    });
  }

  // ---- Make a signing key pair: shown once, never stored.
  var keygen = $('license-keygen');
  if (keygen) {
    keygen.addEventListener('click', async function () {
      var box = $('license-keygen-result');
      box.textContent = '';
      box.hidden = true;
      keygen.disabled = true;
      say('license-keygen-status', 'Making a key pair…');
      try {
        var response = await window.fetch(API + '/keygen', {method: 'POST', credentials: 'same-origin',
          cache: 'no-store', headers: {Accept: 'application/json'}});
        if (!response.ok) throw response;
        var pair = await response.json();
        box.appendChild(el('strong', null, 'Your new private key (shown once)'));
        box.appendChild(el('code', null, pair.private_key));
        box.appendChild(el('p', null, 'Store this private key in your secret manager as ' + pair.store_as +
          '. Never put it in settings or send it by email. Lightwork has not kept a copy, so if it is lost, make a new pair.'));
        box.appendChild(el('strong', null, 'Public key'));
        box.appendChild(el('code', null, pair.public_key));
        box.appendChild(el('p', null, 'Give the public key to whoever runs each server that should accept your licenses. ' +
          'They add it to that server’s trusted license keys (' + pair.trusted_keys_name + ').'));
        var actions = el('p', 'license-actions');
        var copyPrivate = el('button', 'btn btn--primary', 'Copy the private key');
        copyPrivate.type = 'button';
        copyPrivate.addEventListener('click', function () { window.mvCopy(pair.private_key); });
        var copyPublic = el('button', 'btn', 'Copy the public key');
        copyPublic.type = 'button';
        copyPublic.addEventListener('click', function () { window.mvCopy(pair.public_key); });
        var hide = el('button', 'btn btn--ghost', 'I have stored it, hide the key');
        hide.type = 'button';
        hide.addEventListener('click', function () {
          box.textContent = '';
          box.hidden = true;
          pair = null;
          say('license-keygen-status', 'The key is hidden. It cannot be shown again.');
        });
        actions.appendChild(copyPrivate);
        actions.appendChild(copyPublic);
        actions.appendChild(hide);
        box.appendChild(actions);
        box.hidden = false;
        say('license-keygen-status', 'A new key pair is ready. Copy the private key now: it is shown only once.');
      } catch (failure) {
        say('license-keygen-status', await plainFailure(failure, 'make a key pair'));
      } finally {
        keygen.disabled = false;
      }
    });
  }
})();
