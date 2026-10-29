#!/usr/bin/python3
"""Offline privacy and reproducibility acceptance for the Pages artifact."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import public_report as public
import records
import report_prices


def hashes(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


class PublicAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = public.validate(json.loads(public.SNAPSHOT.read_text()))

    def test_frozen_case_build_survives_missing_top_level_annotation(self):
        row = copy.deepcopy(next(r for r in self.data['condition_records'] if r['case']['language'] == 'cpp'))
        build = {'compiler_family': 'Clang', 'compiler_version': '20.1.2',
                 'optimization': 'O3', 'standard': 'c++20', 'string_abi': 1,
                 'standard_library': 'libstdc++ 13', 'strip_mode': 'all'}
        row['target_build'] = None
        row['case']['target_build'] = {**build, 'private_build_path': 'PRIVATE_CANARY'}
        projected = public.project_row(row)
        self.assertEqual(projected['target_build'], build)
        self.assertNotIn('PRIVATE_CANARY', json.dumps(projected))
        self.assertIsNone(public.project_row({**row, 'case': {**row['case'], 'target_build': None}})['target_build'])

    def test_refresh_rejects_lost_build_metadata_before_publishing(self):
        previous = copy.deepcopy(self.data)
        row = next(r for r in previous['condition_records'] if r['case']['language'] == 'cpp')
        row['target_build'] = {'compiler_family': 'Clang', 'compiler_version': '20.1.2',
                               'optimization': 'O3', 'standard': 'c++20', 'string_abi': 1,
                               'standard_library': 'libstdc++ 13', 'strip_mode': 'all'}
        fresh = copy.deepcopy(previous)
        fresh['condition_records'][previous['condition_records'].index(row)]['target_build'] = None
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / 'summary.json'
            snapshot.write_text(json.dumps(previous))
            before = snapshot.read_bytes()
            with patch.object(public, 'SNAPSHOT', snapshot), \
                    patch.object(public, 'project', return_value=fresh), \
                    patch.object(public, 'build') as build:
                with self.assertRaisesRegex(ValueError, 'drop saved target build metadata'):
                    public.refresh(SimpleNamespace(data=fresh))
                build.assert_not_called()
            self.assertEqual(snapshot.read_bytes(), before)

    def test_malformed_failure_receipt_is_preserved_and_safely_projected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(records, 'ROOT', Path(tmp)):
            path = Path(tmp) / 'harness-failure.json'
            self.assertIsNone(records.failure_marker(path))
            for raw in (b'', b'{', b'[]', b'{}', b'\xff'):
                path.write_bytes(raw)
                failure = records.failure_marker(path)
                self.assertEqual(failure['classification'], 'malformed_failure_receipt')
                self.assertFalse(failure['model_correctness_grade_allowed'])
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(records.INPUTS[path.name], hashlib.sha256(raw).hexdigest())
            path.write_text('{"classification":"provider_failure"}')
            self.assertEqual(records.failure_marker(path), {'classification': 'provider_failure'})
            path.write_bytes(b'')
            with self.assertRaises(json.JSONDecodeError): records.read(path)
        row = copy.deepcopy(self.data['condition_records'][0])
        row.update(grade=None, readability=None, failure_receipt_malformed=True,
                   environment_failure={'classification': 'malformed_failure_receipt', 'raw_error': 'PRIVATE'})
        projected = public.project_row(row)
        self.assertTrue(projected['failure_receipt_malformed'])
        self.assertNotIn('PRIVATE', json.dumps(projected))
        public.validate_value(projected, public.ROW_SCHEMA)

    @staticmethod
    def strip_recent_presentation_fields(data):
        """Make an actual legacy fixture from a newer generated snapshot."""
        data.pop('price_bases', None)
        for row in data['records'] + data['condition_records']:
            row.pop('pricing_basis_id', None)
            row['cost'].pop('periods', None)
            if row['receipt']:
                row['receipt'].pop('usage_coverage', None)

    def test_old_summary_does_not_invent_warm_timings(self):
        data = copy.deepcopy(self.data)
        self.strip_recent_presentation_fields(data)
        data['schema_version'] = 9
        data.pop('runtime_performance')
        migrated = public.validate(data)
        self.assertEqual(migrated['schema_version'], 10)
        self.assertEqual(migrated['runtime_performance'], {'warm': None, 'cold': None})

    def test_registration_admission_mode_is_optional_bounded_and_preserved(self):
        from capabilities import registration_report
        row = {'attempt_id': 'a' * 64, 'registration_id': None, 'binding_id': None,
               'status': 'historical_unregistered', 'registered_at': None}
        value = registration_report.empty()
        value.update(status='installed', observations=[row])
        schema = public.SCHEMA['capability_registration']
        public.validate_value(value, schema)
        registration_report.validate(value)
        self.assertNotIn('admission_mode', registration_report.public_projection(value)['observations'][0])
        for mode in ('strict_offline_acceptance', 'trusted_local; offline_acceptance_not_rechecked'):
            row['admission_mode'] = mode
            projected = registration_report.public_projection(value)
            public.validate_value(projected, schema)
            registration_report.validate(projected)
            self.assertEqual(projected['observations'][0]['admission_mode'], mode)
        row['admission_mode'] = 'private or invented claim'
        with self.assertRaisesRegex(ValueError, 'admission mode'):
            registration_report.validate(value)
        row['admission_mode'] = 1
        with self.assertRaises(ValueError):
            public.validate_value(value, schema)
        row.pop('admission_mode')
        row['raw_reasoning'] = 'PRIVATE_CANARY'
        with self.assertRaisesRegex(ValueError, 'publication fields'):
            public.validate_value(value, schema)

    def test_native_history_has_distinct_identity_and_only_one_current_row(self):
        data = copy.deepcopy(self.data)
        current = copy.deepcopy(data['condition_records'][0])
        current.update(attempt_id='a' * 64, attempt_current=True)
        historical = {**copy.deepcopy(current), 'attempt_id': 'b' * 64, 'attempt_current': False}
        data.update(records=[], condition_records=[current, historical])
        public.validate(data)
        data['condition_records'].append(copy.deepcopy(historical))
        with self.assertRaisesRegex(ValueError, 'Duplicate publication row'):
            public.validate(data)
        data['condition_records'].pop()
        historical['attempt_current'] = True
        with self.assertRaisesRegex(ValueError, 'Duplicate current publication row'):
            public.validate(data)
        historical.update(attempt_current=False, attempt_id=None)
        with self.assertRaisesRegex(ValueError, 'lacks identity'):
            public.validate(data)
        historical['attempt_id'] = 'not-an-action-digest'
        with self.assertRaisesRegex(ValueError, 'attempt identity'):
            public.validate(data)

    def test_performance_projection_drops_private_proofs(self):
        data = copy.deepcopy(self.data)
        data.update(environment_audit={'cases': []}, condition_evidence_audit={'cases': []},
            paging_validation={}, glm_diagnosis={'cases': []},
            cpp_build_points={'points': [], 'language_feature_probes': {'probes': []}})
        data['runtime_performance'] = {'cold': None, 'warm': {
            'measured_at': '2026-10-23T00:00:00+00:00', 'total_seconds': 10.0,
            'budget_seconds': 180, 'prepared_cases': 153, 'native_saved_cases': 58,
            'reusable_native_cases': 47, 'retained_changed_input_cases': 11,
            'accepted_regression_checks': 83, 'verification_cache_hit': True,
            'current': True, 'passed': True, 'model_api_calls': 0, 'provider_launches': 0,
            'stages': [{'name': name, 'seconds': 1.0} for name in (
                'acceptance_receipts', 'prepared_case_bytes', 'saved_native_artifacts',
                'fresh_content_seal', 'persistent_action_cache')],
            'inputs_sha256': {'private': 'PRIVATE_PROOF_CANARY'}, 'reasoning': 'PRIVATE_PROOF_CANARY'}}
        projected = public.project(SimpleNamespace(data=data, build_id='f' * 64))
        self.assertNotIn('PRIVATE_PROOF_CANARY', json.dumps(projected))
        public.validate(projected)
        projected['runtime_performance']['warm']['model_api_calls'] = 1
        with self.assertRaisesRegex(ValueError, 'offline warm'):
            public.validate(projected)

    def benchmark_timing_fixture(self):
        return {'schema_version': 1, 'mode': 'offline', 'status': 'offline_cache_miss',
            'total_seconds': 4.0, 'selected_tasks': 4, 'cache_hits': 0,
            'provider_launches': 0, 'model_api_calls': 0, 'failures': 2, 'unprocessed_tasks': 2,
            'stages': [{'stage': 'selection', 'status': 'complete', 'elapsed_seconds': 1.0},
                       {'stage': 'execute', 'status': 'complete', 'elapsed_seconds': 2.0},
                       {'stage': 'finalization', 'status': 'skipped', 'elapsed_seconds': 0.0}]}

    def test_benchmark_timing_projection_and_html_exclude_private_text(self):
        data = copy.deepcopy(self.data)
        data.update(environment_audit={'cases': []}, condition_evidence_audit={'cases': []},
            paging_validation={}, glm_diagnosis={'cases': []},
            cpp_build_points={'points': [], 'language_feature_probes': {'probes': []}})
        benchmark = self.benchmark_timing_fixture()
        canary = 'PRIVATE_TIMING_REASONING_CREDENTIAL_CANARY'
        benchmark.update(reason=canary, selection=canary, error=canary)
        benchmark['stages'][0]['reason'] = canary
        data['runtime_performance'] = {'warm': None, 'cold': None, 'benchmark': benchmark}
        projected = public.validate(public.project(SimpleNamespace(data=data, build_id='f' * 64)))
        self.assertNotIn(canary, json.dumps(projected))
        view = public.Report.__new__(public.Report)
        view.data = projected
        rendered = view.runtime_storage_section()
        self.assertIn('missing inference caches; provider launches refused', rendered)
        self.assertIn('4.000 seconds', rendered)
        self.assertIn('An offline cache miss', rendered)
        self.assertNotIn(canary, rendered)
        projected['runtime_performance'].pop('benchmark')
        self.assertNotIn('benchmark', public.validate(projected)['runtime_performance'])

    def test_benchmark_timing_validation_rejects_unbounded_or_misleading_data(self):
        data = copy.deepcopy(self.data)
        for mutate in (
                lambda value: value.update(reason='private error'),
                lambda value: value.update(total_seconds=float('inf')),
                lambda value: value.update(total_seconds=604801),
                lambda value: value.update(provider_launches=1),
                lambda value: value.update(status='complete'),
                lambda value: value['stages'][0].update(stage='private/error/path'),
                lambda value: value['stages'][0].update(elapsed_seconds=None),
                lambda value: value['stages'].append(dict(value['stages'][0]))):
            with self.subTest(mutate=mutate):
                value = self.benchmark_timing_fixture()
                mutate(value)
                data['runtime_performance'] = {'warm': None, 'cold': None, 'benchmark': value}
                with self.assertRaises(ValueError):
                    public.validate(data)

    def test_benchmark_timing_keeps_live_startup_and_offline_cache_misses_distinct(self):
        from report import benchmark_timing_summary
        receipt = {'timings': {'schema_version': 1, 'total_seconds': 4.0,
                              'stages': self.benchmark_timing_fixture()['stages']},
            'selected_tasks': 4, 'provider_launches': 0, 'model_api_calls': 0,
            'unprocessed_tasks': 2, 'offline': True,
            'failures': [{'error': 'Offline benchmark: missing inference cache; provider launch refused'}]}
        self.assertEqual(benchmark_timing_summary(receipt)['status'], 'offline_cache_miss')
        receipt['offline'] = False
        self.assertEqual(benchmark_timing_summary(receipt)['status'], 'incomplete')
        receipt['preparation_failed'] = True
        self.assertEqual(benchmark_timing_summary(receipt)['status'], 'pre_dispatch_failure')
        receipt.pop('preparation_failed')
        receipt.update(provider_launches=1, model_api_calls=None, interrupted=True)
        value = benchmark_timing_summary(receipt)
        self.assertEqual(value['status'], 'stopped')
        self.assertIsNone(value['model_api_calls'])
        self.assertNotIn('error', json.dumps(value))
        self.assertIsNone(benchmark_timing_summary({}))

    def test_preparation_inspection_is_a_valid_measured_stage(self):
        value = self.benchmark_timing_fixture()
        value['stages'].insert(1, {'stage': 'preparation_inspection', 'status': 'complete',
                                  'elapsed_seconds': 0.1})
        public.validate_benchmark_timing(value)
        value['stages'][1]['stage'] = 'unknown-stage'
        with self.assertRaises(ValueError):
            public.validate_benchmark_timing(value)

    def test_historical_control_scope_survives_report_upgrade(self):
        from capabilities import measurements
        historical = measurements.empty()
        historical.update(schedule_status='configuration_snapshot_only', schedule_id='a' * 64)
        historical['validation'].update(passed=True, current=False, checks=27)
        data = copy.deepcopy(self.data)
        data['capability_measurements'] = historical
        public.validate(data)
        # The same recorded pre-T3 control count must not certify any T3 cell.
        expanded = copy.deepcopy(historical)
        expanded['cells'] = [{'capability_ids': ['T3']}]
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(expanded)
        import report
        historical_view = report.Report.__new__(report.Report)
        historical_view.data = copy.deepcopy(data)
        historical_view.data['capability_preparation']['supported_capabilities'] = ['T1', 'T2', 'T3', 'T5']
        self.assertEqual(historical_view.type_preparation_label(), 'T1/T2/T3/T5 preparation')
        self.assertIn('T4 preparation is not recorded in this snapshot', historical_view.audit_plan_section())
        self.assertNotIn('Tagged variants and subobjects: T4 preparation', historical_view.capability_preparation_section())
        # A pre-T4 receipt is also historical evidence, not T4 certification.
        expanded['cells'] = [{'capability_ids': ['T4']}]
        expanded['validation']['checks'] = 28
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(expanded)
        expanded['cells'] = [{'capability_ids': ['T6']}]
        expanded['validation']['checks'] = 29
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(expanded)

    def test_projection_excludes_private_payloads(self):
        row = copy.deepcopy(next(r for r in self.data['records'] if r['grade'] and r['readability']))
        canary = 'PRIVATE_REASONING_AND_CREDENTIAL_CANARY'
        row.update(answer=canary, reasoning=canary, directory='/home/private', raw_json=canary)
        row['source'] = '/home/private/reconstructed.cpp'
        row['receipt'].update(requests=[{'Authorization': canary}], raw_usage=canary, api_key=canary)
        row['grade'].update(failure=canary, first_mismatches=[canary])
        row['grade']['grading_runtime'] = {'id': 'a' * 64, 'source_inputs_sha256': {'private-path': canary}}
        row['grade']['grading_version'] = 'exact-returns-v3'
        row['readability']['notes'] = canary
        sanitized = public.project_row(row)
        self.assertNotIn(canary, json.dumps(sanitized))
        self.assertNotIn('/home/', json.dumps(sanitized))
        public.validate_value(sanitized, public.ROW_SCHEMA)
        # Sanitization preserves the numeric usage, scores and cost.
        self.assertEqual(sanitized['cost'], row['cost'])
        self.assertEqual(sanitized['receipt']['reported_usage'], row['receipt']['reported_usage'])
        self.assertEqual(sanitized['grade']['passed'], row['grade']['passed'])

    def test_missing_token_categories_remain_unknown_and_zero_is_preserved(self):
        row = copy.deepcopy(next(r for r in self.data['records'] if r['receipt']))
        row['receipt'] = {'reported_usage': {'inputTokens': 10, 'outputTokens': 0}}
        before = copy.deepcopy(row['cost'])
        projected = public.project_row(row)
        self.assertEqual(projected['receipt']['reported_usage'], {
            'inputTokens': 10, 'cacheReadTokens': None, 'cacheWriteTokens': None,
            'outputTokens': 0, 'totalTokens': None})
        self.assertEqual(projected['cost'], before)
        public.validate_value(projected, public.ROW_SCHEMA)
        row['receipt'] = {'reported_usage': {}}
        projected = public.project_row(row)
        self.assertTrue(all(value is None for value in projected['receipt']['reported_usage'].values()))
        rendered = public.charts.token_chart([projected], self.data['models'])
        self.assertIn('Unknown', rendered)
        view = public.PublicReport.__new__(public.PublicReport)
        view.data = copy.deepcopy(self.data)
        view.data['records'], view.data['condition_records'] = [projected], []
        self.assertIn('Unknown', view.case_table([projected]))
        rendered_pages = {}
        view.page = lambda name, title, body: rendered_pages.update({name: body})
        public.report_views.costs(view)
        self.assertIn('Unknown', rendered_pages['costs.html'])

    def test_request_coverage_distinguishes_missing_usage_and_proven_zero(self):
        complete = {'requests': [{'request_id': '1', 'finished_at': '2026-10-23T00:00:00Z'}],
                    'usage_observations': [{'request_id': '1', 'usage': {'inputTokens': 5, 'outputTokens': 2}}]}
        self.assertEqual(report_prices.coverage(complete), {'recorded_requests': 1,
            'requests_with_incomplete_usage': 0, 'proven_zero_forwards': False})
        partial = {**complete, 'requests': complete['requests'] + [{'request_id': '2'}]}
        self.assertEqual(report_prices.coverage(partial)['requests_with_incomplete_usage'], 1)
        self.assertIsNone(report_prices.coverage({})['recorded_requests'])
        row = copy.deepcopy(next(r for r in self.data['records'] if r['receipt']))
        row['receipt'] = {'provider_forwards': 0, 'request_ledger_complete': True}
        projected = public.project_row(row)
        self.assertTrue(projected['receipt']['usage_coverage']['proven_zero_forwards'])
        self.assertEqual(projected['receipt']['reported_usage'], dict.fromkeys(public.USAGE.split(), 0))

    def test_sanitized_pricing_dates_rates_and_missing_invoice_are_visible(self):
        prices = {'verified_at': '2026-10-19T08:08:34+00:00', 'model': 'glm-5.3',
            'currency': 'CNY', 'unit_tokens': 1000000, 'input': 8, 'cache_read': 2, 'output': 28,
            'source': 'https://docs.bigmodel.cn/cn/guide/start/pricing',
            'fx': {'date': '2026-10-16', 'cny_per_usd': 6.68,
                   'source': 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'},
            'api_key': 'PRIVATE_PRICING_CANARY', 'raw_json': 'PRIVATE_PRICING_CANARY',
            'endpoint': 'https://private.example.invalid/endpoint'}
        item = report_prices.basis(prices, 'glm-5.3')
        item['raw_response'] = 'PRIVATE_PRICING_CANARY'
        projected = public.project_price_basis(item)
        public.validate_value(projected, public.PRICE_BASIS)
        data = copy.deepcopy(self.data)
        data['price_bases'] = [projected]
        public.validate(data)
        html = report_prices.section(data)
        self.assertIn('2026-10-16', html)
        self.assertIn('6.68 CNY', html)
        self.assertIn('actual invoice debit unknown', html)
        self.assertIn('Cache write', html)
        self.assertIn('Unknown', html)
        self.assertNotIn('PRIVATE_PRICING_CANARY', html)
        self.assertNotIn('private.example', html)
        projected['source_url'] = 'https://docs.bigmodel.cn/example?token=secret'
        with self.assertRaisesRegex(ValueError, 'pricing source'):
            public.validate(data)

    def test_all_native_states_project_and_budget_stop_is_ungraded(self):
        from report_states import STATES
        row = copy.deepcopy(next(r for r in self.data['records'] if r['receipt']))
        data = copy.deepcopy(self.data)
        data['records'], data['condition_records'] = [row], []
        row.update(grade=None, points_total=None, points_passed=None, program_success=False)
        for status in STATES:
            row['status'] = status
            public.validate(data)
        row['status'] = 'budget_exhausted'
        rendered = public.charts.completion([{'model_label': row['model_label'],
            'planned_cells': 1, 'graded_attempts': 0, 'statuses': {'budget_exhausted': 1}}])
        self.assertIn('budget', rendered.lower())
        self.assertIsNone(row['grade'])
        row['status'] = 'unsupported-state'
        with self.assertRaisesRegex(ValueError, 'result state'):
            public.validate(data)

    def test_baseline_receipts_distinguish_source_and_pin_drift(self):
        from capabilities.baselines import report as baseline
        name = 'benchmarks/scripts/capabilities/baselines/report.py'
        expected = {name: hashlib.sha256((baseline.ROOT / name).read_bytes()).hexdigest()}
        with tempfile.TemporaryDirectory(prefix='baseline-report-control-') as temporary:
            path = Path(temporary) / 'receipt.json'
            value = {'schema_version': 1, 'scope': 'fixture_scope', 'release_accepted': False,
                     'model_api_calls': 0, 'passed': True, 'checks': 16, 'failed': 0,
                     'inputs_sha256': expected, 'pins': {'compiler': 'a' * 64}}
            path.write_text(json.dumps(value))
            module = SimpleNamespace(RECEIPT=path, MINIMUM_CHECKS=16,
                fingerprints=lambda: expected, pins=lambda: value['pins'])
            with patch.object(baseline, 'SCOPES', (('matched_baselines', module, 'fixture_scope'),)), \
                 patch.object(baseline, '_track_pin_inputs') as tracked, \
                 patch.object(subprocess, 'Popen', side_effect=AssertionError('Process forbidden')), \
                 patch.object(subprocess, 'run', side_effect=AssertionError('Process forbidden')):
                row = baseline.for_report(lambda item: item.read_bytes())[0]
                self.assertTrue(row['passed'] and row['current'])
                self.assertTrue(tracked.called)
                module.pins = lambda: {'compiler': 'b' * 64}
                row = baseline.for_report(lambda item: item.read_bytes())[0]
                self.assertTrue(row['passed'])
                self.assertFalse(row['current'])
                module.pins = lambda: value['pins']
                module.fingerprints = lambda: {name: 'c' * 64}
                self.assertFalse(baseline.for_report(lambda item: item.read_bytes())[0]['current'])
                value['scope'] = 'different_acceptance_scope'
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, 'offline receipt'):
                    baseline.for_report(lambda item: item.read_bytes())

    def test_baseline_pin_metadata_is_declared_without_loading_code_or_archives(self):
        from capabilities.baselines import report as baseline
        sdk = {language: character * 64 for language, character in zip(('c', 'cpp', 'rust', 'go'), 'abcd')}
        engine, wheel = '1' * 64, '2' * 64
        metadata = {
            baseline.image_manifest.CONFIG: {},
            baseline.image_manifest.CACHE / 'active.json': {key: {'id': value} for key, value in sdk.items()},
            baseline.executor.CONFIG: {'engine': {'sha256': wheel}},
            baseline.executor.CACHE / 'active.json': {'id': engine},
            baseline.executor.CACHE / (engine + '.json'): {},
            baseline.executor.CACHE / 'provenance' / engine / (wheel + '.json'): {},
        }
        for identity in sdk.values():
            metadata[baseline.image_manifest.CACHE / 'profiles' / (identity + '.json')] = {}
        for directory, identity in ((baseline.build.family_build.TOOLS, 'e' * 64), (baseline.tooling.CACHE, 'f' * 64)):
            metadata[directory / 'active.json'] = {'id': identity}
            metadata[directory / (identity + '.json')] = {}
        read = []
        def reader(path):
            read.append(path)
            return json.dumps(metadata[path]).encode()
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('Process forbidden')), \
             patch.object(subprocess, 'run', side_effect=AssertionError('Process forbidden')):
            baseline._track_pin_inputs(reader)
        self.assertEqual(set(read), set(metadata))

    def test_baseline_public_projection_keeps_only_aggregate_controls(self):
        data = copy.deepcopy(self.data)
        canary = 'PRIVATE_BASELINE_SOURCE_REASONING_TOKEN_CANARY'
        rows = [{'id': name, 'passed': True, 'current': True, 'checks': count, 'failed': 0,
                 'pins': {'credential': canary}, 'raw_json': canary, 'candidate': canary}
                for name, count in (('matched_baselines', 16), ('rust_buffers', 17), ('go_buffers', 18))]
        data['capability_preparation']['controls'] = rows
        data.update(environment_audit={'cases': []}, condition_evidence_audit={'cases': []},
            paging_validation={}, glm_diagnosis={'cases': []},
            cpp_build_points={'points': [], 'language_feature_probes': {'probes': []}})
        projected = public.project(SimpleNamespace(data=data, build_id='f' * 64))
        public.validate(projected)
        self.assertNotIn(canary, json.dumps(projected))
        self.assertEqual(projected['records'], [public.project_row(row) for row in self.data['records']])
        self.assertEqual(projected['condition_records'], [public.project_row(row) for row in self.data['condition_records']])
        self.assertEqual(projected['capability_measurements'], self.data['capability_measurements'])

    def test_legacy_snapshot_does_not_gain_matched_baseline_acceptance(self):
        import report
        view = report.Report.__new__(report.Report)
        view.data = copy.deepcopy(self.data)
        ids = {'matched_baselines', 'rust_buffers', 'go_buffers'}
        view.data['capability_preparation']['controls'] = [row for row in
            view.data['capability_preparation']['controls'] if row['id'] not in ids]
        body = view.capability_preparation_section()
        self.assertNotIn('Compiler and language baseline preparation', body)
        self.assertNotIn('Matched compiler and language baseline workflow', body)

    def test_pending_baseline_acceptance_is_rendered_without_model_scores(self):
        import report
        view = report.Report.__new__(report.Report)
        view.data = copy.deepcopy(self.data)
        rows = [{'id': name, 'passed': True, 'current': True, 'checks': count, 'failed': 0}
                for name, count in (('matched_baselines', 16), ('rust_buffers', 17), ('go_buffers', 18))]
        rows[-1].update(passed=False, checks=0)
        view.data['capability_preparation']['controls'].extend(rows)
        view.data['capability_measurements']['cells'] = [row for row in
            view.data['capability_measurements']['cells'] if row['hint_profile'] != 'known_matched_buffer_entry_v1']
        before = copy.deepcopy(view.data['capability_measurements'])
        body = view.capability_preparation_section()
        self.assertIn('Rust candidate preparation:</strong> current offline acceptance passed', body)
        self.assertIn('Go candidate preparation:</strong> offline acceptance pending or no longer current', body)
        self.assertIn('Matched compiler and language baseline workflow', body)
        self.assertIn('outside the configured model inventory', body)
        self.assertEqual(view.data['capability_measurements'], before)
        rows[0]['current'] = False
        body = view.capability_preparation_section()
        self.assertIn('Matched-matrix acceptance is pending or no longer current', body)
        self.assertNotIn('The current matrix receipt verifies all 1,026', body)

    def test_matched_enrollment_is_taken_from_saved_cells_without_model_observations(self):
        import report
        view = report.Report.__new__(report.Report)
        view.data = copy.deepcopy(self.data)
        view.data['capability_preparation']['controls'] = [
            {'id': name, 'passed': True, 'current': True, 'checks': count, 'failed': 0}
            for name, count in (('matched_baselines', 16), ('rust_buffers', 17), ('go_buffers', 18))]
        cells = [{'task_id': family + '_' + compiler + '_' + language + '_' + condition,
                  'profile_id': compiler + '_' + language + '_' + condition}
                 for family in ('record_codec', 'dispatch_registry', 'numeric_aggregation',
                                'variable_objects', 'callback_policy', 'session_state')
                 for compiler, languages in (('clang20', ('c', 'cpp')), ('rust', ('c', 'rust')), ('go', ('c', 'go')))
                 for language in languages for condition in ('recover', 'codegen')]
        view.data['capability_measurements']['cells'] = cells * 6
        before = copy.deepcopy(view.data)
        self.assertEqual(view.matched_baseline_tasks(), 72)
        body = view.capability_preparation_section()
        self.assertIn('72 paired task definitions are configured in the existing coordinator', body)
        self.assertIn('Configured tasks; inference disabled', body)
        self.assertNotIn('outside the configured model inventory', body)
        self.assertIn('Independent validity review, model ablations and release acceptance remain pending', body)
        self.assertIn('72 matched compiler/language task definitions', view.audit_plan_section())
        self.assertEqual(view.data, before)

    def test_pointer_cell_report_scope_comes_from_the_saved_inventory(self):
        import report
        view = report.Report.__new__(report.Report)
        view.data = copy.deepcopy(self.data)
        new_profiles = ('pointer_topology_recover', 'pointer_topology_recover_counterfactual')
        view.data['capability_measurements']['cells'] = [row for row in
            view.data['capability_measurements']['cells'] if row['profile_id'] not in new_profiles]
        legacy = view.capability_preparation_section()
        self.assertIn('these originals use depth one', legacy)
        self.assertNotIn('four configured conditions vary', legacy)
        before = copy.deepcopy(view.data)
        view.data['capability_measurements']['cells'].extend(
            {'task_id': 'new_' + profile, 'profile_id': profile} for profile in new_profiles)
        body = view.capability_preparation_section()
        self.assertIn('four configured conditions vary', body)
        self.assertIn('separate pointer cells and guards', body)
        self.assertNotIn('these originals use depth one', body)
        self.assertEqual(view.data['records'], before['records'])
        self.assertEqual(view.data['condition_records'], before['condition_records'])
        self.assertEqual(view.data['capability_measurements']['history'],
                         before['capability_measurements']['history'])

    def test_schema_rejects_unknown_fields_and_credentials(self):
        allowed = copy.deepcopy(self.data)
        allowed['capability_preparation']['controls'] = [{'id': 'c_lifecycle', 'passed': True, 'current': True,
                                                         'checks': 22, 'failed': 0}]
        public.validate(allowed)
        allowed['capability_preparation']['controls'][0]['id'] = 'coordinator_incremental'
        public.validate(allowed)
        allowed['capability_preparation']['controls'][0]['id'] = 'unknown_control'
        with self.assertRaisesRegex(ValueError, 'preparation controls'):
            public.validate(allowed)
        for location in ['root', 'receipt', 'label', 'runtime', 'grading_runtime', 'c_executor', 'capability_preparation', 'capability_measurements', 'capability_registration']:
            data = copy.deepcopy(self.data)
            if location == 'root':
                data['raw_requests'] = []
            elif location == 'receipt':
                next(r for r in data['records'] if r['receipt'])['receipt']['headers'] = {}
            elif location == 'runtime':
                data['runtime_attestation']['provider_credentials'] = 'PRIVATE-CANARY'
            elif location == 'grading_runtime':
                data['grading_runtime']['raw_manifest'] = 'PRIVATE-CANARY'
            elif location == 'c_executor':
                data['c_executor']['guest_records'] = ['PRIVATE-CANARY']
            elif location == 'capability_preparation':
                data['capability_preparation']['oracle_records'] = ['PRIVATE-CANARY']
            elif location == 'capability_measurements':
                data['capability_measurements']['raw_reasoning'] = ['PRIVATE-CANARY']
            elif location == 'capability_registration':
                data['capability_registration']['compiler_commands'] = ['PRIVATE-CANARY']
            else:
                next(iter(data['models'].values()))['label'] = 'sk-' + 'A' * 32
            with self.assertRaises(ValueError):
                public.validate(data)

    def test_legacy_snapshot_does_not_gain_grading_attestation(self):
        data = copy.deepcopy(self.data)
        self.strip_recent_presentation_fields(data)
        data['schema_version'] = 3
        data.pop('runtime_performance', None)
        data.pop('task_budget_policy')
        data.pop('grading_runtime')
        data.pop('c_executor')
        data.pop('capability_preparation')
        data.pop('capability_measurements')
        data.pop('capability_registration')
        for row in data['records'] + data['condition_records']:
            if row['grade']:
                row['grade'].pop('grading_runtime_status')
                row['grade'].pop('grading_runtime_id')
        migrated = public.validate(data)
        self.assertEqual(migrated['schema_version'], 10)
        self.assertFalse(migrated['grading_runtime']['controls_passed'])
        self.assertFalse(migrated['c_executor']['controls_passed'])
        for row in migrated['records'] + migrated['condition_records']:
            if row['grade']:
                self.assertEqual(row['grade']['grading_runtime_status'], 'historical_limited')
                self.assertEqual(row['grade']['grading_runtime_id'], '')

    def test_native_grades_are_not_promoted_by_executor_metadata(self):
        data = copy.deepcopy(self.data)
        self.strip_recent_presentation_fields(data)
        data['schema_version'] = 4
        data.pop('runtime_performance', None)
        data.pop('task_budget_policy')
        data.pop('c_executor')
        data.pop('capability_preparation')
        data.pop('capability_measurements')
        data.pop('capability_registration')
        migrated = public.validate(data)
        self.assertFalse(migrated['c_executor']['controls_passed'])
        for row in migrated['records'] + migrated['condition_records']:
            if row['grade']:
                self.assertEqual(row['grade']['return_provenance'], 'native_process_unauthenticated')

    def test_preparation_migration_does_not_invent_capability_measurements(self):
        data = copy.deepcopy(self.data)
        self.strip_recent_presentation_fields(data)
        data['schema_version'] = 5
        data.pop('runtime_performance', None)
        data.pop('task_budget_policy')
        data.pop('capability_preparation')
        data.pop('capability_measurements')
        data.pop('capability_registration')
        migrated = public.validate(data)
        self.assertEqual(migrated['schema_version'], 10)
        self.assertEqual(migrated['capability_preparation']['status'], 'not_recorded')
        self.assertEqual(migrated['capability_preparation']['frozen_tasks'], 0)
        self.assertEqual(migrated['records'], data['records'])

    def test_typed_report_migration_does_not_invent_observations_or_denominators(self):
        data = copy.deepcopy(self.data)
        self.strip_recent_presentation_fields(data)
        data['schema_version'] = 6
        data.pop('runtime_performance', None)
        data.pop('task_budget_policy')
        data.pop('capability_measurements')
        data.pop('capability_registration')
        migrated = public.validate(data)
        self.assertEqual(migrated['schema_version'], 10)
        self.assertEqual(migrated['capability_measurements']['schedule_status'], 'not_recorded')
        self.assertEqual(migrated['capability_measurements']['saved_observations'], 0)
        self.assertEqual(migrated['capability_measurements']['scheduled_cells'], 0)
        self.assertEqual(migrated['records'], data['records'])

    def test_registration_migration_cannot_retroactively_certify_old_measurements(self):
        data = copy.deepcopy(self.data)
        self.strip_recent_presentation_fields(data)
        data['schema_version'] = 7
        data.pop('runtime_performance', None)
        data.pop('task_budget_policy')
        data.pop('capability_registration')
        migrated = public.validate(data)
        self.assertEqual(migrated['schema_version'], 10)
        self.assertEqual(migrated['capability_registration']['status'], 'not_recorded')
        self.assertEqual(migrated['capability_registration']['schedules'], [])
        self.assertEqual(migrated['capability_registration']['observations'], [])
        self.assertEqual(migrated['records'], data['records'])
        self.assertEqual(migrated['condition_records'], data['condition_records'])

    def test_budget_policy_migration_does_not_certify_old_runs(self):
        data = copy.deepcopy(self.data)
        self.strip_recent_presentation_fields(data)
        data['schema_version'] = 8
        data.pop('runtime_performance', None)
        data.pop('task_budget_policy')
        migrated = public.validate(data)
        self.assertEqual(migrated['schema_version'], 10)
        self.assertIsNone(migrated['task_budget_policy'])
        self.assertEqual(migrated['records'], data['records'])
        self.assertEqual(migrated['condition_records'], data['condition_records'])
        for invalid in (0, True, '8192'):
            broken = copy.deepcopy(self.data)
            broken['task_budget_policy'] = {'policy_id': 'fixture', 'limits': {
                'max_model_turns': 12, 'max_response_tokens': invalid,
                'max_output_tokens': 32768, 'max_observed_total_tokens': 200000}}
            with self.assertRaises(ValueError):
                public.validate(broken)

    def test_case_links_and_global_metric_consistency(self):
        rows = self.data['records'] + self.data['condition_records']
        self.assertEqual(len(rows), len({(r['experiment'], r['case']['id'], r['model'], r['condition'], r.get('attempt_id')) for r in rows}))
        active = records.active_condition_rows(self.data)
        self.assertTrue(active)
        self.assertEqual(set(r['model'] for r in active), set(self.data['models']))
        for row in rows:
            if row['grade']:
                self.assertLessEqual(row['grade']['passed'], row['grade']['total'])
                self.assertTrue(row['receipt'])

    def test_reproducible_html_only_without_raw_files_or_model_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'site'
            # Public rendering must not even ask the private record reader for data.
            with patch.object(records, 'read_bytes', side_effect=AssertionError('Private cache read')), \
                 patch.object(records, 'collect', side_effect=AssertionError('Private cache collection')):
                with patch.object(public.action_cache.Cache, 'lookup', return_value=None):
                    public.build(out)
                first = hashes(out)
                with patch.object(public.PublicReport, 'build', side_effect=AssertionError('Expected rendering cache hit')):
                    public.build(out)
                self.assertEqual(first, hashes(out))
            receipt = public.validate_site(out)
            self.assertGreater(receipt['pages'], 8)
            self.assertEqual(receipt['model_calls'], 0)
            self.assertFalse(any(Path(p).suffix in ('.json', '.jsonl', '.csv', '.zip', '.md', '.sse') for p in first))
            self.assertTrue((out / 'history/index.html').is_file())
            for internal in ('diagnostics.html', 'measurement.html', 'timings.html'):
                self.assertFalse((out / internal).exists())
            self.assertIn('data-radar-group="capability-overview"', (out / 'index.html').read_text())
            self.assertIn('data-chart-panel="matched"', (out / 'comparison.html').read_text())
            methods = (out / 'methodology.html').read_text()
            self.assertIn('How scores are calculated', methods)
            self.assertIn('Saved results and missing scores', methods)
            self.assertIn('Codex OAuth', methods)
            typed = (out / 'capabilities.html').read_text()
            self.assertIn('Current task results', typed)
            self.assertNotIn('Immutable pre-run registration', typed)
            self.assertNotIn('base_url', methods)
            pages = Path(tmp) / 'docs'
            public.stage_pages(out, pages)
            self.assertEqual(first, hashes(pages))
            public.stage_pages(out, pages)
            self.assertEqual(first, hashes(pages))
            manual = pages / 'manual.md'
            manual.write_text('Authored document: must not be overwritten')
            with self.assertRaises(ValueError):
                public.stage_pages(out, pages)
            self.assertTrue(manual.is_file())
            for name in ['raw.json', 'raw.jsonl', 'trace.sse', 'data.zip']:
                path = out / name
                path.write_text('private canary')
                with self.assertRaises(ValueError):
                    public.validate_site(out)
                path.unlink()
            (out / 'leak.html').symlink_to(out / 'index.html')
            with self.assertRaises(ValueError):
                public.validate_site(out)

    def test_network_and_process_guards_fail_closed(self):
        def attempt_render(report):
            for operation in [lambda: socket.create_connection(('api.example.invalid', 443)),
                              lambda: subprocess.Popen(['model-provider'])]:
                with self.assertRaises(RuntimeError):
                    operation()
            raise RuntimeError('intentional test interruption')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(public.PublicReport, 'build', attempt_render), \
                 patch.object(public.action_cache.Cache, 'lookup', return_value=None):
                with self.assertRaisesRegex(RuntimeError, 'intentional test interruption'):
                    public.build(Path(tmp) / 'site')
            self.assertFalse((Path(tmp) / 'site').exists())

    def test_bounded_readme_summary(self):
        import report_views
        original = copy.deepcopy(self.data)
        cells = report_views.aggregate_cells(self.data)
        graded = sum(cell['grade'] is not None for cell in cells)
        passed = sum(cell['success'] for cell in cells)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'README.md'
            path.write_text('Authored methods\n<!-- benchmark-results:start -->\nold\n<!-- benchmark-results:end -->\nAuthored related projects\n')
            public.refresh_readme(self.data, path)
            first = path.read_text()
            public.refresh_readme(self.data, path)
            self.assertEqual(first, path.read_text())
            self.assertTrue(first.startswith('Authored methods\n'))
            self.assertTrue(first.endswith('Authored related projects\n'))
            self.assertIn(f'**{graded:,}/{len(cells):,} model × task cells', first)
            self.assertIn(f'{passed:,} have full marks.', first)
            self.assertIn('| GPT-6.1 Sol | Codex |', first)
            self.assertIn('| GLM-5.3 | ZCode |', first)
            for category in ('Localization', 'Reconstruction', 'Types / ABI'):
                self.assertIn('| ' + category + ' |', first)
            self.assertIn('Ungraded states are not correctness zeros.', first)
            self.assertIn('not a matched-coverage ranking', first)
            self.assertIn('total unknown', first)
            self.assertEqual(self.data, original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
