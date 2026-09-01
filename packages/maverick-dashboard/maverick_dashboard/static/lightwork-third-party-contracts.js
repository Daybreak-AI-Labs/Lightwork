"use strict";

(function (global) {
  var MAX_RESPONSE_BYTES = 64 * 1024;
  var MAX_JSON_INPUT_BYTES = 32 * 1024;
  var ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var OP_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/;
  var FIELD_TYPES = new Set([
    "boolean", "decimal", "digest", "integer", "multi_choice",
    "single_choice", "text_ref", "timestamp"
  ]);
  var SENSITIVITIES = new Set(["public", "internal", "confidential", "restricted"]);
  var FORMULA_OPERATORS = new Set([
    "add", "subtract", "multiply", "divide", "minimum", "maximum", "count"
  ]);
  var CONTRACT_LIFECYCLE_KEYS = [
    "agreement_type", "agreement_ref", "effective_at", "expires_at",
    "renewal_mode", "notice_period_days", "governing_law_ref", "parent_contract_id"
  ];
  var ENGAGEMENT_LIFECYCLE_KEYS = [
    "engagement_type", "service_category_ref", "start_at", "end_at",
    "operational_criticality", "data_access", "hosting_model",
    "linked_contract_ids", "business_owner_ref"
  ];

  function failure(message, code, status) {
    var error = new Error(message);
    error.code = code;
    error.status = status || 0;
    return error;
  }

  async function readBoundedJson(response) {
    var declared = response.headers.get("Content-Length");
    if (declared !== null && Number(declared) > MAX_RESPONSE_BYTES) {
      if (response.body && typeof response.body.cancel === "function") {
        await response.body.cancel();
      }
      throw failure("Server response exceeded the 64 KiB safety limit.", "response_too_large", response.status);
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      throw failure("Server response stream is unavailable.", "response_stream_unavailable", response.status);
    }
    var reader = response.body.getReader();
    var chunks = [];
    var total = 0;
    while (true) {
      var part = await reader.read();
      if (part.done) { break; }
      total += part.value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw failure("Server response exceeded the 64 KiB safety limit.", "response_too_large", response.status);
      }
      chunks.push(part.value);
    }
    var bytes = new Uint8Array(total);
    var offset = 0;
    chunks.forEach(function (chunk) { bytes.set(chunk, offset); offset += chunk.byteLength; });
    var responseText = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    try {
      return JSON.parse(responseText);
    } catch (_error) {
      throw failure("Server returned invalid JSON.", "response_invalid_json", response.status);
    }
  }

  async function parseResponse(response) {
    var payload = await readBoundedJson(response);
    if (!response.ok) {
      var detail = payload && typeof payload.detail === "string"
        ? payload.detail.slice(0, 500)
        : "Contract lifecycle request failed.";
      throw failure(detail, "request_failed", response.status);
    }
    return payload;
  }

  function identifier(value, label, operation) {
    var selected = String(value || "").trim();
    var pattern = operation ? OP_PATTERN : ID_PATTERN;
    if (!pattern.test(selected)) { throw failure(label + " must be a valid opaque reference.", "invalid_input"); }
    return selected;
  }

  function optionalIdentifier(value, label) {
    var selected = String(value || "").trim();
    return selected ? identifier(selected, label, false) : null;
  }

  function positiveInteger(value, label, maximum) {
    var selected = Number(value);
    if (!Number.isInteger(selected) || selected < 1 || selected > maximum) {
      throw failure(label + " must be an integer between 1 and " + maximum + ".", "invalid_input");
    }
    return selected;
  }

  function nonnegativeInteger(value, label, maximum) {
    var selected = Number(value);
    if (!Number.isInteger(selected) || selected < 0 || selected > maximum) {
      throw failure(label + " must be an integer between 0 and " + maximum + ".", "invalid_input");
    }
    return selected;
  }

  function optionalTimestamp(value, label) {
    if (value === null || value === undefined || String(value).trim() === "") { return null; }
    if (typeof value === "number" && Number.isFinite(value) && value > 0) { return value; }
    var parsed = Date.parse(String(value));
    if (!Number.isFinite(parsed) || parsed <= 0) { throw failure(label + " is invalid.", "invalid_input"); }
    return parsed / 1000;
  }

  function splitIds(value, label, maximum) {
    var raw = String(value || "").split(",").map(function (item) { return item.trim(); }).filter(Boolean);
    var unique = Array.from(new Set(raw.map(function (item) { return identifier(item, label, false); })));
    if (unique.length > maximum) { throw failure(label + " exceeds its maximum of " + maximum + ".", "invalid_input"); }
    return unique.sort();
  }

  function exactKeys(value, allowed, label) {
    if (!value || Array.isArray(value) || typeof value !== "object") {
      throw failure(label + " must be a JSON object.", "invalid_input");
    }
    Object.keys(value).forEach(function (key) {
      if (!allowed.includes(key)) { throw failure(label + " contains an unsupported field.", "invalid_input"); }
    });
    allowed.forEach(function (key) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) {
        throw failure(label + " is missing " + key + ".", "invalid_input");
      }
    });
    return value;
  }

  function choice(value, allowed, label) {
    if (!allowed.includes(value)) { throw failure(label + " is unsupported.", "invalid_input"); }
    return value;
  }

  function parseJson(value, label, expected) {
    var parsed = value;
    if (typeof value === "string") {
      if (new TextEncoder().encode(value).byteLength > MAX_JSON_INPUT_BYTES) {
        throw failure(label + " exceeds the 32 KiB input bound.", "invalid_input");
      }
      try { parsed = JSON.parse(value); }
      catch (_error) { throw failure(label + " must be valid JSON.", "invalid_input"); }
    }
    if (expected === "array" && !Array.isArray(parsed)) {
      throw failure(label + " must be a JSON array.", "invalid_input");
    }
    if (expected === "object" && (!parsed || Array.isArray(parsed) || typeof parsed !== "object")) {
      throw failure(label + " must be a JSON object.", "invalid_input");
    }
    return parsed;
  }

  function validateFieldSchema(value) {
    var fields = parseJson(value, "Custom field schema", "array");
    if (fields.length > 128) { throw failure("Custom field schema exceeds 128 fields.", "invalid_input"); }
    var seen = new Set();
    return fields.map(function (raw) {
      var field = exactKeys(raw, ["field_id", "label_ref", "field_type", "required", "sensitivity", "options"], "Custom field");
      var fieldId = identifier(field.field_id, "Custom field ID", false);
      if (seen.has(fieldId)) { throw failure("Custom field IDs must be unique.", "invalid_input"); }
      seen.add(fieldId);
      if (!FIELD_TYPES.has(field.field_type) || !SENSITIVITIES.has(field.sensitivity) || typeof field.required !== "boolean") {
        throw failure("Custom field type, sensitivity, or required flag is invalid.", "invalid_input");
      }
      if (!Array.isArray(field.options) || field.options.length > 256) {
        throw failure("Custom field options are invalid.", "invalid_input");
      }
      var options = Array.from(new Set(field.options.map(function (item) { return identifier(item, "Custom option", false); })));
      var isChoice = field.field_type === "single_choice" || field.field_type === "multi_choice";
      if ((isChoice && options.length === 0) || (!isChoice && options.length !== 0)) {
        throw failure("Custom field options do not match the selected type.", "invalid_input");
      }
      return {
        field_id: fieldId, label_ref: identifier(field.label_ref, "Custom label reference", false),
        field_type: field.field_type, required: field.required,
        sensitivity: field.sensitivity, options: options
      };
    });
  }

  function validateFieldValues(value) {
    var values = parseJson(value, "Custom field values", "object");
    var keys = Object.keys(values);
    if (keys.length > 128) { throw failure("Custom field values exceed 128 fields.", "invalid_input"); }
    var normalized = {};
    keys.sort().forEach(function (key) {
      var fieldId = identifier(key, "Custom value field ID", false);
      var selected = values[key];
      var scalar = selected === null || typeof selected === "boolean" ||
        typeof selected === "number" || typeof selected === "string";
      if (Array.isArray(selected)) {
        if (selected.length > 256) { throw failure("Custom value list is too large.", "invalid_input"); }
        selected = selected.map(function (item) { return identifier(item, "Custom value option", false); });
      } else if (!scalar || selected === null || (typeof selected === "number" && !Number.isFinite(selected))) {
        throw failure("Custom values must be bounded scalars or option lists.", "invalid_input");
      } else if (typeof selected === "string") {
        selected = identifier(selected, "Custom value reference", false);
      }
      normalized[fieldId] = selected;
    });
    return normalized;
  }

  function validateFormulas(value) {
    var formulas = parseJson(value, "Formula definitions", "array");
    if (formulas.length > 64) { throw failure("Formula definitions exceed 64 formulas.", "invalid_input"); }
    var seen = new Set();
    return formulas.map(function (raw) {
      var formula = exactKeys(raw, ["formula_id", "operator", "operands", "round_digits"], "Formula");
      var formulaId = identifier(formula.formula_id, "Formula ID", false);
      if (seen.has(formulaId)) { throw failure("Formula IDs must be unique.", "invalid_input"); }
      seen.add(formulaId);
      if (!FORMULA_OPERATORS.has(formula.operator) || !Array.isArray(formula.operands) ||
          formula.operands.length < 1 || formula.operands.length > 16) {
        throw failure("Formula operator or operands are invalid.", "invalid_input");
      }
      var operands = formula.operands.map(function (rawOperand) {
        var operand = exactKeys(rawOperand, ["source", "field_id", "value"], "Formula operand");
        if (operand.source === "field") {
          if (operand.value !== null) { throw failure("Field operands cannot contain a literal.", "invalid_input"); }
          return { source: "field", field_id: identifier(operand.field_id, "Formula field ID", false), value: null };
        }
        if (operand.source === "literal" && operand.field_id === null &&
            typeof operand.value === "number" && Number.isFinite(operand.value)) {
          return { source: "literal", field_id: null, value: operand.value };
        }
        throw failure("Formula operand is invalid.", "invalid_input");
      });
      return {
        formula_id: formulaId, operator: formula.operator, operands: operands,
        round_digits: nonnegativeInteger(formula.round_digits, "Formula round digits", 8)
      };
    });
  }

  function validateLifecycle(kind, value) {
    var lifecycle = parseJson(value, "Lifecycle", "object");
    if (kind === "contract") {
      exactKeys(lifecycle, CONTRACT_LIFECYCLE_KEYS, "Lifecycle");
      return {
        agreement_type: choice(lifecycle.agreement_type, [
          "data_processing", "license", "master_services", "nda", "other",
          "statement_of_work", "subscription"
        ], "Agreement type"),
        agreement_ref: identifier(lifecycle.agreement_ref, "Agreement reference", false),
        effective_at: optionalTimestamp(lifecycle.effective_at, "Effective time"),
        expires_at: optionalTimestamp(lifecycle.expires_at, "Expiry time"),
        renewal_mode: choice(lifecycle.renewal_mode, ["none", "manual", "automatic"], "Renewal mode"),
        notice_period_days: nonnegativeInteger(lifecycle.notice_period_days, "Notice period", 3650),
        governing_law_ref: optionalIdentifier(lifecycle.governing_law_ref, "Governing-law reference"),
        parent_contract_id: optionalIdentifier(lifecycle.parent_contract_id, "Parent Contract ID")
      };
    }
    exactKeys(lifecycle, ENGAGEMENT_LIFECYCLE_KEYS, "Lifecycle");
    return {
      engagement_type: choice(lifecycle.engagement_type, [
        "consulting", "data_processing", "other", "outsourcing", "service", "software"
      ], "Engagement type"),
      service_category_ref: identifier(lifecycle.service_category_ref, "Service category reference", false),
      start_at: optionalTimestamp(lifecycle.start_at, "Start time"),
      end_at: optionalTimestamp(lifecycle.end_at, "End time"),
      operational_criticality: choice(lifecycle.operational_criticality, ["low", "medium", "high", "critical"], "Operational criticality"),
      data_access: choice(lifecycle.data_access, ["none", "metadata", "confidential", "restricted"], "Data access"),
      hosting_model: choice(lifecycle.hosting_model, ["none", "vendor_cloud", "customer_cloud", "on_premises", "hybrid"], "Hosting model"),
      linked_contract_ids: Array.isArray(lifecycle.linked_contract_ids)
        ? splitIds(lifecycle.linked_contract_ids.join(","), "Linked Contract ID", 128)
        : splitIds(lifecycle.linked_contract_ids, "Linked Contract ID", 128),
      business_owner_ref: identifier(lifecycle.business_owner_ref, "Business owner reference", false)
    };
  }

  function buildCreatePayload(values) {
    var kind = values.record_kind === "engagement" ? "engagement" : "contract";
    var payload = {
      vendor_id: identifier(values.vendor_id, "Vendor ID", false),
      vendor_revision: positiveInteger(values.vendor_revision, "Vendor revision", 2147483647),
      title_ref: identifier(values.title_ref, "Title reference", false),
      owner_ref: identifier(values.owner_ref, "Owner reference", false),
      status: kind === "contract"
        ? choice(values.contract_status, ["draft", "active"], "Contract status")
        : choice(values.engagement_status, ["planned", "active"], "Engagement status"),
      lifecycle: validateLifecycle(kind, kind === "contract" ? {
        agreement_type: values.agreement_type,
        agreement_ref: identifier(values.agreement_ref, "Agreement reference", false),
        effective_at: optionalTimestamp(values.effective_at, "Effective time"),
        expires_at: optionalTimestamp(values.expires_at, "Expiry time"),
        renewal_mode: values.renewal_mode,
        notice_period_days: nonnegativeInteger(values.notice_period_days, "Notice period", 3650),
        governing_law_ref: optionalIdentifier(values.governing_law_ref, "Governing-law reference"),
        parent_contract_id: optionalIdentifier(values.parent_contract_id, "Parent Contract ID")
      } : {
        engagement_type: values.engagement_type,
        service_category_ref: identifier(values.service_category_ref, "Service category reference", false),
        start_at: optionalTimestamp(values.start_at, "Start time"),
        end_at: optionalTimestamp(values.end_at, "End time"),
        operational_criticality: values.operational_criticality,
        data_access: values.data_access,
        hosting_model: values.hosting_model,
        linked_contract_ids: splitIds(values.linked_contract_ids, "Linked Contract ID", 128),
        business_owner_ref: identifier(values.business_owner_ref, "Business owner reference", false)
      }),
      field_schema: validateFieldSchema(values.field_schema_json),
      field_values: validateFieldValues(values.field_values_json),
      formulas: validateFormulas(values.formulas_json),
      idempotency_key: identifier(values.idempotency_key, "Idempotency key", true)
    };
    var recordId = optionalIdentifier(values.record_id, "Record ID");
    if (recordId !== null) { payload.record_id = recordId; }
    return { kind: kind, payload: payload };
  }

  function buildUpdatePayload(values) {
    var kind = values.record_kind === "engagement" ? "engagement" : "contract";
    var payload = {
      expected_revision: positiveInteger(values.expected_revision, "Expected revision", 2147483647),
      idempotency_key: identifier(values.idempotency_key, "Idempotency key", true)
    };
    if (values.include_title_ref === true) { payload.title_ref = identifier(values.update_title_ref, "Title reference", false); }
    if (values.include_owner_ref === true) { payload.owner_ref = identifier(values.update_owner_ref, "Owner reference", false); }
    if (values.include_status === true) { payload.status = identifier(values.update_status, "Lifecycle status", false); }
    if (values.include_lifecycle === true) { payload.lifecycle = validateLifecycle(kind, values.lifecycle_json); }
    if (values.include_field_schema === true) { payload.field_schema = validateFieldSchema(values.update_field_schema_json); }
    if (values.include_field_values === true) { payload.field_values = validateFieldValues(values.update_field_values_json); }
    if (values.include_formulas === true) { payload.formulas = validateFormulas(values.update_formulas_json); }
    if (Object.keys(payload).length === 2) { throw failure("Select at least one field to update.", "invalid_input"); }
    return { kind: kind, payload: payload };
  }

  global.LightworkThirdPartyContractsClient = {
    MAX_RESPONSE_BYTES: MAX_RESPONSE_BYTES,
    buildCreatePayload: buildCreatePayload,
    buildUpdatePayload: buildUpdatePayload,
    parseResponse: parseResponse,
    readBoundedJson: readBoundedJson,
    splitIds: splitIds,
    validateFieldSchema: validateFieldSchema,
    validateFieldValues: validateFieldValues,
    validateFormulas: validateFormulas,
    validateLifecycle: validateLifecycle
  };

  if (typeof document === "undefined") { return; }
  var shell = document.getElementById("third-party-contracts-shell");
  if (!shell) { return; }
  var apiBase = shell.dataset.apiBase;
  var operations = document.getElementById("tpc-operations");
  var readiness = document.getElementById("tpc-readiness");
  var readinessDetail = document.getElementById("tpc-readiness-detail");
  var statusMark = document.getElementById("tpc-status-mark");
  var statusOutput = document.getElementById("tpc-status-output");
  var operationStatus = document.getElementById("tpc-operation-status");
  var operationOutput = document.getElementById("tpc-operation-output");
  var recordList = document.getElementById("tpc-record-list");
  var createForm = document.getElementById("tpc-create-form");
  var updateForm = document.getElementById("tpc-update-form");
  var inspectForm = document.getElementById("tpc-inspect-form");

  function field(form, name) {
    var selected = form.elements.namedItem(name);
    if (!selected) { throw failure("Required form control is unavailable.", "ui_contract"); }
    return selected;
  }

  function values(form, names) {
    var result = {};
    names.forEach(function (name) {
      var selected = field(form, name);
      result[name] = selected.type === "checkbox" ? selected.checked : selected.value;
    });
    return result;
  }

  function renderJson(element, value) {
    element.textContent = JSON.stringify(value, null, 2).slice(0, MAX_RESPONSE_BYTES);
  }

  async function request(path, options) {
    var response = await fetch(apiBase + path, Object.assign({
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: { "Accept": "application/json" }
    }, options || {}));
    return parseResponse(response);
  }

  async function run(label, callback) {
    operationStatus.textContent = label + "…";
    operationStatus.dataset.state = "checking";
    try {
      var result = await callback();
      operationStatus.textContent = label + " completed.";
      operationStatus.dataset.state = "ready";
      renderJson(operationOutput, result);
      return result;
    } catch (error) {
      operationStatus.textContent = String(error.message || "Operation failed.").slice(0, 500);
      operationStatus.dataset.state = "failed";
      operationOutput.textContent = "";
      error.rendered = true;
      throw error;
    }
  }

  function addDefinition(list, term, value) {
    var dt = document.createElement("dt");
    var dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = String(value === null || value === undefined ? "—" : value).slice(0, 300);
    list.appendChild(dt);
    list.appendChild(dd);
  }

  function selectRecord(record) {
    [inspectForm, updateForm, document.getElementById("tpc-archive-form"), document.getElementById("tpc-delete-form")].forEach(function (form) {
      field(form, "record_id").value = String(record.record_id || "");
      if (form !== inspectForm && form !== updateForm) { field(form, "expected_revision").value = String(record.revision || ""); }
    });
    field(updateForm, "record_kind").value = record.record_kind;
    field(updateForm, "expected_revision").value = String(record.revision || "");
    operationStatus.textContent = "Selected " + String(record.record_kind) + " " + String(record.record_id) + ".";
    renderJson(operationOutput, record);
  }

  function renderRecords(records) {
    recordList.replaceChildren();
    if (!records.length) {
      var empty = document.createElement("p");
      empty.className = "tpc-empty";
      empty.textContent = "No Contract or Engagement matched the current tenant filters.";
      recordList.appendChild(empty);
      return;
    }
    records.forEach(function (record) {
      var article = document.createElement("article");
      article.className = "tpc-record";
      var head = document.createElement("div");
      head.className = "tpc-record__head";
      var title = document.createElement("h3");
      title.textContent = String(record.title_ref || record.record_id || "Record").slice(0, 300);
      var pill = document.createElement("span");
      pill.className = "tpc-pill";
      pill.textContent = String(record.record_kind || "record");
      head.appendChild(title); head.appendChild(pill); article.appendChild(head);
      var details = document.createElement("dl");
      addDefinition(details, "Record", record.record_id);
      addDefinition(details, "Vendor", record.vendor_id);
      addDefinition(details, "Vendor type", record.vendor_type);
      addDefinition(details, "Status", record.status);
      addDefinition(details, "Owner", record.owner_ref);
      addDefinition(details, "Revision", record.revision);
      addDefinition(details, "Redacted", record.redaction && record.redaction.applied === true ? "yes" : "no");
      article.appendChild(details);
      var button = document.createElement("button");
      button.className = "btn";
      button.type = "button";
      button.textContent = "Select and inspect";
      button.addEventListener("click", function () { selectRecord(record); });
      article.appendChild(button);
      recordList.appendChild(article);
    });
  }

  function filterQuery() {
    var selected = values(document.getElementById("tpc-filter-form"), [
      "record_kind", "vendor_id", "status", "owner_ref", "limit", "include_sensitive_fields"
    ]);
    var params = new URLSearchParams();
    ["record_kind", "vendor_id", "status", "owner_ref"].forEach(function (key) {
      if (String(selected[key]).trim()) { params.set(key, String(selected[key]).trim()); }
    });
    params.set("limit", String(positiveInteger(selected.limit, "Maximum results", 1000)));
    if (selected.include_sensitive_fields === true) { params.set("include_sensitive_fields", "true"); }
    return params.toString();
  }

  async function loadRecords() {
    var page = await request("?" + filterQuery());
    var records = Array.isArray(page.items) ? page.items : [];
    document.getElementById("tpc-list-count").textContent = records.length + (records.length === 1 ? " record" : " records");
    document.getElementById("tpc-list-status").textContent = "Loaded " + records.length + " tenant-scoped records.";
    renderRecords(records);
    return page;
  }

  async function refreshStatus() {
    operations.disabled = true;
    readiness.textContent = "Checking…";
    statusMark.textContent = "Checking";
    statusMark.dataset.state = "checking";
    try {
      var status = await request("/status");
      renderJson(statusOutput, status);
      var ready = status && status.enabled === true && status.configured === true && status.ready === true;
      if (!ready) {
        readiness.textContent = "Unavailable";
        statusMark.textContent = "Closed";
        statusMark.dataset.state = "failed";
        readinessDetail.textContent = "Controls remain closed until the exact tenant composition reports enabled, configured, and ready.";
        return;
      }
      readiness.textContent = "Ready";
      statusMark.textContent = "Ready";
      statusMark.dataset.state = "ready";
      readinessDetail.textContent = "Tenant composition is ready. Role-specific controls remain independently enforced.";
      operations.disabled = false;
      await loadRecords();
    } catch (error) {
      readiness.textContent = "Unavailable";
      statusMark.textContent = "Failed";
      statusMark.dataset.state = "failed";
      readinessDetail.textContent = String(error.message || "Readiness check failed.").slice(0, 500);
      operations.disabled = true;
    }
  }

  function switchKind() {
    var engagement = field(createForm, "record_kind").value === "engagement";
    var contractFields = document.getElementById("tpc-contract-fields");
    var engagementFields = document.getElementById("tpc-engagement-fields");
    contractFields.hidden = engagement;
    contractFields.disabled = engagement;
    engagementFields.hidden = !engagement;
    engagementFields.disabled = !engagement;
  }

  field(createForm, "record_kind").addEventListener("change", switchKind);
  document.getElementById("tpc-refresh-status").addEventListener("click", refreshStatus);
  document.getElementById("tpc-filter-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try { await run("Loading Contract and Engagement register", loadRecords); }
    catch (_error) { /* governed status already rendered */ }
  });
  document.getElementById("tpc-clear-filters").addEventListener("click", function () {
    document.getElementById("tpc-filter-form").reset();
    loadRecords().catch(function () {});
  });

  createForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var built = buildCreatePayload(values(createForm, [
        "record_kind", "record_id", "vendor_id", "vendor_revision", "title_ref", "owner_ref",
        "contract_status", "agreement_type", "agreement_ref", "effective_at", "expires_at",
        "renewal_mode", "notice_period_days", "governing_law_ref", "parent_contract_id",
        "engagement_status", "engagement_type", "service_category_ref", "start_at", "end_at",
        "operational_criticality", "data_access", "hosting_model", "linked_contract_ids",
        "business_owner_ref", "field_schema_json", "field_values_json", "formulas_json", "idempotency_key"
      ]));
      var created = await run("Creating " + built.kind, function () {
        return request("/" + (built.kind === "contract" ? "contracts" : "engagements"), {
          method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify(built.payload)
        });
      });
      selectRecord(created);
      field(createForm, "idempotency_key").value = "";
      await loadRecords();
    } catch (error) {
      if (error.rendered !== true) { operationStatus.textContent = String(error.message || error).slice(0, 500); operationStatus.dataset.state = "failed"; }
    }
  });

  updateForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var selected = values(updateForm, [
        "record_id", "record_kind", "expected_revision", "idempotency_key",
        "include_title_ref", "update_title_ref", "include_owner_ref", "update_owner_ref",
        "include_status", "update_status", "include_lifecycle", "lifecycle_json",
        "include_field_schema", "update_field_schema_json", "include_field_values",
        "update_field_values_json", "include_formulas", "update_formulas_json"
      ]);
      var recordId = encodeURIComponent(identifier(selected.record_id, "Record ID", false));
      var built = buildUpdatePayload(selected);
      var updated = await run("Updating " + built.kind, function () {
        return request("/" + (built.kind === "contract" ? "contracts/" : "engagements/") + recordId, {
          method: "PATCH", headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify(built.payload)
        });
      });
      selectRecord(updated);
      field(updateForm, "idempotency_key").value = "";
      await loadRecords();
    } catch (error) {
      if (error.rendered !== true) { operationStatus.textContent = String(error.message || error).slice(0, 500); operationStatus.dataset.state = "failed"; }
    }
  });

  function inspectRecordId() { return encodeURIComponent(identifier(field(inspectForm, "record_id").value, "Record ID", false)); }
  function sensitiveQuery() { return field(inspectForm, "include_sensitive_fields").checked ? "?include_sensitive_fields=true" : ""; }
  inspectForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    try { selectRecord(await run("Loading exact record", function () { return request("/" + inspectRecordId() + sensitiveQuery()); })); }
    catch (_error) { /* governed status already rendered */ }
  });
  document.getElementById("tpc-load-history").addEventListener("click", async function () {
    try { await run("Loading immutable history", function () { return request("/" + inspectRecordId() + "/history" + sensitiveQuery()); }); }
    catch (_error) { /* governed status already rendered */ }
  });
  document.getElementById("tpc-load-tombstone").addEventListener("click", async function () {
    try { await run("Loading irreversible tombstone", function () { return request("/" + inspectRecordId() + "/tombstone"); }); }
    catch (_error) { /* governed status already rendered */ }
  });

  function bindRevisionOperation(formId, label, method, suffix, confirmationRequired) {
    document.getElementById(formId).addEventListener("submit", async function (event) {
      event.preventDefault();
      var form = event.currentTarget;
      if (confirmationRequired && !field(form, "confirmation").checked) {
        operationStatus.textContent = "Confirm this irreversible or retention-sensitive action first.";
        operationStatus.dataset.state = "failed";
        return;
      }
      try {
        var recordId = encodeURIComponent(identifier(field(form, "record_id").value, "Record ID", false));
        var payload = {
          expected_revision: positiveInteger(field(form, "expected_revision").value, "Expected revision", 2147483647),
          idempotency_key: identifier(field(form, "idempotency_key").value, "Idempotency key", true)
        };
        var result = await run(label, function () {
          return request("/" + recordId + suffix, {
            method: method, headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify(payload)
          });
        });
        if (result && result.record_kind) { selectRecord(result); }
        field(form, "idempotency_key").value = "";
        if (confirmationRequired) { field(form, "confirmation").checked = false; }
        await loadRecords();
      } catch (error) {
        if (error.rendered !== true) { operationStatus.textContent = String(error.message || error).slice(0, 500); operationStatus.dataset.state = "failed"; }
      }
    });
  }
  bindRevisionOperation("tpc-archive-form", "Archiving record", "POST", "/archive", true);
  bindRevisionOperation("tpc-delete-form", "Deleting archived record", "DELETE", "", true);

  document.getElementById("tpc-verify-integrity").addEventListener("click", async function () {
    try { await run("Verifying Contract lifecycle integrity", function () { return request("/operations/integrity"); }); }
    catch (_error) { /* governed status already rendered */ }
  });
  document.getElementById("tpc-retention-due").addEventListener("click", async function () {
    try {
      var limit = positiveInteger(document.getElementById("tpc-retention-limit").value, "Retention limit", 1000);
      await run("Loading retention-due records", function () { return request("/operations/retention-due?limit=" + encodeURIComponent(limit)); });
    } catch (error) {
      if (error.rendered !== true) { operationStatus.textContent = String(error.message || error).slice(0, 500); operationStatus.dataset.state = "failed"; }
    }
  });
  document.getElementById("tpc-recovery-preflight").addEventListener("click", async function () {
    try {
      await run("Running recovery preflight", async function () {
        var status = await request("/status");
        var integrity = await request("/operations/integrity");
        return {
          schema: "lightwork.third-party-contract-recovery-preflight.v1",
          ready: status.ready === true && integrity.verified === true,
          integrity: integrity,
          browser_supplied_recovery_path_accepted: false,
          next_step: "Run the server-configured recovery backup job with global administrator authority."
        };
      });
    } catch (_error) { /* governed status already rendered */ }
  });
  document.getElementById("tpc-purge-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    if (!field(form, "confirmation").checked) {
      operationStatus.textContent = "Confirm the retention-authorized purge before continuing.";
      operationStatus.dataset.state = "failed";
      return;
    }
    try {
      var limit = positiveInteger(field(form, "limit").value, "Purge limit", 1000);
      await run("Purging retention-due records", function () {
        return request("/operations/purge-expired", {
          method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify({ limit: limit })
        });
      });
      field(form, "confirmation").checked = false;
      await loadRecords();
    } catch (error) {
      if (error.rendered !== true) { operationStatus.textContent = String(error.message || error).slice(0, 500); operationStatus.dataset.state = "failed"; }
    }
  });

  switchKind();
  refreshStatus();
}(typeof window !== "undefined" ? window : this));
