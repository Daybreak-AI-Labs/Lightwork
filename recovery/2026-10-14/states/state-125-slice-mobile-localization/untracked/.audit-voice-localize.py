from pathlib import Path
import re
root=Path('packages/maverick-dashboard/maverick_dashboard')
p=root/'static/lightwork-read-aloud.js';s=p.read_text(encoding='utf8')
s=s.replace('  var owner = null;', '''  var owner = null;
  function copy(node, source, values) { if (window.LightworkI18n) window.LightworkI18n.set(node, source, values); else { node.textContent = source.replace(/\\{(\\w+)\\}/g, function (_, key) { return String((values || {})[key] ?? ''); }); node.lang = 'en'; node.dir = 'ltr'; } }
  function label(source) { return window.LightworkI18n ? window.LightworkI18n.text(source) : source; }
  function number(value) { return window.LightworkI18n ? window.LightworkI18n.number(value) : String(value); }''')
s=s.replace('var parts = [], index = 0, browserOnly = false, utterance = null;',"var parts = [], index = 0, browserOnly = false, utterance = null, contentLanguage = 'und';")
s=re.sub(r"(pause|button|status)\.textContent = ('(?:[^'\\]|\\.)*');",r'copy(\1, \2);',s)
s=s.replace("options.append(skip, document.createTextNode(' Skip code and tables when reading'));", "var optionLabel = document.createElement('span'); copy(optionLabel, 'Skip code and tables when reading');\n    options.append(skip, ' ', optionLabel);")
s=s.replace("button.setAttribute('aria-label', 'Read the result aloud')", "button.setAttribute('aria-label', label('Read the result aloud'))")
s=s.replace("button.setAttribute('aria-label', 'Stop reading')", "button.setAttribute('aria-label', label('Stop reading'))")
s=s.replace('if (message) status.textContent = message;', 'if (message) copy(status, message);')
s=s.replace('window.lwVoice.humanize(text)', 'window.lwVoice.humanize(text, contentLanguage)').replace('window.lwVoice.best()', 'window.lwVoice.best(contentLanguage)')
s=s.replace("status.textContent = 'Reading part ' + (index + 1) + ' of ' + parts.length + (browserOnly ? ' with the device voice.' : '.');", "copy(status, browserOnly ? 'Reading part {current} of {total} with the device voice.' : 'Reading part {current} of {total}.', {current:number(index + 1), total:number(parts.length)});")
s=s.replace('var selection = source.cloneNode(true);', "// Result language is content metadata, never the interface or inherited html language.\n      contentLanguage = source.getAttribute('lang') || 'und';\n      var selection = source.cloneNode(true);")
s=s.replace("pause.textContent = paused ? 'Resume reading' : 'Pause reading';", "copy(pause, paused ? 'Resume reading' : 'Pause reading');")
s=s.replace("    if (button.getAttribute('data-voice-only')", "    copy(button, 'Read aloud'); button.setAttribute('aria-label', label('Read the result aloud'));\n    if (button.getAttribute('data-voice-only')")
p.write_text(s,encoding='utf8',newline='\n')

p=root/'static/lightwork-voice.js';s=p.read_text(encoding='utf8')
s=s.replace("return (v.lang || '').toLowerCase().indexOf('en') === 0 &&\n             !AVOID.test(v.name || '');", "return !AVOID.test(v.name || '');")
s=s.replace('function best() {', 'function best(contentLanguage) {')
s=s.replace('    return list[0];', '''    var language = String(contentLanguage || '').toLowerCase();
    if (/^[a-z]{2,3}(?:-[a-z0-9]+)*$/.test(language) && language !== 'und') {
      var exact = list.find(function (voice) { return (voice.lang || '').toLowerCase() === language; });
      if (exact) return exact;
      var base = language.split('-')[0];
      var matching = list.find(function (voice) { return (voice.lang || '').toLowerCase().split('-')[0] === base; });
      if (matching) return matching;
    }
    // Unknown source language must not be guessed from interface preferences.
    return list.find(function (voice) { return voice.default; }) || null;''')
s=s.replace('function humanize(text) {', 'function humanize(text, contentLanguage) {')
s=s.replace("    for (var i = 0; i < SAY.length;", "    if (contentLanguage && !/^en(?:-|$)/i.test(contentLanguage)) return out.trim();\n    for (var i = 0; i < SAY.length;")
p.write_text(s,encoding='utf8',newline='\n')

