(function thirdPartyCollaborationPortal(global) {
  "use strict";

  const CONTRACT_SCHEMA = "lightwork.third-party-collaboration-contract.v1";
  const RECEIPT_SCHEMA = "lightwork.third-party-collaboration-receipt.v1";
  const TOKEN_RE = /^tpc1\.[A-Za-z0-9_-]{43}$/;
  const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  const SHA256_RE = /^[0-9a-f]{64}$/;
  const PORTAL_PATH_RE = /^\/api\/v1\/external\/third-party-collaboration\/[A-Za-z0-9][A-Za-z0-9_-]{15,127}\/portal$/;
  const MAX_QUESTIONS = 1000;
  const MAX_ARTIFACTS = 100;
  const MAX_ARTIFACT_BYTES = 100 * 1024 * 1024;
  const RESPONSE_TYPES = new Set([
    "boolean",
    "single_choice",
    "multi_choice",
    "integer",
    "text_ref",
  ]);
  const ARTIFACT_KINDS = new Set([
    "audit_report",
    "certification",
    "policy",
    "questionnaire",
    "test_report",
    "other_evidence",
  ]);
  const LIMITATION_LABELS = Object.freeze({
    respondent_attestations_are_unverified:
      "Respondent attestations are stored as unverified evidence.",
    artifact_manifests_only_no_upload_bytes:
      "Only digest manifests are accepted; this surface never receives document bytes.",
    no_network_or_email_delivery:
      "Invitation network transport and email delivery are outside this portal contract.",
    no_model_inference:
      "No model infers, completes, or scores respondent answers.",
  });
  const CONTRACT_KEYS = new Set([
    "schema",
    "invitation_id",
    "vendor_ref",
    "assessment_id",
    "notice_ref",
    "notice_sha256",
    "artifact_policy",
    "questions",
    "required_question_ids",
    "answered_question_ids",
    "draft_revision",
    "draft_artifacts",
    "expires_at",
    "limitations",
  ]);
  const QUESTION_KEYS = new Set([
    "question_id",
    "domain",
    "prompt_ref",
    "response_type",
    "required",
    "options",
  ]);
  const POLICY_KEYS = new Set([
    "allowed_kinds",
    "maximum_artifacts",
    "maximum_total_bytes",
  ]);
  const ARTIFACT_KEYS = new Set([
    "artifact_id",
    "kind",
    "content_sha256",
    "size_bytes",
    "media_type_ref",
  ]);
  const DRAFT_RECEIPT_KEYS = new Set([
    "invitation_id",
    "revision",
    "draft_sha256",
    "active_question_ids",
    "required_question_ids",
    "artifact_count",
    "idempotent_replay",
  ]);
  const FINAL_RECEIPT_KEYS = new Set([
    "schema",
    "invitation_id",
    "assessment_id",
    "assessment_revision",
    "assessment_integrity_sha256",
    "question_ids",
    "attestation_sha256",
    "evidence_status",
    "idempotent_replay",
  ]);

  class PortalContractError extends Error {
    constructor(message) {
      super(message);
      this.name = "PortalContractError";
    }
  }

  class PortalRequestError extends Error {
    constructor(status) {
      super("portal request failed");
      this.name = "PortalRequestError";
      this.status = status;
    }
  }

  function fail(message) {
    throw new PortalContractError(message);
  }

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function closedRecord(value, keys, label) {
    if (!isRecord(value)) fail(`${label} must be an object`);
    const actual = Object.keys(value);
    if (actual.length !== keys.size || actual.some((key) => !keys.has(key))) {
      fail(`${label} does not match the closed contract`);
    }
    for (const key of keys) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) {
        fail(`${label} is missing a required field`);
      }
    }
    return value;
  }

  function integer(value, minimum, maximum, label) {
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
      fail(`${label} is outside its bound`);
    }
    return value;
  }

  function finiteNumber(value, minimum, maximum, label) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
      fail(`${label} is outside its bound`);
    }
    return value;
  }

  function identifier(value, label) {
    if (typeof value !== "string" || !ID_RE.test(value)) {
      fail(`${label} is invalid`);
    }
    return value;
  }

  function digest(value, label) {
    if (typeof value !== "string" || !SHA256_RE.test(value)) {
      fail(`${label} is invalid`);
    }
    return value;
  }

  function boolean(value, label) {
    if (typeof value !== "boolean") fail(`${label} must be boolean`);
    return value;
  }

  function closedIdentifierList(value, label, maximum, allowed) {
    if (!Array.isArray(value) || value.length > maximum) {
      fail(`${label} exceeds its bound`);
    }
    const result = value.map((item) => identifier(item, label));
    if (new Set(result).size !== result.length) fail(`${label} contains duplicates`);
    if (allowed && result.some((item) => !allowed.has(item))) {
      fail(`${label} contains an unsupported value`);
    }
    return result;
  }

  function validateArtifactPolicy(value) {
    const item = closedRecord(value, POLICY_KEYS, "artifact policy");
    const kinds = closedIdentifierList(
      item.allowed_kinds,
      "allowed artifact kinds",
      ARTIFACT_KINDS.size,
      ARTIFACT_KINDS,
    );
    const maximumArtifacts = integer(
      item.maximum_artifacts,
      0,
      MAX_ARTIFACTS,
      "maximum artifacts",
    );
    const maximumTotalBytes = integer(
      item.maximum_total_bytes,
      0,
      MAX_ARTIFACT_BYTES,
      "maximum artifact bytes",
    );
    if (
      (maximumArtifacts === 0 && (kinds.length !== 0 || maximumTotalBytes !== 0)) ||
      (maximumArtifacts > 0 && (kinds.length === 0 || maximumTotalBytes === 0))
    ) {
      fail("artifact policy bounds are inconsistent");
    }
    return {
      allowed_kinds: kinds,
      maximum_artifacts: maximumArtifacts,
      maximum_total_bytes: maximumTotalBytes,
    };
  }

  function validateArtifact(value, policy, label) {
    const item = closedRecord(value, ARTIFACT_KEYS, label);
    const kind = identifier(item.kind, `${label} kind`);
    if (!policy.allowed_kinds.includes(kind)) fail(`${label} kind is not allowed`);
    return {
      artifact_id: identifier(item.artifact_id, `${label} ID`),
      kind,
      content_sha256: digest(item.content_sha256, `${label} content digest`),
      size_bytes: integer(item.size_bytes, 1, MAX_ARTIFACT_BYTES, `${label} size`),
      media_type_ref: identifier(item.media_type_ref, `${label} media type reference`),
    };
  }

  function validateQuestion(value) {
    const item = closedRecord(value, QUESTION_KEYS, "question");
    const responseType = item.response_type;
    if (typeof responseType !== "string" || !RESPONSE_TYPES.has(responseType)) {
      fail("question response type is unsupported");
    }
    const options = closedIdentifierList(item.options, "question options", 1000);
    if (
      (["single_choice", "multi_choice"].includes(responseType) && options.length === 0) ||
      (!["single_choice", "multi_choice"].includes(responseType) && options.length !== 0)
    ) {
      fail("question options do not match its response type");
    }
    return {
      question_id: identifier(item.question_id, "question ID"),
      domain: identifier(item.domain, "question domain"),
      prompt_ref: identifier(item.prompt_ref, "question prompt reference"),
      response_type: responseType,
      required: boolean(item.required, "question required flag"),
      options,
    };
  }

  function validateContract(value) {
    const item = closedRecord(value, CONTRACT_KEYS, "invitation contract");
    if (item.schema !== CONTRACT_SCHEMA) fail("invitation contract schema is unsupported");
    const policy = validateArtifactPolicy(item.artifact_policy);
    if (!Array.isArray(item.questions) || item.questions.length > MAX_QUESTIONS) {
      fail("questions exceed their bound");
    }
    const questions = item.questions.map(validateQuestion);
    const questionIds = questions.map((question) => question.question_id);
    const questionSet = new Set(questionIds);
    if (questionSet.size !== questionIds.length) fail("question IDs contain duplicates");
    const required = closedIdentifierList(
      item.required_question_ids,
      "required question IDs",
      MAX_QUESTIONS,
    );
    const answered = closedIdentifierList(
      item.answered_question_ids,
      "answered question IDs",
      MAX_QUESTIONS,
    );
    if (required.some((questionId) => !questionSet.has(questionId))) {
      fail("required questions are outside the visible contract");
    }
    if (answered.some((questionId) => !questionSet.has(questionId))) {
      fail("answered questions are outside the visible contract");
    }
    if (!Array.isArray(item.draft_artifacts) || item.draft_artifacts.length > policy.maximum_artifacts) {
      fail("draft artifact manifests exceed the invitation policy");
    }
    const draftArtifacts = item.draft_artifacts.map((artifact) =>
      validateArtifact(artifact, policy, "draft artifact manifest"),
    );
    const artifactIds = draftArtifacts.map((artifact) => artifact.artifact_id);
    if (new Set(artifactIds).size !== artifactIds.length) {
      fail("draft artifact IDs contain duplicates");
    }
    const totalBytes = draftArtifacts.reduce((total, artifact) => total + artifact.size_bytes, 0);
    if (totalBytes > policy.maximum_total_bytes) {
      fail("draft artifact manifests exceed the byte policy");
    }
    if (!Array.isArray(item.limitations) || item.limitations.length !== 4) {
      fail("contract limitations are incomplete");
    }
    const limitations = closedIdentifierList(item.limitations, "contract limitations", 4);
    if (
      limitations.some((limitation) =>
        !Object.prototype.hasOwnProperty.call(LIMITATION_LABELS, limitation),
      ) ||
      new Set(limitations).size !== Object.keys(LIMITATION_LABELS).length
    ) {
      fail("contract limitations are unsupported or incomplete");
    }
    return {
      schema: CONTRACT_SCHEMA,
      invitation_id: identifier(item.invitation_id, "invitation ID"),
      vendor_ref: identifier(item.vendor_ref, "respondent reference"),
      assessment_id: identifier(item.assessment_id, "assessment ID"),
      notice_ref: identifier(item.notice_ref, "notice reference"),
      notice_sha256: digest(item.notice_sha256, "notice digest"),
      artifact_policy: policy,
      questions,
      required_question_ids: required,
      answered_question_ids: answered,
      draft_revision: integer(item.draft_revision, 0, 1000000, "draft revision"),
      draft_artifacts: draftArtifacts,
      expires_at: finiteNumber(item.expires_at, 1, 100000000000, "invitation expiry"),
      limitations,
    };
  }

  function validateDraftReceipt(value) {
    const item = closedRecord(value, DRAFT_RECEIPT_KEYS, "draft receipt");
    const active = closedIdentifierList(
      item.active_question_ids,
      "active question IDs",
      MAX_QUESTIONS,
    );
    const required = closedIdentifierList(
      item.required_question_ids,
      "required question IDs",
      MAX_QUESTIONS,
    );
    if (required.some((questionId) => !new Set(active).has(questionId))) {
      fail("draft receipt required questions are not active");
    }
    return {
      invitation_id: identifier(item.invitation_id, "draft invitation ID"),
      revision: integer(item.revision, 1, 1000000, "draft revision"),
      draft_sha256: digest(item.draft_sha256, "draft digest"),
      active_question_ids: active,
      required_question_ids: required,
      artifact_count: integer(item.artifact_count, 0, MAX_ARTIFACTS, "artifact count"),
      idempotent_replay: boolean(item.idempotent_replay, "draft replay flag"),
    };
  }

  function validateFinalReceipt(value) {
    const item = closedRecord(value, FINAL_RECEIPT_KEYS, "final receipt");
    if (item.schema !== RECEIPT_SCHEMA) fail("final receipt schema is unsupported");
    if (item.evidence_status !== "respondent_attestation_unverified") {
      fail("final receipt evidence status is unsupported");
    }
    return {
      schema: RECEIPT_SCHEMA,
      invitation_id: identifier(item.invitation_id, "final invitation ID"),
      assessment_id: identifier(item.assessment_id, "final assessment ID"),
      assessment_revision: integer(
        item.assessment_revision,
        1,
        1000000,
        "assessment revision",
      ),
      assessment_integrity_sha256: digest(
        item.assessment_integrity_sha256,
        "assessment integrity digest",
      ),
      question_ids: closedIdentifierList(item.question_ids, "final question IDs", MAX_QUESTIONS),
      attestation_sha256: digest(item.attestation_sha256, "attestation digest"),
      evidence_status: item.evidence_status,
      idempotent_replay: boolean(item.idempotent_replay, "final replay flag"),
    };
  }

  const state = {
    initialized: false,
    credential: null,
    apiBase: null,
    contract: null,
    answerMemory: new Map(),
    artifactMemory: [],
    pendingOperations: new Map(),
    activeControllers: new Set(),
    busy: false,
  };

  let elements = null;

  function element(id) {
    const value = global.document.getElementById(id);
    if (!value) fail("portal document contract is incomplete");
    return value;
  }

  function setStatus(message, statusState) {
    if (!elements) return;
    elements.status.textContent = message;
    if (statusState) elements.status.dataset.state = statusState;
    else delete elements.status.dataset.state;
  }

  function text(parent, value) {
    parent.textContent = String(value);
  }

  function append(parent, tagName, value, className) {
    const child = global.document.createElement(tagName);
    if (value !== undefined && value !== null) text(child, value);
    if (className) child.className = className;
    parent.appendChild(child);
    return child;
  }

  function makeLabel(control, labelText, helpText) {
    const wrapper = append(control.parentNode, "div", null, "field");
    control.parentNode.removeChild(control);
    const label = append(wrapper, "label", labelText);
    label.htmlFor = control.id;
    wrapper.appendChild(control);
    if (helpText) append(wrapper, "p", helpText, "help");
    return wrapper;
  }

  function setBusy(value) {
    state.busy = value;
    for (const button of [
      elements.unlockButton,
      elements.lockButton,
      elements.addArtifactButton,
      elements.saveDraftButton,
      elements.finalizeButton,
    ]) {
      button.disabled = value;
    }
  }

  function secureOperationId(action, body) {
    const fingerprint = JSON.stringify(body);
    const pending = state.pendingOperations.get(action);
    if (pending && pending.fingerprint === fingerprint) return pending.key;
    if (!global.crypto || typeof global.crypto.randomUUID !== "function") {
      fail("secure operation identifiers are unavailable");
    }
    const key = `portal-${action}-${global.crypto.randomUUID()}`;
    state.pendingOperations.set(action, { fingerprint, key });
    return key;
  }

  async function portalRequest(route, method, body) {
    if (!state.credential || !state.apiBase) throw new PortalRequestError(404);
    const controller = new global.AbortController();
    state.activeControllers.add(controller);
    const timeout = global.setTimeout(() => controller.abort(), 15000);
    const headers = {
      Accept: "application/json",
      Authorization: `ThirdPartyInvitation ${state.credential}`,
    };
    let requestBody;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      requestBody = JSON.stringify(body);
    }
    try {
      const response = await global.fetch(`${state.apiBase}${route}`, {
        method,
        headers,
        body: requestBody,
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) throw new PortalRequestError(response.status);
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.toLowerCase().startsWith("application/json")) {
        throw new PortalContractError("portal response content type is invalid");
      }
      return await response.json();
    } catch (error) {
      if (error instanceof PortalRequestError || error instanceof PortalContractError) throw error;
      throw new PortalRequestError(0);
    } finally {
      global.clearTimeout(timeout);
      state.activeControllers.delete(controller);
    }
  }

  function friendlyRequestError(error) {
    if (error instanceof PortalContractError) {
      return "The invitation authority returned an invalid closed contract. The workspace stayed locked.";
    }
    if (!(error instanceof PortalRequestError)) return "The portal operation could not be completed.";
    if (error.status === 404) return "The invitation is unavailable, expired, rotated, or revoked.";
    if (error.status === 403) return "The response is outside the invitation's authorized scope.";
    if (error.status === 409) return "The invitation changed. Reload its contract before continuing.";
    if (error.status === 422) return "The response is incomplete or invalid for the current contract.";
    if (error.status === 429) return "Too many attempts were made. Wait before trying again.";
    return "The invitation authority is temporarily unavailable. No completion is claimed.";
  }

  function controlId(prefix, index) {
    return `${prefix}-${index}`;
  }

  function renderQuestionControl(card, question, index, rememberedValue) {
    const controlIdValue = controlId("question-control", index);
    if (question.response_type === "boolean") {
      const select = append(card, "select");
      select.id = controlIdValue;
      select.dataset.responseType = question.response_type;
      append(select, "option", "Choose yes or no").value = "";
      append(select, "option", "Yes").value = "true";
      append(select, "option", "No").value = "false";
      if (typeof rememberedValue === "boolean") select.value = String(rememberedValue);
      makeLabel(select, "Response", "Choose an explicit boolean response.");
      return;
    }
    if (question.response_type === "integer" || question.response_type === "text_ref") {
      const input = append(card, "input");
      input.id = controlIdValue;
      input.dataset.responseType = question.response_type;
      if (question.response_type === "integer") {
        input.type = "number";
        input.min = "-1000000000";
        input.max = "1000000000";
        input.step = "1";
        if (Number.isSafeInteger(rememberedValue)) input.value = String(rememberedValue);
      } else {
        input.type = "text";
        input.maxLength = 128;
        input.pattern = "[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}";
        input.autocomplete = "off";
        input.spellcheck = false;
        if (typeof rememberedValue === "string") input.value = rememberedValue;
      }
      makeLabel(
        input,
        "Response",
        question.response_type === "text_ref"
          ? "Enter an opaque evidence or policy reference, not unrestricted narrative text."
          : "Enter a whole number from -1,000,000,000 through 1,000,000,000.",
      );
      return;
    }
    if (question.response_type === "single_choice") {
      const select = append(card, "select");
      select.id = controlIdValue;
      select.dataset.responseType = question.response_type;
      append(select, "option", "Choose an authorized option").value = "";
      for (const optionValue of question.options) {
        const option = append(select, "option", optionValue);
        option.value = optionValue;
      }
      if (typeof rememberedValue === "string" && question.options.includes(rememberedValue)) {
        select.value = rememberedValue;
      }
      makeLabel(select, "Response", "Only invitation-authorized options are available.");
      return;
    }
    const choices = append(card, "div", null, "choice-list");
    choices.dataset.responseType = question.response_type;
    choices.setAttribute("role", "group");
    choices.setAttribute("aria-label", "Authorized response options");
    const remembered = new Set(Array.isArray(rememberedValue) ? rememberedValue : []);
    for (const [optionIndex, optionValue] of question.options.entries()) {
      const label = append(choices, "label");
      const checkbox = append(label, "input");
      checkbox.type = "checkbox";
      checkbox.value = optionValue;
      checkbox.id = controlId("question-choice", index * 1000 + optionIndex);
      checkbox.checked = remembered.has(optionValue);
      append(label, "span", optionValue);
    }
  }

  function renderQuestions(contract) {
    const fragment = global.document.createDocumentFragment();
    const answered = new Set(contract.answered_question_ids);
    for (const [index, question] of contract.questions.entries()) {
      const card = append(fragment, "fieldset", null, "question-card portal-question");
      card.dataset.questionId = question.question_id;
      card.dataset.responseType = question.response_type;
      const legend = append(card, "legend", question.prompt_ref);
      legend.id = controlId("question-legend", index);
      const metadata = append(card, "div", null, "question-meta");
      append(metadata, "span", `Domain: ${question.domain}`, "chip");
      append(metadata, "span", `Reference: ${question.question_id}`, "chip");
      if (question.required) append(metadata, "span", "Required", "chip required-chip");
      if (answered.has(question.question_id) && !state.answerMemory.has(question.question_id)) {
        append(
          card,
          "p",
          "A prior draft contains this answer, but its value is not returned to the browser. Re-enter it before finalizing.",
          "answered-note",
        );
      }
      renderQuestionControl(card, question, index, state.answerMemory.get(question.question_id));
      const clear = append(card, "button", "Clear this response", "secondary remove-row");
      clear.type = "button";
      clear.addEventListener("click", () => {
        for (const input of card.querySelectorAll("input")) {
          if (input.type === "checkbox") input.checked = false;
          else input.value = "";
        }
        for (const select of card.querySelectorAll("select")) select.value = "";
        state.answerMemory.delete(question.question_id);
        setStatus(
          "Response cleared locally. Save the draft to refresh server-authorized conditional questions.",
        );
      });
    }
    if (contract.questions.length === 0) {
      append(fragment, "p", "No questions are currently authorized for this invitation.", "help");
    }
    elements.questions.replaceChildren(fragment);
    const answeredVisible = contract.questions.filter((question) =>
      contract.answered_question_ids.includes(question.question_id),
    ).length;
    text(
      elements.questionProgress,
      `${answeredVisible} of ${contract.questions.length} visible questions recorded in the current draft`,
    );
  }

  function createTextInput(parent, id, labelText, value, maximum, helpText) {
    const input = append(parent, "input");
    input.id = id;
    input.type = "text";
    input.maxLength = maximum;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.value = value || "";
    makeLabel(input, labelText, helpText);
    return input;
  }

  function renderArtifactRow(artifact, supportIds) {
    const index = elements.artifactManifests.children.length;
    const card = append(elements.artifactManifests, "fieldset", null, "artifact-card portal-artifact");
    const legend = append(card, "legend", `Evidence manifest ${index + 1}`);
    legend.id = controlId("artifact-legend", index);
    const grid = append(card, "div", null, "artifact-grid");
    const artifactId = createTextInput(
      grid,
      controlId("artifact-id", index),
      "Manifest reference",
      artifact ? artifact.artifact_id : "",
      128,
      "An opaque reference unique within this response.",
    );
    artifactId.dataset.artifactField = "artifact_id";
    const kind = append(grid, "select");
    kind.id = controlId("artifact-kind", index);
    kind.dataset.artifactField = "kind";
    for (const allowedKind of state.contract.artifact_policy.allowed_kinds) {
      const option = append(kind, "option", allowedKind);
      option.value = allowedKind;
    }
    if (artifact && state.contract.artifact_policy.allowed_kinds.includes(artifact.kind)) {
      kind.value = artifact.kind;
    }
    makeLabel(kind, "Evidence kind", "Kinds are fixed by the invitation policy.");
    const contentDigest = createTextInput(
      grid,
      controlId("artifact-digest", index),
      "Content SHA-256",
      artifact ? artifact.content_sha256 : "",
      64,
      "Exactly 64 lowercase hexadecimal characters.",
    );
    contentDigest.dataset.artifactField = "content_sha256";
    contentDigest.pattern = "[0-9a-f]{64}";
    const size = append(grid, "input");
    size.id = controlId("artifact-size", index);
    size.type = "number";
    size.min = "1";
    size.max = String(MAX_ARTIFACT_BYTES);
    size.step = "1";
    size.dataset.artifactField = "size_bytes";
    if (artifact) size.value = String(artifact.size_bytes);
    makeLabel(size, "Original size in bytes", "Metadata only; no corresponding bytes are uploaded.");
    const mediaType = createTextInput(
      grid,
      controlId("artifact-media", index),
      "Media type reference",
      artifact ? artifact.media_type_ref : "",
      128,
      "Use an approved opaque reference such as application.pdf.",
    );
    mediaType.dataset.artifactField = "media_type_ref";
    const support = append(card, "div", null, "support-list");
    support.dataset.artifactField = "supported_questions";
    append(support, "p", "Questions supported by this digest manifest", "help");
    const selected = new Set(supportIds || []);
    for (const [questionIndex, question] of state.contract.questions.entries()) {
      const label = append(support, "label");
      const checkbox = append(label, "input");
      checkbox.type = "checkbox";
      checkbox.value = question.question_id;
      checkbox.checked = selected.has(question.question_id);
      checkbox.id = controlId("artifact-support", index * MAX_QUESTIONS + questionIndex);
      append(label, "span", `${question.prompt_ref} (${question.question_id})`);
    }
    const remove = append(card, "button", "Remove manifest", "secondary remove-row");
    remove.type = "button";
    remove.addEventListener("click", () => {
      card.remove();
      renumberArtifactRows();
    });
  }

  function renumberArtifactRows() {
    const rows = Array.from(elements.artifactManifests.children);
    rows.forEach((row, index) => {
      const legend = row.querySelector("legend");
      if (legend) text(legend, `Evidence manifest ${index + 1}`);
    });
  }

  function renderArtifacts(contract, preserveLocal) {
    elements.artifactManifests.replaceChildren();
    if (!preserveLocal) {
      state.artifactMemory = contract.draft_artifacts.map((artifact) => ({
        artifact,
        supportIds: [],
      }));
    }
    for (const item of state.artifactMemory) {
      renderArtifactRow(item.artifact, item.supportIds);
    }
    const policy = contract.artifact_policy;
    text(
      elements.artifactPolicy,
      policy.maximum_artifacts === 0
        ? "This invitation does not authorize evidence manifests."
        : `Up to ${policy.maximum_artifacts} manifests and ${policy.maximum_total_bytes.toLocaleString()} declared bytes total; allowed kinds: ${policy.allowed_kinds.join(", ")}.`,
    );
    elements.addArtifactButton.hidden = policy.maximum_artifacts === 0;
  }

  function renderLimitations(contract) {
    const fragment = global.document.createDocumentFragment();
    for (const limitation of contract.limitations) {
      append(fragment, "li", LIMITATION_LABELS[limitation]);
    }
    append(
      fragment,
      "li",
      "The contract reports answered question references but never returns prior answer values to this browser.",
    );
    append(
      fragment,
      "li",
      "A completed response still requires internal review, evidence validation, and independent risk acceptance.",
    );
    elements.contractLimitations.replaceChildren(fragment);
  }

  function renderContract(contract, preserveLocal) {
    state.contract = contract;
    text(elements.vendorReference, contract.vendor_ref);
    text(elements.assessmentReference, contract.assessment_id);
    text(elements.noticeReference, contract.notice_ref);
    text(elements.noticeDigest, contract.notice_sha256);
    text(elements.draftRevision, contract.draft_revision);
    const expiry = new Date(contract.expires_at * 1000);
    elements.invitationExpiry.dateTime = expiry.toISOString();
    text(elements.invitationExpiry, expiry.toLocaleString());
    renderLimitations(contract);
    renderQuestions(contract);
    renderArtifacts(contract, preserveLocal);
    elements.workspace.hidden = false;
    elements.unlockPanel.hidden = true;
    elements.receiptPanel.hidden = true;
  }

  function readQuestionValue(card, requiredForFinal) {
    const type = card.dataset.responseType;
    if (type === "multi_choice") {
      const checked = Array.from(card.querySelectorAll('input[type="checkbox"]:checked')).map(
        (input) => input.value,
      );
      if (checked.length === 0) return undefined;
      if (new Set(checked).size !== checked.length) fail("multi-choice response is duplicated");
      return checked;
    }
    const control = card.querySelector("input[data-response-type], select[data-response-type]");
    if (!control || control.value === "") return undefined;
    if (type === "boolean") {
      if (control.value === "true") return true;
      if (control.value === "false") return false;
      fail("boolean response is invalid");
    }
    if (type === "integer") {
      const parsed = Number(control.value);
      return integer(parsed, -1000000000, 1000000000, "integer response");
    }
    if (type === "text_ref") return identifier(control.value, "response reference");
    if (type === "single_choice") {
      const question = state.contract.questions.find(
        (item) => item.question_id === card.dataset.questionId,
      );
      if (!question || !question.options.includes(control.value)) fail("choice response is invalid");
      return control.value;
    }
    if (requiredForFinal) fail("question response type is invalid");
    return undefined;
  }

  function readArtifacts(answers) {
    const policy = state.contract.artifact_policy;
    const rows = Array.from(elements.artifactManifests.querySelectorAll(".portal-artifact"));
    if (rows.length > policy.maximum_artifacts) fail("artifact count exceeds the invitation policy");
    const artifacts = [];
    const artifactIds = new Set();
    const memory = [];
    let totalBytes = 0;
    for (const row of rows) {
      const value = {};
      for (const input of row.querySelectorAll("[data-artifact-field]")) {
        const field = input.dataset.artifactField;
        if (field === "supported_questions") continue;
        value[field] = field === "size_bytes" ? Number(input.value) : input.value;
      }
      const artifact = validateArtifact(value, policy, "artifact manifest");
      if (artifactIds.has(artifact.artifact_id)) fail("artifact IDs must be unique");
      artifactIds.add(artifact.artifact_id);
      totalBytes += artifact.size_bytes;
      const supportContainer = row.querySelector('[data-artifact-field="supported_questions"]');
      const supportIds = supportContainer
        ? Array.from(supportContainer.querySelectorAll('input[type="checkbox"]:checked')).map(
            (input) => input.value,
          )
        : [];
      if (supportIds.length === 0) fail("every artifact manifest must support at least one answer");
      for (const questionId of supportIds) {
        if (!Object.prototype.hasOwnProperty.call(answers, questionId)) {
          fail("an artifact manifest cannot support an unanswered question");
        }
        answers[questionId].artifact_ids.push(artifact.artifact_id);
      }
      artifacts.push(artifact);
      memory.push({ artifact, supportIds });
    }
    if (totalBytes > policy.maximum_total_bytes) {
      fail("artifact bytes exceed the invitation policy");
    }
    state.artifactMemory = memory;
    return artifacts;
  }

  function submission(requireComplete) {
    if (!state.contract) fail("invitation contract is unavailable");
    const answers = {};
    for (const card of elements.questions.querySelectorAll(".portal-question")) {
      const questionId = identifier(card.dataset.questionId, "question ID");
      const value = readQuestionValue(card, requireComplete);
      if (value === undefined) continue;
      state.answerMemory.set(questionId, value);
      answers[questionId] = { value, artifact_ids: [] };
    }
    if (Object.keys(answers).length > MAX_QUESTIONS) fail("answer count exceeds its bound");
    if (requireComplete) {
      const missing = state.contract.required_question_ids.filter(
        (questionId) => !Object.prototype.hasOwnProperty.call(answers, questionId),
      );
      if (missing.length !== 0) fail("all required visible questions must be answered");
      if (Object.keys(answers).length === 0) fail("at least one answer is required to finalize");
    }
    const artifacts = readArtifacts(answers);
    return { answers, artifacts };
  }

  async function loadContract(preserveLocal) {
    const raw = await portalRequest("/contract", "GET");
    const contract = validateContract(raw);
    if (state.contract && state.contract.invitation_id !== contract.invitation_id) {
      fail("invitation identity changed");
    }
    renderContract(contract, preserveLocal);
    return contract;
  }

  function lockPortal(message, showReceipt) {
    for (const controller of state.activeControllers) controller.abort();
    state.activeControllers.clear();
    state.credential = null;
    state.contract = null;
    state.answerMemory.clear();
    state.artifactMemory = [];
    state.pendingOperations.clear();
    elements.credential.value = "";
    elements.attestation.checked = false;
    elements.questions.replaceChildren();
    elements.artifactManifests.replaceChildren();
    elements.workspace.hidden = true;
    elements.unlockPanel.hidden = Boolean(showReceipt);
    if (!showReceipt) elements.receiptPanel.hidden = true;
    setStatus(message || "Invitation credential cleared from this page.", "success");
  }

  async function unlock(rawCredential) {
    if (!state.initialized) fail("portal is not initialized");
    if (state.busy) return false;
    if (typeof rawCredential !== "string" || !TOKEN_RE.test(rawCredential)) {
      elements.credential.value = "";
      setStatus("The invitation credential format is invalid.", "error");
      return false;
    }
    state.credential = rawCredential;
    elements.credential.value = "";
    state.answerMemory.clear();
    state.artifactMemory = [];
    state.pendingOperations.clear();
    setBusy(true);
    setStatus("Opening the invitation's server-authorized contract…");
    try {
      await loadContract(false);
      setStatus("Invitation opened. No evidence completeness or identity verification is implied.", "success");
      elements.questions.querySelector("input, select")?.focus();
      return true;
    } catch (error) {
      state.credential = null;
      state.contract = null;
      state.answerMemory.clear();
      state.artifactMemory = [];
      elements.workspace.hidden = true;
      elements.unlockPanel.hidden = false;
      setStatus(friendlyRequestError(error), "error");
      elements.credential.focus();
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft() {
    if (state.busy) return;
    setBusy(true);
    try {
      const payload = submission(false);
      const withoutKey = {
        ...payload,
        expected_revision: state.contract.draft_revision,
      };
      const body = {
        ...withoutKey,
        idempotency_key: secureOperationId("draft", withoutKey),
      };
      const rawReceipt = await portalRequest("/draft", "PUT", body);
      const receipt = validateDraftReceipt(rawReceipt);
      if (receipt.invitation_id !== state.contract.invitation_id) {
        fail("draft receipt invitation does not match the contract");
      }
      state.pendingOperations.delete("draft");
      await loadContract(true);
      setStatus(
        `Draft revision ${receipt.revision} recorded. Conditional questions were refreshed.`,
        "success",
      );
    } catch (error) {
      if (error instanceof PortalRequestError && error.status === 409) {
        try {
          await loadContract(false);
        } catch (_refreshError) {
          lockPortal("The invitation changed and could not be safely reloaded.", false);
        }
      }
      setStatus(friendlyRequestError(error), "error");
    } finally {
      setBusy(false);
    }
  }

  function renderReceipt(receipt) {
    text(elements.receiptRevision, receipt.assessment_revision);
    text(elements.receiptAssessmentDigest, receipt.assessment_integrity_sha256);
    text(elements.receiptAttestationDigest, receipt.attestation_sha256);
    text(elements.receiptEvidenceStatus, receipt.evidence_status);
    elements.receiptPanel.hidden = false;
  }

  async function finalizeResponse() {
    if (state.busy) return;
    if (elements.attestation.checked !== true) {
      setStatus("Explicit respondent attestation is required before finalization.", "error");
      elements.attestation.focus();
      return;
    }
    setBusy(true);
    try {
      const payload = submission(true);
      const withoutKey = {
        ...payload,
        attested: true,
        expected_draft_revision: state.contract.draft_revision,
      };
      const body = {
        ...withoutKey,
        idempotency_key: secureOperationId("final", withoutKey),
      };
      const rawReceipt = await portalRequest("/finalize", "POST", body);
      const receipt = validateFinalReceipt(rawReceipt);
      if (
        receipt.invitation_id !== state.contract.invitation_id ||
        receipt.assessment_id !== state.contract.assessment_id
      ) {
        fail("final receipt authority does not match the invitation contract");
      }
      state.pendingOperations.delete("final");
      renderReceipt(receipt);
      lockPortal("Final response recorded. The invitation credential was cleared.", true);
    } catch (error) {
      setStatus(friendlyRequestError(error), "error");
    } finally {
      setBusy(false);
    }
  }

  function addArtifact() {
    if (!state.contract) return;
    const count = elements.artifactManifests.querySelectorAll(".portal-artifact").length;
    if (count >= state.contract.artifact_policy.maximum_artifacts) {
      setStatus("The invitation's evidence-manifest count bound has been reached.", "error");
      return;
    }
    renderArtifactRow(null, []);
    elements.artifactManifests.lastElementChild?.querySelector("input")?.focus();
  }

  function bindDocument() {
    elements = {
      status: element("portal-status"),
      unlockPanel: element("unlock-panel"),
      unlockForm: element("unlock-form"),
      credential: element("invitation-credential"),
      unlockButton: element("unlock-button"),
      workspace: element("workspace"),
      lockButton: element("lock-button"),
      vendorReference: element("vendor-reference"),
      assessmentReference: element("assessment-reference"),
      noticeReference: element("notice-reference"),
      noticeDigest: element("notice-digest"),
      invitationExpiry: element("invitation-expiry"),
      draftRevision: element("draft-revision"),
      contractLimitations: element("contract-limitations"),
      questionProgress: element("question-progress"),
      questions: element("questions"),
      artifactPolicy: element("artifact-policy"),
      artifactManifests: element("artifact-manifests"),
      addArtifactButton: element("add-artifact-button"),
      attestation: element("respondent-attestation"),
      saveDraftButton: element("save-draft-button"),
      finalizeButton: element("finalize-button"),
      receiptPanel: element("receipt-panel"),
      receiptRevision: element("receipt-revision"),
      receiptAssessmentDigest: element("receipt-assessment-digest"),
      receiptAttestationDigest: element("receipt-attestation-digest"),
      receiptEvidenceStatus: element("receipt-evidence-status"),
    };
    elements.unlockForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const supplied = elements.credential.value;
      elements.credential.value = "";
      void unlock(supplied);
    });
    elements.lockButton.addEventListener("click", () => lockPortal(null, false));
    elements.addArtifactButton.addEventListener("click", addArtifact);
    elements.saveDraftButton.addEventListener("click", () => void saveDraft());
    elements.finalizeButton.addEventListener("click", () => void finalizeResponse());
  }

  function initialize() {
    if (state.initialized) return;
    const pathname = global.location ? global.location.pathname : "";
    if (!PORTAL_PATH_RE.test(pathname)) return;
    const removedFragment = Boolean(global.location.hash);
    if (removedFragment) {
      global.history.replaceState(null, "", pathname);
    }
    state.apiBase = pathname.slice(0, -"/portal".length);
    bindDocument();
    state.initialized = true;
    if (removedFragment) {
      setStatus("URL fragments are not accepted and were removed without being read.", "error");
    }
  }

  const publicApi = Object.freeze({
    unlock,
    lock: () => {
      if (state.initialized) lockPortal(null, false);
    },
    validateContract,
    validateDraftReceipt,
    validateFinalReceipt,
  });
  Object.defineProperty(global, "LightworkThirdPartyPortal", {
    configurable: false,
    enumerable: true,
    writable: false,
    value: publicApi,
  });

  if (global.document) {
    if (global.document.readyState === "loading") {
      global.document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
      initialize();
    }
  }
})(globalThis);
