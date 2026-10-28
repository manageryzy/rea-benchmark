"""Focused offline checks for incremental coordinator validation actions."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import action_cache as ac
import validation_cache as vc

SOURCE = '''import unittest
import socket
class TypedCoordinatorControls(unittest.TestCase):
    def helper(self): return 7
    def test_alpha(self): self.assertEqual(self.helper(), 7)
    def test_beta(self):
        with self.assertRaises(AssertionError):
            socket.socket().connect(('127.0.0.1', 1))
'''


class Controls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='validation-cache-controls-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'workspace'
        (self.root / '.cache/benchmark-runtime').mkdir(parents=True)
        for name in vc.PROJECTED:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(SOURCE if name == vc.PROJECTED[0] else '')
        (self.root / 'dependency.txt').write_text('fixture/toolchain/config revision one')
        self.cache = ac.Cache(Path(self.temporary.name) / 'cas')

    def plan(self, tests=('test_alpha', 'test_beta')):
        return vc.Plan(self.root, vc.PROJECTED, tests,
                       inputs={'dependency.txt': (self.root / 'dependency.txt').read_bytes()})

    def passed(self, name):
        return {'checks': 1, 'passed': True, 'tests': [{'name': name, 'status': 'passed'}],
                'provider_launches': 0, 'model_api_calls': 0}

    def test_01_repeat_hit_skips_snapshot_and_worker(self):
        plan = self.plan()
        for name in plan.tests: plan.commit(self.cache, name, self.passed(name))
        def forbidden(*args): raise AssertionError('Snapshot on cache hit')
        result = vc.execute(self.plan(), cache=self.cache, snapshot=forbidden)
        self.assertEqual((result['cache_hits'], result['executed']), (2, 0))

    def test_02_one_test_edit_invalidates_only_that_test(self):
        before = self.plan()
        p = self.root / vc.PROJECTED[0]
        p.write_text(SOURCE.replace('self.assertEqual(self.helper(), 7)', 'self.assertEqual(self.helper(), 8)'))
        after = self.plan()
        self.assertNotEqual(before.actions['test_alpha'].key, after.actions['test_alpha'].key)
        self.assertEqual(before.actions['test_beta'].key, after.actions['test_beta'].key)

    def test_03_shared_helper_and_fixture_changes_invalidate(self):
        before = self.plan()
        (self.root / vc.PROJECTED[0]).write_text(SOURCE.replace('return 7', 'return 8'))
        after = self.plan()
        self.assertTrue(all(before.actions[n].key != after.actions[n].key for n in before.tests))
        (self.root / vc.PROJECTED[0]).write_text(SOURCE)
        (self.root / 'dependency.txt').write_text('changed pinned toolchain')
        after = self.plan()
        self.assertTrue(all(before.actions[n].key != after.actions[n].key for n in before.tests))

    def test_04_unrelated_file_and_tmp_location_are_invariant(self):
        before = self.plan()
        (self.root / 'report.py').write_text('unrelated report change')
        after = self.plan()
        self.assertEqual(before.actions['test_alpha'].key, after.actions['test_alpha'].key)
        relocated = Path(self.temporary.name) / 'relocated'
        shutil.copytree(self.root, relocated)
        other = vc.Plan(relocated, vc.PROJECTED, before.tests,
                        inputs={'dependency.txt': (relocated / 'dependency.txt').read_bytes()})
        self.assertEqual(before.actions['test_alpha'].key, other.actions['test_alpha'].key)

    def test_05_corrupt_output_rejected(self):
        plan = self.plan()
        plan.commit(self.cache, 'test_alpha', self.passed('test_alpha'))
        record = self.cache.lookup(plan.actions['test_alpha'])
        digest = record['output_files']['check.json']['digest']
        self.cache._path('cas', digest['hash']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError): plan.lookup(self.cache, 'test_alpha')

    def test_06_failed_or_wrong_test_cannot_be_cached(self):
        plan = self.plan()
        for change in ({'passed': False}, {'model_api_calls': 1}, {'checks': 0},
                       {'tests': [{'name': 'test_beta', 'status': 'passed'}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                plan.commit(self.cache, 'test_alpha', self.passed('test_alpha') | change)

    def test_07_actual_isolated_worker_then_zero_worker_replay(self):
        def snapshot(source, target):
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('.cache'))
            (target / '.cache/benchmark-runtime').mkdir(parents=True)
        result = vc.execute(self.plan(), jobs=2, cache=self.cache, snapshot=snapshot)
        self.assertTrue(result['passed'])
        self.assertEqual(result['executed'], 2)
        result = vc.execute(self.plan(), cache=self.cache,
                            snapshot=lambda *args: self.fail('Repeated worker setup'))
        self.assertEqual(result['executed'], 0)

    def test_08_mutation_and_incomplete_admission_rejected(self):
        plan = self.plan()
        (self.root / 'dependency.txt').write_text('changed after planning')
        with self.assertRaises(ValueError): plan.unchanged()
        with self.assertRaises(RuntimeError): vc.require(self.plan(), {'schema_version': 2}, self.cache)

    def test_09_projection_excludes_unselected_executable_body(self):
        projected = vc.project(SOURCE.encode(), {'test_alpha'}).decode()
        self.assertIn('def test_alpha', projected)
        self.assertNotIn('def test_beta', projected)
        self.assertIn('def helper', projected)

    def test_10_executable_mode_is_an_input(self):
        before = self.plan()
        path = self.root / 'dependency.txt'
        path.chmod(path.stat().st_mode ^ 0o111)
        after = self.plan()
        self.assertNotEqual(before.actions['test_alpha'].key, after.actions['test_alpha'].key)

    def test_11_admission_resolves_actions_and_preserves_old_receipt(self):
        plan = self.plan()
        for name in plan.tests: plan.commit(self.cache, name, self.passed(name))
        receipt = self.root / 'receipt.json'
        receipt.write_text('{"schema_version":1,"historical":true}')
        outcome = vc.execute(plan, cache=self.cache)
        vc.write_receipt(receipt, plan, outcome)
        vc.require(plan, json.loads(receipt.read_bytes()), self.cache)
        self.assertEqual(len(list((self.root / '.cache/benchmark-runtime/validation-receipts').glob('*.json'))), 1)
        (self.root / 'dependency.txt').write_text('new fixture revision')
        with self.assertRaises(RuntimeError): vc.require(self.plan(), json.loads(receipt.read_bytes()), self.cache)


if __name__ == '__main__':
    unittest.main(verbosity=2)