p=root/'static/lightwork-voice-setup.js';s=p.read_text(encoding='utf8')
s=s.replace("function text(name, value) { el(name).textContent = value; }", '''function text(name, value, values) { var node=el(name); delete node.dataset.userContent; if(window.LightworkI18n) window.LightworkI18n.set(node,value,values); else { node.textContent=value.replace(/\\{(\\w+)\\}/g,function(_,key){return String((values||{})[key]??'');}); node.lang='en'; node.dir='ltr'; } }
  function raw(name, value) { var node=el(name); node.textContent=value; node.lang='und'; node.dir='auto'; node.dataset.userContent=''; }
  function formatted(source) { return window.LightworkI18n?window.LightworkI18n.text(source):source; }
  function number(value, options) { return window.LightworkI18n?window.LightworkI18n.number(value,options):new Intl.NumberFormat('en',options).format(value); }''')
s=s.replace("text('status', message + ' Refresh setup status before trying again; an earlier download may still be running.');", "text('status', '{reason} Refresh setup status before trying again; an earlier download may still be running.', {reason:formatted(message)});")
lines=s.splitlines()
for i,line in enumerate(lines):
    if "text('model-details'" in line:
        lines[i]="    text('model-details', model ? 'About {size} MB. Download source: Hugging Face, whisper.cpp model catalog.' : \"This setup supports the configured model from Lightwork’s fixed catalog.\", {size:model?number(model.size_bytes / 1e6,{minimumFractionDigits:1,maximumFractionDigits:1}):''});"
    elif "text('technical'" in line:
        lines[i]="    text('technical', model ? '{model} · {bytes} bytes · {source} · SHA-256: {hash} · Configured speech backend: {backend}' : '', model?{model:model.id,bytes:number(model.size_bytes),source:model.source_url,hash:model.sha256,backend:data.configuration.backend}:{});"
    elif "text('readiness'" in line:
        lines[i]="    text('readiness', data.engine_available ? (data.configuration.backend === 'local' ? 'The local speech engine is installed. Dictation is configured to run on this computer.' : 'The local speech engine is installed. Dictation follows your selected speech provider and may use a configured cloud service.') :"
    elif "ready:'Model file verified" in line:
        lines[i]="      ready:data.engine_available ? 'Model file verified during the last setup. Try dictation using the microphone in Chat.' : 'Model file verified during the last setup.', failed:'Setup failed. Review the connection and available disk space, then confirm again to retry.'};"
    elif "var status = !data.enabled" in line:
        lines[i]="    var unavailable = !data.enabled || !data.can_download || data.configuration.managed_path || !model;\n    var status = unavailable ? 'Voice model setup is unavailable for this deployment.' :"
    elif "text('status', status);" in line:
        lines[i]="    if(unavailable && data.reason) raw('status',data.reason);\n    else if(job && job.state==='failed' && job.error) text('status','Setup failed. {reason}',{reason:job.error});\n    else text('status', status);"
    elif "text('progress-text', active()" in line:
        lines[i]="    text('progress-text', active() ? '{received} MB of {total} MB ({percent}). Lightwork checks the file before using it.' : '',active()?{received:number(job.received_bytes / 1e6,{minimumFractionDigits:1,maximumFractionDigits:1}),total:number(job.total_bytes / 1e6,{minimumFractionDigits:1,maximumFractionDigits:1}),percent:number(job.total_bytes?job.received_bytes/job.total_bytes:0,{style:'percent',maximumFractionDigits:0,roundingMode:'floor'})}:{});"
s='\n'.join(lines)+'\n';p.write_text(s,encoding='utf8',newline='\n')

# The voice section is owned separately from the remainder of the settings form.
p=root/'templates/settings.html';s=p.read_text(encoding='utf8');a=s.index('  <section class="panel voice-setup"');b=s.index('  </section>',a)+len('  </section>');part=s[a:b]
part=re.sub(r'>([^<>]+)<',lambda m:'>{{ ui('+repr(m[1].strip()).replace('\\\'','\'')+') }}<' if re.search('[A-Za-z]',m[1]) and '{{' not in m[1] else m[0],part)
part=part.replace('aria-label="Voice model download progress"', 'aria-label="{{ ut(\"Voice model download progress\") }}"')
# Consent text follows an input tag, so the same explicit copy wrapper preserves the checkbox.
s=s[:a]+part+s[b:];p.write_text(s,encoding='utf8',newline='\n')
