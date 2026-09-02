/* Guard the Plugins control plane against ambiguous, duplicate mutations.
   Every action is confirmed against its exact target, runs single-flight, and
   restores its controls after server or network failures so the operator can
   correct the problem and retry. */
(function (global) {
  "use strict";

  function lines(value) {
    return String(value || "")
      .split(/[\s,]+/)
      .map(function (item) { return item.trim(); })
      .filter(Boolean);
  }

  function confirmationFor(form, submitter) {
    var action = submitter && submitter.getAttribute("data-plugin-action");
    var target = submitter && submitter.getAttribute("data-plugin-target");
    target = String(target || "this plugin");
    if (action === "enable") {
      return "Enable plugin \u201c" + target + "\u201d? Lightwork will import and execute this plugin's registered code on the next run.";
    }
    if (action === "disable") {
      return "Disable plugin \u201c" + target + "\u201d? Its tools, channels, and integrations will stop being configured on the next run.";
    }
    if (action === "reset") {
      return "Reset plugin \u201c" + target + "\u201d to the organization default? This dashboard override will be removed.";
    }
    if (action === "install") {
      return "Install approved package \u201c" + target + "\u201d into this Lightwork environment? Package installation executes third-party install hooks and changes the runtime.";
    }
    if (action === "replace-approvals") {
      var field = form && form.elements && form.elements.names;
      var packages = lines(field && field.value);
      var exact = packages.length ? packages.join(", ") : "no packages";
      return "Replace the complete approved plugin-package list with " + exact + "? " +
        (packages.length
          ? "Only these " + packages.length + " package(s) will remain eligible for in-app installation."
          : "This clears the list and blocks every new in-app package install.");
    }
    if (action === "install-switch-on") {
      return "Turn in-app plugin installation on? Administrators will be able to install every package on the current approved list.";
    }
    if (action === "install-switch-off") {
      return "Turn in-app plugin installation off? New in-app package installations will be blocked.";
    }
    return "Apply this plugin control-plane change?";
  }

  function setStatus(node, message, failed) {
    if (!node) return;
    node.textContent = message;
    node.setAttribute("role", failed ? "alert" : "status");
    node.setAttribute("data-tone", failed ? "danger" : "info");
  }

  async function failureDetail(response) {
    var fallback = "The server rejected the change (HTTP " + response.status + ").";
    try {
      var contentType = String(response.headers.get("content-type") || "");
      if (contentType.indexOf("application/json") !== -1) {
        var body = await response.json();
        if (body && typeof body.detail === "string" && body.detail.trim()) {
          return body.detail.trim();
        }
      }
    } catch (_error) {
      // The bounded status fallback intentionally replaces malformed responses.
    }
    return fallback;
  }

  function defaultPayload(form, submitter) {
    var payload;
    try {
      payload = new global.FormData(form, submitter);
    } catch (_error) {
      payload = new global.FormData(form);
    }
    if (submitter && submitter.name && !payload.has(submitter.name)) {
      payload.append(submitter.name, submitter.value);
    }
    return payload;
  }

  function createController(options) {
    var fetchRequest = options.fetchRequest;
    var confirmChange = options.confirmChange;
    var controls = options.controls;
    var statusNode = options.statusNode;
    var payloadFor = options.payloadFor || defaultPayload;
    var reload = options.reload;
    var inFlight = false;

    async function submit(form, submitter) {
      if (inFlight || (form.dataset && form.dataset.inFlight === "true")) {
        return { ok: false, reason: "in_flight" };
      }
      var prompt = confirmationFor(form, submitter);
      if (!confirmChange(prompt)) {
        setStatus(statusNode, "No plugin changes were made.", false);
        return { ok: false, reason: "cancelled" };
      }

      // Snapshot the submitted values before disabling controls; disabled form
      // fields are intentionally omitted by FormData.
      var payload;
      try {
        payload = payloadFor(form, submitter);
      } catch (_error) {
        setStatus(
          statusNode,
          "Plugin change failed: submitted values could not be prepared. Reload the page and try again.",
          true
        );
        return { ok: false, reason: "invalid_payload" };
      }
      inFlight = true;
      if (form.dataset) form.dataset.inFlight = "true";
      var allControls = controls();
      var disabledBefore = allControls.map(function (control) {
        return { control: control, disabled: Boolean(control.disabled) };
      });
      allControls.forEach(function (control) { control.disabled = true; });
      if (submitter) submitter.setAttribute("aria-busy", "true");
      setStatus(statusNode, "Applying the confirmed plugin change\u2026", false);

      var responseReceived = false;
      try {
        var response = await fetchRequest(form.action, {
          method: "POST",
          body: payload,
          credentials: "same-origin",
          redirect: "follow",
          headers: { "Accept": "application/json" },
        });
        responseReceived = true;
        if (!response.ok) {
          throw new Error(await failureDetail(response));
        }
        setStatus(statusNode, "Plugin change saved. Reloading current state\u2026", false);
        reload();
        return { ok: true };
      } catch (error) {
        var detail = responseReceived && error && error.message
          ? error.message
          : "Could not reach the server \u2014 check your connection and try again.";
        setStatus(statusNode, "Plugin change failed: " + detail, true);
        return { ok: false, reason: "failed", detail: detail };
      } finally {
        disabledBefore.forEach(function (entry) {
          entry.control.disabled = entry.disabled;
        });
        if (submitter) submitter.removeAttribute("aria-busy");
        if (form.dataset) delete form.dataset.inFlight;
        inFlight = false;
      }
    }

    return { submit: submit, isInFlight: function () { return inFlight; } };
  }

  function init(documentRef) {
    var doc = documentRef || global.document;
    if (!doc) return null;
    var forms = Array.prototype.slice.call(
      doc.querySelectorAll("form[data-plugin-mutation]")
    );
    if (!forms.length) return null;
    var statusNode = doc.getElementById("plugin-mutation-status");
    var controller = createController({
      fetchRequest: global.fetch.bind(global),
      confirmChange: global.confirm.bind(global),
      controls: function () {
        return forms.reduce(function (items, form) {
          return items.concat(Array.prototype.slice.call(
            form.querySelectorAll("button, input, textarea, select")
          ));
        }, []);
      },
      statusNode: statusNode,
      payloadFor: defaultPayload,
      reload: function () { global.location.reload(); },
    });
    forms.forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var submitter = event.submitter || doc.activeElement;
        if (!submitter || !submitter.getAttribute("data-plugin-action")) {
          submitter = form.querySelector("[data-plugin-action]");
        }
        controller.submit(form, submitter);
      });
    });
    return controller;
  }

  var api = {
    confirmationFor: confirmationFor,
    createController: createController,
    failureDetail: failureDetail,
    init: init,
    lines: lines,
  };
  global.LightworkPluginMutations = api;
  if (global.document) {
    if (global.document.readyState === "loading") {
      global.document.addEventListener("DOMContentLoaded", function () { init(); });
    } else {
      init();
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
