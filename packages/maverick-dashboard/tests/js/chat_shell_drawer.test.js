// Run with: node --test packages/maverick-dashboard/tests/js/chat_shell_drawer.test.js
"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const shell = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/templates/chat_base.html"), "utf8");
const start = shell.indexOf("    // Small screens: the Menu button opens the sidebar as a drawer.");
const end = shell.indexOf("    // Filter the recents list as you type.");
const drawer = shell.slice(start, end);

function open() {
  const dom = new JSDOM(`<body class="chat-shell">
    <aside class="cs-side" id="cs-side"><button id="cs-menu-close">Close menu</button><a href="/chat">New chat</a>
      <button id="navfinder-open">Find a page</button></aside>
    <div id="cs-backdrop"></div>
    <main><button id="cs-menu" aria-expanded="false">Menu</button></main></body>`, { runScripts: "outside-only" });
  dom.window.eval(drawer);
  return dom;
}

test("the menu button opens the chat sidebar as a drawer and moves focus into it", () => {
  assert.ok(start > 0 && end > start);
  const dom = open(), doc = dom.window.document;
  doc.getElementById("cs-menu").click();
  assert.ok(doc.body.classList.contains("cs-drawer-open"));
  assert.equal(doc.getElementById("cs-menu").getAttribute("aria-expanded"), "true");
  assert.equal(doc.activeElement.id, "cs-menu-close");
});

test("Escape, the close button and the backdrop close it and return focus to Menu", () => {
  const dom = open(), doc = dom.window.document;
  for (const close of [
    () => doc.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape" })),
    () => doc.getElementById("cs-menu-close").click(),
    () => doc.getElementById("cs-backdrop").click(),
  ]) {
    doc.getElementById("cs-menu").click();
    close();
    assert.equal(doc.body.classList.contains("cs-drawer-open"), false);
    assert.equal(doc.getElementById("cs-menu").getAttribute("aria-expanded"), "false");
    assert.equal(doc.activeElement.id, "cs-menu");
  }
});

test("the drawer styles only apply on small screens and the shell carries the form dialog", () => {
  assert.match(shell, /@media \(max-width: 860px\)[\s\S]*body\.cs-drawer-open \.cs-side/);
  assert.ok(shell.includes('<dialog id="mv-form"'));
  assert.ok(!/\.cs-side \{ display: none; \}/.test(shell));
});
