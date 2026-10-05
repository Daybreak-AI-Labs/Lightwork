"use strict";
// The shared tab bar (templates/_tabs.html + static/lightwork-tabs.js): tabs
// are links to their views, so the address is the state; the script adds the
// keys of a tab list. Run with: node --test tests/js/tabs.test.js
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const script = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-tabs.js"), "utf8");

function fixture() {
  const html = `<main id="content">
    <div class="mv-tabs" role="tablist" aria-label="Task replay views" data-mv-tabs>
      <a class="mv-tab" role="tab" id="t-main" href="/replay" aria-selected="true" tabindex="0">Replay</a>
      <a class="mv-tab" role="tab" id="t-branches" href="/replay?tab=branches" aria-selected="false" tabindex="-1">Branches</a>
      <a class="mv-tab" role="tab" id="t-video" href="/replay?tab=video" aria-selected="false" tabindex="-1">Video</a>
    </div><div role="tabpanel" aria-labelledby="t-main"><button id="inside">Inside</button></div></main>`;
  const dom = new JSDOM(html, {url: "https://fixture.invalid/replay", runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window, doc = w.document, opened = [];
  doc.querySelectorAll('[role="tab"]').forEach((tab) => tab.addEventListener("click", (event) => {
    event.preventDefault();
    opened.push(tab.getAttribute("href"));
  }));
  w.eval(script);
  w.mvTabs.init();  // the page may still be loading; the script also binds on DOMContentLoaded
  const key = (target, name) => {
    const event = new w.KeyboardEvent("keydown", {key: name, bubbles: true, cancelable: true});
    target.dispatchEvent(event);
    return event;
  };
  return {w, doc, opened, key, tab: (id) => doc.getElementById(id)};
}

test("arrow keys move focus along the tabs and wrap, with one tab in the Tab order", () => {
  const f = fixture();
  f.tab("t-main").focus();
  assert.equal(f.key(f.tab("t-main"), "ArrowRight").defaultPrevented, true);
  assert.equal(f.doc.activeElement, f.tab("t-branches"));
  assert.deepEqual(["t-main", "t-branches", "t-video"].map((id) => f.tab(id).getAttribute("tabindex")), ["-1", "0", "-1"]);
  f.key(f.tab("t-branches"), "ArrowRight");
  f.key(f.tab("t-video"), "ArrowRight");
  assert.equal(f.doc.activeElement, f.tab("t-main"), "Right on the last tab wraps to the first");
  f.key(f.tab("t-main"), "ArrowLeft");
  assert.equal(f.doc.activeElement, f.tab("t-video"), "Left on the first tab wraps to the last");
  f.key(f.tab("t-video"), "Home");
  assert.equal(f.doc.activeElement, f.tab("t-main"));
  f.key(f.tab("t-main"), "End");
  assert.equal(f.doc.activeElement, f.tab("t-video"));
});

test("moving focus opens nothing; Space opens the focused tab's address", () => {
  const f = fixture();
  f.tab("t-main").focus();
  f.key(f.tab("t-main"), "ArrowRight");
  assert.deepEqual(f.opened, []);
  assert.equal(f.tab("t-main").getAttribute("aria-selected"), "true", "the shown view stays selected");
  assert.equal(f.key(f.tab("t-branches"), " ").defaultPrevented, true);
  assert.deepEqual(f.opened, ["/replay?tab=branches"]);
});

test("other keys and modified arrows are left alone", () => {
  const f = fixture();
  f.tab("t-main").focus();
  assert.equal(f.key(f.tab("t-main"), "a").defaultPrevented, false);
  const event = new f.w.KeyboardEvent("keydown", {key: "ArrowRight", altKey: true, bubbles: true, cancelable: true});
  f.tab("t-main").dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
  assert.equal(f.doc.activeElement, f.tab("t-main"));
});

test("the bar binds once even when the script runs again", () => {
  const f = fixture();
  f.w.eval(script);
  f.w.mvTabs.init();
  f.tab("t-main").focus();
  f.key(f.tab("t-main"), "ArrowRight");
  assert.equal(f.doc.activeElement, f.tab("t-branches"), "one listener: a second would skip a tab");
});
