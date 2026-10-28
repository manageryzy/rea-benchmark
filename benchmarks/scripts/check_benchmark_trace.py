"""Focused tests for private timing telemetry; no benchmark/provider execution."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import benchmark_trace as trace


class TraceChecks(unittest.TestCase):
    def test_nested_threads_and_exceptions_preserve_behavior_without_arguments(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(trace, 'SCOPES', {}):
            path = Path(tmp) / 'trace.json'
            with trace.Trace(path) as recorder:
                @trace.traced('inner', recorder=recorder)
                def inner(secret):
                    raise ValueError(secret)
                @trace.traced('outer', recorder=recorder)
                def outer(secret):
                    try:
                        inner(secret)
                    except ValueError:
                        return 42
                self.assertEqual(outer('SECRET-DO-NOT-RECORD'), 42)
                worker = threading.Thread(target=lambda: outer('SECOND-SECRET'))
                worker.start(); worker.join()
                self.assertEqual(outer.__name__, 'outer')
            raw = path.read_text()
            self.assertNotIn('SECRET', raw)
            events = json.loads(raw)['traceEvents']
            self.assertEqual(len(events), 8)
            self.assertEqual(len({e['tid'] for e in events}), 2)
            self.assertEqual(sum(e['ph'] == 'B' for e in events), 4)
            self.assertTrue(path.with_suffix('.html').is_file())

    def test_live_view_preserves_unfinished_call_and_html_escaping(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(trace, 'SCOPES', {}):
            path = Path(tmp) / 'trace.json'
            with trace.Trace(path) as recorder:
                recorder.event('</script>', 'B', 1, recorder.started)
                trace.render(path)
                html = path.with_suffix('.html').read_text()
                self.assertIn('\\u003c/script>', html)
                self.assertIn('true', html)
            self.assertEqual(len(json.loads(path.read_text())['traceEvents']), 1)

    def test_disabled_decorator_preserves_result_and_exception(self):
        @trace.traced('disabled')
        def original(value):
            if value < 0:
                raise ValueError('negative')
            return value + 1
        self.assertEqual(original(2), 3)
        with self.assertRaises(ValueError):
            original(-1)

    def test_existing_trace_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.json'
            path.write_text('prior evidence')
            with self.assertRaises(FileExistsError):
                trace.Trace(path)
            self.assertEqual(path.read_text(), 'prior evidence')

    def test_detail_counters_restore_leaf_functions_and_skip_html(self):
        import hashlib
        import os
        with tempfile.TemporaryDirectory() as tmp, patch.object(trace, 'SCOPES', {}):
            path = Path(tmp) / 'detail.json'
            secret = Path(tmp) / 'SECRET-PATH'; secret.write_bytes(b'SECRET-BYTES')
            originals = (Path.read_bytes, Path.resolve, os.lstat, hashlib.sha256)
            with trace.Trace(path, detail=True, render_html=False):
                self.assertEqual(hashlib.sha256(secret.read_bytes()).digest(),
                                 originals[3](b'SECRET-BYTES').digest())
            self.assertEqual(originals, (Path.read_bytes, Path.resolve, os.lstat, hashlib.sha256))
            summary = json.loads(path.with_suffix('.summary.json').read_text())
            names = {r['name'] for r in summary['functions']}
            self.assertIn('io:Path.read_bytes', names); self.assertIn('hash:sha256', names)
            self.assertNotIn('SECRET', path.read_text())
            self.assertFalse(path.with_suffix('.html').exists())

    def test_runtime_archive_and_interrupt_cleanup_are_traced_before_worker(self):
        import hashlib
        import io
        import tarfile
        import runtime_storage
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); archive = root / 'source.tar'
            with tarfile.open(archive, 'w') as stream:
                member = tarfile.TarInfo('node_modules/file'); member.size = 4
                stream.addfile(member, io.BytesIO(b'data'))
            identity = {'paths': ['node_modules'], 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
            path = root / 'trace.json'
            with self.assertRaises(KeyboardInterrupt):
                with trace.Trace(path, scopes=trace.RUNTIME_SCOPES, runtime_io=True, render_html=False):
                    with runtime_storage.temporary_runtime(identity, archive, temporary_root=tmp) as work:
                        self.assertEqual((work / 'node_modules/file').read_bytes(), b'data')
                        raise KeyboardInterrupt()
            self.assertEqual(list(root.glob('rea-runtime-*')), [])
            events = json.loads(path.read_text())['traceEvents']
            names = {e['name'] for e in events}
            self.assertIn('runtime_storage:temporary_runtime.enter', names)
            self.assertIn('runtime_storage:temporary_runtime.exit_cleanup', names)
            summary = json.loads(path.with_suffix('.summary.json').read_text())
            counts = {r['name']: r['calls'] for r in summary['functions']}
            for key in ('runtime_archive:source.read', 'runtime_archive:linux_snapshot.write',
                        'runtime_archive:extract_member', 'runtime_archive:sha256.update',
                        'runtime_storage:temporary_directory_cleanup'):
                self.assertGreater(counts.get(key, 0), 0, key)
            self.assertFalse(summary['completed'])
            self.assertNotIn('node_modules/file', path.read_text())

    def test_staged_merges_shared_clock_and_preserves_nonzero_exit(self):
        import runtime_storage
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.json'
            def fake_runtime_main():
                self.assertTrue(path.exists())  # trace exists before staging begins
                arguments = sys.argv
                worker = Path(arguments[arguments.index('--output') + 1])
                epoch = int(arguments[arguments.index('--epoch-ns') + 1])
                with trace.Trace(worker, scopes={}, epoch_ns=epoch, process_id=2, render_html=False) as recorder:
                    @trace.traced('offline-worker', recorder=recorder)
                    def local_only(): return None
                    local_only()
                return 130
            with patch.object(runtime_storage, 'main', fake_runtime_main):
                with self.assertRaises(SystemExit) as error:
                    trace.staged(['unused.py'], path)
            self.assertEqual(error.exception.code, 130)
            events = json.loads(path.read_text())['traceEvents']
            self.assertEqual({e['pid'] for e in events}, {1, 2})
            self.assertTrue(any(e['name'].startswith('startup:instrument_import:') for e in events))
            self.assertTrue(all(e['ts'] >= 0 for e in events))
            self.assertTrue(path.with_name('trace.runtime.json').is_file())
            self.assertTrue(path.with_name('trace.worker.json').is_file())

    def test_bootstrap_is_traced_and_scoped_probes_restore_wrappers(self):
        import sys
        import types
        from read_session import ReadSession
        from capabilities import config
        original = config.load
        module = types.ModuleType('offline_probe')
        module.main = lambda: None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'startup.json'
            with (patch.dict(sys.modules, {'offline_probe': module}),
                  patch.object(sys, 'argv', ['trace', '--output', str(path), '--accelerate', '--no-render', '--', 'offline_probe.py'])):
                trace.main()
            events = json.loads(path.read_text())['traceEvents']
            names = {e['name'] for e in events}
            for label in ('startup:import_command', 'startup:ReadSession.enter', 'startup:install_probes', 'command:main'):
                self.assertIn(label, names)
            self.assertIs(config.load, original)

    def test_incremental_hash_and_buffer_io_are_visible_without_payloads(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'hash.json'
            with trace.Trace(path, scopes={}, detail=True, render_html=False):
                h = hashlib.sha256(b'a'); h.update(b'b')
                self.assertEqual(h.copy().hexdigest(), hashlib.sha256(b'ab').hexdigest())
                data = Path(tmp) / 'data'; data.write_bytes(b'private')
                self.assertEqual(data.read_bytes(), b'private')
            names = {r['name'] for r in json.loads(path.with_suffix('.summary.json').read_text())['functions']}
            self.assertTrue({'hash:sha256.update', 'hash:sha256.hexdigest', 'io:buffer.read'} <= names)
            self.assertNotIn('private', path.read_text())

    def test_deepcopy_probe_counts_outer_calls_and_restores_function(self):
        import copy
        original = copy.deepcopy
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'copy.json'
            with trace.Trace(path, scopes={}, detail=True, render_html=False):
                source = {'DO-NOT-RECORD': [{'number': n} for n in range(1000)]}
                result = copy.deepcopy(source)
                self.assertEqual(result, source)
                self.assertIsNot(result, source)
            self.assertIs(copy.deepcopy, original)
            summary = json.loads(path.with_suffix('.summary.json').read_text())
            copies = [r for r in summary['functions'] if r['name'].startswith('copy:deepcopy:')]
            self.assertEqual(sum(r['calls'] for r in copies), 1)
            self.assertNotIn('DO-NOT-RECORD', path.read_text())

    def test_incomplete_worker_keeps_only_complete_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'partial.json'
            event = {'name': 'not-finished', 'ph': 'B', 'pid': 2, 'tid': 1, 'ts': 5}
            path.write_text('{"traceEvents":[' + json.dumps(event) + ',\n{"name":')
            self.assertEqual(trace.trace_events(path), [event])


if __name__ == '__main__':
    unittest.main()
