#!/usr/bin/python3
"""Focused saved-data reporting checks; no inference, grading or full rebuild."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import public_report
import report
import report_views as views


class ReaderViews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = public_report.validate(json.loads(public_report.SNAPSHOT.read_text()))

    def view(self):
        value = public_report.PublicReport.__new__(public_report.PublicReport)
        value.data = copy.deepcopy(self.data)
        return value

    def test_check_weighted_and_case_equal_use_same_strict_grades(self):
        rows = self.view().current_records()
        for item in report.summary(rows, self.data['models']):
            graded = [r for r in rows if r['model'] == item['model'] and r['grade'] and r['grade']['total']]
            if not graded:
                continue
            self.assertEqual(item['checks'], sum(r['grade']['total'] for r in graded))
            self.assertEqual(item['passed'], sum(r['grade']['passed'] for r in graded))
            self.assertAlmostEqual(item['case_equal_mean'], sum(100*r['grade']['passed']/r['grade']['total'] for r in graded)/len(graded))

    def test_empty_cohort_does_not_render_zero_comparison(self):
        view = self.view()
        rows = copy.deepcopy(view.current_records())
        missing_model = next(iter(view.data['models']))
        for row in rows:
            if row['model'] == missing_model:
                row['grade'] = None
        with patch.object(view, 'current_records', return_value=rows):
            rendered = views.comparison(view)
        shared_panel = rendered.split('data-chart-panel="matched"', 1)[1].split('data-chart-panel="all"', 1)[0]
        self.assertIn('No common graded cases', shared_panel)
        self.assertNotIn('reported tokens', shared_panel)
        self.assertNotIn('bar-track', shared_panel)

    def test_one_case_cohort_is_explicit(self):
        view = self.view()
        rows = view.current_records()
        selected = next((r['experiment'], r['case']['id']) for r in rows if r['grade'])
        rows = [r for r in rows if (r['experiment'], r['case']['id']) == selected]
        with patch.object(view, 'current_records', return_value=rows):
            result = views.comparison(view)
        self.assertIn('shared 1/1 native cases', result)
        self.assertIn('Included cases', result)

    def test_public_unknown_total_stays_unknown_in_case_table(self):
        view = self.view()
        row = next(r for r in view.current_records() if r['receipt'])
        row['receipt']['reported_usage']['totalTokens'] = None
        self.assertIn('Unknown', view.case_table([row]))
        self.assertEqual(report.summary([row], view.data['models'])[0]['total_tokens'], 0)

    def test_rejection_is_not_semantic_execution(self):
        row = {'status': 'graded', 'grade': {'artifact_valid': False, 'task_success': False,
               'return_provenance': 'not_executed', 'passed': 0, 'total': 24}}
        self.assertEqual(views.typed_outcome(row), ('Submission format rejected', 'Not executed', '0/24 strict task points'))
        row.update(status='budget_exhausted', grade=None)
        self.assertIn('ungraded', views.typed_outcome(row)[0])

    def test_small_nonzero_cost_remains_nonzero_in_tables(self):
        value = report.money({'low_usd': 0.00000000001, 'high_usd': 0.00000000001, 'usage_complete': True})
        self.assertNotEqual(value, '$0.0000')
        self.assertGreater(float(value.removeprefix('$')), 0)

    def test_cost_ledger_deduplicates_attempts_not_grades(self):
        view = self.view()
        original = views.expenditure(view)
        view.data['records'] += copy.deepcopy(view.data['records'])
        view.data['capability_measurements']['history'] += copy.deepcopy(view.data['capability_measurements']['history'])
        doubled = views.expenditure(view)
        self.assertEqual(original, doubled)
        self.assertEqual(views.cost_sum(original), views.cost_sum(doubled))
        self.assertEqual({r['track'] for r in original}, {'Native reconstruction', 'Typed capabilities'})

    def test_capability_landing_separates_inventory_and_keeps_history(self):
        view = self.view()
        pages = {}
        with patch.object(view, 'page', side_effect=lambda name, title, body: pages.update({name: body})), patch.object(view, 'capability_inventory_page') as inventory:
            views.capabilities(view)
        inventory.assert_called_once()
        self.assertNotIn('id="typed-cells"', pages['capabilities.html'])
        for row in self.data['capability_measurements']['history']:
            name = 'attempts/' + row['attempt_id'] + '.html'
            self.assertIn(name, pages)
            self.assertIn(name, pages['capabilities.html'])
        self.assertIn('id="metrics"', pages['capabilities.html'])
        self.assertIn('All valid saved grades count once', pages['capabilities.html'])
        self.assertIn('Overall capability radar', pages['capabilities.html'])

    def test_focused_views_render_without_process_or_network(self):
        with tempfile.TemporaryDirectory(prefix='rea-reader-views-') as root, patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('subprocess.Popen', side_effect=AssertionError('process forbidden')):
            view = public_report.PublicReport(Path(root), copy.deepcopy(self.data))
            view.overview()
            view.costs()
            view.capabilities_page()
            for name in ('index.html', 'costs.html', 'capabilities.html', 'capability-inventory.html'):
                self.assertTrue((Path(root) / name).is_file())


if __name__ == '__main__':
    unittest.main()
