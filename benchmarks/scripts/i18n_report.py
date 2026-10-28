"""Report-only gettext-style catalogues; offline assets and missing-copy warnings.

Message IDs are English templates. Translations, including the English display
copy, live in separate JSON files. Technical evidence identifiers stay verbatim.
"""
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import warnings

ASSETS = Path(__file__).parent / 'report_assets'
LOCALES = ('en', 'zh-CN', 'ja')
PLACEHOLDER = re.compile(r'\{(\d+)\}')
VARIABLE = re.compile(r'\b[a-f0-9]{12,64}\b|(?<![\w])\d+(?:[,.]\d+)*(?![\w])')
TECHNICAL = re.compile(r'^(?:[a-f0-9]{12,64}|[A-Z]\d+|(?:cpp|rust|go|c)\d*|[\w.+:/@\\-]*[_/\\-][\w.+:/@\\-]*|DeepSeek V[\d.]+ (?:Flash|Pro)(?: \d+)?|GPT-[\d.]+ (?:Sol|Luna)|GLM-[\d.]+|(?:g\+\+ |Ubuntu clang version |libstdc\+\+ ).*)$')
VERBATIM = {'C', 'C++', 'Go', 'Rust', 'GCC', 'Clang', 'DSH', 'ZCode', 'Codex', 'Antigravity', 'REA', 'Ghidra', 'JST', 'USD', 'CNY', 'n', 'n=0'}
PATTERNS = ('Evidence: {0}', 'Condition: {0}. Status: {1}.', 'Semantic checks: {0}',
            '{0} · output {1}', 'Excluded: {0}', 'Not reviewed. Provenance: {0}',
            'Not applicable to this structured artifact. Provenance: {0}',
            'Search {0}', '{0} model scores', '{0} [{1}]; {2}',
            '{0} overlapping model profiles', '{0} model comparison',
            '{0}: {1}/{2}', '{0}: {1}% ({2}/{3})', 'historical {0}', 'snapshot {0}', 'Snapshot {0}',
            'Recorded request/step timestamp span: {0} to {1}. Recorded rate periods: {2}.', '{0} [{1}]', 'output {0}', '{0}: {1}')


def normalize(text):
    values = []
    def replace(match):
        values.append(match[0])
        return '{' + str(len(values) - 1) + '}'
    return VARIABLE.sub(replace, ' '.join(text.split())), values


def technical(text):
    if re.fullmatch(r'[\w.+:/@\\;\[\](),<> =—–-]+', text) and all(
            re.fullmatch(r'(?:[\w.-]*[_-][\w.-]*|[A-Z]\d+|\d+|[a-f0-9]{12,}|\[?task_[a-f0-9]+\]?|cpp|rust|go|n|v\d+)[;,\[\] ]*', word)
            for word in text.split()): return True
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]+', text): return True
    return (not re.search(r'[A-Za-z\u3040-\u30ff\u4e00-\u9fff]', text)
            or text in VERBATIM or bool(TECHNICAL.fullmatch(text)))


def pattern(source):
    chunks = PLACEHOLDER.split(source)
    return re.compile('^' + ''.join('(.+?)' if i % 2 else re.escape(part)
                                   for i, part in enumerate(chunks)) + '$')


def source_parts(text, english, depth=0):
    """Return catalogue IDs used by text; unknown prose is never silently ignored."""
    text = ' '.join(text.split())
    if not text or technical(text): return []
    source, _ = normalize(text)
    if source in english: return [source]
    if depth < 8:
        for template in PATTERNS:
            if template.startswith('{'): continue
            found = pattern(template).fullmatch(text)
            if found:
                return [template] + [key for value in found.groups()
                                     for key in source_parts(value, english, depth + 1)]
        for separator in (' · ', '; ', ' / '):
            if separator in text:
                return [key for value in text.split(separator)
                        for key in source_parts(value, english, depth + 1)]
        for template in PATTERNS:
            found = pattern(template).fullmatch(text)
            if found:
                return [template] + [key for value in found.groups()
                                     for key in source_parts(value, english, depth + 1)]
    return [source]


def load_catalogs(directory=ASSETS / 'locales'):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate translation key: ' + key)
            if not isinstance(value, str): raise ValueError('Translation must be text: ' + key)
            result[key] = value
        return result
    result = {}
    for locale in LOCALES:
        path = Path(directory) / (locale + '.json')
        if not path.exists():
            warnings.warn(f'i18n missing_language_file: {locale}: {path}', RuntimeWarning)
            result[locale] = {}
        else:
            result[locale] = json.loads(path.read_text(), object_pairs_hook=unique)
    return result


def catalog_issues(catalogs):
    issues = []
    for locale in LOCALES:
        for key, source in catalogs['en'].items():
            translated = catalogs.get(locale, {}).get(key)
            if not translated:
                issues.append((locale, key, 'missing_translation'))
            elif set(PLACEHOLDER.findall(key)) != set(PLACEHOLDER.findall(translated)):
                issues.append((locale, key, 'placeholder_mismatch'))
    return issues


def assets():
    catalogs = load_catalogs()
    for locale, key, reason in catalog_issues(catalogs):
        warnings.warn(f'i18n {reason}: {locale}: {key}', RuntimeWarning)
    result = {'assets/i18n.js': (ASSETS / 'i18n.js').read_bytes()}
    for locale, values in catalogs.items():
        result['assets/locales/' + locale + '.js'] = ('window.REA_TRANSLATIONS = window.REA_TRANSLATIONS || {};\n'
            + 'window.REA_TRANSLATIONS[' + json.dumps(locale) + '] = '
            + json.dumps(values, ensure_ascii=False).replace('<', '\\u003c') + ';\n').encode()
    result['assets/i18n-config.js'] = ('window.REA_I18N_CONFIG = ' + json.dumps({
        'locales': list(LOCALES), 'patterns': list(PATTERNS), 'verbatim': sorted(VERBATIM),
        'variable': VARIABLE.pattern, 'technical': TECHNICAL.pattern}, ensure_ascii=False) + ';\n').encode()
    return result


class Texts(HTMLParser):
    def __init__(self):
        super().__init__(); self.stack = []; self.items = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        ignored = any(skip for _, skip in self.stack)
        own_skip = tag in ('code', 'pre', 'script', 'style') or values.get('translate') == 'no' or 'data-no-i18n' in values
        if not ignored and not own_skip:
            self.items.extend(value for name, value in attrs if value and name in
                              ('title','aria-label','placeholder','alt','data-radar-tip','data-i18n-label'))
        if tag not in ('br','hr','meta','link','input','img','wbr','source'):
            self.stack.append((tag, ignored or own_skip or 'data-i18n-label' in values))
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                self.stack = self.stack[:i]; break
    def handle_data(self, text):
        if not any(skip for _, skip in self.stack): self.items.append(text)


def audit(output, *, strict=False):
    catalogs = load_catalogs(); missing = {}
    for path in sorted(Path(output).rglob('*.html')):
        reader = Texts(); reader.feed(path.read_text())
        for text in reader.items:
            for key in source_parts(text, catalogs['en']):
                if key not in catalogs['en']: missing.setdefault(key, str(path.relative_to(output)))
    issues = catalog_issues(catalogs)
    for key, page in missing.items(): warnings.warn(f'i18n missing_source: {page}: {key}', RuntimeWarning)
    if strict and (missing or issues): raise ValueError(f'i18n incomplete: {len(missing)} source messages, {len(issues)} translations')
    return {'missing_sources': missing, 'translation_issues': issues}
