/* AI-entity task operator workbench. Tenant, actor, entity authority, authority
   digest, retention policy, and storage remain server-owned. */
(function () {
  "use strict";

  var MAX_RESPONSE_BYTES = 64 * 1024;
  var ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var OPERATION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/;

  function responseTooLarge() {
    var error = new Error("The server response exceeded the 64 KiB safety limit.");
    error.code = "response_too_large";
    return error;
  }

  function invalidResponse() {
    var error = new Error("The server returned an invalid JSON response.");
    error.code = "response_invalid_json";
    return error;
  }

  async function cancelBody(body) {
    if (!body || typeof body.cancel !== "function") return;
    try { await body.cancel(); }
    catch (_error) { /* The bounded request has already failed closed. */ }
  }

  async function boundedResponseText(response) {
    var declared = response.headers && response.headers.get("Content-Length");
    if (declared && /^\d+$/.test(declared) && Number(declared) > MAX_RESPONSE_BYTES) {
      await cancelBody(response.body);
      throw responseTooLarge();
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      // A whole-body fallback cannot enforce the raw-byte ceiling before
      // allocation. Supported browsers expose a stream; otherwise fail closed.
      throw invalidResponse();
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8", {fatal: false});
    var received = 0;
    var text = "";
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      received += chunk.value.byteLength;
      if (received > MAX_RESPONSE_BYTES) {
        try { await reader.cancel(); }
        catch (_error) { /* The size violation remains authoritative. */ }
        throw responseTooLarge();
      }
      text += decoder.decode(chunk.value, {stream: true});
    }
    return text + decoder.decode();
  }

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  async function readBoundedJson(response) {
    var responseText = await boundedResponseText(response);
    if (!responseText) throw invalidResponse();
    var payload;
    try { payload = JSON.parse(responseText); }
    catch (_error) { throw invalidResponse(); }
    if (!isRecord(payload)) throw invalidResponse();
    return payload;
  }

  async function parseResponse(response) {
    var payload;
    try { payload = await readBoundedJson(response); }
    catch (error) {
      error.status = response.status;
      throw error;
    }
    if (!response.ok) {
      var failure = new Error(
        typeof payload.detail === "string" ? payload.detail : "The operation failed."
      );
      failure.status = response.status;
      throw failure;
    }
    return payload;
  }

  function positiveInteger(raw, label, maximum) {
    var selected = Number(raw);
    if (!Number.isSafeInteger(selected) || selected < 1 || selected > maximum) {
      throw new Error(label + " must be a positive integer no greater than " + maximum + ".");
    }
    return selected;
  }

  function identifier(raw, label) {
    var selected = String(raw || "").trim();
    if (!ID_PATTERN.test(selected)) {
      throw new Error(label + " must be a valid reference.");
    }
    return selected;
  }

  function operationKey(raw) {
    var selected = String(raw || "").trim();
    if (!OPERATION_PATTERN.test(selected)) {
      throw new Error("Enter a valid request reference; reuse it only when retrying the same action.");
    }
    return selected;
  }

  function splitRefs(raw) {
    var selected = String(raw || "").trim();
    if (!selected) return [];
    var seen = Object.create(null);
    var result = [];
    selected.split(",").forEach(function (candidate) {
      var reference = identifier(candidate, "Collaborator reference");
      if (!seen[reference]) {
        seen[reference] = true;
        result.push(reference);
      }
    });
    if (result.length > 64) throw new Error("No more than 64 collaborators are allowed.");
    return result;
  }

  function futureTimestamp(raw, label) {
    var milliseconds = Date.parse(String(raw || ""));
    if (!Number.isFinite(milliseconds) || milliseconds <= Date.now()) {
      throw new Error(label + " must be a valid future date and time.");
    }
    return milliseconds / 1000;
  }

  function buildCreatePayload(fields) {
    var title = String(fields.title || "").trim();
    if (!title || title.length > 500) throw new Error("Title is required and must not exceed 500 characters.");
    var description = String(fields.description || "").trim();
    if (description.length > 16000) throw new Error("Description must not exceed 16,000 characters.");
    var payload = {
      entity_id: identifier(fields.entity_id, "Inventory entity ID"),
      entity_revision: positiveInteger(fields.entity_revision, "Entity revision", 2147483647),
      kind: String(fields.kind),
      title: title,
      description: description,
      collaborator_refs: splitRefs(fields.collaborator_refs),
      priority: String(fields.priority),
      idempotency_key: operationKey(fields.idempotency_key)
    };
    if (["review", "remediation"].indexOf(payload.kind) === -1) {
      throw new Error("Task kind is invalid.");
    }
    if (["low", "medium", "high", "critical"].indexOf(payload.priority) === -1) {
      throw new Error("Priority is invalid.");
    }
    var assignee = String(fields.assignee_ref || "").trim();
    if (assignee) payload.assignee_ref = identifier(assignee, "Assignee reference");
    var due = String(fields.due_at || "").trim();
    if (due) payload.due_at = futureTimestamp(due, "Due date");
    return payload;
  }

  function buildUpdatePayload(fields) {
    var payload = {
      expected_revision: positiveInteger(fields.expected_revision, "Expected revision", 2147483647),
      idempotency_key: operationKey(fields.idempotency_key)
    };
    var changes = 0;
    var status = String(fields.status || "");
    if (status) {
      if (["open", "in_progress", "blocked", "resolved", "dismissed"].indexOf(status) === -1) {
        throw new Error("Status is invalid.");
      }
      payload.status = status;
      changes += 1;
    }
    var priority = String(fields.priority || "");
    if (priority) {
      if (["low", "medium", "high", "critical"].indexOf(priority) === -1) {
        throw new Error("Priority is invalid.");
      }
      payload.priority = priority;
      changes += 1;
    }
    var assigneeAction = String(fields.assignee_action || "keep");
    if (assigneeAction === "set") {
      payload.assignee_ref = identifier(fields.assignee_ref, "Assignee reference");
      changes += 1;
    } else if (assigneeAction === "clear") {
      payload.assignee_ref = null;
      changes += 1;
    } else if (assigneeAction !== "keep") {
      throw new Error("Assignee update action is invalid.");
    }
    var collaboratorAction = String(fields.collaborator_action || "keep");
    if (collaboratorAction === "replace") {
      payload.collaborator_refs = splitRefs(fields.collaborator_refs);
      changes += 1;
    } else if (collaboratorAction === "clear") {
      payload.collaborator_refs = [];
      changes += 1;
    } else if (collaboratorAction !== "keep") {
      throw new Error("Collaborator update action is invalid.");
    }
    var dueAction = String(fields.due_action || "keep");
    if (dueAction === "set") {
      payload.due_at = futureTimestamp(fields.due_at, "Due date");
      changes += 1;
    } else if (dueAction === "clear") {
      payload.due_at = null;
      changes += 1;
    } else if (dueAction !== "keep") {
      throw new Error("Due date update action is invalid.");
    }
    if (changes === 0) throw new Error("Select at least one task field to update.");
    return payload;
  }

  if (typeof window !== "undefined") {
    Object.defineProperty(window, "LightworkAIEntityTasksClient", {
      configurable: false,
      enumerable: false,
      writable: false,
      value: Object.freeze({
        MAX_RESPONSE_BYTES: MAX_RESPONSE_BYTES,
        readBoundedJson: readBoundedJson,
        parseResponse: parseResponse,
        splitRefs: splitRefs,
        buildCreatePayload: buildCreatePayload,
        buildUpdatePayload: buildUpdatePayload
      })
    });
  }

  if (typeof document === "undefined") return;
  var shell = document.getElementById("ai-entity-task-shell");
  if (!shell) return;
  var apiBase = shell.dataset.apiBase || "";
  var operations = document.getElementById("aet-operations");
  var readiness = document.getElementById("aet-readiness");
  var readinessDetail = document.getElementById("aet-readiness-detail");
  var statusMark = document.getElementById("aet-status-mark");
  var statusOutput = document.getElementById("aet-status-output");
  var operationStatus = document.getElementById("aet-operation-status");
  var operationOutput = document.getElementById("aet-operation-output");
  var taskList = document.getElementById("aet-task-list");
  var listStatus = document.getElementById("aet-list-status");
  var listCount = document.getElementById("aet-list-count");
  var filterForm = document.getElementById("aet-filter-form");
  var createForm = document.getElementById("aet-create-form");
  var updateForm = document.getElementById("aet-update-form");
  var inspectForm = document.getElementById("aet-inspect-form");

  function field(form, name) {
    var selected = form.elements.namedItem(name);
    if (!selected || typeof selected.value !== "string") {
      throw new Error("The task form is incomplete.");
    }
    return selected;
  }

  function values(form, names) {
    var result = {};
    names.forEach(function (name) { result[name] = field(form, name).value.trim(); });
    return result;
  }

  function show(element, value) {
    element.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }

  function setMark(label, state) {
    statusMark.textContent = label;
    statusMark.dataset.state = state;
  }

  function failureText(label, error) {
    return label + " failed" +
      (Number.isInteger(error.status) ? " (" + error.status + ")" : "") +
      ": " + String(error.message || error);
  }

  async function request(path, options) {
    var supplied = options || {};
    var selected = Object.assign({
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      referrerPolicy: "no-referrer"
    }, supplied);
    selected.headers = Object.assign({Accept: "application/json"}, supplied.headers || {});
    return parseResponse(await fetch(apiBase + path, selected));
  }

  async function run(label, action) {
    operationStatus.textContent = label + "…";
    operationStatus.dataset.state = "checking";
    operationOutput.textContent = "";
    try {
      var payload = await action();
      show(operationOutput, payload);
      operationStatus.textContent = label + " completed.";
      operationStatus.dataset.state = "ready";
      return payload;
    } catch (error) {
      operationStatus.textContent = failureText(label, error);
      operationStatus.dataset.state = "failed";
      error.rendered = true;
      throw error;
    }
  }

  function clear(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function make(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  function taskDefinition(list, label, value) {
    list.appendChild(make("dt", "", label));
    list.appendChild(make("dd", "", value === null || value === undefined || value === "" ? "—" : value));
  }

  function formattedDate(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "—";
    var date = new Date(value * 1000);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
  }

  function selectTask(task) {
    field(inspectForm, "task_id").value = String(task.task_id || "");
    field(updateForm, "task_id").value = String(task.task_id || "");
    field(updateForm, "expected_revision").value = String(task.revision || "");
    field(updateForm, "status").value = "";
    field(updateForm, "priority").value = "";
    field(updateForm, "assignee_action").value = "keep";
    field(updateForm, "collaborator_action").value = "keep";
    field(updateForm, "due_action").value = "keep";
  }

  function taskId() {
    return encodeURIComponent(identifier(field(inspectForm, "task_id").value, "Task ID"));
  }

  function renderTasks(payload) {
    clear(taskList);
    var items = Array.isArray(payload.items) ? payload.items : [];
    listCount.textContent = items.length + (items.length === 1 ? " task" : " tasks");
    if (!items.length) {
      taskList.appendChild(make("p", "aet-empty", "No tasks matched these filters."));
      return;
    }
    items.forEach(function (task) {
      if (!isRecord(task)) return;
      var card = make("article", "aet-task");
      var head = make("div", "aet-task__head");
      head.appendChild(make("h3", "", task.title || "Untitled task"));
      head.appendChild(make("span", "aet-pill", task.status || "unknown"));
      card.appendChild(head);
      var definitions = make("dl");
      taskDefinition(definitions, "Task", task.task_id);
      taskDefinition(definitions, "Entity", task.entity_id);
      taskDefinition(definitions, "Entity revision", task.entity_revision);
      taskDefinition(definitions, "Priority", task.priority);
      taskDefinition(definitions, "Assignee", task.assignee_ref);
      taskDefinition(definitions, "Due", formattedDate(task.due_at));
      taskDefinition(definitions, "Authority digest", task.entity_authority_sha256);
      taskDefinition(definitions, "Task revision", task.revision);
      card.appendChild(definitions);
      var actions = make("div", "aet-actions");
      var inspect = make("button", "btn", "Inspect");
      inspect.type = "button";
      inspect.addEventListener("click", async function () {
        selectTask(task);
        try { await run("Loading current task", function () { return request("/" + taskId()); }); }
        catch (_error) { /* The governed status region contains the error. */ }
      });
      var edit = make("button", "btn", "Use for update");
      edit.type = "button";
      edit.addEventListener("click", function () {
        selectTask(task);
        updateForm.scrollIntoView({behavior: "smooth", block: "start"});
      });
      actions.appendChild(inspect);
      actions.appendChild(edit);
      card.appendChild(actions);
      taskList.appendChild(card);
    });
  }

  async function loadTasks() {
    listStatus.textContent = "Loading tenant tasks…";
    try {
      var selected = values(filterForm, ["entity_id", "status", "assignee_ref", "priority", "limit"]);
      var query = new URLSearchParams();
      if (selected.entity_id) identifier(selected.entity_id, "Inventory entity ID");
      if (selected.assignee_ref) identifier(selected.assignee_ref, "Assignee reference");
      ["entity_id", "status", "assignee_ref", "priority"].forEach(function (name) {
        if (selected[name]) query.set(name, selected[name]);
      });
      query.set("limit", String(positiveInteger(selected.limit, "Result limit", 500)));
      var payload = await request("?" + query.toString());
      renderTasks(payload);
      listStatus.textContent = "Loaded " + String(payload.count || 0) + " tenant task records.";
      return payload;
    } catch (error) {
      clear(taskList);
      listCount.textContent = "0 tasks";
      listStatus.textContent = failureText("Loading tasks", error);
      throw error;
    }
  }

  async function refreshStatus() {
    readiness.textContent = "Checking…";
    readinessDetail.textContent = "Checking availability before enabling actions.";
    setMark("Checking", "checking");
    statusOutput.textContent = "";
    operations.disabled = true;
    try {
      var payload = await request("/status");
      show(statusOutput, payload);
      if (payload.ready === true && payload.enabled === true && payload.configured === true) {
        readiness.textContent = "Ready";
        readinessDetail.textContent = "Task controls are ready. Lightwork checks the current inventory item before each change.";
        setMark("Ready", "ready");
        operations.disabled = false;
        try { await loadTasks(); }
        catch (_error) { /* List status is independently rendered. */ }
      } else if (payload.enabled !== true) {
        readiness.textContent = "Disabled";
        readinessDetail.textContent = "Enable both AI Inventory and AI Entity Tasks before operating.";
        setMark("Disabled", "failed");
      } else if (payload.configured !== true) {
        readiness.textContent = "Setup incomplete";
        readinessDetail.textContent = "This feature is enabled, but workspace setup is incomplete.";
        setMark("Unready", "failed");
      } else {
        readiness.textContent = "Not verified";
        readinessDetail.textContent = "Server dependencies are configured, but operational readiness has not been verified.";
        setMark("Unready", "failed");
      }
    } catch (error) {
      readiness.textContent = "Unavailable";
      readinessDetail.textContent = String(error.message || error);
      setMark("Error", "failed");
    }
  }

  document.getElementById("aet-refresh-status").addEventListener("click", refreshStatus);

  filterForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    try { await loadTasks(); }
    catch (_error) { /* List status is independently rendered. */ }
  });

  document.getElementById("aet-clear-filters").addEventListener("click", async function () {
    filterForm.reset();
    field(filterForm, "limit").value = "100";
    try { await loadTasks(); }
    catch (_error) { /* List status is independently rendered. */ }
  });

  createForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var payload = buildCreatePayload(values(createForm, [
        "entity_id", "entity_revision", "kind", "title", "description",
        "assignee_ref", "collaborator_refs", "priority", "due_at", "idempotency_key"
      ]));
      var record = await run("Creating AI entity task", function () {
        return request("", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
      });
      selectTask(record);
      await loadTasks();
    } catch (error) {
      if (error.rendered !== true) {
        operationStatus.textContent = String(error.message || error);
        operationStatus.dataset.state = "failed";
      }
    }
  });

  updateForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var selected = values(updateForm, [
        "task_id", "expected_revision", "status", "priority", "assignee_action",
        "assignee_ref", "collaborator_action", "collaborator_refs", "due_action",
        "due_at", "idempotency_key"
      ]);
      var selectedTask = encodeURIComponent(identifier(selected.task_id, "Task ID"));
      var payload = buildUpdatePayload(selected);
      var record = await run("Updating AI entity task", function () {
        return request("/" + selectedTask, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
      });
      selectTask(record);
      field(updateForm, "idempotency_key").value = "";
      await loadTasks();
    } catch (error) {
      if (error.rendered !== true) {
        operationStatus.textContent = String(error.message || error);
        operationStatus.dataset.state = "failed";
      }
    }
  });

  inspectForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var record = await run("Loading current task", function () { return request("/" + taskId()); });
      selectTask(record);
    } catch (_error) { /* The governed status region contains the error. */ }
  });

  document.getElementById("aet-load-history").addEventListener("click", async function () {
    try { await run("Loading task history", function () { return request("/" + taskId() + "/history"); }); }
    catch (_error) { /* The governed status region contains the error. */ }
  });

  document.getElementById("aet-verify-integrity").addEventListener("click", async function () {
    try { await run("Verifying tenant task integrity", function () { return request("/operations/integrity"); }); }
    catch (_error) { /* The governed status region contains the error. */ }
  });

  document.getElementById("aet-retention-due").addEventListener("click", async function () {
    try {
      var limit = positiveInteger(document.getElementById("aet-retention-limit").value, "Retention limit", 1000);
      await run("Listing retention-due tasks", function () {
        return request("/operations/retention-due?limit=" + encodeURIComponent(limit));
      });
    } catch (error) {
      if (error.rendered !== true) {
        operationStatus.textContent = String(error.message || error);
        operationStatus.dataset.state = "failed";
      }
    }
  });

  document.getElementById("aet-purge-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var confirmation = form.elements.namedItem("confirmation");
    if (!confirmation.checked) {
      operationStatus.textContent = "Confirm the retention-authorized purge before continuing.";
      operationStatus.dataset.state = "failed";
      return;
    }
    try {
      var limit = positiveInteger(field(form, "limit").value, "Purge limit", 1000);
      await run("Purging retention-due tasks", function () {
        return request("/operations/purge-expired", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({limit: limit})
        });
      });
      confirmation.checked = false;
      await loadTasks();
    } catch (error) {
      if (error.rendered !== true) {
        operationStatus.textContent = String(error.message || error);
        operationStatus.dataset.state = "failed";
      }
    }
  });

  refreshStatus();
}());
