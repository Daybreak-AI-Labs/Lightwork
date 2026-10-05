/* Own work and prices through the current native department projection. */
(function () {
  'use strict';
  var shell = document.getElementById('department-board-shell');
  if (!shell) return;
  var board = shell.dataset.board, context = shell.dataset.context, locked = false, invalid = false, generation = 0, requests = new Set();
  var days = document.getElementById('department-board-days'), refresh = document.getElementById('department-board-refresh');
  var status = document.getElementById('department-board-status'), error = document.getElementById('department-board-error');
  var targets = ['metrics', 'statuses', 'groups', 'records'].map(function (key) { return document.getElementById('department-board-' + key); });
  var labels = {active:'In progress',done:'Finished',blocked:'Waiting',queued:'Queued',failed:'Stopped with an error',cancelled:'Cancelled',draft:'Draft',success:'Succeeded',running:'Still running'};
  function node(tag, text, parent) { var result = document.createElement(tag); if (text !== undefined) result.textContent = text; if (parent) parent.appendChild(result); return result; }
  function number(value) { return Number(value || 0).toLocaleString(); }
  function money(value) { return new Intl.NumberFormat(undefined, {style:'currency', currency:'USD',minimumFractionDigits:2,maximumFractionDigits:4}).format(value || 0); }
  function knownPrice(row,key) { return row.runs && row.unknown_prices===row.runs ? 'Price unavailable' : money(row[key]); }
  function clear() { targets.forEach(function (target) { target.replaceChildren(); }); }
  function metric(label, value, hint) { var card = node('section',undefined,targets[0]);card.className='panel department-board__card';node('h2',label,card);node('p',value,card).className='department-board__metric';if(hint)node('p',hint,card).className='muted'; }
  function list(target, values, empty) {
    if (!values.length) { node('p',empty,target).className='muted';return; }
    var ul=node('ul',undefined,target);ul.className='department-board__list';
    values.forEach(function (value) { var li=node('li',undefined,ul);var title=node(value.href?'a':'span',value.label,li);if(value.href)title.href=value.href;node('span',value.detail,li); });
  }
  function paint(data) {
    clear();
    if (board==='overview') {
      metric('Your tasks',number(data.totals.total),'All time within your access');
      metric('In progress',number(data.totals.active),'Current task status');
      metric('Started',number(data.window.started),'In the last '+data.days+' days');
      metric('Finished',number(data.window.delivered),'Last updated in this period');
      list(targets[1],Object.keys(data.by_status).map(function(key){return {label:labels[key]||'Other task state',detail:number(data.by_status[key])};}),'No permitted tasks on record.');
      list(targets[2],data.domains.map(function(row){return {label:row.label,detail:number(row.done)+' finished / '+number(row.total)+' total'};}),'No department work on record.');
      list(targets[3],data.recent.map(function(row){return {label:row.title,href:'/chat/goal/'+row.id,detail:labels[row.status]||'Other task state'};}),'No recent permitted tasks.');
    } else {
      metric('Known runtime cost',knownPrice(data.totals,'dollars'),'Finished attempts in this period');
      metric('Finished attempts',number(data.totals.runs),'Retries are separate attempts');
      metric('Missing prices',number(data.totals.unknown_prices),'Excluded from the known cost');
      metric('Known cost, all time',knownPrice(data.all_time,'dollars'),number(data.all_time.unknown_prices)+' all-time attempts have missing prices');
      list(targets[1],Object.keys(data.outcomes).map(function(key){return {label:labels[key]||'Other attempt state',detail:number(data.outcomes[key])};}),'No attempts in this period.');
      list(targets[2],data.top_goals.map(function(row){return {label:row.title,href:'/chat/goal/'+row.goal_id,detail:knownPrice(row,'cost')+' · '+number(row.runs)+' attempts'+(row.unknown_prices?' · '+number(row.unknown_prices)+' missing prices':'')};}),'No finished permitted attempts in this period.');
      list(targets[3],data.daily.filter(function(row){return row.runs;}).reverse().map(function(row){return {label:new Date(row.d+'T12:00:00Z').toLocaleDateString(),detail:knownPrice(row,'cost')+' · '+number(row.runs)+' attempts'+(row.unknown_prices?' · '+number(row.unknown_prices)+' missing prices':'')};}),'No recorded prices in this period.');
    }
    status.textContent='Checked '+new Date(data.generated_at*1000).toLocaleString()+'. '+data.scope_notice;
  }
  async function load() {
    if (locked||invalid) return;
    var started=++generation;locked=true;refresh.disabled=true;days.disabled=true;error.hidden=true;status.textContent='Reading your permitted work…';
    var controller=new AbortController();requests.add(controller);var timeout=setTimeout(function(){controller.abort();},15000);
    try {
      var response=await fetch('/api/v1/dashboards/'+encodeURIComponent(board)+'?days='+encodeURIComponent(days.value),{credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'X-Lightwork-Board-Context':context}});
      var data=await response.json();if(started!==generation||invalid)return;
      if(!response.ok){var problem=new Error(typeof data.detail==='string'?data.detail:'This report is unavailable. Try refreshing it.');problem.status=response.status;throw problem;}
      if(data.context!==context||data.scope!=='permitted_own'){var changed=new Error('Your account, workspace or department access changed. Reload this page.');changed.status=409;throw changed;}
      paint(data);
    } catch(problem) {
      if(started!==generation)return;clear();error.hidden=false;error.textContent=problem.name==='AbortError'?'The report took too long to read. Refresh to try again.':problem.message||'The report could not be read. Refresh to try again.';status.textContent='No current report is shown.';
      if([401,403,409].includes(problem.status)){invalid=true;document.getElementById('department-board-reload').hidden=false;}
    } finally {clearTimeout(timeout);requests.delete(controller);if(started===generation){locked=false;refresh.disabled=invalid;days.disabled=invalid;}}
  }
  refresh.addEventListener('click',load);days.addEventListener('change',load);
  function contextChanged(){clear();generation++;requests.forEach(function(controller){controller.abort();});locked=false;load();}
  document.addEventListener('lightwork:copilot-context-changed',contextChanged);
  document.addEventListener('lightwork:account-context-changed',contextChanged);
  document.addEventListener('lightwork:workspace-changed',contextChanged);
  window.addEventListener('lightwork:context-changed',contextChanged);
  window.addEventListener('pageshow',function(event){if(event.persisted){clear();load();}});
  load();
})();
