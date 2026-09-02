// Run with: node --test packages/maverick-dashboard/tests/js/lightwork_navfinder.test.js
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function navFinderClient() {
  const window = {};
  const context = { console, window };
  vm.createContext(context);
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-navfinder.js"),
    "utf8",
  );
  vm.runInContext(source, context, { filename: "lightwork-navfinder.js" });
  return window.LightworkNavFinder;
}

function keyboard(key, overrides = {}) {
  return Object.assign({
    key,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    isComposing: false,
    defaultPrevented: false,
    propagationStopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  }, overrides);
}

class ClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  contains(value) { return this.values.has(value); }
}

class Element {
  constructor(document, id = "") {
    this.document = document;
    this.id = id;
    this.attributes = {};
    this.listeners = {};
    this.classList = new ClassList();
    this.hidden = false;
    this.isConnected = true;
    this.parentElement = null;
    this.value = "";
    this.textContent = "";
    this.clickCount = 0;
    this.inertAncestor = false;
  }
  addEventListener(type, callback) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(callback);
  }
  dispatch(type, event = keyboard(type)) {
    event.target = event.target || this;
    event.currentTarget = this;
    for (const callback of this.listeners[type] || []) callback(event);
    return event;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() { this.document.activeElement = this; }
  closest(selector) { return selector === "[inert]" && this.inertAncestor ? {} : null; }
  click() {
    this.clickCount += 1;
    this.dispatch("click", keyboard("click"));
  }
  scrollIntoView() { this.scrolled = true; }
}

class Dialog extends Element {
  constructor(document, id) {
    super(document, id);
    this.open = false;
  }
  showModal() {
    this.open = true;
    this.setAttribute("open", "");
  }
  close() {
    this.open = false;
    this.removeAttribute("open");
    this.dispatch("close", keyboard("close"));
  }
}

class Document {
  constructor() {
    this.elements = new Map();
    this.listeners = {};
    this.activeElement = null;
  }
  add(element) {
    this.elements.set(element.id, element);
    return element;
  }
  getElementById(id) { return this.elements.get(id) || null; }
  addEventListener(type, callback) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(callback);
  }
  dispatch(type, event) {
    for (const callback of this.listeners[type] || []) callback(event);
    return event;
  }
}

function fixture() {
  const document = new Document();
  const trigger = document.add(new Element(document, "navfinder-open"));
  const dialog = document.add(new Dialog(document, "navfinder-dialog"));
  const input = document.add(new Element(document, "navfinder-input"));
  const results = document.add(new Element(document, "navfinder-results"));
  const close = document.add(new Element(document, "navfinder-close"));
  const count = document.add(new Element(document, "navfinder-count"));
  const empty = document.add(new Element(document, "navfinder-empty"));
  const navToggle = document.add(new Element(document, "nav-toggle"));
  const content = document.add(new Element(document, "content"));

  const details = [
    ["overview", "Overview General /overview"],
    ["privacy-rights", "Request workflows Privacy /privacy/rights"],
    ["settings", "Settings System /settings"],
  ];
  const options = details.map(([name, search], index) => {
    const parent = new Element(document, `row-${index}`);
    const option = new Element(document, `navfinder-option-${index}`);
    option.parentElement = parent;
    option.setAttribute("data-navfinder-target", "");
    option.setAttribute("data-search", search);
    option.setAttribute("href", `/${name}`);
    return option;
  });
  results.querySelectorAll = (selector) => selector === "[data-navfinder-target]" ? options : [];

  return { document, trigger, dialog, input, results, close, count, empty, navToggle, content, options };
}

test("open, filter, arrows, Enter and Escape preserve accessible state", () => {
  const ui = fixture();
  navFinderClient().init(ui.document);

  ui.trigger.click();
  assert.equal(ui.dialog.open, true);
  assert.equal(ui.document.activeElement, ui.input);
  assert.equal(ui.trigger.getAttribute("aria-expanded"), "true");
  assert.equal(ui.input.getAttribute("aria-expanded"), "true");
  assert.equal(ui.count.textContent, "3 pages");
  assert.equal(ui.input.getAttribute("aria-activedescendant"), ui.options[0].id);

  ui.input.value = "privacy rights";
  ui.input.dispatch("input", keyboard("input"));
  assert.deepEqual(ui.options.map((option) => option.hidden), [true, false, true]);
  assert.equal(ui.count.textContent, "1 page");
  assert.equal(ui.input.getAttribute("aria-activedescendant"), ui.options[1].id);

  ui.input.value = "";
  ui.input.dispatch("input", keyboard("input"));
  ui.dialog.dispatch("keydown", keyboard("ArrowUp", { target: ui.input }));
  assert.equal(ui.options[2].getAttribute("aria-selected"), "true");
  ui.dialog.dispatch("keydown", keyboard("ArrowDown", { target: ui.input }));
  ui.dialog.dispatch("keydown", keyboard("ArrowDown", { target: ui.input }));
  assert.equal(ui.options[1].getAttribute("aria-selected"), "true");

  const enter = ui.dialog.dispatch("keydown", keyboard("Enter", { target: ui.input }));
  assert.equal(enter.defaultPrevented, true);
  assert.equal(ui.options[1].clickCount, 1);
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.document.activeElement, ui.trigger);
  assert.equal(ui.trigger.getAttribute("aria-expanded"), "false");
});

test("listbox key handling does not hijack the dialog close button", () => {
  const ui = fixture();
  navFinderClient().init(ui.document);
  ui.trigger.click();

  const enter = ui.dialog.dispatch("keydown", keyboard("Enter", { target: ui.close }));
  assert.equal(enter.defaultPrevented, false);
  assert.deepEqual(ui.options.map((option) => option.clickCount), [0, 0, 0]);
  assert.equal(ui.dialog.open, true);
});

test("Ctrl/Cmd+K opens globally and Escape restores the invoking focus", () => {
  for (const modifier of ["ctrlKey", "metaKey"]) {
    const ui = fixture();
    navFinderClient().init(ui.document);
    ui.content.focus();

    const shortcut = keyboard("k", { [modifier]: true });
    ui.document.dispatch("keydown", shortcut);
    assert.equal(shortcut.defaultPrevented, true);
    assert.equal(ui.dialog.open, true);
    assert.equal(ui.document.activeElement, ui.input);

    const escape = ui.dialog.dispatch("keydown", keyboard("Escape"));
    assert.equal(escape.defaultPrevented, true);
    assert.equal(escape.propagationStopped, true);
    assert.equal(ui.dialog.open, false);
    assert.equal(ui.document.activeElement, ui.content);
  }
});

test("focus falls back to the mobile nav toggle when the opener becomes inert", () => {
  const ui = fixture();
  navFinderClient().init(ui.document);
  ui.trigger.click();
  ui.trigger.inertAncestor = true;

  ui.close.click();
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.document.activeElement, ui.navToggle);
});

test("an empty filter exposes a status and no active descendant", () => {
  const ui = fixture();
  navFinderClient().init(ui.document);
  ui.trigger.click();
  ui.input.value = "destination-that-does-not-exist";
  ui.input.dispatch("input", keyboard("input"));

  assert.equal(ui.count.textContent, "0 pages");
  assert.equal(ui.empty.hidden, false);
  assert.equal(ui.results.hidden, true);
  assert.equal(ui.input.getAttribute("aria-activedescendant"), null);
});
