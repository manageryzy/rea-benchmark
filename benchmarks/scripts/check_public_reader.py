#!/usr/bin/python3
"""Offline reader-report controls; no benchmark, grading or model execution."""
import copy
from html.parser import HTMLParser
import json
import re
import report_reader
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

import public_report
import report_reader_summary
import report_views
from report_labels import route_label


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids = set()
        self.links = []
        self.stack = []
        self.hidden_charts = []
        self.figures = 0
        self.feed(source)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if attrs.get('id'):
            if attrs['id'] in self.ids:
                raise AssertionError('Duplicate anchor: '+attrs['id'])
            self.ids.add(attrs['id'])
        if attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'figure':
            self.figures += 1
            if any(t == 'details' and 'open' not in a for t, a in self.stack):
                self.hidden_charts.append(attrs)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                self.stack = self.stack[:i]
                return


class ReaderControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='rea-reader-check-')
        cls.output = Path(cls.directory.name)
        cls.data = json.loads(public_report.SNAPSHOT.read_text())
        cls.original = copy.deepcopy(cls.data)
        cls.report = public_report.PublicReport(cls.output, cls.data)
        cls.report.build()
        cls.docs = {p.relative_to(cls.output).as_posix(): Document(p.read_text()) for p in cls.output.rglob('*.html')}

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_saved_scores_selection_and_accounting_are_unchanged(self):
        self.assertEqual(self.data, self.original)
        self.assertEqual(report_views.aggregate_stats(report_views.aggregate_cells(self.data),self.data['models']),
                         report_views.aggregate_stats(report_views.aggregate_cells(self.original),self.original['models']))

    def test_every_internal_link_and_anchor_resolves(self):
        for name, doc in self.docs.items():
            for href in doc.links:
                url = urlsplit(href)
                if url.scheme or url.netloc:
                    continue
                target = (self.output/name).parent / unquote(url.path) if url.path else self.output/name
                target = target.resolve().relative_to(self.output).as_posix()
                self.assertTrue((self.output/target).is_file(), (name, href))
                if url.fragment:
                    self.assertIn(unquote(url.fragment), self.docs[target].ids, (name, href))

    def test_reader_navigation_excludes_engineering_reports(self):
        for path in ('diagnostics.html','measurement.html','timings.html'):
            self.assertFalse((self.output/path).exists())
        for name in self.docs:
            source=(self.output/name).read_text()
            for old in ('diagnostics.html','measurement.html','timings.html'):
                self.assertNotIn(old, source, name)
            self.assertIn('reader-navigation',source,name)

    def test_primary_charts_are_visible_and_all_models_selected(self):
        for name in ('index.html','comparison.html','dimensions.html','costs.html'):
            self.assertGreater(self.docs[name].figures,0,name)
            self.assertFalse(self.docs[name].hidden_charts,name)
        for name in ('comparison.html','dimensions.html'):
            source=(self.output/name).read_text()
            self.assertIn('value="all-models" selected',source)
            self.assertIn('value="codex"',source)
            self.assertNotIn('deepseek-harness models',source)

    def test_all_saved_native_attempts_remain_in_usage_details(self):
        native = [r for r in self.report.all_records() if r.get('receipt')]
        identities = {(r['experiment'], r['case']['id'], r['model'], r['condition'], r.get('attempt_id')) for r in native}
        source = (self.output/'costs.html').read_text()
        links = re.findall(r'href="cases/[^"#]+#[^"]+"', source)
        self.assertEqual(len(links), len(identities))

    def test_deadline_zeros_are_labelled_as_time_limits(self):
        for row in self.report.all_records():
            if ((row.get('budget_condition') or {}).get('outcome') or {}).get('status') == 'deadline_exceeded':
                self.assertEqual(report_reader._native_state(row), 'Time limit reached / not passed')
                self.assertEqual(row['grade']['passed'], 0)
        for row in self.data['capability_measurements']['history']:
            if (row.get('budget_outcome') or {}).get('status') == 'deadline_exceeded':
                self.assertEqual(report_reader._typed_state(row), 'Time limit reached / not passed')
                self.assertEqual(row['grade']['passed'], 0)

    def test_codex_route_does_not_rewrite_saved_harness(self):
        for model, info in self.data['models'].items():
            if model.startswith('gpt-'):
                self.assertEqual(route_label(model,info),'Codex')
                self.assertEqual(info,self.original['models'][model])
        groups=report_views.cohorts(self.report.current_records(),self.data['models'])
        codex=next(models for key,_,models in groups if key=='codex')
        self.assertTrue(all(model.startswith('gpt-') for model in codex))


if __name__ == '__main__':
    unittest.main()
