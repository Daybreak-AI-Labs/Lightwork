(function () {
  'use strict';
  var dialog = document.getElementById('navfinder-dialog');
  if (!dialog) return;
  var timer, generation = 0;
  dialog.addEventListener('lightwork:finder-query',function (event) {
    clearTimeout(timer); var current = ++generation, finder = dialog.lightworkFinder;
    if (!finder || !finder.setSettings) return;
    finder.setSettings([]);
    if (event.detail.reason === 'closed' || !event.detail.query.trim()) return;
    timer = setTimeout(async function () {
      try {
        var response = await fetch('/api/v1/settings/search?q=' + encodeURIComponent(event.detail.query.slice(0,200)),{cache:'no-store'});
        if (!response.ok || current !== generation) return;
        var data = await response.json(); if (current !== generation) return;
        finder.setSettings(data.items.filter(function (item) { return /^\/(?:settings(?:[?#].*)?|settings\/all\?.*|preferences(?:#preference-[a-z_]+)?|notifications#notification-preferences|workspace\/tenant#tsel-title|channels|connections|plugins|learning)$/.test(item.href); }));
      } catch (_failure) { /* The existing finder stays usable when settings search is unavailable. */ }
    },250);
  });
})();
