/* Knowledge library panel: collections, starter content and rebuilding the
   search index -- previously command-line only. */
(function () {
  "use strict";
  var panel = document.getElementById("ka-library");
  if (!panel) return;
  var API = "/api/v1/knowledge/library";
  var canAdmin = panel.dataset.canAdmin === "true";
  var statusEl = document.getElementById("ka-library-status");
  var tbody = document.getElementById("ka-library-collections");
  var starters = document.getElementById("ka-library-starters");

  function el(tag, text, cls) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function human(value) {
    if (typeof window.mvHuman === "function") return window.mvHuman(value);
    var text = String(value || "").replace(/[_.-]+/g, " ").trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }
  function plain(status, action) {
    if (typeof window.mvErrorText === "function") return window.mvErrorText({ status: status }, action);
    return "Could not " + action + ". Please try again.";
  }
  async function call(method, path, body, action) {
    var response;
    try {
      response = await fetch(API + path, {
        method: method, credentials: "same-origin",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body)
      });
    } catch (error) {
      throw new Error(typeof window.mvErrorText === "function" ? window.mvErrorText(error, action) : "Could not " + action + ".");
    }
    var payload = {};
    try { payload = await response.json(); } catch (_error) { payload = null; }
    if (!response.ok || !payload || typeof payload !== "object") {
      throw new Error(response.ok ? "Could not " + action + ". Lightwork returned something unexpected." : plain(response.status, action));
    }
    return payload;
  }
  function confirmed(message, okText) {
    if (typeof window.mvConfirm === "function") return window.mvConfirm(message, { okText: okText });
    return Promise.resolve(false);
  }
  function button(label, handler, cls) {
    var b = el("button", label, cls || "btn");
    b.type = "button";
    b.addEventListener("click", function () {
      if (b.disabled) return;
      b.disabled = true;
      Promise.resolve().then(handler).catch(function (error) {
        statusEl.textContent = error && error.message ? error.message : "Something went wrong. Please try again.";
      }).then(function () { b.disabled = false; });
    });
    return b;
  }
  async function reindex(name) {
    var ok = await confirmed("Rebuild the search index for " + human(name) + "? This can take a while and may use your AI provider, which can cost money.", "Rebuild");
    if (!ok) return;
    statusEl.textContent = "Rebuilding the search index…";
    var result = await call("POST", "/collections/" + encodeURIComponent(name) + "/reindex", undefined, "rebuild the search index");
    statusEl.textContent = "Rebuilt the search index for " + human(name) + " (" + result.chunks + " pieces of text).";
    await load();
  }
  async function seed(collection, title) {
    var ok = await confirmed("Load the starter content “" + title + "”? Loading again replaces the earlier copy.", "Load");
    if (!ok) return;
    statusEl.textContent = "Loading starter content…";
    var result = await call("POST", "/seed", { collection: collection }, "load the starter content");
    statusEl.textContent = "Loaded " + result.chunks + " pieces of text.";
    await load();
  }
  async function load() {
    var body;
    try {
      body = await call("GET", "", undefined, "load the knowledge library");
    } catch (error) {
      statusEl.textContent = error.message;
      return;
    }
    tbody.textContent = "";
    starters.textContent = "";
    if (!body.enabled) {
      statusEl.textContent = "The knowledge library is turned off. An administrator can turn it on in the setup wizard.";
      return;
    }
    var collections = Array.isArray(body.collections) ? body.collections : [];
    statusEl.textContent = collections.length ? "" : "No collections yet." + (canAdmin ? " Load some starter content below to begin." : "");
    collections.forEach(function (item) {
      var tr = el("tr");
      var th = el("th", human(item.name)); th.scope = "row"; tr.appendChild(th);
      tr.appendChild(el("td", item.chunks === null || item.chunks === undefined ? "—" : String(item.chunks)));
      var actions = el("td");
      if (canAdmin) {
        var rebuild = button("Rebuild search index", function () { return reindex(item.name); }, "btn btn--ghost");
        rebuild.setAttribute("aria-label", "Rebuild the search index for " + human(item.name));
        actions.appendChild(rebuild);
      }
      tr.appendChild(actions);
      tbody.appendChild(tr);
    });
    (Array.isArray(body.starter_content) ? body.starter_content : []).forEach(function (item) {
      var li = el("li", item.title + " (" + item.documents + " documents)" + (item.loaded ? " — loaded" : "") + " ");
      if (canAdmin) {
        var load = button(item.loaded ? "Load again" : "Load", function () { return seed(item.collection, item.title); }, "btn btn--ghost");
        load.setAttribute("aria-label", (item.loaded ? "Load again: " : "Load: ") + item.title);
        li.appendChild(load);
      }
      starters.appendChild(li);
    });
  }
  document.getElementById("ka-library-refresh").addEventListener("click", load);
  load();
})();
