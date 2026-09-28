(function () {
  "use strict";

  var root = document.getElementById("rcp-root");
  if (!root) { return; }
  var apiBase = root.dataset.apiBase || "/api/v1/audit/receipts";
  var maxResponseBytes = 8 * 1024 * 1024;
  var hexPattern = /^[0-9a-f]{64}$/;
  var dayPattern = /^\d{4}-\d{2}-\d{2}$/;
  var kindPattern = /^[a-z][a-z0-9_]{0,63}$/;
  var state = document.getElementById("rcp-state");
  var output = document.getElementById("rcp-output");
  var operationState = document.getElementById("rcp-operation-state");
  var rows = document.getElementById("rcp-rows");
  var daySelect = document.getElementById("rcp-day");

  function fail(message) {
    var error = new Error(message);
    error.code = "invalid_input";
    return error;
  }

  async function readBody(response) {
    var declared = Number(response.headers.get("content-length") || 0);
    if (declared > maxResponseBytes) {
      if (response.body && typeof response.body.cancel === "function") { await response.body.cancel(); }
      throw fail("Response exceeds the console byte bound.");
    }
    var text = await response.text();
    if (text.length > maxResponseBytes) { throw fail("Response exceeds the console byte bound."); }
    return text;
  }

  async function readJson(response) {
    var text = await readBody(response);
    var payload;
    try { payload = text ? JSON.parse(text) : {}; }
    catch (_error) { throw fail("The server returned invalid JSON."); }
    if (!response.ok) {
      var detail = payload && typeof payload.detail === "string" ? payload.detail : "Trust receipt request failed.";
      throw fail(detail);
    }
    return payload;
  }

  async function request(path, options) {
    var settings = Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, options || {});
    if (settings.body) { settings.headers["Content-Type"] = "application/json"; }
    return fetch(apiBase + path, settings);
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

  function when(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) { return "—"; }
    return new Date(n * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC";
  }

  function saveBlob(name, text, type) {
    var blob = new Blob([text], { type: type });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  async function loadStatus() {
    var st = await readJson(await request("/status"));
    document.getElementById("rcp-key-id").textContent = st.key_id || "—";
    document.getElementById("rcp-custody").textContent = st.key_custody === "offhost" ? "Externally supplied; signs locally" : st.key_custody;
    document.getElementById("rcp-days").textContent = String(st.days.length);
    document.getElementById("rcp-pubkey").textContent = st.pubkey || "no issuer key available";
    document.getElementById("rcp-verifier-sha").textContent = st.verifier_sha256;
    var title = document.getElementById("rcp-health-title");
    var detail = document.getElementById("rcp-health-detail");
    if (!st.signing_enabled) {
      setMark("failed", "Signing off");
      title.textContent = "Audit signing is off";
      detail.textContent = "Rows are written without a hash chain or signature, so nothing here can be cited. Enable [audit] sign (installer: 'Sign the audit log') and restart.";
    } else if (!st.key_id) {
      setMark("failed", "No key");
      title.textContent = "Signing is on but no issuer key is reachable";
      detail.textContent = "Receipts cannot be signed until the audit signing key loads. Check the key custody configuration.";
    } else if (st.key_custody === "colocated") {
      setMark("partial", "Integrity");
      title.textContent = "Receipts sign with a co-located key";
      detail.textContent = "The signature detects edits by someone without the signing key. A process able to use this key can re-sign history. Independently retain checkpoints to detect changes to committed history.";
    } else {
      setMark("partial", "Integrity");
      title.textContent = "Receipts sign with an externally supplied key";
      detail.textContent = "The private key is injected or unwrapped into this signing process. This does not prove that a host process cannot re-sign history. Independently retain checkpoints to detect changes to committed history.";
    }
    daySelect.textContent = "";
    if (!st.days.length) {
      var none = document.createElement("option");
      none.value = "";
      none.textContent = "No audit day-files on disk";
      daySelect.appendChild(none);
    }
    st.days.forEach(function (day, index) {
      var option = document.createElement("option");
      option.value = day;
      option.textContent = day + (index === 0 ? " (latest)" : "");
      daySelect.appendChild(option);
    });
    show("Issuer state", st);
    return st;
  }

  async function mint(day, rowHash) {
    var body = {
      day: day,
      row_hash: rowHash,
      include_suffix: document.getElementById("rcp-suffix").checked,
      anchor: document.getElementById("rcp-anchor").checked
    };
    var response = await request("/mint", { method: "POST", body: JSON.stringify(body) });
    var text = await readBody(response);
    if (!response.ok) {
      var payload;
      try { payload = JSON.parse(text); } catch (_error) { payload = {}; }
      throw fail(payload && typeof payload.detail === "string" ? payload.detail : "Minting failed.");
    }
    var receipt;
    try { receipt = JSON.parse(text); } catch (_error) { throw fail("The minted receipt is not valid JSON."); }
    var name = "trust-receipt-" + day + "-" + rowHash.slice(0, 12) + ".json";
    saveBlob(name, text, "application/json");
    var commitment = receipt.commitment || null;
    var summary = {
      saved_as: name,
      claim: receipt.claim,
      key_id: receipt.signature && receipt.signature.key_id,
      key_custody: receipt.issuer && receipt.issuer.key_custody,
      chain_rows_after: receipt.chain ? receipt.chain.suffix_ndjson.length : 0,
      commitment: commitment ? commitment.source : "none (verifies at depth 'signed' only)",
      anchor_digest: commitment && commitment.raw_sha256 ? commitment.raw_sha256 : null
    };
    show("Minted " + name, summary);
    document.getElementById("rcp-receipt").value = text;
  }

  function renderRows(payload) {
    rows.textContent = "";
    var note = document.getElementById("rcp-rows-note");
    note.textContent = payload.items.length + " of " + payload.total + " row(s) on " + payload.day + (payload.truncated ? " (showing the latest)" : "");
    if (!payload.items.length) {
      var empty = document.createElement("tr");
      var td = cell("No rows match.");
      td.colSpan = 7;
      empty.appendChild(td);
      rows.appendChild(empty);
      return;
    }
    payload.items.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.appendChild(cell(String(row.position)));
      tr.appendChild(cell(when(row.ts)));
      tr.appendChild(cell(row.kind || "—"));
      tr.appendChild(cell(row.agent || "—"));
      tr.appendChild(cell((row.decision || "recorded") + (row.tool ? " · " + row.tool : "")));
      tr.appendChild(cell(row.hash ? row.hash.slice(0, 16) + "…" : "unsigned"));
      var action = document.createElement("td");
      if (row.signed) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "btn";
        button.textContent = "Mint";
        button.addEventListener("click", function () { mint(payload.day, row.hash).catch(showError); });
        action.appendChild(button);
      } else {
        action.textContent = "not signed";
      }
      tr.appendChild(action);
      rows.appendChild(tr);
    });
  }

  async function loadRows(day, kind) {
    var query = "/rows?day=" + encodeURIComponent(day) + "&limit=200";
    if (kind) { query += "&kind=" + encodeURIComponent(kind); }
    renderRows(await readJson(await request(query)));
  }

  function renderVerdict(result) {
    var box = document.getElementById("rcp-verdict-box");
    var verdict = document.getElementById("rcp-verdict");
    var detail = document.getElementById("rcp-verdict-detail");
    var claims = document.getElementById("rcp-claims");
    box.hidden = false;
    verdict.dataset.ok = result.ok ? "true" : "false";
    verdict.textContent = (result.ok ? "VERIFIED" : "REJECTED") + " · depth " + result.depth;
    detail.textContent = result.reason + (result.trusted_key_ids.length ? " Trusted keys: " + result.trusted_key_ids.join(", ") + "." : " No trusted key was supplied.");
    claims.textContent = "";
    (result.claims || []).forEach(function (claim) {
      var li = document.createElement("li");
      var status = document.createElement("strong");
      status.dataset.status = claim.status;
      status.textContent = claim.status + " ";
      li.appendChild(status);
      li.appendChild(document.createTextNode(claim.name + ": " + claim.detail));
      claims.appendChild(li);
    });
    show("Verification report", result.report);
  }

  async function downloadVerifier() {
    // Fetched, not navigated: the download then rides the same credentials
    // as every other call on this page, whatever the deployment's auth
    // transport, and the served digest is shown next to the saved file.
    var response = await request("/verifier");
    var text = await readBody(response);
    if (!response.ok) {
      var payload;
      try { payload = JSON.parse(text); } catch (_error) { payload = {}; }
      throw fail(payload && typeof payload.detail === "string" ? payload.detail : "Verifier download failed.");
    }
    saveBlob("trust_receipt_verify.py", text, "text/x-python");
    show("Saved trust_receipt_verify.py", {
      sha256: response.headers.get("x-verifier-sha256"),
      bytes: text.length,
      usage: "python trust_receipt_verify.py receipt.json --key <issuer-pubkey-hex> [--anchor-digest <sha256>]"
    });
  }

  document.getElementById("rcp-refresh").addEventListener("click", function () { loadStatus().catch(showError); });
  document.getElementById("rcp-download-verifier").addEventListener("click", function () { downloadVerifier().catch(showError); });

  document.getElementById("rcp-rows-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(event.target);
    (async function () {
      var day = String(form.get("day") || "").trim();
      var kind = String(form.get("kind") || "").trim();
      if (!dayPattern.test(day)) { throw fail("Pick an audit day."); }
      if (kind && !kindPattern.test(kind)) { throw fail("Kind must be a lowercase audit event name."); }
      await loadRows(day, kind);
    })().catch(showError);
  });

  document.getElementById("rcp-file").addEventListener("change", function (event) {
    var file = event.target.files && event.target.files[0];
    if (!file) { return; }
    if (file.size > maxResponseBytes) { showError(fail("Receipt file exceeds the size bound.")); return; }
    file.text().then(function (text) { document.getElementById("rcp-receipt").value = text; }).catch(showError);
  });

  document.getElementById("rcp-verify-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(event.target);
    (async function () {
      var raw = String(form.get("receipt") || "").trim();
      if (!raw) { throw fail("Paste or pick a receipt first."); }
      var receipt;
      try { receipt = JSON.parse(raw); } catch (_error) { throw fail("The receipt is not valid JSON."); }
      if (!receipt || typeof receipt !== "object" || Array.isArray(receipt)) { throw fail("A receipt is a JSON object."); }
      // Send the document verbatim: re-serialising the parsed object would
      // normalise numbers (1767225600.0 -> 1767225600) and break the signature.
      var body = { receipt: raw, keys: [], use_local_keys: document.getElementById("rcp-local-keys").checked };
      var key = String(form.get("key") || "").trim().toLowerCase();
      if (key) {
        if (!hexPattern.test(key)) { throw fail("A trusted key is 64 hex characters."); }
        body.keys.push(key);
      }
      var digest = String(form.get("anchor_digest") || "").trim().toLowerCase();
      if (digest) {
        if (!hexPattern.test(digest)) { throw fail("A checkpoint digest is 64 hex characters."); }
        body.anchor_digest = digest;
      }
      renderVerdict(await readJson(await request("/verify", { method: "POST", body: JSON.stringify(body) })));
    })().catch(showError);
  });

  loadStatus().then(function (st) {
    if (st.days.length) { return loadRows(st.days[0], ""); }
    return null;
  }).catch(function (error) {
    setMark("failed", "Unavailable");
    showError(error);
  });
})();
