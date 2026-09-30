"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const template = fs.readFileSync(
  path.resolve(__dirname, "../../maverick_dashboard/templates/chat_goal.html"), "utf8"
);

function inlineScript(marker) {
  const start = template.indexOf(marker);
  assert.ok(start > 0, `missing script marker ${marker}`);
  const end = template.indexOf("</script>", start);
  assert.ok(end > start);
  return template.slice(start, end).replaceAll("{{ goal.id }}", "7");
}

test("answer actions copy only the saved answer, print, and open sharing", async () => {
  const dom = new JSDOM(`<body>
    <div id="feedback"><button id="feedback-up"></button><button id="feedback-down"></button></div>
    <span id="feedback-msg"></span><span id="answer-action-msg"></span>
    <textarea id="saved-answer-source" hidden>## Saved answer\n\n- First step</textarea>
    <textarea id="edit-text">Unsaved changes</textarea>
    <button id="copy-result"></button><button id="print-result"></button>
    <button id="share-open" aria-expanded="false"></button>
    <details id="share-panel"><summary tabindex="0">Share</summary><button id="share-create">Create link</button></details>
    <div class="answer-actions"><button class="turn-copy">Copy older answer</button><span class="turn-copy-status"></span></div>
    <textarea class="turn-copy-source">An earlier answer</textarea>
  </body>`, { runScripts: "outside-only" });
  const { window } = dom;
  const copied = [];
  let prints = 0;
  Object.defineProperty(window.navigator, "clipboard", {
    value: { writeText: async (value) => { copied.push(value); } }, configurable: true
  });
  window.print = () => { prints += 1; };
  window.fetch = async () => ({ ok: true, json: async () => ({}) });
  window.document.getElementById("share-panel").scrollIntoView = () => {};
  window.eval(inlineScript("// Thumbs up/down feedback:"));
  window.eval(inlineScript("// Saved answer actions"));
  window.eval(inlineScript("// Earlier answers retain"));

  window.document.getElementById("copy-result").click();
  await Promise.resolve();
  assert.deepEqual(copied, ["## Saved answer\n\n- First step"]);
  window.document.querySelector(".turn-copy").click();
  await Promise.resolve();
  assert.deepEqual(copied, ["## Saved answer\n\n- First step", "An earlier answer"]);
  window.document.getElementById("print-result").click();
  assert.equal(prints, 1);
  window.document.getElementById("share-open").click();
  assert.equal(window.document.getElementById("share-panel").open, true);
  assert.equal(window.document.getElementById("share-open").getAttribute("aria-expanded"), "true");
  assert.equal(window.document.activeElement.id, "share-create");
  dom.window.close();
});

test("earlier Copy and Print work while a newer message is still running", async () => {
  const dom = new JSDOM(`<body><div class="answer-actions">
    <button class="turn-copy">Copy</button><button class="turn-print">Print</button><a href="/chat/goal/6/download.md?turn_id=3">Download</a>
    <span class="turn-copy-status"></span></div><textarea hidden>Earlier saved snapshot</textarea></body>`, { runScripts: "outside-only" });
  const { window } = dom;
  const copied = [];
  let prints = 0;
  Object.defineProperty(window.navigator, "clipboard", { value: { writeText: async (value) => copied.push(value) } });
  window.print = () => { prints += 1; };
  window.eval(inlineScript("// Earlier answers retain"));
  window.document.querySelector(".turn-copy").click();
  await Promise.resolve();
  assert.deepEqual(copied, ["Earlier saved snapshot"]);
  window.document.querySelector(".turn-print").click();
  assert.equal(prints, 1);
  dom.window.close();
});

