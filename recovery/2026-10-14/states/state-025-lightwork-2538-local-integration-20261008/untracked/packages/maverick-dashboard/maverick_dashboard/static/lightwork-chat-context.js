(function () {
  'use strict';

  var invalidated = false;

  function notice() {
    var template = document.getElementById('chat-context-changed-template');
    var section = template && template.content.firstElementChild
      ? template.content.firstElementChild.cloneNode(true)
      : document.createElement('section');
    if (!section.hasAttribute('role')) {
      section.className = 'panel';
      section.setAttribute('role', 'alert');
      section.textContent = 'Your workspace changed. Refresh this page to check whether this conversation is still available to you.';
    }
    var refresh = section.querySelector('a');
    if (refresh) refresh.href = window.location.pathname;
    return section;
  }

  function invalidateConversation() {
    if (invalidated) return;
    invalidated = true;

    // Existing listeners cancel attachment and search work and disable edits;
    // the dedicated event also closes the task event stream and timers.
    document.dispatchEvent(new CustomEvent('lightwork:task-access-lost'));
    document.dispatchEvent(new CustomEvent('lightwork:chat-context-invalidated'));

    var conversation = document.querySelector('.convo');
    if (conversation) conversation.replaceChildren(notice());
    var composer = document.getElementById('composer');
    if (composer) composer.replaceChildren(notice());

    var title = document.getElementById('conversation-title');
    var template = document.getElementById('chat-context-changed-template');
    var safeTitle = template && template.getAttribute('data-safe-title') || 'Lightwork conversation';
    if (title) title.textContent = safeTitle;
    var printTitle = document.querySelector('.print-heading h1');
    if (printTitle) printTitle.textContent = safeTitle;
    document.title = 'Lightwork';
  }

  ['lightwork:context-changed', 'lightwork:workspace-changed', 'lightwork:account-context-changed']
    .forEach(function (name) {
      window.addEventListener(name, invalidateConversation);
      document.addEventListener(name, invalidateConversation);
    });
})();
