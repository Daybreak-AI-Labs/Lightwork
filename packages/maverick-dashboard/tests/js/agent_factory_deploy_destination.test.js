"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const template = fs.readFileSync(path.resolve(__dirname,
  "../../maverick_dashboard/templates/agents.html"), "utf8");
const marker = "    // ---- departments roster (real: /api/v1/departments + deploy) ----";
const start = template.indexOf("    (function () {", template.indexOf(marker));
const end = template.indexOf("    })();", start) + "    })();".length;
const source = template.slice(start, end);

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise(resolve => setTimeout(resolve, 0));
}

test("both Agent Factory deploy paths open the exact team with its next action", async () => {
  const dom = new JSDOM(`<div id="dept-roster"></div>
    <dialog id="dept-dialog">
      <span id="dept-dlg-avatar"></span><span id="dept-dlg-title"></span>
      <span id="dept-dlg-sub"></span><span id="dept-dlg-charter"></span>
      <ul id="dept-dlg-members"></ul><button id="dept-dlg-close"></button>
      <button id="dept-dlg-deploy"></button>
      <p id="dept-dlg-msg" role="status"></p>
    </dialog>`, {url: "https://lightwork.test/agents", runScripts: "outside-only"});
  const w = dom.window;
  w.document.getElementById("dept-dialog").showModal = () => {};
  w.document.getElementById("dept-dialog").close = () => {};
  w.mvConfirm = async () => true;
  let deploys = 0;
  w.fetch = async url => {
    if (String(url).endsWith("/deploy")) {
      deploys += 1;
      return {ok: true, status: 201, json: async () => ({
        fleet: {name: "dept-sales-user_alice_83a5de"}, deployed_count: deploys === 1 ? 1 : 0,
      })};
    }
    return {ok: true, json: async () => [{key: "sales", title: "Sales", entitled: true, members: ["sales_ops"]}]};
  };
  w.eval(source);
  await flush();

  const roster = w.document.getElementById("dept-roster");
  roster.querySelector(".dep-deploy").click();
  await flush();
  const cardLink = roster.querySelector(".dep-msg a");
  assert.equal(cardLink.getAttribute("href"), "/fleets#team-dept-sales-user_alice_83a5de");
  assert.match(cardLink.parentElement.textContent, /Team set up\. Give this team a task/);
  assert.match(cardLink.className, /btn--primary/);

  roster.querySelector(".dep-details").click();
  w.document.getElementById("dept-dlg-deploy").click();
  await flush();
  const dialogLink = w.document.querySelector("#dept-dlg-msg a");
  assert.equal(dialogLink.getAttribute("href"), "/fleets#team-dept-sales-user_alice_83a5de");
  assert.match(dialogLink.parentElement.textContent, /Team saved\. No specialist is ready to start\. Review team setup/);
  w.close();
});
