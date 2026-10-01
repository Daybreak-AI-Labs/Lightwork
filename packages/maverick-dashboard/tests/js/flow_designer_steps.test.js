// Run with: node --test packages/maverick-dashboard/tests/js/flow_designer_steps.test.js
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const dir = path.resolve(__dirname, "../../maverick_dashboard/templates");
const read = (name) => fs.readFileSync(path.join(dir, name), "utf8").replace(/\r\n/g, "\n");
const raw = (text) => text.replace(/\{% raw %\}|\{% endraw %\}/g, "");

// The page body with its Jinja pieces resolved the simple way (no server).
function pageHtml() {
  let html = read("flow_designer.html");
  html = html.slice(html.indexOf("{% block content %}") + 19, html.lastIndexOf("{% endblock %}"));
  html = html.replace(/\{% call hero[\s\S]*?\{% endcall %\}/, "")
    .replace('{% include "flow_designer_core.js" %}', () => raw(read("flow_designer_core.js")))
    .replace('{% include "flow_designer.js" %}', () => raw(read("flow_designer.js")))
    .replace(/\{% for d in range\(1, 29\) %\}[\s\S]*?\{% endfor %\}/, '<option value="1">1st</option><option value="15">15th</option>')
    .replace(/\{% from [^%]*%\}/g, "")
    .replace(/\{\{ \(seed_flow or none\) \| tojson \}\}/, "null")
    .replace(/\{\{[^}]*\}\}/g, "");
  return "<!doctype html><html><body><p id=\"fd-state\"></p>" + html + "</body></html>";
}

function open() {
  const calls = [];
  const dom = new JSDOM(pageHtml(), {
    url: "https://app.example/flows/designer", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = async (url, options) => {
        calls.push({ url, options });
        const body = url.startsWith("/api/v1/people/assignable") ? { items: [{ ref: "user:ana", label: "Ana Diaz" }] }
          : url.startsWith("/api/v1/flows/tools") ? { tools: [{ name: "slack_post_message", description: "Post to Slack", params: ["channel_id", "text"], category: "chat" }], categories: ["chat"] }
          : url === "/api/v1/flows" ? { flows: [{ id: "onboard-customer", name: "Onboard a customer", inputs: [{ key: "customer_name", label: "Customer name" }] }] }
          : url.startsWith("/api/v1/flows/gallery") ? { flows: [] } : {};
        return { ok: true, status: 200, json: async () => body };
      };
      window.mvHuman = (v) => String(v || "").replace(/[_.-]+/g, " ").replace(/^./, (c) => c.toUpperCase());
      window.mvConfirm = async () => true;
      window.HTMLElement.prototype.getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 });
      window.SVGElement && (window.SVGElement.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 600 }));
    },
  });
  return { dom, w: dom.window, doc: dom.window.document, calls };
}
const settle = () => new Promise((r) => setTimeout(r, 20));

test("steps can be added, changed and reordered without the canvas", async () => {
  const { dom, w, doc } = open();
  try {
    await settle();
    const add = () => Array.from(doc.querySelectorAll("#fd-simple-body button")).find((b) => b.textContent === "Add step");
    doc.getElementById("fd-steps-add-kind").value = "agent"; add().click(); await settle();
    doc.getElementById("fd-steps-add-kind").value = "approval"; add().click(); await settle();
    const cards = doc.querySelectorAll("#fd-simple-body .fd-scard");
    assert.equal(cards.length, 2);
    // The chosen step's settings open in the side rail with plain labels.
    const rail = doc.querySelector(".fdx-rail");
    assert.ok(rail.contains(doc.getElementById("fd-props")));
    const text = doc.getElementById("fd-props").textContent;
    assert.match(text, /What should the person approve\?/);
    assert.match(text, /Who should be asked/);
    assert.match(text, /Stop waiting after/);
    assert.ok(Array.from(doc.querySelectorAll("#fd-props option")).some((o) => o.textContent === "Ana Diaz"));
    // Move the approval above the AI task.
    Array.from(doc.querySelectorAll("#fd-simple-body button")).find((b) => b.textContent === "Move up").click();
    await settle();
    const order = JSON.parse(JSON.stringify(w.__fdEngine.order().map((s) => s.kind)));
    assert.deepEqual(order, ["approval", "agent"]);
    assert.equal(w.__fdLiveFlow().start, w.__fdEngine.order()[0].id);
  } finally { dom.window.close(); }
});

