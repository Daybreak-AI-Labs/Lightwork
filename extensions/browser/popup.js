// Lightwork popup chat. Thin UI: every network call is delegated to the
// background service worker (see background.js), which talks only to the
// local dashboard. Plain JS, no build step.

const $ = (id) => document.getElementById(id);

let pollTimer = null;
let scope = "";
let watchState = null;
let generation = 0;
let polling = null;
let submitting = false;
let held = false;
let alive = true;
let activeRequestId = null;
let serial = 0;
const session = chrome.storage.session || chrome.storage.local;
let persistence = Promise.resolve();

function controls() {
  $("send").disabled = $("send-page").disabled = !scope || submitting || held;
  $("clear-pending").hidden = !held;
}

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
  if (activeRequestId) chrome.runtime.sendMessage({ type: "cancel", requestId: activeRequestId }, () => {});
  activeRequestId = null;
}

function saveWatch(value, epoch) {
  persistence = persistence.catch(() => {}).then(async () => {
    if (epoch === generation && alive) await session.set({ activeWatch: value });
  });
  return persistence;
}

function renderWatch() {
  $("log").textContent = "";
  if (!watchState) return;
  if (watchState.title) appendEvent("you", watchState.title);
  for (const event of watchState.events) appendEvent(event.agent, event.content);
  if (watchState.complete && watchState.result) appendEvent("result", watchState.result);
  setStatus("goal #" + watchState.goalId + " — " + watchState.status +
    (watchState.hasMore ? " (loading remaining events)" : "") +
    (watchState.trimmed ? " · bounded history; open dashboard for full history" : ""));
}

function setStatus(text, isError) {
  const el = $("status");
  el.textContent = text || "";
  el.className = isError ? "err" : "hint";
}

function appendEvent(agent, content) {
  const p = document.createElement("p");
  p.className = "evt";
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = agent + ": ";
  p.appendChild(who);
  // textContent only — agent output is untrusted text, never HTML.
  p.appendChild(document.createTextNode(content));
  $("log").appendChild(p);
  $("log").scrollTop = $("log").scrollHeight;
}

function ask(msg) {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => finish({ ok: false, kind: msg.type === "chat" ? "unknown" : "transient",
      error: "Client request not confirmed. Check the dashboard before retrying a start." }), 17000);
    function finish(value) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    }
    chrome.runtime.sendMessage(msg, (resp) => {
      if (chrome.runtime.lastError) {
        finish({ ok: false, kind: msg.type === "chat" ? "unknown" : "transient", error: chrome.runtime.lastError.message });
      } else {
        finish(resp || { ok: false, kind: "unknown", error: "No response." });
      }
    });
  });
}

async function poll() {
  if (!alive || !watchState || watchState.complete || polling === generation) return;
  const epoch = generation;
  polling = epoch;
  let delay = 2000;
  try {
    for (let page = 0; page < 10; page++) {
      activeRequestId = "popup-" + epoch + "-" + (++serial);
      const resp = await ask({ type: "events", scope, goalId: watchState.goalId,
        since: watchState.cursor, requestId: activeRequestId });
      if (epoch !== generation || !alive) return;
      activeRequestId = null;
      if (!resp.ok || resp.scope !== scope) {
        setStatus(resp.error || "Dashboard connection changed.", true);
        if (["access", "context", "protocol", "configuration", "refused"].includes(resp.kind) || (resp.ok && resp.scope !== scope)) {
          watchState = null;
          $("log").textContent = "";
          await saveWatch(null, epoch);
        }
        return;
      }
      watchState = LightworkClientProtocol.page(watchState, resp.data);
      renderWatch();
      await saveWatch(watchState, epoch);
      if (epoch !== generation || !alive || !watchState || watchState.complete || !watchState.hasMore) return;
      delay = 0; // Yield after ten pages; never drop a terminal run's backlog.
    }
  } catch (error) {
    if (epoch === generation && alive) {
      setStatus(error.message, true);
      watchState = null;
      await saveWatch(null, epoch);
    }
  } finally {
    if (polling === epoch) polling = null;
    if (epoch === generation && alive && watchState && !watchState.complete) pollTimer = setTimeout(poll, delay);
  }
}

async function startGoal(title, description) {
  if (submitting || held || !scope || !alive) return;
  submitting = true;
  controls();
  const epoch = generation;
  setStatus("starting…");
  try {
    const resp = await ask({ type: "chat", scope, title, description });
    if (epoch !== generation || !alive) return;
    if (!resp.ok || resp.scope !== scope) {
      held = ["unknown", "pending"].includes(resp.kind);
      setStatus(resp.error || "Dashboard connection changed; start not confirmed.", true);
      return;
    }
    stopPolling();
    generation++;
    watchState = { scope, goalId: resp.goal.id, cursor: 0, events: [], status: resp.goal.status,
      complete: false, hasMore: false, result: "", title };
    await saveWatch(watchState, generation);
    if ($("prompt").value.trim() === description) $("prompt").value = "";
    renderWatch();
    // An obsolete poll still owns the pending flag until its bounded request
    // returns; scheduling here prevents that old finally block reviving it.
    pollTimer = setTimeout(poll, 0);
  } finally {
    submitting = false;
    controls();
  }
}

async function sendChat() {
  const text = $("prompt").value.trim();
  if (!text) return;
  // First line becomes the goal title; the full text is the brief.
  const title = text.split("\n", 1)[0].slice(0, 200);
  await startGoal(title, text);
}

