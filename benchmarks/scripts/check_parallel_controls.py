"""Offline regression controls for process/workspace isolation and aggregation."""
import copy
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import parallel_controls as parallel
import control_workspace as workspace
import action_cache as ac

FIXTURE = '''import os
from pathlib import Path
import socket
import time
import unittest

class Controls(unittest.TestCase):
    def exercise(self, number):
        root = Path.cwd()
        self.assertEqual((root / 'task-index').read_text(), 'original')
        (root / 'task-index').write_text(str(number))
        cold = root / 'explicit-cold-cache'
        cold.mkdir()
        self.assertEqual(list(cold.iterdir()), [])
        self.assertNotIn('SYNTHETIC_API_KEY', os.environ)
        with self.assertRaises(AssertionError):
            socket.socket().connect(('127.0.0.1', 1))
        barrier = Path(os.environ['CONTROL_TEST_BARRIER'])
        (barrier / str(os.getpid())).touch()
        limit = time.monotonic() + 10
        while len(list(barrier.iterdir())) < 4 and time.monotonic() < limit:
            time.sleep(0.01)
        self.assertEqual(len(list(barrier.iterdir())), 4)
        self.assertEqual((root / 'task-index').read_text(), str(number))
    def test_one(self): self.exercise(1)
    def test_two(self): self.exercise(2)
    def test_three(self): self.exercise(3)
    def test_four(self): self.exercise(4)
'''


class ParallelControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rea-parallel-controls-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = {'fixture.py': 'f' * 64}
        self.runner = 'a' * 64

    def receipt(self, name='test_one', **changes):
        return {'schema_version': 1, 'sources': self.sources, 'runner_sha256': self.runner,
                'worker_pid': 1, 'tests': [{'name': name, 'status': 'passed', 'seconds': 0}],
                'checks': 1, 'failures': 0, 'errors': 0, 'skipped': 0, 'passed': True,
                'provider_launches': 0, 'model_api_calls': 0, **changes}

    def collect(self, values, expected=('test_one',)):
        return parallel.collect(values, expected, self.sources, self.runner)

    def test_01_four_processes_overlap_and_keep_mutations_private(self):
        roots = []
        for index in range(4):
            root = self.root / str(index)
            scripts = root / 'benchmarks/scripts'
            scripts.mkdir(parents=True)
            (scripts / 'fixture.py').write_text(FIXTURE)
            (root / 'task-index').write_text('original')
            roots.append(root)
        barrier = self.root / 'barrier'
        barrier.mkdir()
        with patch.dict(os.environ, {'CONTROL_TEST_BARRIER': str(barrier), 'SYNTHETIC_API_KEY': 'never-send'}):
            result = parallel.run_prepared(roots, ['test_one', 'test_two', 'test_three', 'test_four'],
                ['benchmarks/scripts/fixture.py'], self.root / 'results', module='fixture', test_class='Controls', timeout=20)
        self.assertTrue(result['passed'])
        self.assertEqual(result['checks'], 4)
        self.assertEqual(len(set(result['worker_pids'])), 4)
        self.assertEqual(len({(root / 'task-index').read_text() for root in roots}), 4)

    def test_02_missing_duplicate_and_unexpected_checks_rejected(self):
        for values, expected in [([], ['test_one']), ([self.receipt()] * 2, ['test_one']),
                ([self.receipt('test_other')], ['test_one'])]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.collect(values, expected)

    def test_03_stale_sources_and_runner_rejected(self):
        for changes in ({'sources': {}}, {'runner_sha256': 'b' * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.collect([self.receipt(**changes)])

    def test_04_skipped_incomplete_and_provider_receipts_rejected(self):
        for changes in ({'skipped': 1}, {'checks': 2}, {'passed': 'true'},
                {'provider_launches': 1}, {'model_api_calls': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.collect([self.receipt(**changes)])

    def test_05_failed_check_cannot_be_reported_as_passing(self):
        value = self.receipt(failures=1, passed=False)
        value['tests'][0]['status'] = 'failed'
        self.assertFalse(self.collect([value])['passed'])
        value['passed'] = True
        with self.assertRaises(ValueError): self.collect([value])

    def test_06_unfinished_check_rejected(self):
        value = self.receipt()
        value['tests'][0]['status'] = 'running'
        with self.assertRaises(ValueError): self.collect([value])

    def test_07_source_symlink_and_escape_rejected(self):
        target = self.root / 'target'
        target.write_bytes(b'content')
        (self.root / 'alias').symlink_to(target)
        (self.root / 'directory-alias').symlink_to(self.root, target_is_directory=True)
        for name in ['alias', 'directory-alias/target', '../target', '/target']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                parallel.fingerprints(self.root, [name])

    def test_08_root_collisions_and_invalid_selection_rejected(self):
        nested = self.root / 'nested'
        nested.mkdir()
        for roots in ([self.root, self.root], [self.root, nested], []):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                parallel.run_prepared(roots, ['test_one'], ['fixture.py'], self.root / 'unused',
                                      module='fixture', test_class='Controls')

    def fixture_root(self, body):
        root = self.root / 'fixture'
        directory = root / 'benchmarks/scripts'
        directory.mkdir(parents=True)
        (directory / 'fixture.py').write_text(
            'import os\nfrom pathlib import Path\nimport time\nimport unittest\n'
            'class Controls(unittest.TestCase):\n    def test_one(self):\n        ' + body + '\n')
        return root

    def run_fixture(self, root, timeout=10):
        return parallel.run_prepared([root], ['test_one'], ['benchmarks/scripts/fixture.py'],
            self.root / 'results', module='fixture', test_class='Controls', timeout=timeout)

    def test_09_source_mutation_during_worker_rejected(self):
        root = self.fixture_root("Path(__file__).write_text('changed')")
        with self.assertRaisesRegex(ValueError, 'bounded result'):
            self.run_fixture(root)
        self.assertFalse((self.root / 'results/result.json').exists())

    def test_10_actual_worker_failure_retains_failed_outcome(self):
        root = self.fixture_root('self.fail("deliberate negative control")')
        result = self.run_fixture(root)
        self.assertFalse(result['passed'])
        self.assertEqual(result['failures'], 1)
        self.assertEqual(result['checks'], 1)

    def test_11_early_process_exit_cannot_pass(self):
        root = self.fixture_root('os._exit(0)')
        with self.assertRaisesRegex(ValueError, 'bounded result'):
            self.run_fixture(root)
        self.assertFalse((self.root / 'results/result.json').exists())

    def test_12_timeout_cannot_generate_acceptance(self):
        import subprocess
        root = self.fixture_root('time.sleep(10)')
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fixture(root, timeout=0.1)
        self.assertFalse((self.root / 'results/result.json').exists())

    def test_13_old_output_directory_is_not_a_new_receipt(self):
        root = self.fixture_root('self.assertTrue(True)')
        (self.root / 'results/worker-0').mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            self.run_fixture(root)

    def test_14_different_worker_sources_rejected_before_spawn(self):
        root = self.fixture_root('self.assertTrue(True)')
        other = self.root / 'other'
        (other / 'benchmarks/scripts').mkdir(parents=True)
        (other / 'benchmarks/scripts/fixture.py').write_text('different')
        with self.assertRaisesRegex(ValueError, 'different source closures'):
            parallel.run_prepared([root, other], ['test_one'], ['benchmarks/scripts/fixture.py'],
                self.root / 'results', module='fixture', test_class='Controls')

    def test_15_acceptance_adapter_rejects_a_forged_pass_flag(self):
        value = self.collect([self.receipt()])
        result = parallel.AggregateResult(value, 'private/results')
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.execution['worker_count'], 1)
        value['passed'] = False
        with self.assertRaises(ValueError):
            parallel.AggregateResult(value, 'private/results')

    def test_16_isolated_entrypoint_requires_bound_setup_sources(self):
        for jobs, sources in [(0, []), (17, []), (True, []), (4, ['fixture.py'])]:
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                parallel.run_isolated(self.root, ['test_one'], sources, self.root / 'results',
                                      module='fixture', test_class='Controls', jobs=jobs)

    def metadata_fixture(self):
        import re
        import platform
        import sys
        state = {'reads': 0, 'validations': 0, 'record': {'path': 'valid', 'valid': True},
                 'policy': {'allowed': True}}
        module = SimpleNamespace(PurePosixPath=PurePosixPath, re=re, platform=platform,
            sys=sys, json=json, canonical=parallel.canonical, relative=lambda value: value,
            policy=lambda language: dict(state['policy']))
        def validate(record, language):
            state['validations'] += 1
            module.relative(record['path'])
            if not record['valid'] or not module.policy(language)['allowed']:
                raise ValueError('Invalid metadata or policy')
        def load(language):
            state['reads'] += 1
            record = json.loads(json.dumps(state['record']))
            module.validate_record(record, language)
            return record
        module.load, module.validate_record = load, validate
        return module, state

    def test_17_identical_metadata_reuses_admission_but_still_loads(self):
        module, state = self.metadata_fixture()
        with parallel.metadata_validation_cache(module):
            module.load('c'); module.load('c')
        self.assertEqual((state['reads'], state['validations']), (2, 1))
        module.load('c')
        self.assertEqual(state['validations'], 2)

    def test_18_mutated_metadata_is_rejected_and_not_cached(self):
        module, state = self.metadata_fixture()
        with parallel.metadata_validation_cache(module):
            module.load('c')
            state['record']['valid'] = False
            for _ in range(2):
                with self.assertRaises(ValueError): module.load('c')
        self.assertEqual(state['validations'], 3)

    def test_19_changed_policy_is_revalidated(self):
        module, state = self.metadata_fixture()
        with parallel.metadata_validation_cache(module):
            module.load('c')
            state['policy']['allowed'] = False
            with self.assertRaises(ValueError): module.load('c')
        self.assertEqual(state['validations'], 2)

    def test_20_direct_validation_remains_uncached(self):
        module, state = self.metadata_fixture()
        with parallel.metadata_validation_cache(module):
            module.load('c')
            module.validate_record(state['record'], 'c')
            module.validate_record(state['record'], 'c')
        self.assertEqual(state['validations'], 3)

    def test_21_mocked_validation_dependencies_bypass_reuse(self):
        module, state = self.metadata_fixture()
        with parallel.metadata_validation_cache(module):
            module.load('c')
            with patch.object(module, 'relative', side_effect=ValueError('changed dependency')):
                with self.assertRaisesRegex(ValueError, 'changed dependency'): module.load('c')
        self.assertEqual(state['validations'], 2)

    def test_22_metadata_admission_cache_has_session_scope(self):
        module, state = self.metadata_fixture()
        for _ in range(2):
            with parallel.metadata_validation_cache(module):
                module.load('c'); module.load('c')
        self.assertEqual(state['validations'], 2)

    def test_32_preexisting_mocked_load_is_not_patched_or_memoized(self):
        module, state = self.metadata_fixture()
        original = module.load
        with patch.object(module, 'load', wraps=original) as loader:
            with parallel.metadata_validation_cache(module):
                module.load('c'); module.load('c')
                self.assertIs(module.load, loader)
        self.assertEqual((state['reads'], state['validations']), (2, 2))

    def test_33_exception_restores_metadata_functions(self):
        module, state = self.metadata_fixture()
        original_load, original_validate = module.load, module.validate_record
        with self.assertRaisesRegex(RuntimeError, 'scope ended'):
            with parallel.metadata_validation_cache(module):
                module.load('c'); module.load('c')
                raise RuntimeError('scope ended')
        self.assertIs(module.load, original_load)
        self.assertIs(module.validate_record, original_validate)
        module.load('c')
        self.assertEqual((state['reads'], state['validations']), (3, 2))

    def test_34_builtin_load_is_preserved_without_code_introspection(self):
        module, state = self.metadata_fixture()
        module.load = len
        with parallel.metadata_validation_cache(module):
            self.assertIs(module.load, len)
            self.assertEqual(module.load([1, 2]), 2)
        self.assertEqual(state['validations'], 0)

    def test_23_real_manifest_load_keeps_content_and_metadata_checks(self):
        from grading import image_manifest as image
        policy = image.policy('c')
        descriptor = {'sha256': 'a' * 64, 'bytes': 1, 'mode': 0o555, 'elf': True}
        manifest = {'schema_version': 1, 'language': 'c', 'policy_sha256': image.digest(policy),
            'files': {'usr/bin/gcc': descriptor, 'lib/ld-linux-x86-64.so.2': descriptor},
            'directories': ['usr', 'usr/bin', 'lib'], 'loader': 'lib/ld-linux-x86-64.so.2',
            'compiler': {'command': '/usr/bin/gcc', 'executable_sha256': 'a' * 64,
                         'version': 'gcc ' + policy['compiler']['version']}}
        identity = image.digest(manifest)
        record = {'id': identity, 'manifest': manifest, 'archive_sha256': 'b' * 64,
                  'files': 2, 'bytes': 2}
        cache = self.root / 'image'
        (cache / 'profiles').mkdir(parents=True)
        (cache / 'active.json').write_bytes(image.canonical({'c': {'id': identity,
                                              'policy_sha256': image.digest(policy)}}))
        path = cache / 'profiles' / (identity + '.json')
        path.write_bytes(image.canonical(record))
        original = image.validate_record
        calls = []
        def counted(value, language):
            calls.append(language)
            return original(value, language)
        with patch.object(image, 'validate_record', counted), parallel.metadata_validation_cache(image):
            image.load('c', cache=cache); image.load('c', cache=cache)
            self.assertEqual(len(calls), 1)
            corrupt = {**record, 'archive_sha256': 'invalid'}
            path.write_bytes(image.canonical(corrupt))
            with self.assertRaisesRegex(ValueError, 'archive'): image.load('c', cache=cache)
            path.write_bytes(image.canonical({**record, 'manifest': {**manifest, 'language': 'go'}}))
            with self.assertRaisesRegex(ValueError, 'manifest is corrupt'): image.load('c', cache=cache)



    def test_35_imported_load_alias_reuses_admission_without_skipping_reads(self):
        module, state = self.metadata_fixture()
        alias = module.load
        with parallel.metadata_validation_cache(module):
            alias('c'); alias('c')
            self.assertEqual((state['reads'], state['validations']), (2, 1))
            module.validate_record(state['record'], 'c')
            self.assertEqual(state['validations'], 2)
            state['record']['valid'] = False
            with self.assertRaises(ValueError): alias('c')
            self.assertEqual((state['reads'], state['validations']), (3, 3))


class WorkspaceControls(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rea-control-workspace-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = ac.Cache(self.root / 'source')
        self.destination = ac.Cache(self.root / 'destination')

    def seed(self):
        return workspace.seed_actions(self.source.root, self.destination.root)

    def test_01_selected_action_exact_reuse_and_explicit_cold_miss(self):
        action = ac.Action('capability_prepare_v1', {'a/b': b'input'}, arguments=['offline'])
        original = self.source.commit(action, {'result': (b'answer', True)})
        lower = [ac.Action(kind, {'source': b'control'})
                 for kind in ('c_compile', 'capability_go_buffer_execute_v1')]
        for selected in lower:
            self.source.commit(selected, {'result.json': b'control result'})
        counts = self.seed()
        self.assertEqual(counts['actions'], 3)
        self.assertTrue(all(self.destination.lookup(selected) for selected in lower))
        self.assertEqual(self.destination.lookup(action), original)
        self.assertIsNone(ac.Cache(self.root / 'explicit-cold').lookup(action))
        # The ordinary implementation remains in force, without read-through.
        self.assertEqual(ac.Cache.__module__, 'action_cache')
        output = self.destination.root / 'cas' / ac.digest(b'answer')['hash']
        output.write_bytes(b'changed')
        self.assertEqual(self.source.get(ac.digest(b'answer')), b'answer')

    def test_02_model_submission_and_unknown_actions_excluded(self):
        for kind in ('inference', 'accounting', 'public_report', 'capability_submission_v1', 'unknown'):
            self.source.commit(ac.Action(kind, {'sensitive': b'private-reasoning'}), {'raw': b'private-reasoning'})
        self.assertEqual(self.seed()['excluded'], 5)
        self.assertFalse((self.destination.root / 'cas').exists())

    def test_03_corrupt_selected_blob_rejected(self):
        action = ac.Action('c_execute', {'source.c': b'code'})
        self.source.commit(action, {'stdout': b'output'})
        (self.source.root / 'cas' / ac.digest(b'output')['hash']).write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'Corrupt CAS'): self.seed()

    def test_04_action_identity_change_rejected(self):
        action = ac.Action('c_execute', {'source.c': b'code'})
        record = self.source.commit(action, {'stdout': b'output'})
        record['action']['timeout_seconds'] = 42
        (self.source.root / 'ac' / action.key).write_bytes(ac.canonical(record))
        with self.assertRaisesRegex(ValueError, 'identity'): self.seed()

    def test_05_symlink_cache_record_rejected(self):
        action = ac.Action('c_execute', {})
        self.source.commit(action, {'stdout': b''})
        record = self.source.root / 'ac' / action.key
        renamed = self.root / 'record'
        record.rename(renamed)
        record.symlink_to(renamed)
        with self.assertRaisesRegex(ValueError, 'record'): self.seed()

    def test_06_source_copy_is_independent(self):
        source = self.root / 'tree'
        source.mkdir()
        (source / 'task-index').write_bytes(b'original')
        destination = self.root / 'copy'
        workspace.copy_tree(source, destination)
        (destination / 'task-index').write_bytes(b'worker-update')
        self.assertEqual((source / 'task-index').read_bytes(), b'original')

    def test_07_symlink_source_and_special_files_rejected(self):
        source = self.root / 'tree'
        source.mkdir()
        (source / 'link').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            workspace.copy_tree(source, self.root / 'copy')

    def test_08_sdk_metadata_private_and_payload_shared(self):
        source = self.root / 'sdk'
        payload = source / ('a' * 64)
        payload.mkdir(parents=True)
        (payload / 'compiler').write_bytes(b'pinned')
        (source / 'active.json').write_bytes(b'original')
        archives = source / 'archives'
        archives.mkdir()
        archive = archives / ('b' * 64 + '.tar.gz')
        archive.write_bytes(b'pinned archive fixture')
        (source / 'worker-state').mkdir()
        (source / 'worker-state' / 'state').write_bytes(b'should-not-copy')
        destination = self.root / 'copy'
        workspace.copy_sdk(source, destination)
        (destination / 'active.json').write_bytes(b'worker-update')
        self.assertEqual((source / 'active.json').read_bytes(), b'original')
        self.assertTrue((destination / ('a' * 64)).is_symlink())
        self.assertFalse((destination / 'worker-state').exists())
        second = self.root / 'second-copy'
        workspace.copy_sdk(destination, second)
        self.assertEqual((second / 'active.json').read_bytes(), b'worker-update')
        self.assertEqual((second / ('a' * 64)).resolve(), payload.resolve())
        self.assertEqual((second / 'archives' / archive.name).resolve(), archive.resolve())



if __name__ == '__main__':
    unittest.main(verbosity=2)