test("newly finished answer actions initialize once after authoritative refresh", async () => {
  const dom = new JSDOM("<body></body>", { runScripts: "outside-only" });
  const { window } = dom;
  const copied = [];
  Object.defineProperty(window.navigator, "clipboard", { value: { writeText: async (value) => copied.push(value) } });
  window.eval(inlineScript("// Saved answer actions"));
  window.document.body.innerHTML = `<button id="copy-result">Copy</button><span id="answer-action-msg"></span>
    <textarea id="saved-answer-source" hidden>Finished answer</textarea><textarea id="edit-text">Unsaved edit</textarea>`;
  window.document.dispatchEvent(new window.CustomEvent("lightwork:answer-ready"));
  window.document.dispatchEvent(new window.CustomEvent("lightwork:answer-ready"));
  window.document.getElementById("copy-result").click();
  await Promise.resolve();
  assert.deepEqual(copied, ["Finished answer"]);
  dom.window.close();
});

function refreshHarness(response) {
  const dom = new JSDOM(`<body><textarea id="answer-input">Keep my next message</textarea>
    <div id="qa-thread">An existing clarification</div>
    <div id="answer-panel" data-goal-id="7"><div id="result-section"><div id="result">Live answer</div></div></div>
    <p id="answer-refresh-notice" hidden><span id="answer-refresh-text"></span>
      <button id="answer-refresh-retry" hidden></button><a id="answer-refresh-open" hidden></a></p></body>`, { runScripts: "outside-only" });
  dom.window.fetch = response;
  dom.window.lwMarkdown = { upgrade: () => {} };
  const start = template.indexOf("      function applyOperatePermission(");
  const end = template.indexOf("      refreshRetry.addEventListener", start);
  assert.ok(start > 0 && end > start);
  dom.window.eval(`let goalId = 7, done = true, currentStatus = 'done', answerNeedsRefresh = true;
    let answerRefreshInFlight = false, resultEl, resultSection, deliverableStaleEl, STRUCTURED, lastResult, canOperate = true;
    function syncComposer() { window.freshCanOperate = canOperate; }
    const refreshNotice = document.getElementById('answer-refresh-notice');
    const refreshText = document.getElementById('answer-refresh-text');
    const refreshRetry = document.getElementById('answer-refresh-retry');
    const refreshOpen = document.getElementById('answer-refresh-open');
    ${template.slice(start, end)}
    window.refreshAnswerPanel = refreshAnswerPanel;
    window.markRestarted = () => { done = false; currentStatus = 'pending'; };`);
  return dom;
}

const freshPanel = `<div id="answer-panel" data-goal-id="7" data-status="done" data-structured="false">
  <div id="result-section"><div id="result" data-markdown>Saved answer</div></div>
  <textarea id="saved-answer-source">Saved answer</textarea><button id="copy-result">Copy</button></div>`;

test("terminal refresh replaces only the answer, preserves drafted input, and offers retry on failure", async () => {
  let attempt = 0;
  const dom = refreshHarness(async (_url, options) => {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    attempt += 1;
    return { ok: attempt > 1, status: attempt > 1 ? 200 : 503, text: async () => freshPanel };
  });
  const { window } = dom;
  let initialized = 0;
  window.document.addEventListener("lightwork:answer-ready", () => { initialized += 1; });
  await window.refreshAnswerPanel();
  assert.equal(window.document.getElementById("answer-refresh-retry").hidden, false);
  assert.equal(window.document.getElementById("result").textContent, "Live answer");
  await window.refreshAnswerPanel();
  assert.equal(window.document.getElementById("result").textContent, "Saved answer");
  assert.equal(window.document.getElementById("answer-input").value, "Keep my next message");
  assert.equal(window.document.getElementById("qa-thread").textContent, "An existing clarification");
  assert.equal(initialized, 1);
  assert.equal(window.freshCanOperate, false);
  assert.equal(window.document.getElementById("answer-refresh-notice").hidden, true);
  dom.window.close();
});

