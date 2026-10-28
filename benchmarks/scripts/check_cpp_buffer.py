#!/usr/bin/python3
"""Offline original-language pairing controls; no provider or inference dispatch."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import build, buffers, buffer_evidence, tasks
from capabilities.cpp_buffer import evidence, runtime
from grading import artifacts, offline
from grading.c_executor import runtime as executor


def controls(name):
    spec = importlib.util.spec_from_file_location(name, build.SUITE / ('controls/' + name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

cpp = controls('cpp_buffer_solutions')
c = controls('buffer_solutions')


class CppBufferControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originals, cls.cases, cls.oracles = {}, {}, {}
        for family in json.loads((build.SUITE / 'config/families.json').read_bytes())['families']:
            name = family['id']
            cls.originals[name] = build.build(name)
            cls.cases[name] = buffers.vectors(name)
            cls.oracles[name] = buffers.reference(cls.originals[name], cls.cases[name])

    def subset(self, family='record_codec', indices=(0, 3)):
        cases = [self.cases[family][index] for index in indices]
        return cases, buffers.reference(self.originals[family], cases)

    def grade(self, source, family='record_codec', indices=(0, 3)):
        cases, oracle = self.subset(family, indices)
        return buffers.grade(source, cases, oracle, language='cpp')

    def test_01_full_gold_cpp_and_c_use_identical_oracles(self):
        count = 0
        for name in self.cases:
            with self.subTest(family=name):
                result = buffers.grade(cpp.solution(name), self.cases[name], self.oracles[name], language='cpp')
                baseline = buffers.grade(c.solution(name), self.cases[name], self.oracles[name])
                self.assertEqual(result['passed'], result['total'], result)
                self.assertEqual(result['passed'], baseline['passed'])
                self.assertEqual(result['capabilities'], baseline['capabilities'])
                self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')
                count += result['total']
        self.assertEqual(count, 222)

    def test_02_paired_frozen_tasks_share_binary_entry_vectors_and_oracle(self):
        for family in self.cases:
            for suffix in ('recover', 'codegen'):
                first, second = [tasks.frozen(family + '_' + language + '_' + suffix) for language in ('c', 'cpp')]
                self.assertEqual(first['binary_sha256'], second['binary_sha256'])
                for case in (first, second):
                    raw = (action_cache.ROOT / case['grading_spec']).read_bytes()
                    self.assertEqual(runtime.sha(raw), case['grading_spec_sha256'])
                specs = [json.loads((action_cache.ROOT / case['grading_spec']).read_bytes()) for case in (first, second)]
                self.assertEqual(specs[0]['cases'], specs[1]['cases'])
                self.assertEqual(specs[0]['oracle'], specs[1]['oracle'])
                self.assertEqual([case['output_language'] for case in (first, second)], ['c', 'cpp'])
                directories = [action_cache.ROOT / case['public_directory'] for case in (first, second)]
                self.assertEqual((directories[0] / 'target').read_bytes(), (directories[1] / 'target').read_bytes())
                manifests = [json.loads((d / 'manifest.json').read_bytes()) for d in directories]
                self.assertEqual(manifests[0]['entry_rva'], manifests[1]['entry_rva'])
                self.assertEqual(first['capability_ids'], second['capability_ids'])
                self.assertNotEqual(first['public_package_sha256'], second['public_package_sha256'])
                self.assertIn('solution.cpp', (directories[1] / 'prompt.txt').read_text())
                if suffix == 'codegen':
                    for d in directories: self.assertIn(tasks.SEMANTICS[family], (d / 'prompt.txt').read_text())

    def test_03_signedness_defect_fails(self):
        source = cpp.solution('numeric_aggregation').replace(
            'bits >= 32768 ? static_cast<std::int32_t>(bits) - 65536 : static_cast<std::int32_t>(bits)', 'static_cast<std::int32_t>(bits)')
        result = self.grade(source, 'numeric_aggregation', (7, 12, 13))
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_04_sort_direction_defect_fails(self):
        source = cpp.solution('numeric_aggregation').replace('std::sort(values.begin(), values.begin() + count);',
            'std::sort(values.begin(), values.begin() + count, [](auto a, auto b) { return a > b; });')
        result = self.grade(source, 'numeric_aggregation', (7, 12, 13))
        self.assertTrue(result['compiled'])
        self.assertLess(result['passed'], result['total'])

    def test_05_wrong_output_length_fails(self):
        source = cpp.solution('record_codec').replace('*output_len = required;', '*output_len = required + 1;')
        result = self.grade(source)
        self.assertEqual(result['passed'], 0)
        self.assertTrue(all(not row['components']['length'] for row in result['checks']))

    def test_06_alias_defect_fails_while_disjoint_control_passes(self):
        source = cpp.solution('record_codec').replace('std::array<std::uint8_t, 520> message{};', 'auto *message = output;')
        source = source.replace('std::copy_n(message.begin(), required, output);', '')
        result = self.grade(source, indices=(3, 22, 23, 24))
        self.assertTrue(result['checks'][0]['passed'])
        self.assertLess(result['passed'], result['total'])

    def test_07_error_writes_and_preserved_tail_fail(self):
        cases = self.cases['record_codec']
        source = cpp.solution('record_codec').replace('if (capacity < required) return -1;',
            'if (capacity < required) { output[0] = 0; return -1; }')
        self.assertEqual(self.grade(source, indices=(len(cases) - 4,))['passed'], 0)
        source = cpp.solution('record_codec').replace('    return 0;\n}', '    output[required] = 0;\n    return 0;\n}')
        result = self.grade(source, indices=(len(cases) - 1,))
        self.assertFalse(result['checks'][0]['components']['preserved_bytes'])

    def test_08_equivalent_reverse_serialization_passes(self):
        source = cpp.solution('record_codec').replace('std::copy_n(message.begin(), required, output);',
            'for (auto i = required; i > 0; --i) output[i - 1] = message[i - 1];')
        result = self.grade(source, indices=(0, 3, 22))
        self.assertEqual(result['passed'], result['total'])

    def test_09_parent_cache_hit_does_not_compile_or_execute(self):
        source = cpp.solution('record_codec')
        first = self.grade(source)
        with patch.object(runtime, 'compile', side_effect=AssertionError('Duplicate compile')), patch.object(executor, 'invoke', side_effect=AssertionError('Duplicate execution')):
            second = self.grade(source)
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])

    def test_10_compile_cache_reuses_bound_elf(self):
        source = cpp.solution('record_codec')
        first = runtime.compile(source)
        with patch.object(runtime, '_compile', side_effect=AssertionError('Duplicate compiler')):
            second = runtime.compile(source)
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['elf'], second['elf'])

    def test_11_exact_cpp_artifact_admission(self):
        raw = b'int value;\r\n'
        self.assertEqual(artifacts.admit({'solution.cpp': raw}, {'solution.cpp': 1})['solution.cpp']['value'].encode(), raw)
        for raw in (b'\xff', b'int\0', b''):
            with self.assertRaises(ValueError): artifacts.admit({'solution.cpp': raw}, {'solution.cpp': 1})
        with self.assertRaises(ValueError): artifacts.admit({'solution.c': b'int x;'}, {'solution.cpp': 1})

    def test_12_missing_entry_signature_errors_and_heap_are_rejected(self):
        for source in ('int unrelated() { return 0; }', 'int reconstructed(int n) { return n; }',
                       'int *volatile escaped; extern "C" std::int32_t reconstructed(const std::uint8_t*,std::size_t,std::uint8_t*,std::size_t,std::size_t*) { escaped = new int(3); return *escaped; }'):
            with self.subTest(source=source):
                result = runtime.compile(source)
                self.assertIn(result['status'], ('compile_error', 'unsupported_image'))
                self.assertIsNone(result['elf'])

    def test_13_compiler_has_no_target_or_private_host_access(self):
        source = cpp.solution('record_codec')
        _, request, frozen, _, _ = runtime.compile_action(source)
        self.assertEqual(set(request), {'source'})
        self.assertFalse(any('/runs/' in name or '/controls/' in name or '/grade-specs/' in name for name in frozen))
        for path in ('/mnt/h/work/rea/benchmarks/config/capabilities.json', '/work/target'):
            result = runtime.compile('#include "' + path + '"\n' + source)
            self.assertEqual(result['status'], 'compile_error')

    def test_14_stage_hash_ignores_reports_and_tracks_compiler_helpers(self):
        source = cpp.solution('record_codec'); first = runtime.compile_action(source)[0].key
        read = Path.read_bytes
        def unrelated(path):
            raw = read(path)
            return raw + b'\n# report-only edit\n' if str(path).endswith('scripts/report.py') else raw
        with patch.object(Path, 'read_bytes', unrelated): self.assertEqual(first, runtime.compile_action(source)[0].key)
        def related(path):
            raw = read(path)
            return raw + b'\n// helper edit\n' if str(path).endswith('/cpp_buffer/helpers.cpp') else raw
        with patch.object(Path, 'read_bytes', related): self.assertNotEqual(first, runtime.compile_action(source)[0].key)
        execution = executor.execute_profile()
        unrelated_budget = copy.deepcopy(execution)
        unrelated_budget['limits']['worker_wall_seconds'] += 1
        with patch.object(executor, 'execute_profile', return_value=unrelated_budget):
            self.assertEqual(first, runtime.compile_action(source)[0].key)
        image_budget = copy.deepcopy(execution)
        image_budget['limits']['max_elf_bytes'] -= 1
        with patch.object(executor, 'execute_profile', return_value=image_budget):
            self.assertNotEqual(first, runtime.compile_action(source)[0].key)

    def test_15_missing_validation_fails_before_compiler(self):
        token = evidence._controls.set(False)
        try:
            with tempfile.TemporaryDirectory() as d, patch.object(evidence, 'RECEIPT', Path(d) / 'missing'), patch.object(runtime, '_compile', side_effect=AssertionError('Compiler')):
                with self.assertRaisesRegex(RuntimeError, 'controls'): runtime.compile(cpp.solution('record_codec'))
        finally: evidence._controls.reset(token)

    def test_16_prelaunch_source_drift_rejected(self):
        _, request, frozen, environment, policy = runtime.compile_action(cpp.solution('record_codec'))
        changed = {**frozen, runtime.PREFIX + 'worker.py': b'changed'}
        with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
            runtime._compile(request, changed, environment, policy)


def main():
    buffer_evidence.require_validation()
    started = evidence.begin()
    with evidence.validation_controls(), offline.guard():
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CppBufferControls))
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__': main()
