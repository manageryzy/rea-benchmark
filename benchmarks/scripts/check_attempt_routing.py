#!/usr/bin/python3
"""Offline action-addressed storage controls; no target execution or provider."""
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import action_cache
import cache
from capabilities import attempts


class AttemptRoutingChecks:
    def routing_fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix='attempt-routing-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        trial = 'benchmarks/runs/capabilities-v1/model/case/' + 'a' * 64 + '/' + 'b' * 64
        entry = {'directory': trial, 'inference_trial': trial}
        return root, entry

    def write_attempt(self, root, entry, key, *, legacy=False, complete=False):
        relative = entry['inference_trial'] if legacy else str(Path(entry['inference_trial']).parent / key)
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        task = {'directory': relative, 'fingerprint': key}
        if not legacy:
            task['inference_trial'] = entry['inference_trial']
        (directory / 'task.json').write_bytes(action_cache.canonical(task))
        if complete:
            (directory / 'result.json').write_text('{}')
            (directory / 'completed.json').write_text('{}')
        return relative

    def test_84_new_action_uses_hash_sibling_without_hashing_physical_path(self):
        root, entry = self.routing_fixture()
        action = action_cache.Action('inference', {'configuration/effective.json':
            action_cache.canonical({'trial': cache.inference_trial(entry)})})
        physical = attempts.route(entry, action.key, root=root)
        task = {**entry, 'directory': physical}
        repeated = action_cache.Action('inference', {'configuration/effective.json':
            action_cache.canonical({'trial': cache.inference_trial(task)})})
        self.assertEqual(action.key, repeated.key)
        self.assertEqual(Path(physical).name, action.key)
        self.assertNotEqual(physical, entry['inference_trial'])
        self.assertFalse(any(root.iterdir()))

    def test_85_matching_legacy_completion_keeps_original_directory_and_bytes(self):
        root, entry = self.routing_fixture()
        key = 'c' * 64
        relative = self.write_attempt(root, entry, key, legacy=True, complete=True)
        before = {str(p): p.read_bytes() for p in (root / relative).iterdir()}
        self.assertEqual(attempts.route(entry, key, root=root), relative)
        self.assertEqual(attempts.status(root / relative), 'cached')
        self.assertEqual(before, {str(p): p.read_bytes() for p in (root / relative).iterdir()})

    def test_86_changed_action_preserves_old_stopped_attempt_and_gets_new_directory(self):
        root, entry = self.routing_fixture()
        old = self.write_attempt(root, entry, 'c' * 64, legacy=True)
        before = (root / old / 'task.json').read_bytes()
        relative = attempts.route(entry, 'd' * 64, root=root)
        self.assertNotEqual(relative, old)
        self.assertEqual(attempts.status(root / relative), 'not_started')
        self.assertEqual(before, (root / old / 'task.json').read_bytes())
        self.assertEqual(attempts.route(entry, 'c' * 64, root=root), old)
        self.assertTrue(attempts.status(root / old).startswith('interrupted'))

    def test_87_same_addressed_action_is_stopped_or_cached_without_new_attempt(self):
        for complete in (False, True):
            root, entry = self.routing_fixture()
            relative = self.write_attempt(root, entry, 'd' * 64, complete=complete)
            self.assertEqual(relative, attempts.route(entry, 'd' * 64, root=root))
            self.assertEqual(attempts.status(root / relative), 'cached' if complete
                             else 'interrupted; duplicate_inference_refused')
            self.assertEqual(len(list((root / entry['inference_trial']).parent.iterdir())), 1)

    def test_88_corrupt_or_unidentified_storage_fails_closed(self):
        root, entry = self.routing_fixture()
        relative = self.write_attempt(root, entry, 'd' * 64)
        path = root / relative / 'task.json'
        path.write_text(json.dumps({'fingerprint': 'e' * 64, 'directory': relative}))
        with self.assertRaises(ValueError):
            attempts.route(entry, 'd' * 64, root=root)
        legacy = root / entry['inference_trial']
        legacy.mkdir(); (legacy / 'interrupted.json').write_text('{}')
        with self.assertRaises(ValueError):
            attempts.route(entry, 'f' * 64, root=root)

    def test_89_physical_storage_and_tmp_relocation_do_not_change_logical_trial(self):
        root, entry = self.routing_fixture()
        other = tempfile.TemporaryDirectory(prefix='attempt-relocation-')
        self.addCleanup(other.cleanup)
        key = 'd' * 64
        first = attempts.route(entry, key, root=root)
        second = attempts.route(entry, key, root=other.name)
        self.assertEqual(first, second)
        self.write_attempt(root, entry, key)
        self.assertEqual(attempts.route(entry, key, root=root), first)
        self.assertEqual(cache.inference_trial({'directory': first,
            'inference_trial': entry['inference_trial']}), entry['inference_trial'])

    def test_90_legacy_trial_encoding_and_manual_offline_callers_are_unchanged(self):
        root, entry = self.routing_fixture()
        old = {'directory': entry['directory']}
        self.assertEqual(cache.inference_trial(old), entry['inference_trial'])
        self.assertEqual(attempts.route(old, 'c' * 64, root=root), old['directory'])
        for name in ('/tmp/escape', '../escape', 'runs/../escape'):
            with self.assertRaises(ValueError):
                attempts.route({**entry, 'inference_trial': name}, 'c' * 64, root=root)

    def test_91_resolver_uses_current_action_before_reusing_old_status(self):
        from capabilities import coordinator
        root, entry = self.routing_fixture()
        entry.update(suite='capabilities-v1', preparation_status='ready', model='fixture',
                     status='interrupted; duplicate_inference_refused', capability_case={'kind': 'abi_probe'})
        task = {'directory': str(Path(entry['inference_trial']).parent / ('d' * 64)), 'fingerprint': 'd' * 64}
        def prepare(row, model, **kwargs):
            self.assertFalse(kwargs['materialize'])
            return task
        with patch.object(coordinator, 'ROOT', root), \
             patch.object(coordinator.records, 'models', return_value={'fixture': {'id': 'fixture'}}), \
             patch.object(coordinator, 'require_case_validation') as gates, \
             patch.object(coordinator, 'prepare', side_effect=prepare):
            resolved = coordinator.resolve_selected([entry])
            self.assertEqual(gates.call_count, 1)
            self.assertEqual(resolved[0]['status'], 'not_started')
            self.assertEqual(resolved[0]['resolved_fingerprint'], task['fingerprint'])
            self.assertEqual(entry['status'], 'interrupted; duplicate_inference_refused')
            with patch.object(coordinator, 'prepare', side_effect=AssertionError('Repeated resolution')):
                self.assertEqual(coordinator.resolve_selected(resolved), resolved)


    @contextmanager
    def stop_state_fixture(self):
        """Keep real state writes and batch cleanup confined to a temporary root."""
        import records
        import run
        temporary = tempfile.TemporaryDirectory(prefix='attempt-stop-control-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        configuration = root / 'benchmarks/config'
        configuration.mkdir(parents=True)
        (configuration / 'execution.json').write_text('{"experiments_stopped":true}')
        (configuration / 'models.json').write_text('{"models":[{"id":"fixture"}]}')
        entry = {'model': 'fixture', 'case': 'case', 'experiment': 'control', 'suite': 'control',
                 'condition': 'control', 'status': 'not_started', 'preparation_status': 'ready',
                 'directory': 'runs/fixture'}
        selection = {'execution': {'max_parallel_models': 1}}
        with ExitStack() as stack:
            for replacement in (patch.object(run, 'ROOT', root),
                                patch.object(run, 'BENCH', root / 'benchmarks'),
                                patch.object(records, 'ROOT', root),
                                patch.object(records, 'BENCH', root / 'benchmarks')):
                stack.enter_context(replacement)
            stack.enter_context(patch.dict('os.environ', {'REA_BENCHMARK_OFFLINE': '0'}))
            yield root, entry, selection

    def stop_at_batch_phase(self, phase):
        import run
        from capabilities import coordinator, registration
        seen, resolutions = [], 0
        with self.stop_state_fixture() as (root, entry, selection):
            def visit(name):
                seen.append(name)
                if name == phase:
                    run.set_execution_stopped(True)
            def resolve(rows, **kwargs):
                nonlocal resolutions
                resolutions += 1
                visit('resolve' + str(resolutions))
                return rows
            def begin(rows, chosen, **kwargs):
                visit('begin')
                return None
            def prepare(rows):
                visit('prepare')
                return rows
            def complete(reference, rows, chosen, prepare_task):
                visit('bind')
                return rows, None
            registry = SimpleNamespace(begin=begin, complete=complete)
            with patch.object(coordinator, 'resolve_selected', side_effect=resolve), \
                 patch.object(coordinator, 'prepare_selected', side_effect=prepare), \
                 patch.object(registration, 'Registry', return_value=registry), \
                 patch.object(run, 'execute', side_effect=AssertionError('Dispatch after stop')) as dispatch:
                with self.assertRaises(InterruptedError):
                    run.run_batch([entry], selection)
                dispatch.assert_not_called()
            expected = ['resolve1', 'begin', 'prepare', 'resolve2', 'bind']
            self.assertEqual(seen, expected[:expected.index(phase) + 1])
            state = json.loads((root / 'benchmarks/config/execution.json').read_bytes())
            self.assertTrue(state['experiments_stopped'])
            self.assertTrue(state.get('last_stop_at'))
            self.assertFalse((root / '.cache/benchmark-runtime/live-batch.lock').exists())
            receipt = json.loads((root / 'benchmarks/config/benchmark-status.json').read_bytes())
            self.assertEqual(receipt['provider_launches'], 0)
            self.assertEqual(receipt['model_api_calls'], 0)

    def test_93_stop_during_initial_resolution_is_not_cleared_by_resume(self):
        self.stop_at_batch_phase('resolve1')

    def test_94_preparation_stop_is_checked_before_second_resolution(self):
        self.stop_at_batch_phase('prepare')

    def test_95_stop_during_second_resolution_prevents_binding(self):
        self.stop_at_batch_phase('resolve2')

    def test_96_stop_during_binding_prevents_dispatch(self):
        self.stop_at_batch_phase('bind')

    def test_97_synchronous_execute_rejects_old_epoch_even_after_quick_resume(self):
        import run
        with self.stop_state_fixture() as (root, entry, selection):
            cancel = threading.Event()
            cancel.stop_epoch = None
            run.set_execution_stopped(True)
            state = json.loads((root / 'benchmarks/config/execution.json').read_bytes())
            run.set_execution_stopped(False, expected_stop_at=state['last_stop_at'])
            self.assertFalse(json.loads((root / 'benchmarks/config/execution.json').read_bytes())['experiments_stopped'])
            self.assertFalse(cancel.is_set())
            with patch.object(run, 'prepare_task', side_effect=AssertionError('Preparation after stop')) as prepare, \
                 patch.object(run, 'launch_model', side_effect=AssertionError('Model after stop')) as dispatch:
                with self.assertRaises(InterruptedError):
                    run.execute(entry, {'id': 'fixture'}, cancel_event=cancel)
                prepare.assert_not_called()
                dispatch.assert_not_called()

    def test_98_expected_none_is_a_real_epoch_not_an_unchecked_resume(self):
        import run
        with self.stop_state_fixture() as (root, entry, selection):
            run.set_execution_stopped(False, expected_stop_at=None)
            self.assertFalse(json.loads((root / 'benchmarks/config/execution.json').read_bytes())['experiments_stopped'])
            run.set_execution_stopped(True)
            path = root / 'benchmarks/config/execution.json'
            stopped = path.read_bytes()
            with self.assertRaises(InterruptedError):
                run.set_execution_stopped(False, expected_stop_at=None)
            self.assertEqual(path.read_bytes(), stopped)

    def test_99_official_stop_and_resume_serialize_before_epoch_comparison(self):
        import fcntl
        import run
        with self.stop_state_fixture() as (root, entry, selection):
            acquired, release = threading.Event(), threading.Event()
            resume_waiting, resume_done = threading.Event(), threading.Event()
            errors = {'stop': [], 'resume': []}
            original_flock = fcntl.flock
            def coordinated_flock(descriptor, operation):
                name = threading.current_thread().name
                if name == 'routing-resume-control' and operation & fcntl.LOCK_EX:
                    resume_waiting.set()
                result = original_flock(descriptor, operation)
                if name == 'routing-stop-control' and operation & fcntl.LOCK_EX:
                    acquired.set()
                    if not release.wait(5):
                        raise AssertionError('Stop lock control timed out')
                return result
            def stop():
                try:
                    run.set_execution_stopped(True)
                except BaseException as error:
                    errors['stop'].append(error)
            def resume():
                try:
                    run.set_execution_stopped(False, expected_stop_at=None)
                except BaseException as error:
                    errors['resume'].append(error)
                finally:
                    resume_done.set()
            stopper = threading.Thread(target=stop, name='routing-stop-control', daemon=True)
            resumer = threading.Thread(target=resume, name='routing-resume-control', daemon=True)
            with patch.object(fcntl, 'flock', side_effect=coordinated_flock):
                try:
                    stopper.start()
                    self.assertTrue(acquired.wait(3), 'Official stop did not acquire the state lock')
                    resumer.start()
                    self.assertTrue(resume_waiting.wait(3), 'Official resume did not use the state lock')
                    self.assertFalse(resume_done.is_set(), 'Resume bypassed the active stop lock')
                finally:
                    release.set()
                    for worker in (stopper, resumer):
                        if worker.ident is not None:
                            worker.join(5)
            self.assertFalse(stopper.is_alive())
            self.assertFalse(resumer.is_alive())
            self.assertEqual(errors['stop'], [])
            self.assertEqual(len(errors['resume']), 1)
            self.assertIsInstance(errors['resume'][0], InterruptedError)
            state = json.loads((root / 'benchmarks/config/execution.json').read_bytes())
            self.assertTrue(state['experiments_stopped'])
            self.assertTrue(state.get('last_stop_at'))


    @contextmanager
    def batch_stage_fixture(self):
        from capabilities import coordinator, registration
        with self.stop_state_fixture() as fixture:
            registry = SimpleNamespace(begin=lambda *args, **kwargs: None,
                complete=lambda reference, rows, *args: (rows, None))
            with patch.object(coordinator, 'resolve_selected', side_effect=lambda rows, **kwargs: rows), \
                 patch.object(coordinator, 'prepare_selected', side_effect=lambda rows: rows) as prepare, \
                 patch.object(registration, 'Registry', return_value=registry):
                yield *fixture, prepare

    def batch_stage_receipt(self, root):
        self.assertFalse((root / '.cache/benchmark-runtime/live-batch.lock').exists())
        self.assertTrue(json.loads((root / 'benchmarks/config/execution.json').read_bytes())['experiments_stopped'])
        receipt = json.loads((root / 'benchmarks/config/benchmark-status.json').read_bytes())
        self.assertEqual(receipt['provider_launches'], 0)
        self.assertEqual(receipt['model_api_calls'], 0)
        self.assertGreaterEqual(receipt['timings']['total_seconds'], 0)
        for stage in receipt['timings']['stages']:
            self.assertGreaterEqual(stage['elapsed_seconds'], 0)
        return receipt

    def test_100_preparation_failure_saves_timing_and_skips_finalizer(self):
        import run
        with self.batch_stage_fixture() as (root, entry, selection, prepare):
            prepare.side_effect = ValueError('Preparation control')
            with patch.object(run, 'execute', side_effect=AssertionError('Unexpected dispatch')) as dispatch, \
                 patch.object(run, 'finalize', side_effect=AssertionError('Failure rebuilt report')) as finalize:
                with self.assertRaisesRegex(ValueError, 'Preparation control'):
                    run.run_batch([entry], selection, finalize=finalize)
                dispatch.assert_not_called()
                finalize.assert_not_called()
            receipt = self.batch_stage_receipt(root)
            self.assertTrue(receipt['preparation_failed'])
            stages = {row['stage']: row['status'] for row in receipt['timings']['stages']}
            self.assertEqual(stages['preparation'], 'failed')
            self.assertEqual(stages['finalization'], 'skipped')

    def test_101_failed_queue_saves_progress_without_rebuilding_report(self):
        import run
        with self.batch_stage_fixture() as (root, entry, selection, prepare):
            progress = []
            original_save = run.save
            def save(path, value, *args, **kwargs):
                if path.name == 'benchmark-status.json' and value.get('running'):
                    progress.append(value)
                return original_save(path, value, *args, **kwargs)
            with patch.object(run, 'execute', side_effect=ValueError('Dispatch control')) as dispatch, \
                 patch.object(run, 'save', side_effect=save), \
                 patch.object(run, 'finalize', side_effect=AssertionError('Failure rebuilt report')) as finalize:
                with self.assertRaisesRegex(RuntimeError, 'Benchmark incomplete'):
                    run.run_batch([entry, {**entry, 'case': 'second'}], selection, report_every=1, finalize=finalize)
                self.assertEqual(dispatch.call_count, 1)
                finalize.assert_not_called()
            self.assertEqual(len(progress), 1)
            receipt = self.batch_stage_receipt(root)
            self.assertEqual(len(receipt['failures']), 1)
            self.assertEqual(receipt['unprocessed_tasks'], 1)
            self.assertEqual(receipt['timings']['stages'][-1]['status'], 'skipped')

    def test_102_success_finalizes_once_and_records_stage_timings(self):
        import run
        with self.batch_stage_fixture() as (root, entry, selection, prepare):
            timings = run.BatchTimings()
            with timings.stage('selection'):
                pass
            with patch.object(run, 'execute', return_value={'status': 'cache_hit'}), \
                 patch.object(run, 'finalize') as finalize:
                result = run.run_batch([entry], selection, report_every=1, finalize=finalize, timings=timings)
                finalize.assert_called_once_with()
            receipt = self.batch_stage_receipt(root)
            self.assertEqual(receipt, result)
            self.assertEqual(receipt['cache_hits'], 1)
            self.assertEqual([row['stage'] for row in receipt['timings']['stages']],
                ['selection', 'action_resolution', 'schedule', 'preparation', 'prepared_action_resolution',
                 'binding', 'execute', 'finalization'])
            self.assertTrue(all(row['status'] == 'complete' for row in receipt['timings']['stages']))

    def test_103_interrupted_execution_saves_timing_and_skips_finalizer(self):
        import run
        with self.batch_stage_fixture() as (root, entry, selection, prepare):
            with patch.object(run, 'execute', side_effect=KeyboardInterrupt), \
                 patch.object(run, 'finalize', side_effect=AssertionError('Cancellation rebuilt report')) as finalize:
                with self.assertRaises(KeyboardInterrupt):
                    run.run_batch([entry], selection, finalize=finalize)
                finalize.assert_not_called()
            receipt = self.batch_stage_receipt(root)
            self.assertTrue(receipt['interrupted'])
            stages = {row['stage']: row['status'] for row in receipt['timings']['stages']}
            self.assertEqual(stages['execute'], 'interrupted')
            self.assertEqual(stages['finalization'], 'skipped')

    def test_104_capability_only_selection_skips_native_attempt_inventory(self):
        import run
        from capabilities import coordinator
        selection = {'models': ['fixture'], 'suites': ['capabilities-v1'], 'experiments': 'primary',
                     'cases': 'all', 'condition': 'active', 'capabilities': True}
        rows = [{'model': 'fixture', 'suite': 'capabilities-v1', 'experiment': 'capabilities-v1',
                 'case': status, 'status': status} for status in ['not_started', 'preparation_pending', 'preparation_invalid']]
        with patch.object(run.records, 'registry', return_value={'experiments': [
                {'id': 'native', 'suite': 'core', 'role': 'primary'}]}), \
             patch.object(run.records, 'models', return_value={'fixture': {'id': 'fixture'}}), \
             patch.object(run, 'plan', side_effect=AssertionError('Unselected native inventory')) as plan, \
             patch.object(coordinator, 'entries', return_value=rows):
            self.assertEqual(run.selected_entries(selection), rows)
            plan.assert_not_called()


class AttemptRoutingControls(AttemptRoutingChecks, unittest.TestCase):
    pass


if __name__ == '__main__':
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Provider network')), \
         patch.object(subprocess, 'Popen', side_effect=AssertionError('External process')):
        unittest.main(verbosity=2)
