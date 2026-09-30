(function () {
  "use strict";

  var root = document.getElementById("ec-root");
  if (!root) { return; }
  var admin = root.dataset.mode === "admin";
  var base = "/api/v1/employee-companion";
  var context = root.dataset.companionContext || "";
  var busy = false;
  var loaded = false;
  var blocked = false;
  var stale = false;
  var available = false;
  var policy = null;
  var policies = [];
  var employees = [];
  var messages = [];
  var status = document.getElementById("ec-status");

  function el(id) { return document.getElementById(id); }
  function node(tag, text, className) {
    var item = document.createElement(tag);
    if (text !== undefined) { item.textContent = String(text); }
    if (className) { item.className = className; }
    return item;
  }
  function announce(text, error) {
    status.textContent = text;
    status.dataset.error = error ? "true" : "false";
  }
  function dateLabel(value) {
    if (value === null || value === undefined || value === "") { return "Time unavailable"; }
    var date = new Date(typeof value === "number" ? value * 1000 : value);
    return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString();
  }
  function taskState(value) {
    var labels = {
      running: "Running", active: "Running", pending: "Waiting", queued: "Queued", planned: "Planned",
      paused: "Paused", blocked: "Needs attention", waiting_for_approval: "Needs approval",
      awaiting_approval: "Needs approval", awaiting_input: "Needs your input", needs_input: "Needs your input",
      completed: "Completed", succeeded: "Completed", done: "Completed", failed: "Failed",
      cancelled: "Cancelled", canceled: "Cancelled", stopped: "Stopped", interrupted: "Interrupted",
      error: "Needs attention", draft: "Awaiting submission"
    };
    return labels[value] || "Status unavailable";
  }
  function retentionLabel(days) {
    return days === 0 ? "Not saved" : days === 1 ? "1 day" : String(days) + " days";
  }
  function employeeName(principal) {
    var employee = employees.find(function (item) { return item.principal === principal; });
    return employee ? employee.label || "Employee" : "Workspace member";
  }
  function taskLink(task, label) {
    var link;
    try {
      var target = new URL(task.href, window.location.origin);
      if (typeof task.href !== "string" || target.origin !== window.location.origin ||
          !/^\/chat\/goal\/[^/?#]+$/.test(target.pathname) || target.search || target.hash) {
        return node("span", label);
      }
      link = node("a", label);
      link.href = target.pathname;
      return link;
    } catch (_) { return node("span", label); }
  }
  function setBusy(value) {
    busy = value;
    root.setAttribute("aria-busy", value ? "true" : "false");
    el("ec-refresh").disabled = busy || blocked;
    if (admin) {
      el("ec-policy-controls").disabled = busy || blocked || !loaded;
      el("ec-save").disabled = busy || blocked || stale || !loaded || !el("ec-owner").value;
    } else {
      var mayAsk = loaded && available && !blocked && !stale && !busy;
      el("ec-message").disabled = !mayAsk;
      el("ec-send").disabled = !mayAsk;
      root.querySelectorAll("[data-question]").forEach(function (button) { button.disabled = !mayAsk; });
      el("ec-clear").disabled = busy || blocked || !loaded;
      el("ec-clear-yes").disabled = busy || blocked || !loaded;
      el("ec-clear-no").disabled = busy;
    }
  }
  function clearSensitiveView() {
    messages = [];
    if (admin) {
      policies = [];
      employees = [];
      el("ec-policies").textContent = "";
      el("ec-events").textContent = "";
      el("ec-owner").textContent = "";
      el("ec-policy-form").reset();
    } else {
      el("ec-messages").textContent = "";
      el("ec-tasks").textContent = "";
      el("ec-message").value = "";
      el("ec-clear-confirm").hidden = true;
    }
    el("ec-policy-label").textContent = "Access needs checking";
    el("ec-policy-summary").textContent = "Reload this page after checking your account and workspace.";
    el("ec-badge").textContent = "Unavailable";
  }
  function failure(error) {
    if (error.status === 401 || error.status === 403 || error.contextChanged) {
      blocked = true;
      clearSensitiveView();
      announce("Your account, workspace or access changed. Reload this page to check your current access.", true);
    } else if (error.status === 409) {
      stale = true;
      announce(admin
        ? "This policy changed since you loaded it. Your unsaved choices are still here. Reload saved policies, review the current values and apply your choices again."
        : "Your companion policy changed. Your question is still here. Refresh to check the current policy before asking again.", true);
    } else {
      announce(error.message || "The companion could not be reached. Refresh to try again.", true);
    }
  }
  async function request(path, method, body) {
    var controller = new AbortController();
    var timer = window.setTimeout(function () { controller.abort(); }, 15000);
    try {
      var headers = { Accept: "application/json" };
      if (context) { headers["X-Lightwork-Companion-Context"] = context; }
      if (body !== undefined) { headers["Content-Type"] = "application/json"; }
      var response = await fetch(base + path, {
        method: method || "GET", credentials: "same-origin", cache: "no-store",
        headers: headers, signal: controller.signal,
        body: body === undefined ? undefined : JSON.stringify(body)
      });
      // Check loss of access before parsing a possible HTML sign-in response.
      if (response.status === 401 || response.status === 403) {
        var denied = new Error("Access changed.");
        denied.status = response.status;
        throw denied;
      }
      var text = await response.text();
      if (text.length > 2 * 1024 * 1024) { throw new Error("The reply was too large. Refresh to try again."); }
      var data;
      try { data = text ? JSON.parse(text) : {}; }
      catch (_) { throw new Error("Lightwork returned an unexpected reply. Refresh to try again."); }
      if (!response.ok) {
        var error = new Error(response.status === 400 || response.status === 422
          ? "Check your entries and try again. Your changes have not been confirmed."
          : "Lightwork could not confirm this request. Refresh to check the current state before retrying.");
        error.status = response.status;
        var detail = data.detail;
        var code = typeof detail === "string" ? detail : detail && detail.code;
        error.contextChanged = typeof code === "string" && /context.*(changed|mismatch)|companion_context/.test(code);
        throw error;
      }
      if (blocked || !data || typeof data.context !== "string" || !data.context ||
          (context && data.context !== context)) {
        var mismatch = new Error("The account or workspace changed.");
        mismatch.contextChanged = true;
        throw mismatch;
      }
      context = data.context;
      return data;
    } catch (error) {
      if (error.name === "AbortError") {
        throw new Error("This is taking too long. Refresh to check the current state before retrying. Your entered text is still here.");
      }
      if (error instanceof TypeError) {
        throw new Error("Could not connect to Lightwork. Check your connection and try again. Your entered text is still here.");
      }
      throw error;
    } finally { window.clearTimeout(timer); }
  }
  function renderMessages() {
    var container = el("ec-messages");
    container.textContent = "";
    if (!messages.length) {
      container.appendChild(node("p", "Ask what is running or what needs your attention. Your tasks keep working while you check in.", "muted"));
      return;
    }
    messages.forEach(function (message) {
      if (!message || typeof message.content !== "string") { return; }
      var user = message.role === "user";
      var article = node("article", undefined, "ec-message" + (user ? " ec-message--user" : ""));
      article.appendChild(node("h3", user ? "You" : "Companion"));
      article.appendChild(node("p", message.content));
      if (!user && Array.isArray(message.sources) && message.sources.length) {
        var sources = node("ul", undefined, "ec-sources");
        sources.setAttribute("aria-label", "Task sources checked for this answer");
        message.sources.forEach(function (source, index) {
          var item = node("li");
          item.appendChild(taskLink(source, "Task " + (index + 1) + " · " + taskState(source.status)));
          item.appendChild(node("span", " — checked " + dateLabel(source.checked_at), "muted"));
          sources.appendChild(item);
        });
        article.appendChild(sources);
      }
      if (!user && message.trace) {
        var trace = node("details");
        trace.appendChild(node("summary", "Answer record"));
        if (message.trace.decision === "allowed" && message.trace.reason === "owner_and_policy") {
          trace.appendChild(node("p", "Allowed after checking your ownership and companion policy."));
        }
        var revision = message.trace.policy_revision !== undefined ? message.trace.policy_revision : message.trace.revision;
        if (Number.isInteger(revision)) { trace.appendChild(node("p", "Companion policy version " + revision + ".")); }
        if (message.trace.checked_at || message.trace.time) {
          trace.appendChild(node("p", "Checked " + dateLabel(message.trace.checked_at || message.trace.time) + "."));
        }
        article.appendChild(trace);
      }
      container.appendChild(article);
    });
  }
  function renderTasks(tasks) {
    var list = el("ec-tasks");
    list.textContent = "";
    if (!available) { list.appendChild(node("li", "Task access is unavailable under your current companion settings.")); return; }
    if (!tasks.length) { list.appendChild(node("li", "No active tasks to show.")); return; }
    tasks.forEach(function (task) {
      var item = node("li");
      item.appendChild(taskLink(task, task.title || "View task"));
      item.appendChild(node("span", taskState(task.status) + " · Checked " + dateLabel(task.checked_at), "muted"));
      list.appendChild(item);
    });
  }
  function applyEmployee(data, preserveTransient) {
    var sameTransientPolicy = policy && data.policy && policy.retention_days === 0 && policy.revision === data.policy.revision;
    policy = data.policy;
    available = data.available === true;
    if (!policy || !Number.isInteger(policy.revision)) { throw new Error("Your companion policy could not be checked. Refresh to try again."); }
    var days = policy.retention_days;
    el("ec-policy-label").textContent = available ? "Ready for task questions" : "Companion unavailable";
    el("ec-badge").textContent = available ? "Read only" : "Not enabled";
    el("ec-policy-summary").textContent = data.explanation || (available ? "Your own task status, under your administrator's policy." : "Ask your administrator about companion enrollment.");
    el("ec-retention").textContent = days === 0
      ? "New messages are not saved on the server. They stay on this page until you clear it, reload or leave."
      : "History expires after " + retentionLabel(days) + ". Expired messages are removed when you access history or an administrator updates your policy. You can clear it here.";
    if (!preserveTransient || !sameTransientPolicy || days !== 0 || !available) {
      messages = Array.isArray(data.history) ? data.history : [];
    } else {
      // A new snapshot does not reauthorize historic source references.
      messages = messages.map(function (message) { return Object.assign({}, message, { sources: [] }); });
    }
    renderMessages();
    renderTasks(Array.isArray(data.tasks) ? data.tasks : []);
  }
  function selectEmployee() {
    var owner = el("ec-owner").value;
    var selected = policies.find(function (item) { return item.owner === owner; });
    selected = selected || { revision: 0, enabled: false, allowed_sources: [], allowed_tools: [], retention_days: 0 };
    el("ec-revision").value = String(selected.revision);
    el("ec-enabled").checked = selected.enabled === true;
    el("ec-source").checked = (selected.allowed_sources || []).includes("own_tasks");
    el("ec-tool").checked = (selected.allowed_tools || []).includes("task_status");
    el("ec-retention-days").value = String(selected.retention_days);
    el("ec-selection-summary").textContent = !owner ? "Choose an employee to review their policy." : selected.revision === 0 ? "Not enrolled. Review the options before enabling." : "Showing this employee's saved policy.";
    setBusy(busy);
  }
  function tableEmpty(container, text) {
    var row = node("tr");
    var cell = node("td", text);
    cell.colSpan = 4;
    row.appendChild(cell);
    container.appendChild(row);
  }
  function applyAdmin(data) {
    policies = Array.isArray(data.policies) ? data.policies : [];
    employees = Array.isArray(data.employees) ? data.employees : [];
    available = data.available === true;
    el("ec-policy-label").textContent = available ? "Workspace preview enabled" : "Workspace preview turned off";
    el("ec-badge").textContent = available ? "Read only" : "Off";
    el("ec-policy-summary").textContent = data.explanation || (available ? "Each employee also needs an enabled policy." : "You can prepare employee policies while the workspace preview is off.");
    var owner = el("ec-owner");
    var selected = owner.value;
    owner.textContent = "";
    var placeholder = node("option", employees.length ? "Choose an employee" : "No employees available");
    placeholder.value = "";
    owner.appendChild(placeholder);
    employees.forEach(function (employee) {
      var option = node("option", employee.label || "Employee");
      option.value = employee.principal;
      owner.appendChild(option);
    });
    if (employees.some(function (employee) { return employee.principal === selected; })) { owner.value = selected; }
    var rows = el("ec-policies");
    rows.textContent = "";
    if (!policies.length) { tableEmpty(rows, "No employee policies saved yet. Choose an employee above to begin."); }
    policies.forEach(function (item) {
      var row = node("tr");
      row.appendChild(node("td", employeeName(item.owner)));
      row.appendChild(node("td", item.enabled ? "Enabled" : "Disabled"));
      var taskAccess = (item.allowed_sources || []).includes("own_tasks") && (item.allowed_tools || []).includes("task_status");
      row.appendChild(node("td", taskAccess ? "Own task status" : "Not allowed"));
      row.appendChild(node("td", retentionLabel(item.retention_days)));
      rows.appendChild(row);
    });
    var events = el("ec-events");
    events.textContent = "";
    var activity = Array.isArray(data.events) ? data.events : [];
    if (!activity.length) { tableEmpty(events, "No companion activity recorded yet."); }
    var actions = {
      policy_updated: "Policy changed", policy_changed: "Policy changed", policy_saved: "Policy saved",
      reply: "Companion question answered", message: "Task question answered", message_answered: "Task question answered", read: "Task status checked",
      history_cleared: "Conversation history cleared", history_deleted: "Conversation history cleared",
      history_expired: "Expired conversation history removed", retention_pruned: "Expired conversation history removed", message_denied: "Question blocked by policy"
    };
    activity.forEach(function (event) {
      var row = node("tr");
      row.appendChild(node("td", dateLabel(event.time)));
      row.appendChild(node("td", employeeName(event.owner)));
      row.appendChild(node("td", actions[event.action] || "Companion activity recorded"));
      row.appendChild(node("td", employeeName(event.actor)));
      events.appendChild(row);
    });
    selectEmployee();
  }
  async function refresh() {
    if (busy || blocked) { return; }
    setBusy(true);
    announce(admin ? "Loading saved policies and activity." : "Checking your policy and task status.");
    try {
      var data = await request(admin ? "/admin" : "");
      if (admin) { applyAdmin(data); } else { applyEmployee(data, true); }
      loaded = true;
      stale = false;
      announce(admin ? "Saved policies and activity are up to date." : available ? "Ready. Ask about your work." : "Your companion is unavailable. You can still clear your conversation history.");
    } catch (error) { failure(error); }
    finally { setBusy(false); }
  }

  el("ec-refresh").addEventListener("click", refresh);
  if (admin) {
    el("ec-owner").addEventListener("change", selectEmployee);
    el("ec-policy-form").addEventListener("submit", async function (event) {
      event.preventDefault();
      if (busy || blocked || stale || !loaded || !el("ec-owner").value) { return; }
      var body = {
        owner: el("ec-owner").value, expected_revision: Number(el("ec-revision").value),
        enabled: el("ec-enabled").checked,
        allowed_sources: el("ec-source").checked ? ["own_tasks"] : [],
        allowed_tools: el("ec-tool").checked ? ["task_status"] : [],
        retention_days: Number(el("ec-retention-days").value)
      };
      setBusy(true);
      announce("Saving this employee's policy.");
      try {
        await request("/admin/policy", "PUT", body);
        // Read the current workspace snapshot before claiming the page is current.
        var data = await request("/admin");
        applyAdmin(data);
        announce("Policy saved. The current settings are shown below.");
      } catch (error) { failure(error); }
      finally { setBusy(false); }
    });
  } else {
    el("ec-message-form").addEventListener("submit", async function (event) {
      event.preventDefault();
      if (busy || blocked || stale || !loaded || !available) { return; }
      var input = el("ec-message");
      var question = input.value.trim();
      if (!question) { input.focus(); return; }
      setBusy(true);
      announce("Checking your task status.");
      try {
        var data = await request("/messages", "POST", { message: question, expected_revision: policy.revision });
        if (!data.message || typeof data.message.content !== "string" || !data.policy) {
          throw new Error("The answer could not be confirmed. Your question is still here. Refresh before retrying.");
        }
        policy = data.policy;
        if (policy.retention_days === 0) {
          messages = messages.map(function (message) { return Object.assign({}, message, { sources: [] }); });
          messages = messages.concat([{ role: "user", content: question }, data.message]).slice(-100);
        } else {
          messages = Array.isArray(data.history) ? data.history : messages.concat([{ role: "user", content: question }, data.message]);
          // Persisted turns deliberately omit source links until a fresh access
          // check. Preserve only the current answer's freshly checked sources.
          if (messages.length && messages[messages.length - 1].role === "assistant") {
            messages[messages.length - 1] = data.message;
          }
        }
        renderMessages();
        input.value = "";
        announce("Answer ready. Task sources show when they were checked.");
      } catch (error) { failure(error); }
      finally { setBusy(false); if (!input.disabled) { input.focus(); } }
    });
    root.querySelectorAll("[data-question]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (busy || blocked || stale || !available) { return; }
        el("ec-message").value = button.dataset.question;
        el("ec-message").focus();
      });
    });
    el("ec-clear").addEventListener("click", function () {
      el("ec-clear-confirm").hidden = false;
      el("ec-clear-no").focus();
    });
    el("ec-clear-no").addEventListener("click", function () {
      el("ec-clear-confirm").hidden = true;
      el("ec-clear").focus();
    });
    el("ec-clear-yes").addEventListener("click", async function () {
      if (busy || blocked || !loaded) { return; }
      setBusy(true);
      announce("Clearing your conversation history.");
      try {
        await request("/history", "DELETE");
        messages = [];
        renderMessages();
        el("ec-clear-confirm").hidden = true;
        announce("Your companion conversation has been cleared. Your tasks are unchanged.");
      } catch (error) { failure(error); }
      finally { setBusy(false); if (!el("ec-clear").disabled) { el("ec-clear").focus(); } }
    });
  }
  // Do not carry private content through the browser's page restoration cache.
  window.addEventListener("pagehide", function () { blocked = true; clearSensitiveView(); });
  window.addEventListener("pageshow", function (event) { if (event.persisted) { window.location.reload(); } });
  refresh();
})();
