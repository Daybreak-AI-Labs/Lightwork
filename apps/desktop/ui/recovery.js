(function () {
  "use strict";
  var busy = false;
  var get = function (id) { return document.getElementById(id); };
  async function waitForWorkspace() {
    var deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      var controller = new AbortController(), timer;
      try {
        await Promise.race([
          fetch("http://127.0.0.1:8765/livez", { mode: "no-cors", cache: "no-store", signal: controller.signal }),
          new Promise(function (_, reject) {
            timer = setTimeout(function () { controller.abort(); reject(new Error("Readiness request timed out.")); }, Math.min(5000, deadline - Date.now()));
          })
        ]);
        return;
      } catch (_) {
        if (Date.now() < deadline) await new Promise(function (resolve) { setTimeout(resolve, Math.min(1000, deadline - Date.now())); });
      } finally { clearTimeout(timer); }
    }
    throw new Error("The selected workspace mode did not respond within 60 seconds. Retry or inspect startup details from Reconnect Lightwork.");
  }
  async function switchMode(recovery) {
    if (busy) return;
    busy = true;
    get("enter-recovery").disabled = get("leave-recovery").disabled = true;
    get("status").textContent = "Closing this app's local workspace and starting the selected mode…";
    var modeChanged = false;
    try {
      if (!window.__TAURI__ || !window.__TAURI__.core) throw new Error("Open these controls from Lightwork Desktop.");
      await window.__TAURI__.core.invoke("switch_recovery_mode", { recovery: recovery, confirm: true });
      modeChanged = true;
      await waitForWorkspace();
      window.location.replace(recovery ? "http://127.0.0.1:8765/workspace/recovery" : "http://127.0.0.1:8765/");
    } catch (error) {
      get("status").textContent = modeChanged
        ? "The mode change was acknowledged, but the workspace is not responding. Retry or review startup details."
        : "Lightwork could not confirm the mode change. Retry or review the details.";
      get("details").textContent = String(error);
    } finally {
      busy = false;
      get("enter-recovery").disabled = get("leave-recovery").disabled = false;
    }
  }
  get("enter-recovery").addEventListener("click", function () { return switchMode(true); });
  get("leave-recovery").addEventListener("click", function () { return switchMode(false); });
})();
