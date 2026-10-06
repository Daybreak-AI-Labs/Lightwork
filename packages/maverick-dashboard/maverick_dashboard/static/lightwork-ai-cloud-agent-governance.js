/* Cloud AI agents page: list agents, checks and changes on load; every
   action is a row button; references, versions and fingerprints are carried
   from the chosen row or computed from a chosen file, never typed. */
(function () {
  "use strict";
  var root = document.getElementById("cag-root");
  if (!root) { return; }
  var API = root.dataset.api || "/api/v1/assurance/cloud-agents";
  var can = {
    operate: root.dataset.canOperate === "true",
    admin: root.dataset.canAdmin === "true",
    audit: root.dataset.canAudit === "true"
  };
  var MAX_BYTES = 2 * 1024 * 1024;
  var PROVIDERS = {
    aws_bedrock: "AWS Bedrock",
    azure_ai_foundry: "Azure AI Foundry",
    google_vertex_ai: "Google Vertex AI"
  };
  var ACTIONS = {
    disable_agent: "Turn the agent off",
    quarantine_agent: "Quarantine the agent",
    restrict_tool: "Stop it using a tool",
    restrict_model: "Stop it using a model",
    detach_data_source: "Disconnect a data source"
  };
  var DECISIONS = {
    allow_intent: ["Allowed", "ok"],
    deny: ["Blocked by policy", "failed"],
    observe: ["Watch only — no change allowed", "warn"]
  };
  var STATUSES = {
    prepared: ["Ready to send", "idle"],
    dispatching: ["Sending — needs confirmation", "warn"],
    adapter_accepted: ["Provider accepted it", "ok"],
    adapter_rejected: ["Provider refused it", "failed"],
    adapter_reported_no_change: ["Provider reported no change", "warn"],
    reconciliation_required: ["Needs confirmation", "warn"],
    reconciled_applied: ["Confirmed done", "ok"],
    reconciled_no_change: ["Confirmed not done", "idle"],
    retryable: ["Can be sent again", "idle"]
  };
  var LIFECYCLE = {
    active: ["Running", "ok"], disabled: ["Turned off", "idle"],
    draft: ["Draft", "idle"], unknown: ["Unknown", "warn"]
  };

  var state = { agents: [], evaluations: [], intents: [], policies: [], framework: null,
    frameworkEnabled: false, checkAgent: null, confirmIntent: null, attestReview: null };
  var result = document.getElementById("cag-result");
  var output = document.getElementById("cag-output");

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  function human(value) {
    if (typeof window.mvHuman === "function") { return window.mvHuman(value); }
    var text = String(value || "").replace(/[_.-]+/g, " ").trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }
  function when(ts) {
    if (typeof window.mvWhen === "function") { return window.mvWhen(ts); }
    return ts ? (window.mvTimeAbsolute ? window.mvTimeAbsolute(ts) : new Date(ts * 1000).toLocaleString()) : "";
  }
  function badge(pair) {
    var span = el("span", "badge " + pair[1], pair[0]);
    return span;
  }
  function plainError(status, action, headers) {
    if (typeof window.mvErrorText === "function") {
      // Headers carry the turned-off reason, so "off" is never read as "no permission".
      return window.mvErrorText({ status: status, headers: headers }, action);
    }
    if (status === 403) { return "Could not " + action + ". You do not have permission to do this."; }
    if (status === 409) { return "Could not " + action + ". This changed since you opened it. Refresh the page and try again."; }
    return "Could not " + action + ". Something went wrong. Try again.";
  }
  var CONFLICTS = {
    cloud_agent_dispatch_requires_reconciliation:
      "The provider's answer was unclear. Use Confirm what happened once you have checked the provider.",
    cloud_agent_governance_disabled: "Cloud AI agents are turned off for this workspace.",
    cloud_agent_governance_register_unavailable: "This list isn't available on this installation yet."
  };

  async function run(method, path, payload, action) {
    var options = { method: method, headers: { Accept: "application/json" }, credentials: "same-origin" };
    if (method !== "GET") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    var response;
    try { response = await fetch(API + path, options); }
    catch (error) {
      throw new Error(typeof window.mvErrorText === "function"
        ? window.mvErrorText(error, action) : "Could not " + action + ". Lightwork could not be reached.");
    }
    var text = await response.text();
    if (text.length > MAX_BYTES) { throw new Error("Could not " + action + ". The answer was too large."); }
    var body;
    try {
      if (!text) { throw new Error("empty"); }
      body = JSON.parse(text);
    } catch (_error) {
      throw new Error("Could not " + action + ". Lightwork returned something unexpected. Refresh and try again.");
    }
    if (!response.ok) {
      var detail = body && typeof body.detail === "string" ? body.detail : "";
      var error = new Error(CONFLICTS[detail] || plainError(response.status, action, response.headers));
      error.status = response.status;
      error.technical = detail;
      throw error;
    }
    return body;
  }

  function say(message, technical) {
    // Like its error sentences, the page also runs without the shell's helpers.
    if (typeof window.mvStatus === "function") window.mvStatus(result, message);
    else result.textContent = message;
    output.textContent = technical === undefined ? "" :
      (typeof technical === "string" ? technical : JSON.stringify(technical, null, 2));
  }
  function failed(error) {
    say(error && error.message ? error.message : "Something went wrong. Try again.",
      error && error.technical ? error.technical : undefined);
  }

  function agentLabel(agentRef) {
    for (var i = 0; i < state.agents.length; i += 1) {
      if (state.agents[i].agent_ref === agentRef) { return state.agents[i].label; }
    }
    return "An agent no longer in the list";
  }
  function targetLabel(agent, targetRef) {
    if (!agent || targetRef === agent.agent_ref) { return "The whole agent"; }
    var groups = [["tool_refs", "Tool"], ["model_refs", "Model"], ["data_source_refs", "Data source"]];
    for (var g = 0; g < groups.length; g += 1) {
      var refs = agent[groups[g][0]] || [];
      var index = refs.indexOf(targetRef);
      if (index >= 0) { return groups[g][1] + " " + (index + 1); }
    }
    return "A connected item";
  }
  function agentByRef(agentRef) {
    for (var i = 0; i < state.agents.length; i += 1) {
      if (state.agents[i].agent_ref === agentRef) { return state.agents[i]; }
    }
    return null;
  }

  function emptyRow(tbody, columns, text) {
    tbody.textContent = "";
    var tr = el("tr");
    var td = el("td", null, text);
    td.colSpan = columns;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  function button(label, handler, cls) {
    var b = el("button", cls || "btn", label);
    b.type = "button";
    b.addEventListener("click", function () {
      if (b.disabled) { return; }
      b.disabled = true;
      Promise.resolve().then(handler).catch(failed).then(function () { b.disabled = false; });
    });
    return b;
  }

  // ---- status -----------------------------------------------------------
  async function loadStatus() {
    var status = await run("GET", "/status", undefined, "check your cloud accounts");
    var list = document.getElementById("cag-providers");
    list.textContent = "";
    var bindings = Array.isArray(status.provider_bindings) ? status.provider_bindings : [];
    bindings.forEach(function (binding) {
      var name = PROVIDERS[binding.provider] || human(binding.provider);
      list.appendChild(el("li", null, name + ": " + (binding.active ? "connected" :
        binding.bound ? "connection paused" : "not connected yet")));
    });
    state.frameworkEnabled = status.databricks_framework_enabled === true;
    document.getElementById("cag-status").textContent = !status.enabled
      ? "Cloud AI agents are turned off for this workspace."
      : bindings.length
        ? "These cloud accounts are set up for this workspace."
        : "No cloud accounts are set up yet. Ask an administrator to connect one.";
  }

  // ---- agents -----------------------------------------------------------
  async function loadAgents() {
    var body = await run("GET", "/inventory?limit=500", undefined, "load your cloud agents");
    var agents = [];
    (Array.isArray(body.providers) ? body.providers : []).forEach(function (provider) {
      var metadata = provider.metadata || {};
      (Array.isArray(provider.records) ? provider.records : []).forEach(function (record) {
        var copy = Object.assign({}, record);
        copy.provider_name = PROVIDERS[record.provider] || human(record.provider);
        copy.complete = metadata.snapshot_complete === true;
        agents.push(copy);
      });
    });
    agents.sort(function (a, b) {
      return (a.provider_name + a.first_seen_at).localeCompare(b.provider_name + b.first_seen_at);
    });
    var counts = {};
    agents.forEach(function (agent) {
      counts[agent.provider_name] = (counts[agent.provider_name] || 0) + 1;
      agent.label = agent.provider_name + " agent " + counts[agent.provider_name] +
        (agent.first_seen_at ? " (first seen " + when(agent.first_seen_at) + ")" : "");
    });
    state.agents = agents;
    renderAgents();
  }

  function renderAgents() {
    var tbody = document.getElementById("cag-agents");
    if (!state.agents.length) {
      emptyRow(tbody, 5, "No cloud agents found yet. Once an administrator connects a cloud account, its agents appear here after the next scan.");
      return;
    }
    tbody.textContent = "";
    state.agents.forEach(function (agent) {
      var tr = el("tr");
      var th = el("th", null, agent.label);
      th.scope = "row";
      tr.appendChild(th);
      var status = el("td");
      status.appendChild(badge(LIFECYCLE[agent.lifecycle_state] || [human(agent.lifecycle_state), "idle"]));
      if (agent.drifted) { status.appendChild(el("div", "muted", "Changed since last review")); }
      tr.appendChild(status);
      tr.appendChild(el("td", null, (agent.tool_refs || []).length + " tools · " +
        (agent.model_refs || []).length + " models · " +
        (agent.data_source_refs || []).length + " data sources"));
      tr.appendChild(el("td", null, when(agent.last_seen_at)));
      var actions = el("td");
      var wrap = el("div", "cag-actions", null);
      if (can.operate) {
        var check = button("Check against a policy", function () { return openCheck(agent); });
        check.setAttribute("aria-label", "Check " + agent.label + " against a policy");
        wrap.appendChild(check);
      }
      actions.appendChild(wrap);
      tr.appendChild(actions);
      tbody.appendChild(tr);
    });
  }

  // ---- policy check -----------------------------------------------------
  async function loadPolicies() {
    try {
      var body = await run("GET", "/policies", undefined, "load your policies");
      state.policies = Array.isArray(body.items) ? body.items : [];
    } catch (_error) {
      state.policies = [];
    }
  }

  function option(select, value, label) {
    var o = el("option", null, label);
    o.value = value;
    select.appendChild(o);
  }

  function fillTargets(agent) {
    var action = document.getElementById("cag-check-action").value;
    var select = document.getElementById("cag-check-target");
    select.textContent = "";
    var key = { restrict_tool: ["tool_refs", "Tool"], restrict_model: ["model_refs", "Model"],
      detach_data_source: ["data_source_refs", "Data source"] }[action];
    if (!key) { option(select, agent.agent_ref, "The whole agent"); return; }
    var refs = agent[key[0]] || [];
    if (!refs.length) { option(select, "", "This agent has none"); return; }
    refs.forEach(function (ref, index) { option(select, ref, key[1] + " " + (index + 1)); });
  }

  async function openCheck(agent) {
    if (!state.policies.length) { await loadPolicies(); }
    state.checkAgent = agent;
    var panel = document.getElementById("cag-check-panel");
    document.getElementById("cag-check-agent").textContent = agent.label;
    var policy = document.getElementById("cag-check-policy");
    policy.textContent = "";
    if (!state.policies.length) {
      option(policy, "", "No policies are available from your policy service yet");
    } else {
      state.policies.forEach(function (item, index) { option(policy, String(index), item.title); });
    }
    var action = document.getElementById("cag-check-action");
    action.textContent = "";
    Object.keys(ACTIONS).forEach(function (key) { option(action, key, ACTIONS[key]); });
    fillTargets(agent);
    panel.hidden = false;
    document.getElementById("cag-check-title").focus();
  }

  async function submitCheck(form) {
    var agent = state.checkAgent;
    if (!agent) { return; }
    var policy = state.policies[Number(form.elements.policy.value)];
    if (!policy) { throw new Error("Choose a policy first. If the list is empty, ask an administrator to connect your policy service."); }
    var target = form.elements.target.value;
    if (!target) { throw new Error("This agent has nothing of that kind to change. Choose a different change."); }
    var evaluation = await run("POST", "/policy-evaluations", {
      agent_ref: agent.agent_ref,
      expected_agent_revision: agent.record_revision,
      policy_ref: policy.policy_ref,
      policy_revision: policy.policy_revision,
      policy_sha256: policy.policy_sha256,
      requested_action: form.elements.action.value,
      target_ref: target,
      idempotency_key: form.elements.idempotency_key.value
    }, "run the policy check");
    var decision = DECISIONS[evaluation.decision] || [human(evaluation.decision)];
    form.elements.idempotency_key.value = reference();
    say("Policy check finished: " + decision[0] + ".", evaluation);
    document.getElementById("cag-check-panel").hidden = true;
    await loadEvaluations();
  }

  // ---- evaluations ------------------------------------------------------
  async function loadEvaluations() {
    var body = await run("GET", "/policy-evaluations", undefined, "load policy checks");
    state.evaluations = Array.isArray(body.items) ? body.items : [];
    renderEvaluations();
  }
  function renderEvaluations() {
    var tbody = document.getElementById("cag-checks");
    if (!state.evaluations.length) {
      emptyRow(tbody, 5, "No policy checks yet. Use Check against a policy on an agent above.");
      return;
    }
    tbody.textContent = "";
    var prepared = {};
    state.intents.forEach(function (intent) { prepared[intent.evaluation_ref] = true; });
    state.evaluations.forEach(function (evaluation) {
      var tr = el("tr");
      var th = el("th", null, agentLabel(evaluation.agent_ref));
      th.scope = "row";
      tr.appendChild(th);
      tr.appendChild(el("td", null, (ACTIONS[evaluation.requested_action] || human(evaluation.requested_action)) +
        " · " + targetLabel(agentByRef(evaluation.agent_ref), evaluation.target_ref)));
      var decision = el("td");
      decision.appendChild(badge(DECISIONS[evaluation.decision] || [human(evaluation.decision), "idle"]));
      if (Array.isArray(evaluation.reason_codes) && evaluation.reason_codes.length) {
        decision.appendChild(el("div", "muted", evaluation.reason_codes.map(human).join(", ")));
      }
      tr.appendChild(decision);
      tr.appendChild(el("td", null, when(evaluation.evaluated_at)));
      var actions = el("td");
      if (can.admin && evaluation.decision === "allow_intent" && !prepared[evaluation.evaluation_ref]) {
        actions.appendChild(button("Prepare change", function () { return prepareChange(evaluation); }));
      } else if (prepared[evaluation.evaluation_ref]) {
        actions.appendChild(el("span", "muted", "Change prepared"));
      }
      tr.appendChild(actions);
      tbody.appendChild(tr);
    });
  }
  async function prepareChange(evaluation) {
    var intent = await run("POST", "/enforcement-intents", {
      evaluation_ref: evaluation.evaluation_ref,
      idempotency_key: reference()
    }, "prepare the change");
    say("Change prepared. Review it under Changes, then send it to the provider.", intent);
    await loadIntents();
    renderEvaluations();
  }

  // ---- intents ----------------------------------------------------------
  async function loadIntents() {
    var body = await run("GET", "/enforcement-intents", undefined, "load changes");
    state.intents = Array.isArray(body.items) ? body.items : [];
    renderIntents();
  }
  function renderIntents() {
    var tbody = document.getElementById("cag-changes");
    if (!state.intents.length) {
      emptyRow(tbody, 5, "No changes prepared yet. After a policy check allows a change, use Prepare change.");
      return;
    }
    tbody.textContent = "";
    state.intents.forEach(function (intent) {
      var tr = el("tr");
      var th = el("th", null, agentLabel(intent.agent_ref));
      th.scope = "row";
      tr.appendChild(th);
      tr.appendChild(el("td", null, (ACTIONS[intent.action] || human(intent.action)) + " · " +
        targetLabel(agentByRef(intent.agent_ref), intent.target_ref)));
      var status = el("td");
      status.appendChild(badge(STATUSES[intent.status] || [human(intent.status), "idle"]));
      tr.appendChild(status);
      tr.appendChild(el("td", null, String(intent.attempts || 0)));
      var actions = el("td");
      var wrap = el("div", "cag-actions", null);
      if (can.admin && (intent.status === "prepared" || intent.status === "retryable")) {
        wrap.appendChild(button("Send to provider", function () { return dispatchChange(intent); }, "btn btn--primary"));
      }
      if (can.admin && (intent.status === "dispatching" || intent.status === "reconciliation_required")) {
        wrap.appendChild(button("Confirm what happened", function () { return openConfirm(intent); }));
      }
      actions.appendChild(wrap);
      tr.appendChild(actions);
      tbody.appendChild(tr);
    });
  }
  async function confirmed(message, okText) {
    if (typeof window.mvConfirm === "function") { return window.mvConfirm(message, { okText: okText }); }
    return false;
  }
  async function dispatchChange(intent) {
    var ok = await confirmed(
      "Send this change to " + (PROVIDERS[intent.provider] || "the provider") + ": " +
        (ACTIONS[intent.action] || human(intent.action)) + " for " + agentLabel(intent.agent_ref) +
        "? The provider will act on it straight away.",
      "Send change"
    );
    if (!ok) { return; }
    var body = await run("POST", "/enforcement-intents/" + encodeURIComponent(intent.intent_ref) + "/dispatch", {
      expected_revision: intent.revision,
      idempotency_key: reference()
    }, "send the change");
    var status = STATUSES[body.status] || [human(body.status)];
    say("Sent. Current status: " + status[0] + ". After the next scan of the provider, confirm whether it took effect.", body);
    await loadIntents();
  }

  function openConfirm(intent) {
    state.confirmIntent = intent;
    document.getElementById("cag-confirm-change").textContent =
      (ACTIONS[intent.action] || human(intent.action)) + " for " + agentLabel(intent.agent_ref);
    document.getElementById("cag-confirm-panel").hidden = false;
    document.getElementById("cag-confirm-title").focus();
  }

  async function fingerprint(file) {
    if (!file) { throw new Error("Choose a file first."); }
    if (!window.crypto || !window.crypto.subtle) {
      throw new Error("This browser cannot read the file securely. Try a current version of Chrome, Edge, Firefox or Safari.");
    }
    var buffer = await file.arrayBuffer();
    var digest = await window.crypto.subtle.digest("SHA-256", buffer);
    return Array.prototype.map.call(new Uint8Array(digest), function (byte) {
      return ("0" + byte.toString(16)).slice(-2);
    }).join("");
  }
  function reference() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") { return "ui-" + window.crypto.randomUUID(); }
    return "ui-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
  }

  async function submitConfirm(form) {
    var intent = state.confirmIntent;
    if (!intent) { return; }
    var agent = agentByRef(intent.agent_ref);
    if (!agent) { throw new Error("This agent is no longer in the list. Refresh the page and try again."); }
    var digest = await fingerprint(form.elements.evidence.files && form.elements.evidence.files[0]);
    var body = await run("POST", "/enforcement-intents/" + encodeURIComponent(intent.intent_ref) + "/reconcile", {
      expected_revision: intent.revision,
      resolution: form.elements.resolution.value,
      evidence_ref: "evd_" + digest.slice(0, 32),
      evidence_sha256: digest,
      observed_inventory_revision: agent.inventory_revision,
      idempotency_key: form.elements.idempotency_key.value
    }, "save the confirmation");
    var status = STATUSES[body.status] || [human(body.status)];
    say("Confirmation saved. Status: " + status[0] + ".", body);
    document.getElementById("cag-confirm-panel").hidden = true;
    form.reset();
    form.elements.idempotency_key.value = reference();
    await loadIntents();
  }

  // ---- Databricks -------------------------------------------------------
  async function loadFramework() {
    var panel = document.getElementById("cag-framework");
    if (!state.frameworkEnabled) { panel.hidden = true; return; }
    var body = await run("GET", "/databricks/framework", undefined, "load Databricks approvals");
    state.framework = body;
    panel.hidden = false;
    var active = body.activation;
    document.getElementById("cag-framework-active").textContent = active
      ? "In use: mapping version " + active.version + " since " + when(active.activated_at) + "."
      : "No Databricks mapping is in use yet.";
    var attested = {};
    (body.attestations || []).forEach(function (a) { attested[a.review_ref] = a; });
    var tbody = document.getElementById("cag-reviews");
    var reviews = body.reviews || [];
    if (!reviews.length) {
      emptyRow(tbody, 4, "No Databricks mapping reviews yet.");
      return;
    }
    tbody.textContent = "";
    reviews.forEach(function (review) {
      var tr = el("tr");
      var th = el("th", null, review.pack_version);
      th.scope = "row";
      tr.appendChild(th);
      tr.appendChild(el("td", null, (review.decision === "approved" ? "Approved" : "Rejected") + " · " + when(review.reviewed_at)));
      var attestation = attested[review.review_ref];
      tr.appendChild(el("td", null, attestation ? "Approved " + when(attestation.attested_at) : "Waiting"));
      var actions = el("td");
      var wrap = el("div", "cag-actions", null);
      if (can.admin && review.decision === "approved" && !attestation) {
        if (review.reviewed_by_you) {
          wrap.appendChild(el("span", "muted", "Someone else must give the second approval"));
        } else {
          wrap.appendChild(button("Give second approval", function () { return openAttest(review); }));
        }
      }
      if (can.admin && attestation && (!active || active.version !== review.pack_version)) {
        wrap.appendChild(button("Start using this version", function () {
          return activate(review, attestation, "activate");
        }, "btn btn--primary"));
      }
      if (can.admin && attestation && active && active.version === review.pack_version && active.prior_version) {
        wrap.appendChild(button("Go back to the previous version", function () {
          return activate(review, attestation, "rollback");
        }, "btn btn--danger"));
      }
      actions.appendChild(wrap);
      tr.appendChild(actions);
      tbody.appendChild(tr);
    });
  }
  function openAttest(review) {
    state.attestReview = review;
    document.getElementById("cag-attest-review").textContent = "Mapping version " + review.pack_version;
    document.getElementById("cag-attest-panel").hidden = false;
    document.getElementById("cag-attest-title").focus();
  }
  async function submitAttest(form) {
    var review = state.attestReview;
    if (!review) { return; }
    var digest = await fingerprint(form.elements.evidence.files && form.elements.evidence.files[0]);
    var body = await run("POST", "/databricks/framework-attestations", {
      review_ref: review.review_ref,
      expected_review_revision: review.revision,
      evidence_links: [{ evidence_ref: "evd_" + digest.slice(0, 32), evidence_sha256: digest,
        relation_code: form.elements.relation.value }],
      idempotency_key: form.elements.idempotency_key.value
    }, "save the second approval");
    say("Second approval saved. The mapping can now be put into use.", body);
    document.getElementById("cag-attest-panel").hidden = true;
    form.reset();
    form.elements.idempotency_key.value = reference();
    await loadFramework();
  }
  async function activate(review, attestation, action) {
    var ok = await confirmed(action === "rollback"
      ? "Go back to the previous Databricks mapping version? The current one stops being used."
      : "Start using Databricks mapping version " + review.pack_version + "? It replaces the version in use.",
      action === "rollback" ? "Go back" : "Start using it");
    if (!ok) { return; }
    var active = state.framework && state.framework.activation;
    var body = await run("POST", "/databricks/framework-activations", {
      pack_version: review.pack_version,
      review_ref: review.review_ref,
      attestation_ref: attestation.attestation_ref,
      expected_activation_revision: active ? active.revision : 0,
      action: action,
      idempotency_key: reference()
    }, action === "rollback" ? "go back to the previous version" : "start using this version");
    say(action === "rollback" ? "Went back to the previous version." : "Mapping version " + review.pack_version + " is now in use.", body);
    await loadFramework();
  }

  // ---- wiring -----------------------------------------------------------
  function guard(form, handler) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var submit = form.querySelector("button[type=submit]");
      if (submit) { submit.disabled = true; }
      Promise.resolve().then(function () { return handler(form); }).catch(failed)
        .then(function () { if (submit) { submit.disabled = false; } });
    });
  }
  guard(document.getElementById("cag-check-form"), submitCheck);
  guard(document.getElementById("cag-confirm-form"), submitConfirm);
  guard(document.getElementById("cag-attest-form"), submitAttest);
  document.getElementById("cag-check-action").addEventListener("change", function () {
    if (state.checkAgent) { fillTargets(state.checkAgent); }
  });
  [["cag-check-cancel", "cag-check-panel"], ["cag-confirm-cancel", "cag-confirm-panel"],
    ["cag-attest-cancel", "cag-attest-panel"]].forEach(function (pair) {
    document.getElementById(pair[0]).addEventListener("click", function () {
      document.getElementById(pair[1]).hidden = true;
    });
  });
  var integrity = document.getElementById("cag-integrity");
  if (!can.audit) { integrity.hidden = true; }
  integrity.addEventListener("click", function () {
    integrity.disabled = true;
    run("GET", "/integrity", undefined, "check the records").then(function (body) {
      say(body.verified === true ? "All cloud agent records are intact."
        : "Some records could not be confirmed as intact. Ask an administrator to look into it.", body);
    }).catch(failed).then(function () { integrity.disabled = false; });
  });

  async function loadAll() {
    try { await loadStatus(); } catch (error) {
      document.getElementById("cag-status").textContent = error.message;
    }
    var tasks = [
      [loadAgents, "cag-agents", 5],
      [loadIntents, "cag-changes", 5]
    ];
    for (var i = 0; i < tasks.length; i += 1) {
      try { await tasks[i][0](); } catch (error) {
        emptyRow(document.getElementById(tasks[i][1]), tasks[i][2], error.message);
      }
    }
    try { await loadEvaluations(); } catch (error) {
      emptyRow(document.getElementById("cag-checks"), 5, error.message);
    }
    try { await loadFramework(); } catch (error) {
      document.getElementById("cag-framework-active").textContent = error.message;
    }
  }
  document.getElementById("cag-refresh").addEventListener("click", loadAll);
  loadAll();

  window.__cloudAgentPage = { run: run, state: state, loadAll: loadAll };
})();
