#!/usr/bin/python3
"""Offline report aggregation controls. No providers, graders or validation suite."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import report_views as views


def native(condition='old', passed=1, *, grade=True, case='cpp', attempt='saved'):
    return {'model': 'model', 'model_label': 'Model', 'experiment': 'native', 'experiment_label': 'Native',
            'role': 'primary', 'case': {'id': case, 'suite': 'basic', 'language': 'cpp', 'label': case},
            'condition': condition, 'attempt_id': attempt, 'attempt_current': True,
            'target_build': {'compiler_family': 'GCC', 'optimization': 'O0'}, 'output_language': 'cpp',
            'receipt': {'elapsed_seconds': 1}, 'status': 'graded' if grade else 'not_started',
            'grade': {'passed': passed, 'total': 2} if grade else None, 'program_success': grade and passed == 2}


def data(rows=()):
    return {'models': {'model': {'label': 'Model'}}, 'records': [], 'condition_records': list(rows),
            'conditions': {'active_case_conditions': {'basic': 'active'}},
            'capability_measurements': {'cells': [], 'history': []},
            'capability_registration': {'observations': []}}


def typed(attempt='saved', passed=1):
    return {'model': 'model', 'task_id': 'task', 'family_id': 'family', 'profile_id': 'profile',
            'build_id': 'b' * 64, 'binary_sha256': 'a' * 64, 'public_package_sha256': 'c' * 64,
            'source_language': 'cpp', 'output_language': None, 'hint_profile': 'hint',
            'capability_ids': ['T1'], 'record_valid': True, 'attempt_id': attempt, 'status': 'graded',
            'analysis_condition': 'old', 'grade': {'passed': passed, 'total': 2, 'task_success': passed == 2,
                                                 'artifact_valid': True}}


class AggregateControls(unittest.TestCase):
    def test_valid_cache_counts_across_conditions_and_duplicates_once(self):
        saved, current = native(), native('active', grade=False)
        value = data([saved, current, copy.deepcopy(saved)])
        original = copy.deepcopy(value)
        rows = views.aggregate_native_rows(value)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['condition'], 'old')
        self.assertEqual(value, original)

    def test_selected_zero_beats_older_full_marks_not_best_score(self):
        rows = views.aggregate_native_rows(data([native(passed=2), native('active', passed=0)]))
        self.assertEqual(rows[0]['grade']['passed'], 0)
        self.assertTrue(views.valid_grade(rows[0]))

    def test_later_ungraded_failure_does_not_erase_grade(self):
        current = native('active', grade=False)
        current['status'] = 'environment_failure'
        self.assertEqual(views.aggregate_native_rows(data([native(), current]))[0]['condition'], 'old')

    def test_mismatched_build_or_output_and_diagnostic_are_not_reused(self):
        for field, change in [('target_build', {'compiler_family': 'Clang'}), ('output_language', 'c'),
                              ('role', 'diagnostic')]:
            saved = native()
            saved[field] = change
            rows = views.aggregate_native_rows(data([saved, native('active', grade=False)]))
            self.assertIsNone(rows[0]['grade'])

    def test_retired_cases_do_not_expand_current_denominator(self):
        value = data([native(case='retired'), native('active')])
        self.assertEqual([r['case']['id'] for r in views.aggregate_native_rows(value)], ['cpp'])

    def test_incomplete_or_malformed_grade_never_becomes_correctness_zero(self):
        for change in ({'status': 'budget_exhausted'}, {'record_valid': False},
                       {'failure_receipt_malformed': True}, {'grade': {'passed': 3, 'total': 2}},
                       {'grade': {'passed': float('nan'), 'total': 2}}):
            saved = {**native(), **change}
            self.assertIsNone(views.aggregate_native_rows(data([saved, native('active', grade=False)]))[0]['grade'])

    def test_complete_over_budget_is_counted_once_with_compliance_separate(self):
        saved = native('active', passed=2)
        saved['status'] = 'completed_over_budget'
        value = data([saved])
        stat = views.aggregate_stats(views.aggregate_cells(value), value['models'])[0]
        self.assertEqual((stat['graded_attempts'], stat['passed_cells'], stat['graded_over_budget']), (1, 1, 1))
        self.assertEqual(sum(stat['statuses'].values()), 1)

    def test_typed_valid_grade_reused_despite_preparation_and_condition_change(self):
        saved = typed()
        cell = {**saved, 'observation': None, 'status': 'preparation_stale', 'analysis_condition': 'active'}
        value = data()
        value['capability_measurements'] = {'cells': [cell], 'history': [saved, copy.deepcopy(saved)]}
        cells = views.aggregate_cells(value)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]['grade']['passed'], 1)

    def test_typed_target_contract_and_record_integrity_must_match(self):
        for field in ('binary_sha256', 'public_package_sha256', 'build_id', 'hint_profile', 'output_language'):
            saved = typed()
            cell = {**saved, 'observation': None, field: 'changed'}
            value = data()
            value['capability_measurements'] = {'cells': [cell], 'history': [saved]}
            self.assertIsNone(views.aggregate_cells(value)[0]['grade'], field)
        saved['record_valid'] = False
        value['capability_measurements'] = {'cells': [{**typed(), 'observation': None}], 'history': [saved]}
        self.assertIsNone(views.aggregate_cells(value)[0]['grade'])

    def test_typed_selected_zero_and_registration_order_never_best_score(self):
        older, newer = typed('older', 2), typed('newer', 0)
        value = data()
        value['capability_measurements'] = {'cells': [{**older, 'observation': None}], 'history': [older, newer]}
        value['capability_registration']['observations'] = [
            {'attempt_id': 'older', 'registered_at': '2026-10-25'}, {'attempt_id': 'newer', 'registered_at': '2026-10-26'}]
        self.assertEqual(views.aggregate_cells(value)[0]['grade']['passed'], 0)
        value['capability_measurements']['cells'][0]['observation'] = older
        self.assertEqual(views.aggregate_cells(value)[0]['grade']['passed'], 2)

    def test_large_categories_unmeasured_and_future_extension(self):
        value = data([native('active', passed=0)])
        saved = typed()
        value['capability_measurements'] = {'cells': [{**saved, 'observation': None}], 'history': []}
        group = views.aggregate_dimensions(views.aggregate_cells(value), value['models'])[0]
        axes = {axis['id']: score for axis, score in zip(group['axes'], group['models'][0]['values'])}
        self.assertEqual(axes['native']['score'], 0)
        self.assertIsNone(axes['types']['score'])
        self.assertNotIn('unpacking', axes)
        value['capability_measurements']['cells'][0]['capability_ids'] = ['U1']
        group = views.aggregate_dimensions(views.aggregate_cells(value), value['models'])[0]
        self.assertIn('unpacking', [a['id'] for a in group['axes']])

    def test_real_snapshot_conserves_configured_cells_and_cumulative_costs(self):
        import public_report
        value = public_report.validate(json.loads(public_report.SNAPSHOT.read_text()))
        view = public_report.PublicReport.__new__(public_report.PublicReport)
        view.data = value
        before = views.expenditure(view)
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('subprocess.Popen', side_effect=AssertionError('process forbidden')):
            cells = views.aggregate_cells(value)
        self.assertEqual(len(cells), 1170)
        self.assertEqual(len({c['key'] for c in cells}), len(cells))
        for model in value['models']:
            selected = [c for c in cells if c['model'] == model]
            self.assertEqual(len(selected), 195)
            self.assertEqual(sum(c['category'] == 'native' for c in selected), 42)
        self.assertEqual(before, views.expenditure(view))
        value['condition_records'] *= 2
        value['capability_measurements']['history'] *= 2
        self.assertEqual(views.aggregate_cells(value), cells)


if __name__ == '__main__':
    unittest.main()