test("an action step is chosen from a list and its settings are labelled boxes", async () => {
  const { dom, w, doc } = open();
  try {
    await settle();
    doc.getElementById("fd-steps-add-kind").value = "action";
    Array.from(doc.querySelectorAll("#fd-simple-body button")).find((b) => b.textContent === "Add step").click();
    await settle();
    const sel = doc.getElementById("fd-tool-select");
    assert.equal(sel.tagName, "SELECT");
    sel.value = "slack_post_message"; sel.dispatchEvent(new w.Event("change"));
    await settle();
    const labels = Array.from(doc.querySelectorAll("#fd-props label")).map((l) => l.textContent);
    assert.ok(labels.includes("Channel id") || labels.includes("Channel ID"));
    assert.ok(!doc.getElementById("fd-props").textContent.includes("Params (JSON)"));
  } finally { dom.window.close(); }
});

test("the schedule picker writes the timetable from plain choices", async () => {
  const { dom, w, doc } = open();
  try {
    await settle();
    const freq = doc.getElementById("fd-sched-freq");
    freq.value = "weekly"; freq.dispatchEvent(new w.Event("change"));
    doc.querySelector('#fd-sched-days input[value="3"]').checked = true;
    doc.querySelector('#fd-sched-days input[value="3"]').dispatchEvent(new w.Event("change"));
    const time = doc.getElementById("fd-sched-time"); time.value = "14:30"; time.dispatchEvent(new w.Event("change"));
    assert.equal(doc.getElementById("fd-schedule").value, "30 14 * * 1,3");
    assert.match(doc.getElementById("fd-sched-words").textContent, /every Monday, Wednesday at 2:30 pm/);
    assert.equal(doc.getElementById("fd-sched-days").hidden, false);
    assert.equal(doc.getElementById("fd-tz").tagName, "SELECT");
    assert.ok(doc.getElementById("fd-tz").options.length > 5);
    freq.value = "monthly"; freq.dispatchEvent(new w.Event("change"));
    assert.equal(doc.getElementById("fd-schedule").value, "30 14 1 * *");
    assert.equal(w.__fdLiveFlow().schedule, "30 14 1 * *");
  } finally { dom.window.close(); }
});

// Reachability of the live flow from its first step, over every way out of a step.
function reachable(w) {
  const flow = JSON.parse(JSON.stringify(w.__fdLiveFlow()));
  const byId = Object.fromEntries(flow.nodes.map((n) => [n.id, n]));
  const seen = new Set(), queue = flow.start ? [flow.start] : [];
  while (queue.length) {
    const n = byId[queue.shift()];
    if (!n || seen.has(n.id)) continue;
    seen.add(n.id);
    for (const key of ["next", "if_true", "if_false", "on_error", "on_expire"]) if (n[key]) queue.push(n[key]);
    for (const c of n.cases || []) if (c.to) queue.push(c.to);
  }
  return { flow, byId, seen };
}

function stepsHelpers(doc) {
  const add = () => Array.from(doc.querySelectorAll("#fd-simple-body button")).find((b) => b.textContent === "Add step");
  const where = () => doc.getElementById("fd-steps-add-where");
  return {
    where,
    async addKind(kind, choose) {
      doc.getElementById("fd-steps-add-kind").value = kind;
      if (choose) {
        const sel = where();
        assert.ok(sel, "the Steps view must ask where the new step goes");
        const option = Array.from(sel.options).find((o) => o.textContent === choose);
        assert.ok(option, "missing choice: " + choose + " in " + Array.from(sel.options).map((o) => o.textContent));
        sel.value = option.value;
      }
      add().click();
      await settle();
    },
  };
}

