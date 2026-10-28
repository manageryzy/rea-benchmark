"""Focused offline controls for the consolidated HTML audit presentation fixes."""
import unittest
import re

import report_charts as charts
from report_states import STATES


class ReportChartReviewChecks(unittest.TestCase):
    def item(self, **overrides):
        row = dict(model='deepseek-flash', model_label='DeepSeek Flash', passed=8, checks=10,
                   planned_cells=1, graded_attempts=1, programs_passed=0, points_passed=0,
                   points_total=1, readability_mean=None, readability_n=0,
                   runtime_median_seconds=12, runtime_n=1, runtime_timeouts=0,
                   runtime_values_seconds=[12], started_attempts=1,
                   cost={'low_usd': 0.000001, 'high_usd': 0.000001, 'usage_complete': True},
                   statuses={'graded': 1})
        row.update(overrides)
        return row

    def test_budget_stop_keeps_planned_denominator(self):
        result = charts.completion([self.item(statuses={'budget_exhausted': 1}, graded_attempts=0)])
        self.assertIn('state-budget_exhausted', result)
        self.assertIn('width:100.0000%', result)
        self.assertIn('0/1 graded', result)
        self.assertNotIn('Completion data diagnostic', result)

    def test_each_state_has_a_count_and_unknown_is_diagnostic(self):
        result = charts.completion([self.item(statuses=dict.fromkeys(STATES, 1), planned_cells=len(STATES))])
        for state, label in STATES.items():
            self.assertIn('state-' + state, result)
            self.assertIn(label, result)
        self.assertNotIn('Completion data diagnostic', result)
        unknown = charts.completion([self.item(statuses={'unexpected': 1})])
        self.assertIn('Completion data diagnostic', unknown)
        self.assertIn('Unknown state: unexpected', unknown)

    def test_native_html_inventory_is_readable(self):
        result = charts.capability_coverage({'configured_cells': 918, 'scheduled_cells': 4,
                                            'saved_observations': 7, 'totals': [{'graded_cells': 2}]})
        self.assertNotIn('<svg', result)
        self.assertEqual(result.count('class="coverage-card"'), 4)
        self.assertIn('<dd>918</dd>', result)

    def test_only_structurally_equivalent_metrics_are_combined(self):
        separate = charts.score_charts([self.item()])
        self.assertIn('<figcaption>Function points passing every check</figcaption>', separate)
        combined = charts.score_charts([self.item()], structurally_single_point=True)
        self.assertNotIn('<figcaption>Function points passing every check</figcaption>', combined)
        self.assertIn('One function point per basic program', combined)
        self.assertIn('Original program and function-point counts', combined)

    def test_tokens_unknown_is_distinct_from_reported_zero(self):
        rows = [{'model': 'deepseek-flash', 'receipt': {'reported_usage': {'inputTokens': None, 'outputTokens': 0}}}]
        result = charts.token_chart(rows, {'deepseek-flash': {'label': 'DeepSeek Flash'}})
        self.assertIn('Total unknown', result)
        self.assertIn('<td class="num">Fresh input</td><td class="num">Unknown</td>', result)
        self.assertIn('<td class="num">Output</td><td class="num">0</td>', result)
        self.assertIn('Exact token categories and reporting coverage', result)

    def test_proven_no_forwards_is_zero(self):
        rows = [{'model': 'deepseek-flash', 'receipt': {'reported_usage': {},
                 'usage_coverage': {'proven_zero_forwards': True}}}]
        result = charts.token_chart(rows, {'deepseek-flash': {'label': 'DeepSeek Flash'}})
        self.assertIn('0 observed reported-total tokens', result)
        self.assertIn('<td class="num">Fresh input</td><td class="num">0</td>', result)

    def test_small_nonzero_cost_and_scope_survive_presentation(self):
        result = charts.cost_chart([self.item()], scope='Shared 3 cases; recorded 2026-10-24 UTC')
        # Decimal and scientific notation must both preserve the nonzero amount.
        displayed = [float(value) for value in re.findall(r'\$([0-9.eE+-]+)', result)]
        self.assertTrue(displayed)
        self.assertTrue(all(value == 0.000001 for value in displayed))
        self.assertIn('Shared 3 cases; recorded 2026-10-24 UTC', result)
        self.assertIn('not cost-efficiency rankings', result)
        self.assertIn('Exact cost estimates', result)

    def test_runtime_population_and_exact_samples(self):
        result = charts.runtime_chart([self.item()])
        self.assertIn('Only graded attempts with saved durations', result)
        self.assertIn('12.000', result)
        self.assertIn('Linear seconds scale', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
