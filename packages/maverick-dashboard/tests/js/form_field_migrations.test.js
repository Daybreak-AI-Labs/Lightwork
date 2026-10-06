"use strict";
// The forms moved onto the shared error pattern (mvFieldErrors): each error
// is tied to its field, the summary takes focus, and values are preserved.
// Pages: Settings spending per task, Spending limits, Automations "Create a
// schedule". Run with: node --test tests/js/form_field_migrations.test.js
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, "../../maverick_dashboard");
const ui = fs.readFileSync(path.join(root, "static/lightwork-ui.js"), "utf8");
const template = name => fs.readFileSync(path.join(root, "templates", name), "utf8").replace(/\r\n/g, "\n");
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

function page(url, body, scripts, setup) {
  const dom = new JSDOM(`<title>Page · Lightwork</title><div id="mv-toasts"></div><div id="mv-toasts-alert"></div>
    <dialog id="mv-confirm"><p id="mv-confirm-msg"></p><button id="mv-confirm-cancel">Cancel</button><button id="mv-confirm-ok">Confirm</button></dialog>
    <main id="content">${body}</main>`, {url, runScripts: "outside-only"});
  const w = dom.window;
  w.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
  if (setup) setup(w);
  for (const script of scripts) w.eval(script);
  w.eval(ui);
  return w;
}
function inlineScript(name, marker) {
  const source = template(name), at = source.indexOf(marker);
  const start = source.lastIndexOf("<script>", at) + "<script>".length;
  return source.slice(start, source.indexOf("</script>", at));
}
const summaryLinks = doc => [...doc.querySelectorAll("[data-mv-error-summary]:not([hidden]) a")].map(a => [a.getAttribute("href"), a.textContent]);

test("Settings: 'Stop a task when it has spent' with no amount is caught next to the amount", () => {
  const w = page("https://lightwork.test/settings", `<form method="post" action="/settings/budget" id="settings-budget-form" data-mv-validate data-mv-keep="settings-budget">
      <div class="mv-error-summary" id="settings-budget-errors" data-mv-error-summary tabindex="-1" hidden></div>
      <fieldset><legend>Stop tasks that go over a limit</legend>
      <label><input type="radio" name="mode" value="disabled"> No limit for each task</label>
      <label><input type="radio" name="mode" value="configured" checked> Stop a task when it has spent</label>
      <label><span>Amount per task (US dollars)</span>
        <input class="input" id="settings-task-budget" type="number" name="max_dollars" min="0.01" step="0.01" aria-describedby="task-limit-help"></label>
      <p class="mv-form__err" id="settings-task-budget-error" role="alert" hidden></p></fieldset>
      <p id="task-limit-help">Help</p><button type="submit">Save task limit</button></form>`,
  [inlineScript("settings.html", "getElementById('settings-task-budget-error')")]);
  const doc = w.document, form = doc.getElementById("settings-budget-form"), field = doc.getElementById("settings-task-budget");
  let sent = 0;
  form.addEventListener("submit", event => { event.preventDefault(); sent += 1; });
  form.requestSubmit();
  assert.equal(sent, 0);
  assert.deepEqual(summaryLinks(doc), [["#settings-task-budget", "Enter the amount per task in US dollars, or choose another option."]]);
  assert.equal(doc.activeElement.id, "settings-budget-errors");
  assert.equal(field.getAttribute("aria-invalid"), "true");
  assert.equal(field.getAttribute("aria-describedby"), "settings-task-budget-error task-limit-help");
  // Stream I's wording still wins for a value the browser rejects.
  field.value = "7.505";
  form.requestSubmit();
  assert.match(summaryLinks(doc)[0][1], /two decimal places/);
  assert.equal(field.value, "7.505");
  assert.equal(doc.querySelectorAll(".mv-form__err:not([hidden])").length, 1);
  // Choosing no limit needs no amount.
  form.querySelector('input[value="disabled"]').checked = true;
  field.value = "";
  form.requestSubmit();
  assert.equal(sent, 1);
  w.close();
});

const LIMIT_FORM = (id, current) => `<form method="post" action="/spending-limits/${id}" data-limit-form="the workspace" id="limit-${id}" data-mv-validate data-mv-keep="limits-${id}" data-limit-amount="${current}">
  <div class="mv-error-summary" id="limit-${id}-errors" data-mv-error-summary tabindex="-1" hidden></div>
  <fieldset><legend>Choose one</legend>
  <label><input type="radio" name="choice" value="none"> No limit</label>
  <label><input type="radio" name="choice" value="limit" checked> Limit spending to
    <input type="number" name="amount" min="0.01" step="0.01" aria-label="Workspace limit amount in dollars" id="limit-${id}-amount"
      data-error-range="Enter an amount of at least $0.01, with at most two decimal places. For example: 250.00."></label>
  <select name="period"><option value="month" selected>per month</option></select></fieldset>
  <button type="submit">Save workspace limit</button></form>`;
