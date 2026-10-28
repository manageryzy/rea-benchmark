#!/usr/bin/python3
"""Offline controls for nonallocating Rust reconstruction; never invokes models."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities.baselines import build, reference, rust_controls, rust_evidence, rust_grade, rust_runtime
from grading import offline
from grading.c_executor import runtime as executor

SIGNATURE = '''#[no_mangle]
pub unsafe extern "C" fn reconstructed(input: *const u8, input_len: usize,
    output: *mut u8, output_capacity: usize, output_len: *mut usize) -> i32'''


class RustBufferControls(unittest.TestCase):
    def grade(self, source, family='record_codec', indices=(0, 3)):
        cases = [reference.vectors(family)[index] for index in indices]
        return rust_grade.grade(source, family, cases=cases)

    def test_01_all_six_gold_controls_match_342_original_observations(self):
        total = 0
        for family in build.FAMILIES:
            with self.subTest(family=family):
                result = rust_grade.grade(rust_controls.solution(family), family)
                self.assertEqual(result['passed'], result['total'])
                self.assertTrue(result['task_success'])
                self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')
                self.assertFalse(result['release_accepted'])
                total += result['total']
        self.assertEqual(total, 342)

    def test_02_modified_oracle_rejected_before_candidate_compile(self):
        cases = reference.vectors('record_codec')[:2]
        oracle = reference.observe('rust', 'record_codec', cases)
        for key, replacement in (('status', -77), ('length', 1), ('arena_hex', '00' * 2048)):
            changed = copy.deepcopy(oracle)
            changed['observations'][0][key] = replacement
            with patch.object(rust_runtime, 'compile', side_effect=AssertionError('Candidate compiler')):
                with self.assertRaisesRegex(ValueError, 'oracle binding'):
                    rust_grade.grade(rust_controls.solution('record_codec'), 'record_codec', cases=cases, oracle=changed)

    def test_03_duplicate_ids_and_invalid_cases_fail_before_compile(self):
        cases = reference.vectors('record_codec')[:1]
        with patch.object(rust_runtime, 'compile', side_effect=AssertionError('Candidate compiler')):
            with self.assertRaises(ValueError):
                rust_grade.grade(rust_controls.solution('record_codec'), 'record_codec', cases=cases * 2)
            for field, value in (('capacity', -1), ('capabilities', ['unknown'])):
                changed = copy.deepcopy(cases)
                changed[0][field] = value
                with self.assertRaises(ValueError):
                    rust_grade.grade(rust_controls.solution('record_codec'), 'record_codec', cases=changed)

    def test_04_signedness_defect_fails(self):
        source = rust_controls.solution('numeric_aggregation').replace('value as u32', 'value as u16 as u32')
        result = self.grade(source, 'numeric_aggregation', (7, 12, 13))
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_05_sort_direction_defect_fails(self):
        source = rust_controls.solution('numeric_aggregation').replace('values[..count].sort_unstable();',
            'values[..count].sort_unstable_by(|a, b| b.cmp(a));')
        result = self.grade(source, 'numeric_aggregation', (7, 12, 13))
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_06_wrong_length_does_not_receive_full_credit(self):
        source = rust_controls.solution('record_codec').replace('output_len.write(required);', 'output_len.write(required + 1);')
        result = self.grade(source, indices=(3, 7, 12))
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_07_unrelated_memory_write_is_detected(self):
        result = self.grade(SIGNATURE + ' { *output_len = 0; output.sub(1).write(0); -2 }')
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_08_wrong_signature_has_no_return_provenance(self):
        result = self.grade('#[no_mangle] pub fn reconstructed() -> u64 { 0 }')
        self.assertFalse(result['compiled'])
        self.assertEqual(result['return_provenance'], 'not_executed')

    def test_09_out_of_bounds_write_cannot_pass(self):
        result = self.grade(SIGNATURE + ' { output.add(0x10000000).write(0); *output_len = 0; -2 }')
        self.assertTrue(result['compiled'])
        self.assertEqual(result['passed'], 0)

    def test_10_syscall_cannot_forge_host_return(self):
        source = SIGNATURE + ''' {
            core::arch::asm!("syscall", in("rax") 60_u64, in("rdi") 0_u64,
                lateout("rcx") _, lateout("r11") _);
            *output_len = 0; -2
        }'''
        result = self.grade(source)
        self.assertTrue(result['compiled'])
        self.assertEqual(result['passed'], 0)

    def test_11_panic_is_a_bounded_failure(self):
        result = self.grade(SIGNATURE + ' { panic!("controlled panic") }', indices=(0,))
        self.assertTrue(result['compiled'])
        self.assertEqual(result['passed'], 0)
        self.assertFalse(result['task_success'])

    def test_12_cold_compiler_receives_no_vectors_or_expected_answers(self):
        captured, original = [], rust_runtime.process.run
        def checked(command, **kwargs):
            request = json.loads(kwargs['input'])
            self.assertEqual(set(request), {'source'})
            captured.append(request)
            return original(command, **kwargs)
        with tempfile.TemporaryDirectory(prefix='rust-control-cold-') as temporary, \
             patch.object(rust_runtime.process, 'run', side_effect=checked):
            result = rust_runtime.compile(rust_controls.solution('record_codec'), cache=action_cache.Cache(temporary))
        self.assertEqual(result['status'], 'compiled')
        self.assertEqual(len(captured), 1)

    def test_13_warm_grades_launch_no_compiler_original_or_vm_worker(self):
        with patch.object(rust_runtime, '_invoke', side_effect=AssertionError('Duplicate compiler')), \
             patch.object(executor, '_invoke', side_effect=AssertionError('Duplicate VM')), \
             patch.object(reference.process, 'run', side_effect=AssertionError('Duplicate original')):
            for family in build.FAMILIES:
                result = rust_grade.grade(rust_controls.solution(family), family)
                self.assertTrue(result['cache_hit'])
                self.assertTrue(result['task_success'])

    def test_14_unrelated_control_source_changes_preserve_compile_cache(self):
        source = rust_controls.solution('record_codec')
        before = rust_runtime.compile(source)
        original, selected = Path.read_bytes, Path(rust_controls.__file__)
        def changed(path):
            raw = original(path)
            return raw + b'\n# unrelated control changes\n' if path == selected else raw
        with patch.object(Path, 'read_bytes', changed), \
             patch.object(rust_runtime, '_invoke', side_effect=AssertionError('Duplicate compiler')):
            after = rust_runtime.compile(source)
        self.assertEqual(before['action_id'], after['action_id'])
        self.assertTrue(after['cache_hit'])

    def test_15_flags_and_source_size_cannot_escape_profile(self):
        original = json.loads(rust_runtime.POLICY.read_bytes())
        with patch.object(rust_runtime.json, 'loads', return_value={**original, 'rust_flags': ['--help']}):
            with self.assertRaises(ValueError): rust_runtime.policy()
        with patch.object(rust_runtime, '_invoke', side_effect=AssertionError('Compiler')):
            for source in ('', '\0', ' ' * 262145):
                with self.assertRaises(ValueError): rust_runtime.compile(source)

    def test_16_loaded_grader_changes_are_rejected(self):
        original, selected = Path.read_bytes, Path(rust_grade.__file__)
        def changed(path):
            raw = original(path)
            return raw + b'\n# runtime drift\n' if path == selected else raw
        with patch.object(Path, 'read_bytes', changed), \
             patch.object(rust_runtime, 'compile', side_effect=AssertionError('Compiler')):
            with self.assertRaisesRegex(RuntimeError, 'Loaded'):
                self.grade(rust_controls.solution('record_codec'))

    def test_17_execution_source_changes_affect_grade_but_preserve_compile_action(self):
        family = 'record_codec'
        source = rust_controls.solution(family)
        cases = reference.vectors(family)
        oracle = reference.observe('rust', family, cases)
        compiled = rust_runtime.compile(source)
        before = rust_grade.grading_action(source, family, cases, oracle, compiled).key
        original = executor.sources
        def changed(stage):
            selected = original(stage)
            if stage == 'c_execute':
                name = 'benchmarks/scripts/grading/c_executor/machine.py'
                selected[name] += b'\n# changed execution semantics\n'
            return selected
        with patch.object(executor, 'sources', side_effect=changed), \
             patch.object(rust_runtime, '_invoke', side_effect=AssertionError('Duplicate compiler')):
            after = rust_grade.grading_action(source, family, cases, oracle, compiled).key
            self.assertNotEqual(before, after)
            self.assertEqual(rust_runtime.compile(source)['action_id'], compiled['action_id'])


if __name__ == '__main__':
    started = rust_evidence.begin()
    with offline.guard():
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RustBufferControls))
    value = rust_evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)
