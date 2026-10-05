// Shared by the Learning, Learned, Workforce, Tasks and Savings buttons that
// replace terminal commands: plain requests, plain errors, and background
// jobs (start, show progress, offer Stop when the job can stop, then show
// the result). Everything a person sees is a plain sentence.
(function () {
  'use strict';
  if (window.LWJobs) return;
  var POLL_MS = 1500;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function money(v) { return window.mvMoney ? window.mvMoney(Number(v) || 0) : '$' + (Number(v) || 0).toFixed(2); }
  function when(ts) { return (typeof window.mvWhen === 'function' ? window.mvWhen(ts) : '') || ''; }

  // A failed request as one sentence. Our routes write their refusals for
  // people; anything else gets the shared wording.
  async function problem(failure, action) {
    if (failure && [404, 409, 413, 422, 503].indexOf(failure.status) >= 0) {
      var text = window.mvPlainDetail ? window.mvPlainDetail(failure.detail) : '';
      if (text) return text;
    }
    if (failure && failure.status === 403) return 'Only an administrator can ' + action + '.';
    return typeof window.mvErrorText === 'function' ? window.mvErrorText(failure, action) : 'Could not ' + action + '.';
  }

  async function request(url, opts) {
    opts = opts || {};
    var init = { method: opts.method || 'GET', raw: !!opts.raw, body: opts.json };
    if (opts.json === undefined && opts.body !== undefined) {
      init.headers = { 'Content-Type': opts.type || 'application/octet-stream' };
      init.body = opts.body;
    }
    return window.mvFetchJSON(url, init);
}

  function saveBlob(blob, name) {
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  }

  function technical(lines, title) {
    var box = el('details', 'lw-ops-tech');
    box.appendChild(el('summary', null, title || 'Technical details'));
    var list = el('ul');
    (lines || []).forEach(function (t) { if (t !== '' && t != null) list.appendChild(el('li', null, String(t))); });
    box.appendChild(list);
    return box;
  }

  function table(headers, rows) {
    var t = el('table');
    var head = el('tr');
    headers.forEach(function (h) { var th = el('th', null, h); th.scope = 'col'; head.appendChild(th); });
    var thead = el('thead'); thead.appendChild(head); t.appendChild(thead);
    var body = el('tbody');
    rows.forEach(function (cells) {
      var tr = el('tr');
      cells.forEach(function (c) {
        var td = el('td');
        if (c instanceof Node) td.appendChild(c); else td.textContent = c == null ? '' : String(c);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    t.appendChild(body);
    return t;
  }

  var STATE_TEXT = {
    running: 'Working on it. You can leave this page; the result stays here until Lightwork restarts.',
    stopping: 'Stopping at the next safe point…',
    done: 'Finished.',
    stopped: 'Stopped early. Here is what was done before it stopped.',
    failed: ''
  };

  // Start a job (POST ``url``), then poll it until it ends. ``ui`` holds:
  // status (element for the progress line), out (element for the result),
  // render(result, job) -> Node|Node[]|null, action (words for errors).
  async function run(url, init, ui) {
    ui.out.textContent = '';
    ui.status.textContent = 'Starting…';
    var job;
    try {
      job = (await request(url, init)).job;
    } catch (failure) {
      ui.status.textContent = await problem(failure, ui.action);
      return null;
    }
    return follow(job, ui);
  }

  function follow(job, ui) {
    return new Promise(function (resolve) {
      var stop = null;
      function show(j) {
        ui.status.textContent = j.state === 'failed' ? (j.problem || 'This job could not finish.') : STATE_TEXT[j.state] || '';
        if (j.can_stop && !stop) {
          stop = el('button', 'btn btn--ghost', 'Stop');
          stop.type = 'button';
          stop.addEventListener('click', async function () {
            if (!(await window.mvConfirm('Stop this job? It stops at the next safe point and keeps what it has already done.',
              { okText: 'Stop' }))) return;
            stop.disabled = true;
            try {
              await request('/api/v1/learning-ops/jobs/' + encodeURIComponent(j.id) + '/stop', { method: 'POST', json: {} });
            } catch (failure) {
              ui.status.textContent = await problem(failure, 'stop the job');
              stop.disabled = false;
            }
          });
          ui.status.appendChild(document.createTextNode(' '));
          ui.status.appendChild(stop);
        } else if (stop && j.can_stop) {
          ui.status.appendChild(document.createTextNode(' '));
          ui.status.appendChild(stop);
        }
      }
      async function tick(j) {
        show(j);
        if (j.state === 'running' || j.state === 'stopping') {
          setTimeout(async function () {
            try {
              tick((await request('/api/v1/learning-ops/jobs/' + encodeURIComponent(j.id))).job);
            } catch (failure) {
              ui.status.textContent = await problem(failure, 'check on the job');
              resolve(null);
            }
          }, POLL_MS);
          return;
        }
        ui.out.textContent = '';
        if (j.result && j.state !== 'failed' && ui.render) {
          var nodes = ui.render(j.result, j);
          (Array.isArray(nodes) ? nodes : [nodes]).forEach(function (n) { if (n) ui.out.appendChild(n); });
        }
        resolve(j);
      }
      tick(job);
    });
  }

  // Wire a button to start a job; ``prepare`` returns {url, init} or null
  // (null = the person cancelled or something is missing).
  function wire(button, prepare, ui) {
    if (!button) return;
    button.addEventListener('click', async function () {
      if (button.dataset.inFlight === 'true') return;
      button.dataset.inFlight = 'true';
      try {
        var spec = await prepare();
        if (!spec) return;
        button.disabled = true;
        var job = await run(spec.url, spec.init, ui);
        if (job && ui.after) ui.after(job);
      } finally {
        button.disabled = false;
        delete button.dataset.inFlight;
      }
    });
  }

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(reader.error); };
      reader.readAsArrayBuffer(file);
    });
  }

  function readText(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(reader.error); };
      reader.readAsText(file);
    });
  }

  window.LWJobs = { el: el, plural: plural, money: money, when: when, problem: problem,
    request: request, saveBlob: saveBlob, technical: technical, table: table, run: run,
    follow: follow, wire: wire, readFile: readFile, readText: readText };
})();
