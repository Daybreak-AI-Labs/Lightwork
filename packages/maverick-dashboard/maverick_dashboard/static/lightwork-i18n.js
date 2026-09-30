(function () {
  'use strict';
  var payload = document.getElementById('lightwork-i18n-data'), data = {language: 'en', locale: 'en', catalog: {}};
  try { if (payload) data = JSON.parse(payload.textContent); } catch (_) { /* Honest English fallback. */ }
  // A bounded page-local record preserves language when a product formatter's
  // string result is subsequently handed to a status or toast controller.
  var formatted = new Map();
  function lookup(source) { return Object.prototype.hasOwnProperty.call(data.catalog || {}, source) ? data.catalog[source] : formatted.get(source) || {text: source, lang: 'en'}; }
  function text(source, values) {
    var row = lookup(source), value = row.text.replace(/\{([A-Za-z0-9_]+)\}/g, function (whole, name) { return values && Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole; });
    if (formatted.size >= 256) formatted.delete(formatted.keys().next().value);
    formatted.set(value, {text:value, lang:row.lang}); return value;
  }
  function set(element, source, values) {
    if (!element || element.closest('[data-user-content]')) return;
    var row = lookup(source), cursor = 0, match, pattern = /\{([A-Za-z0-9_]+)\}/g;
    element.replaceChildren();
    while ((match = pattern.exec(row.text))) {
      element.appendChild(document.createTextNode(row.text.slice(cursor, match.index)));
      if (values && Object.prototype.hasOwnProperty.call(values, match[1])) {
        var value = document.createElement('bdi'); value.lang = 'und'; value.dir = 'auto';
        value.dataset.userContent = ''; value.textContent = String(values[match[1]]); element.appendChild(value);
      } else element.appendChild(document.createTextNode(match[0]));
      cursor = match.index + match[0].length;
    }
    element.appendChild(document.createTextNode(row.text.slice(cursor))); element.lang = row.lang;
    element.dir = row.lang === 'ar' ? 'rtl' : 'ltr'; element.dataset.productCopy = '';
  }
  function number(value, options) {
    var numeric = Number(value); if (!Number.isFinite(numeric)) return String(value);
    return new Intl.NumberFormat(data.locale || data.language || 'en', options || {}).format(numeric);
  }
  function formatDate(value, options) {
    // A product date without a time is a calendar date, not UTC midnight.
    var parts = typeof value === 'string' && /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    var date = parts ? new Date(Number(parts[1]), Number(parts[2])-1, Number(parts[3])) : value instanceof Date ? value : new Date(typeof value === 'number' || /^\d+(?:\.\d+)?$/.test(value) ? Number(value) * 1000 : value);
    if (Number.isNaN(date.getTime())) return text('Unknown');
    return new Intl.DateTimeFormat(data.locale || data.language || 'en', options || (parts ? {dateStyle: 'medium'} : {dateStyle: 'medium', timeStyle: 'short'})).format(date);
  }
  function plural(one, other, count) {
    var category = new Intl.PluralRules(data.locale || data.language || 'en').select(Number(count));
    return text(category === 'one' ? one : other, {count: number(count)});
  }
  function validation(form) {
    if (!form || form.dataset.productValidationBound) return;
    form.dataset.productValidationBound = 'true';
    form.addEventListener('invalid', function (event) {
      var field = event.target, validity = field.validity;
      if (!validity || validity.customError || !field.setCustomValidity) return;
      var source = validity.valueMissing ? 'Complete this field.' : validity.tooLong ? 'This value is too long.' : validity.typeMismatch ? 'Enter a valid value.' : 'Check this value.';
      var message = field._productValidationMessage;
      if (!message) {
        message = document.createElement('span'); message.className = 'product-validation'; message.setAttribute('role', 'alert');
        message.id = 'product-validation-' + Math.random().toString(36).slice(2);
        field.insertAdjacentElement('afterend', message); field._productValidationMessage = message;
        field.setAttribute('aria-describedby', [field.getAttribute('aria-describedby'), message.id].filter(Boolean).join(' '));
      }
      set(message, source); message.hidden = false; field.setCustomValidity(text(source));
    }, true);
    function clear(event) {
      var field = event.target;
      if (field._productValidationMessage) { field.setCustomValidity(''); field._productValidationMessage.hidden = true; }
    }
    form.addEventListener('input', clear); form.addEventListener('change', clear);
  }
  function translate(root) {
    (root || document).querySelectorAll('[data-i18n]').forEach(function (element) { set(element, element.getAttribute('data-i18n')); });
    (root || document).querySelectorAll('[data-product-date]').forEach(function (element) { if (!element.closest('[data-user-content]')) { element.textContent = formatDate(element.dataset.productDate); element.lang = data.locale || data.language; element.dir = 'auto'; } });
    (root || document).querySelectorAll('[data-product-number]').forEach(function (element) { if (!element.closest('[data-user-content]')) { element.textContent = number(element.dataset.productNumber); element.lang = data.locale || data.language; element.dir = 'auto'; } });
    (root || document).querySelectorAll('form[data-product-validation]').forEach(validation);
  }
  window.LightworkI18n = {text: text, set: set, languageFor: function (source) { return lookup(source).lang; }, has: function (source) { return Object.prototype.hasOwnProperty.call(data.catalog || {}, source); },
    number: number, formatDate: formatDate, plural: plural, translate: translate, validation: validation, locale: data.locale, language: data.language};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { translate(); }); else translate();
})();
