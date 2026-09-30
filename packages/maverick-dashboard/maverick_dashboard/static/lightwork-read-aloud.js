/* A single cancellable playback owner across server and browser voices. */
(function () {
  'use strict';
  window.lwReadAloud = function (button) {
    if (!button || button.dataset.readAloudBound) return;
    button.dataset.readAloudBound = "true";
    var generation = 0, active = false, paused = false, player = null, url = null, request = null;
    var parts = [], index = 0, browserOnly = false, utterance = null;
    var status = document.createElement('span');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    var pause = document.createElement('button'); pause.type = 'button'; pause.className = 'btn btn--ghost'; pause.hidden = true;
    pause.textContent = 'Pause reading';
    var options = document.createElement('label'), skip = document.createElement('input');
    skip.type = 'checkbox'; skip.checked = true;
    options.append(skip, document.createTextNode(' Skip code and tables when reading'));
    button.after(pause, options, status);
    function release() {
      if (player) { player.pause(); player.onended = null; player.onerror = null; player = null; }
      if (url) { URL.revokeObjectURL(url); url = null; }
    }
    function stop(message) {
      generation += 1; active = false; paused = false;
      if (request) request.abort(); request = null;
      release();
      if (window.lwVoice) window.lwVoice.stop();
      else if (window.speechSynthesis) window.speechSynthesis.cancel();
      utterance = null; pause.hidden = true;
      button.setAttribute('aria-pressed', 'false'); button.setAttribute('aria-busy', 'false');
      button.setAttribute('aria-label', 'Read the result aloud'); button.textContent = 'Read aloud';
      if (message) status.textContent = message;
    }
    function next(token) {
      if (!active || token !== generation) return;
      index += 1; release();
      if (index >= parts.length) { stop('Reading finished.'); return; }
      play(token);
    }
    function browser(text, token) {
      if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) { stop('Audio is unavailable. Try reading again when a voice is available.'); return; }
      var clean = window.lwVoice ? window.lwVoice.humanize(text) : text;
      utterance = new SpeechSynthesisUtterance(clean);
      var voice = window.lwVoice && window.lwVoice.best();
      if (voice) { utterance.voice = voice; utterance.lang = voice.lang; }
      utterance.onend = function () { next(token); };
      utterance.onerror = function (event) { if (token === generation && event.error !== 'canceled') stop('Playback stopped. Select Read aloud to try again.'); };
      window.speechSynthesis.cancel(); window.speechSynthesis.speak(utterance);
      if (paused) window.speechSynthesis.pause();
    }
    async function play(token) {
      if (!active || token !== generation) return;
      status.textContent = 'Reading part ' + (index + 1) + ' of ' + parts.length + (browserOnly ? ' with the device voice.' : '.');
      if (browserOnly) { browser(parts[index], token); return; }
      request = new AbortController();
      var controller = request, timer = setTimeout(function () { controller.abort(); }, 20000);
      button.setAttribute('aria-busy', 'true');
      try {
        var response = await fetch('/api/v1/voice/speak', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: parts[index]}), signal: controller.signal});
        if (!response.ok) throw new Error('Voice unavailable');
        var blob = await response.blob();
        if (!active || token !== generation) return;
        url = URL.createObjectURL(blob); player = new Audio(url);
        player.onended = function () { next(token); };
        player.onerror = function () { if (token === generation) stop('Audio could not play. Select Read aloud to retry.'); };
        if (!paused) {
          try { await player.play(); }
          catch (_) { if (token === generation) stop('Your browser blocked audio. Select Read aloud to try again.'); }
        }
      } catch (_) {
        if (!active || token !== generation) return;
        browserOnly = true;
        status.textContent = 'The server voice is unavailable. Reading the complete selection with the device voice.';
        browser(parts[index], token);
      } finally {
        clearTimeout(timer);
        if (request === controller) request = null;
        if (token === generation) button.setAttribute('aria-busy', 'false');
      }
    }
    button.addEventListener('click', function () {
      if (active) { stop('Reading stopped.'); return; }
      var source = document.getElementById('result') || document.getElementById('deliverable') || document.getElementById('result-section');
      if (!source) { status.textContent = 'No result is available to read yet.'; return; }
      var selection = source.cloneNode(true);
      if (skip.checked) selection.querySelectorAll('pre, code, table').forEach(function (node) { node.remove(); });
      var text = (selection.textContent || '').trim();
      if (!text) { status.textContent = 'No readable text remains with these options.'; return; }
      // Complete content, bounded per request. Never silently slice the result.
      parts = text.match(/[\s\S]{1,3000}/gu) || []; index = 0; browserOnly = false; active = true;
      button.setAttribute('aria-pressed', 'true'); button.setAttribute('aria-label', 'Stop reading'); button.textContent = 'Stop reading';
      pause.hidden = false; pause.textContent = 'Pause reading'; play(++generation);
    });
    pause.addEventListener('click', function () {
      if (!active) return;
      paused = !paused; pause.textContent = paused ? 'Resume reading' : 'Pause reading';
      if (player) { if (paused) player.pause(); else player.play().catch(function () { stop('Playback blocked. Select Read aloud to retry.'); }); }
      if (window.speechSynthesis) { if (paused) window.speechSynthesis.pause(); else window.speechSynthesis.resume(); }
    });
    document.addEventListener('lightwork:answer-ready', function () { stop(); }, {once: true});
    window.addEventListener('pagehide', function () { stop(); });
    if (button.getAttribute('data-voice-only') === '1') button.click();
  };
})();
