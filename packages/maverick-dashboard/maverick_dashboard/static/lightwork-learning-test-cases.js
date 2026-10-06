/* Reviewed ordinary authoring, using the real shared self-coaching collection. */
(function () {
  'use strict';
  var root = document.getElementById('learning-test-questions'); if (!root) return;
  var form = document.getElementById('learning-case-form'), model = document.getElementById('learning-case-model');
  var question = document.getElementById('learning-case-question'), expected = document.getElementById('learning-case-expected');
  var reviewButton = document.getElementById('learning-case-review'), refresh = document.getElementById('learning-case-refresh');
  var status = document.getElementById('learning-case-status'), preview = document.getElementById('learning-case-preview');
  var result = document.getElementById('learning-case-result'), original = document.getElementById('learning-case-original');
  var setup = document.getElementById('learning-case-setup'), setupReview = document.getElementById('learning-case-setup-review'), setupOriginal = document.getElementById('learning-case-setup-original');
  var list = document.getElementById('learning-case-list'), pageStatus = document.getElementById('learning-case-page-status');
  var previous = document.getElementById('learning-case-previous'), next = document.getElementById('learning-case-next');
  var editing = document.getElementById('learning-case-editing'), newQuestion = document.getElementById('learning-case-new'), retire = document.getElementById('learning-case-retire');
  var API = '/api/v1/learning-test-cases', current = null, busy = false, generation = 0, reviewed = null, pending = null, recovery = null, unsaved = false;
  var selectedCase = null, pageOffset = 0, nextOffset = null, pendingSetup = null;
  function node(tag, text, parent) { var item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (parent) parent.append(item); return item; }
  function lock(value) { busy = value; [model, question, expected].forEach(function (item) { item.disabled = value || !current || !!current.blocked; }); reviewButton.disabled = value || !current || !!current.blocked || !current.revision; refresh.disabled = value; original.disabled = setupOriginal.disabled = value; setup.hidden = !current || !current.setup_required; setupReview.disabled = value || !current || !current.revision || !!current.setup_blocked; [previous,next,newQuestion,retire].forEach(function(item){item.disabled=value;}); retire.hidden = !selectedCase || !current || !current.revision; newQuestion.hidden = !selectedCase; }
  function invalidate() { reviewed = null; preview.replaceChildren(); }
  function selection() { document.dispatchEvent(new CustomEvent('lightwork:copilot-selection', {detail: {selection: selectedCase ? {kind:'learning_test_case',model:selectedCase.model,reference:selectedCase.reference} : {}}})); }
  function resetSelection() { selectedCase = null; editing.textContent = 'Add a new question.'; reviewButton.textContent = 'Review question'; selection(); }
  function recoveryReady() {
    if (recovery || !window.mvDraftRecovery) return;
    recovery = window.mvDraftRecovery.create({host: form, key: 'self-coaching-test-question', compact: true,
      read: function () { var value={model: model.value, question: question.value, expected: expected.value}; if(selectedCase)value.reference=selectedCase.reference;return value; },
      restore: function (value) {
        if (busy || !current || !value || !['expected,model,question','expected,model,question,reference'].includes(Object.keys(value).sort().join(',')) ||
            typeof value.model !== 'string' || typeof value.question !== 'string' || typeof value.expected !== 'string' ||
            value.model.length > 200 || value.question.length > 4000 || value.expected.length > 4000 || value.reference && !/^[a-f0-9]{64}$/.test(value.reference))
          throw new Error('Refresh the current collection before restoring this question. Current entered work is kept.');
        model.value = current.models.some(function (row) { return row.value === value.model; }) ? value.model : '';
        question.value = value.question; expected.value = value.expected; invalidate(); unsaved = true;
        resetSelection(); if(value.reference){selectedCase={model:value.model,reference:value.reference};editing.textContent='Restored correction. Its original question will be checked again during review.';reviewButton.textContent='Review correction';selection();}
        lock(false);status.textContent = 'Draft restored for review. Nothing has been changed in the shared collection.';
      }});
  }
  function saved(context, isSetup) { context = context || (current && current.context); if (!context) return null; try { var row = JSON.parse(sessionStorage.getItem((isSetup ? 'learning-case-setup:' : 'learning-case-save:') + context) || 'null'); return row && row.context === context && /^[a-f0-9]{32}$/.test(row.operation) ? row : null; } catch (_) { return null; } }
  function remember(operation, context, isSetup) { var row = {operation: operation, context: context}; sessionStorage.setItem((isSetup ? 'learning-case-setup:' : 'learning-case-save:') + context, JSON.stringify(row)); if(isSetup){pendingSetup=row;setupOriginal.hidden=false;}else{pending=row;original.hidden=false;} }
  async function request(path, body) {
    var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, 20000);
    try {
      var response = await fetch(API + path, {method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined, signal: controller.signal});
      var value = await response.json().catch(function () { return {}; });
      if (response.redirected || !response.ok) {
        var error = new Error(typeof value.detail === 'string' ? value.detail : 'This question could not be read or saved. Check the original outcome before another attempt.');
        error.status = response.status;
        error.accessChanged = response.redirected || response.status === 401 || response.status === 403 || response.status === 409 && typeof value.detail === 'string' && value.detail.startsWith('Your account or workspace changed.');
        throw error;
      }
      return value;
    } finally { clearTimeout(timer); }
  }
  function lines(parent, values) {
    var list = node('dl', undefined, parent);
    values.forEach(function (value) { node('dt', value[0], list); var text = node('dd', value[1], list); text.style.whiteSpace = 'pre-wrap'; text.style.overflowWrap = 'anywhere'; });
  }
  function showOriginal(row) {
    result.replaceChildren(); node('h4', 'Original saved outcome', result); node('p', row.message, result);
    if (row.question) lines(result, [['Model', row.model], ['Question', row.question], ['Expected answer', row.expected], ['Current collection', row.current]]);
    else if(row.current) lines(result,[['Current readiness',row.current]]);
  }
  async function readOriginal(operation, isSetup) {
    if (busy) return; var started = ++generation; invalidate(); lock(true);
    try {
      var row = await request((isSetup?'/setup':'') + '/requests/' + encodeURIComponent(operation)); if (started !== generation) return;
      if (current && row.context !== current.context) { var contextError = new Error('Your account or workspace changed. Refresh before continuing.'); contextError.accessChanged = true; throw contextError; }
      if(isSetup){pendingSetup={operation:operation,context:row.context};setupOriginal.hidden=false;}else{pending = {operation: operation, context: row.context}; original.hidden = false;} showOriginal(row);
    } catch (error) { if (started === generation) { if(error.accessChanged){changed();return;}result.replaceChildren(); node('p', error.message + ' Nothing was repeated.', result); } }
    finally { if (started === generation) lock(false); }
  }
  async function load() {
    if (busy) return; var selected = model.value, started = ++generation; invalidate(); lock(true);
    try {
      var state = await request(''); if (started !== generation) return;
      if (current && current.context !== state.context) { question.value = ''; expected.value = ''; pending = pendingSetup = null; unsaved = false; result.replaceChildren();resetSelection(); if (recovery) { recovery.dispose(); recovery.panel.remove(); recovery = null; } }
      current = state; model.replaceChildren(); var empty = node('option', 'Choose a model', model); empty.value = '';
      state.models.forEach(function (row) { var item = node('option', row.label + ' / ' + row.questions + ' saved questions', model); item.value = row.value; });
      if (state.models.some(function (row) { return row.value === selected; })) model.value = selected;
      status.textContent = state.blocked || state.total + ' questions are saved in the current shared collection. Existing questions stay in place.';
      pending = saved(); original.hidden = !pending; pendingSetup=saved(null,true);setupOriginal.hidden=!pendingSetup;recoveryReady();
      list.replaceChildren(); previous.hidden=next.hidden=true; pageStatus.textContent='Choose a model to browse its current questions.';
    } catch (error) {
      if (started === generation) {
        if(error.accessChanged){changed();return;}
        current = null; model.replaceChildren(); var emptyOption = node('option', 'Current models unavailable', model); emptyOption.value = ''; status.textContent = error.message;
        try { var identity = await request('/context'); if (started === generation) { pending = saved(identity.context); original.hidden = !pending;pendingSetup=saved(identity.context,true);setupOriginal.hidden=!pendingSetup; } } catch (contextFailure) { if (started === generation) { if(contextFailure.accessChanged){changed();return;}pending = pendingSetup = null; original.hidden = setupOriginal.hidden = true; } }
      }
    }
    finally { if (started === generation) lock(false); }
  }
  [model, question, expected].forEach(function (item) {
    function edited() { invalidate(); unsaved = !!(question.value || expected.value); }
    item.addEventListener('input', edited); item.addEventListener('change', edited);
  });
  async function browse(offset) {
    if(busy||!current||!model.value)return;var started=++generation;invalidate();lock(true);
    try{var page=await request('/cases?model='+encodeURIComponent(model.value)+'&offset='+offset);if(started!==generation)return;
      if(page.context!==current.context){var contextError=new Error('Your account or workspace changed. Refresh before continuing.');contextError.accessChanged=true;throw contextError;}
      if(offset&&page.revision!==current.revision){lock(false);await browse(0);return;}
      current.revision=page.revision;pageOffset=page.offset;nextOffset=page.next;list.replaceChildren();
      pageStatus.textContent=page.total+' saved questions. '+(page.cases.length?'Choose a question to review or change it.':'No questions are saved for this model.');
      bulkRows.clear();bulkModel=page.model;page.cases.forEach(function(row){var box=node('article',undefined,list);box.className='stack';if(!row.supported){node('h4',row.question,box);node('p',row.notice,box);return;}if(current.revision){box.dataset.bulkId=row.value;box.dataset.bulkLabel=String(row.question).slice(0,120);bulkRows.set(row.value,row);}var choose=node('button',row.question,box);choose.type='button';choose.className='btn btn--ghost';choose.style.whiteSpace='pre-wrap';choose.style.overflowWrap='anywhere';choose.style.maxWidth='100%';choose.addEventListener('click',async function(){if(busy)return;if(unsaved&&window.mvConfirm&&!await window.mvConfirm('Replace the entered draft with this saved question? Your existing recovery copy remains separate.',{okText:'Open saved question'}))return;selectedCase={model:page.model,reference:row.value};question.value=row.question;expected.value=row.expected;unsaved=false;invalidate();editing.textContent='Correct the selected saved question. Other saved information stays in place.';reviewButton.textContent='Review correction';selection();lock(false);question.focus();});});
      previous.hidden=!pageOffset;next.hidden=nextOffset===null;bulkReady();
    }catch(error){if(started===generation){if(error.accessChanged){changed();return;}list.replaceChildren();pageStatus.textContent=error.message;}}
    finally{if(started===generation)lock(false);}
  }
  // Retire many saved questions at once (ADM-7): filter, select all
  // matching, one counted confirmation, then a report of each question. Each
  // retirement goes through the same review and save as a single one.
  var bulkRows=new Map(),bulkModel='',bulk=null;
  function bulkReady(){
    if(!window.mvBulk)return;
    if(bulk){bulk.refresh();return;}
    if(!list.querySelector('article[data-bulk-id]'))return;
    bulk=window.mvBulk({list:list,rows:'article[data-bulk-id]',noun:'question',nouns:'questions',filter:true,
      actions:[{label:'Retire',verb:'Retire',danger:true,note:'Retired questions leave future checks. Their history stays.',
        run:async function(item){
          var row=bulkRows.get(item.id);if(!row||busy)throw{bulkReason:'Refresh the current collection before retiring questions.'};
          try{
            var state=await request('');
            if(!current||state.context!==current.context)throw new Error('Your account or workspace changed. Refresh before continuing.');
            var reviewed=await request('/change-review',{context:state.context,revision:state.revision,model:bulkModel,question:row.question,expected:row.expected,reference:item.id,action:'retire'});
            var receipt=await request('/save',{token:reviewed.token,confirmed:true});
            if(!receipt.confirmed)throw new Error(receipt.message||'Lightwork could not confirm the retirement.');
          }catch(error){throw{bulkReason:error.bulkReason||error.message};}
          return{note:'retired'};
        },
        after:function(){if(current)current.revision='';void load().then(function(){return browse(0);});}}]});
  }
  model.addEventListener('change',function(){resetSelection();list.replaceChildren();pageOffset=0;nextOffset=null;void browse(0);});
  document.getElementById('learning-case-browse').addEventListener('toggle',function(event){if(event.currentTarget.open&&!list.childElementCount)void browse(0);});
  previous.addEventListener('click',function(){void browse(Math.max(0,pageOffset-50));});next.addEventListener('click',function(){if(nextOffset!==null)void browse(nextOffset);});
  newQuestion.addEventListener('click',async function(){if(busy)return;if(unsaved&&window.mvConfirm&&!await window.mvConfirm('Start a new question instead of this entered correction?',{okText:'Start new question'}))return;resetSelection();question.value='';expected.value='';unsaved=false;invalidate();lock(false);question.focus();});
  window.addEventListener('beforeunload', function (event) { if (unsaved || busy && reviewed) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('lightwork:drafts-ready', recoveryReady);
  refresh.addEventListener('click', load);
  original.addEventListener('click', function () { var row = pending || saved(); if (row) readOriginal(row.operation); });
  setupOriginal.addEventListener('click',function(){var row=pendingSetup||saved(null,true);if(row)readOriginal(row.operation,true);});
  setupReview.addEventListener('click',function(){void prepare('setup');});
  async function prepare(action) {
    if (busy || !current || action!=='retire'&&action!=='setup'&&!form.reportValidity()) return;
    var started = ++generation; invalidate(); lock(true); result.replaceChildren();
    try {
      var body={context: current.context, revision: current.revision, model: model.value, question: question.value, expected: expected.value};if(action==='setup'){body={context:current.context,revision:current.revision};}else if(action){if(!selectedCase||selectedCase.model!==model.value)throw new Error('Select a current saved question before changing it.');body.reference=selectedCase.reference;body.action=action;}
      var row = await request(action==='setup'?'/setup/review':action?'/change-review':'/review', body);
      if (started !== generation) return; reviewed = row;
      node('h4', action==='setup'?'Review learning-question setup':action==='retire'?'Review this question retirement':action?'Review this complete correction':'Review this complete question', preview);
      row.changes.forEach(function (change) { lines(preview, [[change.label, change.before === undefined ? String(change.after) : String(change.before) + ' → ' + String(change.after)]]); });
      var label = node('label', undefined, preview), consent = node('input', undefined, label); consent.type = 'checkbox'; label.append(document.createTextNode(action==='setup'?' I reviewed the storage choice, shared use and exact setting change.':' I reviewed the complete question, expected answer and shared use.'));
      var apply = node('button', action==='setup'?'Save reviewed setup':action==='retire'?'Retire reviewed question':action?'Save reviewed correction':'Save reviewed question', preview); apply.type = 'button'; apply.className = 'btn btn--primary'; apply.disabled = true;
      consent.addEventListener('change', function () { apply.disabled = busy || !consent.checked; });
      apply.addEventListener('click', async function () {
        if (busy || !consent.checked || reviewed !== row || started !== generation) return;
        try { remember(row.operation, row.context, action==='setup'); } catch (_) { status.textContent = 'The original request could not be kept in this browser. Nothing was submitted. Allow site storage before continuing.'; return; }
        lock(true); apply.disabled = true;
        try {
          var receipt = await request('/save', {token: row.token, confirmed: true}); if (started !== generation) return;
          invalidate(); showOriginal(receipt); status.textContent = receipt.message + ' Refresh the current collection before adding another question.';
          if (receipt.confirmed&&action!=='setup') { unsaved = false; if (recovery) recovery.clear(); resetSelection();list.replaceChildren(); }
        } catch (error) { if (started === generation) { if(error.accessChanged){changed();return;}invalidate(); status.textContent = 'The original save reply is unconfirmed. Check its original outcome before another attempt; nothing will be repeated automatically. ' + (error.name === 'AbortError' ? '' : error.message); } }
        finally { if (started === generation) { if (current) current.revision = ''; lock(false); } }
        if(action==='setup'&&started===generation)await load();
      });
      var cancel = node('button', 'Cancel this review', preview); cancel.type = 'button'; cancel.className = 'btn btn--ghost'; cancel.addEventListener('click', function () { if (!busy) { invalidate(); reviewButton.focus(); } });
      consent.focus(); status.textContent = 'Review every field before saving. No question has been added yet.';
    } catch (error) { if (started === generation) {if(error.accessChanged){changed();return;}status.textContent = error.message;} }
    finally { if (started === generation) lock(false); }
  }
  form.addEventListener('submit',function(event){event.preventDefault();void prepare(selectedCase?'correct':null);});
  retire.addEventListener('click',function(){void prepare('retire');});
  function changed() { generation++; current = null; pending = pendingSetup = null; unsaved = false; if (recovery) { recovery.dispose(); recovery.panel.remove(); recovery = null; } invalidate(); model.replaceChildren(); question.value = ''; expected.value = ''; result.replaceChildren();list.replaceChildren();resetSelection(); original.hidden = setupOriginal.hidden = true; status.textContent = 'Your account, workspace or access changed. Refresh before continuing.'; lock(false); }
  document.addEventListener('lightwork:workspace-changed', changed); document.addEventListener('lightwork:account-context-changed', changed); window.addEventListener('lightwork:context-changed', changed);
  document.addEventListener('lightwork:copilot-context-changed',function(event){if(event.detail?.reason!=='selection')changed();});
  document.addEventListener('lightwork:product-operation-complete',function(event){if(!current||!['learning.setup_test_questions','learning.add_test_question','learning.correct_test_question','learning.retire_test_question'].includes(event.detail?.operation)||!['confirmed','replayed'].includes(event.detail?.status))return;current.revision='';invalidate();list.replaceChildren();previous.hidden=next.hidden=true;status.textContent='Copilot saved a learning-question change. Your entered work is kept. Refresh the current collection before another review.';lock(busy);});
  var operation = new URLSearchParams(window.location.search).get('learning_case_request');
  var setupOperation = new URLSearchParams(window.location.search).get('learning_case_setup');
  load().then(function () { if (/^[a-f0-9]{32}$/.test(setupOperation || '')) readOriginal(setupOperation,true);else if (/^[a-f0-9]{32}$/.test(operation || '')) readOriginal(operation); });
}());
