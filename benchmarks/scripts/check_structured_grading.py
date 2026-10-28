#!/usr/bin/python3
"""Offline controls for the closed localization artifact worker; zero providers."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import build, locations
from grading import artifacts, localization, offline
from grading.structured import runtime, evidence


class StructuredControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.truth = locations.private_truth('record_codec', build.build('record_codec'))
        cls.answer = locations.answer(cls.truth)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rea-artifact-controls-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.cache = action_cache.Cache(Path(self.temporary.name) / 'ac')

    def files(self, answer=None):
        return {'locations.json': action_cache.canonical(answer or self.answer)}

    def grade(self, answer=None):
        with runtime.validation_controls(), offline.guard():
            return runtime.grade(self.files(answer), self.truth, cache=self.cache)

    def test_01_actual_closed_worker_and_equivalent_artifacts(self):
        result = self.grade()
        self.assertEqual(result['status'], 'graded')
        self.assertTrue(result['capabilities']['L1']['top1'])
        self.assertEqual(result['capabilities']['L4']['f1'], 1)
        self.assertEqual(result['grading_runtime']['scope'], 'closed_data_only_artifact_worker_v1')
        self.assertEqual(set(result['grading_runtime']['source_inputs_sha256']), set(runtime.SOURCES))
        different = copy.deepcopy(self.answer)
        different['sites'].reverse()
        equivalent = self.grade(different)
        self.assertEqual(result['capabilities'], equivalent['capabilities'])
        self.assertNotEqual(result['grading_id'], equivalent['grading_id'])

    def test_02_single_defect_changes_behavior_score(self):
        answer = copy.deepcopy(self.answer)
        answer['references'][0]['access'] = 'write'
        result = self.grade(answer)
        self.assertTrue(result['artifact_valid'])
        self.assertEqual(result['capabilities']['L4']['correct'], 0)

    def test_03_invalid_json_is_artifact_failure_not_environment_failure(self):
        with runtime.validation_controls(), offline.guard():
            result = runtime.grade({'locations.json': b'{"x":1,"x":2}'}, self.truth, cache=self.cache)
        self.assertEqual(result['status'], 'invalid_artifact')
        self.assertFalse(result['artifact_valid'])
        self.assertEqual(result['capabilities'], {})

    def test_04_wrong_binary_is_invalid_artifact(self):
        answer = copy.deepcopy(self.answer)
        answer['binary_sha256'] = '0' * 64
        result = self.grade(answer)
        self.assertEqual(result['status'], 'wrong_target')
        self.assertFalse(result['artifact_valid'])

    def test_05_invalid_private_truth_is_infrastructure_failure(self):
        with runtime.validation_controls(), offline.guard(), self.assertRaisesRegex(RuntimeError, 'no model correctness grade'):
            runtime.grade(self.files(), {**self.truth, 'validated': False}, cache=self.cache)

    def test_06_cache_reuses_a_verified_result_without_worker(self):
        first = self.grade()
        with patch.object(runtime, 'invoke', side_effect=AssertionError('Duplicate artifact worker')):
            second = self.grade()
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])
        self.assertEqual(first['capabilities'], second['capabilities'])

    def test_07_host_monkeypatches_do_not_enter_worker(self):
        with (patch.object(artifacts, 'admit', side_effect=AssertionError('Host parser')),
              patch.object(localization, 'grade', side_effect=AssertionError('Host scorer'))):
            result = self.grade()
        self.assertTrue(result['artifact_valid'])

    def test_08_source_drift_is_refused_before_launch(self):
        action, request, profile, environment, frozen = runtime.prepare(self.files(), self.truth)
        changed = {**frozen, runtime.SOURCES[0]: b'changed'}
        with patch.object(runtime, 'sources', return_value=changed), patch.object(runtime.process, 'run') as child:
            with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                runtime.invoke(request, profile, environment, frozen)
            child.assert_not_called()

    def test_09_runtime_drift_is_refused_before_launch(self):
        action, request, profile, environment, frozen = runtime.prepare(self.files(), self.truth)
        with patch.object(runtime.image, 'record', return_value={**runtime.image.record(), 'id': '0' * 64}), patch.object(runtime.process, 'run') as child:
            with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                runtime.invoke(request, profile, environment, frozen)
            child.assert_not_called()

    def test_10_missing_or_stale_controls_block_production(self):
        path = Path(self.temporary.name) / 'missing-controls.json'
        with patch.object(evidence, 'RECEIPT', path):
            with self.assertRaisesRegex(RuntimeError, 'missing or stale'):
                runtime.grade(self.files(), self.truth, cache=self.cache)
            path.write_bytes(action_cache.canonical({'schema_version': 1, 'passed': True,
                'runtime_id': runtime.image.record()['id'], 'inputs_sha256': {}, 'model_api_calls': 0}))
            with self.assertRaisesRegex(RuntimeError, 'missing or stale'):
                runtime.grade(self.files(), self.truth, cache=self.cache)

    def test_11_false_runtime_in_a_cache_result_is_rejected(self):
        action, request, profile, environment, frozen = runtime.prepare(self.files(), self.truth)
        self.cache.commit(action, {'grade.json': action_cache.canonical({'status': 'graded', 'grading_runtime': {'runtime_id': '0' * 64}})})
        with runtime.validation_controls(), self.assertRaisesRegex(ValueError, 'declared action'):
            runtime.grade(self.files(), self.truth, cache=self.cache)

    def test_12_input_and_policy_bounds(self):
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            runtime.prepare({'locations.json': b'x' * 262145}, self.truth)
        path = Path(self.temporary.name) / 'policy.json'
        for key in runtime.policy()['limits']:
            value = runtime.policy()
            value['limits'][key] = 0
            path.write_bytes(action_cache.canonical(value))
            with self.subTest(key=key), patch.object(runtime, 'CONFIG', path), self.assertRaises(ValueError):
                runtime.policy()

    def test_13_declared_sources_only_and_stage_isolation(self):
        before = runtime.prepare(self.files(), self.truth)[0].key
        shadow = Path(self.temporary.name) / 'source'
        for name in runtime.SOURCES:
            path = shadow / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((runtime.ROOT / name).read_bytes())
        with patch.object(runtime, 'ROOT', shadow):
            self.assertEqual(before, runtime.prepare(self.files(), self.truth)[0].key)
            (shadow / 'README.md').write_text('Unrelated report/pricing/code change')
            self.assertEqual(before, runtime.prepare(self.files(), self.truth)[0].key)
            path = shadow / 'benchmarks/scripts/grading/localization.py'
            path.write_bytes(path.read_bytes() + b'\n# semantic dependency change\n')
            self.assertNotEqual(before, runtime.prepare(self.files(), self.truth)[0].key)


def main():
    started = evidence.begin()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StructuredControls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__':
    main()
