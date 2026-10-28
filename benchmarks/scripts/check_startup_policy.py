"""Focused offline controls for trusted startup, streaming queues and image reuse."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

import startup_policy
import runtime_snapshot
import runtime_attestation
import run
from capabilities import registration, coordinator
from grading import runtime


class StartupControls(unittest.TestCase):
    def test_startup_epoch_waits_for_other_model_admission(self):
        from read_session import ReadSession
        session = ReadSession(targets={})
        held, release = threading.Event(), threading.Event()
        def admission():
            with session.lock:
                held.set()
                release.wait(3)
        def prepare():
            with session.startup_scope():
                return True
        try:
            with ThreadPoolExecutor(2) as workers:
                active = workers.submit(admission)
                self.assertTrue(held.wait(2))
                pending = workers.submit(prepare)
                self.assertFalse(pending.done())
                release.set()
                active.result()
                self.assertTrue(pending.result())
        finally:
            release.set()
            session.executor.shutdown()

    def test_strict_gate_and_context_restoration(self):
        function = Mock(side_effect=ValueError('stale real receipt'))
        gate = startup_policy.offline_gate(function)
        with self.assertRaises(ValueError):
            gate()
        with self.assertRaisesRegex(RuntimeError, 'test'):
            with startup_policy.scope('trusted_local'):
                self.assertIsNone(gate())
                raise RuntimeError('test')
        with self.assertRaises(ValueError):
            gate()
        self.assertEqual(function.call_count, 2)

    def test_mode_crosses_explicit_thread_context_only(self):
        with startup_policy.scope('trusted_local'), ThreadPoolExecutor(1) as workers:
            self.assertTrue(workers.submit(copy_context().run, startup_policy.trusted).result())
            self.assertFalse(workers.submit(startup_policy.trusted).result())

    def test_image_identity_and_adapter_change(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runtime_attestation, 'CONTROL', ['adapter.py']):
            root = Path(tmp)
            (root/'adapter.py').write_bytes(b'one')
            (root/'benchmarks/scripts').mkdir(parents=True)
            (root/'benchmarks/scripts/startup_policy.py').write_bytes(b'policy fixture')
            policy = {'components': {}, 'runtime_environment': {}, 'network': {}}
            with patch.dict('os.environ', {'REA_PINNED_IMAGE_ID': 'a'*64, 'REA_PINNED_IMAGE_MANIFEST': 'b'*64}):
                first = startup_policy.pinned_runtime('test', root, policy)
                self.assertEqual(first, startup_policy.pinned_runtime('test', root, policy))
                (root/'adapter.py').write_bytes(b'two')
                self.assertNotEqual(first, startup_policy.pinned_runtime('test', root, policy))
            with patch.dict('os.environ', {'REA_PINNED_IMAGE_ID': '', 'REA_PINNED_IMAGE_MANIFEST': ''}):
                with self.assertRaises(ValueError):
                    startup_policy.pinned_runtime('test', root, policy)

    def test_snapshot_manifest_change_invalidates(self):
        with startup_policy.scope('trusted_local'), patch.object(runtime_snapshot, '_RUNTIMES', {}), \
             patch.object(runtime_snapshot, 'populate', side_effect=[{'identity': 'one'}, {'identity': 'two'}]) as populate, \
             patch.dict('os.environ', {'REA_PINNED_IMAGE_ID': 'a'*64, 'REA_PINNED_IMAGE_MANIFEST': 'b'*64}):
            first = runtime_snapshot.current('test', root='/test', policy={}, capture=None)
            self.assertEqual(first, runtime_snapshot.current('test', root='/test', policy={}, capture=None))
            with patch.dict('os.environ', {'REA_PINNED_IMAGE_MANIFEST': 'c'*64}):
                self.assertNotEqual(first, runtime_snapshot.current('test', root='/test', policy={}, capture=None))
            self.assertEqual(populate.call_count, 2)

    def test_prepared_image_reuses_extraction_and_rejects_replacement(self):
        def extract(record, root):
            root.mkdir(parents=True)
            (root/'compiler').write_bytes(b'fixture')
        with tempfile.TemporaryDirectory() as tmp, patch.object(runtime, 'extract', side_effect=extract) as build:
            record = {'id': 'a'*64, 'archive_sha256': 'b'*64}
            root = runtime.prepared_image(record, cache=tmp)
            self.assertEqual(root, runtime.prepared_image(record, cache=tmp))
            self.assertEqual(build.call_count, 1)
            root.rename(root.with_name('old-root'))
            root.mkdir()
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                runtime.prepared_image(record, cache=tmp)

    def test_cell_binding_keeps_full_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = registration.Registry(tmp)
            schedule = {'binding_mode': 'per_cell', 'implementation_sha256': {},
                        'cells': [{'cell_id': 'first'}, {'cell_id': 'later'}]}
            schedule_id = registry.publish(schedule)
            ready = {'record_kind': 'cell_execution_binding', 'registration_id': schedule_id,
                     'implementation_sha256': {}, 'cells': [{'cell_id': 'first', 'prospective': True}]}
            binding_id = registry.publish(ready, registration_id=schedule_id)
            selected, _, bound = registry.binding({'registration_id': schedule_id,
                'binding_id': binding_id, 'cell_id': 'first'})
            self.assertEqual(len(selected['cells']), 2)
            self.assertEqual(bound['cell_id'], 'first')
            ready['cells'][0]['cell_id'] = 'not-scheduled'
            invalid = registry.publish(ready, registration_id=schedule_id)
            with self.assertRaises(ValueError):
                registry.binding({'registration_id': schedule_id, 'binding_id': invalid, 'cell_id': 'not-scheduled'})

    def test_parallel_models_without_catalogue_barrier_and_budget_continues(self):
        from request_contract import BudgetExhausted
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bench = root/'benchmarks'; (bench/'config').mkdir(parents=True)
            (bench/'config/execution.json').write_text(json.dumps({'experiments_stopped': True, 'last_stop_at': 'initial'}))
            selected = [{'experiment': 'control', 'suite': 'record-parser', 'case': f'case-{i}',
                         'model': model, 'condition': 'test', 'status': 'unchecked'}
                        for model in ('a', 'b') for i in range(2)]
            selection = {'execution': {'startup_mode': 'trusted_local', 'max_parallel_models': 2,
                                      'budget_stop_scope': 'case', 'skip_saved_failures': False}}
            first_pair = threading.Barrier(2, timeout=5)
            seen = []
            active, peaks = {}, {}
            active_lock = threading.Lock()
            global_peak = 0
            def execute(entry, model, *args):
                nonlocal global_peak
                self.assertTrue(startup_policy.trusted())
                mid = model['id']
                with active_lock:
                    active[mid] = active.get(mid, 0) + 1
                    peaks[mid] = max(peaks.get(mid, 0), active[mid])
                    global_peak = max(global_peak, sum(active.values()))
                    seen.append((mid, entry['case']))
                try:
                    if entry['case'] == 'case-0':
                        first_pair.wait()
                    if mid == 'a' and entry['case'] == 'case-0':
                        raise BudgetExhausted('local budget control; no API')
                    return {'status': 'local_control', 'provider_launches': 0}
                finally:
                    with active_lock:
                        active[mid] -= 1
            import parallel_cells
            def pool(**options):
                return ThreadPoolExecutor(max_workers=options['max_workers'],
                    initializer=lambda: startup_policy._MODE.set('trusted_local'))
            with patch.object(run, 'ROOT', root), patch.object(run, 'BENCH', bench), \
                 patch.object(run, '_execution_state', side_effect=lambda: json.loads((bench/'config/execution.json').read_text())), \
                 patch.object(run.records, 'models', return_value={k:{'id': k} for k in ('a','b')}), \
                 patch.object(registration.Registry, 'begin', return_value=None), \
                 patch.object(coordinator, 'inspect_selected', side_effect=AssertionError('bulk preparation')), \
                 patch.object(run, 'ProcessPoolExecutor', side_effect=pool), \
                 patch.object(parallel_cells, 'execute', side_effect=execute):
                with self.assertRaisesRegex(RuntimeError, 'Benchmark incomplete'):
                    run.run_batch(selected, selection)
                result = json.loads((bench/'config/benchmark-status.json').read_text())
            self.assertEqual(len(seen), 4)
            self.assertEqual(peaks, {'a': 1, 'b': 1})
            self.assertEqual(global_peak, 2)
            self.assertEqual(active, {'a': 0, 'b': 0})
            for mid in ('a', 'b'):
                self.assertEqual([case for model, case in seen if model == mid], ['case-0', 'case-1'])
            self.assertEqual(result['selected_tasks'], 4)
            self.assertEqual(result['unprocessed_tasks'], 0)
            self.assertEqual(result['provider_launches'], 0)
            self.assertEqual(len(result['failures']), 1)
            self.assertFalse((root/'.cache/benchmark-runtime/live-batch.lock').exists())


if __name__ == '__main__':
    unittest.main()
