#!/usr/bin/python3
"""T6 original virtual/callback calls, receiver thunks and cache controls; no models."""
import copy
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import elf
from capabilities.indirect_calls import artifacts, codegen, contracts, evidence, reference, runtime


class IndirectCallControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = {variant: runtime.original(variant) for variant in contracts.VARIANTS}

    def target(self, variant='baseline'):
        return self.targets[variant]

    def gold(self, variant='baseline'):
        return contracts.gold(self.target(variant)['binary_sha256'], variant)

    def grade(self, value=None, variant='baseline', nonces=None):
        raw = action_cache.canonical(self.gold(variant) if value is None else value)
        return runtime.grade(raw, self.target(variant), nonces=nonces)

    def assert_success(self, result):
        failures = [(r['nonce'], r['direction'], r['observation']['status'])
                    for r in result['checks'] if not r['passed']]
        self.assertTrue(result['task_success'], (result['status'], failures[:3]))

    def assert_both_fail(self, result):
        self.assertTrue(result['artifact_valid'])
        self.assertFalse(result['task_success'])
        self.assertEqual({r['direction'] for r in result['checks'] if not r['passed']},
                         {'original_caller_to_replacement', 'bridge_to_original_callee'})

    def test_01_stripped_originals_retain_distinct_frozen_boundaries(self):
        self.assertNotEqual(self.target()['binary_sha256'], self.target('reordered')['binary_sha256'])
        for target in self.targets.values():
            private, public = elf.stripped_matches(bytes.fromhex(target['private_hex']),
                                                   bytes.fromhex(target['public_hex']))
            self.assertEqual(private.allocated(), public.allocated())
            self.assertEqual(len(set(target['entries'].values())), 4)
            self.assertIn('original_leaf_0', private.symbols())
            self.assertIn('original_leaf_1', private.symbols())
            runtime.validate_target(target)

    def test_02_native_emulator_and_independent_python_reference(self):
        count = 0
        for target in self.targets.values():
            native = runtime.native(target)
            for row in native['observations']:
                actual = runtime.observe(bytes.fromhex(target['private_hex']), 'original_reference', row['nonce'])
                expected = reference.observation(row['nonce']).hex()
                self.assertTrue(actual['returned'])
                self.assertEqual(actual['status'], 0)
                self.assertEqual(actual['output_hex'], expected)
                self.assertEqual(row['output_hex'], expected)
                count += 1
        self.assertEqual(count, 56)

    def test_03_gold_all_vectors_both_original_directions(self):
        for variant in contracts.VARIANTS:
            result = self.grade(variant=variant)
            self.assert_success(result)
            self.assertEqual(result['total'], 56)
            self.assertEqual(result['passed'], result['total'])
            self.assertEqual(result['capability_ids'], ['T6'])
            self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')

    def test_04_operation_order_has_no_semantic_meaning(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant)
            value['operations'].reverse()
            self.assert_success(self.grade(value, variant))

    def test_05_wrong_virtual_slot_fails_actual_indirect_calls(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant)
            for operation in value['operations']:
                operation['virtual_slot'] = (operation['virtual_slot'] + 1) % 4
            result = self.grade(value, variant, [0, 1, 20, 21])
            self.assertTrue(result['artifact_valid'])
            self.assertFalse(result['task_success'])
            self.assertEqual({r['direction'] for r in result['checks'] if not r['passed']},
                             {'original_caller_to_replacement'})

    def test_06_wrong_this_adjustment_fails_original_implementation_calls(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant)
            for operation in value['operations']:
                operation['this_adjustment'] = 0
            result = self.grade(value, variant, [2, 3, 22, 23])
            self.assertTrue(result['artifact_valid'])
            self.assertFalse(result['task_success'])
            self.assertEqual({r['direction'] for r in result['checks'] if not r['passed']},
                             {'original_caller_to_replacement'})

    def test_07_wrong_receiver_relationship_fails_both_endpoints(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant)
            for operation in value['operations']:
                operation['receiver_slot'] ^= 1
            self.assert_both_fail(self.grade(value, variant, [0, 1, 2, 3]))

    def test_08_wrong_function_pointer_relationship_fails_both_endpoints(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant)
            for operation in value['operations']:
                operation['callback_slot'] ^= 1
            self.assert_both_fail(self.grade(value, variant, [0, 1, 2, 3]))

    def test_09_wrong_callback_context_fails_both_endpoints(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant)
            for operation in value['operations']:
                operation['context_slot'] ^= 1
            self.assert_both_fail(self.grade(value, variant, [0, 1, 2, 3]))

    def test_10_blind_cross_layout_descriptions_fail(self):
        for actual, described in (('baseline', 'reordered'), ('reordered', 'baseline')):
            value = contracts.gold(self.target(actual)['binary_sha256'], described)
            self.assert_both_fail(self.grade(value, actual, [0, 1, 2, 3]))

    def test_11_null_receiver_callback_and_context_preserve_state(self):
        for variant in contracts.VARIANTS:
            result = self.grade(variant=variant, nonces=list(range(4, 16)))
            self.assert_success(result)
            for row in result['checks']:
                output = bytes.fromhex(row['observation']['output_hex'])
                self.assertEqual(output[:8], ((-9) % 2**64).to_bytes(8, 'little'))
                self.assertEqual(output[-16:], b'\x01' + b'\0' * 7 + b'\x01' + b'\0' * 7)

    def test_12_real_nonprimary_base_adjustment_thunks_exist(self):
        for variant, adjustment in (('baseline', 16), ('reordered', 32)):
            image = elf.ELF(bytes.fromhex(self.target(variant)['private_hex']))
            symbols = image.symbols()
            for operation in ('apply_a', 'apply_b'):
                names = [n for n in symbols if n.startswith('_ZThn' + str(adjustment) + '_')
                         and operation in n]
                self.assertEqual(len(names), 1, (variant, operation, names))
                symbol = symbols[names[0]]
                raw = image.bytes_at(symbol['rva'], symbol['size'])
                # This pinned GCC image emits an actual receiver subtraction
                # followed by a relative branch to the overriding method.
                self.assertEqual(raw[:4], bytes((0x48, 0x83, 0xef, adjustment)))
                if raw[4] == 0xe9:
                    destination = symbol['rva'] + 9 + int.from_bytes(raw[5:9], 'little', signed=True)
                elif raw[4] == 0xeb:
                    destination = symbol['rva'] + 6 + int.from_bytes(raw[5:6], 'little', signed=True)
                else:
                    self.fail('Pinned compiler thunk has no witnessed direct branch')
                implementations = [v['rva'] for n, v in symbols.items()
                                   if n.startswith('_ZN7Service') and operation in n]
                self.assertIn(destination, implementations)

    def test_13_malformed_descriptions_never_launch_guest(self):
        invalid = [b'[]', b'\xff', b'{"schema_version":1,"schema_version":1}']
        for defect in ('bool', 'duplicate-operation', 'duplicate-receiver', 'duplicate-callback',
                       'duplicate-context', 'slot', 'adjustment', 'alignment', 'missing', 'source'):
            value = self.gold()
            first, second = value['operations']
            if defect == 'bool': first['operation'] = True
            elif defect == 'duplicate-operation': second['operation'] = first['operation']
            elif defect.startswith('duplicate-'):
                name = defect.removeprefix('duplicate-') + '_slot'
                second[name] = first[name]
            elif defect == 'slot': first['virtual_slot'] = 4
            elif defect == 'adjustment': first['this_adjustment'] = -136
            elif defect == 'alignment': first['this_adjustment'] = -1
            elif defect == 'missing': value['operations'].pop()
            else: value['source'] = 'not permitted'
            invalid.append(action_cache.canonical(value))
        for raw in invalid:
            with self.subTest(raw=raw[:40]), patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
                result = runtime.grade(raw, self.target(), nonces=[0])
                self.assertFalse(result['artifact_valid'])
                self.assertEqual(result['return_provenance'], 'not_executed')

    def test_14_wrong_binary_is_format_valid_but_unexecuted(self):
        value = self.gold()
        value['binary_sha256'] = '0' * 64
        with patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
            result = self.grade(value, nonces=[0])
        self.assertTrue(result['artifact_valid'])
        self.assertEqual(result['status'], 'incorrect_description')
        self.assertFalse(result['task_success'])
        self.assertEqual(result['return_provenance'], 'not_executed')

    def test_15_missing_production_gate_prevents_worker_launch(self):
        with patch.object(evidence, '_controls') as state, \
             patch.object(evidence, 'RECEIPT', runtime.ROOT / '.cache/missing-indirect-controls.json'), \
             patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
            state.get.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'controls missing or stale'):
                self.grade(nonces=[0])

    def test_16_endpoint_object_and_runtime_tampering_fail_admission(self):
        for defect in ('entry', 'object', 'rehash', 'runtime', 'family', 'build'):
            target = copy.deepcopy(self.target())
            if defect == 'entry': target['entries']['original_callee'] += 1
            elif defect in ('object', 'rehash'):
                raw = bytes.fromhex(target['original_obj_hex'])
                raw = bytes([raw[0] ^ 1]) + raw[1:]
                target['original_obj_hex'] = raw.hex()
                if defect == 'rehash': target['original_obj_sha256'] = runtime.sha(raw)
            elif defect == 'runtime': target['runtime']['compiler_image'] = '0' * 64
            elif defect == 'family': target['family'] = 'other'
            else: target['build_id'] = '0' * 64
            with self.subTest(defect=defect), patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')):
                with self.assertRaises(ValueError):
                    runtime.grade(action_cache.canonical(self.gold()), target, nonces=[0])

    def test_17_host_generator_patch_cannot_replace_closed_generation(self):
        value = self.gold()
        for operation in value['operations']:
            operation['callback_slot'] ^= 1
        with patch.object(codegen, 'generate', return_value=codegen.generate(self.gold())):
            result = runtime.grade(action_cache.canonical(value) + b'\n', self.target(), nonces=[0, 1])
        self.assert_both_fail(result)

    def test_18_parent_cache_hits_launch_no_compiler_or_emulator(self):
        first = self.grade()
        with patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
            second = self.grade()
            self.assertTrue(runtime.original()['cache_hit'])
            self.assertTrue(runtime.native(self.target())['cache_hit'])
            self.assertTrue(runtime.probe(action_cache.canonical(self.gold()), self.target())['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])

    def test_19_vector_revision_reuses_compiler_and_guest_actions(self):
        full = self.grade()
        with patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(runtime.executor, '_invoke', side_effect=AssertionError('Guest worker')):
            subset = self.grade(nonces=[0, 1, 2, 3])
        self.assert_success(subset)
        self.assertEqual(full['compile_id'], subset['compile_id'])
        self.assertNotEqual(full['grading_id'], subset['grading_id'])

    def action_keys(self):
        target = self.target()
        raw = action_cache.canonical(self.gold())
        probe = {**runtime.engine.object_request('probe', target), 'artifact_hex': raw.hex(),
                 'binary_sha256': target['binary_sha256']}
        native = {**runtime.engine.object_request('native', target), 'nonces': list(contracts.NONCES)}
        return (runtime.compile_action('original', {'mode': 'original'}, 'baseline')[0].key,
                runtime.compile_action('probe', probe)[0].key,
                runtime.compile_action('native', native)[0].key,
                runtime.grade_action(raw, target, list(contracts.NONCES)).key)

    def test_20_generator_change_affects_only_dependent_actions(self):
        from capabilities.variant_objects import runtime as variants
        before = self.action_keys()
        older = variants.compile_action('original', {'mode': 'original'}, 'baseline')[0].key
        read = Path.read_bytes
        def changed(path):
            raw = read(path)
            return raw + b'\n# changed generator\n' if path == runtime.ROOT / (runtime.MODULE + 'codegen.py') else raw
        with patch.object(Path, 'read_bytes', changed):
            after = self.action_keys()
            self.assertEqual(older, variants.compile_action('original', {'mode': 'original'}, 'baseline')[0].key)
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[2], after[2])
        self.assertNotEqual(before[1], after[1])
        self.assertNotEqual(before[3], after[3])

    def test_21_unrelated_report_prices_and_unselected_header_preserve_actions(self):
        before = self.action_keys()
        read = Path.read_bytes
        unrelated = {runtime.ROOT / 'benchmarks/scripts/report.py',
                     runtime.ROOT / 'benchmarks/config/pricing-20261019.json',
                     runtime.ROOT / (runtime.PREFIX + 'reordered.hpp')}
        def changed(path):
            raw = read(path)
            return raw + b'\n# unrelated change\n' if path in unrelated else raw
        with patch.object(Path, 'read_bytes', changed):
            self.assertEqual(before, self.action_keys())

    def test_22_loaded_sources_alias_and_policy_drift_fail_before_worker(self):
        from capabilities import object_probes
        request = {'mode': 'original'}
        _, sources, platform, policy = runtime.compile_action('original', request, 'baseline')
        altered = copy.deepcopy(policy)
        altered['limits']['pipe_bytes'] += 1
        with patch.object(object_probes.images, 'image', side_effect=AssertionError('Worker')):
            for selected, settings in (('baseline', altered), ('reordered', policy)):
                with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                    runtime.engine._run(request, sources, platform, settings, selected)
        read = Path.read_bytes
        for name in (runtime.engine.HOST_SOURCE, runtime.MODULE + 'runtime.py', runtime.MODULE + 'contracts.py'):
            def changed(path):
                raw = read(path)
                return raw + b'\n# replaced loaded source\n' if path == runtime.ROOT / name else raw
            with self.subTest(source=name), patch.object(Path, 'read_bytes', changed):
                with self.assertRaisesRegex(RuntimeError, 'Loaded object runtime changed'):
                    runtime.compile_action('original', request, 'baseline')

    def test_23_late_parent_contract_drift_cannot_commit_old_grade(self):
        raw = action_cache.canonical(self.gold())
        action = runtime.grade_action(raw, self.target(), [7, 3])
        changed = action_cache.Action('capability_indirect_call_grade_v1', {'drift': b'changed'})
        self.assertIsNone(action_cache.Cache().lookup(action))
        with patch.object(runtime.engine, 'grade_action', side_effect=[action, changed]), \
             patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(runtime.executor, '_invoke', side_effect=AssertionError('Guest worker')):
            with self.assertRaisesRegex(RuntimeError, 'grading inputs changed'):
                self.grade(nonces=[7, 3])
        self.assertIsNone(action_cache.Cache().lookup(action))

    def test_24_production_gate_binds_count_sources_and_pins(self):
        valid = {'schema_version': 1, 'passed': True, 'checks': 28, 'failed': 0, 'model_api_calls': 0,
                 'inputs_sha256': evidence.fingerprints(), 'pins': evidence.pins()}
        with tempfile.TemporaryDirectory(prefix='indirect-gate-') as temporary, \
             patch.object(evidence, '_controls') as state, \
             patch.object(evidence, 'RECEIPT', Path(temporary) / 'controls.json'):
            state.get.return_value = False
            evidence.RECEIPT.write_bytes(action_cache.canonical(valid))
            evidence.require_validation()
            for defect in ('count', 'source', 'pins', 'failed', 'model'):
                value = copy.deepcopy(valid)
                if defect == 'count': value['checks'] = 27
                elif defect == 'source': value['inputs_sha256'][runtime.engine.HOST_SOURCE] = '0' * 64
                elif defect == 'pins': value['pins']['probe']['compiler_image'] = '0' * 64
                elif defect == 'failed': value['failed'] = 1
                else: value['model_api_calls'] = 1
                evidence.RECEIPT.write_bytes(action_cache.canonical(value))
                with self.subTest(defect=defect), self.assertRaisesRegex(RuntimeError, 'controls missing or stale'):
                    evidence.require_validation()

    def test_25_vector_and_artifact_transport_bounds_fail_closed(self):
        for value in ([], [True], [-1], [2**64], [0, 0], [[0]], list(range(33))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.vectors(value)
        with patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')):
            for raw in (b'', None, b'x' * 262145):
                with self.assertRaises(ValueError):
                    runtime.grade(raw, self.target(), nonces=[0])

    def test_26_cold_native_calibration_is_independent_of_generator(self):
        source = action_cache.Cache()
        action = runtime.compile_action('original', {'mode': 'original'}, 'baseline')[0]
        saved = source.lookup(action)
        outputs = {name: source.get(record['digest']) for name, record in saved['output_files'].items()}
        nonce = 0x123456789abcdef2
        while (nonce >> 2) % 5 in (1, 2, 3):
            nonce += 4
        with tempfile.TemporaryDirectory(prefix='indirect-native-independent-') as temporary:
            local = action_cache.Cache(temporary)
            local.commit(action, outputs)
            with patch.object(codegen, 'generate', side_effect=AssertionError('Candidate generator')), \
                 patch.object(runtime.engine, '_run', wraps=runtime.engine._run) as worker:
                result = runtime.native(self.target(), nonces=[nonce], cache=local)
            self.assertFalse(result['cache_hit'])
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(worker.call_args.args[0]['mode'], 'native')
        self.assertEqual(result['observations'][0]['output_hex'], reference.observation(nonce).hex())
        self.assertEqual(result['original_obj_sha256'], self.target()['original_obj_sha256'])

    def test_27_closed_source_closures_exclude_private_answers(self):
        original = set(runtime.sources('original', 'baseline'))
        probe = set(runtime.sources('probe'))
        native = set(runtime.sources('native'))
        for names in (original, probe, native):
            self.assertNotIn(runtime.MODULE + 'contracts.py', names)
            self.assertNotIn(runtime.MODULE + 'reference.py', names)
        self.assertNotIn(runtime.PREFIX + 'reordered.hpp', original)
        self.assertNotIn(runtime.MODULE + 'codegen.py', original | native)
        self.assertNotIn(runtime.PREFIX + 'original.cpp', probe | native)
        self.assertNotIn(runtime.PREFIX + 'baseline.hpp', probe | native)

    def test_28_single_operation_defects_remain_partial_and_not_success(self):
        for name, index, nonce in (('this_adjustment', 0, 2), ('virtual_slot', 1, 1)):
            value = self.gold()
            value['operations'][index][name] = 0 if name == 'this_adjustment' else 2
            result = self.grade(value, nonces=[0, 1, 2, 3])
            self.assertTrue(result['artifact_valid'])
            self.assertFalse(result['task_success'])
            self.assertEqual((result['passed'], result['total']), (7, 8))
            failed = [(r['nonce'], r['direction']) for r in result['checks'] if not r['passed']]
            self.assertEqual(failed, [(nonce, 'original_caller_to_replacement')])


def main():
    started = evidence.begin()
    with evidence.validation_controls(), patch.object(socket.socket, 'connect', side_effect=AssertionError('Network/model')):
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(IndirectCallControls))
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__':
    main()
