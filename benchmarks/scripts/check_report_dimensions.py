#!/usr/bin/python3
"""Focused offline checks for scores projected from saved native measurements."""
import copy
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

import report_views as views
import report_charts as charts


CATALOG = {
    'alpha': {'label': 'Alpha', 'harness': 'harness-a'},
    'beta': {'label': 'Beta', 'harness': 'harness-b'},
}


def row(case_id, *, model='alpha', language='cpp', suite='record-parser',
        passed=1, total=1, graded=True, standard=None, abi=None,
        build=None, review=None):
    return {
        'experiment': 'frozen', 'experiment_label': 'Frozen suite',
        'case': {'id': case_id, 'label': case_id, 'language': language,
                 'suite': suite, 'standard': standard, 'string_abi': abi},
        'model': model, 'condition': 'current', 'target_build': build,
        'grade': {'compiled': True, 'passed': passed, 'total': total} if graded else None,
        'readability': review,
    }


def group(groups, identity):
    return next(value for value in groups if value['id'] == identity)


def metric(groups, group_id, axis_id, model='alpha'):
    selected = group(groups, group_id)
    index = next(i for i, axis in enumerate(selected['axes']) if axis['id'] == axis_id)
    return next(item for item in selected['models'] if item['model'] == model)['values'][index]


class RadarMarkup(HTMLParser):
    """Collect the chart contract without an extra HTML parsing dependency."""
    def __init__(self, content):
        super().__init__()
        self.svgs = 0
        self.series = {}
        self.toggles = []
        self.current = None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'svg' and attrs.get('role') == 'group':
            self.svgs += 1
        if tag == 'g' and 'data-radar-series' in attrs:
            self.current = attrs['data-radar-series']
            self.series[self.current] = {'observed': int(attrs['data-observed-axes']),
                                         'points': [], 'polygons': 0}
        if tag == 'input' and 'data-radar-toggle' in attrs:
            self.toggles.append(attrs)
        if self.current is not None:
            if tag == 'circle' and 'dimension-point' in attrs.get('class', '').split():
                self.series[self.current]['points'].append(attrs)
            if tag == 'polygon' and 'dimension-profile' in attrs.get('class', '').split():
                self.series[self.current]['polygons'] += 1

    def handle_endtag(self, tag):
        if tag == 'g':
            self.current = None


