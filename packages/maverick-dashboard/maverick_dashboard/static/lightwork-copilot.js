/* Shared, page-aware Lightwork Copilot. The server owns the page knowledge;
   this client renders it with text-only DOM APIs and keeps the helper read-only. */
(function () {
  "use strict";

  var fab = document.getElementById("mv-copilot-fab");
  var panel = document.getElementById("mv-copilot");
  var closeButton = document.getElementById("mv-copilot-close");
  var tips = document.getElementById("mv-copilot-tips");
  var prompts = document.getElementById("mv-copilot-prompts");
  var log = document.getElementById("mv-copilot-log");
  var form = document.getElementById("mv-copilot-form");
  var input = document.getElementById("mv-copilot-input");
  var note = document.getElementById("mv-copilot-note");
  var providerSetup = document.getElementById("mv-copilot-provider-setup");
  var sendButton = document.getElementById("mv-copilot-send");
  if (!fab || !panel || !closeButton || !tips || !prompts || !log ||
      !form || !input || !note || !sendButton || !providerSetup) return;

  var history = [];
  var opener = null;
  var contextLoaded = false;
  var fallback = {
    title: "Lightwork",
    summary: "Use this helper to understand the current workspace and its authority boundary.",
    boundaries: [
      "Copilot guidance is read-only and never grants authority or executes an action.",
      "Local status is not proof of live provider or customer acceptance."
    ],
    next_steps: ["Read the page readiness state before running an operation."],
    suggested_questions: [
      "What can I do on this page?",
      "What should I verify before I continue?"
    ]
  };

  function clear(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function addMessage(role, text) {
    var message = document.createElement("div");
    message.className = "mv-copilot__msg mv-copilot__msg--" + role;
    message.textContent = String(text);
    log.appendChild(message);
    log.scrollTop = log.scrollHeight;
  }

  function renderContext(context) {
    var selected = context && typeof context === "object" ? context : fallback;
    clear(tips);
    (selected.boundaries || fallback.boundaries).slice(0, 3).forEach(function (text) {
      var item = document.createElement("li");
      item.textContent = String(text);
      tips.appendChild(item);
    });
    clear(prompts);
    (selected.suggested_questions || fallback.suggested_questions).slice(0, 3)
      .forEach(function (question) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "mv-copilot__prompt";
        button.textContent = String(question);
        button.addEventListener("click", function () {
          input.value = String(question);
          input.focus();
        });
        prompts.appendChild(button);
      });
  }

  function renderProviderSetup(payload) {
    var setup = payload && payload.provider_setup;
    var href = setup && typeof setup.href === "string" ? setup.href : "";
    var label = setup && typeof setup.label === "string" ? setup.label : "";
    var allowed = href === "/start" || href === "/settings#settings-integrations";
    providerSetup.hidden = !allowed;
    if (!allowed) return;
    providerSetup.setAttribute("href", href);
    providerSetup.textContent = label || "Open the in-app setup guide";
  }

  async function loadContext() {
    if (contextLoaded) return;
    contextLoaded = true;
    renderContext(fallback);
    try {
      var response = await fetch(
        "/api/v1/copilot/context?page=" + encodeURIComponent(window.location.pathname),
        {headers: {Accept: "application/json"}, credentials: "same-origin", cache: "no-store"}
      );
      if (!response.ok) throw new Error("context unavailable");
      var payload = await response.json();
      renderContext(payload);
      renderProviderSetup(payload);
    } catch (_error) {
      note.textContent = "Using the local page guide.";
    }
  }

  function open() {
    opener = document.activeElement;
    panel.hidden = false;
    fab.setAttribute("aria-expanded", "true");
    loadContext();
    input.focus();
  }

  function close() {
    panel.hidden = true;
    fab.setAttribute("aria-expanded", "false");
    if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
  }

  fab.addEventListener("click", function () { panel.hidden ? open() : close(); });
  closeButton.addEventListener("click", close);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.hidden) {
      event.preventDefault();
      close();
    }
  });

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    var message = (input.value || "").trim();
    if (!message) return;
    input.value = "";
    note.textContent = "";
    addMessage("user", message);
    sendButton.disabled = true;
    sendButton.setAttribute("aria-busy", "true");
    try {
      var response = await fetch("/api/v1/copilot", {
        method: "POST",
        headers: {"Content-Type": "application/json", Accept: "application/json"},
        credentials: "same-origin",
        body: JSON.stringify({
          message: message,
          page: window.location.pathname,
          history: history.slice(-6)
        })
      });
      var payload;
      try { payload = await response.json(); }
      catch (_error) { payload = {detail: "The server returned an unreadable response."}; }
      if (!response.ok || !payload.reply) {
        throw new Error(typeof payload.detail === "string" ? payload.detail : "Copilot is unavailable.");
      }
      addMessage("bot", payload.reply);
      // Only prior user questions return to the server. Model replies stay in
      // this browser log and can never be replayed as caller-supplied assistant
      // authority on a later request.
      history.push({role: "user", content: message});
      history = history.slice(-6);
      if (payload.page_context) renderContext(payload.page_context);
      renderProviderSetup(payload);
      if (payload.mode === "built_in_fallback") {
        note.textContent = "Model provider unavailable — using the built-in page guide.";
      } else if (payload.mode === "built_in") {
        note.textContent = "Built-in page guide — no model provider required.";
      } else {
        note.textContent = payload.warning ||
          "Model-generated guidance is unverified — confirm it against live status and retained evidence.";
      }
    } catch (error) {
      note.textContent = "Copilot could not answer: " + String(error.message || error);
    } finally {
      sendButton.disabled = false;
      sendButton.removeAttribute("aria-busy");
      input.focus();
    }
  });
}());
