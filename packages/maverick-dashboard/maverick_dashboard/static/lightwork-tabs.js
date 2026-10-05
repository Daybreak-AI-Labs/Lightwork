/* Tab bars whose tabs are addresses (templates/_tabs.html).
   Each tab is a link to its view (?tab=…), so the address is the state and
   the bar works without scripts. This adds the keys of a tab list: Left and
   Right (Up and Down too) move between tabs, Home and End go to the first and
   last, and Enter or Space opens the focused tab. Only the focused tab is in
   the Tab order (a roving tabindex), so one Tab press leaves the bar. Moving
   focus opens nothing: each view is a page load, so a tab opens on request. */
(function () {
  "use strict";
  var KEYS = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

  function tabsOf(list) {
    return Array.prototype.filter.call(list.querySelectorAll('[role="tab"]'), function (tab) {
      return tab.closest('[role="tablist"]') === list;
    });
  }

  function focusTab(tabs, index) {
    tabs.forEach(function (tab, i) { tab.setAttribute("tabindex", i === index ? "0" : "-1"); });
    tabs[index].focus();
  }

  function onKey(event) {
    var list = event.currentTarget;
    var tabs = tabsOf(list);
    var index = tabs.indexOf(event.target);
    if (index < 0 || event.altKey || event.ctrlKey || event.metaKey) return;
    var next = null;
    if (event.key in KEYS) next = (index + KEYS[event.key] + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else if (event.key === " " || event.key === "Spacebar") {
      event.preventDefault();
      event.target.click();
      return;
    }
    if (next === null) return;
    event.preventDefault();
    focusTab(tabs, next);
  }

  function init(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll("[data-mv-tabs]"), function (list) {
      if (list.getAttribute("data-mv-tabs-ready") === "true") return;
      list.setAttribute("data-mv-tabs-ready", "true");
      list.addEventListener("keydown", onKey);
    });
  }

  window.mvTabs = { init: init };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { init(); });
  else init();
})();