class DimensionScores(unittest.TestCase):
    def test_case_equal_score_does_not_weight_large_check_sets_more(self):
        groups = views.dimension_groups([
            row('short', passed=1, total=1),
            row('long', passed=0, total=99),
        ], CATALOG)
        score = metric(groups, 'language', 'cpp')
        self.assertEqual(score['score'], 50.0)
        self.assertEqual(score['n'], 2)
        self.assertEqual(score['checks'], 100)
        self.assertEqual(len(score['members']), 2)

    def test_unmeasured_axis_is_distinct_from_measured_zero(self):
        groups = views.dimension_groups([
            row('zero', passed=0, total=12),
            row('ungraded', language='rust', graded=False),
        ], CATALOG)
        cpp = metric(groups, 'language', 'cpp')
        rust = metric(groups, 'language', 'rust')
        self.assertEqual((cpp['score'], cpp['n'], cpp['checks']), (0.0, 1, 12))
        self.assertIsNone(rust['score'])
        self.assertEqual(rust['n'], 0)
        self.assertFalse(rust['members'])

    def test_models_are_separate_and_missing_grades_do_not_dilute(self):
        groups = views.dimension_groups([
            row('one', passed=3, total=4),
            row('two', graded=False),
            row('one', model='beta', passed=1, total=4),
        ], CATALOG)
        self.assertEqual(metric(groups, 'language', 'cpp')['score'], 75.0)
        self.assertEqual(metric(groups, 'language', 'cpp', 'beta')['score'], 25.0)
        self.assertEqual(metric(groups, 'language', 'cpp')['n'], 1)

    def test_language_is_recorded_source_language_not_case_name_guess(self):
        groups = views.dimension_groups([
            row('looks-like-cpp', language='rust', passed=1, total=2),
        ], CATALOG)
        self.assertIsNone(metric(groups, 'language', 'cpp')['score'])
        self.assertEqual(metric(groups, 'language', 'rust')['score'], 50.0)

    def test_readability_counts_each_observed_dimension(self):
        groups = views.dimension_groups([
            row('review-one', review={'total': 3, 'scores': {'names': 2, 'formatting': 1}}),
            row('review-two', review={'total': 0, 'scores': {'names': 0}}),
            row('not-reviewed'),
        ], CATALOG)
        names = metric(groups, 'readability', 'names')
        formatting = metric(groups, 'readability', 'formatting')
        self.assertEqual((names['score'], names['n']), (50.0, 2))
        self.assertEqual((formatting['score'], formatting['n']), (50.0, 1))
        self.assertIsNone(metric(groups, 'readability', 'decomposition')['score'])
        self.assertIsNone(names['checks'])

    def test_unknown_build_metadata_does_not_create_compiler_observations(self):
        groups = views.dimension_groups([
            row('clang20-O3-cpp14-abi1', build=None),
        ], CATALOG)
        build = group(groups, 'build-conditions')
        for model in build['models']:
            for value in model['values']:
                self.assertEqual(value['n'], 0)
                self.assertIsNone(value['score'])

    def test_configured_build_points_are_visible_but_not_scored_as_executed(self):
        configured = [{'compiler_family': 'Clang', 'compiler_version': '20.1.2',
                       'optimization': level} for level in ('O0', 'O2', 'O3')]
        groups = views.dimension_groups([
            row('actual', build={'compiler_family': 'GCC', 'compiler_version': '13.3.0',
                                'optimization': 'O2'}),
        ], CATALOG, compiler_points=configured)
        self.assertEqual(metric(groups, 'build-conditions', 'compiler:GCC')['score'], 100.0)
        self.assertIsNone(metric(groups, 'build-conditions', 'compiler:Clang')['score'])
        self.assertIsNone(metric(groups, 'build-conditions', 'optimization:O0')['score'])
        self.assertIsNone(metric(groups, 'build-conditions', 'optimization:O3')['score'])
        self.assertEqual(metric(groups, 'build-conditions', 'optimization:O2')['n'], 1)

    def test_explicit_build_standard_and_abi_override_case_metadata(self):
        groups = views.dimension_groups([
            row('explicit', standard='c++11', abi=0,
                build={'standard': 'c++14', 'string_abi': 1}),
            row('legacy-explicit', standard='c++11', abi=0, passed=0),
            row('cpp14-abi1-in-name-is-not-evidence'),
        ], CATALOG)
        for axis, expected in [('c++11', 0.0), ('abi0', 0.0),
                               ('c++14', 100.0), ('abi1', 100.0)]:
            value = metric(groups, 'cpp-standard-abi', axis)
            self.assertEqual((value['score'], value['n']), (expected, 1))

    def test_behavior_uses_point_grade_not_whole_program_grade(self):
        first = row('cpp', suite='complex-native-v1', passed=999, total=1000)
        first['grade']['points'] = {'P0': {'passed': 0, 'total': 10},
                                    'P1': {'passed': 4, 'total': 4}}
        second = row('rust', language='rust', suite='complex-native-v1', passed=1, total=100)
        second['grade']['points'] = {'P0': {'passed': 100, 'total': 100}}
        groups = views.dimension_groups([first, second], CATALOG)
        value = metric(groups, 'behavior', 'P0')
        self.assertEqual((value['score'], value['n'], value['checks']), (50.0, 2, 110))
        self.assertEqual(metric(groups, 'behavior', 'P1')['score'], 100.0)
        self.assertIsNone(metric(groups, 'behavior', 'P2')['score'])

    def test_rust_byte_inspection_preserves_behavior_and_family_identity(self):
        original = row('rust', language='rust', suite='complex-native-v1')
        original['grade']['points'] = {'P4': {'passed': 0, 'total': 512}}
        variant = row('rust-data-v2', language='rust', suite='stl-native-v2')
        variant['grade']['points'] = {'P4': {'passed': 512, 'total': 512}}
        groups = views.dimension_groups([original, variant], CATALOG)
        value = metric(groups, 'behavior', 'P4')
        self.assertEqual((value['score'], value['n'], value['checks']), (50.0, 2, 1024))
        family = next(axis for axis in group(groups, 'task-family')['axes']
                      if axis['id'] == 'stl-native-v2')
        self.assertIn('Rust', family['label'])

    def test_whole_program_failure_does_not_invent_missing_point_breakdown(self):
        failed = row('rust', language='rust', suite='complex-native-v1', passed=0, total=3088)
        failed['grade'].update(compiled=False, points={})
        groups = views.dimension_groups([failed], CATALOG)
        self.assertEqual(metric(groups, 'language', 'rust')['score'], 0.0)
        value = metric(groups, 'behavior', 'P0')
        self.assertIsNone(value['score'])
        self.assertEqual(value['n'], 0)

    def test_renderer_shows_missing_as_text_and_retains_measured_zero(self):
        groups = views.dimension_groups([row('zero', passed=0)], CATALOG)
        rendered = charts.capability_dimensions([group(groups, 'language')], CATALOG)
        self.assertIn('dimension-unmeasured', rendered)
        self.assertIn('Unmeasured', rendered)
        self.assertIn('>0.0<', rendered)
        self.assertIn('observed dots only', rendered.lower())
        self.assertNotIn('<polygon class="dimension-profile"', rendered)
        self.assertIn('Exact dimension scores', rendered)

    def test_renderer_connects_only_complete_profiles_and_escapes_names(self):
        rows = [row('case-' + language, language=language) for language in ('cpp', 'rust', 'go')]
        groups = views.dimension_groups(rows, CATALOG)
        catalog = copy.deepcopy(CATALOG)
        catalog['alpha']['label'] = '<Unsafe & label>'
        rendered = charts.capability_dimensions([group(groups, 'language')], catalog)
        self.assertEqual(rendered.count('<polygon class="dimension-profile"'), 1)
        self.assertIn('&lt;Unsafe &amp; label&gt;', rendered)
        self.assertNotIn('<Unsafe & label>', rendered)
        self.assertIn('Polygon area is not an aggregate score', rendered)

    def test_renderer_overlays_models_once_and_keeps_missing_axes_unconnected(self):
        rows = [row('case-' + language, language=language) for language in ('cpp', 'rust', 'go')]
        rows.append(row('partial', model='beta', passed=0))
        groups = views.dimension_groups(rows, CATALOG)
        rendered = charts.capability_dimensions([group(groups, 'language')], CATALOG)
        markup = RadarMarkup(rendered)
        self.assertEqual(markup.svgs, 1, 'Each dimension group needs one shared radar')
        self.assertEqual(set(markup.series), {'alpha', 'beta'})
        self.assertEqual((markup.series['alpha']['observed'], markup.series['alpha']['polygons']), (3, 1))
        self.assertEqual((markup.series['beta']['observed'], markup.series['beta']['polygons']), (1, 0))
        self.assertEqual(len(markup.series['alpha']['points']), 3)
        self.assertEqual(len(markup.series['beta']['points']), 1)
        for series in markup.series.values():
            for point in series['points']:
                self.assertEqual(point.get('tabindex'), '0', 'Scores need keyboard readout')
                self.assertIn('data-radar-tip', point)
        self.assertEqual({item['value'] for item in markup.toggles}, {'alpha', 'beta'})
        self.assertTrue(all(item.get('type') == 'checkbox' and 'checked' in item for item in markup.toggles))
        self.assertIn('data-radar-group="language"', rendered)
        self.assertIn('data-radar-all', rendered)
        self.assertIn('data-radar-none', rendered)
        self.assertIn('data-radar-readout', rendered)

    def test_projection_preserves_input_objects(self):
        rows = [row('one', passed=2, total=3)]
        original = copy.deepcopy(rows)
        original_catalog = copy.deepcopy(CATALOG)
        views.dimension_groups(rows, CATALOG)
        self.assertEqual(rows, original)
        self.assertEqual(CATALOG, original_catalog)

    def test_projection_performs_no_network_or_process_calls(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('subprocess.Popen', side_effect=AssertionError('process forbidden')):
            groups = views.dimension_groups([row('saved')], CATALOG)
        self.assertEqual(metric(groups, 'language', 'cpp')['score'], 100.0)


if __name__ == '__main__':
    unittest.main()
