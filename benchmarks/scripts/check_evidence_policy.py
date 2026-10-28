"""Focused offline selection and action-key checks for compact evidence."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import action_cache
import cache
import environment_profile

ROOT = environment_profile.ROOT
POLICY = 'benchmarks/config/evidence-paging-lean.json'
CONDITION = 'lean-evidence-v4'


def fixture(condition=CONDITION):
    public = {'prompt.txt': b'fixture prompt', 'target': b'ELF fixture',
              'tools.json': b'{}', 'manifest.json': b'{}'}
    model = {'id': 'offline-fixture', 'harness': 'deepseek-harness'}
    case = {'id': 'cpp', 'language': 'cpp', 'max_tool_calls': 20,
            'timeout_seconds': 60, 'max_output_tokens': 8192}
    profile = environment_profile.current(model['harness'], condition)
    identity = cache.inference_identity({'experiment': 'offline-fixture', 'condition': condition},
        model, case, profile, public['prompt.txt'], public['target'], public['tools.json'],
        public['manifest.json'], {'schema_version': 1, 'harness_version': 'fixture', 'tool_timeout_ms': 1000})
    return identity, public, cache.profile_bytes(identity)


class EvidencePolicyControls(unittest.TestCase):
    def test_all_native_suites_select_the_distinct_compact_condition(self):
        data = json.loads((ROOT / 'benchmarks/config/conditions.json').read_text())
        self.assertEqual(set(data['active_case_conditions'].values()), {CONDITION})
        self.assertEqual(set(data['active_case_conditions']), {'record-parser', 'complex-native-v1', 'stl-native-v2'})
        self.assertEqual(environment_profile.paging_policy_path(CONDITION), ROOT / POLICY)

    def test_historical_inline_and_v2_policies_are_retained(self):
        self.assertIsNone(environment_profile.paging_policy_path('lossless-evidence-v1'))
        self.assertEqual(environment_profile.paging_policy_path('paged-evidence-v2'),
                         ROOT / 'benchmarks/config/evidence-paging.json')
        old = json.loads((ROOT / 'benchmarks/config/evidence-paging.json').read_text())
        self.assertNotIn('compact_semantic_views', old)

    def test_compact_policy_and_implementation_are_declared_action_inputs(self):
        identity, public, files = fixture()
        self.assertIn(POLICY, files)
        self.assertIn('benchmarks/scripts/evidence-paging.mjs', files)
        action = action_cache.inference(identity, public, recorded_files=files)
        self.assertEqual(action_cache.Action, type(action))

    def test_policy_change_invalidates_only_the_affected_declared_action(self):
        identity, public, files = fixture()
        original = action_cache.inference(identity, public, recorded_files=files).key
        changed_files = dict(files)
        value = json.loads(files[POLICY]);value['inline_characters'] = 16000
        changed_files[POLICY] = json.dumps(value).encode()
        changed_identity = copy.deepcopy(identity)
        changed_identity['environment']['files_sha256'][POLICY] = hashlib.sha256(changed_files[POLICY]).hexdigest()
        changed = action_cache.inference(changed_identity, public, recorded_files=changed_files).key
        self.assertNotEqual(original, changed)
        # An unrelated script is not a declared input and cannot perturb the key.
        extra = {**files, 'unrelated/report-only.py': b'changed report'}
        self.assertEqual(original, action_cache.inference(identity, public, recorded_files=extra).key)

    def test_new_condition_does_not_reuse_historical_v2_actions(self):
        old = fixture('paged-evidence-v2');new = fixture()
        self.assertNotEqual(action_cache.inference(old[0], old[1], recorded_files=old[2]).key,
                            action_cache.inference(new[0], new[1], recorded_files=new[2]).key)

    def test_basic_compiler_preparation_adds_retrieval_without_dispatch(self):
        import records
        import run
        public = ROOT / 'benchmarks/suites/record-parser/public/build-points/cpp--gcc13--O0/target'
        if not public.exists():
            self.skipTest('Optional locally built compiler point absent')
        entry = {'experiment': 'native-pilot-20261019', 'case': 'cpp--gcc13--O0',
                 'condition': CONDITION, 'directory': '.cache/benchmark-runtime/offline-paging-selection'}
        with patch.object(action_cache, 'inference', wraps=action_cache.inference) as selected:
            task = run.prepare_task(entry, records.models()['gpt-6.1-sol'],
                                    materialize=False, offline_fixture=True)
        tools = json.loads(selected.call_args.args[1]['tools.json'])['tools']
        self.assertIn('read_evidence', [tool['name'] for tool in tools])
        self.assertEqual(task['condition'], CONDITION)
        self.assertEqual(task['environment_profile']['id'], CONDITION)
        self.assertTrue(task['offline_fixture'])
        self.assertNotIn('runtime_attestation', task)


if __name__ == '__main__':
    unittest.main(verbosity=2)
