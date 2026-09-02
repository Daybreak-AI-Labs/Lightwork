(function () {
  "use strict";

  var root = document.getElementById("rob-root");
  if (!root) { return; }
  var apiBase = root.dataset.apiBase || "/api/v1/regulatory";
  var maxResponseBytes = 2 * 1024 * 1024;
  var actionPattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;
  var state = document.getElementById("rob-state");
  var output = document.getElementById("rob-output");
  var operationState = document.getElementById("rob-operation-state");
  var rows = document.getElementById("rob-rows");
  var library = document.getElementById("rob-library");
  var problemList = document.getElementById("rob-problem-list");
  var gatewayPreviewState = document.getElementById("rob-gateway-preview-state");
  var current = null;

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
      var detail = payload && typeof payload.detail === "string" ? payload.detail : "Regulatory compiler request failed.";
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

  function setMark(marker, text) {
    state.dataset.state = marker;
    state.textContent = text;
  }

  function cell(text) {
    var td = document.createElement("td");
    td.textContent = text;
    return td;
  }

  function scopeOf(ob) {
    var parts = [];
    if (ob.actions && ob.actions.length) { parts.push("tools: " + ob.actions.join(", ")); }
    if (ob.deny_tools && ob.deny_tools.length) { parts.push("removed: " + ob.deny_tools.join(", ")); }
    if (ob.min_risk) { parts.push("risk ≥ " + ob.min_risk); }
    return parts.join(" · ") || "—";
  }

  function renderObligations(items, note) {
    rows.textContent = "";
    document.getElementById("rob-obligations-note").textContent = note;
    if (!items.length) {
      var empty = document.createElement("tr");
      var td = cell("No regulation is active: the gate carries no compiled obligation. Enable profiles in [compliance] to change that.");
      td.colSpan = 6;
      empty.appendChild(td);
      rows.appendChild(empty);
      return;
    }
    items.forEach(function (ob) {
      var tr = document.createElement("tr");
      tr.appendChild(cell(ob.regulation));
      tr.appendChild(cell(ob.article));
      var title = cell(ob.title || ob.id);
      var id = document.createElement("div");
      id.className = "muted";
      id.textContent = ob.id;
      title.appendChild(id);
      tr.appendChild(title);
      var effectCell = document.createElement("td");
      var chip = document.createElement("span");
      chip.className = "rob-effect";
      chip.dataset.effect = ob.effect;
      chip.textContent = ob.effect.replace("_", " ");
      effectCell.appendChild(chip);
      tr.appendChild(effectCell);
      tr.appendChild(cell(scopeOf(ob)));
      var source = document.createElement("td");
      if (ob.source_url && /^https:\/\//.test(ob.source_url)) {
        var link = document.createElement("a");
        link.href = ob.source_url;
        link.rel = "noopener noreferrer";
        link.target = "_blank";
        link.textContent = "primary source";
        source.appendChild(link);
      } else {
        source.textContent = "—";
      }
      tr.appendChild(source);
      rows.appendChild(tr);
    });
  }

  function renderLibrary(items) {
    library.textContent = "";
    items.forEach(function (entry) {
      var li = document.createElement("li");
      var label = document.createElement("span");
      label.textContent = entry.regulation + " (" + entry.key + ") · " + entry.obligations + " obligation" + (entry.obligations === 1 ? "" : "s") + (entry.active ? " · active" : "");
      li.appendChild(label);
      var button = document.createElement("button");
      button.type = "button";
      button.className = "btn";
      button.textContent = "Inspect";
      button.addEventListener("click", function () { inspect(entry.key).catch(showError); });
      li.appendChild(button);
      library.appendChild(li);
    });
  }

  function renderProblems(verification) {
    problemList.textContent = "";
    problemList.hidden = !verification.problems.length;
    verification.problems.forEach(function (problem) {
      var li = document.createElement("li");
      li.textContent = problem;
      problemList.appendChild(li);
    });
  }

  async function loadStatus() {
    var st = await request("/status");
    current = st;
    document.getElementById("rob-active").textContent = String(st.active_profiles.length);
    document.getElementById("rob-count").textContent = String(st.obligations.length);
    document.getElementById("rob-problems").textContent = String(st.verification.problems.length);
    var title = document.getElementById("rob-health-title");
    var detail = document.getElementById("rob-health-detail");
    if (!st.active_profiles.length) {
      setMark("partial", "Nothing compiled");
      title.textContent = "No regulation is active";
      detail.textContent = "The action gate carries no compiled obligation. Enable profiles in [compliance] (installer: regulated-posture step) and restart.";
    } else if (st.verification.sound) {
      setMark("ready", "Live gates");
      title.textContent = st.active_profiles.join(", ") + " compiled and verified";
      detail.textContent = st.verification.inspected + " obligation(s) inspected; every one is a live, non-vacuous gate.";
    } else {
      setMark("failed", "Vacuous gate");
      title.textContent = "A compiled obligation does not bite";
      detail.textContent = "Verification found " + st.verification.problems.length + " problem(s). The CI gate would fail; see the list below.";
    }
    renderObligations(st.obligations, st.active_profiles.length ? "Under " + st.active_profiles.join(", ") : "Nothing is active");
    renderLibrary(st.library);
    renderProblems(st.verification);
    document.getElementById("rob-enable-snippet").textContent = "[compliance]\nprofiles = [" + st.library.map(function (e) { return JSON.stringify(e.key); }).join(", ") + "]";
    show("Compiled policy (" + (st.active_profiles.join(", ") || "none") + ")", { policy: st.policy, attenuation: st.attenuation, verification: st.verification });
    return st;
  }

  async function loadTools() {
    var payload = await request("/tools");
    var list = document.getElementById("rob-tools");
    list.textContent = "";
    payload.items.forEach(function (name) {
      var option = document.createElement("option");
      option.value = name;
      list.appendChild(option);
    });
  }

  async function inspect(key) {
    var payload = await request("/profiles/" + encodeURIComponent(key));
    renderObligations(payload.obligations, (payload.active ? "Active — " : "Preview — ") + key);
    renderProblems(payload.verification);
    show("Would compile to (" + key + ")", { policy: payload.policy, attenuation: payload.attenuation, verification: payload.verification });
  }

  async function check(action, scope) {
    var body = { action: action };
    if (scope === "all" && current) { body.profiles = current.library.map(function (e) { return e.key; }); }
    var result = await request("/check", { method: "POST", body: JSON.stringify(body) });
    var box = document.getElementById("rob-verdict-box");
    var verdict = document.getElementById("rob-verdict");
    var detail = document.getElementById("rob-verdict-detail");
    box.hidden = false;
    verdict.dataset.decision = result.decision;
    verdict.textContent = result.action + ": " + result.decision.replace("_", " ").toUpperCase();
    var parts = [];
    if (!result.classified) { parts.push("Not a classified tool: the risk floors cannot see it, so only an exact-name clause could apply."); }
    else { parts.push("Risk tier " + result.risk + "."); }
    if (result.obligation) { parts.push("Decided under " + result.obligation.regulation + " " + result.obligation.article + " (" + result.obligation.obligation_id + ")."); }
    if (result.attenuated) { parts.push("Removed from every capability by an attenuation obligation."); }
    if (!result.obligation && !result.attenuated) { parts.push("No active obligation covers this action; the verdict comes from the rest of the governance overlay."); }
    detail.textContent = parts.join(" ");
    show("Check: " + result.action + " under " + (result.profiles.join(", ") || "no regulation"), result);
  }

  function parseGatewayConstraintDocument(value) {
    var text = String(value || "").trim();
    if (!text) { throw fail("A gateway constraint document is required."); }
    if (text.length > 131072) { throw fail("Gateway constraint document exceeds the console bound."); }
    var document;
    try { document = JSON.parse(text); }
    catch (_error) { throw fail("Gateway constraint document must be valid JSON."); }
    if (!document || typeof document !== "object" || Array.isArray(document)) {
      throw fail("Gateway constraint document must be a JSON object.");
    }
    return document;
  }

  async function previewGatewayConstraints(value) {
    if (!current || !Array.isArray(current.active_profiles) || !current.active_profiles.length) {
      throw fail("Configure an active regulatory profile before previewing a gateway overlay.");
    }
    var result = await request("/gateway-constraints/preview", {
      method: "POST",
      body: JSON.stringify({ document: value, profiles: current.active_profiles })
    });
    if (!result || result.runtime_enforcement_changed !== false ||
        result.gateway_source_verified !== false || !Array.isArray(result.coverage)) {
      throw fail("The server returned an invalid gateway constraint preview.");
    }
    if (gatewayPreviewState) {
      gatewayPreviewState.textContent = result.complete
        ? "Complete restrictive mapping; runtime enforcement is unchanged."
        : "Incomplete mapping: reconcile every unmapped gateway selector before relying on it.";
    }
    show("Gateway constraint preview (runtime unchanged)", result);
    return result;
  }

  async function refresh() {
    await loadStatus();
    await loadTools();
  }

  document.getElementById("rob-refresh").addEventListener("click", function () { refresh().catch(showError); });

  document.getElementById("rob-check-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(event.target);
    (async function () {
      var action = String(form.get("action") || "").trim();
      if (!actionPattern.test(action)) { throw fail("Action must be letters, digits, or _.:- (max 64)."); }
      await check(action, String(form.get("scope") || "active"));
    })().catch(showError);
  });

  document.getElementById("rob-gateway-preview-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(event.target);
    (async function () {
      var document = parseGatewayConstraintDocument(form.get("document"));
      await previewGatewayConstraints(document);
    })().catch(function (error) {
      if (gatewayPreviewState) { gatewayPreviewState.textContent = "Gateway preview was refused or unavailable."; }
      showError(error);
    });
  });

  refresh().catch(function (error) {
    setMark("failed", "Unavailable");
    showError(error);
  });
})();
