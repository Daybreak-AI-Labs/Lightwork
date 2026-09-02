(function () {
  "use strict";

  var root = document.getElementById("acp-root");
  if (!root) { return; }

  var apiBase = root.dataset.apiBase || "/api/v1/agent-control-plane";
  var canOperate = root.dataset.canOperate === "true";
  var canAudit = root.dataset.canAudit === "true";
  var canAdmin = root.dataset.canAdmin === "true";
  var canHalt = root.dataset.canHalt === "true";
  var haltScope = root.dataset.haltScope || "unknown";
  var maxResponseBytes = 2 * 1024 * 1024;
  var maxAnnouncedErrorChars = 1000;
  var configuredTimeout = Number(root.dataset.requestTimeoutMs || 15000);
  var requestTimeoutMs = Number.isFinite(configuredTimeout) && configuredTimeout >= 10
    ? Math.min(configuredTimeout, 120000)
    : 15000;
  var rosterLimit = 100;
  var rosterOffset = 0;
  var rosterNextOffset = null;
  var desiredRosterOffset = 0;
  var mutationInFlight = false;
  var controlPlaneReady = false;
  var haltAuthorityReady = false;
  var operationRequested = 0;
  var toolPattern = /^[A-Za-z0-9_.-]{1,64}$/;

  var state = document.getElementById("acp-state");
  var output = document.getElementById("acp-output");
  var operationState = document.getElementById("acp-operation-state");
  var rows = document.getElementById("acp-rows");
  var rosterHeading = document.getElementById("acp-roster-title");
  var rosterPage = document.getElementById("acp-roster-page");
  var rosterPrevious = document.getElementById("acp-roster-prev");
  var rosterNext = document.getElementById("acp-roster-next");
  var verbControls = document.getElementById("acp-verb-controls");
  var verbSelect = document.getElementById("acp-verb-select");
  var verbSubmit = document.getElementById("acp-verb-submit");
  var attenuateControls = document.getElementById("acp-attenuate-controls");
  var haltControls = document.getElementById("acp-halt-controls");
  var clearHalt = document.getElementById("acp-clear-halt");

  var planeRequested = 0;
  var planeApplied = 0;
  var planeRefreshPromise = null;
  var planeLastResult = {
    planeOk: false, halt: null, haltComplete: false, planeError: null, haltError: null
  };

  function fail(message) {
    var error = new Error(message);
    error.code = "invalid_input";
    return error;
  }

  function boundedError(error) {
    var message = error && error.message ? error.message : String(error);
    if (message.length <= maxAnnouncedErrorChars) { return message; }
    return message.slice(0, maxAnnouncedErrorChars - 1) + "…";
  }

  function responseDetail(payload) {
    var detail = payload && payload.detail;
    if (typeof detail === "string" && detail) { return detail; }
    if (Array.isArray(detail)) {
      var entries = detail.slice(0, 8).map(function (entry) {
        if (typeof entry === "string") { return entry.slice(0, 250); }
        if (!entry || typeof entry !== "object") { return "Invalid request value."; }
        var location = Array.isArray(entry.loc)
          ? entry.loc.filter(function (part) {
            return part !== "body" && part !== "query" && part !== "path";
          }).map(String).join(".")
          : "";
        var message = typeof entry.msg === "string" && entry.msg
          ? entry.msg
          : "Invalid request value.";
        return (location ? location + ": " : "") + message.slice(0, 250);
      });
      if (detail.length > entries.length) {
        entries.push(String(detail.length - entries.length) + " more validation error(s)");
      }
      if (entries.length) { return boundedError(entries.join("; ")); }
    }
    if (detail && typeof detail === "object" &&
        typeof detail.message === "string" && detail.message) {
      return detail.message;
    }
    return "Control plane request failed.";
  }

  function utf8ByteLength(value) {
    var bytes = 0;
    for (var index = 0; index < value.length; index += 1) {
      var code = value.charCodeAt(index);
      if (code <= 0x7f) { bytes += 1; }
      else if (code <= 0x7ff) { bytes += 2; }
      else if (code >= 0xd800 && code <= 0xdbff &&
          index + 1 < value.length &&
          value.charCodeAt(index + 1) >= 0xdc00 &&
          value.charCodeAt(index + 1) <= 0xdfff) {
        bytes += 4;
        index += 1;
      } else { bytes += 3; }
    }
    return bytes;
  }

  async function boundedResponseText(response) {
    var declared = Number(response.headers.get("content-length") || 0);
    if (declared > maxResponseBytes) {
      if (response.body && typeof response.body.cancel === "function") {
        await response.body.cancel();
      }
      throw fail("Response exceeds the console byte bound.");
    }
    if (response.body && typeof response.body.getReader === "function" &&
        typeof TextDecoder === "function") {
      var reader = response.body.getReader();
      var decoder = new TextDecoder("utf-8", { fatal: false });
      var received = 0;
      var text = "";
      while (true) {
        var chunk = await reader.read();
        if (chunk.done) { break; }
        received += chunk.value.byteLength;
        if (received > maxResponseBytes) {
          await reader.cancel();
          throw fail("Response exceeds the console byte bound.");
        }
        text += decoder.decode(chunk.value, { stream: true });
      }
      return text + decoder.decode();
    }
    // Test doubles and older non-browser fetch implementations may not expose a
    // ReadableStream. Keep the validation byte-accurate there, while real
    // browsers take the bounded streaming path above before allocating the body.
    var fallback = await response.text();
    if (utf8ByteLength(fallback) > maxResponseBytes) {
      throw fail("Response exceeds the console byte bound.");
    }
    return fallback;
  }

  async function readJson(response) {
    var text = await boundedResponseText(response);
    var payload;
    try { payload = text ? JSON.parse(text) : {}; }
    catch (_error) { throw fail("The server returned invalid JSON."); }
    if (!response.ok) {
      var detail = responseDetail(payload);
      if (detail === "control_plane_disabled") {
        detail = "The control plane is off: set [control_plane] enable = true.";
      }
      var responseError = fail(detail);
      responseError.status = response.status;
      responseError.payload = payload;
      throw responseError;
    }
    return payload;
  }

  async function requestAt(url, options) {
    var supplied = options || {};
    var settings = Object.assign({ credentials: "same-origin" }, supplied);
    settings.headers = Object.assign(
      { Accept: "application/json" }, supplied.headers || {}
    );
    if (settings.body) { settings.headers["Content-Type"] = "application/json"; }
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timeout = null;
    if (controller) {
      settings.signal = controller.signal;
      timeout = setTimeout(function () { controller.abort(); }, requestTimeoutMs);
    }
    try {
      return await readJson(await fetch(url, settings));
    } catch (error) {
      if (controller && controller.signal.aborted) {
        throw fail("The control-plane request timed out; no result was assumed.");
      }
      throw error;
    } finally {
      if (timeout !== null) { clearTimeout(timeout); }
    }
  }

  function request(path, options) {
    return requestAt(apiBase + path, options);
  }

  function show(label, payload, ticket) {
    if (ticket !== undefined && ticket !== null && ticket !== operationRequested) {
      return false;
    }
    var announced = label;
    if (payload && typeof payload === "object") {
      if (payload.ok === false && typeof payload.detail === "string" && payload.detail) {
        announced += ": " + boundedError(payload.detail);
      }
      if (Object.prototype.hasOwnProperty.call(payload, "audit_written")) {
        announced += payload.audit_written === true
          ? "; control-plane action audit row written"
          : payload.audit_written === false
            ? "; control-plane action audit row was not written"
            : "; control-plane action audit delivery was not confirmed";
      }
    }
    operationState.textContent = announced;
    output.textContent = typeof payload === "string"
      ? payload
      : JSON.stringify(payload, null, 2);
    return true;
  }

  function showError(error, prefix, ticket) {
    if (ticket !== undefined && ticket !== null && ticket !== operationRequested) {
      return false;
    }
    var message = boundedError(error);
    operationState.textContent = (prefix || "Failed") + ": " + message;
    output.textContent = message;
    return true;
  }

  function checkPrincipal(value) {
    var principal = String(value || "");
    if (!principal || principal !== principal.trim() || principal.length > 512 ||
        /[\u0000-\u001f\u007f-\u009f]/.test(principal)) {
      throw fail("Principal must be non-empty printable text without surrounding whitespace (max 512).");
    }
    return principal;
  }

  function setMark(marker, text) {
    state.dataset.state = marker;
    state.textContent = text;
  }

  function setBusy(control, busy) {
    if (!control) { return; }
    if (busy) {
      control.dataset.acpBusy = "true";
      control.setAttribute("aria-busy", "true");
      control.disabled = true;
      return;
    }
    delete control.dataset.acpBusy;
    control.removeAttribute("aria-busy");
    if (control === rosterPrevious) {
      control.disabled = rosterOffset <= 0;
    } else if (control === rosterNext) {
      control.disabled = rosterNextOffset === null;
    } else if (control === clearHalt) {
      control.disabled = !canHalt || !haltAuthorityReady;
    } else if (control === verbSubmit) {
      updateVerbSubmit();
    } else if (control.dataset.requiresControlPlane === "true") {
      control.disabled = !controlPlaneReady;
    } else {
      control.disabled = control.dataset.roleDisabled === "true";
    }
  }

  async function withBusy(control, task) {
    setBusy(control, true);
    try { return await task(); }
    finally { setBusy(control, false); }
  }

  function runGuarded(target, isMutation, task, ownsOutput) {
    if (!target || target.dataset.inFlight === "true") {
      return Promise.resolve(null);
    }
    if (isMutation && mutationInFlight) { return Promise.resolve(null); }
    target.dataset.inFlight = "true";
    if (isMutation) {
      mutationInFlight = true;
      root.dataset.mutationPending = "true";
    }
    var ticket = ownsOutput ? ++operationRequested : null;
    return Promise.resolve()
      .then(function () { return task(ticket); })
      .catch(function (error) {
        if (ownsOutput) { showError(error, undefined, ticket); }
        return null;
      })
      .finally(function () {
        delete target.dataset.inFlight;
        if (isMutation) {
          mutationInFlight = false;
          delete root.dataset.mutationPending;
        }
      });
  }

  async function collectReason(verb, principal) {
    if (typeof window.mvForm !== "function") {
      throw fail("The governed reason dialog is unavailable; no change was sent.");
    }
    var values = await window.mvForm(
      verb.charAt(0).toUpperCase() + verb.slice(1) + " " + principal,
      [{
        key: "reason",
        label: "Reason recorded with the control attempt",
        type: "text",
        required: true,
        placeholder: "Why is this control necessary?",
        help: "Audit delivery can fail soft; verify the ledger after the state refresh."
      }],
      { okText: verb.charAt(0).toUpperCase() + verb.slice(1) + " principal" }
    );
    if (values === null) { return null; }
    var reason = String(values.reason || "").trim();
    if (!reason) { throw fail("A reason is required."); }
    if (reason.length > 500) { throw fail("Reason must be at most 500 characters."); }
    return reason;
  }

  async function confirmAction(message, okText) {
    if (typeof window.mvConfirm !== "function") {
      throw fail("The governed confirmation dialog is unavailable; no change was sent.");
    }
    return window.mvConfirm(message, { okText: okText });
  }

  function updateVerbSubmit() {
    var verb = verbSelect ? verbSelect.value : "";
    var mutation = verb !== "ledger";
    if (verbSubmit) {
      verbSubmit.disabled = verbControls.dataset.roleDisabled === "true" ||
        (mutation && !controlPlaneReady);
    }
  }

  function updateRuntimeControls() {
    var options = verbSelect.querySelectorAll("option[data-requires-control-plane]");
    options.forEach(function (option) { option.disabled = !controlPlaneReady; });
    if (verbControls.dataset.roleDisabled !== "true") {
      verbControls.disabled = false;
      verbControls.removeAttribute("aria-disabled");
    }
    attenuateControls.disabled = !canOperate || !controlPlaneReady;
    attenuateControls.setAttribute("aria-disabled", String(attenuateControls.disabled));
    haltControls.disabled = !canHalt || !haltAuthorityReady;
    haltControls.setAttribute("aria-disabled", String(haltControls.disabled));
    clearHalt.disabled = !canHalt || !haltAuthorityReady;
    var rowMutations = rows.querySelectorAll("button[data-requires-control-plane]");
    rowMutations.forEach(function (button) { button.disabled = !controlPlaneReady; });
    updateVerbSubmit();
  }

  function isNonNegativeInteger(value) {
    return typeof value === "number" && Number.isFinite(value) &&
      Number.isInteger(value) && value >= 0;
  }

  function hasMetricContract(value, names) {
    if (!value || typeof value !== "object" || Array.isArray(value)) { return false; }
    return names.every(function (name) { return isNonNegativeInteger(value[name]); });
  }

  function validatePlaneSnapshot(payload) {
    if (!payload || typeof payload !== "object" ||
        !payload.status || !payload.agents) {
      throw fail("The server returned an invalid control-plane snapshot.");
    }
    var st = payload.status;
    var agents = payload.agents;
    var capturedAt = st.captured_at;
    var rosterCapturedAt = agents.captured_at;
    var complete = st.schema === "lightwork.agent-control-plane-status.v1" &&
      typeof capturedAt === "number" && Number.isFinite(capturedAt) && capturedAt >= 0 &&
      typeof rosterCapturedAt === "number" && Number.isFinite(rosterCapturedAt) &&
      rosterCapturedAt === capturedAt &&
      typeof st.enabled === "boolean" && typeof st.halted === "boolean" &&
      typeof st.halt_scope === "string" && st.halt_scope.length > 0 &&
      typeof st.trust_enforced === "boolean" &&
      typeof st.external_agents_enabled === "boolean" &&
      hasMetricContract(
        st.native, ["principals", "running_goals", "suspended", "contained"]
      ) &&
      hasMetricContract(
        st.external, ["agents", "active", "suspended", "contained"]
      );
    if (!complete) {
      throw fail("The server returned an incomplete control-plane status contract.");
    }
    return { status: st, agents: agents };
  }

  function applyStatus(st, halt, haltError) {
    var haltStatusComplete = !haltError && halt && typeof halt.active === "boolean" &&
      halt.scope === haltScope &&
      (haltScope === "local" ||
        (haltScope === "cluster" && typeof halt.cluster_halt === "boolean")) &&
      st.halted === halt.active;
    haltAuthorityReady = Boolean(haltStatusComplete);
    controlPlaneReady = Boolean(st.enabled === true && haltStatusComplete);
    updateRuntimeControls();
    var native = st && st.native ? st.native : {};
    var external = st && st.external ? st.external : {};
    document.getElementById("acp-native").textContent = String(native.principals ?? "unknown");
    document.getElementById("acp-external").textContent = String(external.agents ?? "unknown");
    var suspended = Number(native.suspended);
    var externalSuspended = Number(external.suspended);
    var nativeContained = Number(native.contained);
    var externalContained = Number(external.contained);
    document.getElementById("acp-suspended").textContent =
      Number.isFinite(suspended) && Number.isFinite(externalSuspended) &&
        Number.isFinite(nativeContained) && Number.isFinite(externalContained)
        ? String(suspended + externalSuspended) + " / " +
          String(nativeContained + externalContained)
        : "unknown";

    var title = document.getElementById("acp-health-title");
    var detail = document.getElementById("acp-health-detail");
    if (!haltStatusComplete) {
      setMark("partial", "Halt unknown");
      title.textContent = "Emergency-stop state is unverified";
      detail.textContent = "The hardened halt status could not be read. Do not assume agents are running.";
    } else if (halt.active) {
      if (haltScope === "cluster" && halt.cluster_halt !== true) {
        setMark("failed", "Partial halt");
        title.textContent = "Cluster halt is not confirmed";
        detail.textContent = "This replica may be stopped while other replicas remain active.";
      } else {
        setMark("failed", haltScope === "cluster" ? "Fleet halted" : "Local halt");
        title.textContent = haltScope === "cluster"
          ? "The shared fleet halt is active"
          : "This deployment is halted locally";
        detail.textContent = "The hardened halt status confirms the stop at its configured scope.";
      }
    } else if (!st.enabled) {
      setMark("partial", "Read-only");
      title.textContent = "Control plane is off";
      detail.textContent = "Bounded roster reads remain available, but mutation verbs are refused until [control_plane] enable = true.";
    } else {
      setMark("ready", "Enabled");
      title.textContent = "Control plane is live";
      detail.textContent = (st.trust_enforced ? "Trust plane enforced. " : "Trust plane open. ") +
        (st.external_agents_enabled ? "External-agent gateway on." : "External-agent gateway off.");
    }
  }

  function cell(text) {
    var td = document.createElement("td");
    td.textContent = text;
    return td;
  }

  function currentRosterFocus() {
    var active = document.activeElement;
    if (!active || !rows.contains(active)) { return null; }
    var principal = active.dataset.acpPrincipal;
    var verb = active.dataset.acpVerb;
    return principal ? { principal: principal, verb: verb || "" } : { principal: "", verb: "" };
  }

  function restoreRosterFocus(previous) {
    if (!previous) { return; }
    var buttons = rows.querySelectorAll("button[data-acp-principal]");
    var principalMatch = null;
    for (var index = 0; index < buttons.length; index += 1) {
      var button = buttons[index];
      if (button.dataset.acpPrincipal !== previous.principal) { continue; }
      if (!principalMatch) { principalMatch = button; }
      if (button.dataset.acpVerb === previous.verb) {
        button.focus();
        return;
      }
    }
    if (principalMatch) { principalMatch.focus(); return; }
    rosterHeading.focus();
  }

  function ceilingOf(row) {
    if (row.kind !== "external") { return "—"; }
    var parts = [];
    parts.push(row.max_risk ? "Maximum risk: " + row.max_risk : "No risk cap");
    parts.push(row.max_dollars === null || row.max_dollars === undefined
      ? "No dollar cap"
      : "Maximum dollars: $" + row.max_dollars);
    if (row.deny_tools && row.deny_tools.length) {
      parts.push("Denied tools: " + row.deny_tools.join(", "));
    }
    if (row.allow_tools && row.allow_tools.length) {
      parts.push("Allowed tools: " + row.allow_tools.join(", "));
    }
    if (row.max_wall_seconds !== null && row.max_wall_seconds !== undefined) {
      parts.push("Maximum wall time: " + row.max_wall_seconds + " seconds");
    }
    return parts.join(" · ");
  }

  function validateToolList(value, field) {
    if (!Array.isArray(value) || value.length > 256 ||
        value.some(function (name) { return !toolPattern.test(name); })) {
      throw fail("The server returned an invalid " + field + " tool list.");
    }
    return value.slice();
  }

  function validateRosterRow(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw fail("The server returned an invalid roster row.");
    }
    var row = Object.assign({}, value);
    row.principal = checkPrincipal(row.principal);
    if (row.kind !== "native" && row.kind !== "external") {
      throw fail("The server returned an invalid roster principal kind.");
    }
    if (typeof row.state !== "string" || !row.state || row.state.length > 64 ||
        /[\u0000-\u001f\u007f-\u009f]/.test(row.state)) {
      throw fail("The server returned an invalid roster state.");
    }
    if (!isNonNegativeInteger(row.running_goals) ||
        typeof row.revoked !== "boolean" || typeof row.contained !== "boolean") {
      throw fail("The server returned invalid roster control state.");
    }
    if (row.platform !== undefined &&
        (typeof row.platform !== "string" || row.platform.length > 128 ||
         /[\u0000-\u001f\u007f-\u009f]/.test(row.platform))) {
      throw fail("The server returned an invalid roster platform label.");
    }
    if (row.revoked_reason !== undefined &&
        (typeof row.revoked_reason !== "string" || row.revoked_reason.length > 500 ||
         /[\u0000-\u001f\u007f-\u009f]/.test(row.revoked_reason))) {
      throw fail("The server returned an invalid roster incident summary.");
    }
    if (row.max_risk !== null && row.max_risk !== undefined &&
        !["low", "medium", "high"].includes(row.max_risk)) {
      throw fail("The server returned an invalid roster risk ceiling.");
    }
    ["max_dollars", "max_wall_seconds"].forEach(function (field) {
      if (row[field] !== null && row[field] !== undefined &&
          (typeof row[field] !== "number" || !Number.isFinite(row[field]) || row[field] <= 0)) {
        throw fail("The server returned an invalid roster numeric ceiling.");
      }
    });
    row.allow_tools = validateToolList(row.allow_tools || [], "allowed");
    row.deny_tools = validateToolList(row.deny_tools || [], "denied");
    return row;
  }

  async function mutatePrincipal(verb, principal, reason, ticket) {
    if (!controlPlaneReady) {
      throw fail("Control-plane state is not verified; no principal change was sent.");
    }
    var body = { principal: principal };
    if (reason !== undefined) { body.reason = reason; }
    var options = { method: "POST", body: JSON.stringify(body) };
    var result = null;
    var requestError = null;
    try {
      result = await request("/principals/" + verb, options);
    } catch (error) {
      requestError = error;
    }
    var refreshed = await refresh();
    if (requestError) {
      if (requestError.payload && requestError.payload.ok === false) {
        show("Not applied: " + verb + " " + principal +
          (refreshed.planeOk ? "" : "; enforced state could not be re-read"),
        requestError.payload, ticket);
        return requestError.payload;
      }
      showError(requestError, refreshed.planeOk
        ? "Request failed; enforced state was re-read"
        : "Request failed; enforced state could not be re-read", ticket);
      return null;
    }
    show((result.ok ? "Applied: " : "Not applied: ") + verb + " " + principal +
      (refreshed.planeOk ? "" : "; enforced state could not be re-read"), result, ticket);
    return result;
  }

  async function ledger(principal, ticket) {
    var query = new URLSearchParams({ principal: principal, limit: "100" });
    var payload = await request("/principals/ledger?" + query.toString());
    if (ticket === operationRequested) {
      show(
        "Ledger: " + principal + " (" + payload.count + " event(s))",
        payload.items,
        ticket
      );
    }
    return payload;
  }

  function verbButton(label, principal, handler, isMutation) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn";
    button.textContent = label;
    button.dataset.acpPrincipal = principal;
    button.dataset.acpVerb = label.toLowerCase();
    if (isMutation) {
      button.dataset.requiresControlPlane = "true";
      button.disabled = !controlPlaneReady;
    }
    button.setAttribute("aria-label", label + " principal " + principal);
    button.addEventListener("click", function () {
      runGuarded(button, isMutation, function (ticket) {
        return handler(button, ticket);
      }, true);
    });
    return button;
  }

  function renderRow(row) {
    var tr = document.createElement("tr");
    var principal = document.createElement("th");
    principal.scope = "row";
    principal.textContent = row.principal;
    tr.appendChild(principal);
    tr.appendChild(cell(row.kind + (row.platform ? " · " + row.platform : "")));
    var stateCell = document.createElement("td");
    var mark = document.createElement("span");
    mark.className = "acp-state";
    mark.dataset.state = row.state;
    mark.textContent = row.state + (row.revoked_reason ? " (" + row.revoked_reason + ")" : "");
    stateCell.appendChild(mark);
    tr.appendChild(stateCell);
    tr.appendChild(cell(String(row.running_goals ?? "unknown")));
    tr.appendChild(cell(ceilingOf(row)));
    var actions = document.createElement("td");
    var verbs = document.createElement("div");
    verbs.className = "acp-verbs";
    if (row.contained) {
      if (canAdmin) {
        verbs.appendChild(verbButton("Release", row.principal, async function (button, ticket) {
          if (!(await confirmAction(
            "Release principal " + row.principal + "? Its durable named-principal protection may be lifted.",
            "Release principal"
          ))) { return null; }
          return withBusy(button, function () {
            return mutatePrincipal("release", row.principal, undefined, ticket);
          });
        }, true));
      }
    } else if (row.revoked) {
      if (canOperate) {
        verbs.appendChild(verbButton("Resume", row.principal, async function (button, ticket) {
          if (!(await confirmAction(
            "Resume principal " + row.principal + "? Its capability may be used again.",
            "Resume principal"
          ))) { return null; }
          return withBusy(button, function () {
            return mutatePrincipal("resume", row.principal, undefined, ticket);
          });
        }, true));
      }
    } else {
      if (canOperate) {
        verbs.appendChild(verbButton("Suspend", row.principal, async function (button, ticket) {
          var reason = await collectReason("suspend", row.principal);
          if (reason === null) { return null; }
          return withBusy(button, function () {
            return mutatePrincipal("suspend", row.principal, reason, ticket);
          });
        }, true));
      }
      if (canAdmin) {
        verbs.appendChild(verbButton("Contain", row.principal, async function (button, ticket) {
          var reason = await collectReason("contain", row.principal);
          if (reason === null) { return null; }
          return withBusy(button, function () {
            return mutatePrincipal("contain", row.principal, reason, ticket);
          });
        }, true));
      }
    }
    if (canAudit) {
      verbs.appendChild(verbButton("Ledger", row.principal, function (button, ticket) {
        return withBusy(button, function () { return ledger(row.principal, ticket); });
      }, false));
    }
    actions.appendChild(verbs);
    tr.appendChild(actions);
    return tr;
  }

  function applyRoster(payload) {
    if (!payload || !Array.isArray(payload.items)) {
      throw fail("The server returned an invalid bounded roster page.");
    }
    var validatedItems = payload.items.map(validateRosterRow);
    var focus = currentRosterFocus();
    var total = Number(payload.total);
    var offset = Number(payload.offset);
    var limit = Number(payload.limit);
    var count = Number(payload.count);
    var end = offset + validatedItems.length;
    var expectedHasMore = end < total;
    var suppliedNext = payload.next_offset === null ? null : Number(payload.next_offset);
    if (!Number.isInteger(total) || total < 0 || !Number.isInteger(offset) || offset < 0 ||
        offset > total || !Number.isInteger(limit) || limit < 1 || limit > rosterLimit ||
        !Number.isInteger(count) || count !== validatedItems.length || count > limit ||
        end > total || payload.has_more !== expectedHasMore ||
        (expectedHasMore && suppliedNext !== end) ||
        (!expectedHasMore && suppliedNext !== null)) {
      throw fail("The server returned invalid roster pagination metadata.");
    }
    rosterOffset = offset;
    rosterNextOffset = expectedHasMore ? suppliedNext : null;
    rows.textContent = "";
    if (!validatedItems.length) {
      var empty = document.createElement("tr");
      empty.appendChild(cell(total === 0
        ? "No governed principals were returned. The API refuses completeness when roster authorities are unreadable."
        : "No principals were returned for this bounded page."));
      empty.firstChild.colSpan = 6;
      rows.appendChild(empty);
    } else {
      validatedItems.forEach(function (row) { rows.appendChild(renderRow(row)); });
    }
    var first = validatedItems.length ? offset + 1 : 0;
    var last = validatedItems.length ? end : 0;
    rosterPage.textContent = "Showing " + first + "–" + last + " of " + total +
      " governed principals (maximum " + rosterLimit + " on this page).";
    rosterPrevious.disabled = offset <= 0;
    rosterNext.disabled = payload.has_more !== true || rosterNextOffset === null;
    restoreRosterFocus(focus);
    updateRuntimeControls();
  }

  function renderRosterError(error) {
    var focus = currentRosterFocus();
    var message = boundedError(error);
    rows.textContent = "";
    var failed = document.createElement("tr");
    failed.appendChild(cell("Roster unavailable: " + message));
    failed.firstChild.colSpan = 6;
    rows.appendChild(failed);
    rosterPage.textContent = "Roster page unavailable: " + message;
    rosterPrevious.disabled = true;
    rosterNext.disabled = true;
    restoreRosterFocus(focus);
  }

  function haltPayloadComplete(payload) {
    return Boolean(payload && typeof payload.active === "boolean" &&
      payload.scope === haltScope &&
      (haltScope === "local" ||
        (haltScope === "cluster" && typeof payload.cluster_halt === "boolean")));
  }

  async function drainPlaneRefreshes() {
    while (planeApplied < planeRequested) {
      var ticket = planeRequested;
      var offset = desiredRosterOffset;
      var results = await Promise.allSettled([
        request("/snapshot?limit=" + rosterLimit + "&offset=" + offset),
        requestAt("/api/v1/halt")
      ]);
      if (ticket !== planeRequested) { continue; }
      var halt = results[1].status === "fulfilled" ? results[1].value : null;
      var haltError = results[1].status === "rejected" ? results[1].reason : null;
      var refreshResult = {
        planeOk: false,
        halt: halt,
        haltComplete: haltPayloadComplete(halt),
        planeError: null,
        haltError: haltError
      };
      var planeError = null;
      if (results[0].status === "fulfilled") {
        try {
          var validated = validatePlaneSnapshot(results[0].value);
          applyStatus(validated.status, halt, haltError);
          applyRoster(validated.agents);
          refreshResult.planeOk = true;
        } catch (invalidSnapshotError) {
          planeError = invalidSnapshotError;
        }
      } else {
        planeError = results[0].reason;
      }
      if (planeError) {
        refreshResult.planeError = planeError;
        controlPlaneReady = false;
        haltAuthorityReady = haltPayloadComplete(halt);
        updateRuntimeControls();
        setMark("failed", "Unavailable");
        document.getElementById("acp-health-title").textContent =
          "Control-plane state is unavailable";
        document.getElementById("acp-health-detail").textContent = boundedError(planeError);
        renderRosterError(planeError);
      }
      planeLastResult = refreshResult;
      planeApplied = ticket;
    }
    return planeLastResult;
  }

  function refreshPlane(offset) {
    if (Number.isInteger(offset) && offset >= 0) { desiredRosterOffset = offset; }
    controlPlaneReady = false;
    haltAuthorityReady = false;
    updateRuntimeControls();
    setMark("partial", "Refreshing");
    document.getElementById("acp-health-title").textContent =
      "Refreshing control-plane state";
    document.getElementById("acp-health-detail").textContent =
      "Mutation controls remain disabled until the bounded snapshot and halt authority agree.";
    planeRequested += 1;
    if (!planeRefreshPromise) {
      planeRefreshPromise = drainPlaneRefreshes().finally(function () {
        planeRefreshPromise = null;
      });
    }
    return planeRefreshPromise;
  }

  function refreshStatus() {
    return refreshPlane(rosterOffset);
  }

  function refreshRoster(offset) {
    return refreshPlane(offset);
  }

  function refresh() {
    return refreshPlane(rosterOffset);
  }

  function haltMatches(payload, expectedActive) {
    if (!haltPayloadComplete(payload)) { return false; }
    if (!expectedActive) {
      return haltScope === "cluster"
        ? payload.active === false && payload.cluster_halt === false
        : payload.active === false;
    }
    if (haltScope === "cluster") {
      return payload.active === true && payload.cluster_halt === true;
    }
    return payload.active === true;
  }

  async function changeHalt(expectedActive, reason, ticket) {
    if (!haltAuthorityReady) {
      throw fail("Halt authority is not verified; no halt change was sent.");
    }
    var mutationError = null;
    try {
      await requestAt("/api/v1/halt", expectedActive
        ? { method: "POST", body: JSON.stringify({ reason: reason }) }
        : { method: "DELETE" });
    } catch (error) {
      mutationError = error;
    }

    var refreshed = await refreshStatus();
    var verified = refreshed.halt;
    if (!refreshed.haltComplete) {
      var verificationError = refreshed.haltError || fail(
        "The hardened halt endpoint returned an incomplete status."
      );
      var combined = mutationError
        ? boundedError(mutationError) + " Halt state could not be re-read: " + boundedError(verificationError)
        : "Halt state could not be re-read: " + boundedError(verificationError);
      throw fail(combined);
    }
    if (mutationError) {
      show("Halt request failed; current state was re-read: " + boundedError(mutationError), {
        request_error: boundedError(mutationError),
        verified_state: verified
      }, ticket);
      return false;
    }
    if (!haltMatches(verified, expectedActive)) {
      show("Halt change was not confirmed at the required scope", verified, ticket);
      return false;
    }
    show(
      expectedActive ? "Deployment halt verified" : "Deployment halt cleared and verified",
      verified,
      ticket
    );
    return true;
  }

  document.getElementById("acp-refresh").addEventListener("click", function (event) {
    runGuarded(event.currentTarget, false, function () {
      return withBusy(event.currentTarget, refresh);
    }, false);
  });

  document.getElementById("acp-load-roster").addEventListener("click", function (event) {
    runGuarded(event.currentTarget, false, function () {
      return withBusy(event.currentTarget, function () { return refreshRoster(rosterOffset); });
    }, false);
  });

  rosterPrevious.addEventListener("click", function (event) {
    runGuarded(event.currentTarget, false, function () {
      var previousOffset = Math.max(0, rosterOffset - rosterLimit);
      return withBusy(event.currentTarget, function () { return refreshRoster(previousOffset); });
    }, false);
  });

  rosterNext.addEventListener("click", function (event) {
    runGuarded(event.currentTarget, false, function () {
      if (rosterNextOffset === null) { return null; }
      return withBusy(event.currentTarget, function () { return refreshRoster(rosterNextOffset); });
    }, false);
  });

  document.getElementById("acp-verb-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var formElement = event.currentTarget;
    var submitter = event.submitter;
    var form = new FormData(formElement);
    var verb = String(form.get("verb") || "");
    runGuarded(formElement, verb !== "ledger", async function (ticket) {
      var principal = checkPrincipal(form.get("principal"));
      var reason = String(form.get("reason") || "").trim();
      if (verb === "ledger") {
        if (!canAudit) { throw fail("Audit permission is required."); }
        return withBusy(submitter, function () { return ledger(principal, ticket); });
      }
      var canApplyVerb =
        ((verb === "suspend" || verb === "resume") && canOperate) ||
        ((verb === "contain" || verb === "release") && canAdmin);
      if (!canApplyVerb) {
        throw fail("This principal action is not available to the current role.");
      }
      if (verb === "suspend" || verb === "contain") {
        if (!reason) { throw fail("A reason is required to " + verb + "."); }
        if (reason.length > 500) { throw fail("Reason must be at most 500 characters."); }
      }
      if (!(await confirmAction(
        verb.charAt(0).toUpperCase() + verb.slice(1) + " principal " + principal +
          "? The console will attempt to re-read enforced state after the request.",
        verb.charAt(0).toUpperCase() + verb.slice(1) + " principal"
      ))) { return null; }
      return withBusy(submitter, function () {
        return mutatePrincipal(verb, principal,
          verb === "suspend" || verb === "contain" ? reason : undefined,
          ticket);
      });
    }, true);
  });

  verbSelect.addEventListener("change", updateVerbSubmit);

  document.getElementById("acp-attenuate-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var formElement = event.currentTarget;
    var submitter = event.submitter;
    var form = new FormData(formElement);
    runGuarded(formElement, true, async function (ticket) {
      if (!canOperate) { throw fail("Operate permission is required."); }
      var principal = checkPrincipal(form.get("principal"));
      var body = { deny_tools: [] };
      String(form.get("deny_tools") || "").split(",").forEach(function (tool) {
        var name = tool.trim();
        if (name) { body.deny_tools.push(name); }
      });
      if (body.deny_tools.length > 64) {
        throw fail("At most 64 denied tools may be added at once.");
      }
      if (body.deny_tools.some(function (name) { return !toolPattern.test(name); })) {
        throw fail("Denied tool names must use letters, digits, _, . or - (max 64 each).");
      }
      var risk = String(form.get("max_risk") || "");
      if (risk) {
        if (!["low", "medium", "high"].includes(risk)) {
          throw fail("Max risk must be low, medium or high.");
        }
        body.max_risk = risk;
      }
      var dollars = String(form.get("max_dollars") || "").trim();
      if (dollars) {
        body.max_dollars = Number(dollars);
        if (!Number.isFinite(body.max_dollars) || body.max_dollars <= 0 ||
            body.max_dollars > 1000000) {
          throw fail("Max dollars must be greater than 0 and at most 1,000,000.");
        }
      }
      var wall = String(form.get("max_wall_seconds") || "").trim();
      if (wall) {
        body.max_wall_seconds = Number(wall);
        if (!Number.isFinite(body.max_wall_seconds) || body.max_wall_seconds <= 0 ||
            body.max_wall_seconds > 31536000) {
          throw fail("Max wall seconds must be greater than 0 and at most 31,536,000.");
        }
      }
      if (!body.deny_tools.length && body.max_risk === undefined &&
          body.max_dollars === undefined && body.max_wall_seconds === undefined) {
        throw fail("At least one attenuation restriction is required.");
      }
      if (!(await confirmAction(
        "Narrow the capability ceiling for " + principal + "? This operation never widens it.",
        "Narrow ceiling"
      ))) { return null; }
      return withBusy(submitter, async function () {
        if (!controlPlaneReady) {
          throw fail("Control-plane state is not verified; no attenuation was sent.");
        }
        var result = null;
        var requestError = null;
        try {
          body.principal = principal;
          result = await request("/principals/attenuate", {
            method: "POST", body: JSON.stringify(body)
          });
        } catch (error) {
          requestError = error;
        }
        var refreshed = await refresh();
        if (requestError) {
          showError(requestError, refreshed.planeOk
            ? "Request failed; enforced state was re-read"
            : "Request failed; enforced state could not be re-read", ticket);
          return null;
        }
        show((result.ok ? "Applied: " : "Not applied: ") + "attenuate " + principal +
          (refreshed.planeOk ? "" : "; enforced state could not be re-read"), result, ticket);
        return result;
      });
    }, true);
  });

  document.getElementById("acp-halt-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var formElement = event.currentTarget;
    var submitter = event.submitter;
    var form = new FormData(formElement);
    runGuarded(formElement, true, async function (ticket) {
      if (!canHalt) { throw fail("The current role cannot change this deployment's halt."); }
      var reason = String(form.get("reason") || "").trim();
      if (!reason) { throw fail("A reason is required to halt the deployment."); }
      if (reason.length > 200) { throw fail("Halt reason must be at most 200 characters."); }
      if (!(await confirmAction(
        "Halt this deployment at its configured " + haltScope +
          " scope? Running work stops at the next enforced tool boundary.",
        "Halt deployment"
      ))) { return null; }
      return withBusy(submitter, function () { return changeHalt(true, reason, ticket); });
    }, true);
  });

  document.getElementById("acp-clear-halt").addEventListener("click", function (event) {
    var button = event.currentTarget;
    runGuarded(button, true, async function (ticket) {
      if (!canHalt) { throw fail("The current role cannot change this deployment's halt."); }
      if (!(await confirmAction(
        "Clear the " + haltScope + " halt? Governed agents may resume work immediately.",
        "Clear halt"
      ))) { return null; }
      return withBusy(button, function () { return changeHalt(false, undefined, ticket); });
    }, true);
  });

  refresh().catch(function (error) {
    controlPlaneReady = false;
    haltAuthorityReady = false;
    updateRuntimeControls();
    setMark("failed", "Unavailable");
    document.getElementById("acp-health-title").textContent =
      "Initial control-plane state is unavailable";
    document.getElementById("acp-health-detail").textContent = boundedError(error);
  });
})();
