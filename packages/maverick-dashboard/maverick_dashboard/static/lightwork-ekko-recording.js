// Ekko page: "Record activity". Sends an activity file (or, on Windows, starts
// the app-in-front recorder) for the device chosen above, then shows progress.
// Enrollment, limits and the recorder lease are all checked on the server.
(() => {
  'use strict';
  const root = document.getElementById('ekko-recording');
  if (!root) return;
  const $ = id => document.getElementById('ekko-rec-' + id);
  const MAX = 1000000;
  let busy = false;
  function say(text) { window.mvStatus($('msg'), text); }
  function failText(failure, what) {
    return typeof window.mvErrorText === 'function' ? window.mvErrorText(failure, what) : 'Could not ' + what + '. Please try again.';
  }
  function device() {
    const pick = window.LightworkEkkoDevice;
    const id = typeof pick === 'function' ? pick() : 'local-device';
    return /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/.test(id) ? id : 'local-device';
  }
  function lock(state) { busy = state; $('import').disabled = state; $('watch').disabled = state; root.setAttribute('aria-busy', String(state)); }
  function readText(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(file);
    });
  }
  async function follow(job, live) {
    for (let tries = 0; tries < 43200; tries += 1) {
      await new Promise(resolve => setTimeout(resolve, tries ? 2000 : 500));
      let data;
      try { data = await window.mvFetchJSON('/api/v1/ekko/recording/' + job, { cache: 'no-store' }, 'check the recording'); }
      catch (error) { say(error.message); return; }
      if (data && data.state === 'running') { if (live) say('Recording. Use Pause or Stop above to end it.'); continue; }
      const result = (data && data.result) || {};
      const recorded = Number(result.recorded) || 0, skipped = Number(result.skipped) || 0;
      say((result.message ? result.message + ' ' : '') + 'Recorded ' + recorded + (recorded === 1 ? ' activity' : ' activities')
        + (skipped ? ' and skipped ' + skipped + ' that your enrollment doesn’t allow.' : '.'));
      return;
    }
  }
  async function start(path, body, live) {
    let data;
    try { data = await window.mvFetchJSON(path, { method: 'POST', cache: 'no-store', body: body }, 'start recording'); }
    catch (error) { say(error.message); return; }
    if (!data || !/^[a-f0-9]{32}$/.test(String(data.job))) { say('The recording couldn’t be checked.'); return; }
    await follow(data.job, live);
  }
  $('import').addEventListener('click', async () => {
    if (busy) return;
    if (!$('consent').checked) { say('Tick the box above to confirm what Ekko records.'); return; }
    const file = $('file').files && $('file').files[0];
    if (!file) { say('Choose an activity file first.'); return; }
    if (file.size > MAX) { say('That file is too large. Choose a file under 1 MB.'); return; }
    lock(true); say('Recording from the file…');
    try {
      const text = await readText(file);
      await start('/api/v1/ekko/recording/import', { device_id: device(), events: text.slice(0, MAX), acknowledge_capture_scope: true }, false);
    } catch (error) { say(failText(error, 'record from the file')); }
    finally { lock(false); }
  });
  $('watch').addEventListener('click', async () => {
    if (busy) return;
    if (!$('consent').checked) { say('Tick the box above to confirm what Ekko records.'); return; }
    lock(true); say('Starting the recorder…');
    try { await start('/api/v1/ekko/recording/watch', { device_id: device(), acknowledge_capture_scope: true }, true); }
    catch (error) { say(failText(error, 'start recording')); }
    finally { lock(false); }
  });
  window.mvFetchJSON('/api/v1/ekko/recording/options', { cache: 'no-store' })
    .then(data => { if (data && data.windows_active_app === true) $('watch').hidden = false; })
    .catch(() => {});
})();
