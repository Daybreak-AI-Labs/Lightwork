/* Accessible, registry-backed navigation finder for the Lightwork shell.
   The server renders every allowed destination into the dialog. This module
   only filters, ranks and activates those links; it never constructs a route. */
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
    var placedOrder = "";
    // The destinations are server-rendered by GET /nav/finder (the same
    // role-filtered registry as the sidebar) and inserted on first open, so
    // every page does not carry the whole list. The list is never built here.
    var source = results.getAttribute("data-navfinder-src");
    var loaded = !source || pageOptions.length > 0;
    var loading = null;

    function adoptPages() {
      pageOptions = Array.prototype.slice.call(results.querySelectorAll("[data-navfinder-target]"));
      pageOptions.forEach(bindOption);
      options = pageOptions.concat(recordOptions);
      loaded = true;
    }

    function ensureLoaded() {
      if (loaded) return Promise.resolve();
      if (loading) return loading;
      count.textContent = "Loading pages\u2026";
      loading = global.fetch(source, {headers: {"Accept": "text/html"}, credentials: "same-origin"})
        .then(function (response) {
          if (!response.ok) throw new Error("finder unavailable");
          return response.text();
        })
        .then(function (html) {
          results.insertAdjacentHTML("afterbegin", html);
          adoptPages();
          filterResults();
        })
        .catch(function () {
          count.textContent = "Pages could not be loaded. Use the sidebar, or try again.";
        })
        .finally(function () { loading = null; });
      return loading;
    }

    function normalise(value) {
      return String(value || "").replace(/[\u2018\u2019]/g, "'").replace(/\s+/g, " ").trim().toLowerCase();
    }

    // How well a page's own names match what was typed, strongest first: the
    // whole name, its start, the start of each word, then words inside it.
    // Labels outrank the page heading, which outranks a synonym; a match only
    // in the group, address or aliases ranks below every named match.
    var NAME_FIELDS = [["data-label", 3], ["data-heading", 2], ["data-synonyms", 1]];

    function nameTier(name, query, terms) {
      if (name === query) return 4;
      if (name.indexOf(query) === 0) return 3;
      var words = name.split(/[^\p{L}\p{N}']+/u);
      var starts = terms.every(function (term) {
        return words.some(function (word) { return word.indexOf(term) === 0; });
      });
      if (starts) return 2;
      return terms.every(function (term) { return name.indexOf(term) !== -1; }) ? 1 : 0;
    }

    function score(option, query, terms) {
      var best = 0;
      NAME_FIELDS.forEach(function (field) {
        String(option.getAttribute(field[0]) || "").split("|").forEach(function (name) {
          var tier = name ? nameTier(normalise(name), query, terms) : 0;
          if (tier) best = Math.max(best, tier * 4 + field[1]);
        });
      });
      if (best) return best;
      var haystack = normalise(option.getAttribute("data-search"));
      return terms.every(function (term) { return haystack.indexOf(term) !== -1; }) ? 1 : 0;
    }

    // Best matches first; equal matches keep the sidebar's registry order.
    function rankPages(query) {
      var terms = query.split(" ").filter(Boolean);
      return pageOptions.map(function (option, index) {
        return {option: option, index: index, score: terms.length ? score(option, query, terms) : 1};
      }).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
    }

    // Keep the listbox in the order people hear and arrow through: ranked
    // pages first, then records and settings as they arrive.
    function placePages(ranked) {
      if (typeof results.insertBefore !== "function") return;
      var after = null;
      for (var child = results.firstElementChild; child; child = child.nextElementSibling) {
        if (!child.querySelector || !child.querySelector("[data-navfinder-target]")) { after = child; break; }
      }
      ranked.forEach(function (entry) {
        var item = entry.option.parentElement;
        if (item && item.parentElement === results) results.insertBefore(item, after);
      });
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
      var ranked = rankPages(normalise(input.value));
      ranked.forEach(function (entry) {
        var hidden = !entry.score;
        entry.option.hidden = hidden;
        if (entry.option.parentElement) entry.option.parentElement.hidden = hidden;
      });
      var order = ranked.map(function (entry) { return entry.index; }).join(",");
      if (order !== placedOrder) {
        placePages(ranked);
        placedOrder = order;
      }
      options = ranked.map(function (entry) { return entry.option; }).concat(recordOptions, settingOptions);

      var visible = visibleOptions();
      var pages = pageOptions.filter(function (option) { return !option.hidden; }).length;
      if (global.LightworkI18n) {
        var locale = global.LightworkI18n;
        locale.set(count, locale.plural("{count} page", "{count} pages", pages));
        [[recordOptions, "{count} record shown", "{count} records shown"],
         [settingOptions, "{count} setting shown", "{count} settings shown"]].forEach(function (part) {
          if (!part[0].length) return;
          count.appendChild(doc.createTextNode(", "));
          var partCount = doc.createElement("span");
          locale.set(partCount, locale.plural(part[1], part[2], part[0].length));
          count.appendChild(partCount);
        });
      } else count.textContent = (pages === 1 ? "1 page" : pages + " pages") +
        (recordOptions.length ? ", " + recordOptions.length + " records shown" : "") +
        (settingOptions.length ? ", " + settingOptions.length + (settingOptions.length === 1 ? " setting shown" : " settings shown") : "");
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
      if (loaded) filterResults(); else ensureLoaded();
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
        label.lang = "und"; label.dir = "auto"; label.setAttribute("data-user-content", "");
        var description = doc.createElement("span"); description.className = "navfinder__group";
        var reference = doc.createElement("bdi"); reference.lang = "und"; reference.dir = "auto"; reference.setAttribute("data-user-content", "");
        reference.textContent = row.kind + " \u00b7 " + row.id; description.appendChild(reference);
        if (row.revision) {
          description.appendChild(doc.createTextNode(" \u00b7 "));
          var version = doc.createElement("span");
          if (global.LightworkI18n) global.LightworkI18n.set(version, "Version"); else version.textContent = "version";
          var revision = doc.createElement("bdi"); revision.lang = "und"; revision.dir = "auto"; revision.setAttribute("data-user-content", "");
          revision.textContent = row.revision; description.append(version, doc.createTextNode(" "), revision);
        }
        if (row.state) {
          var state = doc.createElement("bdi"); state.lang = "und"; state.dir = "auto"; state.setAttribute("data-user-content", "");
          state.textContent = row.state.replaceAll("_", " "); description.append(doc.createTextNode(" \u00b7 "), state);
        }
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
        var label=doc.createElement('span');label.className='navfinder__label';label.textContent=row.title;label.lang='und';label.dir='auto';label.setAttribute('data-user-content','');
        var group=doc.createElement('span');group.className='navfinder__group';
        var prefix=doc.createElement('span');
        if (global.LightworkI18n) global.LightworkI18n.set(prefix, 'Setting'); else prefix.textContent='Setting';
        var metadata=doc.createElement('bdi');metadata.lang='und';metadata.dir='auto';metadata.setAttribute('data-user-content','');
        metadata.textContent=row.group + (row.effective ? ' - '+row.effective : '') + (row.required_role ? ' - '+row.required_role : '');
        group.append(prefix,doc.createTextNode(' - '),metadata);
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
    if (!loaded) trigger.addEventListener("mouseenter", function () { ensureLoaded(); }, {once: true});

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
