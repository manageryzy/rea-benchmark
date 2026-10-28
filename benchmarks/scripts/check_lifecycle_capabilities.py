#!/usr/bin/python3
"""Explicit C5/C6 lifecycle controls against the actual stripped originals."""
import copy
import json
import unittest
from unittest.mock import patch

import action_cache
from capabilities import build, lifecycle, lifecycle_evidence
from grading import offline
from grading.c_executor import runtime as executor


class LifecycleControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = action_cache.Cache()
        cls.targets, cls.scenarios, cls.oracles, cls.sources = {}, {}, {}, {}
        for family in lifecycle.FAMILIES:
            cls.targets[family] = lifecycle.original(family, cache=cls.cache)
            cls.scenarios[family] = lifecycle.vectors(family)
            cls.oracles[family] = lifecycle.reference(cls.targets[family], cls.scenarios[family], cache=cls.cache)
            cls.sources[family] = (build.SUITE / 'controls/lifecycle' / (family + '.c')).read_text()

    def grade(self, family, *, source=None, scenarios=None):
        selected = self.scenarios[family] if scenarios is None else scenarios
        oracle = self.oracles[family] if scenarios is None else lifecycle.reference(self.targets[family], selected, cache=self.cache)
        return lifecycle.grade(self.sources[family] if source is None else source, family, selected, oracle, cache=self.cache)

    def mutate(self, family, old, new):
        self.assertIn(old, self.sources[family])
        return self.sources[family].replace(old, new)

    def test_01_gold_recovery_against_both_actual_originals(self):
        for family in lifecycle.FAMILIES:
            with self.subTest(family=family):
                result = self.grade(family)
                self.assertEqual(result['passed'], result['total'], result)
                self.assertTrue(result['compiled'])
                self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')
                self.assertEqual(set(result['capabilities']), set(lifecycle.FAMILIES[family]))

    def test_02_private_state_layout_and_inactive_bytes_are_not_scored(self):
        source = self.mutate('session_state', 'context.alive = 0;',
            'context.alive = 0; context.checksum = 0xaabbccdd; context.count = 123;')
        result = self.grade('session_state', source=source)
        self.assertEqual(result['passed'], result['total'])

    def test_03_structurally_equivalent_arithmetic(self):
        source = self.mutate('session_state',
            'context.checksum * context.multiplier + value + context.count',
            'value + context.count + context.checksum * context.multiplier')
        self.assertEqual((result := self.grade('session_state', source=source))['passed'], result['total'])

    def test_04_wrong_callback_argument_order_fails(self):
        source = self.mutate('callback_policy', 'callback(operation, first, second)', 'callback(operation, second, first)')
        result = self.grade('callback_policy', source=source, scenarios=self.scenarios['callback_policy'][:1])
        self.assertLess(result['passed'], result['total'])
        self.assertTrue(any(not row['components'].get('callbacks', True) for row in result['checks']))

    def test_05_wrong_callback_count_fails(self):
        source = self.mutate('callback_policy', 'uint64_t response = callback(operation, first, second);',
            'callback(operation, first, second); uint64_t response = callback(operation, first, second);')
        result = self.grade('callback_policy', source=source, scenarios=self.scenarios['callback_policy'][:1])
        self.assertTrue(any(row.get('failure') == 'callback_budget' for row in result['checks']))
        self.assertLess(result['passed'], result['total'])

    def test_06_failed_callback_does_not_commit_state(self):
        source = self.mutate('callback_policy', 'if (response == UINT64_MAX) return -3;',
            'if (response == UINT64_MAX) { context.alive = 1; context.remaining = 0; '
            'memcpy(storage, &context, sizeof(context)); return -3; }')
        result = self.grade('callback_policy', source=source, scenarios=self.scenarios['callback_policy'][1:2])
        self.assertLess(result['passed'], result['total'])
        self.assertTrue(any(not row['components'].get('status', True) for row in result['checks']))

    def test_07_error_path_must_preserve_output(self):
        source = self.mutate('callback_policy', 'if (response == UINT64_MAX) return -3;',
            'if (response == UINT64_MAX) { *output = 0; return -3; }')
        result = self.grade('callback_policy', source=source, scenarios=self.scenarios['callback_policy'][1:2])
        self.assertTrue(any(not row['components'].get('output_bytes', True) for row in result['checks']))

    def test_08_destroy_and_recreate_are_observed(self):
        source = self.mutate('session_state', 'context.alive = 0;', 'context.alive = 1;')
        result = self.grade('session_state', source=source, scenarios=self.scenarios['session_state'][:1])
        self.assertTrue(any(not row['components'].get('status', True) for row in result['checks']))

    def test_09_global_cross_instance_state_fails(self):
        source = self.mutate('session_state', 'Context context;\n    memcpy(&context, storage, sizeof(context));',
            'static Context saved;\n    Context context = saved;')
        source = source.replace('memcpy(storage, &context, sizeof(context));', 'saved = context;')
        result = self.grade('session_state', source=source, scenarios=self.scenarios['session_state'][:1])
        self.assertLess(result['passed'], result['total'])
        self.assertTrue(any(not row['components'].get('status', True) for row in result['checks']))

    def test_10_reset_must_clear_count(self):
        source = self.mutate('session_state', 'context.count = 0;', 'if (operation == 0) context.count = 0;')
        result = self.grade('session_state', source=source)
        self.assertTrue(any(not row['components'].get('output_bytes', True) for row in result['checks']))

    def test_11_guard_and_inactive_instance_writes_fail(self):
        source = self.mutate('session_state', '(void)callback;', '(void)callback; storage[-1] = 0;')
        result = self.grade('session_state', source=source, scenarios=self.scenarios['session_state'][:1])
        self.assertEqual(result['passed'], 0)
        self.assertEqual(result['checks'][0]['failure'], 'forbidden_memory_write')

    def test_12_current_worker_input_excludes_oracle_target_and_future(self):
        scenario = self.scenarios['callback_policy'][0]
        request = lifecycle.invocation(scenario['steps'][0], lifecycle.initial(scenario['steps'][0]['seed']))
        self.assertEqual(set(request), {'id', 'return_type', 'regions', 'arguments', 'callbacks'})
        self.assertEqual(len(request['regions']), 1)
        self.assertEqual(request['callbacks'], [{'id': 'policy', 'returns': [scenario['steps'][0]['response']]}])
        self.assertEqual(len(bytes.fromhex(request['regions'][0]['data_hex'])), 512)
        self.assertNotIn('oracle', json.dumps(request))

    def test_13_changed_private_vectors_cannot_borrow_an_oracle(self):
        changed = copy.deepcopy(self.scenarios['session_state'])
        changed[0]['steps'][0]['value'] ^= 1
        with self.assertRaisesRegex(ValueError, 'vectors'):
            lifecycle.grade(self.sources['session_state'], 'session_state', changed, self.oracles['session_state'], cache=self.cache)

    def test_14_cached_grades_do_not_compile_or_invoke(self):
        first = self.grade('session_state')
        with patch.object(executor, 'compile', side_effect=AssertionError('Duplicate compile')), \
             patch.object(executor, 'invoke', side_effect=AssertionError('Duplicate VM')):
            second = self.grade('session_state')
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])

    def test_15_scenario_reset_does_not_import_previous_guest_globals(self):
        # Inspect genuine gold calls, using cached machine executions. A forced
        # parent aggregation miss must not carry an earlier scenario's globals.
        invoke, lookup = executor.invoke, self.cache.lookup
        states = []
        def observed(*args, **kwargs):
            states.append(kwargs.get('state'))
            return invoke(*args, **kwargs)
        def parent_miss(action):
            return None if action.descriptor['kind'] == 'capability_lifecycle_grade_v1' else lookup(action)
        with patch.object(self.cache, 'lookup', side_effect=parent_miss), patch.object(executor, 'invoke', side_effect=observed):
            result = self.grade('session_state')
        self.assertEqual(result['passed'], result['total'])
        starts, index = [], 0
        for scenario in self.scenarios['session_state']:
            starts.append(index)
            index += len(scenario['steps'])
        self.assertEqual(len(states), index)
        self.assertEqual([index for index, state in enumerate(states) if state is None], starts)

    def test_16_strict_scenario_contract(self):
        for change in ('duplicate', 'oracle', 'bool', 'negative'):
            value = copy.deepcopy(self.scenarios['session_state'])
            if change == 'duplicate': value[0]['steps'][1]['id'] = value[0]['steps'][0]['id']
            if change == 'oracle': value[0]['steps'][0]['expected'] = 0
            if change == 'bool': value[0]['steps'][0]['instance'] = False
            if change == 'negative': value[0]['steps'][0]['value'] = -1
            with self.subTest(change=change), self.assertRaises(ValueError): lifecycle.validate(value)

    def test_17_unrelated_report_change_does_not_invalidate_grade(self):
        first = lifecycle.grading_action(self.sources['session_state'], 'session_state', self.scenarios['session_state'], self.oracles['session_state'])
        from pathlib import Path
        real = Path.read_bytes
        def changed(path):
            raw = real(path)
            return raw + b'\n# unrelated report revision\n' if path.name == 'report.py' else raw
        with patch.object(Path, 'read_bytes', changed):
            second = lifecycle.grading_action(self.sources['session_state'], 'session_state', self.scenarios['session_state'], self.oracles['session_state'])
        self.assertEqual(first.key, second.key)

    def test_18_existing_buffer_target_actions_keep_their_cached_identity(self):
        from pathlib import Path
        records = [json.loads(path.read_bytes()) for path in
                   (action_cache.ROOT / '.cache/benchmark-capabilities/task-index').glob('*.json')]
        for family in json.loads((build.SUITE / 'config/families.json').read_bytes())['families']:
            original = build.build(family['id'], cache=self.cache)
            self.assertTrue(original['cache_hit'])
            expected = {row['build_id'] for row in records
                        if row['family_id'] == family['id'] and row['kind'] in ('c_buffer', 'cpp_buffer')}
            self.assertEqual(expected, {original['build_id']})

    def test_19_original_oracle_cache_does_not_execute_the_target_again(self):
        for family in lifecycle.FAMILIES:
            with patch.object(lifecycle.sandbox, 'image', side_effect=AssertionError('Repeated original execution')):
                result = lifecycle.reference(self.targets[family], self.scenarios[family], cache=self.cache)
            self.assertTrue(result['cache_hit'])
            self.assertEqual(result['reference_id'], self.oracles[family]['reference_id'])

    def test_20_new_builder_changes_do_not_invalidate_existing_abi_builds(self):
        from pathlib import Path
        from capabilities.abi import runtime as abi
        first = abi.original_action('record_codec', 'sysv_amd64')[0]
        real = Path.read_bytes
        def changed(path):
            raw = real(path)
            return raw + b'\n# unrelated lifecycle build change\n' if path.name == 'lifecycle_build.py' else raw
        with patch.object(Path, 'read_bytes', changed):
            second = abi.original_action('record_codec', 'sysv_amd64')[0]
        self.assertEqual(first.key, second.key)
        self.assertIsNotNone(self.cache.lookup(first))

    def test_21_callback_and_state_components_have_distinct_scores(self):
        source = self.mutate('callback_policy', 'callback(operation, first, second)', 'callback(operation, second, first)')
        result = self.grade('callback_policy', source=source, scenarios=self.scenarios['callback_policy'][:1])
        self.assertLess(result['capabilities']['C5']['passed'], result['capabilities']['C5']['total'])
        self.assertEqual(result['capabilities']['C6']['passed'], result['capabilities']['C6']['total'])
        self.assertLess(result['passed'], result['total'])

    def test_22_production_admission_requires_complete_current_controls(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / 'receipt.json'
            sources, pins = {'fixture': 'a' * 64}, {'fixture': 'image'}
            valid = {'schema_version': 1, 'passed': True, 'model_api_calls': 0,
                     'checks': 22, 'failed': 0, 'inputs_sha256': sources, 'pins': pins}
            token = lifecycle_evidence._controls.set(False)
            try:
                with patch.object(lifecycle_evidence, 'RECEIPT', receipt), \
                     patch.object(lifecycle_evidence, 'fingerprints', return_value=sources), \
                     patch.object(lifecycle_evidence, 'pins', return_value=pins):
                    for key, wrong in [('passed', False), ('checks', 0), ('checks', True), ('checks', 21),
                                       ('failed', 1), ('model_api_calls', 1), ('inputs_sha256', {}), ('pins', {})]:
                        receipt.write_bytes(action_cache.canonical({**valid, key: wrong}))
                        with self.subTest(field=key, value=wrong), self.assertRaises(RuntimeError):
                            lifecycle_evidence.require_validation()
                    receipt.write_bytes(action_cache.canonical(valid))
                    lifecycle_evidence.require_validation()
            finally:
                lifecycle_evidence._controls.reset(token)


def main():
    started = lifecycle_evidence.begin()
    with lifecycle_evidence.validation_controls(), offline.guard():
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(LifecycleControls)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    lifecycle_evidence.finish(started, result)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
