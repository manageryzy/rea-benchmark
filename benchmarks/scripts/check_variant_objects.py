#!/usr/bin/python3
"""T4 original-endpoint, union/subobject and cache controls; zero model calls."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import elf
from capabilities.variant_objects import artifacts, codegen, contracts, evidence, reference, runtime


class VariantObjectControls(unittest.TestCase):
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

    def alternatives(self, value):
        return {row['role']: row for row in value['payload']['alternatives']}

    def assert_both_fail(self, value):
        self.assertTrue(value['artifact_valid'])
        self.assertFalse(value['task_success'])
        self.assertEqual({row['direction'] for row in value['checks'] if not row['passed']},
                         {'original_caller_to_replacement', 'bridge_to_original_callee'})

    def test_01_stripped_originals_retain_distinct_frozen_boundaries(self):
        self.assertNotEqual(self.target()['binary_sha256'], self.target('reordered')['binary_sha256'])
        for target in self.targets.values():
            private, public = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
            self.assertEqual(private.allocated(), public.allocated())
            self.assertEqual(len(set(target['entries'].values())), 4)
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
        self.assertEqual(count, 32)

    def test_03_gold_all_vectors_both_original_directions(self):
        for variant in contracts.VARIANTS:
            result = self.grade(variant=variant)
            self.assertTrue(result['task_success'], result)
            self.assertEqual(result['passed'], result['total'])
            self.assertEqual(result['total'], 32)
            self.assertEqual(result['capability_ids'], ['T4'])
            self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')

    def test_04_equivalent_order_alignment_and_unused_tail(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant)
            value['subobjects'].reverse()
            value['payload']['alternatives'].reverse()
            value.update(size=64, alignment=16)
            # The reordered tag follows the payload: unused tail cannot claim
            # to overlap that live field. Root tail extent is free in both.
            if variant == 'baseline': value['payload']['extent'] = 16
            self.assertTrue(self.grade(value, variant)['task_success'])

    def test_05_wrong_active_alternative_tags_fail_real_calls(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant); a = self.alternatives(value)
            a['number']['tag'], a['pair']['tag'] = a['pair']['tag'], a['number']['tag']
            self.assert_both_fail(self.grade(value, variant, [0, 1, 3, 4]))

    def test_06_wrong_base_offsets_fail_real_subobject_access(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant); a, b = value['subobjects']
            a['offset'], b['offset'] = b['offset'], a['offset']
            self.assert_both_fail(self.grade(value, variant, [0, 1, 4]))

    def test_07_wrong_union_payload_offset_is_observable(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant); value['size'] = 64
            value['payload']['offset'] = 40
            self.assert_both_fail(self.grade(value, variant, [0, 1]))

    def test_08_wrong_pair_member_order_is_observable(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant); pair = self.alternatives(value)['pair']
            pair['first_offset'], pair['second_offset'] = pair['second_offset'], pair['first_offset']
            self.assert_both_fail(self.grade(value, variant, [1, 4]))

    def test_09_wrong_number_member_offset_corrupts_active_value_or_guards(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant)
            value['size'] = 64; value['payload']['extent'] = 12
            self.alternatives(value)['number']['offset'] = 4
            self.assert_both_fail(self.grade(value, variant, [0, 3]))

    def test_10_blind_cross_layout_descriptions_fail_in_both_directions(self):
        for actual, described in (('baseline', 'reordered'), ('reordered', 'baseline')):
            value = contracts.gold(self.target(actual)['binary_sha256'], described)
            self.assert_both_fail(self.grade(value, actual, [0, 1, 4]))

    def test_11_unknown_tag_preservation_and_complementary_directions(self):
        for variant in contracts.VARIANTS:
            self.assertTrue(self.grade(variant=variant, nonces=[2, 8])['task_success'])
            value = self.gold(variant)
            self.alternatives(value)['number']['tag'] = 0xffffffff
            result = self.grade(value, variant, [2])
            checks = {row['direction']: row['passed'] for row in result['checks']}
            self.assertFalse(checks['original_caller_to_replacement'])
            self.assertTrue(checks['bridge_to_original_callee'])
            self.assertFalse(result['task_success'])

    def test_12_malformed_descriptions_never_launch_the_guest(self):
        invalid = [b'[]', b'\xff', b'{"schema_version":1,"schema_version":1}']
        for defect in ('bool', 'overlap', 'duplicate-tags', 'pair-overlap', 'missing', 'code', 'extent', 'alignment'):
            value = self.gold()
            if defect == 'bool': value['tag_offset'] = True
            elif defect == 'overlap': value['subobjects'][0]['offset'] = value['subobjects'][1]['offset']
            elif defect == 'duplicate-tags':
                self.alternatives(value)['number']['tag'] = self.alternatives(value)['pair']['tag']
            elif defect == 'pair-overlap':
                pair = self.alternatives(value)['pair']; pair['first_offset'] = pair['second_offset']
            elif defect == 'missing': value['subobjects'].pop()
            elif defect == 'extent': value['payload']['extent'] = 1000
            elif defect == 'alignment': value['alignment'] = True
            else: value['source'] = 'not allowed'
            invalid.append(action_cache.canonical(value))
        for raw in invalid:
            with self.subTest(raw=raw[:40]), patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
                result = runtime.grade(raw, self.target(), nonces=[0])
                self.assertFalse(result['artifact_valid'])
                self.assertEqual(result['return_provenance'], 'not_executed')

    def test_13_wrong_binary_is_format_valid_but_not_executed(self):
        value = self.gold(); value['binary_sha256'] = '0' * 64
        with patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
            result = self.grade(value, nonces=[0])
        self.assertTrue(result['artifact_valid'])
        self.assertEqual(result['status'], 'incorrect_description')
        self.assertFalse(result['task_success'])
        self.assertEqual(result['return_provenance'], 'not_executed')

    def test_14_missing_production_gate_fails_before_any_worker(self):
        with patch.object(evidence, '_controls') as state, \
             patch.object(evidence, 'RECEIPT', runtime.ROOT / '.cache/missing-variant-controls.json'), \
             patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
            state.get.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'controls missing or stale'):
                self.grade(nonces=[0])

    def test_15_host_generator_monkeypatch_does_not_change_closed_generation(self):
        value = self.gold(); a, b = value['subobjects']
        a['offset'], b['offset'] = b['offset'], a['offset']
        with patch.object(codegen, 'generate', return_value=codegen.generate(self.gold())):
            result = runtime.grade(action_cache.canonical(value) + b'\n', self.target(), nonces=[0, 1])
        self.assert_both_fail(result)

    def test_16_endpoint_object_rehash_and_runtime_tampering_fail_admission(self):
        for defect in ('object', 'rehashed-object', 'entry', 'build', 'runtime', 'family'):
            target = copy.deepcopy(self.target())
            if defect in ('object', 'rehashed-object'):
                target['original_obj_hex'] = '00' + target['original_obj_hex'][2:]
                if defect == 'rehashed-object':
                    target['original_obj_sha256'] = runtime.sha(bytes.fromhex(target['original_obj_hex']))
            elif defect == 'entry': target['entries']['original_callee'] += 1
            elif defect == 'build': target['build_id'] = '0' * 64
            elif defect == 'family': target['family'] = 'other'
            else: target['runtime']['policy_sha256'] = '0' * 64
            with self.subTest(defect=defect), \
                 patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
                 patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
                with self.assertRaises(ValueError):
                    runtime.grade(action_cache.canonical(self.gold()), target, nonces=[0])

    def test_17_parent_cache_hits_launch_neither_compiler_nor_emulator(self):
        first = self.grade()
        with patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest')):
            second = self.grade()
            self.assertTrue(runtime.original()['cache_hit'])
            self.assertTrue(runtime.native(self.target())['cache_hit'])
            self.assertTrue(runtime.probe(action_cache.canonical(self.gold()), self.target())['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])

    def test_18_private_vector_revision_reuses_compiler_and_guest_actions(self):
        full = self.grade()
        with patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(runtime.executor, '_invoke', side_effect=AssertionError('Guest worker')):
            subset = self.grade(nonces=[0, 1])
        self.assertTrue(subset['task_success'])
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

    def test_19_generator_changes_affect_only_dependent_actions(self):
        from capabilities.pointers import runtime as pointers
        from capabilities.abi import runtime as abi
        before = self.action_keys()
        old_pointer = pointers.compile_action('original', {'mode': 'original'}, 'baseline')[0].key
        old_abi = abi.original_action('record_codec', 'sysv_amd64')[0].key
        read = Path.read_bytes
        def changed(path):
            raw = read(path)
            return raw + b'\n# generator control\n' if path == runtime.ROOT / (runtime.MODULE + 'codegen.py') else raw
        with patch.object(Path, 'read_bytes', changed):
            after = self.action_keys()
            self.assertEqual(old_pointer, pointers.compile_action('original', {'mode': 'original'}, 'baseline')[0].key)
            self.assertEqual(old_abi, abi.original_action('record_codec', 'sysv_amd64')[0].key)
        self.assertEqual(before[0], after[0]); self.assertEqual(before[2], after[2])
        self.assertNotEqual(before[1], after[1]); self.assertNotEqual(before[3], after[3])

    def test_20_report_prices_and_unselected_header_do_not_change_actions(self):
        before = self.action_keys(); read = Path.read_bytes
        unrelated = {runtime.ROOT / 'benchmarks/scripts/report.py',
                     runtime.ROOT / 'benchmarks/config/pricing-20261019.json',
                     runtime.ROOT / (runtime.PREFIX + 'reordered.hpp')}
        def changed(path):
            raw = read(path)
            return raw + b'\n# unrelated control\n' if path in unrelated else raw
        with patch.object(Path, 'read_bytes', changed):
            self.assertEqual(before, self.action_keys())

    def test_21_source_alias_and_policy_drift_fail_before_worker_launch(self):
        request = {'mode': 'original'}
        _, sources, platform, policy = runtime.compile_action('original', request, 'baseline')
        altered = copy.deepcopy(policy); altered['limits']['pipe_bytes'] += 1
        from capabilities import object_probes
        with patch.object(object_probes.images, 'image', side_effect=AssertionError('Worker')):
            with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                runtime.engine._run(request, sources, platform, altered, 'baseline')
            with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                runtime.engine._run(request, sources, platform, policy, 'reordered')
        read = Path.read_bytes
        for changed_source in (runtime.engine.HOST_SOURCE, runtime.MODULE + 'runtime.py', runtime.MODULE + 'contracts.py'):
            def replaced_host(path):
                raw = read(path)
                return raw + b'\n# replaced loaded source\n' if path == runtime.ROOT / changed_source else raw
            with self.subTest(changed_source=changed_source), patch.object(Path, 'read_bytes', replaced_host):
                with self.assertRaisesRegex(RuntimeError, 'Loaded object runtime changed'):
                    runtime.compile_action('original', request, 'baseline')
        # A late parent-contract change must not publish a grade under its old
        # action, even when all child compiler/VM observations are cached.
        raw = action_cache.canonical(self.gold())
        action = runtime.grade_action(raw, self.target(), [9, 3])
        changed = action_cache.Action('capability_variant_object_grade_v1', {'drift': b'changed'})
        self.assertIsNone(action_cache.Cache().lookup(action))
        with patch.object(runtime.engine, 'grade_action', side_effect=[action, changed]), \
             patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(runtime.executor, '_invoke', side_effect=AssertionError('Guest worker')):
            with self.assertRaisesRegex(RuntimeError, 'grading inputs changed'):
                self.grade(nonces=[9, 3])
        self.assertIsNone(action_cache.Cache().lookup(action))

    def test_22_production_gate_checks_count_source_and_runtime_pins(self):
        valid = {'schema_version': 1, 'passed': True, 'checks': 24, 'failed': 0, 'model_api_calls': 0,
                 'inputs_sha256': evidence.fingerprints(), 'pins': evidence.pins()}
        with tempfile.TemporaryDirectory(prefix='variant-gate-') as temporary, \
             patch.object(evidence, '_controls') as state, \
             patch.object(evidence, 'RECEIPT', Path(temporary) / 'controls.json'):
            state.get.return_value = False
            evidence.RECEIPT.write_bytes(action_cache.canonical(valid))
            evidence.require_validation()
            for defect in ('count', 'source', 'pins', 'failed', 'model'):
                value = copy.deepcopy(valid)
                if defect == 'count': value['checks'] = 23
                elif defect == 'source': value['inputs_sha256'][runtime.engine.HOST_SOURCE] = '0' * 64
                elif defect == 'pins': value['pins']['probe']['compiler_image'] = '0' * 64
                elif defect == 'failed': value['failed'] = 1
                else: value['model_api_calls'] = 1
                evidence.RECEIPT.write_bytes(action_cache.canonical(value))
                with self.subTest(defect=defect), self.assertRaisesRegex(RuntimeError, 'controls missing or stale'):
                    evidence.require_validation()

    def test_23_vector_and_artifact_transport_bounds_are_fail_closed(self):
        for value in ([], [True], [-1], [2**64], [0, 0], [[0]], list(range(33))):
            with self.subTest(value=value), self.assertRaises(ValueError): runtime.vectors(value)
        with patch.object(runtime.engine, '_run', side_effect=AssertionError('Compiler')):
            for raw in (b'', None, b'x' * 262145):
                with self.subTest(length=len(raw) if raw is not None else None), self.assertRaises(ValueError):
                    runtime.grade(raw, self.target(), nonces=[0])

    def test_24_native_calibration_is_cold_and_independent_of_candidate_generator(self):
        source = action_cache.Cache()
        action = runtime.compile_action('original', {'mode': 'original'}, 'baseline')[0]
        saved = source.lookup(action)
        outputs = {name: source.get(record['digest']) for name, record in saved['output_files'].items()}
        nonce = 0x123456789abcdef
        with tempfile.TemporaryDirectory(prefix='variant-native-independent-') as temporary:
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


def main():
    started = evidence.begin()
    with evidence.validation_controls(), patch.object(socket.socket, 'connect', side_effect=AssertionError('Network/model')):
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(VariantObjectControls))
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__': main()