test("a step added after a Decision goes on the chosen Yes or No path instead of being left unconnected", async () => {
  const { dom, w, doc } = open();
  try {
    await settle();
    const steps = stepsHelpers(doc);
    await steps.addKind("agent");
    // A plain chain needs no question: the new step simply follows the last one.
    assert.equal(steps.where(), null);
    await steps.addKind("branch");
    // After a Decision the person is asked which path the next step belongs to.
    assert.deepEqual(Array.from(steps.where().options).map((o) => o.textContent),
      ["If yes, after “Decision”", "If no, after “Decision”"]);
    await steps.addKind("approval", "If yes, after “Decision”");
    // The next add starts on the path just worked on, not back at the first open end.
    assert.equal(steps.where().selectedOptions[0].textContent, "After “Approval”");
    await steps.addKind("agent", "After “Approval”");
    await steps.addKind("setvar", "If no, after “Decision”");

    const { flow, byId, seen } = reachable(w);
    const branch = flow.nodes.find((n) => n.kind === "branch");
    const approval = flow.nodes.find((n) => n.kind === "approval");
    assert.equal(branch.if_true, approval.id);
    assert.equal(byId[approval.id].next, flow.nodes.filter((n) => n.kind === "agent")[1].id);
    assert.equal(byId[branch.if_false].kind, "setvar");
    // The point of the fix: every step is reachable from the first step.
    assert.deepEqual([...seen].sort(), flow.nodes.map((n) => n.id).sort());
    // Once both paths lead somewhere, only the end of each path is still open.
    assert.ok(Array.from(steps.where().options).every((o) => !/Decision/.test(o.textContent)));
  } finally { dom.window.close(); }
});

test("adding with no explicit place after a Decision still connects the step to a path", async () => {
  const { dom, w, doc } = open();
  try {
    await settle();
    const steps = stepsHelpers(doc);
    await steps.addKind("branch");
    w.__fdEngine.add("approval");
    await settle();
    const { flow, seen } = reachable(w);
    assert.equal(flow.nodes.length, 2);
    assert.deepEqual([...seen].sort(), flow.nodes.map((n) => n.id).sort());
  } finally { dom.window.close(); }
});

test("the Steps list says which steps will never run and where each Decision path leads", async () => {
  const { dom, w, doc } = open();
  try {
    await settle();
    const steps = stepsHelpers(doc);
    await steps.addKind("agent");
    await steps.addKind("branch");
    await steps.addKind("approval", "If yes, after “Decision”");
    const cards = () => Array.from(doc.querySelectorAll("#fd-simple-body .fd-scard"));
    const decision = cards().find((c) => /Decision/.test(c.textContent));
    assert.match(decision.textContent, /If yes: step 3 “Approval”/);
    assert.match(decision.textContent, /If no: ends the flow/);
    assert.doesNotMatch(cards().map((c) => c.textContent).join(" "), /never run/);

    // Disconnect the approval from the Decision from the step settings.
    const decisionCard = decision; decisionCard.click(); await settle();
    const yes = Array.from(doc.querySelectorAll("#fd-props select")).find((s) => /If yes, go to/.test(s.closest(".field").textContent));
    yes.value = ""; yes.dispatchEvent(new w.Event("change")); await settle();
    const lonely = cards().find((c) => /Approval/.test(c.textContent));
    assert.match(lonely.textContent, /Not connected, so this step never runs/);
  } finally { dom.window.close(); }
});

test("the page offers no code boxes or developer wording", () => {
  const html = read("flow_designer.html"), js = read("flow_designer.js");
  for (const phrase of ["Params (JSON)", "Input data (JSON)", "Schedule expression (cron)", "POST /webhook/run", "_break",
    "saved flow id", "[webhooks]", "[flows]", "{{issue_body}}", "Fix invalid JSON", "Make start"]) {
    assert.ok(!html.includes(phrase) && !js.includes(phrase), phrase);
  }
});
