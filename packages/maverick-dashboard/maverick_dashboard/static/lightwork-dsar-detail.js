// One privacy request: every step is a button (start, confirm identity, add a
// system, check Microsoft 365, create the secure link, collect, erase, close).
(function () {
  'use strict';
  var doc = document;
  var page = doc.getElementById('dsar-page');
  if (!page) return;
  var requestId = page.getAttribute('data-request-id');
  var subject = page.getAttribute('data-subject') || '';
  var channel = page.getAttribute('data-channel') || '';
  var fulfillmentId = page.getAttribute('data-fulfillment-id');
  var revision = parseInt(page.getAttribute('data-revision') || '0', 10);
  var statusEl = doc.getElementById('dsar-status');
  var base = '/api/v1/privacy/dsar/';

  function $(id) { return doc.getElementById(id); }
  function say(text) { if (statusEl) statusEl.textContent = text; }
  function fail(failure, action) {
    say(window.mvErrorText ? window.mvErrorText(failure, action) : 'Could not ' + action + '. Please try again.');
  }
  function ask(message, okText) {
    return window.mvConfirm ? window.mvConfirm(message, { okText: okText }) : Promise.resolve(false);
  }
  function form(title, fields, okText) {
    return window.mvForm ? window.mvForm(title, fields, { okText: okText }) : Promise.resolve(null);
  }
  function call(method, url, body) {
    var opts = { method: method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) { if (!r.ok) throw r; return r.json(); });
  }
  function reloadSoon() { setTimeout(function () { location.reload(); }, 700); }
  function human(v) { return window.mvHuman ? window.mvHuman(v) : String(v); }
  function on(id, handler) { var el = $(id); if (el) el.addEventListener('click', handler); }

  var METHODS = [
    { value: 'email_challenge', label: 'Confirmed by email' },
    { value: 'account_login', label: 'Signed in to their account' },
    { value: 'document_check', label: 'Checked an ID document' },
    { value: 'in_person', label: 'Checked in person' },
    { value: 'verified_by_processor', label: 'Checked by a service provider' },
    { value: 'other', label: 'Another way' },
  ];

  on('dsar-acknowledge', function () {
    call('POST', base + encodeURIComponent(requestId) + '/acknowledge').then(function () {
      say('Receipt marked as confirmed.'); reloadSoon();
    }).catch(function (f) { fail(f, 'mark receipt confirmed'); });
  });

  on('dsar-extend', function () {
    form('Record an extension', [
      { key: 'reason', label: 'Why do you need more time?', type: 'text', required: true,
        placeholder: 'For example: many systems to search', help: 'This reason goes into the notice you send.' },
      { key: 'days', label: 'Extra days (leave empty for the full two months under GDPR)', type: 'text',
        placeholder: '45' },
    ], 'Record').then(function (values) {
      if (!values) return;
      var body = { reason: values.reason };
      if (values.days) body.days = parseInt(values.days, 10);
      return call('POST', base + encodeURIComponent(requestId) + '/extend', body).then(function () {
        say('Extension recorded. Now send the extension notice.'); reloadSoon();
      });
    }).catch(function (f) { fail(f, 'record the extension'); });
  });

  on('dsar-verify-manual', function () {
    form('Confirm identity another way', [
      { key: 'method', label: 'How did you confirm it?', type: 'select',
        options: METHODS.filter(function (m) { return m.value !== 'email_challenge'; }), default: 'document_check', required: true },
      { key: 'reference', label: 'Where is the proof kept?', type: 'text', required: true,
        placeholder: 'For example: support ticket 4521' },
    ], 'Confirm').then(function (values) {
      if (!values) return;
      return call('POST', base + encodeURIComponent(requestId) + '/verify', values).then(function () {
        say('Identity confirmed; the request is now open.'); reloadSoon();
      });
    }).catch(function (f) { fail(f, 'confirm identity'); });
  });

  doc.querySelectorAll('.dsar-send').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var kind = btn.getAttribute('data-kind');
      var fields = [];
      if (kind === 'refusal') {
        fields.push({ key: 'lawful_basis', label: 'Lawful basis for refusing (goes to the requester)', type: 'text', required: true });
      }
      if (kind === 'completion') {
        fields.push({ key: 'secure_link', label: 'Secure link (create it in step 3 first)', type: 'text', required: true, placeholder: 'https://…/dsar/response/…' });
        fields.push({ key: 'secure_link_expires', label: 'Link expires on (YYYY-MM-DD)', type: 'text', required: true });
      }
      var ready = fields.length ? form('Send: ' + human(kind), fields, 'Send') : ask('Send the "' + human(kind) + '" notice to the requester now?', 'Send');
      ready.then(function (values) {
        if (!values) return;
        var body = { kind: kind };
        if (typeof values === 'object') Object.keys(values).forEach(function (k) { body[k] = values[k]; });
        return call('POST', base + encodeURIComponent(requestId) + '/send', body).then(function (out) {
          say(out.sent ? 'Notice sent.' : 'Notice not sent: ' + out.reason); reloadSoon();
        });
      }).catch(function (f) { fail(f, 'send the notice'); });
    });
  });

  on('dsar-start', function () {
    call('POST', base + encodeURIComponent(requestId) + '/fulfillment').then(function () {
      say('Started. Next, confirm who they are.'); reloadSoon();
    }).catch(function (f) { fail(f, 'start answering this request'); });
  });

  on('dsar-identity', function () {
    form('Confirm identity', [
      { key: 'method', label: 'How did you confirm it?', type: 'select', options: METHODS, default: 'email_challenge', required: true },
      { key: 'reference', label: 'Where is the proof kept?', type: 'text', required: true,
        placeholder: 'For example: support ticket 4521', help: 'A note or reference so others can find the proof later.' },
    ], 'Confirm').then(function (values) {
      if (!values) return;
      return call('POST', base + 'fulfillment/' + encodeURIComponent(fulfillmentId) + '/identity', {
        method: values.method, reference: values.reference, revision: revision,
      }).then(function () { say('Identity confirmed.'); reloadSoon(); });
    }).catch(function (f) { fail(f, 'confirm their identity'); });
  });

  on('dsar-source', function () {
    form('Add a system you searched', [
      { key: 'source', label: 'System', type: 'text', required: true, placeholder: 'For example: Salesforce' },
      { key: 'reference', label: 'What you did', type: 'text', required: true, placeholder: 'For example: exported their contact record' },
      { key: 'items', label: 'Records found', type: 'number', default: 0, required: true },
    ], 'Add').then(function (values) {
      if (!values) return;
      var items = parseInt(values.items, 10);
      if (!(items >= 0)) { say('Enter how many records were found (0 or more).'); return; }
      return call('POST', base + 'fulfillment/' + encodeURIComponent(fulfillmentId) + '/sources', {
        source: values.source, reference: values.reference, items: items, revision: revision,
      }).then(function () { say('Added.'); reloadSoon(); });
    }).catch(function (f) { fail(f, 'add that system'); });
  });

  on('dsar-m365', function () {
    say('Checking Microsoft 365…');
    call('POST', base + encodeURIComponent(requestId) + '/collect-microsoft365', { revision: revision }).then(function (body) {
      say(body.message || 'Microsoft 365 checked.'); reloadSoon();
    }).catch(function (f) {
      if (f && f.status === 503) { say('Microsoft 365 is not connected. Ask an administrator to connect it.'); return; }
      if (f && f.status === 422) { say('This person is not a Microsoft 365 account, so their data must be collected another way.'); return; }
      fail(f, 'check Microsoft 365');
    });
  });

  on('dsar-link', function () {
    form('Create a secure link', [
      { key: 'hours', label: 'Link works for', type: 'select', default: '72', options: [
        { value: '24', label: '1 day' }, { value: '72', label: '3 days' },
        { value: '168', label: '7 days' }, { value: '336', label: '14 days' },
      ] },
      { key: 'single', label: 'Allow it to be opened only once', type: 'bool', default: true },
    ], 'Create link').then(function (values) {
      if (!values) return;
      var body = { expires_hours: parseInt(values.hours || '72', 10), revision: revision };
      if (values.single) body.max_reads = 1;
      return call('POST', base + 'fulfillment/' + encodeURIComponent(fulfillmentId) + '/response', body).then(function (res) {
        var box = $('dsar-link-box');
        $('dsar-link-url').value = location.origin + res.response_path;
        box.hidden = false;
        $('dsar-link-url').focus();
        say('Link created. Copy it now; it will not be shown again.');
        if (res.revision) { revision = res.revision; page.setAttribute('data-revision', String(revision)); }
      });
    }).catch(function (f) { fail(f, 'create the link'); });
  });
  on('dsar-link-copy', function () {
    var v = $('dsar-link-url').value;
    if (window.mvCopy) window.mvCopy(v);
  });

  on('dsar-fulfill', function () {
    var kind = page.getAttribute('data-kind');
    var msg = kind === 'access' || kind === 'portability'
      ? 'Collect everything Lightwork holds about ' + subject + ' and mark this request answered?'
      : 'Mark this request ready for deletion? Their data is erased in the next step.';
    ask(msg, 'Continue').then(function (ok) {
      if (!ok) return;
      call('POST', base + encodeURIComponent(requestId) + '/fulfill').then(function () {
        say('Done.'); reloadSoon();
      }).catch(function (f) { fail(f, 'complete this step'); });
    });
  });

  on('dsar-close', function () {
    ask('Close this request? It will no longer count toward the deadline list.', 'Close request').then(function (ok) {
      if (!ok) return;
      call('POST', base + encodeURIComponent(requestId) + '/close').then(function () {
        say('Request closed.'); reloadSoon();
      }).catch(function (f) { fail(f, 'close the request'); });
    });
  });

  // Erasure: pick the matching person (by their identifier) and erase.
  on('dsar-erase', function () {
    call('GET', '/api/v1/compliance/people').then(function (body) {
      // The request's own channel is where it arrived (often email), not
      // necessarily where the person chatted, so match on the person only
      // and list the request's channel first.
      var people = (body.people || []).filter(function (p) { return p.user === subject; });
      people.sort(function (a, b) { return (b.channel === channel) - (a.channel === channel); });
      if (!people.length) {
        say('Lightwork holds no conversations for ' + subject + '. There is nothing to erase here; close the request once other systems are done.');
        return null;
      }
      var options = people.map(function (p, i) {
        return { value: String(i), label: p.person + ' (' + p.channel_label + ')' };
      });
      return form('Erase their data', [
        { key: 'who', label: 'Which account', type: 'select', options: options, default: '0', required: true },
      ], 'Next').then(function (values) {
        if (!values) return null;
        var chosen = people[parseInt(values.who || '0', 10)];
        return ask('Permanently erase everything Lightwork holds about ' + chosen.person +
          ' on ' + chosen.channel_label + '? This cannot be undone.', 'Erase').then(function (ok) {
          if (!ok) return null;
          return call('POST', '/api/v1/compliance/people/erase', { channel: chosen.channel, user: chosen.user }).then(function (res) {
            say((res.message || 'Erased.') + (res.clean === false
              ? ' Keep this request open until the erasure is certified.'
              : ' You can now close the request.'));
          });
        });
      });
    }).catch(function (f) { fail(f, 'erase their data'); });
  });

  // Where to search: a readable checklist.
  on('dsar-worksheet', function () {
    var out = $('dsar-worksheet-result');
    call('GET', base + encodeURIComponent(requestId) + '/estate-worksheet').then(function (w) {
      out.textContent = '';
      var h = doc.createElement('h3'); h.textContent = 'Where to search'; out.appendChild(h);
      var ul = doc.createElement('ul'); ul.className = 'dsar-list';
      function item(text) { var li = doc.createElement('li'); li.textContent = text; ul.appendChild(li); }
      var held = (w.platform_holdings || {}).sections || {};
      var total = Object.keys(held).reduce(function (n, k) { return n + (held[k] || 0); }, 0);
      item('Lightwork itself: ' + total + ' record(s) found.');
      var dm = w.data_map || {};
      if (dm.enabled) {
        (dm.assets || []).forEach(function (a) {
          var cats = (a.categories || []).map(function (c) { return human(c).toLowerCase(); });
          item(human(a.system || 'System') + ' – ' + (a.name || 'data store') +
            (cats.length ? ' (holds ' + cats.join(', ') + ')' : ''));
        });
        if (!(dm.assets || []).length) item('Your data map lists no systems that hold personal details.');
      } else {
        item('Your data map is not set up, so other systems are not listed.');
      }
      var vendors = (w.processors || {}).vendors || [];
      vendors.forEach(function (v) { item('Ask vendor ' + v.name + ' for their records.'); });
      var docs = (w.documents || {}).hits || [];
      docs.forEach(function (d) { item('Document: ' + (d.title || d.name || 'untitled')); });
      out.appendChild(ul);
    }).catch(function (f) { fail(f, 'load the search checklist'); });
  });
})();
