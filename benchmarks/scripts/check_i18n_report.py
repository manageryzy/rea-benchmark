"""Focused offline controls for report localization; no benchmark or API calls."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

import i18n_report as i18n


class CatalogControls(unittest.TestCase):
    def test_all_languages_have_complete_placeholder_safe_catalogs(self):
        catalogs = i18n.load_catalogs()
        self.assertGreater(len(catalogs['en']), 1000)
        self.assertEqual(i18n.catalog_issues(catalogs), [])
        self.assertEqual(set(catalogs['en']), set(catalogs['zh-CN']))
        self.assertEqual(set(catalogs['en']), set(catalogs['ja']))

    def test_missing_and_wrong_placeholder_are_reported(self):
        catalogs = {'en': {'Page {0} of {1}': 'Page {0} of {1}'},
                    'zh-CN': {}, 'ja': {'Page {0} of {1}': '{0}/{2}'}}
        self.assertEqual(i18n.catalog_issues(catalogs), [
            ('zh-CN', 'Page {0} of {1}', 'missing_translation'),
            ('ja', 'Page {0} of {1}', 'placeholder_mismatch')])
        catalogs['en']['Page {0} of {1}'] = 'Page {0}'
        self.assertIn(('en', 'Page {0} of {1}', 'placeholder_mismatch'), i18n.catalog_issues(catalogs))

    def test_missing_locale_is_warning_not_silent(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'en.json').write_text('{"Overview":"Overview"}')
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter('always')
                value = i18n.load_catalogs(folder)
            self.assertEqual(value['ja'], {})
            self.assertEqual(len(seen), 2)
            self.assertTrue(all('missing_language_file' in str(item.message) for item in seen))

    def test_duplicate_key_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'en.json').write_text('{"x":"one","x":"two"}')
            with self.assertRaisesRegex(ValueError, 'Duplicate translation key'):
                i18n.load_catalogs(folder)

    def test_scripts_preserve_literal_copy_without_html_execution(self):
        values = {locale: {'x': '</script><img src=x onerror=alert(1)>'} for locale in i18n.LOCALES}
        with patch.object(i18n, 'load_catalogs', return_value=values):
            assets = i18n.assets()
        for locale in i18n.LOCALES:
            raw = assets[f'assets/locales/{locale}.js'].decode()
            self.assertNotIn('</script>', raw)
            self.assertIn('\\u003c/script>', raw)
        self.assertTrue(all(name.endswith('.js') for name in assets))

    def test_evidence_identifiers_are_not_translated(self):
        for text in ['task_0123456789abcdef', 'deepseek-v4-pro', 'cpp--clang20--O3',
                     '7f' * 32, '2026-10-27T06:55:06+00:00', 'GPT-6.1 Sol']:
            self.assertEqual(i18n.source_parts(text, {}), [], text)
        self.assertEqual(i18n.source_parts('New untranslated instruction', {}), ['New untranslated instruction'])

    def test_parser_covers_accessibility_and_full_svg_but_not_code(self):
        reader = i18n.Texts()
        reader.feed('<h1>Overview</h1><pre>private raw text</pre><code>technical</code>'
                    '<svg><text data-i18n-label="Native languages"><tspan>Native</tspan>'
                    '<tspan>languages</tspan></text></svg><input placeholder="Search results">'
                    '<p translate="no">evidence identity</p><button aria-label="Next page">Next page</button>')
        self.assertEqual(reader.items, ['Overview', 'Native languages', 'Search results', 'Next page', 'Next page'])

    def test_strict_page_audit_names_missing_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'case.html').write_text('<h1>New untranslated instruction</h1>')
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter('always')
                with self.assertRaisesRegex(ValueError, 'i18n incomplete'):
                    i18n.audit(folder, strict=True)
            self.assertTrue(any('case.html: New untranslated instruction' in str(w.message) for w in seen))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, help='Strictly audit a generated site after focused controls')
    args = parser.parse_args()
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CatalogControls))
    if not outcome.wasSuccessful(): raise SystemExit(1)
    if args.site:
        result = i18n.audit(args.site, strict=True)
        print('I18N PAGE AUDIT:', json.dumps(result, ensure_ascii=False))
