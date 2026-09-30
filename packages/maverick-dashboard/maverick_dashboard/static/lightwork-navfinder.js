/* Accessible, registry-backed navigation finder for the Lightwork shell.
   The server renders every allowed destination into the dialog. This module
   only filters and activates those links; it never constructs a route. */
(function (global) {
  "use strict";

  function init(documentRef) {
    var doc = documentRef || global.document;
    if (!doc) return null;

    var trigger = doc.getElementById("navfinder-open");
    var dialog = doc.getElementById("navfinder-dialog");
    var input = doc.getElementById("navfinder-input");
    var results = doc.getElementById("navfinder-results");
    var closeButton = doc.getElementById("navfinder-close");
    var count = doc.getElementById("navfinder-count");
    var empty = doc.getElementById("navfinder-empty");
    if (!trigger || !dialog || !input || !results || !closeButton || !count || !empty) {
      return null;
    }
    if (dialog.getAttribute("data-navfinder-ready") === "true") return null;
    dialog.setAttribute("data-navfinder-ready", "true");

    var options = Array.prototype.slice.call(
      results.querySelectorAll("[data-navfinder-target]")
    );
    var pageOptions = options.slice();
    var recordOptions = [];
    var settingOptions = [];
    var activeIndex = -1;
    var returnFocus = null;
    var isOpen = false;

    function normalise(value) {
      return String(value || "").trim().toLowerCase();
    }

    function visibleOptions() {
      return options.filter(function (option) { return !option.hidden; });
    }

    function setActive(index, shouldScroll) {
      var visible = visibleOptions();
      options.forEach(function (option) {
        option.classList.remove("is-active");
        option.setAttribute("aria-selected", "false");
      });

      if (!visible.length) {
        activeIndex = -1;
        input.removeAttribute("aria-activedescendant");
        return null;
      }

      activeIndex = ((index % visible.length) + visible.length) % visible.length;
      var selected = visible[activeIndex];
      selected.classList.add("is-active");
      selected.setAttribute("aria-selected", "true");
      input.setAttribute("aria-activedescendant", selected.id);
      if (shouldScroll && typeof selected.scrollIntoView === "function") {
        selected.scrollIntoView({ block: "nearest" });
      }
      return selected;
    }

    function filterResults() {
      var terms = normalise(input.value).split(/\s+/).filter(Boolean);
      pageOptions.forEach(function (option) {
        var haystack = normalise(option.getAttribute("data-search"));
        var matches = terms.every(function (term) { return haystack.indexOf(term) !== -1; });
        option.hidden = !matches;
        if (option.parentElement) option.parentElement.hidden = !matches;
      });

      var visible = visibleOptions();
      var pages = pageOptions.filter(function (option) { return !option.hidden; }).length;
      count.textContent = (pages === 1 ? "1 page" : pages + " pages") +
        (recordOptions.length ? ", " + recordOptions.length + " records shown" : "");
      empty.hidden = visible.length !== 0;
      results.hidden = visible.length === 0;
      setActive(visible.length ? 0 : -1, false);
      return visible;
    }

    function canRestoreFocus(node) {
      if (!node || typeof node.focus !== "function" || node.isConnected === false) return false;
      try {
        return !(typeof node.closest === "function" && node.closest("[inert]"));
      } catch (error) {
        return false;
      }
    }

    function restoreFocus() {
      var target = canRestoreFocus(returnFocus) ? returnFocus : doc.getElementById("nav-toggle");
      returnFocus = null;
      if (canRestoreFocus(target)) target.focus();
    }

    function onClosed() {
      if (!isOpen) return;
      isOpen = false;
      trigger.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      queryChanged("closed");
      restoreFocus();
    }

    function closeFinder() {
      if (!isOpen) return;
      if (dialog.open && typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
        onClosed();
      }
    }

    function openFinder(opener) {
      if (isOpen) {
        input.focus({ preventScroll: true });
        return;
      }
      returnFocus = opener || doc.activeElement || trigger;
      isOpen = true;
      trigger.setAttribute("aria-expanded", "true");
      input.setAttribute("aria-expanded", "true");
      input.value = "";
      filterResults();
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
      input.focus({ preventScroll: true });
      queryChanged("opened");
    }

    trigger.addEventListener("click", function () { openFinder(trigger); });
    closeButton.addEventListener("click", closeFinder);
    input.addEventListener("input", function () { filterResults(); queryChanged("input"); });

    function queryChanged(reason) {
      if (typeof global.CustomEvent === "function") {
        dialog.dispatchEvent(new global.CustomEvent("lightwork:finder-query", {detail: {reason: reason, query: input.value}}));
      }
    }

    function bindOption(option) {
      option.addEventListener("mouseenter", function () {
        var index = visibleOptions().indexOf(option);
        if (index >= 0) setActive(index, false);
      });
      option.addEventListener("click", closeFinder);
    }

    function setRecords(rows) {
      recordOptions.forEach(function (option) { option.parentElement.remove(); });
      recordOptions = [];
      rows.forEach(function (row, index) {
        var entry = doc.createElement("li"); entry.setAttribute("role", "none");
        var option = doc.createElement("a"); option.id = "navfinder-record-" + index;
        option.href = row.href; option.setAttribute("role", "option"); option.setAttribute("tabindex", "-1");
        option.setAttribute("aria-selected", "false");
        var label = doc.createElement("span"); label.className = "navfinder__label"; label.textContent = row.title;
        var description = doc.createElement("span"); description.className = "navfinder__group";
        description.textContent = row.kind + " · " + row.id + (row.revision ? " · version " + row.revision : "") + (row.state ? " · " + row.state.replaceAll("_", " ") : "");
        option.appendChild(label); option.appendChild(description); entry.appendChild(option); results.appendChild(entry);
        recordOptions.push(option); bindOption(option);
      });
      options = pageOptions.concat(recordOptions, settingOptions); filterResults();
      if (!isOpen) input.removeAttribute("aria-activedescendant");
    }

    function setSettings(rows) {
      settingOptions.forEach(function (option) { option.parentElement.remove(); }); settingOptions = [];
      rows.forEach(function (row,index) {
        var entry=doc.createElement('li');entry.setAttribute('role','none');
        var option=doc.createElement('a');option.id='navfinder-setting-'+index;option.href=row.href;option.setAttribute('role','option');option.setAttribute('tabindex','-1');option.setAttribute('aria-selected','false');
        var label=doc.createElement('span');label.className='navfinder__label';label.textContent=row.title;
        var group=doc.createElement('span');group.className='navfinder__group';group.textContent='Setting - '+row.group;
        option.appendChild(label);option.appendChild(group);entry.appendChild(option);results.appendChild(entry);settingOptions.push(option);bindOption(option);
      });
      options=pageOptions.concat(recordOptions,settingOptions);filterResults();
    }

    dialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      closeFinder();
    });
    dialog.addEventListener("close", onClosed);
    dialog.addEventListener("keydown", function (event) {
      if (event.isComposing) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeFinder();
        return;
      }
      // Arrow/Enter listbox control belongs to the combobox. Do not consume
      // those keys when a user tabs to the dialog's Close button.
      if (event.target !== input) return;
      var visible = visibleOptions();
      if (event.key === "ArrowDown" && visible.length) {
        event.preventDefault();
        setActive(activeIndex + 1, true);
      } else if (event.key === "ArrowUp" && visible.length) {
        event.preventDefault();
        setActive(activeIndex - 1, true);
      } else if (event.key === "Enter" && visible.length && activeIndex >= 0) {
        event.preventDefault();
        visible[activeIndex].click();
      }
    });

    options.forEach(bindOption);

    doc.addEventListener("keydown", function (event) {
      var shortcut = (event.ctrlKey || event.metaKey) && !event.altKey &&
        normalise(event.key) === "k";
      if (!shortcut) return;
      event.preventDefault();
      if (isOpen) input.focus({ preventScroll: true });
      else openFinder(doc.activeElement || trigger);
    });

    var controller = {
      open: openFinder,
      close: closeFinder,
      filter: filterResults,
      visibleOptions: visibleOptions,
      setRecords: setRecords,
      setSettings: setSettings,
    };
    dialog.lightworkFinder = controller;
    return controller;
  }

  global.LightworkNavFinder = { init: init };
  if (global.document) {
    if (global.document.readyState === "loading") {
      global.document.addEventListener("DOMContentLoaded", function () { init(global.document); });
    } else {
      init(global.document);
    }
  }
})(typeof window !== "undefined" ? window : this);
