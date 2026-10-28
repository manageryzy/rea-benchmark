"""Focused, network-forbidden controls for grading and holding completed budgets."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run
from request_contract import BudgetExhausted
from capabilities import coordinator, registration


class CompletedBudgetRunnerTests(unittest.TestCase):
    def batch(self, *, offline=False, cache_hit=False, other_model=False,
              scope='model', failure=None, skip_saved=False, timeout_scope='model'):
        with tempfile.TemporaryDirectory(prefix='rea-budget-runner-') as temporary:
            root = Path(temporary)
            bench = root / 'benchmarks'
            state = bench / 'config/execution.json'
            run.save(state, {'experiments_stopped': True, 'last_stop_at': 'fixture'})
            models = ['limited', 'other'] if other_model else ['limited']
            entries = [{'experiment': 'fixture', 'suite': 'fixture', 'case': case,
                        'model': model, 'condition': 'fixture',
                        'status': 'cached' if cache_hit else 'not_started'}
                       for model in models for case in ('first', 'second')]
            registry = SimpleNamespace(begin=lambda *a, **k: None,
                complete=lambda ref, selected, *a: (selected, None))
            calls = []
            finalizations = []

            def execute(entry, model, *args):
                calls.append((entry['model'], entry['case']))
                hold = entry['model'] == 'limited' and entry['case'] == 'first'
                if hold and failure:
                    raise failure('Offline fixture')
                return {'status': 'cache_hit' if cache_hit else 'inferred',
                        'provider_launches': 0, 'queue_stop_required': hold,
                        'queue_stop_reason': 'task_observed_token_limit_reached' if hold else None,
                        'budget_status': 'completed_over_budget' if hold else 'wire_enforced',
                        'budget_exceeded': hold}

            with patch.object(run, 'ROOT', root), patch.object(run, 'BENCH', bench), \
                    patch.object(run.records, 'ROOT', root), patch.object(run.records, 'INPUTS', {}), \
                    patch.object(run.records, 'models', return_value={m: {'id': m} for m in models}), \
                    patch.object(registration, 'Registry', return_value=registry), \
                    patch.object(coordinator, 'resolve_selected', side_effect=lambda x, **k: x), \
                    patch.object(coordinator, 'prepare_selected', side_effect=lambda x: x), \
                    patch.object(run, 'execute', side_effect=execute), \
                    patch.object(run, 'launch_model', side_effect=AssertionError('Provider forbidden')), \
                    patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
                    patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}), \
                    redirect_stdout(io.StringIO()):
                selection = {'execution': {'max_parallel_models': 2,
                    'budget_stop_scope': scope, 'skip_saved_failures': skip_saved, 'transport_timeout_scope': timeout_scope}}
                def launch_batch():
                    return run.run_batch(entries, selection, offline=offline,
                        finalize=lambda: finalizations.append(True))
                if offline or scope == 'case' and failure is None:
                    launch_batch()
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Benchmark incomplete'):
                        launch_batch()
            receipt = json.loads((bench / 'config/benchmark-status.json').read_text())
            self.assertFalse((root / '.cache/benchmark-runtime/live-batch.lock').exists())
            self.assertTrue(json.loads(state.read_text())['experiments_stopped'])
            receipt['_test_finalizations'] = len(finalizations)
            return calls, receipt

    def test_completed_answer_counts_once_and_holds_next_paid_case(self):
        calls, receipt = self.batch()
        self.assertEqual(calls, [('limited', 'first')])
        self.assertEqual(len(receipt['outcomes']), 1)
        self.assertEqual(receipt['failures'], [])
        self.assertEqual(receipt['unprocessed_tasks'], 1)
        self.assertEqual(len(receipt['queue_holds']), 1)
        self.assertTrue(receipt['outcomes'][0]['budget_exceeded'])

    def test_saved_budget_outcome_cannot_resume_the_model_queue(self):
        calls, receipt = self.batch(cache_hit=True)
        self.assertEqual(calls, [('limited', 'first')])
        self.assertEqual(receipt['cache_hits'], 1)
        self.assertEqual(len(receipt['queue_holds']), 1)

    def test_other_model_can_continue_within_its_own_limits(self):
        calls, receipt = self.batch(other_model=True)
        self.assertEqual(set(calls), {('limited', 'first'), ('other', 'first'), ('other', 'second')})
        self.assertEqual(receipt['unprocessed_tasks'], 1)
        self.assertEqual([row['model'] for row in receipt['queue_holds']], ['limited'])

    def test_offline_cache_inspection_can_finish_without_releasing_paid_queue(self):
        calls, receipt = self.batch(offline=True, cache_hit=True)
        self.assertEqual(calls, [('limited', 'first'), ('limited', 'second')])
        self.assertEqual(receipt['queue_holds'], [])
        self.assertEqual(receipt['model_api_calls'], 0)
        self.assertEqual(receipt['unprocessed_tasks'], 0)
        self.assertTrue(receipt['outcomes'][0]['queue_stop_required'])

    def test_case_scope_continues_after_completed_over_budget_answer(self):
        calls, receipt = self.batch(scope='case')
        self.assertEqual(calls, [('limited', 'first'), ('limited', 'second')])
        self.assertEqual(receipt['queue_holds'], [])
        self.assertEqual(receipt['unprocessed_tasks'], 0)
        self.assertTrue(receipt['outcomes'][0]['budget_exceeded'])
        self.assertEqual(receipt['_test_finalizations'], 1)

    def test_case_scope_preserves_cached_budget_flag_without_another_hold(self):
        calls, receipt = self.batch(scope='case', cache_hit=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(receipt['cache_hits'], 2)
        self.assertTrue(receipt['outcomes'][0]['queue_stop_required'])
        self.assertEqual(receipt['queue_holds'], [])

    def test_case_scope_continues_incomplete_budget_and_publishes_failure(self):
        calls, receipt = self.batch(scope='case', failure=BudgetExhausted)
        self.assertEqual(len(calls), 2)
        self.assertEqual(receipt['failures'][0]['classification'], 'task_budget_exhausted')
        self.assertEqual(receipt['unprocessed_tasks'], 0)
        self.assertEqual(receipt['_test_finalizations'], 1)

    def test_fresh_provider_failure_still_stops_model(self):
        calls, receipt = self.batch(scope='case', failure=RuntimeError, skip_saved=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt['unprocessed_tasks'], 1)
        self.assertEqual(receipt['_test_finalizations'], 1)

    def test_explicit_saved_failure_skip_preserves_failure_and_continues(self):
        calls, receipt = self.batch(scope='case', failure=run.PreservedAttempt, skip_saved=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(receipt['failures'][0]['classification'], 'preserved_terminal_attempt')
        self.assertEqual(receipt['_test_finalizations'], 1)

    def test_saved_failure_skip_defaults_to_disabled(self):
        calls, receipt = self.batch(scope='case', failure=run.PreservedAttempt)
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt['unprocessed_tasks'], 1)

    def test_stale_cache_is_not_reused_and_does_not_hide_later_cases(self):
        calls, receipt = self.batch(scope='case', failure=run.cache.StaleInference, skip_saved=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(receipt['cache_hits'], 0)
        self.assertEqual(receipt['failures'][0]['classification'], 'stale_inference_not_reused')
        self.assertEqual(receipt['unprocessed_tasks'], 0)

    def test_stale_cache_still_holds_without_explicit_skip(self):
        calls, receipt = self.batch(scope='case', failure=run.cache.StaleInference)
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt['unprocessed_tasks'], 1)

    def test_saved_budget_stop_never_prepares_or_launches_again(self):
        with tempfile.TemporaryDirectory(prefix='rea-preserved-budget-') as temporary:
            root = Path(temporary)
            (root / 'budget-exhausted.json').write_text('{}')
            entry = {'directory': '.', 'status': 'saved_inference; offline_grading_pending'}
            with patch.object(run, 'ROOT', root), patch.object(run, '_execution_allowed'), \
                    patch.object(run, 'prepare_task', side_effect=AssertionError('Preparation forbidden')), \
                    patch.object(run, 'launch_model', side_effect=AssertionError('Provider forbidden')):
                with self.assertRaises(BudgetExhausted):
                    run.execute(entry, {})
            self.assertEqual((root / 'budget-exhausted.json').read_text(), '{}')

    def test_archival_error_keeps_failure_but_refreshes_global_report(self):
        import sys
        archive = SimpleNamespace(main=lambda: (_ for _ in ()).throw(ValueError('Missing historical bytes')))
        audit = SimpleNamespace(main=lambda: self.fail('Audit cannot run after archive failure'))
        refreshed = []
        report = SimpleNamespace(build=lambda: refreshed.append(True))
        with patch.dict(sys.modules, {'archive_profiles': archive, 'audit_condition': audit, 'report': report}), \
                patch.object(run.records, 'read', return_value={'outcomes': [{'status': 'inferred'}]}):
            with self.assertRaisesRegex(ValueError, 'Missing historical bytes'):
                run.finalize()
        self.assertEqual(refreshed, [True])


class StreamTimeoutQueueTests(CompletedBudgetRunnerTests):
    def test_default_holds_timeout_queue(self):
        calls, receipt = self.batch(scope='case', failure=run.CaseTransportTimeout)
        self.assertEqual(calls, [('limited', 'first')])
        self.assertEqual(receipt['unprocessed_tasks'], 1)

    def test_case_scope_continues_next_case_without_retry(self):
        calls, receipt = self.batch(scope='case', timeout_scope='case', failure=run.CaseTransportTimeout)
        self.assertEqual(calls, [('limited', 'first'), ('limited', 'second')])
        self.assertEqual(receipt['failures'][0]['classification'], 'case_transport_timeout')
        self.assertEqual(receipt['unprocessed_tasks'], 0)
        self.assertEqual(receipt['_test_finalizations'], 1)

    def test_unknown_failure_still_holds(self):
        calls, receipt = self.batch(scope='case', timeout_scope='case', failure=RuntimeError)
        self.assertEqual(calls, [('limited', 'first')])
        self.assertEqual(receipt['unprocessed_tasks'], 1)

    def test_other_model_is_unaffected(self):
        calls, receipt = self.batch(scope='case', timeout_scope='case',
            failure=run.CaseTransportTimeout, other_model=True)
        self.assertEqual(set(calls), {('limited', 'first'), ('limited', 'second'),
                                     ('other', 'first'), ('other', 'second')})
        self.assertEqual(len(calls), 4)
        self.assertEqual(receipt['unprocessed_tasks'], 0)


class StreamTimeoutRecognitionTests(unittest.TestCase):
    def setUp(self):
        import request_contract
        self.contract_api = request_contract
        tmp = tempfile.TemporaryDirectory(prefix='rea-stream-timeout-')
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        provider = {'protocol': 'openai-chat-completions', 'provider': 'bigmodel-standard-api',
                    'model': 'glm-5.3', 'base_url': 'https://offline.invalid/v4'}
        self.model = {'id': 'glm-5.3', 'harness': 'zcode', 'reasoning': 'high'}
        gateway = {'max_tool_calls': 8}
        harness = {'max_output_tokens': 8192, 'timeout_seconds': 30, 'tool_timeout_ms': 10000,
                   'allowed_tool_names': ['mcp__rea__open_binary']}
        contract = request_contract.build(provider, self.model, gateway, harness)
        harness['request_contract'] = contract
        self.task = {'id': 'timeout-control', 'model': 'glm-5.3', 'reasoning': 'high',
            'request_contract': contract, 'runtime_attestation': {'provider': provider,
            'configuration': {'harness': harness, 'gateway': gateway}}}
        self.budget = {'contract': contract, 'contract_sha256': request_contract.digest(contract),
            'status': 'rejected', 'transport_complete': False, 'provider_forwards': 2,
            'rejected_requests': 1, 'requests': [
                {'forwarded': True, 'http_status': 200, 'response_complete': True},
                {'forwarded': True, 'http_status': 200},
                {'forwarded': False, 'rejected': 'concurrent_provider_requests_denied'}]}
        self.result = {'model': 'glm-5.3', 'case_id': 'timeout-control', 'exit_code': 1,
            'timed_out': False, 'upstream_error': None, 'budget_exceeded': False,
            'usage_complete': False, 'request_budget': self.budget}
        self.failure = {'classification': 'harness_or_provider_failure',
                        'model_correctness_grade_allowed': False}
        self.events = [
            {'type': 'streamRecovery.updated', 'payload': {'failureKind': 'provider_timeout', 'retryNumber': 1}},
            {'type': 'turn.failed', 'payload': {'error': {'underlyingErrorMessage': 'concurrent_provider_requests_denied'}}}]
        patcher = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden'))
        patcher.start(); self.addCleanup(patcher.stop)

    def write_fixture(self):
        for name, value in [('result.json', self.result), ('request-budget.json', self.budget),
                            ('harness-failure.json', self.failure)]:
            (self.directory / name).write_text(json.dumps(value))
        (self.directory / 'zcode-events.jsonl').write_text('\n'.join(json.dumps(e) for e in self.events) + '\n')

    def recognize(self):
        self.write_fixture()
        return run.preserved_stream_timeout(self.directory, self.task, self.model)

    def test_proven_timeout_is_recognized(self):
        self.assertTrue(self.recognize())

    def test_quota_or_auth_is_not_timeout(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.budget['requests'][-2]['http_status'] = status
                self.assertFalse(self.recognize())

    def test_unknown_recovery_reason_is_not_timeout(self):
        self.events[0]['payload']['failureKind'] = 'unknown'
        self.assertFalse(self.recognize())

    def test_concurrent_request_without_timeout_is_not_allowed(self):
        self.events = self.events[1:]
        self.assertFalse(self.recognize())

    def test_other_policy_violation_is_not_allowed(self):
        self.budget['requests'][-1]['rejected'] = 'invalid_model'
        self.assertFalse(self.recognize())

    def test_unknown_upstream_error_is_not_allowed(self):
        self.result['upstream_error'] = {'exception_type': 'OSError'}
        self.assertFalse(self.recognize())

    def test_changed_contract_is_rejected(self):
        self.budget['contract_sha256'] = '0' * 64
        self.assertFalse(self.recognize())

    def test_malformed_event_is_rejected(self):
        self.write_fixture()
        with (self.directory / 'zcode-events.jsonl').open('a') as events:
            events.write('{broken')
        self.assertFalse(run.preserved_stream_timeout(self.directory, self.task, self.model))

    def test_completed_stream_is_not_misclassified(self):
        self.budget['requests'][-2]['response_complete'] = True
        self.assertFalse(self.recognize())

    def test_execute_records_timeout_without_grading_or_retry(self):
        task = {**self.task, 'directory': '.', 'track': 'basic', 'fingerprint': 'offline-timeout',
                'inference_identity': {}}
        entry = {'directory': '.', 'status': 'not_started', 'experiment': 'fixture',
                 'case': task['id'], 'model': self.model['id']}
        def failed_launch(*args):
            self.write_fixture()
            raise RuntimeError('ZCode/provider failed')
        with patch.object(run, 'ROOT', self.directory), patch.object(run, '_execution_allowed'), \
                patch.object(run, 'inspect_native_entry', side_effect=lambda value: value), \
                patch.object(run, 'prepare_task', return_value=task), \
                patch.object(run.cache, 'inference_files', return_value={}), \
                patch.object(run.action_cache, 'inference', return_value=object()), \
                patch.object(run.action_cache, 'Cache', return_value=SimpleNamespace(lookup=lambda action: None)), \
                patch.object(run, 'launch_model', side_effect=failed_launch) as launched, \
                patch.object(run, 'grade_saved', side_effect=AssertionError('Failure must not be graded')):
            with self.assertRaises(run.CaseTransportTimeout):
                run.execute(entry, self.model)
        launched.assert_called_once()
        self.assertTrue((self.directory / 'harness-failure.json').exists())



if __name__ == '__main__':
    unittest.main(verbosity=2)
