/* Shared, page-aware Lightwork Copilot. The server owns the page knowledge;
   this client renders text-only guidance and registered, explicitly reviewed actions. */
(function () {
  "use strict";

  var fab = document.getElementById("mv-copilot-fab");
  var panel = document.getElementById("mv-copilot");
  var closeButton = document.getElementById("mv-copilot-close");
  var tips = document.getElementById("mv-copilot-tips");
  var prompts = document.getElementById("mv-copilot-prompts");
  var log = document.getElementById("mv-copilot-log");
  var form = document.getElementById("mv-copilot-form");
  var input = document.getElementById("mv-copilot-input");
  var note = document.getElementById("mv-copilot-note");
  var providerSetup = document.getElementById("mv-copilot-provider-setup");
  var hello = document.getElementById("mv-copilot-hello");
  var sendButton = document.getElementById("mv-copilot-send");
  var clearButton = document.getElementById("mv-copilot-clear");
  if (!fab || !panel || !closeButton || !tips || !prompts || !log ||
      !form || !input || !note || !sendButton || !clearButton || !providerSetup) return;

  var history = [];
  var recent = [];
  var recentLoaded = false;
  var recentAt = 0;
  var recentKey = 'lightwork:copilot:recent:v1';
  var recentAge = 60 * 60 * 1000;
  var recentTimer = null;
  var clientScope = null, failedMessage = null;
  var opener = null;
  var generation = 0, sending = false, catalog = null, actionBusy = false;
  var operations = document.getElementById('mv-copilot-operations');
  var operationStatus = document.getElementById('mv-copilot-operation-status');
  var operationButtons = document.getElementById('mv-copilot-operation-buttons');
  var actionHost = document.getElementById('mv-copilot-action');
  var operationPath = '/api/v1/copilot/operations';
  var allowedOperations = ['learning_example.status', 'learning_example.withdraw'];
  var fallback = {
    title: "Lightwork",
    summary: "Get help with the work in front of you.",
    boundaries: [
      "Ask for help or choose an available action below.",
      "You review and confirm changes before they happen."
    ],
    next_steps: ["Check whether the page says it is ready before you start something."],
    suggested_questions: [
      "What can I do on this page?",
      "What should I verify before I continue?"
    ]
  };

  function clear(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function forgetRecent() {
    if (recentTimer !== null) { clearTimeout(recentTimer); recentTimer = null; }
    recent = []; history = []; recentLoaded = false; recentAt = 0;
    try { sessionStorage.removeItem(recentKey); } catch (_) { /* This tab cannot retain chat. */ }
  }

  function stopPendingReply() {
    generation += 1; sending = false; input.readOnly = false;
    sendButton.disabled = false; sendButton.removeAttribute('aria-busy');
  }

  function armRecentExpiry() {
    if (recentTimer !== null) clearTimeout(recentTimer);
    if (recentAt) recentTimer = setTimeout(expireRecent,
      Math.max(1, recentAge - (Date.now() - recentAt) + 1));
  }

  function rememberRecent(question, reply) {
    if (!clientScope) return;
    recent.push({role: 'user', text: question}, {role: 'bot', text: reply});
    recent = recent.slice(-8);
    recentAt = Date.now();
    try { sessionStorage.setItem(recentKey, JSON.stringify({scope: clientScope, at: recentAt, turns: recent})); }
    catch (_) { /* The visible conversation still works in this page. */ }
    armRecentExpiry();
  }

  function expireRecent() {
    if (recentAt && Date.now() - recentAt > recentAge) {
      stopPendingReply();
      forgetRecent(); recentLoaded = true; clear(log);
      failedMessage = null; input.value = '';
      note.textContent = 'The earlier chat expired after an hour without a reply.';
    }
  }

  // Start the expiry clock even if this page loads while Copilot stays closed.
  // The transcript is not rendered until the current account scope is checked.
  try {
    var waiting = JSON.parse(sessionStorage.getItem(recentKey) || 'null');
    if (waiting && Number.isFinite(waiting.at)) {
      recentAt = waiting.at;
      if (waiting.at > Date.now() || Date.now() - waiting.at > recentAge) forgetRecent();
      else armRecentExpiry();
    }
  } catch (_) { forgetRecent(); }

  function restoreRecent(scope) {
    if (recentLoaded) return;
    recentLoaded = true;
    var saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(recentKey) || 'null'); } catch (_) { /* Ignore malformed local state. */ }
    if (!saved || saved.scope !== scope || !Number.isFinite(saved.at) ||
        saved.at > Date.now() || Date.now() - saved.at > recentAge || !Array.isArray(saved.turns) ||
        saved.turns.length > 8 || saved.turns.some(function (turn) {
          return !turn || !['user', 'bot'].includes(turn.role) || typeof turn.text !== 'string' ||
            !turn.text || turn.text.length > 2000;
        })) {
      forgetRecent(); recentLoaded = true; return;
    }
    recent = saved.turns;
    recentAt = saved.at;
    armRecentExpiry();
    history = recent.filter(function (turn) { return turn.role === 'user'; })
      .slice(-6).map(function (turn) { return {role: 'user', content: turn.text}; });
    var pending = Array.from(log.children);
    clear(log);
    recent.forEach(function (turn) { addMessage(turn.role, turn.text); });
    pending.forEach(function (row) { log.appendChild(row); });
  }

  function addMessage(role, text, actionLink) {
    var message = document.createElement("div");
    message.className = "mv-copilot__msg mv-copilot__msg--" + role;
    message.textContent = String(text);
    if (role === "bot" && actionLink && typeof actionLink.href === "string" &&
        /^\/[a-z0-9/-]{0,159}$/.test(actionLink.href) &&
        typeof actionLink.label === "string") {
      var link = document.createElement("a");
      link.href = actionLink.href;
      link.textContent = actionLink.label.slice(0, 80);
      message.appendChild(document.createElement("br"));
      message.appendChild(link);
    }
    log.appendChild(message);
    log.scrollTop = log.scrollHeight;
    return message;
  }

  function completeReply(question, reply, actionLink) {
    input.value = '';
    addMessage('bot', reply, actionLink);
    rememberRecent(question, reply);
    // Earlier assistant text is display-only; only user questions become
    // untrusted context on a later model request.
    history.push({role: 'user', content: question});
    history = history.slice(-6);
  }

  function renderContext(context) {
    var selected = context && typeof context === "object" ? context : fallback;
    clear(tips);
    (selected.boundaries || fallback.boundaries).slice(0, 3).forEach(function (text) {
      var item = document.createElement("li");
      item.textContent = String(text);
      tips.appendChild(item);
    });
    clear(prompts);
    (selected.suggested_questions || fallback.suggested_questions).slice(0, 3)
      .forEach(function (question) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "mv-copilot__prompt";
        button.textContent = String(question);
        button.addEventListener("click", function () {
          if (sending) return;
          input.value = String(question);
          input.focus();
        });
        prompts.appendChild(button);
      });
  }

  var setupLinks = ["/start", "/settings#settings-integrations", "/settings#settings-models",
    "/settings#settings-maintenance", "/providers"];

  function renderProviderSetup(payload) {
    var setup = payload && payload.provider_setup;
    var readiness = payload && payload.workspace_readiness;
    var next = readiness && readiness.next_action;
    var routeStatus = payload && payload.model_route_status;
    var modelBlocked = routeStatus === "pricing" || routeStatus === "model";
    // Not ready: say so plainly, with one button to the place that fixes it,
    // instead of an empty panel. The built-in page guide still answers.
    if (modelBlocked && hello) {
      hello.textContent = String(payload.model_route_reason || "Choose an available AI model in Settings.") +
        " The built-in page guide still answers questions about this page.";
    } else if (readiness && readiness.verdict !== "ready" && hello) {
      hello.textContent = "Connect an AI service to use Copilot. " + String(readiness.reason || "") +
        " Until then, the built-in page guide answers questions about this page.";
    }
    if (!modelBlocked && next && typeof next.href === "string" && setupLinks.indexOf(next.href) !== -1) {
      setup = {href: next.href, label: next.label};
    }
    var href = setup && typeof setup.href === "string" ? setup.href : "";
    var label = setup && typeof setup.label === "string" ? setup.label : "";
    var allowed = setupLinks.indexOf(href) !== -1;
    providerSetup.hidden = !allowed;
    if (!allowed) return;
    providerSetup.setAttribute("href", href);
    providerSetup.textContent = label || "Open the in-app setup guide";
  }

  async function loadContext() {
    var started = generation;
    renderContext(fallback);
    try {
      var response = await fetch(
        "/api/v1/copilot/context?page=" + encodeURIComponent(window.location.pathname),
        {headers: {Accept: "application/json"}, credentials: "same-origin", cache: "no-store"}
      );
      if (!response.ok) throw new Error("context unavailable");
      var payload = await response.json();
      if (started !== generation || panel.hidden) return;
      renderContext(payload);
      renderProviderSetup(payload);
    } catch (_error) {
      if (started !== generation || panel.hidden) return;
      note.textContent = "Using the local page guide.";
    }
  }

  async function deadlineFetch(url, options) {
    var control = new AbortController(), timer = setTimeout(function () { control.abort(); }, 45000);
    try { return await fetch(url, Object.assign({}, options, {signal: control.signal})); }
    finally { clearTimeout(timer); }
  }
  async function verifyScope(started) {
    var response = await deadlineFetch('/api/v1/copilot/context?page=' + encodeURIComponent(window.location.pathname),
      {headers: {Accept: 'application/json'}, credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok || response.redirected) {
      if ([401, 403].includes(response.status) || response.redirected) { forgetRecent(); input.value = ''; clear(log); clientScope = null; }
      throw new Error('scope unavailable');
    }
    var fresh = await response.json();
    if (started !== generation || panel.hidden) return false;
    if (!fresh || typeof fresh.client_context !== 'string' || !/^[a-f0-9]{64}$/.test(fresh.client_context)) throw new Error('scope unavailable');
    if (clientScope && clientScope !== fresh.client_context) {
      forgetRecent(); input.value = ''; clear(log); catalog = null;
      clientScope = fresh.client_context;
      note.textContent = 'Your account or workspace changed. Earlier questions and drafts were cleared. Enter a new question for this workspace.';
      return false;
    }
    clientScope = fresh.client_context; restoreRecent(clientScope);
    renderContext(fresh); renderProviderSetup(fresh); return true;
  }

  function controller() { return window.LightworkLearningExample; }
  function matchingSelection(value, selected) {
    return value && selected && value.kind === 'flow_run' && value.flow_id === selected.flow_id && value.run_id === selected.run_id;
  }
  async function operationRequest(path, options) {
    var response = await fetch(operationPath + path, Object.assign({
      headers: {'Content-Type': 'application/json', Accept: 'application/json'},
      credentials: 'same-origin', cache: 'no-store'
    }, options));
    var body = null;
    try { body = await response.json(); } catch (_) { /* Raw errors stay out of the UI. */ }
    return {ok: response.ok, status: response.status, body: body};
  }
  function validCatalog(body) {
    var current = controller();
    return current && body && body.schema === 1 && matchingSelection(body.selection, current.selection)
      && typeof body.scope === 'string' && /^[a-f0-9]{64}$/.test(body.scope)
      && Array.isArray(body.operations);
  }
  function renderOperations(body) {
    clear(operationButtons);
    body.operations.filter(function (item) { return item && allowedOperations.includes(item.id); })
      .forEach(function (item) {
        var button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn--ghost';
        button.textContent = item.id === 'learning_example.status' ? 'Check status' : 'Stop using this example';
        button.disabled = item.available !== true || actionBusy;
        button.addEventListener('click', function () { openOperation(item.id); });
        operationButtons.appendChild(button);
      });
    operations.hidden = false;
    operationStatus.textContent = 'Actions apply to the learning example from the run you are viewing.';
  }
  async function loadOperations(started) {
    var current = controller();
    if (!current || !operations || !operationButtons || !actionHost) return null;
    try {
      var selected = current.selection;
      var result = await operationRequest('?flow_id=' + encodeURIComponent(selected.flow_id)
        + '&run_id=' + encodeURIComponent(selected.run_id), {method: 'GET'});
      if (started !== generation || panel.hidden) return null;
      var pending = validCatalog(result.body) && result.body.record && result.body.record.status === 'audit_pending';
      if ((!result.ok && !pending) || !validCatalog(result.body)) {
        var hadContext = !!catalog;
        var code = result.body && result.body.detail && result.body.detail.code;
        if (result.status === 401 || result.status === 409 ||
            (result.status === 403 && code !== 'operation_disabled')) {
          forgetRecent(); input.value = ""; clear(log);
        }
        catalog = null; clear(operationButtons); operations.hidden = false;
        operationStatus.textContent = 'Actions are unavailable here. Your unsent question is kept unless access changed.';
        if (hadContext || actionHost.contains(document.getElementById('le-card'))) current.invalidate();
        return null;
      }
      if (catalog && catalog.scope !== result.body.scope) {
        current.invalidate(); forgetRecent(); input.value = ""; clear(log);
        note.textContent = 'Your workspace or access changed. Earlier questions and reviews have been cleared.';
      }
      catalog = result.body; renderOperations(catalog); return catalog;
    } catch (_) {
      if (started === generation && !panel.hidden) {
        catalog = null; clear(operationButtons); operations.hidden = false;
        operationStatus.textContent = 'We could not check available actions. Close and reopen Copilot to try again.';
      }
      return null;
    }
  }
  async function openOperation(id) {
    if (actionBusy || !allowedOperations.includes(id) || !controller() || controller().isBusy()) return false;
    actionBusy = true; var started = generation;
    operationStatus.textContent = 'Checking the current example…';
    Array.from(operationButtons.children).forEach(function (button) { button.disabled = true; });
    try {
      var fresh = await loadOperations(started);
      if (!fresh || started !== generation || panel.hidden) return false;
      var registered = fresh.operations.find(function (item) { return item.id === id && item.available === true; });
      if (!registered || !controller().attach(actionHost, fresh)) return false;
      if (id === 'learning_example.withdraw') await controller().review();
      else { var refresh = document.getElementById('le-refresh'); if (refresh) refresh.focus(); }
      return true;
    } finally {
      actionBusy = false;
      if (catalog && started === generation && !panel.hidden) renderOperations(catalog);
    }
  }

  document.addEventListener('lightwork:copilot-context-changed', function () {
    stopPendingReply(); forgetRecent(); recentLoaded = true; clear(log);
    failedMessage = null; input.value = ''; catalog = null;
    note.textContent = 'The selected work changed. Earlier questions and drafts were cleared.';
    if (controller()) controller().invalidate();
  });
  window.addEventListener('focus', expireRecent);
  document.addEventListener('visibilitychange', expireRecent);

  clearButton.addEventListener('click', function () {
    if (sending) stopPendingReply();
    forgetRecent(); recentLoaded = true; clear(log); failedMessage = null;
    input.value = ''; note.textContent = 'Recent chat cleared from this tab. An earlier request may still finish, but its reply will not appear here.'; input.focus();
  });

  function open() {
    generation += 1;
    expireRecent();
    opener = document.activeElement;
    panel.hidden = false;
    fab.setAttribute("aria-expanded", "true");
    loadContext();
    verifyScope(generation).catch(function () { note.textContent = "Current workspace could not be checked. Your draft will not be sent until access is confirmed."; });
    loadOperations(generation);
    if (window.LightworkProductOperations) window.LightworkProductOperations.open();
    input.focus();
  }

  function close() {
    if (sending) note.textContent = "The earlier request may still finish on the server. Its reply will not appear after closing; sending again starts a new request.";
    generation += 1; sending = false; input.readOnly = false; sendButton.disabled = false; sendButton.removeAttribute('aria-busy');
    if (controller()) controller().release();
    if (window.LightworkProductOperations) window.LightworkProductOperations.close();
    panel.hidden = true;
    fab.setAttribute("aria-expanded", "false");
    if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
  }

  fab.addEventListener("click", function () { panel.hidden ? open() : close(); });
  closeButton.addEventListener("click", close);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.hidden) {
      if (event.defaultPrevented) return;
      event.preventDefault();
      close();
    }
  });

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    expireRecent();
    var message = (input.value || "").trim();
    if (!message || sending || actionBusy || (controller() && controller().isBusy())
        || (window.LightworkProductOperations && window.LightworkProductOperations.isBusy())) return;
    sending = true; var started = generation;
    // Keep the editable draft until its matching response succeeds.
    input.readOnly = true;
    note.textContent = "";
    var requestRow;
    if (failedMessage && failedMessage.isConnected && failedMessage.textContent === message) requestRow = failedMessage;
    else requestRow = addMessage("user", message);
    failedMessage = null;
    sendButton.disabled = true;
    sendButton.setAttribute("aria-busy", "true");
    try {
      if (!(await verifyScope(started))) return;
      if (catalog && controller()) {
        var proposed = await operationRequest('/propose', {method: 'POST', body: JSON.stringify({
          selection: controller().selection, expected_scope: catalog.scope, message: message
        })});
        if (started !== generation || panel.hidden) return;
        var pendingProposal = validCatalog(proposed.body) && proposed.body.record
          && proposed.body.record.status === 'audit_pending';
        if ((!proposed.ok && !pendingProposal) || !validCatalog(proposed.body)
            || proposed.body.scope !== catalog.scope) {
          if ([401,403,409].includes(proposed.status) || (validCatalog(proposed.body) && proposed.body.scope !== catalog.scope)) { controller().invalidate(); forgetRecent(); input.value = ""; clear(log); }
          catalog = null;
          throw new Error('Current operation context could not be confirmed');
        }
        if (pendingProposal) {
          if (await openOperation('learning_example.status')) {
            if (started === generation && !panel.hidden) completeReply(message, 'This decision is waiting for its audit record. Check the current status before doing anything else.');
          } else if (started === generation && !panel.hidden) {
            var statusKept = requestRow.isConnected && input.value.trim() === message;
            failedMessage = statusKept ? requestRow : null;
            note.textContent = statusKept
              ? 'The current example status could not be opened. Your question is kept; use the native page to check it before trying again.'
              : 'Access or selection changed. Earlier questions were cleared. Reopen the native page to check current status.';
          }
          return;
        }
        var proposal = proposed.body.proposal;
        if (proposal && allowedOperations.includes(proposal.operation_id)) {
          if (await openOperation(proposal.operation_id)) {
            if (started === generation && !panel.hidden) completeReply(message, proposal.operation_id === 'learning_example.withdraw'
              ? 'Review this example below. Enter your reason in the form and confirm when you are ready.'
              : 'Here is the current learning example.');
          } else if (started === generation && !panel.hidden) {
            var proposalKept = requestRow.isConnected && input.value.trim() === message;
            failedMessage = proposalKept ? requestRow : null;
            note.textContent = proposalKept
              ? 'The current example could not be opened. Your question is kept; use the native page or retry after checking access.'
              : 'Access or selection changed. Earlier questions were cleared. Reopen the native page to check current status.';
          }
          return;
        }
      }
      var productProposal = window.LightworkProductOperations && await window.LightworkProductOperations.propose(message);
      if (productProposal === 'unavailable') {
        if (started === generation && !panel.hidden) {
          failedMessage = requestRow;
          note.textContent = 'Copilot could not open the current control. Your question is kept. Check the native page or retry after the connection returns.';
        }
        return;
      }
      if (productProposal) {
        if (started === generation && !panel.hidden)
          completeReply(message, 'Review the selected control and its current source below. Nothing has been changed.');
        return;
      }
      var response = await deadlineFetch("/api/v1/copilot", {
        method: "POST",
        headers: {"Content-Type": "application/json", Accept: "application/json", "X-Lightwork-Copilot-Context": clientScope},
        credentials: "same-origin",
        body: JSON.stringify({
          message: message,
          page: window.location.pathname,
          history: history.slice(-6)
        })
      });
      var payload;
      try { payload = await response.json(); }
      catch (_error) { payload = {detail: "The server returned an unreadable response."}; }
      if (started !== generation || panel.hidden) return;
      if (!response.ok || !payload.reply) throw new Error('unavailable');
      if (!(await verifyScope(started))) return;
      completeReply(message, payload.reply, payload.action_link);
      if (payload.page_context) renderContext(payload.page_context);
      renderProviderSetup(payload);
      if (payload.mode === "built_in_fallback") {
        note.textContent = payload.warning || "The AI provider is not available — using the built-in page guide.";
      } else if (payload.mode === "built_in") {
        note.textContent = payload.warning || "Built-in page guide — no AI provider needed.";
      } else {
        note.textContent = payload.warning ||
          "AI answers can be wrong — check them against what the page shows before acting.";
      }
    } catch (error) {
      if (started === generation && !panel.hidden) { failedMessage = requestRow; note.textContent = 'Copilot could not answer. Your question is still here; edit it or press Send to retry. A timed-out AI request may still finish on the server.'; }
    } finally {
      if (started === generation) {
        sending = false; input.readOnly = false; sendButton.disabled = false;
        sendButton.removeAttribute("aria-busy");
        var productForm = document.getElementById('mv-product-operation-form');
        if (!panel.hidden && !actionHost.contains(document.activeElement) &&
            !(productForm && productForm.contains(document.activeElement))) input.focus();
      }
    }
  });
}());
