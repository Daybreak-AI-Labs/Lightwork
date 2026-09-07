(function () {
  "use strict";
  var busy = false;
  var get = function (id) { return document.getElementById(id); };
  async function switchMode(recovery) {
    if (busy) return;
    busy = true;
    get("enter-recovery").disabled = get("leave-recovery").disabled = true;
    get("status").textContent = "Closing this app's local workspace and starting the selected mode…";
    try {
      if (!window.__TAURI__ || !window.__TAURI__.core) throw new Error("Open these controls from Lightwork Desktop.");
      await window.__TAURI__.core.invoke("switch_recovery_mode", { recovery: recovery, confirm: true });
      var responding = false;
      for (var attempt = 0; attempt < 60; attempt++) {
        try {
          await fetch("http://127.0.0.1:8765/livez", { mode: "no-cors", cache: "no-store" });
          responding = true; break;
        } catch (_) { await new Promise(function (resolve) { setTimeout(resolve, 1000); }); }
      }
      if (!responding) throw new Error("The selected workspace mode did not start. Retry or inspect startup details from Reconnect Lightwork.");
      window.location.replace(recovery ? "http://127.0.0.1:8765/workspace/recovery" : "http://127.0.0.1:8765/");
    } catch (error) {
      get("status").textContent = "Lightwork could not switch modes. No recovery action was applied. Retry or review the details.";
      get("details").textContent = String(error);
    } finally {
      busy = false;
      get("enter-recovery").disabled = get("leave-recovery").disabled = false;
    }
  }
  get("enter-recovery").addEventListener("click", function () { return switchMode(true); });
  get("leave-recovery").addEventListener("click", function () { return switchMode(false); });
})();
