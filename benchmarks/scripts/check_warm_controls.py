#!/usr/bin/python3
"""Small offline controls for warm integrity checks and temporary cache persistence."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import action_cache
import check_warm_cache as warm
import runtime_storage
import timed_checks
import sys
from types import SimpleNamespace


class WarmControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='warm-control-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_fresh_seal_detects_same_size_mutation(self):
        path = self.root / 'input'; path.write_bytes(b'first')
        snapshot = warm.Snapshot(self.root)
        self.assertEqual(snapshot.read('input'), b'first')
        path.write_bytes(b'other')
        with self.assertRaisesRegex(ValueError, 'changed during'):
            snapshot.seal()

    def test_snapshot_rejects_escaping_path_and_symlink(self):
        (self.root / 'link').symlink_to('/etc/hostname')
        for name in ('../escape', '/absolute', 'link'):
            with self.assertRaises(ValueError): warm.Snapshot(self.root).read(name)

    def test_receipt_requires_actual_pass_and_current_content(self):
        (self.root / 'source').write_bytes(b'exact source')
        config = self.root / 'benchmarks/config'; config.mkdir(parents=True)
        receipt = {'schema_version': 1, 'passed': True, 'model_api_calls': 0, 'provider_launches': 0,
                   'inputs_sha256': {'source': action_cache.digest(b'exact source')['hash']}}
        path = config / 'fixture.json'; path.write_text(json.dumps(receipt))
        warm.verify_receipt(warm.Snapshot(self.root), 'fixture.json')
        (self.root / 'source').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'changed'):
            warm.verify_receipt(warm.Snapshot(self.root), 'fixture.json')
        receipt['passed'] = False; path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'successful offline acceptance'):
            warm.verify_receipt(warm.Snapshot(self.root), 'fixture.json')

    def test_action_ignores_unrelated_file_but_binds_selected_bytes(self):
        source = self.root / 'selected'; source.write_bytes(b'input')
        def action():
            snapshot = warm.Snapshot(self.root); snapshot.read('selected')
            return action_cache.Action('warm-fixture', {'inputs.json': action_cache.canonical(snapshot.seal())})
        first = action()
        (self.root / 'unrelated-report.py').write_bytes(b'new presentation')
        self.assertEqual(first.key, action().key)
        source.write_bytes(b'changed input')
        self.assertNotEqual(first.key, action().key)

    def test_miss_creates_cache_then_fresh_reader_hits_and_rejects_corruption(self):
        local = action_cache.Cache(self.root / 'persistent')
        action = action_cache.Action('warm-fixture', {'input': b'exact'})
        self.assertIsNone(local.lookup(action))
        hit = local.commit(action, {'proof': b'verified'})
        self.assertEqual(action_cache.Cache(local.root).lookup(action), hit)
        local._path('cas', hit['output_files']['proof']['digest']['hash']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Corrupt CAS'):
            local.lookup(action)

    def test_provider_and_process_guard_reject_launch(self):
        with patch.object(subprocess, 'Popen', side_effect=warm.forbidden):
            with self.assertRaisesRegex(RuntimeError, 'cannot start'):
                subprocess.run(['/usr/bin/true'])

    def test_time_budget_does_not_turn_into_cold_work(self):
        timings = warm.Timings(-1)
        with self.assertRaisesRegex(RuntimeError, 'budget exceeded'):
            with timings.stage('forbidden'): self.fail('Started expired stage')

    def test_cold_timing_requires_finish_before_claiming_pass(self):
        path = self.root / 'cold-timings.json'
        with patch.object(timed_checks, 'RECEIPT', path):
            with patch.object(sys, 'argv', ['timed_checks', 'start']):
                self.assertEqual(timed_checks.main(), 0)
            with patch.object(sys, 'argv', ['timed_checks', 'run', '--', 'python3', 'check_fixture.py']), \
                 patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                self.assertEqual(timed_checks.main(), 0)
            value = json.loads(path.read_bytes())
            self.assertFalse(value['complete'])
            self.assertFalse(value['passed'])
            self.assertEqual(len(value['stages']), 1)
            with patch.object(sys, 'argv', ['timed_checks', 'finish']):
                self.assertEqual(timed_checks.main(), 0)
            self.assertTrue(json.loads(path.read_bytes())['passed'])

    def test_new_cache_survives_real_temporary_wsl_runtime_cleanup(self):
        # A tiny pinned installation exercises the actual mount/cleanup guard,
        # without extracting gigabytes merely to test cache persistence.
        with tempfile.TemporaryDirectory(prefix='warm-persistence-', dir=action_cache.ROOT / '.cache/benchmark-runtime') as directory:
            root = Path(directory)
            (root / 'node_modules').mkdir()
            (root / 'node_modules/entry').write_bytes(b'pinned tool')
            package, archive = runtime_storage.package(root, self.root / 'packages', paths=['node_modules'])
            persistent = root / '.cache/benchmark-actions/v3'
            source = str(action_cache.ROOT / 'benchmarks/scripts')
            script = ('import sys,json; from pathlib import Path; sys.path.insert(0,' + repr(source) + '); '
                'import action_cache; local=action_cache.Cache(' + repr(str(persistent)) + '); '
                'action=action_cache.Action("temporary-cache-persistence", {"input":b"fixed input"}); '
                'hit=local.lookup(action); existed=hit is not None; '
                'hit=hit or local.commit(action,{"output":b"persistent output"}); '
                'assert local.get(hit["output_files"]["output"]["digest"])==b"persistent output"; '
                'Path("proof.json").write_text(json.dumps({"key":action.key,"hit":existed}))')
            configuration = {'mode': 'linux_temp', 'temporary_root': str(self.root), 'cleanup_grace_seconds': 1}
            for expected_hit in (False, True):
                with patch.object(runtime_storage, 'package', return_value=(package, archive)):
                    self.assertEqual(runtime_storage.execute(['/usr/bin/python3', '-c', script], root=root,
                                                            configuration=configuration), 0)
                proof = json.loads((root / 'proof.json').read_bytes())
                self.assertEqual(proof['hit'], expected_hit)
                self.assertTrue((persistent / 'ac' / proof['key']).is_file())
                self.assertEqual(list(self.root.glob('rea-runtime-*')), [])
                if not expected_hit: first_key = proof['key']
                else: self.assertEqual(proof['key'], first_key)


if __name__ == '__main__':
    unittest.main(verbosity=2)
