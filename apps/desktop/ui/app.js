// Splash controller for the Lightwork desktop shell.
//
// The Rust side (src/lib.rs) starts `maverick dashboard` on 127.0.0.1:8765 if
// nothing is listening; this page waits for it and navigates in.
//
// IMPORTANT — why `mode: "no-cors"`: this page's origin is the Tauri webview
// (tauri://localhost / http://tauri.localhost), so a request to the dashboard
// at http://127.0.0.1:8765 is cross-origin. The dashboard emits no CORS header
// for this origin, so a normal `fetch` is blocked and REJECTS — which made the
// old splash poll forever and never connect. A `no-cors` probe instead RESOLVES
// (opaque) the moment the server answers and only rejects on a refused
// connection, which is exactly the signal we want. Top-level navigation
// (location.replace) is not subject to CORS, so once we know the port answers
// we just go there.

(function () {
  "use strict";

  var PORT = 8765; // canonical dashboard port (maverick dashboard default)
  var DASH = "http://127.0.0.1:" + PORT;
  var FAST_MS = 600; // poll interval while we expect a quick startup
  var SLOW_MS = 2000; // poll interval after we've shown the trouble card
  var TROUBLE_AFTER = 9; // ~6s of fast polls before surfacing help

  var statusText = document.getElementById("status-text");
  var trouble = document.getElementById("trouble");
  var tries = 0;
  var connected = false;
  var probeTimer;
  var generation = 0;
  var retryPending = false;
  var diagnostics = document.getElementById("diagnostics");
  var prepareReport = document.getElementById("prepare-startup-report");
  var downloadReport = document.getElementById("download-startup-report");
  var reportStatus = document.getElementById("startup-report-status");
  var reportPreview = document.getElementById("startup-report");
  var startupReport = null;

  function invoke(command) {
    if (window.__TAURI__ && window.__TAURI__.core) return window.__TAURI__.core.invoke(command);
    return Promise.reject(new Error("Startup controls are available in the Lightwork desktop app."));
  }

  function refreshDetails() {
    return invoke("dashboard_diagnostics").then(function (text) {
      if (diagnostics) diagnostics.textContent = text;
    }, function (error) {
      if (diagnostics) diagnostics.textContent = String(error);
    });
  }

  if (prepareReport && downloadReport && reportStatus && reportPreview) {
    prepareReport.addEventListener("click", function () {
      startupReport = null; prepareReport.disabled = true; downloadReport.disabled = true;
      reportPreview.textContent = ""; reportPreview.hidden = true; reportStatus.textContent = "Preparing startup facts…";
      invoke("startup_support_report").then(function (report) {
        if (!report || report.schema !== "lightwork.startup-support.v1") throw new Error("The startup report could not be verified.");
        startupReport = JSON.stringify(report, null, 2); reportPreview.textContent = startupReport; reportPreview.hidden = false;
        downloadReport.disabled = false; reportStatus.textContent = "Review the startup report, then download it if you want to share it.";
      }).catch(function () {
        startupReport = null; reportStatus.textContent = "Startup report unavailable. You can retry or review the existing startup details.";
      }).finally(function () { prepareReport.disabled = false; });
    });
    downloadReport.addEventListener("click", function () {
      if (!startupReport) return;
      var url = URL.createObjectURL(new Blob([startupReport], {type: "application/json"}));
      var link = document.createElement("a"); link.href = url; link.download = "Lightwork-startup-support.json"; link.click();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      reportStatus.textContent = "Startup report downloaded. Nothing was sent automatically.";
    });
  }

  function setStatus(msg) {
    if (statusText) statusText.textContent = msg;
  }

  function connect() {
    if (connected) return;
    connected = true;
    setStatus("Connected — loading…");
    window.location.replace(DASH + "/");
  }

  function probe() {
    if (connected) return;
    var current = generation;
    tries += 1;

    if (tries === 1) setStatus("Starting the Lightwork engine…");
    else if (tries < TROUBLE_AFTER) setStatus("Waiting for the dashboard…");

    fetch(DASH + "/healthz", { mode: "no-cors", cache: "no-store" })
      .then(function () {
        if (current !== generation) return;
        connect();
      })
      .catch(function () {
        if (current !== generation) return;
        if (tries >= TROUBLE_AFTER) {
          showTrouble();
          // Keep trying slowly so it auto-recovers if the user starts it.
          probeTimer = setTimeout(probe, SLOW_MS);
        } else {
          probeTimer = setTimeout(probe, FAST_MS);
        }
      });
  }

  function showTrouble() {
    if (trouble) trouble.style.display = "block";
    setStatus("Still waiting for the dashboard…");
  }

  // --- trouble-card controls ---
  var retry = document.getElementById("retry");
  if (retry) {
    retry.addEventListener("click", async function () {
      if (retryPending || connected) return;
      retryPending = true;
      retry.disabled = true;
      generation += 1;
      clearTimeout(probeTimer);
      if (trouble) trouble.style.display = "none";
      tries = 0;
      setStatus("Retrying…");
      try {
        await invoke("retry_dashboard");
        probe();
      } catch (error) {
        if (diagnostics) diagnostics.textContent = String(error);
        showTrouble();
        setStatus("Lightwork could not start. Review startup details, then try again.");
        probeTimer = setTimeout(probe, SLOW_MS);
      } finally {
        retryPending = false;
        retry.disabled = false;
      }
    });
  }

  var refresh = document.getElementById("refresh-details");
  if (refresh) refresh.addEventListener("click", refreshDetails);

  var copy = document.getElementById("copy");
  var copied = document.getElementById("copied");
  if (copy) {
    copy.addEventListener("click", function () {
      var cmd = diagnostics ? diagnostics.textContent : "No startup details available.";
      var flash = function (message) {
        if (!copied) return;
        copied.textContent = message || "Copied";
        copied.style.opacity = "1";
        setTimeout(function () { copied.style.opacity = "0"; }, message ? 5000 : 1400);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(cmd).then(flash, fallbackCopy);
      } else {
        fallbackCopy();
      }
      function fallbackCopy() {
        var ta = document.createElement("textarea");
        ta.value = cmd;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try {
          if (document.execCommand("copy")) flash();
          else flash("Could not copy. Select the details above.");
        } catch (e) {
          flash("Could not copy. Select the details above.");
        } finally {
          document.body.removeChild(ta);
        }
      }
    });
  }

  probe();
})();
