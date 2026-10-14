(function () {
  "use strict";

  var root = document.getElementById("cu-root");
  if (!root) { return; }

  var apiBase = root.dataset.apiBase || "/api/v1/computer-use";
  var canOperate = root.dataset.canOperate === "true";
  var requestTimeoutMs = 15000;
  var maxResponseBytes = 2 * 1024 * 1024;
  var selectedSession = null;
  var eventCursor = 0;
  var eventRows = [];
  var pollTimer = null;
  var pollInFlight = false;
  var refreshGeneration = 0;
  var mutationInFlight = false;
  var sessionCreationReady = false;

  function byId(id) { return document.getElementById(id); }
  function text(value, fallback) {
    if (typeof value === "string" && value.trim()) { return value.trim(); }
    if (typeof value === "number" && Number.isFinite(value)) { return String(value); }
    return fallback || "—";
  }
  function object(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }
  function number(value, fallback) {
    return typeof value === "number" && Number.isFinite(value) ? value : fallback;
  }
  function describe(value, fallback) {
    if (typeof value === "string" && value.trim()) { return value.trim().slice(0, 1200); }
    if (typeof value === "number" && Number.isFinite(value)) { return String(value); }
    if (value && typeof value === "object") {
      try { return JSON.stringify(value).slice(0, 1200); }
      catch (_error) { return fallback || "Not reported"; }
    }
    return fallback || "Not reported";
  }
  function formatTime(value) {
    var epoch = number(value, NaN);
    if (!Number.isFinite(epoch) && typeof value === "string" && value) {
      var parsed = Date.parse(value);
      if (Number.isFinite(parsed)) {
        try { return new Date(parsed).toLocaleString(); }
        catch (_error) { return value; }
      }
      return value;
    }
    if (!Number.isFinite(epoch)) { return "Not reported"; }
    try { return new Date(epoch * 1000).toLocaleString(); }
    catch (_error) { return "Not reported"; }
  }
  function formatCountdown(value, fallbackSeconds) {
    var remaining = number(fallbackSeconds, NaN);
    if (!Number.isFinite(remaining)) {
      var epoch = number(value, NaN);
      var expiresMs = Number.isFinite(epoch) ? epoch * 1000 : Date.parse(value || "");
      if (Number.isFinite(expiresMs)) { remaining = Math.ceil((expiresMs - Date.now()) / 1000); }
    }
    if (!Number.isFinite(remaining)) { return "Not reported"; }
    if (remaining <= 0) { return "Expired"; }
    var minutes = Math.floor(remaining / 60);
    var seconds = Math.floor(remaining % 60);
    return minutes ? String(minutes) + "m " + String(seconds) + "s remaining"
      : String(seconds) + "s remaining";
  }
  function utf8ByteLength(value) {
    return new TextEncoder().encode(value).length;
  }
  function responseDetail(payload, fallback) {
    var detail = payload && payload.detail;
    if (typeof detail === "string" && detail) { return detail.slice(0, 1000); }
    if (detail && typeof detail.message === "string") { return detail.message.slice(0, 1000); }
    return fallback || "Computer-use request failed.";
  }

  async function boundedJson(response) {
    if (!response.body || !response.body.getReader) {
      var fallbackText = await response.text();
      if (utf8ByteLength(fallbackText) > maxResponseBytes) {
        throw new Error("Computer-use response exceeded the safety limit.");
      }
      return fallbackText ? JSON.parse(fallbackText) : {};
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var chunks = "";
    var bytes = 0;
    while (true) {
      var part = await reader.read();
      if (part.done) { break; }
      bytes += part.value.byteLength;
      if (bytes > maxResponseBytes) {
        await reader.cancel();
        throw new Error("Computer-use response exceeded the safety limit.");
      }
      chunks += decoder.decode(part.value, {stream: true});
    }
    chunks += decoder.decode();
    return chunks ? JSON.parse(chunks) : {};
  }

  async function request(path, options) {
    var controller = new AbortController();
    var timeout = window.setTimeout(function () { controller.abort(); }, requestTimeoutMs);
    var init = Object.assign({
      credentials: "same-origin",
      headers: {"Accept": "application/json"},
      signal: controller.signal
    }, options || {});
    if (init.body) {
      init.headers = Object.assign({}, init.headers, {"Content-Type": "application/json"});
    }
    try {
      var response = await fetch(apiBase + path, init);
      var payload = await boundedJson(response);
      if (!response.ok) { throw new Error(responseDetail(payload)); }
      return payload;
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new Error("Computer-use request timed out.");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function setMark(state, label) {
    var mark = byId("cu-readiness-mark");
    mark.dataset.state = state;
    mark.textContent = label;
  }

  function dependencyReady(readiness, names) {
    var dependencies = object(readiness.dependencies);
    return names.every(function (name) { return dependencies[name] !== false; });
  }

  function updateStage(name, state, title, detail) {
    var stage = root.querySelector('[data-stage="' + name + '"]');
    if (!stage) { return; }
    stage.dataset.state = state;
    var strong = stage.querySelector("strong");
    var small = stage.querySelector("small");
    if (strong) { strong.textContent = title; }
    if (small) { small.textContent = detail; }
  }

  function blockerMessage(entry) {
    if (typeof entry === "string") { return entry; }
    var row = object(entry);
    return text(row.message, text(row.code, "Runtime reported a blocker."));
  }

  function renderReadiness(payload) {
    var readiness = object(payload.readiness || payload);
    var blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
    var warnings = Array.isArray(readiness.warnings) ? readiness.warnings : [];
    var ready = readiness.ready === true;
    setMark(ready ? "ready" : (blockers.length ? "blocked" : "partial"),
      ready ? "Ready" : (blockers.length ? "Blocked" : "Incomplete"));
    byId("cu-readiness-title").textContent = ready
      ? "Runtime is ready for governed sessions"
      : "Runtime needs attention before autonomous control";
    byId("cu-readiness-detail").textContent = ready
      ? "The reported display, dependencies and policy gates passed this check. Review each session before relying on it."
      : (blockers.length ? blockerMessage(blockers[0]) : "Readiness is incomplete; review every stage below.");
    byId("cu-checked-at").textContent = "Checked " + formatTime(readiness.checked_at);

    var backend = text(readiness.backend, "invalid").toLowerCase();
    var depsReady = backend === "remote"
      ? readiness.backend_ready === true
      : dependencyReady(readiness, ["pyautogui", "mss"]);
    updateStage("runtime", depsReady ? "ready" : "blocked", "1 · Runtime",
      depsReady ? (backend === "remote" ? "The configured execution cell answered its readiness challenge."
        : "Host input and image capture dependencies are present.")
        : "The selected backend or one of its dependencies is unavailable.");
    var display = text(readiness.display, "not selected");
    var hostWarning = warnings.some(function (entry) {
      return blockerMessage(entry).toLowerCase().indexOf("operator host") !== -1;
    });
    var remoteBoundary = backend === "remote" && readiness.backend_ready === true;
    var intentionalHost = backend === "host" && readiness.host_allowed === true && hostWarning;
    updateStage("display", remoteBoundary || (intentionalHost && display !== "not selected") ? "ready" : "blocked",
      "2 · Display boundary", remoteBoundary
        ? "The configured remote execution-cell boundary is ready; its adapter selects the display."
        : (intentionalHost ? "Host operation was deliberately allowed: " + display + "."
          : "No ready execution cell or deliberate host authority was reported."));
    var controls = Array.isArray(payload.controls) ? payload.controls : [];
    var stopReady = readiness.stop_path_ready === true &&
      controls.indexOf("stop") !== -1 && controls.indexOf("takeover") !== -1;
    var approvalReady = readiness.approval_ready === true;
    updateStage("safety", stopReady && approvalReady ? "ready" : "blocked", "3 · Safety controls",
      stopReady && approvalReady
        ? "Pause, takeover and stop are runtime-governed; consequential actions are approval-bound."
        : (stopReady
          ? "Operator controls are available; runtime readiness does not yet attest the action-approval path."
          : "The runtime did not expose both stop and takeover controls."));
    var sealing = readiness.screenshot_custody_ready === true;
    var mediaReader = payload.media_reader_available === true;
    updateStage("evidence", sealing && mediaReader ? "ready" : "partial", "4 · Evidence custody",
      sealing && mediaReader
        ? "Cryptographically sealed observations can be custody-checked during replay."
        : (!sealing ? "Screenshot sealing is not confirmed; a retained frame may have content-hash integrity only."
          : "Sealing is reported, but the runtime does not expose retained bytes for replay."));

    sessionCreationReady = ready && readiness.backend_ready === true &&
      approvalReady && stopReady &&
      (readiness.visual_evidence_required !== true || sealing);

    var createButton = byId("cu-create");
    var backendSelect = byId("cu-create-backend");
    var monitorInput = byId("cu-create-monitor");
    if (backendSelect && (backend === "remote" || backend === "host")) {
      backendSelect.value = backend;
    }
    if (monitorInput && Array.isArray(readiness.monitors) && readiness.monitors.length) {
      var reportedMonitor = number(object(readiness.monitors[0]).id,
        number(object(readiness.monitors[0]).index, NaN));
      if (Number.isFinite(reportedMonitor) && reportedMonitor > 0) {
        monitorInput.value = String(reportedMonitor);
      }
    }
    if (createButton) { createButton.disabled = !canOperate || !sessionCreationReady; }
    var createStatus = byId("cu-create-status");
    if (createStatus) {
      createStatus.textContent = sessionCreationReady
        ? "Readiness passed. Creating a session records its owner, surface, monitor and starting revision."
        : "Session creation is locked until readiness passes.";
    }

    var alerts = byId("cu-readiness-alerts");
    alerts.replaceChildren();
    blockers.slice(0, 12).forEach(function (entry) {
      var alert = document.createElement("div");
      alert.className = "cu-alert cu-alert--blocker";
      var strong = document.createElement("strong");
      strong.textContent = "Blocker · ";
      alert.append(strong, document.createTextNode(blockerMessage(entry)));
      alerts.appendChild(alert);
    });
    warnings.slice(0, 12).forEach(function (entry) {
      var alert = document.createElement("div");
      alert.className = "cu-alert cu-alert--warning";
      var strong = document.createElement("strong");
      strong.textContent = "Warning · ";
      alert.append(strong, document.createTextNode(blockerMessage(entry)));
      alerts.appendChild(alert);
    });
    if (!blockers.length && !warnings.length) {
      var empty = document.createElement("p");
      empty.className = "cu-muted";
      empty.textContent = ready ? "No runtime blockers were reported." : "No detailed blockers were supplied.";
      alerts.appendChild(empty);
    }
  }

  function sessionState(row) { return text(row.state, "unknown").toLowerCase(); }
  function sessionId(row) { return text(row.session_id || row.id, ""); }
  function sessionTitle(row) {
    return text(row.goal_title, text(row.goal_id, "Session " + sessionId(row).slice(0, 8)));
  }

  function renderSessions(payload) {
    var rows = Array.isArray(payload.items) ? payload.items : [];
    var list = byId("cu-session-list");
    list.replaceChildren();
    byId("cu-session-count").textContent = String(rows.length) + (rows.length === 1 ? " session" : " sessions");
    if (!rows.length) {
      var empty = document.createElement("p");
      empty.className = "cu-empty";
      empty.textContent = "No computer-use sessions are visible in this tenant.";
      list.appendChild(empty);
      return;
    }
    rows.forEach(function (row) {
      var id = sessionId(row);
      if (!id) { return; }
      var button = document.createElement("button");
      button.type = "button";
      button.className = "cu-session";
      button.dataset.sessionId = id;
      button.setAttribute("aria-label", "Open computer-use session " + sessionTitle(row));
      button.setAttribute("aria-current", selectedSession && sessionId(selectedSession) === id ? "true" : "false");
      var top = document.createElement("span");
      top.className = "cu-session-meta";
      var name = document.createElement("span");
      name.className = "cu-session-name";
      name.textContent = sessionTitle(row);
      var chip = document.createElement("span");
      chip.className = "cu-chip";
      chip.dataset.state = sessionState(row);
      chip.textContent = sessionState(row);
      top.append(name, chip);
      var meta = document.createElement("span");
      meta.className = "cu-muted";
      meta.textContent = text(row.principal, "Unknown principal") + " · heartbeat " + formatTime(row.heartbeat_at);
      button.append(top, meta);
      button.addEventListener("click", function () { selectSession(id, true); });
      list.appendChild(button);
    });
  }

  function updateControls(row) {
    var state = sessionState(row);
    var revision = number(row.revision, 0);
    var map = {
      "cu-pause": state === "running" || state === "ready",
      "cu-resume": state === "paused",
      "cu-takeover": state === "running" || state === "ready" || state === "paused",
      "cu-release": state === "takeover",
      "cu-recover": state === "orphaned" || state === "error",
      "cu-stop": state !== "stopped" && state !== "stopping" && state !== "unknown"
    };
    Object.keys(map).forEach(function (id) {
      var button = byId(id);
      if (!button || button.dataset.roleDisabled === "true") { return; }
      button.disabled = !canOperate || !map[id] || mutationInFlight || revision < 1;
    });
  }

  function renderSession(row) {
    selectedSession = row;
    var state = sessionState(row);
    byId("cu-session-title").textContent = sessionTitle(row);
    byId("cu-session-state").dataset.state = state;
    byId("cu-session-state").textContent = state;
    byId("cu-principal").textContent = text(row.principal);
    byId("cu-goal").textContent = text(row.goal_id, "Not linked");
    byId("cu-revision").textContent = text(row.revision);
    byId("cu-heartbeat").textContent = formatTime(row.heartbeat_at);
    byId("cu-monitor").textContent = text(row.surface, "desktop") + " · " +
      text(row.monitor_id, "runtime default");
    var controlState = {
      takeover: "Human has control",
      paused: "Agent is paused",
      orphaned: "Heartbeat expired",
      recovering: "Recovery in progress",
      stopping: "Stop pending",
      stopped: "No control active",
      error: "Failed closed"
    };
    byId("cu-takeover-state").textContent = controlState[state] || "Agent governed";
    byId("cu-control-status").textContent = state === "stopping"
      ? "Stop is pending runtime acknowledgement."
      : (state === "stopped" ? "Session is stopped; no further action controls are available."
        : "Every control uses the displayed session revision and refuses stale state.");
    updateControls(row);
    root.querySelectorAll(".cu-session").forEach(function (button) {
      button.setAttribute("aria-current", button.dataset.sessionId === sessionId(row) ? "true" : "false");
    });
  }

  function eventType(event) {
    return text(event.event_type || event.type || event.kind, "runtime event");
  }
  function eventPayload(event) {
    return object(event.payload || event.data || event);
  }
  function eventSummary(event) {
    var payload = eventPayload(event);
    var action = object(payload.action);
    var outcome = object(payload.outcome);
    return text(payload.summary,
      text(payload.message,
        text(action.summary || action.action || payload.action_name,
          text(outcome.state || payload.outcome_state || payload.state, "Runtime state recorded."))));
  }
  function observationId(event) {
    var payload = eventPayload(event);
    var observation = object(payload.observation);
    return text(observation.observation_id || observation.id || payload.observation_id, "");
  }
  function mediaStatus(event) {
    var payload = eventPayload(event);
    var observation = object(payload.observation);
    return object(observation.media || payload.media);
  }

  function showFrame(event, contextEvent) {
    if (!selectedSession) { return; }
    var obsId = observationId(event);
    var media = mediaStatus(event);
    var image = byId("cu-frame-image");
    var note = byId("cu-frame-note");
    var marker = byId("cu-target-marker");
    marker.hidden = true;
    image.hidden = true;
    image.removeAttribute("src");
    image.alt = "";
    if (!obsId || media.available !== true) {
      note.hidden = false;
      note.textContent = text(media.missing_reason,
        "This observation has metadata but no retained frame available for replay.");
      byId("cu-frame-caption").textContent = "Frame unavailable: " + note.textContent;
      return;
    }
    note.hidden = false;
    note.textContent = "Loading retained frame and checking content integrity…";
    image.onload = function () {
      note.hidden = true;
      image.hidden = false;
      var custody = media.custody_sealed === true;
      var evidenceLabel = custody
        ? "Cryptographically sealed computer-use observation"
        : "Computer-use observation with a checked content hash; no custody seal";
      image.alt = evidenceLabel + " from " + formatTime(event.created_at || event.recorded_at || event.at);
      byId("cu-frame-caption").textContent = image.alt + ". " +
        text(media.custody_reason, "Custody status was not reported.");
      var payload = eventPayload(contextEvent || event);
      var target = object(payload.target || object(payload.action).target);
      var x = number(target.normalized_x, NaN);
      var y = number(target.normalized_y, NaN);
      if (Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 1 && y >= 0 && y <= 1) {
        marker.style.left = String(x * 100) + "%";
        marker.style.top = String(y * 100) + "%";
        marker.hidden = false;
      }
    };
    image.onerror = function () {
      image.hidden = true;
      note.hidden = false;
      note.textContent = "The retained frame could not be authorized or did not pass its required integrity checks.";
      byId("cu-frame-caption").textContent = "Frame unavailable: authorization or integrity check failed.";
    };
    image.src = apiBase + "/sessions/" + encodeURIComponent(sessionId(selectedSession)) +
      "/observations/" + encodeURIComponent(obsId) + "/media";
  }

  function renderTimeline() {
    var timeline = byId("cu-timeline");
    timeline.replaceChildren();
    byId("cu-event-count").textContent = String(eventRows.length) +
      (eventRows.length === 1 ? " event" : " events");
    if (!eventRows.length) {
      var empty = document.createElement("p");
      empty.className = "cu-empty";
      empty.textContent = "No runtime events have been retained for this session.";
      timeline.appendChild(empty);
      return;
    }
    eventRows.slice(-200).reverse().forEach(function (event) {
      var article = document.createElement("article");
      article.className = "cu-event";
      var heading = document.createElement("h3");
      heading.textContent = eventType(event).replace(/[_-]+/g, " ");
      var summary = document.createElement("p");
      summary.textContent = eventSummary(event);
      var when = document.createElement("time");
      when.textContent = formatTime(event.created_at || event.recorded_at || event.at);
      article.append(heading, summary, when);
      var obsId = observationId(event);
      if (obsId) {
        var actions = document.createElement("div");
        actions.className = "cu-event-actions";
        var frame = document.createElement("button");
        frame.className = "btn";
        frame.type = "button";
        var media = mediaStatus(event);
        frame.textContent = media.available === true
          ? (media.custody_sealed === true ? "Check sealed frame" : "Check frame content hash")
          : "Explain missing frame";
        frame.setAttribute("aria-label", frame.textContent + " for observation " + obsId);
        frame.addEventListener("click", function () { showFrame(event); });
        actions.appendChild(frame);
        article.appendChild(actions);
      }
      timeline.appendChild(article);
    });
  }

  function latestApproval() {
    for (var index = eventRows.length - 1; index >= 0; index -= 1) {
      var event = eventRows[index];
      var payload = eventPayload(event);
      var approval = object(payload.approval);
      var type = eventType(event).toLowerCase();
      if (Object.keys(approval).length || type.indexOf("approval") !== -1 ||
          type === "action.requested") {
        return {event: event, approval: Object.keys(approval).length ? approval : payload};
      }
    }
    return null;
  }

  function renderApproval() {
    var host = byId("cu-approval");
    host.replaceChildren();
    var selected = latestApproval();
    if (!selected) {
      var empty = document.createElement("p");
      empty.className = "cu-empty";
      empty.textContent = selectedSession
        ? "No approval event is present in this bounded session history."
        : "Select a session to inspect its pending approval context.";
      host.appendChild(empty);
      return;
    }
    var approval = selected.approval;
    var card = document.createElement("div");
    card.className = "cu-approval";
    var status = document.createElement("strong");
    status.textContent = "Approval " + text(approval.status, "recorded");
    var dl = document.createElement("dl");
    [
      ["Session", selectedSession ? sessionId(selectedSession) : "Not reported"],
      ["Request", text(approval.approval_id || approval.id || approval.request_id || approval.action_id, "Not reported")],
      ["Requester", text(approval.requester || approval.principal, "Not reported")],
      ["Risk", text(approval.risk || approval.risk_tier, "Not reported")],
      ["Claim", describe(approval.claim || approval.claimed_by, "Unclaimed")],
      ["Expires", formatTime(approval.expires_at)],
      ["Countdown", formatCountdown(approval.expires_at, approval.countdown_seconds)],
      ["Frame", text(approval.observation_id || observationId(selected.event), "Not bound")]
    ].forEach(function (pair) {
      var dt = document.createElement("dt");
      dt.textContent = pair[0];
      var dd = document.createElement("dd");
      dd.textContent = pair[1];
      dl.append(dt, dd);
    });
    var target = document.createElement("p");
    target.className = "cu-target";
    var exactTarget = approval.target_summary || approval.target || object(approval.action).target;
    var intendedEffect = approval.effect || approval.effect_commitment ||
      object(approval.action).effect || approval.summary;
    target.textContent = "Target: " + describe(exactTarget, "Not included") +
      "\nEffect: " + describe(intendedEffect, "Not included — do not approve from this projection alone.");
    card.append(status, dl, target);
    var boundObservationId = text(
      approval.observation_id || observationId(selected.event), ""
    );
    if (boundObservationId) {
      var boundFrameEvent = null;
      for (var index = eventRows.length - 1; index >= 0; index -= 1) {
        if (observationId(eventRows[index]) === boundObservationId) {
          boundFrameEvent = eventRows[index];
          if (mediaStatus(boundFrameEvent).available === true) { break; }
        }
      }
      var frameButton = document.createElement("button");
      frameButton.className = "btn";
      frameButton.type = "button";
      frameButton.textContent = boundFrameEvent && mediaStatus(boundFrameEvent).available === true
        ? "Review bound frame" : "Explain unavailable bound frame";
      frameButton.setAttribute(
        "aria-label", frameButton.textContent + " " + boundObservationId
      );
      frameButton.addEventListener("click", function () {
        showFrame(boundFrameEvent || selected.event, selected.event);
        byId("cu-frame").scrollIntoView({block: "center"});
      });
      card.appendChild(frameButton);
    }
    host.appendChild(card);
  }

  async function loadReadiness() {
    try { renderReadiness(await request("/readiness")); }
    catch (error) {
      setMark("blocked", "Unavailable");
      byId("cu-readiness-title").textContent = "Runtime readiness is unavailable";
      byId("cu-readiness-detail").textContent = responseDetail({detail: error.message});
      updateStage("runtime", "blocked", "1 · Runtime", "The runtime did not return a trustworthy readiness result.");
    }
  }

  async function loadSessions() {
    try {
      var payload = await request("/sessions?limit=100");
      renderSessions(payload);
      if (selectedSession) {
        var stillVisible = (payload.items || []).some(function (row) {
          return sessionId(row) === sessionId(selectedSession);
        });
        if (!stillVisible) { clearSelection("The selected session is no longer visible."); }
      }
    } catch (error) {
      var list = byId("cu-session-list");
      list.textContent = "";
      var empty = document.createElement("p");
      empty.className = "cu-empty";
      empty.textContent = responseDetail({detail: error.message});
      list.appendChild(empty);
      byId("cu-session-count").textContent = "Unavailable";
    }
  }

  function clearSelection(message) {
    selectedSession = null;
    eventRows = [];
    eventCursor = 0;
    byId("cu-session-title").textContent = "No session selected";
    byId("cu-session-state").dataset.state = "stopped";
    byId("cu-session-state").textContent = "None";
    byId("cu-live-announcement").textContent = message || "Session selection cleared.";
    renderTimeline();
    renderApproval();
    updateControls({state: "stopped", revision: 0});
  }

  async function loadEvents(id, reset) {
    var cursor = reset ? 0 : eventCursor;
    var payload = await request("/sessions/" + encodeURIComponent(id) +
      "/events?after_revision=" + encodeURIComponent(String(cursor)) + "&limit=200");
    var incoming = Array.isArray(payload.events) ? payload.events : [];
    if (reset) { eventRows = []; }
    var known = new Set(eventRows.map(function (event) { return number(event.revision, -1); }));
    incoming.forEach(function (event) {
      var revision = number(event.revision, -1);
      if (revision < 0 || !known.has(revision)) {
        eventRows.push(event);
        known.add(revision);
      }
    });
    eventRows.sort(function (left, right) {
      return number(left.revision, 0) - number(right.revision, 0);
    });
    if (eventRows.length > 200) { eventRows = eventRows.slice(-200); }
    eventCursor = number(payload.next_cursor, eventRows.length
      ? number(eventRows[eventRows.length - 1].revision, cursor) : cursor);
    renderTimeline();
    renderApproval();
    if (incoming.length && !reset) {
      byId("cu-live-announcement").textContent = String(incoming.length) +
        " new computer-use event" + (incoming.length === 1 ? "" : "s") + ".";
    }
  }

  async function selectSession(id, focusHeading) {
    var generation = ++refreshGeneration;
    stopPolling();
    eventCursor = 0;
    eventRows = [];
    try {
      var row = await request("/sessions/" + encodeURIComponent(id));
      if (generation !== refreshGeneration) { return; }
      renderSession(row.session || row);
      await loadEvents(id, true);
      if (focusHeading) { byId("cu-session-title").focus(); }
      startPolling();
    } catch (error) {
      clearSelection(responseDetail({detail: error.message}));
      byId("cu-control-status").textContent = responseDetail({detail: error.message});
    }
  }

  async function pollSelected() {
    if (!selectedSession || document.hidden || mutationInFlight || pollInFlight) { return; }
    var id = sessionId(selectedSession);
    pollInFlight = true;
    try {
      var row = await request("/sessions/" + encodeURIComponent(id));
      if (!selectedSession || sessionId(selectedSession) !== id) { return; }
      renderSession(row.session || row);
      await loadEvents(id, false);
    } catch (error) {
      byId("cu-control-status").textContent = "Live refresh paused: " + responseDetail({detail: error.message});
    } finally {
      pollInFlight = false;
    }
  }
  function startPolling() {
    stopPolling();
    pollTimer = window.setInterval(pollSelected, 1500);
  }
  function stopPolling() {
    if (pollTimer !== null) { window.clearInterval(pollTimer); pollTimer = null; }
  }

  async function createSession(event) {
    event.preventDefault();
    var button = byId("cu-create");
    var status = byId("cu-create-status");
    if (!button || !status || !canOperate || !sessionCreationReady || mutationInFlight) {
      return;
    }
    var monitor = Number(byId("cu-create-monitor").value);
    var goalText = byId("cu-create-goal").value.trim();
    var goal = goalText ? Number(goalText) : null;
    if (!Number.isInteger(monitor) || monitor < 1 || monitor > 64 ||
        (goal !== null && (!Number.isSafeInteger(goal) || goal < 1))) {
      status.textContent = "Choose a valid monitor and optional positive goal ID.";
      return;
    }
    mutationInFlight = true;
    button.disabled = true;
    status.textContent = "Creating the governed session record…";
    try {
      var result = await request("/sessions", {
        method: "POST",
        body: JSON.stringify({
          surface: byId("cu-create-surface").value,
          backend: byId("cu-create-backend").value,
          monitor_id: monitor,
          goal_id: goal
        })
      });
      var row = object(result.session || result);
      var id = sessionId(row);
      status.textContent = "Session created at revision " + text(row.revision) +
        ". Waiting for its runtime adapter and heartbeat.";
      byId("cu-live-announcement").textContent = status.textContent;
      await loadSessions();
      if (id) { await selectSession(id, true); }
    } catch (error) {
      status.textContent = responseDetail({detail: error.message});
    } finally {
      mutationInFlight = false;
      button.disabled = !sessionCreationReady;
    }
  }

  async function control(action) {
    if (!canOperate || !selectedSession || mutationInFlight) { return; }
    var row = selectedSession;
    var id = sessionId(row);
    var stateRevision = number(row.revision, 0);
    if (!id || stateRevision < 1) { return; }
    var prompt = {
      pause: "Pause this session before its next action?",
      resume: "Return this paused session to agent control?",
      takeover: "Claim human control of this session? Agent actions remain paused until control is returned.",
      release_takeover: "Return this session from human control to the governed runtime?",
      recover: "Start governed recovery for this orphaned or failed session? Recovery returns paused for inspection; it does not silently resume autonomy.",
      stop: "Stop this session now? This cannot resume the same runtime session."
    }[action];
    var accepted = window.mvConfirm ? await window.mvConfirm(prompt) : false;
    if (!accepted) { return; }
    mutationInFlight = true;
    updateControls(row);
    byId("cu-control-status").textContent = "Requesting " + action.replace(/_/g, " ") + "…";
    try {
      var result = await request("/sessions/" + encodeURIComponent(id) + "/" + action, {
        method: "POST",
        body: JSON.stringify({expected_revision: stateRevision})
      });
      renderSession(result.session || result);
      await loadEvents(id, false);
      byId("cu-control-status").textContent = "Runtime recorded " + action.replace(/_/g, " ") +
        " at revision " + text((result.session || result).revision) + ".";
      byId("cu-live-announcement").textContent = byId("cu-control-status").textContent;
      await loadSessions();
    } catch (error) {
      byId("cu-control-status").textContent = responseDetail({detail: error.message});
      await selectSession(id, false);
    } finally {
      mutationInFlight = false;
      if (selectedSession) { updateControls(selectedSession); }
    }
  }

  byId("cu-refresh").addEventListener("click", async function () {
    byId("cu-refresh").disabled = true;
    await Promise.all([loadReadiness(), loadSessions()]);
    if (selectedSession) { await pollSelected(); }
    byId("cu-refresh").disabled = false;
    byId("cu-live-announcement").textContent = "Computer-use status refreshed.";
  });
  var createForm = byId("cu-create-form");
  if (createForm) { createForm.addEventListener("submit", createSession); }
  [
    ["cu-pause", "pause"], ["cu-resume", "resume"],
    ["cu-takeover", "takeover"], ["cu-release", "release_takeover"],
    ["cu-recover", "recover"],
    ["cu-stop", "stop"]
  ].forEach(function (pair) {
    byId(pair[0]).addEventListener("click", function () { control(pair[1]); });
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && selectedSession) { pollSelected(); }
  });
  window.addEventListener("pagehide", stopPolling);

  updateControls({state: "stopped", revision: 0});
  Promise.all([loadReadiness(), loadSessions()]).catch(function () {});
}());
