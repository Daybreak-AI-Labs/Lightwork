(function () {
  'use strict';
  var cards = Array.from(document.querySelectorAll('[data-template-name]'));
  var suggest = document.getElementById('templates-suggested');
  var all = document.getElementById('templates-all');
  var status = document.getElementById('templates-status');
  if (!suggest) return;
  cards.forEach(function (card) {
    var open = card.querySelector('[data-prepare-template]');
    if (!open) return;
    var form = card.querySelector('.template-prepare');
    open.addEventListener('click', function () { form.hidden = false; var first = form.querySelector('textarea, button'); if (first) first.focus(); });
    form.querySelector('[data-cancel-prepare]').addEventListener('click', function () { form.hidden = true; open.focus(); });
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var button = form.querySelector('[type="submit"]'); var message = form.querySelector('.prepare-status');
      if (button.disabled || !form.reportValidity()) return;
      var params = Object.create(null);
      form.querySelectorAll('[data-template-param]').forEach(function (input) { params[input.dataset.templateParam] = input.value; });
      button.disabled = true; message.textContent = 'Preparing your task…';
      try {
        var response = await fetch('/api/v1/templates/' + encodeURIComponent(card.dataset.templateName) + '/prepare', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({params:params})});
        var result = await response.json();
        if (!response.ok || result.started !== false || typeof result.title !== 'string' || typeof result.description !== 'string' || typeof result.context !== 'string') throw new Error(typeof result.detail === 'string' ? result.detail : 'The task could not be prepared. Your inputs are still here.');
        sessionStorage.setItem('lightwork:template-draft:v1', JSON.stringify({title:result.title, description:result.description, context:result.context, created_at:Date.now()}));
        window.location.assign('/chat');
      } catch (error) { message.textContent = error.message || 'Could not prepare the task. Your inputs are still here.'; button.disabled = false; }
    });
  });
  var originalOrder = cards.slice();
  all.addEventListener('click', function () {
    originalOrder.forEach(function (card) { card.hidden = false; card.parentElement.append(card); });
    all.hidden = true; status.textContent = 'Showing all templates.';
  });
  suggest.addEventListener('click', async function () {
    if (suggest.disabled) return;
    suggest.disabled = true; status.textContent = 'Finding suggestions…';
    try {
      var response = await fetch('/api/v1/templates/suggested', {cache:'no-store'});
      if (!response.ok) throw new Error('Suggestions are unavailable. All templates are still available below.');
      var result = await response.json();
      if (!Array.isArray(result.suggested)) throw new Error('Suggestions could not be read. Try again.');
      var names = result.suggested.map(function (item) { return item.name; });
      var matches = cards.filter(function (card) { return names.includes(card.dataset.templateName); });
      if (!matches.length) { status.textContent = 'No suggestions yet. Browse all templates below.'; return; }
      cards.forEach(function (card) { card.hidden = !names.includes(card.dataset.templateName); });
      matches.sort(function (a,b) { return names.indexOf(a.dataset.templateName) - names.indexOf(b.dataset.templateName); }).forEach(function (card) { card.parentElement.append(card); });
      all.hidden = false; status.textContent = 'Suggestions based on your task history. Review before use.';
    } catch (error) { status.textContent = error.message || 'Could not reach Lightwork. Try again.'; }
    finally { suggest.disabled = false; }
  });
  document.querySelectorAll('.template-rating').forEach(function (form) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var button = form.querySelector('button'); var input = form.querySelector('select');
      if (button.disabled || !form.reportValidity()) return;
      var card = form.closest('[data-template-name]'); var message = form.querySelector('.rating-status');
      var stars = Number(input.value);
      button.disabled = true; input.disabled = true; message.textContent = 'Saving…';
      try {
        var response = await fetch('/api/v1/templates/' + encodeURIComponent(card.dataset.templateName) + '/rating', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({stars:stars})});
        var result = await response.json();
        if (!response.ok || result.saved !== true || result.name !== card.dataset.templateName || result.stars !== stars) throw new Error('The rating could not be confirmed. Try again.');
        card.querySelector('.tplm__stars').textContent = '★'.repeat(stars) + '☆'.repeat(5-stars);
        message.textContent = 'Workspace rating saved.';
      } catch (error) { message.textContent = error.message || 'Could not save the rating. Try again.'; }
      finally { button.disabled = false; input.disabled = false; }
    });
  });
})();
