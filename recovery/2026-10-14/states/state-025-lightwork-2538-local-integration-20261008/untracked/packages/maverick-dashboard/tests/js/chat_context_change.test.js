'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const script = fs.readFileSync(
  path.resolve(__dirname, '../../maverick_dashboard/static/lightwork-chat-context.js'),
  'utf8'
);
const template = fs.readFileSync(
  path.resolve(__dirname, '../../maverick_dashboard/templates/chat_goal.html'),
  'utf8'
);

test('workspace changes remove the open transcript, draft, and private page titles', t => {
  const dom = new JSDOM(`<!doctype html><html><head><title>Private customer title</title></head><body>
    <template id="chat-context-changed-template" data-safe-title="Lightwork conversation">
      <section class="panel" role="alert"><p>Your workspace changed. Refresh this page to check whether this conversation is still available to you.</p><a class="btn btn--primary" href="#">Refresh conversation</a></section>
    </template>
    <h1 id="conversation-title">Private customer title</h1>
    <header class="print-heading"><h1>Private customer title</h1></header>
    <div class="convo"><p>Private conversation answer</p><button>Share answer</button></div>
    <div id="composer"><textarea>Private unsent draft</textarea><button>Send</button></div>
  </body></html>`, {url: 'https://lightwork.test/chat/goal/42?private=1', runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const {window} = dom;
  let accessInvalidations = 0;
  window.document.addEventListener('lightwork:task-access-lost', () => accessInvalidations++);
  window.eval(script);

  window.dispatchEvent(new window.CustomEvent('lightwork:context-changed'));

  assert.equal(accessInvalidations, 1);
  assert.doesNotMatch(window.document.body.textContent, /Private customer title|Private conversation answer|Private unsent draft|Share answer|Send/);
  assert.equal(window.document.title, 'Lightwork');
  assert.equal(window.document.getElementById('conversation-title').textContent, 'Lightwork conversation');
  assert.equal(window.document.querySelector('.print-heading h1').textContent, 'Lightwork conversation');
  assert.equal(window.document.querySelectorAll('.convo .panel[role="alert"]').length, 1);
  assert.equal(window.document.querySelector('#composer a').getAttribute('href'), '/chat/goal/42');
  assert.match(window.document.body.textContent, /refresh this page to check/i);

  window.document.dispatchEvent(new window.CustomEvent('lightwork:account-context-changed'));
  assert.equal(accessInvalidations, 1, 'multiple context events do not duplicate the reset');
});

test('conversation invalidation closes live updates and is wired into the Chat page', () => {
  assert.match(template, /lightwork-chat-context\.js/);
  const handler = template.match(/document\.addEventListener\('lightwork:chat-context-invalidated', function \(\) \{\s*contextInvalidated = true;[\s\S]*?\n      \}\);/);
  assert.ok(handler, 'the active task stream has a context invalidation handler');

  const dom = new JSDOM('<!doctype html><html><body></body></html>', {runScripts: 'outside-only'});
  const {window} = dom;
  const cleared = [];
  window.clearTimeout = timer => cleared.push(timer);
  window.closedStreams = 0;
  window.eval(`(function () {
    let contextInvalidated = false, done = false, polling = true, pollTimer = 11, reconnectTimer = 12;
    let stream = {close: () => window.closedStreams++};
    ${handler[0]}
    window.chatState = () => ({contextInvalidated, done, polling, stream});
  })();`);
  window.document.dispatchEvent(new window.CustomEvent('lightwork:chat-context-invalidated'));
  assert.deepEqual(JSON.parse(JSON.stringify(window.chatState())), {contextInvalidated: true, done: true, polling: false, stream: null});
  assert.equal(window.closedStreams, 1);
  assert.deepEqual(cleared, [11, 12]);
  dom.window.close();
});
