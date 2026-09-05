/* Progressive forms for the existing governed action consoles. The original
 * controllers remain the sole owners of requests, confirmations and results. */
(function (root) {
  'use strict';
  function label(key) {
    var names = {idempotency_key:'Request reference', expected_revision:'Current version', expected_version:'Current version', expected_current_version:'Current version', title_key:'Title text reference', description_key:'Description text reference', body_key:'Body text reference', display_name_key:'Display name text reference', heading_key:'Heading text reference'};
    if (Object.prototype.hasOwnProperty.call(names, key)) return names[key];
    return String(key).replace(/_/g, ' ').replace(/\b(id|ref)\b/g, 'reference').replace(/^./, function (s) { return s.toUpperCase(); });
  }
  function allowsRequestReference(schema) {
    if (schema.type !== 'string' || schema.enum || Object.prototype.hasOwnProperty.call(schema, 'const')) return false;
    if ((schema.minLength || 0) > 36 || (schema.maxLength !== undefined && schema.maxLength < 36)) return false;
    if (schema.format && schema.format !== 'uuid') return false;
    try { return !schema.pattern || new RegExp(schema.pattern).test('00000000-0000-4000-8000-000000000000'); }
    catch (_error) { return false; }
  }
  function requestReference() {
    if (root.crypto && typeof root.crypto.randomUUID === 'function') return root.crypto.randomUUID();
    if (!root.crypto || typeof root.crypto.getRandomValues !== 'function') throw new Error('Secure reference generation is unavailable. Enter a request reference to continue.');
    var bytes = root.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    var hex = Array.from(bytes, function (value) { return value.toString(16).padStart(2, '0'); }).join('');
    return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
  }
  function resolve(schema, definitions, seen) {
    schema = schema || {}; seen = seen || [];
    if (schema.$ref) {
      if (seen.includes(schema.$ref)) return {};
      var target = schema.$ref.split('/').pop();
      return Object.assign({}, resolve(definitions[target], definitions, seen.concat(schema.$ref)), schema, {$ref:undefined});
    }
    if (schema.allOf) {
      var combined = {properties:{}, required:[]};
      schema.allOf.forEach(function (part) {
        var item = resolve(part, definitions, seen);
        combined = Object.assign(combined, item, {properties:Object.assign({}, combined.properties, item.properties), required:combined.required.concat(item.required || [])});
      });
      return Object.assign(combined, schema, {allOf:undefined});
    }
    return schema;
  }
  function pathKeys(path) { return Array.from(new Set(Array.from(String(path).matchAll(/\{([A-Za-z0-9_]+)\}/g), function (match) { return match[1]; }))); }
  if (typeof module !== 'undefined' && module.exports) module.exports = {label:label, resolve:resolve, pathKeys:pathKeys};
  if (!root.document) return;
  var doc = root.document;
  var sequence = 0;
  var schemaRequest;
  function loadDefinitions() {
    if (!schemaRequest) schemaRequest = fetch('/openapi.json', {cache:'no-store'}).then(function (response) { if (!response.ok) throw new Error('Definitions unavailable'); return response.json(); });
    return schemaRequest;
  }
  function element(tag, text, className) {
    var node = doc.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node;
  }
  function button(text, handler) { var node = element('button', text, 'btn btn--ghost'); node.type = 'button'; node.addEventListener('click', handler); return node; }
  function rawDetails(input, title) {
    var container = input.closest('label') || input;
    var disclosure = element('details', undefined, 'guided-technical');
    disclosure.append(element('summary', title)); container.before(disclosure); disclosure.append(container); return disclosure;
  }
  function json(input) { try { return JSON.parse(input.value || '{}'); } catch (_error) { return undefined; } }
  function defaultValue(schema) {
    if (schema.type === 'array') return [];
    if (schema.type === 'object' || schema.properties) return {};
    return undefined;
  }
  function matchesChoice(schema, value, definitions) {
    schema = resolve(schema, definitions);
    if (value === undefined) return false;
    if (Object.prototype.hasOwnProperty.call(schema, 'const') && schema.const !== value) return false;
    if (schema.enum && !schema.enum.includes(value)) return false;
    if (schema.type === 'null') return value === null;
    if (schema.type === 'array') return Array.isArray(value);
    if (schema.type === 'integer') return Number.isInteger(value);
    if (schema.type === 'object' || schema.properties) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
      if (!(schema.required || []).every(function (key) { return Object.prototype.hasOwnProperty.call(value, key); })) return false;
      return Object.keys(schema.properties || {}).every(function (key) {
        var child = resolve(schema.properties[key], definitions);
        return !Object.prototype.hasOwnProperty.call(value, key) ||
          ((!Object.prototype.hasOwnProperty.call(child, 'const') || child.const === value[key]) &&
           (!child.enum || child.enum.includes(value[key])));
      });
    }
    return !schema.type || typeof value === schema.type;
  }
  function editor(host, schema, value, definitions, changed, depth, required) {
    schema = resolve(schema, definitions);
    if (schema.readOnly) {
      host.append(element('p', 'This value is supplied by the action.'));
      return function () { return value; };
    }
    if (depth > 10) {
      host.append(element('p', 'This nested detail needs the technical editor below.'));
      return function () { return value; };
    }
    var variants = schema.anyOf || schema.oneOf;
    if (variants) {
      var choices = variants.map(function (v) { return resolve(v, definitions); });
      if (choices.length === 1) return editor(host, choices[0], value, definitions, changed, depth + 1, required);
      var choice = element('select', undefined, 'input'); choice.setAttribute('aria-label', 'Detail format');
      choices.forEach(function (v, index) { var option = element('option', v.title || label(v.type || ('Option ' + (index + 1)))); option.value = String(index); choice.append(option); });
      var selected = choices.findIndex(function (v) { return matchesChoice(v, value, definitions); });
      choice.value = String(Math.max(0, selected));
      var child = element('div'); host.append(choice, child);
      var reader;
      function pick(initial) { child.replaceChildren(); reader = editor(child, choices[Number(choice.value)], initial, definitions, changed, depth + 1, required); }
      pick(value); choice.addEventListener('change', function () { pick(undefined); changed(); });
      return function () { return reader(); };
    }
    var type = schema.type || (schema.properties ? 'object' : (Array.isArray(value) ? 'array' : (value === null ? 'null' : typeof value)));
    if (value !== undefined && ((type === 'object' && (!value || typeof value !== 'object' || Array.isArray(value))) ||
        (type === 'array' && !Array.isArray(value)))) {
      host.append(element('p', 'This existing detail uses a different format. Edit it in Technical action details below.'));
      return function () { return value; };
    }
    if (type === 'object') {
      if (schema.propertyNames && Array.isArray(schema.propertyNames.enum)) {
        var namedProperties = Object.assign({}, schema.properties || {});
        schema.propertyNames.enum.forEach(function (key) { namedProperties[key] = namedProperties[key] || schema.additionalProperties || {}; });
        schema = Object.assign({}, schema, {properties:namedProperties, additionalProperties:false});
      }
      var readers = Object.create(null); var properties = schema.properties || {};
      var source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
      var keys = Array.from(new Set(Object.keys(properties).concat(Object.keys(source))));
      var list = element('div', undefined, 'guided-field-list'); host.append(list);
      function add(key, childSchema, initial, forceInclude) {
        var group = element('fieldset', undefined, 'guided-field'); var requiredKey = (schema.required || []).includes(key);
        group.dataset.guidedField = key;
        group.append(element('legend', label(key) + (requiredKey ? ' (required)' : '')));
        if (childSchema.description) group.append(element('p', childSchema.description, 'muted'));
        list.append(group);
        var content = element('fieldset', undefined, 'guided-value-group'); group.append(content);
        var reader = editor(content, childSchema, initial, definitions, changed, depth + 1, requiredKey);
        var include;
        if (!requiredKey) {
          include = element('input'); include.type = 'checkbox'; include.checked = initial !== undefined || !!forceInclude;
          var includeLabel = element('label'); includeLabel.append(include, doc.createTextNode(' Include this detail')); content.before(includeLabel);
          function toggle() { content.hidden = !include.checked; content.disabled = !include.checked; }
          include.addEventListener('change', function () { toggle(); changed(); }); toggle();
        }
        readers[key] = function () { return requiredKey || include.checked ? reader() : undefined; };
        if (!requiredKey) group.append(button('Remove ' + label(key).toLowerCase(), function () { delete readers[key]; group.remove(); options(); changed(); }));
      }
      keys.forEach(function (key) { add(key, resolve(properties[key] || (typeof schema.additionalProperties === 'object' ? schema.additionalProperties : {}), definitions), source[key]); });
      var missingKeys = element('select', undefined, 'input'); missingKeys.setAttribute('aria-label', 'Detail to add');
      var knownControls = element('div', undefined, 'guided-known-add');
      function options() {
        missingKeys.replaceChildren();
        var empty = element('option', 'Choose a detail'); empty.value = ''; missingKeys.append(empty);
        var available = Object.keys(properties).filter(function (key) { return !Object.prototype.hasOwnProperty.call(readers, key); });
        available.forEach(function (key) { var item = element('option', label(key)); item.value = key; missingKeys.append(item); });
        knownControls.hidden = !available.length;
      }
      if (Object.keys(properties).length) {
        options(); missingKeys.addEventListener('focus', options); knownControls.append(missingKeys, button('Add detail', function () { var key = missingKeys.value; if (key && !Object.prototype.hasOwnProperty.call(readers, key)) { add(key, resolve(properties[key], definitions), undefined, true); options(); changed(); } })); host.append(knownControls);
      }
      if (schema.additionalProperties !== false) {
        var custom = element('details', undefined, 'guided-add'); custom.append(element('summary', 'Add another detail'));
        var keyInput = element('input', undefined, 'input'); keyInput.placeholder = 'Detail name'; keyInput.setAttribute('aria-label', 'New detail name');
        var typeInput = element('select', undefined, 'input'); typeInput.setAttribute('aria-label', 'New detail format');
        [['string','Text'],['number','Number'],['boolean','Yes or no'],['array','List'],['object','Group']].forEach(function (pair) { var item = element('option', pair[1]); item.value = pair[0]; typeInput.append(item); });
        // Typed maps already define their value format, so there is no format
        // choice to make (for example, translation messages are always text).
        typeInput.hidden = typeof schema.additionalProperties === 'object';
        var notice = element('p'); notice.setAttribute('role', 'status');
        custom.append(keyInput, typeInput, button('Add named detail', function () {
          var key = keyInput.value.trim();
          if (!key || key.length > 200 || Object.prototype.hasOwnProperty.call(readers, key)) { notice.textContent = 'Use a unique detail name of up to 200 characters.'; return; }
          var childSchema = typeof schema.additionalProperties === 'object' ? schema.additionalProperties : {type:typeInput.value};
          add(key, childSchema, defaultValue(childSchema), true); keyInput.value = ''; notice.textContent = ''; changed();
        }), notice); host.append(custom);
      }
      return function () { var result = Object.create(null); Object.keys(readers).forEach(function (key) { var item = readers[key](); if (item !== undefined) result[key] = item; }); return result; };
    }
    if (type === 'array') {
      var rows = []; var items = element('div'); host.append(items);
      function addItem(initial) {
        var row = element('fieldset', undefined, 'guided-field'); row.append(element('legend', 'List item'));
        var itemSchema = resolve(schema.items || {}, definitions);
        var record = {row:row, read:editor(row, itemSchema, initial, definitions, changed, depth + 1, true)};
        rows.push(record); items.append(row);
        row.append(button('Remove item', function () { rows = rows.filter(function (item) { return item !== record; }); row.remove(); changed(); }));
      }
      (Array.isArray(value) ? value : []).forEach(addItem);
      host.append(button('Add item', function () { addItem(defaultValue(resolve(schema.items || {}, definitions))); changed(); }));
      return function () { return rows.map(function (item) { return item.read(); }).filter(function (item) { return item !== undefined; }); };
    }
    if (type === 'null') { host.append(element('p', 'No value')); return function () { return null; }; }
    var input;
    if (schema.enum || type === 'boolean') {
      input = element('select', undefined, 'input');
      var empty = element('option', 'Choose…'); empty.value = ''; input.append(empty);
      var values = schema.enum || [true, false];
      values.forEach(function (item, index) { var option = element('option', typeof item === 'boolean' ? (item ? 'Yes' : 'No') : label(item)); option.value = String(index); input.append(option); });
      if (values.includes(value)) input.value = String(values.indexOf(value));
    } else {
      input = element(type === 'string' && (schema.maxLength || 0) > 400 ? 'textarea' : 'input', undefined, 'input');
      if (type === 'number' || type === 'integer') {
        input.type = 'number'; input.step = type === 'integer' ? '1' : 'any';
        if (schema.minimum !== undefined) input.min = String(schema.minimum);
        if (schema.maximum !== undefined) input.max = String(schema.maximum);
      }
      if (schema.maxLength) input.maxLength = schema.maxLength;
      if (schema.minLength) input.minLength = schema.minLength;
      if (value !== undefined && value !== null) input.value = String(value);
      input.autocomplete = 'off';
    }
    input.id = 'guided-value-' + (++sequence);
    var field = host.closest('[data-guided-field]');
    var fieldKey = field ? field.dataset.guidedField : '';
    var inputLabel = element('label', fieldKey ? label(fieldKey) : (schema.title || 'List item'));
    inputLabel.htmlFor = input.id; host.append(inputLabel, input);
    if (fieldKey === 'idempotency_key' && allowsRequestReference(schema)) {
      var generationStatus = element('p', '', 'muted'); generationStatus.setAttribute('role', 'status');
      host.append(button('Generate reference', function () {
        try {
          var generated = requestReference();
          if (schema.pattern && !new RegExp(schema.pattern).test(generated)) throw new Error('This field requires a custom request reference. Enter one that follows its required format.');
          input.value = generated; edit(); input.focus();
          generationStatus.textContent = 'New request reference generated.';
        } catch (error) { generationStatus.textContent = error.message; }
      }), element('p', 'Keep this reference for an exact retry. Generate a new one when changing the request.', 'muted'), generationStatus);
    }
    input.required = !!required && type !== 'string';
    var dirty = false;
    function read() {
      // Merely rendering a form must not normalize existing API values.
      if (!dirty) return value;
      if (input.value === '') return type === 'string' && !schema.enum ? '' : undefined;
      if (schema.enum || type === 'boolean') return (schema.enum || [true,false])[Number(input.value)];
      if (type === 'integer' || type === 'number') { var number = Number(input.value); return Number.isFinite(number) ? number : undefined; }
      return input.value;
    }
    function validate() {
      var current = read();
      var message = required && current === undefined ? 'Enter a value for this required detail.' : '';
      if (!message && typeof current === 'string' && schema.minLength && current.length < schema.minLength) message = 'Enter at least ' + schema.minLength + ' characters.';
      input.setCustomValidity(message);
    }
    function edit() { dirty = true; validate(); changed(); }
    input.addEventListener('input', edit); input.addEventListener('change', edit);
    validate();
    return read;
  }
  function readable(host, value, depth, budget) {
    if (--budget.remaining < 0 || depth > 8) { host.append(element('p', 'More detail is available in the technical response.')); return; }
    if (value && typeof value === 'object') {
      var list = element(Array.isArray(value) ? 'ol' : 'dl', undefined, 'guided-result-list'); host.append(list);
      var keys = Object.keys(value);
      keys.slice(0, 200).forEach(function (key) {
        if (budget.remaining < 0) return;
        var child;
        if (Array.isArray(value)) { child = element('li'); list.append(child); }
        else { list.append(element('dt', label(key))); child = element('dd'); list.append(child); }
        readable(child, value[key], depth + 1, budget);
      });
      if (keys.length > 200) host.append(element('p', 'Showing the first 200 entries. The complete result is available in Technical response.'));
      if (!Object.keys(value).length) host.append(element('p', Array.isArray(value) ? 'No records returned.' : 'No additional details returned.'));
    } else host.append(element('span', value === null ? 'Not reported' : String(value)));
  }
  function attach(select) {
    var prefix = select.id.replace(/-operation$/, '');
    var paths = doc.getElementById(prefix + '-path-values') || doc.getElementById(prefix + '-paths') || doc.getElementById(prefix + '-path');
    var body = doc.getElementById(prefix + '-body');
    if (!paths || !body) return;
    var submit = doc.getElementById(prefix + '-submit');
    var output = doc.getElementById(prefix + '-output');
    var pathArea = element('div', undefined, 'guided-fields');
    var bodyArea = element('div', undefined, 'guided-fields');
    var pathRaw = rawDetails(paths, 'Technical record references'); pathRaw.before(pathArea);
    var bodyRaw = rawDetails(body, 'Technical action details'); bodyRaw.before(bodyArea);
    var definitions = {}; var apiSchema; var readBody; var currentBody; var revision = 0;
    var error = element('p'); error.setAttribute('role', 'alert'); bodyArea.before(error);
    function syncBody() {
      if (!readBody) return;
      body.value = JSON.stringify(readBody()); currentBody = body.value;
      body.dispatchEvent(new Event('input', {bubbles:true}));
    }
    function renderBody() {
      var selected = select.options[select.selectedIndex];
      var method = (selected.dataset.method || 'GET').toLowerCase();
      var route = apiSchema && apiSchema.paths[select.dataset.guidedApi + selected.dataset.path];
      var request = route && route[method] && route[method].requestBody;
      var schema = request && request.content && request.content['application/json'] && request.content['application/json'].schema;
      var supplied = (selected.dataset.guidedProvidedFields || '').split(/\s+/).filter(Boolean);
      if (schema && supplied.length) {
        schema = resolve(schema, definitions);
        var properties = Object.assign({}, schema.properties || {});
        supplied.forEach(function (key) {
          if (Object.prototype.hasOwnProperty.call(properties, key)) properties[key] = Object.assign({}, resolve(properties[key], definitions), {readOnly:true});
        });
        schema = Object.assign({}, schema, {properties:properties});
      }
      var value = json(body);
      bodyArea.replaceChildren(); readBody = undefined;
      bodyArea.hidden = method === 'get'; bodyRaw.hidden = method === 'get';
      error.textContent = '';
      if (method === 'get') return;
      if (value === undefined) { bodyRaw.open = true; error.textContent = 'The existing action details could not be read. Correct them in Technical action details to continue.'; return; }
      error.textContent = ''; bodyArea.append(element('h3', 'Action details'));
      readBody = editor(bodyArea, schema || {type:'object'}, value, definitions, syncBody, 0, true);
      currentBody = body.value;
    }
    function renderPaths() {
      var selected = select.options[select.selectedIndex]; var existing = json(paths) || {};
      pathArea.replaceChildren(); var inputs = Object.create(null);
      pathKeys(selected.dataset.path).forEach(function (key) {
        var input = element('input', undefined, 'input'); input.id = 'guided-path-' + (++sequence); input.required = true; input.value = typeof existing[key] === 'string' || typeof existing[key] === 'number' ? String(existing[key]) : ''; input.autocomplete = 'off';
        var caption = element('label', label(key)); caption.htmlFor = input.id; pathArea.append(caption, input); inputs[key] = input;
        input.addEventListener('input', function () { var values = Object.create(null); Object.keys(inputs).forEach(function (name) { values[name] = inputs[name].value; }); paths.value = JSON.stringify(values); paths.dispatchEvent(new Event('input', {bubbles:true})); });
      });
      pathArea.hidden = !Object.keys(inputs).length; pathRaw.hidden = !Object.keys(inputs).length;
    }
    function change() { revision++; renderPaths(); renderBody(); }
    select.addEventListener('change', change);
    paths.addEventListener('change', renderPaths); body.addEventListener('change', renderBody);
    if (submit) submit.addEventListener('click', function (event) {
      if (bodyRaw.open) renderBody();
      var invalid = Array.from(pathArea.querySelectorAll('input')).concat(bodyArea.hidden ? [] : Array.from(bodyArea.querySelectorAll('input,select,textarea'))).find(function (input) { return !input.checkValidity(); });
      if (invalid || error.textContent) { event.preventDefault(); event.stopImmediatePropagation(); if (invalid) invalid.reportValidity(); return; }
      // Edits already sync immediately. Submitting must not rewrite a reviewed
      // payload or emit a fresh input event that resets its confirmation.
    }, true);
    if (output) {
      var result = element('div', undefined, 'guided-result'); result.setAttribute('aria-live', 'polite');
      var technical = element('details', undefined, 'guided-technical'); technical.append(element('summary', 'Technical response'));
      output.before(result, technical); technical.append(output); output.removeAttribute('aria-live');
      function show() {
        result.replaceChildren(); var value;
        try { value = JSON.parse(output.textContent); } catch (_error) { value = output.textContent; }
        readable(result, value, 0, {remaining:1000});
        if (body.value !== currentBody) renderBody();
      }
      new MutationObserver(show).observe(output, {childList:true, characterData:true, subtree:true}); show();
    }
    change();
    if (select.dataset.guidedApi) {
      var initialRevision = revision; var initialBody = body.value;
      loadDefinitions().then(function (schema) {
        apiSchema = schema; definitions = (schema.components || {}).schemas || {};
        if (revision === initialRevision && body.value === initialBody && !bodyArea.contains(doc.activeElement)) renderBody();
      }).catch(function () { error.textContent = 'Guided field definitions are unavailable. Retry by reloading this page; your existing action details remain available below.'; bodyRaw.open = true; });
    }
  }
  function attachJsonField(input) {
    var form = input.form || input.closest('form');
    if (!form) return;
    var selector = input.dataset.guidedSelector && doc.getElementById(input.dataset.guidedSelector);
    var drafts = Object.create(null); var selectedKey = selector ? selector.value : '';
    var container = input.closest('label') || input;
    var wide = ['wide','ce-wide'].filter(function (name) { return container.classList.contains(name); });
    var title = input.dataset.guidedTitle || label(input.name);
    var area = element('div', undefined, 'guided-fields guided-standalone');
    var raw = rawDetails(input, 'Technical details: ' + title.toLowerCase());
    wide.forEach(function (name) { area.classList.add(name); raw.classList.add(name); });
    raw.before(area);
    var error = element('p'); error.setAttribute('role', 'alert'); area.before(error);
    var notice = element('p', '', 'muted'); notice.setAttribute('role', 'status'); raw.before(notice);
    var definitions = {}; var documentSchema; var schema; var renderedSchema; var read; var current = input.value;
    function selectSchema() {
      var option = selector && selector.options[selector.selectedIndex];
      var endpoint = option ? option.dataset.guidedEndpoint : input.dataset.guidedEndpoint;
      var method = option && option.dataset.guidedMethod || input.dataset.guidedMethod || 'post';
      var route = documentSchema && documentSchema.paths && documentSchema.paths[endpoint];
      var operation = route && route[method.toLowerCase()];
      var content = operation && operation.requestBody && operation.requestBody.content;
      var request = content && content['application/json'];
      schema = request && resolve(request.schema, definitions);
      if (input.dataset.guidedProperty) schema = schema && schema.properties && schema.properties[input.dataset.guidedProperty];
    }
    function changed() {
      if (!read) return;
      input.value = JSON.stringify(read()); current = input.value;
      input.dispatchEvent(new Event('input', {bubbles:true}));
    }
    function render() {
      var value = !input.required && !input.value.trim() ? {} : json(input);
      area.replaceChildren(); read = undefined; error.textContent = '';
      if (value === undefined) { raw.open = true; error.textContent = title + ' must be valid JSON, or use the labeled fields after correcting it.'; return; }
      area.append(element('h3', title));
      read = editor(area, schema || {}, value, definitions, changed, 0, input.required);
      current = input.value; renderedSchema = schema;
    }
    if (selector) selector.addEventListener('change', function () {
      drafts[selectedKey] = {value:input.value, open:raw.open};
      selectedKey = selector.value;
      var draft = drafts[selectedKey];
      input.value = draft ? draft.value : input.defaultValue;
      raw.open = draft ? draft.open : false;
      selectSchema(); render();
      input.dispatchEvent(new Event('input', {bubbles:true}));
      notice.textContent = draft ? 'Your draft for this resource type has been restored.' : 'Complete the fields for this resource type. Drafts are kept separately while this page is open.';
    });
    input.addEventListener('change', render);
    form.addEventListener('submit', function (event) {
      if (raw.open || input.value !== current || renderedSchema !== schema) render();
      var invalid = Array.from(area.querySelectorAll('input,select,textarea')).find(function (control) { return !control.checkValidity(); });
      if (invalid || error.textContent) {
        event.preventDefault(); event.stopImmediatePropagation();
        if (invalid) invalid.reportValidity();
      }
    }, true);
    form.addEventListener('reset', function () { root.setTimeout(function () { drafts = Object.create(null); selectedKey = selector ? selector.value : ''; selectSchema(); render(); }, 0); });
    render();
    var initialValue = input.value;
    loadDefinitions().then(function (document) {
      documentSchema = document;
      definitions = (document.components || {}).schemas || {};
      selectSchema();
      if (!schema) throw new Error('Field definitions unavailable');
      if (input.value === initialValue && !area.contains(doc.activeElement)) render();
    }).catch(function () { notice.textContent = 'Field definitions are unavailable. Existing values can still be edited; the server will check the request.'; });
  }
  doc.querySelectorAll('select[id$="-operation"]').forEach(attach);
  doc.querySelectorAll('textarea[data-guided-endpoint],textarea[data-guided-selector]').forEach(attachJsonField);
})(typeof window === 'undefined' ? globalThis : window);