const limitsScript = () => inlineScript("spending_limits.html", "form[data-limit-form]");

test("Spending limits: a limit with no amount, or a bad amount, is explained and nothing is cleared", () => {
  const w = page("https://lightwork.test/spending-limits", LIMIT_FORM("workspace", ""), [limitsScript()],
    win => { win.mvMoney = v => "$" + Number(v).toFixed(2); });
  const doc = w.document, form = doc.getElementById("limit-workspace"), amount = doc.getElementById("limit-workspace-amount");
  let sent = 0;
  form.addEventListener("submit", event => { event.preventDefault(); sent += 1; });
  form.requestSubmit();
  assert.deepEqual(summaryLinks(doc), [["#limit-workspace-amount", "Enter the limit in US dollars, or choose No limit."]]);
  assert.equal(doc.activeElement.id, "limit-workspace-errors");
  amount.value = "0";
  form.requestSubmit();
  assert.match(summaryLinks(doc)[0][1], /at least \$0\.01/);
  assert.equal(amount.value, "0");
  assert.equal(amount.getAttribute("aria-invalid"), "true");
  assert.match(amount.getAttribute("aria-describedby"), /limit-workspace-amount-error/);
  assert.equal(sent, 0);
  w.close();
});

test("Spending limits: raising or removing a limit says the amount and the button names the action", async () => {
  const w = page("https://lightwork.test/spending-limits", LIMIT_FORM("workspace", "100"), [limitsScript()],
    win => { win.mvMoney = v => "$" + Number(v).toFixed(2); });
  const doc = w.document, form = doc.getElementById("limit-workspace");
  let asked = null;
  w.mvConfirm = (text, opts) => { asked = {text, ok: opts.okText}; return Promise.resolve(false); };
  form.addEventListener("submit", event => event.preventDefault());
  doc.getElementById("limit-workspace-amount").value = "250";
  form.requestSubmit();
  await tick();
  assert.match(asked.text, /from \$100\.00 to \$250\.00/);
  assert.equal(asked.ok, "Raise limit to $250.00");
  form.querySelector('input[value="none"]').checked = true;
  form.requestSubmit();
  await tick();
  assert.match(asked.text, /Remove the \$100\.00 spending limit for the workspace\?/);
  assert.equal(asked.ok, "Remove limit");
  // Lowering a limit is reversible and costs nothing: no question.
  asked = null;
  form.querySelector('input[value="limit"]').checked = true;
  doc.getElementById("limit-workspace-amount").value = "50";
  form.requestSubmit();
  await tick();
  assert.equal(asked, null);
  w.close();
});

function scheduleForm() {
  const source = template("automations.html");
  const start = source.indexOf('<details id="auto-new-sched"');
  return source.slice(start, source.indexOf("</details>\n  </section>", start) + "</details>".length)
    .replace(/{%[^%]*%}/g, "").replace(/{{[^}]*}}/g, "1");
}
function schedulePage(responses, setup) {
  const calls = [];
  return {calls, w: page("https://lightwork.test/automations", scheduleForm(), [], win => {
    win.fetch = async (url, options) => {
      calls.push({url, options});
      const next = typeof responses === "function" ? responses(url) : responses.shift();
      if (next instanceof Error) throw next;
      return {ok: next.status === undefined || next.status < 400, status: next.status || 200,
        json: async () => next.body || next, clone() { return this; }};
    };
    if (setup) setup(win);
  })};
}
const OPTIONS = {workflows: [{name: "weekly-report", title: "Weekly report", params: ["team"]}], tasks: [], zones: ["UTC"]};
const picker = fs.readFileSync(path.join(root, "static/lightwork-schedule-picker.js"), "utf8");

