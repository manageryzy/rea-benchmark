#!/usr/bin/python3
"""Offline Go reconstruction controls, including actual ABIInternal execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import buffers
from capabilities.baselines import build, reference, go_controls, go_evidence, go_grade, go_runtime, go_execute
from grading import offline

SIGNATURE = '''package main
import "unsafe"
func reconstructed(input unsafe.Pointer, inputLen uintptr, output unsafe.Pointer,
    outputCapacity uintptr, outputLen *uintptr) int32'''


class GoBufferControls(unittest.TestCase):
    def grade(self, source, family='record_codec', indices=(0, 3)):
        cases = [reference.vectors(family)[index] for index in indices]
        return go_grade.grade(source, family, cases=cases)

    def test_01_six_gold_controls_match_342_original_calls(self):
        total = 0
        for family in build.FAMILIES:
            with self.subTest(family=family):
                result = go_grade.grade(go_controls.solution(family), family)
                self.assertEqual(result['passed'], result['total'])
                self.assertTrue(result['task_success'])
                self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')
                self.assertFalse(result['release_accepted'])
                total += result['total']
        self.assertEqual(total, 342)

    def test_02_modified_original_oracle_fails_before_compilation(self):
        cases = reference.vectors('record_codec')[:2]
        actual = reference.observe('go', 'record_codec', cases)
        for key, replacement in (('status', -77), ('length', 1), ('arena_hex', '00' * 2048)):
            oracle = copy.deepcopy(actual)
            oracle['observations'][0][key] = replacement
            with patch.object(go_runtime, 'compile', side_effect=AssertionError('Candidate compiler')):
                with self.assertRaisesRegex(ValueError, 'oracle binding'):
                    go_grade.grade(go_controls.solution('record_codec'), 'record_codec', cases=cases, oracle=oracle)

    def test_03_wrong_signedness_fails(self):
        source = go_controls.solution('numeric_aggregation').replace('uint32(int32(values[index]))', 'uint32(uint16(values[index]))')
        result = self.grade(source, 'numeric_aggregation', (7, 12, 13))
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_04_reversed_order_fails(self):
        source = go_controls.solution('numeric_aggregation').replace('values[cursor] < values[cursor-1]', 'values[cursor] > values[cursor-1]')
        result = self.grade(source, 'numeric_aggregation', (7, 12, 13))
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_05_wrong_length_fails(self):
        source = go_controls.solution('record_codec').replace('*outputLen = required', '*outputLen = required + 1')
        result = self.grade(source, indices=(3, 7, 12))
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_06_out_of_bounds_write_cannot_pass(self):
        result = self.grade(SIGNATURE + ' { *(*byte)(unsafe.Add(output, 0x10000000)) = 0; *outputLen = 0; return -2 }')
        self.assertTrue(result['compiled'])
        self.assertEqual(result['passed'], 0)

    def test_07_writes_outside_declared_capacity_are_rejected(self):
        result = self.grade(SIGNATURE + ' { *(*byte)(unsafe.Add(output, -1)) = 0; *outputLen = 0; return -2 }')
        self.assertTrue(result['compiled'])
        self.assertEqual(result['passed'], 0)

    def test_08_panic_is_bounded_failure(self):
        result = self.grade(SIGNATURE + ' { panic("controlled panic") }', indices=(0,))
        self.assertTrue(result['compiled'])
        self.assertEqual(result['passed'], 0)

    def test_09_image_globals_are_not_writable(self):
        source = SIGNATURE + ' { counter++; *outputLen = 0; return -2 }\nvar counter uint64\n'
        result = self.grade(source)
        self.assertTrue(result['compiled'])
        self.assertEqual(result['passed'], 0)

    def test_10_wrong_signature_is_not_executed(self):
        result = self.grade('package main\nfunc reconstructed() uint64 { return 0 }')
        self.assertFalse(result['compiled'])
        self.assertEqual(result['return_provenance'], 'not_executed')

    def test_11_import_policy_uses_syntax_not_comments_or_strings(self):
        source = go_controls.solution('record_codec') + '\n// "os" and "C" in comments are harmless.\nvar description = "import os"\n'
        source = source.replace('import "unsafe"', 'import pointer `unsafe`').replace('unsafe.', 'pointer.')
        result = go_runtime.compile(source)
        self.assertEqual(result['status'], 'compiled')
        self.assertEqual(result['admission']['imports'], ['unsafe'])

    def test_12_actual_forbidden_import_is_rejected(self):
        source = go_controls.solution('record_codec').replace('import "unsafe"', 'import "unsafe"\nimport _ "os"')
        result = self.grade(source)
        self.assertEqual(result['status'], 'forbidden_import')
        self.assertFalse(result['compiled'])
        self.assertEqual(result['return_provenance'], 'not_executed')

    def test_13_linkname_directives_cannot_escape_contract(self):
        source = go_controls.solution('record_codec') + '\n//go:linkname aliased runtime.exit\nfunc aliased(int32)\n'
        result = self.grade(source)
        self.assertEqual(result['status'], 'forbidden_directive')
        self.assertFalse(result['compiled'])

    def test_14_cold_compiler_gets_source_only(self):
        captured, original = [], go_runtime.process.run
        def checked(command, **kwargs):
            request = json.loads(kwargs['input'])
            self.assertEqual(set(request), {'source'})
            captured.append(request)
            return original(command, **kwargs)
        with tempfile.TemporaryDirectory(prefix='go-control-cold-') as temporary, \
             patch.object(go_runtime.process, 'run', side_effect=checked):
            result = go_runtime.compile(go_controls.solution('record_codec'), cache=action_cache.Cache(temporary))
        self.assertEqual(result['status'], 'compiled')
        self.assertEqual(len(captured), 1)

    def test_15_warm_grades_launch_no_workers(self):
        with patch.object(go_runtime, '_invoke', side_effect=AssertionError('Duplicate compiler')), \
             patch.object(go_execute, '_invoke', side_effect=AssertionError('Duplicate VM')), \
             patch.object(reference.process, 'run', side_effect=AssertionError('Duplicate original')):
            for family in build.FAMILIES:
                result = go_grade.grade(go_controls.solution(family), family)
                self.assertTrue(result['cache_hit'])
                self.assertTrue(result['task_success'])

    def test_16_unrelated_control_change_preserves_compiler_cache(self):
        source = go_controls.solution('record_codec')
        before = go_runtime.compile(source)
        original, selected = Path.read_bytes, Path(go_controls.__file__)
        def changed(path):
            raw = original(path)
            return raw + b'\n# unrelated control revision\n' if path == selected else raw
        with patch.object(Path, 'read_bytes', changed), \
             patch.object(go_runtime, '_invoke', side_effect=AssertionError('Duplicate compiler')):
            after = go_runtime.compile(source)
        self.assertEqual(before['action_id'], after['action_id'])
        self.assertTrue(after['cache_hit'])

    def test_17_invalid_invocation_and_source_fail_before_worker(self):
        compiled = go_runtime.compile(go_controls.solution('record_codec'))
        invocation = buffers.invocation(reference.vectors('record_codec')[0])
        with patch.object(go_execute, '_invoke', side_effect=AssertionError('VM')):
            with self.assertRaises(ValueError): go_execute.invoke(compiled['elf'], {**invocation, 'oracle': []})
        with patch.object(go_runtime, '_invoke', side_effect=AssertionError('Compiler')):
            for source in ('', '\0', ' ' * 262145):
                with self.assertRaises(ValueError): go_runtime.compile(source)

    def test_18_loaded_grader_drift_is_rejected(self):
        original, selected = Path.read_bytes, Path(go_grade.__file__)
        def changed(path):
            raw = original(path)
            return raw + b'\n# runtime drift\n' if path == selected else raw
        with patch.object(Path, 'read_bytes', changed), \
             patch.object(go_runtime, 'compile', side_effect=AssertionError('Compiler')):
            with self.assertRaisesRegex(RuntimeError, 'Loaded'):
                self.grade(go_controls.solution('record_codec'))


if __name__ == '__main__':
    started = go_evidence.begin()
    with offline.guard():
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(GoBufferControls))
    value = go_evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)
