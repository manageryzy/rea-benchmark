"""Offline scheduler controls: independent cases, bounded model slots, no API."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import benchmark_config
import parallel_cells
import run
import startup_policy
from capabilities import coordinator, registration
from request_contract import BudgetExhausted


class ModelConcurrencyTests(unittest.TestCase):
    def batch(self, callback, *, models=('a', 'b'), cases=4, slots=2, model_limit=2, scope='case'):
        with tempfile.TemporaryDirectory(prefix='rea-model-slots-') as tmp, ExitStack() as stack:
            root = Path(tmp); bench = root / 'benchmarks'
            run.save(bench / 'config/execution.json', {'experiments_stopped': True, 'last_stop_at': 'fixture'})
            selected = [{'experiment': 'control', 'suite': 'record-parser', 'case': str(i),
                         'model': mid, 'condition': 'fixture', 'status': 'unchecked'}
                        for mid in models for i in range(cases)]
            selection = {'execution': {'startup_mode': 'trusted_local', 'max_parallel_models': model_limit,
                         'budget_stop_scope': scope}}
            if slots is not None:
                selection['execution']['max_parallel_cases_per_model'] = slots
            active = {mid: 0 for mid in models}; peaks = dict(active)
            seen, contexts = [], []
            total_peak = distinct_peak = 0
            lock = threading.Lock()
            blocked = threading.Event()
            def execute(entry, model, *_args):
                nonlocal total_peak, distinct_peak
                mid = entry['model']
                with lock:
                    active[mid] += 1; peaks[mid] = max(peaks[mid], active[mid])
                    total_peak = max(total_peak, sum(active.values()))
                    distinct_peak = max(distinct_peak, sum(n > 0 for n in active.values()))
                    seen.append((mid, entry['case']))
                    contexts.append((mid, entry['case'], list(entry['_budget_completed'])))
                try:
                    result = callback(entry, blocked)
                    return {'status': 'offline_control', 'provider_launches': 0,
                            'directory': mid + '/' + entry['case'], **(result or {})}
                finally:
                    with lock: active[mid] -= 1
            def pool(**options):
                return ThreadPoolExecutor(max_workers=options['max_workers'],
                    initializer=lambda: startup_policy._MODE.set('trusted_local'))
            def output(*args, **kwargs):
                if args and args[0] in ('MODEL QUEUE STOPPED:', 'MODEL QUEUE HELD:'):
                    blocked.set()
            for target, name, options in [
                (run, 'ROOT', {'new': root}), (run, 'BENCH', {'new': bench}),
                (run, '_execution_state', {'side_effect': lambda: json.loads((bench/'config/execution.json').read_text())}),
                (run.records, 'models', {'return_value': {m: {'id': m} for m in models}}),
                (registration.Registry, 'begin', {'return_value': None}),
                (coordinator, 'inspect_selected', {'side_effect': AssertionError('No bulk preparation')}),
                (run, 'ProcessPoolExecutor', {'side_effect': pool}),
                (parallel_cells, 'execute', {'side_effect': execute}),
                (run, 'launch_model', {'side_effect': AssertionError('Provider forbidden')}),
                (socket.socket, 'connect', {'side_effect': AssertionError('Network forbidden')}),
            ]: stack.enter_context(patch.object(target, name, **options))
            stack.enter_context(patch('builtins.print', side_effect=output))
            with redirect_stdout(io.StringIO()):
                try: run.run_batch(selected, selection)
                except RuntimeError as error:
                    self.assertIn('Benchmark incomplete', str(error))
            receipt = json.loads((bench / 'config/benchmark-status.json').read_text())
            self.assertEqual(receipt['provider_launches'], 0)
            self.assertEqual(len(seen), len(set(seen)), 'Each case may run only once')
            self.assertFalse(any(active.values()))
            self.assertFalse((root / '.cache/benchmark-runtime/live-batch.lock').exists())
            return receipt, seen, peaks, total_peak, distinct_peak, contexts

    def test_two_cases_per_model_overlap_and_refill(self):
        barrier = threading.Barrier(4, timeout=5)
        def callback(entry, _):
            if int(entry['case']) < 2: barrier.wait()
        result, seen, peaks, total, distinct, contexts = self.batch(callback)
        self.assertEqual((len(seen), peaks, total, distinct), (8, {'a': 2, 'b': 2}, 4, 2))
        self.assertEqual(result['unprocessed_tasks'], 0)
        for mid, case, settled in contexts:
            if int(case) < 2: self.assertEqual(settled, [])
            else: self.assertTrue(settled)
            self.assertTrue(all(path.startswith(mid + '/') and path != mid + '/' + case for path in settled))

    def test_default_keeps_one_case_per_model(self):
        barrier = threading.Barrier(2, timeout=5)
        def callback(entry, _):
            if entry['case'] == '0': barrier.wait()
        result = self.batch(callback, slots=None)
        self.assertEqual(result[2:5], ({'a': 1, 'b': 1}, 2, 2))

    def test_distinct_model_limit_is_independent_of_case_slots(self):
        barrier = threading.Barrier(2, timeout=5)
        def callback(entry, _): barrier.wait()
        result = self.batch(callback, models=('a', 'b', 'c'), model_limit=1)
        self.assertEqual(result[2:5], ({'a': 2, 'b': 2, 'c': 2}, 2, 1))

    def test_provider_failure_does_not_restart_model_from_other_inflight_case(self):
        barrier = threading.Barrier(4, timeout=5)
        def callback(entry, blocked):
            if int(entry['case']) < 2: barrier.wait()
            if entry['model'] == 'a':
                if entry['case'] == '0': raise RuntimeError('Offline API error control')
                self.assertTrue(blocked.wait(5))
        result, seen, *_ = self.batch(callback)
        self.assertEqual({case for mid, case in seen if mid == 'a'}, {'0', '1'})
        self.assertEqual({case for mid, case in seen if mid == 'b'}, {'0', '1', '2', '3'})
        self.assertEqual((len(result['failures']), result['unprocessed_tasks']), (1, 2))

    def test_model_budget_hold_survives_other_inflight_completion(self):
        barrier = threading.Barrier(4, timeout=5)
        def callback(entry, blocked):
            if int(entry['case']) < 2: barrier.wait()
            if entry['model'] == 'a':
                if entry['case'] == '0': return {'queue_stop_required': True}
                self.assertTrue(blocked.wait(5))
        result, seen, *_ = self.batch(callback, scope='model')
        self.assertEqual(len(seen), 6)
        self.assertEqual((len(result['queue_holds']), result['unprocessed_tasks']), (1, 2))

    def test_case_budget_stop_continues_without_retry(self):
        barrier = threading.Barrier(4, timeout=5)
        def callback(entry, _):
            if int(entry['case']) < 2: barrier.wait()
            if entry['model'] == 'a' and entry['case'] == '0': raise BudgetExhausted('Offline budget control')
        result, seen, *_ = self.batch(callback)
        self.assertEqual((len(seen), len(result['failures']), result['unprocessed_tasks']), (8, 1, 0))
        self.assertEqual(result['failures'][0]['classification'], 'task_budget_exhausted')

    def test_stop_prevents_refilling_slots(self):
        barrier = threading.Barrier(4, timeout=5)
        stopped = threading.Event()
        def callback(entry, _):
            barrier.wait()
            if entry['model'] == 'a' and entry['case'] == '0':
                run.set_execution_stopped(True); stopped.set()
            else: self.assertTrue(stopped.wait(5))
            # Dispatch observes this stop synchronously before refilling slots.
        result, seen, *_ = self.batch(callback)
        self.assertEqual(len(seen), 4)
        self.assertTrue(result.get('interrupted'))

    def test_config_validates_slots_and_keeps_default(self):
        from capabilities import tasks
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(benchmark_config.records, 'registry', return_value={'experiments': []}), \
             patch.object(benchmark_config.records, 'models', return_value={}), \
             patch.object(benchmark_config.records, 'condition_catalog', return_value={'conditions': []}), \
             patch.object(tasks, 'declared', return_value=[]):
            path = Path(tmp) / 'config.json'
            path.write_text('{"schema_version": 1}')
            self.assertEqual(benchmark_config.load(path)['execution']['max_parallel_cases_per_model'], 1)
            for value in (1, 2, 0, 3, True, '2', None):
                path.write_text(json.dumps({'schema_version': 1, 'execution': {'max_parallel_cases_per_model': value}}))
                with self.subTest(value=value):
                    if type(value) is int and value in (1, 2):
                        self.assertEqual(benchmark_config.load(path)['execution']['max_parallel_cases_per_model'], value)
                    else:
                        with self.assertRaisesRegex(ValueError, 'max_parallel_cases_per_model'):
                            benchmark_config.load(path)


if __name__ == '__main__':
    unittest.main()
