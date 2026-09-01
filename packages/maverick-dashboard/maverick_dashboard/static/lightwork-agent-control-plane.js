(function () {
  "use strict";

  var root = document.getElementById("acp-root");
  if (!root) { return; }
  var apiBase = root.dataset.apiBase || "/api/v1/agent-control-plane";
  var canOperate = root.dataset.canOperate === "true";
  var canAudit = root.dataset.canAudit === "true";
  var canAdmin = root.dataset.canAdmin === "true";
  var maxResponseBytes = 2 * 1024 * 1024;
  var principalPattern = /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$/;
  var state = document.getElementById("acp-state");
  var output = document.getElementById("acp-output");
  var operationState = document.getElementById("acp-operation-state");
  var rows = document.getElementById("acp-rows");

  function fail(message) {
    var error = new Error(message);
    error.code = "invalid_input";
    return error;
  }

  async function readJson(response) {
    var declared = Number(response.headers.get("content-length") || 0);
    if (declared > maxResponseBytes) {
      if (response.body && typeof response.body.cancel === "function") { await response.body.cancel(); }
      throw fail("Response exceeds the console byte bound.");
    }
    var text = await response.text();
    if (text.length > maxResponseBytes) { throw fail("Response exceeds the console byte bound."); }
    var payload;
    try { payload = text ? JSON.parse(text) : {}; }
    catch (_error) { throw fail("The server returned invalid JSON."); }
    if (!response.ok) {
      var detail = payload && typeof payload.detail === "string" ? payload.detail : "Control plane request failed.";
      if (detail === "control_plane_disabled") { detail = "The control plane is off: set [control_plane] enable = true."; }
      throw fail(detail);
    }
    return payload;
  }

  async function request(path, options) {
    var settings = Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, options || {});
    if (settings.body) { settings.headers["Content-Type"] = "application/json"; }
    return readJson(await fetch(apiBase + path, settings));
  }

  function show(label, payload) {
    operationState.textContent = label;
    output.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  }

  function showError(error) {
    show("Failed", error && error.message ? error.message : String(error));
  }

  function checkPrincipal(value) {
    var principal = String(value || "").trim();
    if (!principalPattern.test(principal)) { throw fail("Principal must be letters, digits, or _.:@- (max 128)."); }
    return principal;
  }

  function setMark(marker, text) {
    state.dataset.state = marker;
    state.textContent = text;
  }

  async function loadStatus() {
    try {
      var st = await request("/status");
      document.getElementById("acp-native").textContent = String(st.native.principals);
      document.getElementById("acp-external").textContent = String(st.external.agents);
      document.getElementById("acp-suspended").textContent =
        String(st.native.suspended + st.external.suspended) + " / " + String(st.external.contained);
      var title = document.getElementById("acp-health-title");
      var detail = document.getElementById("acp-health-detail");
      if (st.halted) {
        setMark("failed", "Fleet halted");
        title.textContent = "The fleet is halted";
        detail.textContent = "Every kernel stops at its next tool boundary until the halt is cleared.";
      } else if (!st.enabled) {
        setMark("partial", "Read-only");
        title.textContent = "Control plane is off";
        detail.textContent = "Roster and ledger are live; the verbs are refused until [control_plane] enable = true.";
      } else {
        setMark("ready", "Enabled");
        title.textContent = "Control plane is live";
        detail.textContent = (st.trust_enforced ? "Trust plane enforced. " : "Trust plane open. ") +
          (st.external_agents_enabled ? "External-agent gateway on." : "External-agent gateway off.");
      }
      return st;
    } catch (error) {
      setMark("failed", "Unavailable");
      showError(error);
      return null;
    }
  }

  function cell(text) {
    var td = document.createElement("td");
    td.textContent = text;
    return td;
  }

  function verbButton(label, handler) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn";
    button.textContent = label;
    button.addEventListener("click", function () { handler().catch(showError); });
    return button;
  }

  function ceilingOf(row) {
    if (row.kind !== "external") { return "—"; }
    var parts = [];
    parts.push("risk≤" + (row.max_risk || "any"));
    parts.push(row.max_dollars === null || row.max_dollars === undefined ? "$∞" : "$" + row.max_dollars);
    if (row.deny_tools && row.deny_tools.length) { parts.push("deny " + row.deny_tools.join(",")); }
    return parts.join(" · ");
  }

  async function act(verb, principal, reason) {
    var options = { method: "POST" };
    if (reason !== undefined) { options.body = JSON.stringify({ reason: reason }); }
    var result = await request("/agents/" + encodeURIComponent(principal) + "/" + verb, options);
    show((result.ok ? "Applied: " : "Not applied: ") + verb + " " + principal, result);
    await refresh();
    return result;
  }

  function askReason(verb, principal) {
    var reason = window.prompt("Reason to " + verb + " " + principal + " (recorded on the audit chain):", "");
    if (reason === null) { throw fail("Cancelled."); }
    if (!reason.trim()) { throw fail("A reason is required."); }
    return reason.trim();
  }

  function renderRow(row) {
    var tr = document.createElement("tr");
    tr.appendChild(cell(row.principal));
    tr.appendChild(cell(row.kind + (row.platform ? " · " + row.platform : "")));
    var stateCell = document.createElement("td");
    var mark = document.createElement("span");
    mark.className = "acp-state";
    mark.dataset.state = row.state;
    mark.textContent = row.state + (row.revoked_reason ? " (" + row.revoked_reason + ")" : "");
    stateCell.appendChild(mark);
    tr.appendChild(stateCell);
    tr.appendChild(cell(String(row.running_goals)));
    tr.appendChild(cell(ceilingOf(row)));
    var actions = document.createElement("td");
    var verbs = document.createElement("div");
    verbs.className = "acp-verbs";
    if (canOperate) {
      if (row.revoked) {
        verbs.appendChild(verbButton("Resume", function () { return act("resume", row.principal); }));
      } else {
        verbs.appendChild(verbButton("Suspend", function () {
          return act("suspend", row.principal, askReason("suspend", row.principal));
        }));
      }
    }
    if (canAdmin) {
      if (row.contained || row.revoked) {
        verbs.appendChild(verbButton("Release", function () { return act("release", row.principal); }));
      } else {
        verbs.appendChild(verbButton("Contain", function () {
          return act("contain", row.principal, askReason("contain", row.principal));
        }));
      }
    }
    if (canAudit) {
      verbs.appendChild(verbButton("Ledger", function () { return ledger(row.principal); }));
    }
    actions.appendChild(verbs);
    tr.appendChild(actions);
    return tr;
  }

  async function loadRoster() {
    try {
      var payload = await request("/agents");
      rows.textContent = "";
      if (!payload.items.length) {
        var empty = document.createElement("tr");
        empty.appendChild(cell("Nothing is running and no external agent is registered."));
        empty.firstChild.colSpan = 6;
        rows.appendChild(empty);
        return;
      }
      payload.items.forEach(function (row) { rows.appendChild(renderRow(row)); });
    } catch (error) {
      showError(error);
    }
  }

  async function ledger(principal) {
    var payload = await request("/agents/" + encodeURIComponent(principal) + "/ledger?limit=100");
    show("Ledger: " + principal + " (" + payload.count + " event(s))", payload.items);
  }

  async function refresh() {
    await loadStatus();
    await loadRoster();
  }

  document.getElementById("acp-refresh").addEventListener("click", function () { refresh().catch(showError); });
  document.getElementById("acp-load-roster").addEventListener("click", function () { loadRoster().catch(showError); });

  document.getElementById("acp-verb-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(event.target);
    (async function () {
      var principal = checkPrincipal(form.get("principal"));
      var verb = String(form.get("verb") || "");
      var reason = String(form.get("reason") || "").trim();
      if (verb === "ledger") { return ledger(principal); }
      if (verb === "suspend" || verb === "contain") {
        if (!reason) { throw fail("A reason is required to " + verb + "."); }
        return act(verb, principal, reason);
      }
      return act(verb, principal);
    })().catch(showError);
  });

  document.getElementById("acp-attenuate-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(event.target);
    (async function () {
      var principal = checkPrincipal(form.get("principal"));
      var body = { deny_tools: [] };
      String(form.get("deny_tools") || "").split(",").forEach(function (tool) {
        var name = tool.trim();
        if (name) { body.deny_tools.push(name); }
      });
      var risk = String(form.get("max_risk") || "");
      if (risk) { body.max_risk = risk; }
      var dollars = String(form.get("max_dollars") || "").trim();
      if (dollars) { body.max_dollars = Number(dollars); }
      var wall = String(form.get("max_wall_seconds") || "").trim();
      if (wall) { body.max_wall_seconds = Number(wall); }
      var result = await request("/agents/" + encodeURIComponent(principal) + "/attenuate",
        { method: "POST", body: JSON.stringify(body) });
      show((result.ok ? "Applied: " : "Not applied: ") + "attenuate " + principal, result);
      await refresh();
    })().catch(showError);
  });

  document.getElementById("acp-halt-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(event.target);
    (async function () {
      var reason = String(form.get("reason") || "").trim();
      if (!reason) { throw fail("A reason is required to halt the fleet."); }
      if (!window.confirm("Halt every running agent at its next tool boundary?")) { throw fail("Cancelled."); }
      var result = await request("/halt", { method: "POST", body: JSON.stringify({ reason: reason }) });
      show(result.ok ? "Fleet halted" : "Halt did not take effect", result);
      await refresh();
    })().catch(showError);
  });

  document.getElementById("acp-clear-halt").addEventListener("click", function () {
    (async function () {
      var result = await request("/clear-halt", { method: "POST" });
      show(result.ok ? "Fleet released" : "A halt is still active", result);
      await refresh();
    })().catch(showError);
  });

  refresh().catch(showError);
})();
