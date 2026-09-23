/*
 * Lightwork embeddable widget (roadmap: 2028 H1 distribution — "embeddable widget").
 *
 * A dependency-free floating chat button that drops onto any page and talks to
 * a Lightwork dashboard's chat endpoint. Self-hostable: it posts to YOUR
 * dashboard origin, not a hosted service.
 *
 * Usage:
 *   <script src="/widget/maverick-widget.js"
 *           data-maverick-url="https://your-dashboard.example.com"
 *           data-maverick-title="Ask Lightwork"></script>
 *
 * The dashboard must allow the embedding origin (CORS / same-origin policy).
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var BASE = (script && script.getAttribute("data-maverick-url")) || "";
  var TITLE = (script && script.getAttribute("data-maverick-title")) || "Lightwork";
  BASE = BASE.replace(/\/+$/, "");
  var busy = false;
  var unknown = false;
  var alive = true;
  var request = null;
  var pendingTimer = null;
  var pendingId = null;
  var expectedOrigin = new URL(BASE || location.origin, location.href).origin;
  var storageKey = "lightwork.pending-chat.v1:" + expectedOrigin;

  function el(tag, props, css) {
    var n = document.createElement(tag);
    if (props) Object.keys(props).forEach(function (k) {
      // Hyphenated keys (e.g. "aria-label") are attributes, not IDL
      // properties: assigning n["aria-label"] creates a dead expando and the
      // attribute is never set. Route those through setAttribute.
      if (k.indexOf("-") !== -1) { n.setAttribute(k, props[k]); }
      else { n[k] = props[k]; }
    });
    if (css) Object.keys(css).forEach(function (k) { n.style[k] = css[k]; });
    return n;
  }

  var panel = el("div", null, {
    position: "fixed", bottom: "84px", right: "20px", width: "320px",
    maxHeight: "60vh", display: "none", flexDirection: "column",
    background: "#fff", border: "1px solid #ddd", borderRadius: "12px",
    boxShadow: "0 8px 30px rgba(0,0,0,.18)", overflow: "hidden",
    font: "14px/1.4 system-ui, sans-serif", zIndex: 2147483647
  });

  var header = el("div", { textContent: TITLE }, {
    padding: "10px 14px", fontWeight: "600", background: "#111", color: "#fff"
  });
  var log = el("div", null, {
    flex: "1", padding: "12px", overflowY: "auto", color: "#111"
  });
  var form = el("form", null, { display: "flex", borderTop: "1px solid #eee" });
  var input = el("input", {
    type: "text", placeholder: "Type a goal…", required: true
  }, { flex: "1", border: "0", padding: "12px", outline: "none" });
  var send = el("button", { type: "submit", textContent: "Send" }, {
    border: "0", background: "#111", color: "#fff", padding: "0 16px",
    cursor: "pointer"
  });
  form.appendChild(input); form.appendChild(send);
  panel.appendChild(header); panel.appendChild(log); panel.appendChild(form);
  var check = el("button", { type: "button", textContent: "Check start status", hidden: true });
  panel.appendChild(check);

  var fab = el("button", { textContent: "💬", "aria-label": TITLE }, {
    position: "fixed", bottom: "20px", right: "20px", width: "52px",
    height: "52px", borderRadius: "50%", border: "0", background: "#111",
    color: "#fff", fontSize: "22px", cursor: "pointer",
    boxShadow: "0 6px 20px rgba(0,0,0,.25)", zIndex: 2147483647
  });

  function line(who, text) {
    var row = el("div", null, { margin: "6px 0" });
    row.appendChild(el("b", { textContent: who + ": " }));
    row.appendChild(document.createTextNode(text));
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  fab.addEventListener("click", function () {
    var open = panel.style.display === "flex";
    panel.style.display = open ? "none" : "flex";
    if (!open) input.focus();
  });

  function controls() {
    send.disabled = busy || unknown;
    check.hidden = !unknown;
    check.disabled = busy;
  }

  function hold() {
    unknown = true;
    line("Lightwork", "Start not confirmed. Your draft is preserved. Check start status to recover the original request; no new start will be sent.");
  }

  function accepted(id, msg) {
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error("Invalid goal receipt");
    var row = el("div", null, { margin: "6px 0" });
    row.appendChild(el("b", { textContent: "Lightwork: " }));
    row.appendChild(document.createTextNode("Goal accepted — "));
    row.appendChild(el("a", { textContent: "watch it live", href: BASE + "/chat/goal/" + id,
      target: "_blank", rel: "noopener" }));
    log.appendChild(row);
    if (localStorage.getItem(storageKey) === pendingId) localStorage.removeItem(storageKey);
    pendingId = null;
    unknown = false;
    if (msg && input.value.trim() === msg) input.value = "";
  }

  function boundedFetch(path, options, parse) {
    var active = true;
    var controller = new AbortController();
    request = controller;
    var timer;
    var timeout = new Promise(function (_, reject) {
      timer = setTimeout(function () { controller.abort(); reject(new Error("Request not confirmed")); }, 15000);
      pendingTimer = timer;
    });
    var call = fetch(BASE + path, Object.assign({}, options, { credentials: "include", signal: controller.signal }));
    return Promise.race([timeout, call.then(function (response) {
      if (!active || !alive) throw new Error("Obsolete response");
      return parse(response);
    })]).finally(function () {
      active = false;
      clearTimeout(timer);
      if (pendingTimer === timer) pendingTimer = null;
      if (request === controller) request = null;
    });
  }

  async function readReceipt(response) {
    if (!response.ok || !response.body || !response.body.getReader) throw new Error("Receipt not available");
    var reader = response.body.getReader(), decoder = new TextDecoder(), size = 0, text = "";
    try {
      while (true) {
        var part = await reader.read();
        if (part.done) break;
        size += part.value.byteLength;
        if (size > 16384) { await reader.cancel(); throw new Error("Receipt too large"); }
        text += decoder.decode(part.value, { stream: true });
      }
      var data = JSON.parse(text + decoder.decode());
      if (data.state !== "accepted" || !data.goal || typeof data.goal.status !== "string") throw new Error("Invalid receipt");
      return data.goal;
    } finally { reader.releaseLock(); }
  }

  check.addEventListener("click", function () {
    if (busy || !unknown || !pendingId || !alive) return;
    busy = true;
    controls();
    boundedFetch("/api/v1/goal-submissions/receipt?kind=chat", {
      method: "GET", redirect: "error", cache: "no-store", headers: { "Accept": "application/json", "Idempotency-Key": pendingId }
    }, readReceipt).then(function (goal) {
      if (alive) accepted(goal.id);
    }).catch(function () {
      if (alive) line("Lightwork", "No confirmed receipt is visible yet. The original request remains held; no new start was sent.");
    }).finally(function () { busy = false; if (alive) controls(); });
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var msg = input.value.trim();
    if (!msg || busy || unknown || !alive) return;
    busy = true;
    line("You", msg);
    controls();
    try {
      if (expectedOrigin !== location.origin) throw new Error("The widget requires the dashboard's own origin.");
      if (localStorage.getItem(storageKey)) {
        pendingId = localStorage.getItem(storageKey);
        hold(); busy = false; controls(); return;
      }
      pendingId = crypto.randomUUID();
      localStorage.setItem(storageKey, pendingId);
    } catch (error) {
      busy = false;
      line("Lightwork", "Start refused before sending: secure request identity could not be retained.");
      controls(); return;
    }
    // /chat/send takes multipart form fields (title required) and answers
    // with a 303 redirect to the new goal page. fetch follows the redirect,
    // so a landed response whose URL contains /chat/goal/ IS the success
    // signal; link the user straight to the live goal.
    var fd = new FormData();
    fd.append("title", msg);
    fd.append("submission_key", pendingId);
    boundedFetch("/chat/send", { method: "POST", body: fd }, function (r) {
        if (!alive) return;
        if (!r.ok) {
          var failure = new Error("Dashboard replied HTTP " + r.status + ".");
          // A followed redirect can fail AFTER goal creation. Only a direct
          // client-error response proves the start was refused.
          failure.refused = !r.redirected && r.status >= 400 && r.status < 500 && r.status !== 409;
          throw failure;
        }
        var landed = new URL(r.url, location.href);
        var expected = new URL(BASE || location.origin, location.href);
        var m = landed.pathname.match(/^\/chat\/goal\/([1-9]\d*)$/);
        if (r.redirected && landed.origin === expected.origin && !landed.search && !landed.hash && m) {
          accepted(Number(m[1]), msg);
        } else {
          throw new Error("Start not confirmed. Open the dashboard to check your session and tasks before sending again.");
        }
      })
      .catch(function (err) {
        if (!alive) return;
        if (err.refused) {
          if (localStorage.getItem(storageKey) === pendingId) localStorage.removeItem(storageKey);
          pendingId = null;
          line("Lightwork", "Start refused. " + err.message);
        } else { hold(); }
        var link = el("a", { textContent: "Open dashboard", href: BASE || location.origin,
          target: "_blank", rel: "noopener" });
        log.appendChild(link);
      })
      .finally(function () {
        busy = false;
        controls();
      });
  });

  document.body.appendChild(panel);
  document.body.appendChild(fab);
  try {
    pendingId = localStorage.getItem(storageKey);
    if (pendingId && expectedOrigin === location.origin) { hold(); controls(); }
  } catch (_) { /* A new submission will refuse before dispatch if persistence is unavailable. */ }
  window.addEventListener("pagehide", function () {
    alive = false;
    clearTimeout(pendingTimer);
    if (request) request.abort();
  });
})();