test("Automations: create a schedule ties each missing answer to its field and keeps them on a failed request", async () => {
  const {w, calls} = schedulePage(url => url.includes("options") ? OPTIONS : url.includes("preview") ? {next_runs: []} : new TypeError("Failed to fetch"));
  w.eval(picker);
  const doc = w.document, form = doc.getElementById("auto-new-form");
  doc.getElementById("auto-new-sched").open = true;
  doc.getElementById("auto-new-sched").dispatchEvent(new w.Event("toggle"));
  await tick(); await tick();
  form.requestSubmit();
  assert.deepEqual(summaryLinks(doc), [["#auto-new-workflow", "Choose the workflow to run."]]);
  assert.equal(doc.activeElement, doc.querySelector("[data-mv-error-summary]"));
  const workflow = doc.getElementById("auto-new-workflow");
  workflow.value = "weekly-report";
  workflow.dispatchEvent(new w.Event("change", {bubbles: true}));
  form.requestSubmit();
  assert.deepEqual(summaryLinks(doc), [["#auto-new-param-0", "Enter team for this workflow."]]);
  assert.equal(workflow.hasAttribute("aria-invalid"), false);
  doc.getElementById("auto-new-param-0").value = "Support";
  doc.getElementById("auto-new-title").value = "Monday digest";
  form.requestSubmit();
  await tick(); await tick();
  assert.ok(calls.some(c => c.url.includes("/schedules/add")));
  const summary = doc.querySelector("[data-mv-error-summary]:not([hidden])");
  assert.match(summary.textContent, /could not be reached/);
  assert.match(summary.textContent, /What you entered is still here/);
  assert.equal(doc.getElementById("auto-new-param-0").value, "Support");
  assert.equal(doc.getElementById("auto-new-title").value, "Monday digest");
  w.close();
});

test("Automations: what was typed comes back after a reload and is forgotten once the schedule is created", async () => {
  const stored = JSON.stringify({at: Date.now(), values: {what: ["workflow"], "auto-new-workflow": "weekly-report", "auto-new-param-0": "Support", "auto-new-title": "Monday digest"}});
  const {w} = schedulePage(url => url.includes("options") ? OPTIONS : url.includes("preview") ? {next_runs: []} : {next_run: 1790000000},
    win => win.sessionStorage.setItem("mv-keep:automation-schedule", stored));
  w.eval(picker);
  const doc = w.document;
  assert.equal(doc.getElementById("auto-new-sched").open, true, "the section opens so the kept answers are seen");
  assert.equal(doc.getElementById("auto-new-title").value, "Monday digest");
  doc.getElementById("auto-new-sched").dispatchEvent(new w.Event("toggle"));
  await tick(); await tick();
  assert.equal(doc.getElementById("auto-new-workflow").value, "weekly-report");
  assert.equal(doc.getElementById("auto-new-param-0").value, "Support");
  doc.getElementById("auto-new-form").requestSubmit();
  await tick(); await tick();
  assert.match(doc.getElementById("auto-new-preview").textContent, /Schedule created/);
  assert.equal(w.sessionStorage.getItem("mv-keep:automation-schedule"), null);
  w.close();
});

test("Agent Factory: saving without a name or instructions points to each field and sends nothing", async () => {
  const body = `<div id="agent-msg" role="status"></div>
    <form id="agent-form" data-name="acme_helper" data-generation="new" data-creating="1" data-kind="agent" data-mv-validate>
      <div class="mv-error-summary" id="agent-form-errors" data-mv-error-summary tabindex="-1" hidden></div>
      <input id="f-new-id" type="text" value="acme helper">
      <label for="f-description">Name</label>
      <input id="f-description" name="description" type="text" value="" required data-error-required="Give the agent a name, for example Onboarding assistant.">
      <label for="f-persona">What should it do?</label>
      <textarea id="f-persona" name="persona" required data-error-required="Describe what the agent should do, in a sentence or two."></textarea>
      <input id="f-allow" type="hidden" value=""><input id="f-deny" type="hidden" value=""><select id="f-risk" hidden><option value="low" selected>low</option></select>
      <p id="agent-actions-error" hidden></p><p id="agent-risk-error" hidden></p><div id="wf-steps"></div>
      <button type="button" id="btn-save">Create custom agent</button><button type="button" id="btn-validate">Check settings</button>
    </form>`;
  const calls = [];
  const w = page("https://lightwork.example/agents?new=1", body, [], (win) => {
    win.mountToolPicks = () => {}; win.humanName = (x) => x; win.plainError = () => "Could not save.";
    win.fetch = async (url) => { calls.push(url); return new Response("{}", {status: 200, headers: {"Content-Type": "application/json"}}); };
  });
  w.eval(inlineScript("agents.html", "save / validate / reset (real agent-override endpoints)"));
  const doc = w.document;
  doc.getElementById("btn-save").click();
  await tick();
  assert.deepEqual(summaryLinks(doc), [
    ["#f-description", "Give the agent a name, for example Onboarding assistant."],
    ["#f-persona", "Describe what the agent should do, in a sentence or two."]]);
  assert.equal(doc.activeElement, doc.querySelector("[data-mv-error-summary]"));
  assert.equal(doc.getElementById("f-description").getAttribute("aria-invalid"), "true");
  assert.equal(doc.getElementById("f-new-id").value, "acme helper", "nothing typed is cleared");
  assert.deepEqual(calls, []);
  doc.getElementById("btn-validate").click();
  await tick();
  assert.deepEqual(calls, [], "Check settings also waits for the missing answers");
  w.close();
});
