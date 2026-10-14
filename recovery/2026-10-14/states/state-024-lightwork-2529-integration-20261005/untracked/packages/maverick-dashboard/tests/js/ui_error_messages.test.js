"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const { shell } = require("./_shell");

test("409 messages preserve safe business reasons and avoid false stale-edit advice", () => {
  const dom = new JSDOM("", { url: "https://lightwork.test/", runScripts: "outside-only" });
  try {
    const ui = shell(dom.window);
    assert.equal(
      ui.mvErrorText({ status: 409, detail: "A workspace with this name already exists." }, "create the workspace"),
      "Could not create the workspace. A workspace with this name already exists.",
    );
    assert.equal(
      ui.mvErrorText({ status: 409, detail: "This review changed since you opened it." }, "save the review"),
      "Could not save the review. This review changed since you opened it.",
    );
    assert.equal(
      ui.mvErrorText({ status: 409, detail: "The PostgreSQL provider is not configured." }, "start the review"),
      "Could not start the review. This cannot be done right now. Review the item or its settings before trying again.",
    );
  } finally {
    dom.window.close();
  }
});
