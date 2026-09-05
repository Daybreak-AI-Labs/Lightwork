(function (global) {
  "use strict";

  var MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
  var BLUEPRINT_SCHEMA = "lightwork.no-code-grc-blueprint.v1";
  var ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
  var OP = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/;
  var DIGEST = /^[0-9a-f]{64}$/;
  var ROOT_KEYS = ["schema", "studio_id", "name_ref", "description_ref", "objects", "forms", "rules", "collector_bindings", "report_views"];

  function problem(message, code) {
    var error = new Error(message);
    error.code = code || "invalid_input";
    return error;
  }

  function identifier(value, label, operation) {
    var selected = String(value || "").trim();
    var pattern = operation ? OP : ID;
    if (!pattern.test(selected)) { throw problem(label + " is invalid."); }
    return selected;
  }

  function digest(value, label, optional) {
    var selected = String(value || "").trim();
    if (optional && !selected) { return null; }
    if (!DIGEST.test(selected)) { throw problem(label + " must be a lowercase SHA-256 digest."); }
    return selected;
  }

  function integer(value, label, minimum, maximum) {
    var selected = Number(value);
    if (!Number.isSafeInteger(selected) || selected < minimum || selected > maximum) {
      throw problem(label + " is outside its bound.");
    }
    return selected;
  }

  function parseBoundedJson(value, label, maximum) {
    var text = String(value || "");
    if (!text.length || new TextEncoder().encode(text).length > maximum) {
      throw problem(label + " is empty or exceeds its byte bound.");
    }
    var parsed;
    try { parsed = JSON.parse(text); } catch (_error) { throw problem(label + " is not valid JSON."); }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw problem(label + " must be a JSON object.");
    }
    return parsed;
  }

  function opaqueList(value, label, maximum, minimum) {
    var text = String(value || "").trim();
    var items = text ? text.split(",").map(function (item) { return identifier(item.trim(), label, false); }) : [];
    if (items.length < (minimum || 0) || items.length > maximum || new Set(items).size !== items.length) {
      throw problem(label + " has an invalid count or duplicate value.");
    }
    return items;
  }

  function literal(value, label, blankIsNull) {
    var text = String(value === undefined || value === null ? "" : value).trim();
    if (!text && blankIsNull) { return null; }
    if (text === "true") { return true; }
    if (text === "false") { return false; }
    if (/^-?(?:0|[1-9][0-9]*)$/.test(text)) {
      var whole = Number(text);
      if (!Number.isSafeInteger(whole) || Math.abs(whole) > 1000000000000) { throw problem(label + " is outside its bound."); }
      return whole;
    }
    if (/^-?(?:0|[1-9][0-9]*)\.[0-9]+$/.test(text)) {
      var decimal = Number(text);
      if (!Number.isFinite(decimal) || Math.abs(decimal) > 1000000000000) { throw problem(label + " is outside its bound."); }
      return decimal;
    }
    return identifier(text, label, false);
  }

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function emptyComposerState() {
    return { objects: [], forms: [], rules: [], collector_bindings: [], report_views: [] };
  }

  function buildBlueprintFromComposer(metadata, state) {
    if (!state || !Array.isArray(state.objects) || !Array.isArray(state.forms) || !Array.isArray(state.rules) ||
        !Array.isArray(state.collector_bindings) || !Array.isArray(state.report_views)) {
      throw problem("Composer state is invalid.");
    }
    var blueprint = {
      schema: BLUEPRINT_SCHEMA,
      studio_id: identifier(metadata.studio_id, "Studio ID", false),
      name_ref: identifier(metadata.name_ref, "Blueprint name reference", false),
      description_ref: String(metadata.description_ref || "").trim() ? identifier(metadata.description_ref, "Description reference", false) : null,
      objects: copy(state.objects),
      forms: copy(state.forms),
      rules: copy(state.rules),
      collector_bindings: copy(state.collector_bindings),
      report_views: copy(state.report_views)
    };
    if (!blueprint.objects.length) { throw problem("Add at least one object before generating the blueprint."); }
    return blueprint;
  }

  function loadComposerState(value) {
    var keys = Object.keys(value || {}).sort();
    var expected = ROOT_KEYS.slice().sort();
    if (keys.length !== expected.length || keys.some(function (key, index) { return key !== expected[index]; }) || value.schema !== BLUEPRINT_SCHEMA) {
      throw problem("Advanced JSON is not the exact closed blueprint schema.");
    }
    ["objects", "forms", "rules", "collector_bindings", "report_views"].forEach(function (key) {
      if (!Array.isArray(value[key])) { throw problem("Advanced JSON contains an invalid " + key + " collection."); }
    });
    return {
      metadata: {
        studio_id: identifier(value.studio_id, "Studio ID", false),
        name_ref: identifier(value.name_ref, "Blueprint name reference", false),
        description_ref: value.description_ref === null ? "" : identifier(value.description_ref, "Description reference", false)
      },
      state: {
        objects: copy(value.objects),
        forms: copy(value.forms),
        rules: copy(value.rules),
        collector_bindings: copy(value.collector_bindings),
        report_views: copy(value.report_views)
      }
    };
  }

  function buildBuilderPayload(values) {
    var mode = values.mode === "replace" ? "replace" : "create";
    var studioId = identifier(values.studio_id, "Studio ID", false);
    var blueprint = parseBoundedJson(values.blueprint_json, "Blueprint", 1024 * 1024);
    if (blueprint.studio_id !== studioId) { throw problem("Blueprint studio_id must match the selected studio."); }
    var payload = {
      blueprint: blueprint,
      operation_id: identifier(values.operation_id, "Operation ID", true)
    };
    if (mode === "create") {
      payload.expected_latest_version = integer(values.version, "Expected latest version", 0, 2147483646);
    } else {
      payload.expected_revision = integer(values.expected_revision, "Expected revision", 1, 2147483647);
      payload.expected_integrity_sha256 = digest(values.expected_integrity_sha256, "Expected integrity", false);
    }
    return { mode: mode, studioId: studioId, version: integer(values.version, "Version", mode === "create" ? 0 : 1, 2147483647), payload: payload };
  }

  function buildLifecyclePayload(values) {
    var action = String(values.action || "");
    if (["submit", "review-accept", "review-reject", "approve", "publish"].indexOf(action) < 0) {
      throw problem("Lifecycle action is invalid.");
    }
    var payload = {
      expected_revision: integer(values.expected_revision, "Expected revision", 1, 2147483647),
      expected_integrity_sha256: digest(values.expected_integrity_sha256, "Expected integrity", false),
      operation_id: identifier(values.operation_id, "Operation ID", true)
    };
    if (action.indexOf("review-") === 0) {
      payload.accepted = action === "review-accept";
      payload.rationale_ref = identifier(values.authority_ref, "Rationale reference", false);
    }
    if (action === "approve") { payload.approval_ref = identifier(values.authority_ref, "Approval reference", false); }
    if (action === "publish") {
      payload.expected_active_version = integer(values.expected_active_version, "Expected active version", 0, 2147483647);
      payload.expected_active_blueprint_sha256 = digest(values.expected_active_blueprint_sha256, "Expected active blueprint", payload.expected_active_version === 0);
      payload.expected_pointer_revision = integer(values.expected_pointer_revision, "Expected pointer revision", 0, 2147483647);
    }
    return {
      action: action,
      studioId: identifier(values.studio_id, "Studio ID", false),
      version: integer(values.version, "Version", 1, 2147483647),
      payload: payload
    };
  }

  function buildRollbackPayload(values) {
    if (values.confirmation !== true) { throw problem("Rollback confirmation is required."); }
    return {
      studioId: identifier(values.studio_id, "Studio ID", false),
      payload: {
        target_version: integer(values.target_version, "Target version", 1, 2147483647),
        expected_target_blueprint_sha256: digest(values.expected_target_blueprint_sha256, "Target blueprint", false),
        expected_active_version: integer(values.expected_active_version, "Active version", 1, 2147483647),
        expected_active_blueprint_sha256: digest(values.expected_active_blueprint_sha256, "Active blueprint", false),
        expected_pointer_revision: integer(values.expected_pointer_revision, "Pointer revision", 1, 2147483647),
        rationale_ref: identifier(values.rationale_ref, "Rationale reference", false),
        operation_id: identifier(values.operation_id, "Operation ID", true)
      }
    };
  }

  function buildProjectionPayload(values) {
    var kind = String(values.projection_kind || "");
    if (["questionnaire", "collector", "report"].indexOf(kind) < 0) { throw problem("Projection kind is invalid."); }
    return {
      studio_id: identifier(values.studio_id, "Studio ID", false),
      projection_kind: kind,
      definition_id: identifier(values.definition_id, "Definition ID", false),
      target_id: identifier(values.target_id, "Target ID", false),
      target_revision: integer(values.target_revision, "Target revision", 1, 2147483647),
      expected_active_version: integer(values.expected_active_version, "Active version", 1, 2147483647),
      expected_blueprint_sha256: digest(values.expected_blueprint_sha256, "Blueprint digest", false)
    };
  }

  function buildProjectionApplyPayload(values) {
    var payload = buildProjectionPayload(values);
    payload.operation_id = identifier(values.operation_id, "Operation ID", true);
    return payload;
  }

  async function readBoundedJson(response) {
    var advertised = Number(response.headers.get("content-length") || "0");
    if (advertised > MAX_RESPONSE_BYTES) {
      if (response.body && typeof response.body.cancel === "function") { await response.body.cancel(); }
      throw problem("Server response exceeds its byte bound.", "oversized_response");
    }
    var text;
    if (response.body && typeof response.body.getReader === "function") {
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var total = 0;
      var parts = [];
      while (true) {
        var read = await reader.read();
        if (read.done) { break; }
        total += read.value.byteLength;
        if (total > MAX_RESPONSE_BYTES) {
          await reader.cancel();
          throw problem("Server response exceeds its byte bound.", "oversized_response");
        }
        parts.push(decoder.decode(read.value, { stream: true }));
      }
      parts.push(decoder.decode());
      text = parts.join("");
    } else {
      text = await response.text();
      if (new TextEncoder().encode(text).length > MAX_RESPONSE_BYTES) {
        throw problem("Server response exceeds its byte bound.", "oversized_response");
      }
    }
    var payload;
    try { payload = text ? JSON.parse(text) : {}; } catch (_error) { throw problem("Server returned invalid JSON.", "invalid_response"); }
    if (!response.ok) {
      var detail = payload && typeof payload.detail === "string" ? payload.detail : "The operation failed.";
      throw problem(detail, "http_" + response.status);
    }
    return payload;
  }

  global.LightworkNoCodeGRCStudioClient = {
    MAX_RESPONSE_BYTES: MAX_RESPONSE_BYTES,
    buildBuilderPayload: buildBuilderPayload,
    buildLifecyclePayload: buildLifecyclePayload,
    buildBlueprintFromComposer: buildBlueprintFromComposer,
    buildProjectionPayload: buildProjectionPayload,
    buildProjectionApplyPayload: buildProjectionApplyPayload,
    buildRollbackPayload: buildRollbackPayload,
    emptyComposerState: emptyComposerState,
    loadComposerState: loadComposerState,
    opaqueList: opaqueList,
    parseBoundedJson: parseBoundedJson,
    readBoundedJson: readBoundedJson
  };

  if (typeof document === "undefined") { return; }
  var shell = document.getElementById("grc-studio-shell");
  if (!shell) { return; }
  var apiBase = shell.dataset.apiBase;
  var operations = document.getElementById("grcs-operations");
  var statusMark = document.getElementById("grcs-status-mark");
  var readiness = document.getElementById("grcs-readiness");
  var readinessDetail = document.getElementById("grcs-readiness-detail");
  var statusOutput = document.getElementById("grcs-status-output");
  var operationStatus = document.getElementById("grcs-operation-status");
  var operationOutput = document.getElementById("grcs-operation-output");
  var listStatus = document.getElementById("grcs-list-status");
  var versionList = document.getElementById("grcs-version-list");
  var listCount = document.getElementById("grcs-list-count");
  var builderForm = document.getElementById("grcs-builder-form");
  var blueprintEditor = builderForm.elements.blueprint_json;
  var projectionApply = document.getElementById("grcs-projection-apply");
  var composerState = emptyComposerState();
  var permissions = {
    operate: shell.dataset.canOperate === "true",
    audit: shell.dataset.canAudit === "true",
    admin: shell.dataset.canAdmin === "true"
  };

  function values(form) {
    var output = {};
    new FormData(form).forEach(function (value, key) { output[key] = value; });
    form.querySelectorAll('input[type="checkbox"]').forEach(function (input) { output[input.name] = input.checked; });
    return output;
  }

  function show(label, payload) {
    operationStatus.textContent = label;
    operationOutput.textContent = JSON.stringify(payload, null, 2);
  }

  function showError(error) {
    var message = error instanceof Error ? error.message : "The operation failed.";
    operationStatus.textContent = message;
    operationOutput.textContent = JSON.stringify({ error: message, code: error && error.code ? error.code : "operation_failed" }, null, 2);
  }

  function inputValue(id) {
    var element = document.getElementById(id);
    return element ? element.value : "";
  }

  function fieldFor(objectId, fieldId) {
    var selectedObject = composerState.objects.find(function (item) { return item.object_id === objectId; });
    if (!selectedObject) { throw problem("Object ID is not present in the composer."); }
    var selectedField = selectedObject.fields.find(function (item) { return item.field_id === fieldId; });
    if (!selectedField) { throw problem("Field ID is not present on the selected object."); }
    return selectedField;
  }

  function metadata() {
    return {
      studio_id: builderForm.elements.studio_id.value,
      name_ref: builderForm.elements.name_ref.value,
      description_ref: builderForm.elements.description_ref.value
    };
  }

  function definitionCard(titleText, detailText, removeLabel, removeAction) {
    var card = document.createElement("div"); card.className = "grcs-definition";
    var title = document.createElement("strong"); title.textContent = titleText;
    var detail = document.createElement("span"); detail.textContent = detailText;
    var remove = document.createElement("button"); remove.className = "btn"; remove.type = "button";
    remove.textContent = removeLabel; remove.addEventListener("click", removeAction);
    card.appendChild(title); card.appendChild(detail); card.appendChild(remove);
    return card;
  }

  function renderCollection(id, cards, emptyText) {
    var target = document.getElementById(id); target.replaceChildren();
    if (!cards.length) {
      var empty = document.createElement("p"); empty.className = "grcs-empty"; empty.textContent = emptyText;
      target.appendChild(empty); return;
    }
    cards.forEach(function (card) { target.appendChild(card); });
  }

  function renderComposer() {
    renderCollection("grcs-object-list", composerState.objects.map(function (item, index) {
      return definitionCard(item.object_id, item.kind + " · " + item.fields.length + " fields", "Remove object", function () {
        var used = composerState.forms.concat(composerState.rules, composerState.collector_bindings, composerState.report_views)
          .some(function (definition) { return definition.object_id === item.object_id; });
        if (used) { showError(problem("Remove dependent forms, rules, bindings and views before this object.")); return; }
        composerState.objects.splice(index, 1); renderComposer();
      });
    }), "No objects configured.");

    var fieldCards = [];
    composerState.objects.forEach(function (objectDefinition) {
      objectDefinition.fields.forEach(function (item, index) {
        fieldCards.push(definitionCard(objectDefinition.object_id + "." + item.field_id, item.field_type + " · " + item.sensitivity, "Remove field", function () {
          objectDefinition.fields.splice(index, 1); renderComposer();
        }));
      });
    });
    renderCollection("grcs-field-list", fieldCards, "No fields configured.");
    renderCollection("grcs-form-list", composerState.forms.map(function (item, index) {
      return definitionCard(item.form_id, item.object_id + " · " + item.sections.length + " sections", "Remove form", function () { composerState.forms.splice(index, 1); renderComposer(); });
    }), "No forms configured.");
    renderCollection("grcs-rule-list", composerState.rules.map(function (item, index) {
      return definitionCard(item.rule_id, item.object_id + " · " + item.when.conditions.length + " conditions · " + item.then.length + " actions", "Remove rule", function () { composerState.rules.splice(index, 1); renderComposer(); });
    }), "No rules configured.");
    renderCollection("grcs-binding-list", composerState.collector_bindings.map(function (item, index) {
      return definitionCard(item.binding_id, item.collector_id + " v" + item.collector_version + " · " + item.field_mappings.length + " mappings", "Remove binding", function () { composerState.collector_bindings.splice(index, 1); renderComposer(); });
    }), "No collector bindings configured.");
    renderCollection("grcs-view-list", composerState.report_views.map(function (item, index) {
      return definitionCard(item.view_id, item.object_id + " · " + item.columns.length + " columns", "Remove view", function () { composerState.report_views.splice(index, 1); renderComposer(); });
    }), "No report views configured.");
  }

  function generateBlueprint() {
    var blueprint = buildBlueprintFromComposer(metadata(), composerState);
    blueprintEditor.value = JSON.stringify(blueprint, null, 2);
    return blueprint;
  }

  function installComposerHandlers() {
    document.getElementById("grcs-add-object").addEventListener("click", function () {
      try {
        var objectId = identifier(inputValue("grcs-object-id"), "Object ID", false);
        if (composerState.objects.some(function (item) { return item.object_id === objectId; })) { throw problem("Object ID already exists."); }
        composerState.objects.push({
          object_id: objectId,
          kind: identifier(inputValue("grcs-object-kind"), "Object kind", false),
          label_ref: identifier(inputValue("grcs-object-label"), "Object label reference", false),
          description_ref: String(inputValue("grcs-object-description") || "").trim() ? identifier(inputValue("grcs-object-description"), "Object description reference", false) : null,
          fields: []
        });
        renderComposer(); show("Object added to the visual composer.", { object_id: objectId });
      } catch (error) { showError(error); }
    });

    document.getElementById("grcs-add-field").addEventListener("click", function () {
      try {
        var objectId = identifier(inputValue("grcs-field-object"), "Object ID", false);
        var selectedObject = composerState.objects.find(function (item) { return item.object_id === objectId; });
        if (!selectedObject) { throw problem("Add the selected object before its field."); }
        var fieldId = identifier(inputValue("grcs-field-id"), "Field ID", false);
        if (selectedObject.fields.some(function (item) { return item.field_id === fieldId; })) { throw problem("Field ID already exists on this object."); }
        var fieldType = inputValue("grcs-field-type");
        var options = opaqueList(inputValue("grcs-field-options"), "Field option", 256, 0);
        if (["single_choice", "multi_choice"].indexOf(fieldType) >= 0 && !options.length) { throw problem("Choice fields require at least one option."); }
        if (["single_choice", "multi_choice"].indexOf(fieldType) < 0 && options.length) { throw problem("Only choice fields accept options."); }
        var reference = String(inputValue("grcs-field-reference") || "").trim();
        if (["record_ref", "evidence_ref"].indexOf(fieldType) >= 0 && !reference) { throw problem("Reference fields require a reference kind."); }
        if (["record_ref", "evidence_ref"].indexOf(fieldType) < 0 && reference) { throw problem("Only reference fields accept a reference kind."); }
        var defaultValue = literal(inputValue("grcs-field-default"), "Field default", true);
        if (["multi_choice", "timestamp"].indexOf(fieldType) >= 0 && defaultValue !== null) { throw problem("This field type cannot carry a scalar default."); }
        if (fieldType === "single_choice" && defaultValue !== null && options.indexOf(defaultValue) < 0) { throw problem("Choice default must be one of the configured options."); }
        if (fieldType === "digest" && defaultValue !== null) { defaultValue = digest(defaultValue, "Digest default", false); }
        selectedObject.fields.push({
          field_id: fieldId,
          label_ref: identifier(inputValue("grcs-field-label"), "Field label reference", false),
          field_type: fieldType,
          required: document.getElementById("grcs-field-required").checked,
          sensitivity: inputValue("grcs-field-sensitivity"),
          options: options,
          reference_kind: reference ? identifier(reference, "Reference kind", false) : null,
          default: defaultValue
        });
        renderComposer(); show("Field added to the visual composer.", { object_id: objectId, field_id: fieldId });
      } catch (error) { showError(error); }
    });

    document.getElementById("grcs-add-form").addEventListener("click", function () {
      try {
        var formId = identifier(inputValue("grcs-form-id"), "Form ID", false);
        var objectId = identifier(inputValue("grcs-form-object"), "Object ID", false);
        var titleRef = identifier(inputValue("grcs-form-title"), "Form title reference", false);
        var section = {
          section_id: identifier(inputValue("grcs-section-id"), "Section ID", false),
          label_ref: identifier(inputValue("grcs-section-label"), "Section label reference", false),
          field_ids: opaqueList(inputValue("grcs-section-fields"), "Section field ID", 512, 1)
        };
        section.field_ids.forEach(function (fieldId) { fieldFor(objectId, fieldId); });
        var existing = composerState.forms.find(function (item) { return item.form_id === formId; });
        if (existing) {
          if (existing.object_id !== objectId || existing.title_ref !== titleRef) { throw problem("Repeated form IDs must retain object and title authority."); }
          if (existing.sections.some(function (item) { return item.section_id === section.section_id; })) { throw problem("Section ID already exists on this form."); }
          existing.sections.push(section);
        } else {
          composerState.forms.push({ form_id: formId, object_id: objectId, title_ref: titleRef, sections: [section] });
        }
        renderComposer(); show("Form section added to the visual composer.", { form_id: formId, section_id: section.section_id });
      } catch (error) { showError(error); }
    });

    document.getElementById("grcs-add-rule").addEventListener("click", function () {
      try {
        var ruleId = identifier(inputValue("grcs-rule-id"), "Rule ID", false);
        var objectId = identifier(inputValue("grcs-rule-object"), "Object ID", false);
        var mode = inputValue("grcs-rule-mode");
        var conditionField = identifier(inputValue("grcs-condition-field"), "Condition field", false);
        fieldFor(objectId, conditionField);
        var conditionOperator = inputValue("grcs-condition-operator");
        var condition = { field_id: conditionField, operator: conditionOperator, value: literal(inputValue("grcs-condition-value"), "Condition value", conditionOperator === "is_set") };
        var actionType = inputValue("grcs-action-type");
        var targetText = String(inputValue("grcs-action-target") || "").trim();
        var target = targetText ? identifier(targetText, "Action target field", false) : null;
        if (actionType === "emit_task_ref" && target !== null) { throw problem("emit_task_ref cannot target a field."); }
        if (actionType !== "emit_task_ref") { fieldFor(objectId, target); }
        var action = { action_id: identifier(inputValue("grcs-action-id"), "Action ID", false), action_type: actionType, target_field_id: target, value: literal(inputValue("grcs-action-value"), "Action value", true) };
        var existing = composerState.rules.find(function (item) { return item.rule_id === ruleId; });
        if (existing) {
          if (existing.object_id !== objectId || existing.when.mode !== mode) { throw problem("Repeated rule IDs must retain object and match mode."); }
          if (existing.then.some(function (item) { return item.action_id === action.action_id; })) { throw problem("Action ID already exists on this rule."); }
          existing.when.conditions.push(condition); existing.then.push(action);
        } else {
          composerState.rules.push({ rule_id: ruleId, object_id: objectId, when: { mode: mode, conditions: [condition] }, then: [action] });
        }
        renderComposer(); show("Rule step added to the visual composer.", { rule_id: ruleId, action_id: action.action_id });
      } catch (error) { showError(error); }
    });

    document.getElementById("grcs-add-binding").addEventListener("click", function () {
      try {
        var bindingId = identifier(inputValue("grcs-binding-id"), "Binding ID", false);
        var objectId = identifier(inputValue("grcs-binding-object"), "Object ID", false);
        var target = identifier(inputValue("grcs-mapping-target"), "Mapping target field", false);
        fieldFor(objectId, target);
        var collectorId = identifier(inputValue("grcs-collector-id"), "Collector ID", false);
        var collectorVersion = integer(inputValue("grcs-collector-version"), "Collector version", 1, 2147483647);
        var collectorDigest = digest(inputValue("grcs-collector-digest"), "Collector integrity", false);
        var mapping = { source_path: opaqueList(inputValue("grcs-source-path"), "Source path segment", 16, 1), target_field_id: target, mode: inputValue("grcs-mapping-mode") };
        var controls = opaqueList(inputValue("grcs-control-refs"), "Control reference", 512, 1);
        var existing = composerState.collector_bindings.find(function (item) { return item.binding_id === bindingId; });
        if (existing) {
          if (existing.object_id !== objectId || existing.collector_id !== collectorId || existing.collector_version !== collectorVersion || existing.collector_integrity_sha256 !== collectorDigest) { throw problem("Repeated binding IDs must retain pinned collector authority."); }
          if (existing.field_mappings.some(function (item) { return item.target_field_id === target; })) { throw problem("Binding already maps this target field."); }
          existing.field_mappings.push(mapping);
          controls.forEach(function (control) { if (existing.control_refs.indexOf(control) < 0) { existing.control_refs.push(control); } });
        } else {
          composerState.collector_bindings.push({ binding_id: bindingId, collector_id: collectorId, collector_version: collectorVersion, collector_integrity_sha256: collectorDigest, object_id: objectId, field_mappings: [mapping], control_refs: controls });
        }
        renderComposer(); show("Collector mapping added to the visual composer.", { binding_id: bindingId, target_field_id: target });
      } catch (error) { showError(error); }
    });

    document.getElementById("grcs-add-view").addEventListener("click", function () {
      try {
        var viewId = identifier(inputValue("grcs-view-id"), "View ID", false);
        var objectId = identifier(inputValue("grcs-view-object"), "Object ID", false);
        var titleRef = identifier(inputValue("grcs-view-title"), "View title reference", false);
        var columns = opaqueList(inputValue("grcs-view-columns"), "View column", 512, 1);
        var groups = opaqueList(inputValue("grcs-view-groups"), "View group field", 16, 0);
        columns.concat(groups).forEach(function (fieldId) { fieldFor(objectId, fieldId); });
        var filterFieldText = String(inputValue("grcs-filter-field") || "").trim();
        var filter = null;
        if (filterFieldText) {
          var filterField = identifier(filterFieldText, "Filter field", false); fieldFor(objectId, filterField);
          var filterOperator = inputValue("grcs-filter-operator");
          filter = { field_id: filterField, operator: filterOperator, value: literal(inputValue("grcs-filter-value"), "Filter value", filterOperator === "is_set") };
        }
        var sortFieldText = String(inputValue("grcs-sort-field") || "").trim();
        var sortItem = null;
        if (sortFieldText) {
          var sortField = identifier(sortFieldText, "Sort field", false); fieldFor(objectId, sortField);
          sortItem = { field_id: sortField, direction: inputValue("grcs-sort-direction") };
        }
        var existing = composerState.report_views.find(function (item) { return item.view_id === viewId; });
        if (existing) {
          if (existing.object_id !== objectId || existing.title_ref !== titleRef || JSON.stringify(existing.columns) !== JSON.stringify(columns) || JSON.stringify(existing.group_by) !== JSON.stringify(groups)) { throw problem("Repeated view IDs must retain object, title, columns and grouping."); }
          if (filter) { existing.filters.push(filter); }
          if (sortItem) {
            if (existing.sort.some(function (item) { return item.field_id === sortItem.field_id; })) { throw problem("View already sorts this field."); }
            existing.sort.push(sortItem);
          }
        } else {
          composerState.report_views.push({ view_id: viewId, object_id: objectId, title_ref: titleRef, columns: columns, filters: filter ? [filter] : [], group_by: groups, sort: sortItem ? [sortItem] : [] });
        }
        renderComposer(); show("Report definition added to the visual composer.", { view_id: viewId });
      } catch (error) { showError(error); }
    });

    document.getElementById("grcs-compose-blueprint").addEventListener("click", function () {
      try { show("Closed blueprint generated from visual definitions.", generateBlueprint()); } catch (error) { showError(error); }
    });
    document.getElementById("grcs-load-blueprint").addEventListener("click", function () {
      try {
        var loaded = loadComposerState(parseBoundedJson(blueprintEditor.value, "Blueprint", 1024 * 1024));
        composerState = loaded.state;
        builderForm.elements.studio_id.value = loaded.metadata.studio_id;
        builderForm.elements.name_ref.value = loaded.metadata.name_ref;
        builderForm.elements.description_ref.value = loaded.metadata.description_ref;
        renderComposer(); show("Advanced blueprint loaded into the visual composer.", loaded.metadata);
      } catch (error) { showError(error); }
    });
    document.getElementById("grcs-reset-composer").addEventListener("click", function () {
      composerState = emptyComposerState(); blueprintEditor.value = ""; renderComposer();
      show("Visual composer reset.", { reset: true });
    });
  }

  async function request(path, options) {
    var settings = Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, options || {});
    if (settings.body && !settings.headers["Content-Type"]) { settings.headers["Content-Type"] = "application/json"; }
    return readBoundedJson(await fetch(apiBase + path, settings));
  }

  async function loadStatus() {
    statusMark.dataset.state = "checking";
    statusMark.textContent = "Checking";
    readiness.textContent = "Checking availability";
    try {
      var payload = await request("/status");
      statusOutput.textContent = JSON.stringify(payload, null, 2);
      var ready = payload.enabled === true && payload.configured === true && payload.ready === true;
      operations.disabled = !ready;
      projectionApply.disabled = !permissions.admin || payload.runtime_projection_apply_configured !== true;
      statusMark.dataset.state = ready ? "ready" : "failed";
      statusMark.textContent = ready ? "Ready" : "Unavailable";
      readiness.textContent = ready ? "Studio authority ready" : "Studio authority unavailable";
      readinessDetail.textContent = ready ? "The builder is available in this workspace. Applying changes to connected systems requires separate setup." : "Review the availability details before retrying.";
    } catch (error) {
      operations.disabled = true;
      statusMark.dataset.state = "failed";
      statusMark.textContent = "Unavailable";
      readiness.textContent = "Status failed closed";
      readinessDetail.textContent = error.message;
      statusOutput.textContent = JSON.stringify({ error: error.message }, null, 2);
      projectionApply.disabled = true;
    }
  }

  function appendValue(dl, label, value) {
    var dt = document.createElement("dt"); dt.textContent = label;
    var dd = document.createElement("dd"); dd.textContent = String(value);
    dl.appendChild(dt); dl.appendChild(dd);
  }

  function renderVersions(payload) {
    versionList.replaceChildren();
    var items = Array.isArray(payload.items) ? payload.items : [];
    items.forEach(function (item) {
      var article = document.createElement("article"); article.className = "grcs-version";
      var head = document.createElement("div"); head.className = "grcs-version-head";
      var title = document.createElement("h3"); title.textContent = item.studio_id + " · v" + item.version;
      var state = document.createElement("span"); state.className = "grcs-pill"; state.textContent = item.state;
      head.appendChild(title); head.appendChild(state); article.appendChild(head);
      var dl = document.createElement("dl");
      appendValue(dl, "Revision", item.revision);
      appendValue(dl, "Blueprint", item.blueprint_sha256);
      appendValue(dl, "Integrity", item.integrity_sha256);
      appendValue(dl, "Author", item.created_by_ref);
      article.appendChild(dl);
      var load = document.createElement("button"); load.className = "btn"; load.type = "button"; load.textContent = "Load in composer";
      load.disabled = !permissions.operate;
      load.addEventListener("click", function () {
        try {
          var loaded = loadComposerState(item.blueprint);
          composerState = loaded.state;
          builderForm.elements.studio_id.value = loaded.metadata.studio_id;
          builderForm.elements.name_ref.value = loaded.metadata.name_ref;
          builderForm.elements.description_ref.value = loaded.metadata.description_ref;
          builderForm.elements.version.value = item.version;
          builderForm.elements.mode.value = item.state === "draft" ? "replace" : "create";
          builderForm.elements.expected_revision.value = item.state === "draft" ? item.revision : "";
          builderForm.elements.expected_integrity_sha256.value = item.state === "draft" ? item.integrity_sha256 : "";
          builderForm.elements.operation_id.value = "";
          blueprintEditor.value = JSON.stringify(item.blueprint, null, 2);
          renderComposer(); show("Version loaded into the visual composer.", { studio_id: item.studio_id, version: item.version, mode: builderForm.elements.mode.value });
        } catch (error) { showError(error); }
      });
      article.appendChild(load); versionList.appendChild(article);
    });
    listCount.textContent = items.length + (items.length === 1 ? " version" : " versions");
    listStatus.textContent = "Loaded " + items.length + " tenant-scoped versions.";
  }

  document.getElementById("grcs-refresh-status").addEventListener("click", loadStatus);
  installComposerHandlers();
  renderComposer();
  document.getElementById("grcs-filter-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var selected = values(event.currentTarget);
      var query = new URLSearchParams();
      if (String(selected.studio_id || "").trim()) { query.set("studio_id", identifier(selected.studio_id, "Studio ID", false)); }
      if (selected.state) { query.set("state", selected.state); }
      query.set("limit", String(integer(selected.limit, "Limit", 1, 1000)));
      renderVersions(await request("?" + query.toString()));
    } catch (error) { listStatus.textContent = error.message; showError(error); }
  });
  document.getElementById("grcs-builder-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      if (composerState.objects.length) { generateBlueprint(); }
      var built = buildBuilderPayload(values(event.currentTarget));
      var path = "/studios/" + encodeURIComponent(built.studioId) + "/versions" + (built.mode === "replace" ? "/" + built.version : "");
      show("Draft saved.", await request(path, { method: built.mode === "replace" ? "PATCH" : "POST", body: JSON.stringify(built.payload) }));
    } catch (error) { showError(error); }
  });
  document.getElementById("grcs-lifecycle-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var built = buildLifecyclePayload(values(event.currentTarget));
      var admitted = built.action === "submit" ? permissions.operate : built.action.indexOf("review-") === 0 ? permissions.audit : permissions.admin;
      if (!admitted) { throw problem("Current principal lacks authority for this lifecycle action.", "permission_denied"); }
      var suffix = { submit: "submit", "review-accept": "review", "review-reject": "review", approve: "approve", publish: "publish" }[built.action];
      show("Lifecycle transition completed.", await request("/studios/" + encodeURIComponent(built.studioId) + "/versions/" + built.version + "/" + suffix, { method: "POST", body: JSON.stringify(built.payload) }));
    } catch (error) { showError(error); }
  });
  document.getElementById("grcs-rollback-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var built = buildRollbackPayload(values(event.currentTarget));
      show("Rollback completed.", await request("/studios/" + encodeURIComponent(built.studioId) + "/rollback", { method: "POST", body: JSON.stringify(built.payload) }));
    } catch (error) { showError(error); }
  });
  document.getElementById("grcs-inspect-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var selected = values(event.currentTarget);
      var studioId = identifier(selected.studio_id, "Studio ID", false);
      var readType = String(selected.read_type || "");
      if (["active", "version", "history"].indexOf(readType) < 0) { throw problem("Read type is invalid."); }
      if (readType === "history" && !permissions.audit) { throw problem("History requires audit authority.", "permission_denied"); }
      var path = "/studios/" + encodeURIComponent(studioId) + "/active";
      if (readType !== "active") {
        var version = integer(selected.version, "Version", 1, 2147483647);
        path = "/studios/" + encodeURIComponent(studioId) + "/versions/" + version + (readType === "history" ? "/history" : "");
      }
      show("Governed record loaded.", await request(path));
    } catch (error) { showError(error); }
  });
  document.getElementById("grcs-projection-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var selected = values(event.currentTarget);
      var apply = event.submitter && event.submitter.value === "apply";
      if (apply && !permissions.admin) { throw problem("Projection apply requires administrator authority.", "permission_denied"); }
      var payload = apply ? buildProjectionApplyPayload(selected) : buildProjectionPayload(selected);
      show(apply ? "Projection applied through server authority." : "Projection preview loaded.", await request("/operations/projections/" + (apply ? "apply" : "preview"), { method: "POST", body: JSON.stringify(payload) }));
    }
    catch (error) { showError(error); }
  });
  document.getElementById("grcs-export-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var selected = values(event.currentTarget);
      show("Authenticated export created.", await request("/operations/export", { method: "POST", body: JSON.stringify({ studio_id: identifier(selected.studio_id, "Studio ID", false), version: integer(selected.version, "Version", 1, 2147483647), operation_id: identifier(selected.operation_id, "Operation ID", true) }) }));
    } catch (error) { showError(error); }
  });
  document.getElementById("grcs-import-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    try {
      var selected = values(event.currentTarget);
      show("Authenticated export imported as a draft.", await request("/operations/import", { method: "POST", body: JSON.stringify({ envelope: parseBoundedJson(selected.envelope_json, "Export envelope", 2 * 1024 * 1024), expected_latest_version: integer(selected.expected_latest_version, "Expected latest version", 0, 2147483646), operation_id: identifier(selected.operation_id, "Operation ID", true) }) }));
    } catch (error) { showError(error); }
  });
  document.getElementById("grcs-integrity").addEventListener("click", async function () { try { show("Integrity verification completed.", await request("/operations/integrity")); } catch (error) { showError(error); } });
  document.getElementById("grcs-retention").addEventListener("click", async function () { try { var limit = integer(document.getElementById("grcs-retention-limit").value, "Limit", 1, 1000); show("Retention queue loaded.", await request("/operations/retention?limit=" + limit)); } catch (error) { showError(error); } });
  document.getElementById("grcs-retention-purge").addEventListener("click", async function () { try { var limit = integer(document.getElementById("grcs-retention-limit").value, "Limit", 1, 1000); show("Retention purge completed.", await request("/operations/retention/purge", { method: "POST", body: JSON.stringify({ limit: limit }) })); } catch (error) { showError(error); } });
  document.getElementById("grcs-backup").addEventListener("click", async function () { try { show("Recovery backup created.", await request("/operations/recovery/backups", { method: "POST" })); } catch (error) { showError(error); } });
  document.getElementById("grcs-backup-verify-form").addEventListener("submit", async function (event) { event.preventDefault(); try { var selected = values(event.currentTarget); var backupId = identifier(selected.backup_id, "Backup ID", false); show("Recovery backup verified.", await request("/operations/recovery/backups/" + encodeURIComponent(backupId))); } catch (error) { showError(error); } });

  var lifecycleForm = document.getElementById("grcs-lifecycle-form");
  var lifecycleSubmit = document.getElementById("grcs-lifecycle-submit");
  function updateLifecyclePermission() {
    var action = lifecycleForm.elements.action.value;
    lifecycleSubmit.disabled = action === "submit" ? !permissions.operate : action.indexOf("review-") === 0 ? !permissions.audit : !permissions.admin;
  }
  lifecycleForm.elements.action.addEventListener("change", updateLifecyclePermission);
  updateLifecyclePermission();

  loadStatus();
}(typeof window !== "undefined" ? window : globalThis));
