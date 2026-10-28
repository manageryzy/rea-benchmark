/* Offline report localization. No network, HTML injection, or evidence mutation. */
(() => {
  'use strict';
  const config = window.REA_I18N_CONFIG;
  const catalogs = window.REA_TRANSLATIONS || {};
  const locales = config?.locales || ['en', 'zh-CN', 'ja'];
  const names = Object.fromEntries(locales.map(id => [id, catalogs[id]?.['locale.name'] || id]));
  const english = catalogs.en || {};
  const sourceNodes = new WeakMap(), sourceAttributes = new WeakMap();
  const warnings = new Map(), logged = new Set();
  const skip = 'script,style,code,pre,[translate="no"],[data-no-i18n],.i18n-controls,.i18n-warning,[data-i18n-label]';
  const placeholder = /\{(\d+)\}/g;
  const compact = s => s.replace(/\s+/g, ' ').trim();
  const escapeRE = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const variable = new RegExp(config?.variable || '\\b[a-f0-9]{12,64}\\b|(?<![\\w])\\d+(?:[,.]\\d+)*(?![\\w])', 'g');
  const technicalRE = config && new RegExp(config.technical);
  const verbatim = new Set(config?.verbatim || []);
  const patterns = (config?.patterns || []).map(key => ({key, re: new RegExp('^' + key.split(/(\{\d+\})/).map(part => /^\{\d+\}$/.test(part) ? '(.+?)' : escapeRE(part)).join('') + '$')}));
  let locale = 'en';
  const available = value => locales.includes(value) ? value : value?.startsWith('zh') ? 'zh-CN' : value?.startsWith('ja') ? 'ja' : value?.startsWith('en') ? 'en' : null;
  function requestedLocale() {
    const param = new URL(location.href).searchParams.get('lang');
    if (param !== null) return available(param) || 'en';
    try {const saved = available(localStorage.getItem('rea.report.language')); if (saved) return saved;} catch (_) {}
    return available(navigator.language) || 'en';
  }
  function warn(key, reason) {
    const id = `${locale}:${reason}:${key}`;
    warnings.set(id, {locale, key, reason});
    if (!logged.has(id)) {logged.add(id); console.warn(`[REA i18n] ${id}`);}
  }
  function isTechnical(text) {
    if (/^\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]+$/.test(text)) return true;
    if (!/[A-Za-z\u3040-\u30ff\u4e00-\u9fff]/.test(text) || verbatim.has(text) || technicalRE?.test(text)) return true;
    return text.split(/\s+/).every(word => /^(?:[\w.-]*[_-][\w.-]*|[A-Z]\d+|\d+|[a-f0-9]{12,}|\[?task_[a-f0-9]+\]?|cpp|rust|go|n|v\d+)[;,\[\] ]*$/.test(word));
  }
  function substitute(key, args) {
    const base = english[key];
    if (!base) {warn(key, 'missing_source'); return key.replace(placeholder, (_, n) => args[n] ?? `{${n}}`);}
    let value = catalogs[locale]?.[key];
    const tokens = s => [...new Set([...s.matchAll(/\{(\d+)\}/g)].map(m => m[1]))].sort().join(',');
    if (!value) {warn(key, 'missing_translation'); value = base;}
    else if (tokens(value) !== tokens(key)) {warn(key, 'placeholder_mismatch'); value = base;}
    return value.replace(placeholder, (_, n) => args[n] ?? `{${n}}`);
  }
  function translate(text, depth = 0) {
    const trimmed = compact(text);
    if (!trimmed || isTechnical(trimmed)) return text;
    const args = [];
    const key = trimmed.replace(variable, value => {args.push(value); return `{${args.length - 1}}`;});
    let result;
    if (Object.hasOwn(english, key)) result = substitute(key, args);
    else if (depth < 8) {
      const specific = patterns.find(item => !item.key.startsWith('{') && item.re.test(trimmed));
      const separator = [' · ', '; ', ' / '].find(value => trimmed.includes(value));
      if (specific) result = substitute(specific.key, trimmed.match(specific.re).slice(1).map(s => translate(s, depth + 1)));
      else if (separator) result = trimmed.split(separator).map(s => translate(s, depth + 1)).join(separator);
      else {
        const matched = patterns.find(item => item.re.test(trimmed));
        if (matched) result = substitute(matched.key, trimmed.match(matched.re).slice(1).map(s => translate(s, depth + 1)));
      }
    }
    if (result === undefined) {warn(key, 'missing_source'); result = trimmed;}
    return text.slice(0, text.indexOf(text.trimStart())) + result + (text.match(/\s*$/)?.[0] || '');
  }
  function rawText(element) {
    if (element.nodeType === Node.TEXT_NODE) return sourceNodes.get(element)?.source ?? element.data;
    return [...element.childNodes].map(rawText).join('');
  }
  function rawAttribute(element, name) {return sourceAttributes.get(element)?.get(name)?.source ?? element.getAttribute(name);}
  function textNode(node) {
    if (!node.parentElement || node.parentElement.closest(skip)) return;
    const prior = sourceNodes.get(node);
    const source = prior && node.data === prior.rendered ? prior.source : node.data;
    const rendered = translate(source);
    sourceNodes.set(node, {source, rendered});
    if (node.data !== rendered) node.data = rendered;
  }
  function attributes(element) {
    if (element.closest(skip)) return;
    let saved = sourceAttributes.get(element); if (!saved) {saved = new Map(); sourceAttributes.set(element, saved);}
    for (const name of ['title', 'aria-label', 'placeholder', 'alt', 'data-radar-tip']) {
      if (!element.hasAttribute(name)) continue;
      const prior = saved.get(name), current = element.getAttribute(name);
      const source = prior && current === prior.rendered ? prior.source : current;
      const rendered = translate(source); saved.set(name, {source, rendered});
      if (current !== rendered) element.setAttribute(name, rendered);
    }
  }
  function translateTree(root) {
    if (root.nodeType === Node.TEXT_NODE) return textNode(root);
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) attributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) walker.currentNode.nodeType === Node.TEXT_NODE ? textNode(walker.currentNode) : attributes(walker.currentNode);
  }
  function radarLabels() {
    document.querySelectorAll('[data-i18n-label]').forEach(node => {
      const value = translate(node.dataset.i18nLabel), cjk = locale !== 'en';
      const limit = cjk ? 8 : Number(node.dataset.i18nWrap) || 18;
      const parts = cjk ? [...value] : value.split(' '), lines = []; let line = '';
      for (const part of parts) {const joined = line + (line && !cjk ? ' ' : '') + part; if ([...joined].length > limit && line) {lines.push(line);line = part;} else line = joined;}
      if (line) lines.push(line);
      if (node.dataset.i18nY) node.setAttribute('y', String(Number(node.dataset.i18nY) - (lines.length - 1) * 9));
      node.replaceChildren(...lines.map((s, index) => {const span = document.createElementNS('http://www.w3.org/2000/svg', 'tspan');span.setAttribute('x', node.getAttribute('x'));span.setAttribute('dy', String(index ? 18 : 0));span.textContent = s;return span;}));
    });
  }
  const controls = document.createElement('div'); controls.className = 'i18n-controls';
  const label = document.createElement('label'), select = document.createElement('select'); select.id = 'report-language'; select.setAttribute('aria-label', 'Language / 语言 / 言語');
  for (const id of locales) select.add(new Option(names[id], id));
  const languageLabel = document.createTextNode('Language ');label.append(languageLabel, select);controls.append(label);
  const banner = document.createElement('details');banner.className = 'i18n-warning';banner.hidden = true;banner.setAttribute('role', 'status');banner.setAttribute('aria-live', 'polite');
  const headline = document.createElement('summary'), list = document.createElement('ul');banner.append(headline, list);
  (document.querySelector('header') || document.body).prepend(controls, banner);
  let displayedWarnings = null;
  function warningDisplay() {
    const identity = [...warnings.keys()].join('\n');
    if (identity === displayedWarnings) return;
    displayedWarnings = identity;
    banner.hidden = warnings.size === 0;
    const warningKey = '{0} translation warning(s); missing text uses English.';
    headline.textContent = (catalogs[locale]?.[warningKey] || english[warningKey] || warningKey).replace('{0}', String(warnings.size));
    list.replaceChildren(...[...warnings.values()].slice(0, 100).map(item => {const li = document.createElement('li');li.textContent = `${item.locale}: ${item.reason}: ${item.key}`;return li;}));
    document.documentElement.dataset.i18nWarnings = String(warnings.size);
  }
  function links() {
    document.querySelectorAll('a[href]').forEach(link => {
      const url = new URL(link.href, location.href);
      if (url.protocol !== location.protocol || url.host !== location.host || !url.pathname.endsWith('.html')) return;
      url.searchParams.set('lang', locale);link.href = url.href;
    });
  }
  function changeLanguage(value, persist = false) {
    locale = available(value) || 'en';warnings.clear();
    if (!config) warn('i18n-config.js', 'missing_language_config');
    if (!catalogs.en) warn('en', 'missing_language_file');
    if (!catalogs[locale]) warn(locale, 'missing_language_file');
    select.value = locale; document.documentElement.lang = locale;
    languageLabel.data = translate('Language') + ' ';select.setAttribute('aria-label', translate('Language'));
    displayedWarnings = null;
    translateTree(document);radarLabels();links();warningDisplay();
    if (persist) {
      try {localStorage.setItem('rea.report.language', locale);} catch (_) {}
      const url = new URL(location.href);url.searchParams.set('lang', locale);history.replaceState(null, '', url.href);
    }
    window.dispatchEvent(new CustomEvent('rea:languagechange', {detail: {locale}}));
  }
  window.REA_I18N = {translate, rawText, rawAttribute, changeLanguage,
    get locale() {return locale;}, get warnings() {return [...warnings.values()];}};
  select.addEventListener('change', () => changeLanguage(select.value, true));
  window.addEventListener('popstate', () => changeLanguage(requestedLocale()));
  window.addEventListener('pageshow', () => changeLanguage(requestedLocale()));
  const observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.target.nodeType === Node.ELEMENT_NODE && record.target.closest('.i18n-controls,.i18n-warning')) continue;
      if (record.type === 'characterData') textNode(record.target);
      else if (record.type === 'attributes') attributes(record.target);
      else record.addedNodes.forEach(node => translateTree(node));
    }
    warningDisplay();
  });
  changeLanguage(requestedLocale());
  observer.observe(document.body, {childList: true, subtree: true, characterData: true,
    attributes: true, attributeFilter: ['title','aria-label','placeholder','alt','data-radar-tip']});
})();
