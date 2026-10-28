#!/usr/bin/python3
"""C1–C4 controls against the actual stripped originals, with host-owned returns."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import build, buffers, buffer_evidence
from grading import offline
from grading.c_executor import runtime as c_runtime

spec = importlib.util.spec_from_file_location('buffer_solutions',
    build.SUITE / 'controls/buffer_solutions.py')
solutions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solutions)


class BufferControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = action_cache.Cache()
        cls.originals, cls.cases, cls.oracles = {}, {}, {}
        for family in json.loads((build.SUITE / 'config/families.json').read_bytes())['families']:
            name = family['id']
            cls.originals[name] = build.build(name)
            cls.cases[name] = buffers.vectors(name)
            cls.oracles[name] = buffers.reference(cls.originals[name], cls.cases[name])

    def subset(self, family, indices):
        cases = [self.cases[family][index] for index in indices]
        oracle = buffers.reference(self.originals[family], cases)
        return cases, oracle

    def test_01_full_gold_controls_all_six_families(self):
        for name in self.cases:
            with self.subTest(family=name):
                result = buffers.grade(solutions.solution(name), self.cases[name], self.oracles[name], cache=self.cache)
                self.assertEqual(result['passed'], result['total'], result)
                self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')
                for capability in ('C1', 'C2', 'C3', 'C4'):
                    self.assertGreater(result['capabilities'][capability]['total'], 0)
                    self.assertEqual(result['capabilities'][capability]['passed'], result['capabilities'][capability]['total'])

    def test_02_equivalent_serialization_algorithm(self):
        cases, oracle = self.subset('record_codec', [0, 3, 12, 20])
        source = solutions.solution('record_codec').replace(
            'for (size_t index = 0; index < required; ++index)\n        output[index] = message[index];',
            'size_t index = required;\n    while (index) { --index; output[index] = message[index]; }')
        result = buffers.grade(source, cases, oracle, cache=self.cache)
        self.assertEqual(result['passed'], result['total'])

    def test_03_wrong_numeric_signedness(self):
        cases, oracle = self.subset('numeric_aggregation', [7, 12, 13])
        source = solutions.solution('numeric_aggregation').replace(
            'bits >= 32768 ? (int32_t)bits - 65536 : (int32_t)bits', '(int32_t)bits')
        result = buffers.grade(source, cases, oracle, cache=self.cache)
        self.assertLess(result['passed'], result['total'])
        self.assertTrue(any(not check['components'].get('output_bytes', True) for check in result['checks']))

    def test_04_wrong_sort_order(self):
        cases, oracle = self.subset('numeric_aggregation', [7, 12, 13])
        source = solutions.solution('numeric_aggregation').replace('values[position] < values[position - 1]',
                                                                  'values[position] > values[position - 1]')
        result = buffers.grade(source, cases, oracle, cache=self.cache)
        self.assertLess(result['passed'], result['total'])

    def test_05_wrong_output_length(self):
        cases, oracle = self.subset('record_codec', [0, 3])
        source = solutions.solution('record_codec').replace('*output_len = required;', '*output_len = required + 1;')
        result = buffers.grade(source, cases, oracle, cache=self.cache)
        self.assertEqual(result['passed'], 0)
        self.assertTrue(all(not row['components']['length'] for row in result['checks']))

    def test_06_forward_alias_corruption(self):
        cases, oracle = self.subset('record_codec', [3, 22, 23, 24])
        # An otherwise equivalent direct writer corrupts overlapping input after
        # writing the header. A separate disjoint invocation still passes.
        source = solutions.solution('record_codec').replace(
            'uint8_t message[520];', 'uint8_t *message = output;')
        result = buffers.grade(source, cases, oracle, cache=self.cache)
        self.assertTrue(result['checks'][0]['passed'])
        self.assertLess(result['passed'], result['total'])

    def test_07_preserved_tail_is_scored_even_inside_capacity(self):
        cases, oracle = self.subset('record_codec', [len(self.cases['record_codec']) - 1])
        source = solutions.solution('record_codec').replace('    return 0;\n}',
            '    output[required] = 0;\n    return 0;\n}')
        result = buffers.grade(source, cases, oracle, cache=self.cache)
        self.assertEqual(result['passed'], 0)
        self.assertFalse(result['checks'][0]['components']['preserved_bytes'])

    def test_08_capacity_error_cannot_write_output(self):
        cases, oracle = self.subset('record_codec', [len(self.cases['record_codec']) - 4])
        source = solutions.solution('record_codec').replace('if (capacity < required) return -1;',
            'if (capacity < required) { output[0] = 0; return -1; }')
        result = buffers.grade(source, cases, oracle, cache=self.cache)
        self.assertEqual(result['passed'], 0)

    def test_09_oracle_cannot_be_reused_for_changed_vectors(self):
        cases, oracle = self.subset('record_codec', [0])
        changed = copy.deepcopy(cases)
        changed[0]['seed'] ^= 1
        with self.assertRaisesRegex(ValueError, 'vectors'):
            buffers.grade(solutions.solution('record_codec'), changed, oracle, cache=self.cache)

    def test_10_grade_cache_reuse_does_not_execute_candidate(self):
        cases, oracle = self.subset('record_codec', [0, 3])
        first = buffers.grade(solutions.solution('record_codec'), cases, oracle, cache=self.cache)
        with (patch.object(c_runtime, 'compile', side_effect=AssertionError('Duplicate compile')),
              patch.object(c_runtime, 'invoke', side_effect=AssertionError('Duplicate VM'))):
            second = buffers.grade(solutions.solution('record_codec'), cases, oracle, cache=self.cache)
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])

    def test_11_current_invocation_never_contains_oracle_or_target(self):
        for case in self.cases['record_codec']:
            value = buffers.invocation(case)
            self.assertEqual(set(value), {'id', 'return_type', 'callbacks', 'regions', 'arguments'})
            self.assertNotIn('expected', json.dumps(value))
            self.assertEqual(len(value['regions']), 2)
            self.assertEqual(value['arguments'][1], len(bytes.fromhex(case['input_hex'])))

    def test_12_invalid_length_and_arbitrary_bytes_are_present(self):
        rows = self.oracles['record_codec']['observations']
        self.assertTrue(any(row['status'] == -2 and row['length'] == 0 for row in rows))
        self.assertTrue(any(row['status'] == -1 for row in rows))
        self.assertTrue(any(row['status'] == 0 for row in rows))
        self.assertIn('0d0a00ff', [row['input_hex'] for row in self.cases['record_codec']])
        self.assertTrue(any(row['output_offset'] < 64 for row in self.cases['record_codec']))


def main():
    started = buffer_evidence.begin()
    with buffer_evidence.validation_controls(), offline.guard():
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(BufferControls)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    value = buffer_evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__':
    main()