test("an access denial clears the saved answer and a raced restart cannot replace it", async () => {
  const denied = refreshHarness(async () => ({ ok: false, status: 403 }));
  await denied.window.refreshAnswerPanel();
  assert.equal(denied.window.document.getElementById("answer-panel").textContent, "");
  assert.equal(denied.window.document.getElementById("answer-panel").hidden, true);
  assert.equal(denied.window.document.getElementById("answer-refresh-open").hidden, false);
  assert.equal(denied.window.freshCanOperate, false);
  denied.window.close();

  const login = refreshHarness(async () => ({ ok: true, status: 200, redirected: true,
    text: async () => '<form action="/login">Sign in</form>' }));
  await login.window.refreshAnswerPanel();
  assert.equal(login.window.document.getElementById("answer-panel").textContent, "");
  assert.equal(login.window.document.getElementById("answer-panel").hidden, true);
  assert.equal(login.window.document.getElementById("answer-refresh-open").hidden, false);
  assert.equal(login.window.freshCanOperate, false);
  login.window.close();

  let dom;
  dom = refreshHarness(async () => {
    dom.window.markRestarted();
    return { ok: true, status: 200, text: async () => freshPanel };
  });
  await dom.window.refreshAnswerPanel();
  assert.equal(dom.window.document.getElementById("result").textContent, "Live answer");
  dom.window.close();
});

test("a new share link has a working Copy link action", async () => {
  const dom = new JSDOM(`<body><div class="goal-share" data-goal="7">
    <button id="share-create"></button><input id="share-url" hidden><button id="share-copy" hidden></button>
    <span id="share-msg"></span><ul id="share-list"></ul>
  </div></body>`, { runScripts: "outside-only" });
  const { window } = dom;
  const copied = [];
  let requests = 0;
  Object.defineProperty(window.navigator, "clipboard", {
    value: { writeText: async (value) => { copied.push(value); } }, configurable: true
  });
  window.fetch = async () => { requests += 1; return { ok: true, json: async () => ({ id: 3, url: "http://testserver/share/token" }) }; };
  window.mvConfirm = async () => true;
  window.eval(inlineScript("// Share links: create"));
  window.document.dispatchEvent(new window.CustomEvent("lightwork:answer-ready"));
  window.document.dispatchEvent(new window.CustomEvent("lightwork:answer-ready"));

  window.document.getElementById("share-create").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  const field = window.document.getElementById("share-url");
  assert.equal(field.hidden, false);
  assert.equal(window.document.getElementById("share-copy").hidden, false);
  assert.equal(field.value, "http://testserver/share/token");
  window.document.getElementById("share-copy").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(copied, ["http://testserver/share/token"]);
  assert.match(window.document.getElementById("share-msg").textContent, /Link copied/);
  assert.equal(requests, 1);
  window.document.querySelector(".share-revoke").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(requests, 2);
  assert.equal(field.value, "");
  assert.equal(field.hidden, true);
  assert.equal(window.document.getElementById("share-copy").hidden, true);
  assert.match(window.document.getElementById("share-msg").textContent, /Link revoked/);
  dom.window.close();
});

test("a local-only share link explains its reach before and after copying", async () => {
  const dom = new JSDOM(`<body><div class="goal-share" data-goal="7">
    <button id="share-create"></button><input id="share-url" hidden><button id="share-copy" hidden></button>
    <span id="share-msg"></span><ul id="share-list"></ul></div></body>`, { runScripts: "outside-only" });
  const { window } = dom;
  Object.defineProperty(window.navigator, "clipboard", { value: { writeText: async () => {} } });
  window.fetch = async () => ({ ok: true, json: async () => ({ id: 3, url: "http://localhost/share/token", local_only: true }) });
  window.eval(inlineScript("// Share links: create"));
  window.document.getElementById("share-create").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(window.document.getElementById("share-msg").textContent, /limited to this computer or its private network/);
  window.document.getElementById("share-copy").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(window.document.getElementById("share-msg").textContent, /limited to this computer or its private network/);
  dom.window.close();
});

test("existing share links can still be revoked without a Create link control", async () => {
  const dom = new JSDOM(`<body><div class="goal-share" data-goal="7">
    <input id="share-url" hidden><button id="share-copy" hidden></button><span id="share-msg"></span>
    <ul id="share-list"><li data-id="3">Link <button class="share-revoke" data-id="3">Revoke</button></li></ul></div></body>`, { runScripts: "outside-only" });
  const { window } = dom;
  window.mvConfirm = async () => true;
  const requests = [];
  window.fetch = async (url) => { requests.push(url); return { ok: true }; };
  window.eval(inlineScript("// Share links: create"));
  window.document.querySelector(".share-revoke").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(requests, ["/api/v1/goals/7/share/3/revoke"]);
  assert.equal(window.document.querySelector("#share-list li"), null);
  dom.window.close();
});

