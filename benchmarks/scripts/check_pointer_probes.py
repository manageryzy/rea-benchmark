#!/usr/bin/python3
"""Offline T3 controls: real original endpoints, pointer roles and alias effects."""
import copy
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

import action_cache
from grading.artifacts import decode
from capabilities import elf
from capabilities.pointers import artifacts, codegen, contracts, evidence, runtime


def reference_bytes(nonce):
    """Reviewed independent control oracle; production grading executes originals."""
    arena = bytearray((nonce + 31*i + 0xa5) & 255 for i in range(96))
    source, destination, length, capacity = 8, 48, 8, 16
    case = nonce % 12
    if case == 1: source, destination, length, capacity = 8, 14, 12, 16
    elif case == 2: source, destination, length, capacity = 10, 6, 12, 16
    elif case == 3: source, destination, length, capacity = 8, 8, 12, 12
    elif case == 4: source, destination, length, capacity = None, None, 0, 0
    elif case == 5: length, capacity = 8, 4
    elif case == 6: length = capacity = 16
    elif case == 7: length, capacity = 3, 9
    elif case == 8: length, capacity = 3, 0
    elif case == 9: source = None
    elif case == 10: destination = None
    elif case == 11: length = capacity = 1
    status, produced = 0, 0xfedc1234
    if length > capacity or length > 32 or capacity > 32: status = -1
    elif length and (source is None or destination is None): status = -2
    else:
        saved = bytes(arena[source:source+length]) if length else b''
        for i, byte in enumerate(saved): arena[destination+i] = byte ^ ((nonce+13*i) & 255)
        produced = length
    return bytes(arena) + b''.join((value % 2**64).to_bytes(8, 'little') for value in (status, produced, 1, 1))


class PointerControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = {variant: runtime.original(variant) for variant in contracts.VARIANTS}

    def target(self, variant='baseline'):
        return self.targets[variant]

    def gold(self, variant='baseline'):
        return contracts.gold(self.target(variant)['binary_sha256'], variant)

    def fields(self, value):
        return {item['role']: item for item in value['fields']}

    def grade(self, value=None, variant='baseline', nonces=None):
        return runtime.grade(action_cache.canonical(self.gold(variant) if value is None else value),
                             self.target(variant), nonces=nonces)

    def assert_both_fail(self, result):
        self.assertTrue(result['artifact_valid'])
        self.assertFalse(result['task_success'])
        self.assertEqual({row['direction'] for row in result['checks'] if not row['passed']},
                         {'original_caller_to_replacement', 'bridge_to_original_callee'})

    def test_01_four_stripped_originals_have_frozen_independent_endpoints(self):
        self.assertNotEqual(self.target()['binary_sha256'], self.target('reordered')['binary_sha256'])
        for target in self.targets.values():
            private, public = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
            self.assertEqual(private.allocated(), public.allocated())
            self.assertEqual(len(set(target['entries'].values())), 4)
            self.assertEqual(target['original_obj_sha256'], runtime.sha(bytes.fromhex(target['original_obj_hex'])))

    def test_02_native_emulator_and_independent_oracle_agree_on_all_cases(self):
        for target in self.targets.values():
            native = runtime.native(target)
            for row in native['observations']:
                observed = runtime.observe(bytes.fromhex(target['private_hex']), 'original_reference', row['nonce'])
                expected = reference_bytes(row['nonce']).hex()
                self.assertTrue(observed['returned'])
                self.assertEqual(observed['status'], 0)
                self.assertEqual(observed['output_hex'], expected)
                self.assertEqual(row['output_hex'], expected)

    def test_03_gold_all_layouts_all_vectors_both_original_directions(self):
        for variant in contracts.VARIANTS:
            result = self.grade(variant=variant)
            self.assertTrue(result['task_success'], result)
            self.assertEqual(result['passed'], result['total'])
            self.assertEqual(result['total'], 2*len(contracts.NONCES))
            self.assertEqual(result['capability_ids'], ['T3'])
            self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')

    def test_04_equivalent_field_order_padding_and_access_supersets(self):
        for variant in contracts.VARIANTS:
            value = self.gold(variant); value['fields'].reverse()
            value.update(size=64, alignment=16)
            for field in value['fields']:
                if field['kind'] == 'pointer': field['access'] = 'read_write'
            self.assertTrue(self.grade(value, variant=variant)['task_success'])

    def test_05_swapped_buffer_roles_fail_in_both_directions(self):
        value = self.gold(); fields = self.fields(value)
        fields['source']['offset'], fields['destination']['offset'] = fields['destination']['offset'], fields['source']['offset']
        self.assert_both_fail(self.grade(value, nonces=[0, 1, 2]))

    def test_06_swapped_length_capacity_fail_real_original_accesses(self):
        value = self.gold(); fields = self.fields(value)
        fields['length']['offset'], fields['capacity']['offset'] = fields['capacity']['offset'], fields['length']['offset']
        self.assert_both_fail(self.grade(value, nonces=[0, 5, 8]))

    def test_07_wrong_written_field_detects_original_descriptor_corruption(self):
        value = self.gold(); self.fields(value)['produced']['offset'] = 28
        self.assert_both_fail(self.grade(value, nonces=[0, 1]))

    def test_08_wrong_pointer_depth_is_an_observed_task_failure(self):
        value = self.gold(); self.fields(value)['source']['indirections'] = 2
        result = self.grade(value, nonces=[0, 1, 4])
        self.assert_both_fail(result)
        self.assertEqual(result['status'], 'graded')
        self.assertTrue(any(not row['observation']['returned'] for row in result['checks']))

    def test_09_read_extent_claim_changes_real_copy_and_length_observation(self):
        value = self.gold(); self.fields(value)['source']['extent'] = 'capacity'
        self.assert_both_fail(self.grade(value, nonces=[0, 7]))

    def test_10_write_extent_claim_cannot_hide_capacity_errors(self):
        value = self.gold(); self.fields(value)['destination']['extent'] = 'length'
        self.assert_both_fail(self.grade(value, nonces=[5, 8]))

    def test_11_disjoint_claim_passes_disjoint_and_fails_overlap(self):
        value = self.gold(); value['aliasing'] = 'disjoint'
        self.assertTrue(self.grade(value, nonces=[0, 11])['task_success'])
        self.assert_both_fail(self.grade(value, nonces=[1, 2, 3]))

    def test_12_identical_only_claim_distinguishes_partial_overlap(self):
        value = self.gold(); value['aliasing'] = 'identical_or_disjoint'
        self.assertTrue(self.grade(value, nonces=[0, 3])['task_success'])
        self.assert_both_fail(self.grade(value, nonces=[1, 2]))

    def test_13_incompatible_read_write_roles_fail_original_interoperation(self):
        for role, access in (('source', 'write'), ('destination', 'read')):
            value = self.gold(); self.fields(value)[role]['access'] = access
            self.assert_both_fail(self.grade(value, nonces=[0, 1]))

    def test_14_fixed_description_cannot_pass_both_counterfactual_layouts(self):
        for actual, described in (('baseline', 'reordered'), ('reordered', 'baseline')):
            value = contracts.gold(self.target(actual)['binary_sha256'], described)
            self.assert_both_fail(self.grade(value, actual, [0, 1, 5]))

    def test_15_malformed_bounded_artifacts_never_enter_guest_execution(self):
        invalid = [b'[]', b'\xff', b'{"schema_version":1,"schema_version":1}']
        for defect in ('bool_offset', 'overlap', 'outside', 'extra', 'depth', 'extent', 'missing'):
            value = self.gold(); fields = self.fields(value)
            if defect == 'bool_offset': fields['length']['offset'] = True
            elif defect == 'overlap': fields['length']['offset'] = fields['source']['offset']
            elif defect == 'outside': value['size'] = 8
            elif defect == 'extra': value['raw_code'] = 'not allowed'
            elif defect == 'depth': fields['source']['indirections'] = 100
            elif defect == 'extent': fields['source']['extent'] = 'arbitrary'
            elif defect == 'missing': value['fields'].pop()
            invalid.append(action_cache.canonical(value))
        for raw in invalid:
            with self.subTest(raw=raw[:40]), patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest launch')):
                result = runtime.grade(raw, self.target(), nonces=[0])
                self.assertFalse(result['artifact_valid'])
                self.assertEqual(result['return_provenance'], 'not_executed')

    def test_16_wrong_binary_is_admitted_but_not_executed(self):
        value = self.gold(); value['binary_sha256'] = '0'*64
        with patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Guest launch')):
            result = self.grade(value, nonces=[0])
        self.assertTrue(result['artifact_valid'])
        self.assertEqual(result['status'], 'incorrect_description')
        self.assertEqual(result['return_provenance'], 'not_executed')

    def test_17_all_parent_cache_hits_launch_no_workers(self):
        first = self.grade()
        with patch.object(runtime, '_run', side_effect=AssertionError('Compiler launch')), \
             patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Executor launch')):
            second = self.grade()
            self.assertTrue(runtime.original()['cache_hit'])
            self.assertTrue(runtime.native(self.target())['cache_hit'])
            self.assertTrue(runtime.probe(action_cache.canonical(self.gold()), self.target())['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])

    def test_18_action_inputs_isolate_stages_and_older_originals(self):
        from capabilities.abi import runtime as old
        raw = action_cache.canonical(self.gold())
        request = {**runtime.object_request('probe', self.target()), 'artifact_hex': raw.hex(),
                   'binary_sha256': self.target()['binary_sha256']}
        original = runtime.compile_action('original', {'mode': 'original'}, 'baseline')[0].key
        probe = runtime.compile_action('probe', request)[0].key
        grade = runtime.grade_action(raw, self.target(), list(contracts.NONCES)).key
        previous = old.original_action('record_codec', 'sysv_amd64')[0].key
        read = Path.read_bytes
        def mutate(path):
            value = read(path)
            if path == runtime.ROOT/(runtime.MODULE+'codegen.py'): value += b'\n# probe-only control\n'
            return value
        with patch.object(Path, 'read_bytes', mutate):
            self.assertEqual(original, runtime.compile_action('original', {'mode': 'original'}, 'baseline')[0].key)
            self.assertEqual(previous, old.original_action('record_codec', 'sysv_amd64')[0].key)
            self.assertNotEqual(probe, runtime.compile_action('probe', request)[0].key)
        def unrelated(path):
            value = read(path)
            return value+b'\n# unrelated report change\n' if path == runtime.ROOT/'benchmarks/scripts/report.py' else value
        with patch.object(Path, 'read_bytes', unrelated):
            self.assertEqual(original, runtime.compile_action('original', {'mode': 'original'}, 'baseline')[0].key)
            self.assertEqual(probe, runtime.compile_action('probe', request)[0].key)
            self.assertEqual(grade, runtime.grade_action(raw, self.target(), list(contracts.NONCES)).key)

    def test_19_vector_revision_reuses_compiler_and_cached_guest_calls(self):
        full = self.grade()
        with patch.object(runtime, '_run', side_effect=AssertionError('Compiler launch')), \
             patch.object(runtime.executor, '_invoke', side_effect=AssertionError('VM worker launch')):
            subset = self.grade(nonces=[6, 11])
        self.assertTrue(subset['task_success'])
        self.assertNotEqual(full['grading_id'], subset['grading_id'])
        self.assertEqual(full['compile_id'], subset['compile_id'])

    def test_20_host_generator_monkeypatch_cannot_change_closed_probe_semantics(self):
        value = self.gold(); self.fields(value)['produced']['offset'] = 28
        with patch.object(codegen, 'generate', return_value=codegen.generate(self.gold())):
            result = runtime.grade(action_cache.canonical(value)+b'\n', self.target(), nonces=[0])
        self.assert_both_fail(result)

    def test_21_original_tampering_fails_before_compiler_or_executor(self):
        for defect in ('object', 'entry', 'build'):
            target = copy.deepcopy(self.target())
            if defect == 'object': target['original_obj_hex'] = '00'+target['original_obj_hex'][2:]
            elif defect == 'entry': target['entries']['original_callee'] += 1
            else: target['build_id'] = '0'*64
            with patch.object(runtime, '_run', side_effect=AssertionError('Compiler launch')), \
                 patch.object(runtime.executor, 'invoke', side_effect=AssertionError('VM launch')):
                with self.assertRaisesRegex(ValueError, 'integrity'):
                    runtime.grade(action_cache.canonical(self.gold()), target)

    def test_22_invalid_vectors_and_missing_validation_are_fail_closed(self):
        for value in ([], [True], [-1], [2**64], [0, 0], [[0]]):
            with self.subTest(value=value), self.assertRaises(ValueError): runtime.vectors(value)
        with patch.object(evidence, '_controls') as state, patch.object(evidence, 'RECEIPT', runtime.ROOT/'.cache/missing-pointer-controls.json'):
            state.get.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'controls missing or stale'):
                runtime.grade(action_cache.canonical(self.gold()), self.target())

    def test_23_source_policy_and_variant_drift_stop_before_worker(self):
        request = {'mode': 'original'}
        _, frozen, platform, policy = runtime.compile_action('original', request, 'baseline')
        changed = copy.deepcopy(policy); changed['limits']['pipe_bytes'] += 1
        with patch.object(runtime.images, 'image', side_effect=AssertionError('Worker launch')):
            with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                runtime._run(request, frozen, platform, changed, 'baseline')
            with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                runtime._run(request, frozen, platform, policy, 'reordered')


    def test_24_indirect_depth_claims_fail_in_both_original_directions(self):
        for variant in ('indirect', 'indirect_reordered'):
            for role in ('source', 'destination'):
                value = self.gold(variant)
                self.fields(value)[role]['indirections'] = 1
                self.assert_both_fail(self.grade(value, variant, nonces=[0, 1, 9]))

    def test_25_pointer_cell_mutation_fails_even_with_identical_status_and_arena(self):
        variant, nonces = 'indirect', [0, 4, 9]
        path = runtime.ROOT / (runtime.MODULE + 'codegen.py')
        read = Path.read_bytes
        control = b"""
_gold_generate = generate
def generate(value):
    source = next(item for item in value['fields'] if item['role'] == 'source')
    text = _gold_generate(value).replace('recovered_slot(', 'control_slot(', 1)
    wrapper = '''
extern "C" __attribute__((noinline, noipa)) std::int64_t recovered_slot(void* object, std::uint64_t nonce) {
    auto status = control_slot(object, nonce);
    auto* cell = load<void*>(object, SOURCE_OFFSET);
    if (cell) store<std::uint8_t*>(cell, 0, static_cast<std::uint8_t*>(object));
    return status;
}
'''
    return text + wrapper.replace('SOURCE_OFFSET', str(source['offset']))
"""
        def mutate(item):
            raw = read(item)
            return raw + control if item == path else raw
        with patch.object(Path, 'read_bytes', mutate):
            result = self.grade(self.gold(variant), variant, nonces=nonces)
        self.assertTrue(result['artifact_valid'])
        self.assertFalse(result['task_success'])
        self.assertEqual((result['passed'], result['total']), (3, 6))
        for row in result['checks']:
            if row['direction'] == 'bridge_to_original_callee':
                self.assertTrue(row['passed'])
                continue
            self.assertFalse(row['passed'])
            observed = row['observation']
            self.assertTrue(observed['returned'])
            self.assertEqual(observed['status'], 0)
            actual, expected = bytes.fromhex(observed['output_hex']), reference_bytes(row['nonce'])
            self.assertEqual(actual[:112], expected[:112])
            self.assertEqual(actual[112:120], bytes(8))
            self.assertEqual(actual[120:], expected[120:])

    def test_26_indirect_source_and_header_changes_leave_direct_originals_reusable(self):
        original = {variant: runtime.compile_action('original', {'mode': 'original'}, variant)[0].key
                    for variant in contracts.VARIANTS}
        read = Path.read_bytes
        def driver_change(path):
            raw = read(path)
            return raw + b'\n// indirect driver mutation\n' if path == runtime.ROOT / (runtime.PREFIX + 'indirect_original.cpp') else raw
        with patch.object(Path, 'read_bytes', driver_change):
            for variant in contracts.VARIANTS:
                changed = runtime.compile_action('original', {'mode': 'original'}, variant)[0].key
                self.assertEqual(changed == original[variant], not variant.startswith('indirect'))
        def header_change(path):
            raw = read(path)
            return raw + b'\n// unselected layout mutation\n' if path == runtime.ROOT / (runtime.PREFIX + 'indirect_reordered.hpp') else raw
        with patch.object(Path, 'read_bytes', header_change):
            for variant in contracts.VARIANTS:
                changed = runtime.compile_action('original', {'mode': 'original'}, variant)[0].key
                self.assertEqual(changed == original[variant], variant != 'indirect_reordered')

    def test_27_indirect_warm_reuse_launches_no_compiler_or_guest(self):
        with patch.object(runtime, '_run', side_effect=AssertionError('Compiler/native worker')), \
             patch.object(runtime.executor, '_invoke', side_effect=AssertionError('Guest worker')):
            for variant in ('indirect', 'indirect_reordered'):
                self.assertTrue(self.grade(self.gold(variant), variant)['cache_hit'])
                self.assertTrue(runtime.native(self.target(variant))['cache_hit'])


def main():
    started = evidence.begin()
    with evidence.validation_controls(), patch.object(socket.socket, 'connect', side_effect=AssertionError('Network/model access')):
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PointerControls))
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__': main()
