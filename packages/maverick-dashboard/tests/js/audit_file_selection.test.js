'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const script = fs.readFileSync(path.resolve(__dirname, '../../maverick_dashboard/static/lightwork-file-selection.js'), 'utf8');

test('removing one pasted attachment preserves other files and the written draft', () => {
  const dom = new JSDOM('<form><textarea>Keep my answer</textarea><input id="reply-files" type="file" multiple></form>', {runScripts: 'outside-only'});
  const { window } = dom, input = window.document.getElementById('reply-files');
  let files = [{name: 'first.pdf', size: 500, type: 'application/pdf', lastModified: 1}, {name: 'second.txt', size: 100, type: 'text/plain', lastModified: 2}];
  Object.defineProperty(input, 'files', {get: () => files, set: value => { files = value; }});
  window.DataTransfer = class {
    constructor() { this.files = []; this.items = {add: file => this.files.push(file)}; }
  };
  window.eval(script);
  const remove = Array.from(window.document.querySelectorAll('button')).find(button => button.textContent === 'Remove first.pdf');
  remove.click();
  assert.deepEqual(files.map(file => file.name), ['second.txt']);
  assert.equal(window.document.querySelector('textarea').value, 'Keep my answer');
  assert.equal(window.document.querySelectorAll('li').length, 1);
  dom.window.close();
});

test('an unsupported DataTransfer preserves the batch and explains clear-all recovery', () => {
  const dom = new JSDOM('<form><input id="reply-files" type="file" multiple></form>', {runScripts: 'outside-only'});
  const { window } = dom, input = window.document.getElementById('reply-files');
  const files = [{name: 'keep.pdf', size: 50, type: 'application/pdf'}];
  Object.defineProperty(input, 'files', {get: () => files});
  window.eval(script);
  Array.from(window.document.querySelectorAll('button')).find(button => button.textContent === 'Remove keep.pdf').click();
  assert.equal(input.files.length, 1);
  assert.match(window.document.querySelector('[role="status"]').textContent, /Remove all selected files/);
  dom.window.close();
});
