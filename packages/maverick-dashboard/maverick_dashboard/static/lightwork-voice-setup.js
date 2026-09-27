/* Optional, explicit setup of the deployment's pinned speech-to-text model. */
(function () {
  'use strict';
  var el = function (name) { return document.getElementById('voice-setup-' + name); };
  if (!el('status')) return;
  var base = '/api/v1/voice/setup', state = null, context = null;
  var locked = false, writing = false, reading = false, generation = 0, timer = null, readController = null;
  var activeStates = ['checking', 'downloading', 'cancelling'];
  function active() { return state && state.job && activeStates.includes(state.job.state); }
  function text(name, value) { el(name).textContent = value; }
  function stopTimer() { window.clearTimeout(timer); timer = null; }
  function controls() {
    var allowed = !locked && !writing && state && state.enabled && state.can_download && state.model && !active();
    el('confirm').disabled = !allowed;
    el('start').disabled = !allowed || !el('confirm').checked;
    el('cancel').disabled = locked || writing || !active() || state.job.state === 'cancelling';
    el('refresh').disabled = locked || writing || reading;
  }
  function clearPrivate(message) {
    locked = true; state = null; context = null; generation += 1; stopTimer();
    ['model', 'model-details', 'technical', 'policy', 'readiness', 'progress-text'].forEach(function (name) { text(name, ''); });
    el('try').hidden = true;
    el('progress').hidden = true; el('progress').removeAttribute('value'); el('confirm').checked = false;
    text('status', message); controls();
  }
  function problem(message) {
    state = null; stopTimer(); el('confirm').checked = false; el('progress').hidden = true; el('try').hidden = true;
    text('progress-text', ''); text('status', message + ' Refresh setup status before trying again; an earlier download may still be running.'); controls();
  }
  function valid(data) {
    function object(value) { return value && typeof value === 'object' && !Array.isArray(value); }
    function bounded(value, maximum) { return Number.isSafeInteger(value) && value >= 0 && value <= maximum; }
    if (!object(data) || !/^[a-f0-9]{64}$/.test(data.context || '') || typeof data.enabled !== 'boolean' ||
        typeof data.can_download !== 'boolean' || typeof data.engine_available !== 'boolean' ||
        !['missing', 'present_unverified', 'verified'].includes(data.model_state) || !object(data.configuration) ||
        typeof data.configuration.managed_path !== 'boolean' || typeof data.configuration.auto_fetch !== 'boolean' ||
        typeof data.configuration.backend !== 'string' || data.configuration.backend.length > 100 ||
        !(data.reason === null || typeof data.reason === 'string' && data.reason.length <= 2000)) return false;
    var model = data.model;
    if (model !== null && (!object(model) || !/^(tiny|base|small|medium)(\.en)?$|^large-v3(-turbo)?$/.test(model.id || '') ||
        !/^[a-f0-9]{64}$/.test(model.sha256 || '') || !bounded(model.size_bytes, 10 * 1024 ** 3) || model.size_bytes < 1 ||
        model.source_url !== 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-' + model.id + '.bin')) return false;
    if (data.can_download && (!model || data.configuration.managed_path || !data.enabled)) return false;
    var job = data.job;
    if (job !== null && (!object(job) || !/^[A-Za-z0-9_-]{16,100}$/.test(job.id || '') ||
        !activeStates.concat(['cancelled', 'ready', 'failed']).includes(job.state) ||
        !bounded(job.received_bytes, 10 * 1024 ** 3) || !bounded(job.total_bytes, 10 * 1024 ** 3) ||
        job.received_bytes > job.total_bytes || !(job.error === null || typeof job.error === 'string' && job.error.length <= 2000))) return false;
    return true;
  }
  async function request(path, body, signal) {
    var response = await window.fetch(base + path, {method:body ? 'POST' : 'GET', cache:'no-store',
      headers:{'Content-Type':'application/json', ...(context ? {'X-Lightwork-Voice-Context':context} : {})},
      ...(body ? {body:JSON.stringify(body)} : {}), ...(signal ? {signal:signal} : {})});
    if (response.status === 401 || response.status === 403) {
      clearPrivate('Voice setup access is no longer available. Sign in with an administrator account and reload this page.');
      throw new Error('Access changed');
    }
    if (response.status === 409) {
      clearPrivate('The account, workspace or voice setup changed. Please reload this page and review its current status.');
      throw new Error('Setup changed');
    }
    var data; try { data = await response.json(); } catch (_error) { throw new Error('Voice setup returned an unreadable response.'); }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail.slice(0, 2000) : 'Voice setup could not confirm this request.');
    if (!valid(data)) throw new Error('Voice setup information could not be verified.');
    if (context && context !== data.context) {
      clearPrivate('The account, workspace or voice configuration changed. Please reload this page before continuing.');
      throw new Error('Context changed');
    }
    return data;
  }
  function render(data) {
    state = data; context = data.context;
    var model = data.model, job = data.job;
    var names = {tiny:'Compact multilingual model', 'tiny.en':'Compact English model', base:'Standard multilingual model', 'base.en':'Standard English model',
      small:'Larger multilingual model', 'small.en':'Larger English model', medium:'Advanced multilingual model', 'medium.en':'Advanced English model',
      'large-v3':'Largest multilingual model', 'large-v3-turbo':'Fast large multilingual model'};
    text('model', model ? names[model.id] : 'Managed by your deployment');
    text('model-details', model ? 'About ' + (model.size_bytes / 1e6).toFixed(1) + ' MB. Download source: Hugging Face, whisper.cpp model catalog.' : 'This setup supports the configured model from Lightwork’s fixed catalog.');
    text('technical', model ? model.id + ' · ' + model.size_bytes.toLocaleString() + ' bytes · ' + model.source_url + ' · SHA-256: ' + model.sha256 + ' · Configured speech backend: ' + data.configuration.backend : '');
    text('policy', data.configuration.auto_fetch ? 'Your deployment allows automatic model downloads. This setup uses the same policy.' : 'Automatic model downloads are off. This setup downloads only after you confirm.');
    text('readiness', data.engine_available ? 'The local speech engine is installed. ' + (data.configuration.backend === 'local' ? 'Dictation is configured to run on this computer.' : 'Dictation follows your selected speech provider and may use a configured cloud service.') :
      'The speech engine is not available in this installation. Downloading the voice model alone won’t turn on dictation on this computer; your selected speech provider is used instead.');
    var messages = {checking:'Checking the existing model file and verifying its bytes…', downloading:'Downloading model…',
      cancelling:'Cancelling… Stopping the download and cleaning up.', cancelled:'Download cancelled. Review the model and confirm again to retry.',
      ready:'Model file verified during the last setup.' + (data.engine_available ? ' Try dictation using the microphone in Chat.' : ''), failed:'Setup failed. ' + (job && job.error || 'Review the connection and available disk space, then confirm again to retry.')};
    var status = !data.enabled || !data.can_download || data.configuration.managed_path || !model ? (data.reason || 'Voice model setup is unavailable for this deployment.') :
      job && job.state === 'ready' && data.model_state !== 'verified' ? 'The previously verified model file has changed or is missing. Confirm below to check it again.' :
      job ? messages[job.state] : data.model_state === 'verified' ? messages.ready :
        data.model_state === 'present_unverified' ? 'A model file is present but has not been verified by setup. Confirm below to check it and repair it if needed.' :
          'The configured model is not installed. Review the source and download size before confirming.';
    text('status', status);
    el('try').hidden = !(data.enabled && data.engine_available && data.model_state === 'verified' && ['auto', 'local'].includes(data.configuration.backend));
    el('progress').hidden = !active();
    if (active() && job.total_bytes > 0) { el('progress').max = job.total_bytes; el('progress').value = job.received_bytes; }
    else el('progress').removeAttribute('value');
    text('progress-text', active() ? (job.received_bytes / 1e6).toFixed(1) + ' MB of ' + (job.total_bytes / 1e6).toFixed(1) + ' MB (' + (job.total_bytes ? Math.floor(job.received_bytes * 100 / job.total_bytes) : 0) + '%). Lightwork checks the file before using it.' : '');
    controls(); schedule();
  }
  function schedule() {
    stopTimer(); if (!locked && active()) timer = window.setTimeout(function () { return load(); }, 1500);
  }
  async function load() {
    if (locked || reading || writing) return;
    reading = true; var expected = generation, controller = new AbortController(); readController = controller; controls();
    try { var data = await request('', null, controller.signal); if (!locked && expected === generation) render(data); }
    catch (error) { if (!locked && expected === generation) problem(error.message || 'Voice setup status is unavailable.'); }
    finally { if (readController === controller) { readController = null; reading = false; } controls(); }
  }
  async function mutate(path, body) {
    if (locked || writing) return;
    writing = true; generation += 1; stopTimer();
    if (readController) { readController.abort(); readController = null; reading = false; }
    el('confirm').checked = false; controls();
    try { var data = await request(path, body); if (!locked) render(data); }
    catch (error) { if (!locked) problem(error.message || 'Voice setup could not confirm the action.'); }
    finally { writing = false; controls(); }
  }
  el('confirm').addEventListener('change', controls);
  el('start').addEventListener('click', function () {
    if (locked || writing || !state || !state.enabled || !state.can_download || active() || !state.model || !el('confirm').checked) return;
    return mutate('/download', {confirm:true, model:state.model.id, sha256:state.model.sha256, size_bytes:state.model.size_bytes});
  });
  el('cancel').addEventListener('click', function () {
    if (locked || writing || !active() || state.job.state === 'cancelling') return;
    return mutate('/cancel', {job_id:state.job.id});
  });
  el('refresh').addEventListener('click', function () { el('confirm').checked = false; return load(); });
  window.addEventListener('pagehide', function () {
    if (readController) readController.abort();
    clearPrivate('Reload this page to verify voice setup access and current progress.');
  });
  load();
})();
