
  (function () {
    'use strict';
    var index = document.querySelector('.settings-index');
    var tabs = Array.prototype.slice.call(document.querySelectorAll('[data-settings-tab]'));
    var grouped = Array.prototype.slice.call(document.querySelectorAll('[data-settings-group]'));
    var search = document.getElementById('settings-search');
    var filterStatus = document.getElementById('settings-filter-status');
    var groups = ['general', 'models', 'integrations', 'controls', 'access', 'maintenance'];
    var aliases = {appearance: 'general', budget: 'general', providers: 'integrations', webhooks: 'integrations', capabilities: 'controls', features: 'controls', visibility: 'access', voice: 'models', discovery: 'models', connections: 'integrations', system: 'maintenance'};
    var active = (index && index.dataset.initialGroup) || 'general';
    var hash = String(window.location.hash || '').replace(/^#settings-?/, '').replace(/^#/, '');
    if (aliases[hash]) hash = aliases[hash];
    if (groups.indexOf(hash) !== -1) active = hash;

    function sections() {
      return grouped.filter(function (node) { return node.classList.contains('section-head'); }).map(function (head) {
        var panel = head.nextElementSibling;
        return {head: head, panel: panel, group: head.dataset.settingsGroup, title: head.textContent.trim()};
      });
    }
    function selectGroup(group, focus) {
      active = groups.indexOf(group) === -1 ? 'general' : group;
      if (search) search.value = '';
      grouped.forEach(function (node) { node.hidden = node.dataset.settingsGroup !== active; });
      tabs.forEach(function (tab) {
        var selected = tab.dataset.settingsTab === active;
        tab.setAttribute('aria-selected', selected ? 'true' : 'false');
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focus) tab.focus();
      });
      document.querySelectorAll('[data-settings-search-item]').forEach(function (item) { item.hidden = false; });
      if (filterStatus) filterStatus.textContent = sections().filter(function (section) { return section.group === active; }).length + ' sections shown.';
      if (window.history && window.history.replaceState) window.history.replaceState(null, '', '#settings-' + active);
    }
    function applySearch() {
      var query = String(search.value || '').trim().toLocaleLowerCase();
      if (!query) { selectGroup(active, false); return; }
      var visible = 0;
      sections().forEach(function (section) {
        var items = Array.prototype.slice.call(section.panel.querySelectorAll('[data-settings-search-item]'));
        var titleMatch = section.title.toLocaleLowerCase().indexOf(query) !== -1;
        var itemMatches = 0;
        items.forEach(function (item) {
          var match = item.textContent.toLocaleLowerCase().indexOf(query) !== -1;
          item.hidden = !titleMatch && !match;
          if (match) itemMatches += 1;
        });
        var genericMatch = !items.length && section.panel.textContent.toLocaleLowerCase().indexOf(query) !== -1;
        var show = titleMatch || itemMatches > 0 || genericMatch;
        section.head.hidden = !show;
        section.panel.hidden = !show;
        if (show) visible += 1;
      });
      if (filterStatus) filterStatus.textContent = visible ? visible + ' matching sections shown.' : 'No settings match “' + search.value.trim() + '”.';
    }
    tabs.forEach(function (tab, position) {
      tab.addEventListener('click', function () { selectGroup(tab.dataset.settingsTab, false); });
      tab.addEventListener('keydown', function (event) {
        var direction = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        if (!direction) return;
        event.preventDefault();
        var next = (position + direction + tabs.length) % tabs.length;
        selectGroup(tabs[next].dataset.settingsTab, true);
      });
    });
    if (search) search.addEventListener('input', applySearch);
    selectGroup(active, false);

    var forms = Array.prototype.slice.call(document.querySelectorAll('form[action^="/settings"]'));
    var dirty = new Set();

    function sectionName(form) {
      var node = form.closest('.panel');
      while (node && node.previousElementSibling) {
        node = node.previousElementSibling;
        var heading = node.querySelector && node.querySelector('.section-head h2, h2');
        if (heading && heading.textContent.trim()) return heading.textContent.trim();
      }
      return 'another section';
    }
    function markDirty(event) { dirty.add(event.currentTarget); }

    forms.filter(function (form) { return form.dataset.settingsLocal !== 'true'; }).forEach(function (form) {
      form.addEventListener('input', markDirty);
      form.addEventListener('change', markDirty);
      form.addEventListener('submit', function (event) {
        if (form.dataset.settingsSubmitApproved === 'true') {
          delete form.dataset.settingsSubmitApproved;
          dirty.clear();
          return;
        }
        var otherDirty = Array.from(dirty).filter(function (candidate) {
          return candidate !== form;
        });
        if (!otherDirty.length) {
          dirty.delete(form);
          return;
        }
        event.preventDefault();
        var submitter = event.submitter || null;
        var sections = otherDirty.map(sectionName).filter(function (name, index, all) {
          return all.indexOf(name) === index;
        });
        var prompt = 'Saving this section reloads Settings and will discard unsaved changes in '
          + sections.join(', ') + '. Continue?';
        window.mvConfirm(prompt, { okText: 'Discard others and save' }).then(function (approved) {
          if (!approved) return;
          dirty.clear();
          form.dataset.settingsSubmitApproved = 'true';
          if (form.requestSubmit) form.requestSubmit(submitter || undefined);
          else form.submit();
        });
      });
    });

    document.querySelectorAll('[data-model-picker]').forEach(function (select) {
      var custom = document.getElementById(select.dataset.modelPicker);
      if (!custom) return;
      var field = custom.tagName === 'INPUT' ? custom : custom.querySelector('input');
      function sync(focus) {
        var typed = select.value === '__custom__';
        custom.hidden = !typed;
        if (typed && focus && field) field.focus();
      }
      select.addEventListener('change', function () { sync(true); });
      sync(false);
    });
    document.querySelectorAll('[data-model-select-all], [data-model-select-none]').forEach(function (button) {
      button.addEventListener('click', function () {
        var group = button.closest('.model-group');
        var checked = button.hasAttribute('data-model-select-all');
        if (!group) return;
        group.querySelectorAll('input[type="checkbox"]').forEach(function (box) { box.checked = checked; });
        var form = button.closest('form');
        if (form) dirty.add(form);
      });
    });
    document.querySelectorAll('[data-settings-jump]').forEach(function (link) {
      link.addEventListener('click', function (event) {
        event.preventDefault();
        selectGroup(link.dataset.settingsJump, true);
      });
    });

    window.addEventListener('beforeunload', function (event) {
      if (!dirty.size) return;
      event.preventDefault();
      event.returnValue = '';
    });
  })();
  