// Render the bounded, observe-only accessibility/DOM snapshot the content
// script returns into compact lines for the goal brief. Read-only formatting:
// the structured context is already capped by content.js; we keep it tight.
function renderStructured(structured) {
  if (!structured) return "";
  let out = "\n\n--- structured page context (observe-only) ---";
  if (structured.lang) out += "\nlang: " + structured.lang;
  const c = structured.counts || {};
  out += "\ncounts: " + (c.elements || 0) + " interactive, " + (c.landmarks || 0) + " landmarks";
  if (structured.truncated) out += " (truncated)";
  const landmarks = structured.landmarks || [];
  if (landmarks.length) {
    out += "\nlandmarks/headings:";
    for (const l of landmarks) {
      out += "\n  - " + l.tag + (l.role && l.role !== l.tag ? " [" + l.role + "]" : "") +
             (l.name ? ": " + l.name : "");
    }
  }
  const elements = structured.elements || [];
  if (elements.length) {
    out += "\ninteractive elements:";
    for (const e of elements) {
      let line = "\n  - " + (e.role || e.tag);
      if (e.name) line += ' "' + e.name + '"';
      if (e.type) line += " type=" + e.type;
      if (e.value) line += " value=" + JSON.stringify(e.value);
      if (e.disabled) line += " (disabled)";
      if (e.selector) line += "  @ " + e.selector;
      out += line;
    }
  }
  return out;
}

// "Send this page": ask the content script for {title, url, selection,
// structured} and ship it as goal context alongside whatever is in the chat
// box. Same user-triggered, localhost-only, bearer-gated path as before.
async function sendPage() {
  if (submitting || held || !scope) return;
  const epoch = generation;
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs && tabs[0];
  if (!tab || tab.id === undefined) {
    setStatus("no active tab", true);
    return;
  }
  chrome.tabs.sendMessage(tab.id, { type: "getPageContext" }, async (ctx) => {
    if (epoch !== generation || !alive || submitting || held) return;
    if (chrome.runtime.lastError || !ctx) {
      setStatus("cannot read this page (browser-internal pages are off limits)", true);
      return;
    }
    const ask_ = $("prompt").value.trim();
    const title = (ask_ ? ask_.split("\n", 1)[0] : "Look at page: " + ctx.title).slice(0, 200);
    let description = (ask_ || "Review this page.") + "\n\n--- page context ---";
    description += "\ntitle: " + ctx.title + "\nurl: " + ctx.url;
    if (ctx.selection) description += "\nselection:\n" + ctx.selection;
    description += renderStructured(ctx.structured);
    await startGoal(title, description);
  });
}

async function loadSettings() {
  const epoch = generation;
  const s = await chrome.storage.local.get({ base: "http://127.0.0.1:8765", token: "" });
  if (epoch !== generation || !alive) return;
  $("base").value = s.base;
  $("token").value = s.token;
  $("dashboard").href = /^http:\/\/127\.0\.0\.1(:\d+)?$/.test(s.base) ? s.base : "#";
  const identity = await ask({ type: "identity" });
  if (epoch !== generation || !alive) return;
  if (!identity.ok) { setStatus(identity.error, true); return; }
  scope = identity.scope;
  held = Boolean(identity.pending);
  const saved = (await session.get("activeWatch")).activeWatch;
  if (epoch !== generation || !alive) return;
  watchState = saved && saved.scope === scope && Number.isSafeInteger(saved.goalId) &&
    Number.isSafeInteger(saved.cursor) && Array.isArray(saved.events) && saved.events.length <= 1000 ? saved : null;
  if (watchState) watchState = { ...watchState, complete: false, status: "checking current access…" };
  renderWatch();
  if (held) setStatus("A previous start is unconfirmed. Check start status to recover its original goal.", true);
  controls();
  if (watchState && !watchState.complete) pollTimer = setTimeout(poll, 0);
}

async function saveSettings() {
  let base = $("base").value.trim() || "http://127.0.0.1:8765";
  // Local-only by policy AND by manifest: host_permissions stop anything
  // beyond 127.0.0.1, so refuse other hosts here with a clear message.
  if (!/^http:\/\/127\.0\.0\.1(:\d+)?$/.test(base.replace(/\/+$/, ""))) {
    setStatus("dashboard URL must be http://127.0.0.1[:port]", true);
    return;
  }
  base = base.replace(/\/+$/, "");
  generation++;
  stopPolling();
  scope = "";
  watchState = null;
  $("log").textContent = "";
  controls();
  await chrome.storage.local.set({ base: base, token: $("token").value.trim() });
  await loadSettings();
}

document.addEventListener("DOMContentLoaded", () => {
  controls();
  loadSettings();
  $("send").addEventListener("click", sendChat);
  $("send-page").addEventListener("click", sendPage);
  $("save").addEventListener("click", saveSettings);
  $("clear-pending").addEventListener("click", async () => {
    if (!held || submitting) return;
    const epoch = generation;
    submitting = true;
    controls();
    try {
      const response = await ask({ type: "reconcile", scope });
      if (!alive || epoch !== generation) return;
      if (!response.ok || response.scope !== scope || !response.goal) {
        setStatus("Start remains unconfirmed. " + (response.error || "No receipt is visible yet.") + " No new request was sent.", true);
        return;
      }
      held = false;
      await loadSettings();
    } finally { submitting = false; controls(); }
  });
  $("prompt").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) sendChat();
  });
});

window.addEventListener("pagehide", () => { alive = false; generation++; stopPolling(); });