function voiceHarness(text, allowed = true) {
  const dom = new JSDOM(`<body><div id="answer-panel" data-can-operate="${allowed}">
    <div id="result"></div><button id="read-aloud" data-voice-only="0">Read aloud</button>
    <span id="answer-action-msg"></span></div></body>`, { runScripts: "outside-only" });
  dom.window.document.getElementById("result").textContent = text;
  dom.window.eval(inlineScript("// Read-aloud controls"));
  return dom;
}

test("read aloud can stop a pending request without starting late audio", async () => {
  const dom = voiceHarness("An answer");
  const { window } = dom;
  let resolveResponse;
  let signal;
  let audioCreated = 0;
  window.fetch = async (_url, options) => {
    signal = options.signal;
    return new Promise((resolve) => { resolveResponse = resolve; });
  };
  window.Audio = function () { audioCreated += 1; };
  const button = window.document.getElementById("read-aloud");
  button.click();
  assert.equal(button.textContent, "Stop reading");
  button.click();
  assert.equal(signal.aborted, true);
  resolveResponse({ ok: true, blob: async () => new window.Blob(["audio"]) });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(audioCreated, 0);
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.match(window.document.getElementById("answer-action-msg").textContent, /Reading stopped/);
  dom.window.close();
});

test("long-answer reading states its limit and releases audio after finishing", async () => {
  const dom = voiceHarness("a".repeat(4500));
  const { window } = dom;
  let posted;
  const revoked = [];
  window.fetch = async (_url, options) => {
    posted = JSON.parse(options.body);
    return { ok: true, blob: async () => new window.Blob(["audio"]) };
  };
  window.URL.createObjectURL = () => "blob:voice";
  window.URL.revokeObjectURL = (url) => revoked.push(url);
  window.Audio = function () {
    window.currentAudio = this;
    this.play = async () => {};
    this.pause = () => {};
  };
  window.document.getElementById("read-aloud").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(posted.text.length, 4096);
  assert.match(window.document.getElementById("answer-action-msg").textContent, /first part/);
  window.currentAudio.onended();
  assert.deepEqual(revoked, ["blob:voice"]);
  assert.match(window.document.getElementById("answer-action-msg").textContent, /Finished reading the first part/);
  dom.window.close();
});

test("read aloud reports network failure and a read-only account avoids a paid voice request", async () => {
  const dom = voiceHarness("An answer");
  dom.window.fetch = async () => { throw new Error("offline"); };
  dom.window.document.getElementById("read-aloud").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(dom.window.document.getElementById("answer-action-msg").textContent, /Could not read/);
  assert.equal(dom.window.document.getElementById("read-aloud").getAttribute("aria-pressed"), "false");
  dom.window.close();

  const readonly = voiceHarness("An answer", false);
  let requests = 0;
  readonly.window.fetch = async () => { requests += 1; };
  readonly.window.document.getElementById("read-aloud").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(requests, 0);
  assert.match(readonly.window.document.getElementById("answer-action-msg").textContent, /unavailable here/);
  readonly.window.close();
});

test("browser speech fallback has a working Stop reading action", async () => {
  const dom = voiceHarness("An answer");
  const { window } = dom;
  let stopped = 0;
  window.fetch = async () => ({ ok: false });
  window.lwVoice = { speak: () => true, stop: () => { stopped += 1; } };
  window.speechSynthesis = { speaking: true, pending: false };
  const button = window.document.getElementById("read-aloud");
  button.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(button.textContent, "Stop reading");
  button.click();
  assert.equal(stopped, 1);
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.match(window.document.getElementById("answer-action-msg").textContent, /Reading stopped/);
  dom.window.close();
});
