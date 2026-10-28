#!/usr/bin/python3
"""Offline matched-original and C/C++ control checks; no model providers."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import buffers
from capabilities.baselines import build, evidence, reference, tooling
from grading import image_manifest, offline, runtime
from grading.c_executor import runtime as executor


class MatchedBaselineControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = {(compiler, family): build.build(compiler, family)
                       for compiler in build.COMPILERS for family in build.FAMILIES}
        cls.controls = {}
        for name in ('buffer_solutions', 'cpp_buffer_solutions'):
            path = build.ROOT / 'benchmarks/suites/capabilities-v1/controls' / (name + '.py')
            spec = importlib.util.spec_from_file_location('baseline_' + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.controls[name] = module

    def oracle(self, compiler, family, cases=None):
        return reference.observe(compiler, family, reference.vectors(family) if cases is None else cases)

    def test_01_compiler_and_original_identities_are_separate(self):
        self.assertEqual(len({r['build_id'] for r in self.targets.values()}), 18)
        self.assertIn('clang version 20.1.2', tooling.record()['manifest']['compiler']['version'])
        for (compiler, family), target in self.targets.items():
            language = 'cpp' if compiler == 'clang20' else compiler
            self.assertEqual(target['runtime']['compiler_image'], image_manifest.load(language)['id'])
            if compiler == 'clang20': self.assertEqual(target['runtime']['compiler_extension'], tooling.record()['id'])

    def test_02_stripping_and_declared_endpoints_match(self):
        for (compiler, family), target in self.targets.items():
            entry = build.validate(target, compiler)
            self.assertEqual(entry['rva'], target['buffer_entry'])
            self.assertEqual(entry['type'], 2)

    def test_03_all_originals_match_gcc_and_python_on_1026_observations(self):
        total = 0
        for compiler in build.COMPILERS:
            for family in build.FAMILIES:
                row = reference.calibrate(compiler, family)
                self.assertEqual(row['passed'], row['total'])
                total += row['total']
        self.assertEqual(total, 1026)

    def test_04_error_lengths_and_preserved_memory_are_exact(self):
        for family in build.FAMILIES:
            cases = reference.vectors(family)
            for compiler in build.COMPILERS:
                for case, row in zip(cases, self.oracle(compiler, family)['observations']):
                    self.assertEqual(row, reference.expected(family, case))
                    if row['status'] != 0: self.assertEqual(row['arena_hex'], buffers.initial(case).hex())

    def test_05_extended_vectors_cover_overflow_overlap_and_final_arena_byte(self):
        for family in build.FAMILIES:
            added = reference.vectors(family)[len(buffers.vectors(family)):]
            self.assertEqual(len(added), 20)
            self.assertTrue(any(len(bytes.fromhex(c['input_hex'])) == 512 and c['capacity'] == 520
                                and c['output_offset'] == 1528 for c in added))
            self.assertTrue(any(c['capacity'] == 0 for c in added))
            self.assertTrue(any(c['output_offset'] == 62 for c in added))
        payload = (508).to_bytes(2, 'little') + b'\xff\x81' + bytes((i * 31 + 5) % 256 for i in range(508))
        self.assertGreater(reference.value('variable_objects', payload), 2**32)

    def test_06_clang_overlay_is_declared_and_preserves_the_base(self):
        addon = tooling.record()['manifest']
        self.assertEqual(addon['composition'], tooling.COMPOSITION)
        self.assertTrue(set(addon['overrides']) <= tooling.ALLOWED_OVERRIDES)
        with runtime.image('cpp') as (base, pinned):
            before = runtime.image_signature(base)
            with tooling.image() as (private, combined):
                self.assertEqual(combined['id'], pinned['id'])
                for name, override in addon['overrides'].items():
                    self.assertEqual(image_manifest.file_sha(base / name), override['base_sha256'])
                    self.assertEqual(image_manifest.file_sha(private / name), override['extension_sha256'])
                    self.assertNotEqual((base / name).stat().st_ino, (private / name).stat().st_ino)
            self.assertEqual(runtime.image_signature(base), before)

    def test_07_unknown_selections_stop_before_workers(self):
        with patch.object(build.process, 'run', side_effect=AssertionError('Compiler worker')):
            for compiler, family in (('unknown', 'record_codec'), ('go', 'unknown')):
                with self.assertRaises(ValueError): build.build(compiler, family)

    def test_08_other_family_and_language_sources_preserve_original_keys(self):
        selected = build.ROOT / build.PREFIX / 'rust/numeric_aggregation.rs'
        original = Path.read_text
        def changed(path, *args, **kwargs):
            raw = original(path, *args, **kwargs)
            return raw + '\n// selected source revision\n' if path == selected else raw
        selections = (('rust', 'record_codec'), ('go', 'record_codec'), ('rust', 'numeric_aggregation'))
        keys = {key: build.action(*key)[0].key for key in selections}
        with patch.object(Path, 'read_text', changed):
            self.assertEqual(build.action('rust', 'record_codec')[0].key, keys['rust', 'record_codec'])
            self.assertEqual(build.action('go', 'record_codec')[0].key, keys['go', 'record_codec'])
            self.assertNotEqual(build.action('rust', 'numeric_aggregation')[0].key, keys['rust', 'numeric_aggregation'])

    def test_09_private_vectors_and_oracle_do_not_enter_compilation(self):
        before = build.action('rust', 'record_codec')[0].key
        with patch.object(reference, 'vectors', return_value=[]), patch.object(reference, 'value', return_value=0):
            self.assertEqual(build.action('rust', 'record_codec')[0].key, before)

    def test_10_cold_compiler_receives_only_selected_trusted_sources(self):
        captured = []
        original = build.process.run
        def checked(command, **kwargs):
            request = json.loads(kwargs['input'])
            self.assertEqual(set(request), {'compiler', 'family', 'files', 'flags'})
            self.assertEqual(set(request['files']), {'program.rs', 'family.rs'})
            captured.append(request)
            return original(command, **kwargs)
        with tempfile.TemporaryDirectory(prefix='matched-cold-build-') as temporary, \
             patch.object(build.process, 'run', side_effect=checked):
            result = build.build('rust', 'record_codec', cache=action_cache.Cache(temporary))
        self.assertEqual(len(captured), 1)
        self.assertEqual(result['public_hex'], self.targets['rust', 'record_codec']['public_hex'])

    def test_11_cold_original_worker_receives_no_expected_answers(self):
        captured = []
        original = reference.process.run
        def checked(command, **kwargs):
            request = json.loads(kwargs['input'])
            self.assertEqual(set(request), {'cases'})
            captured.append(request)
            return original(command, **kwargs)
        with tempfile.TemporaryDirectory(prefix='matched-cold-reference-') as temporary, \
             patch.object(reference.process, 'run', side_effect=checked):
            result = reference.observe('go', 'record_codec', reference.vectors('record_codec')[:1], cache=action_cache.Cache(temporary))
        self.assertEqual(len(captured), 1)
        self.assertEqual(result['observations'][0], reference.expected('record_codec', captured[0]['cases'][0]))

    def test_12_invalid_vectors_and_loaded_source_drift_are_rejected(self):
        cases = reference.vectors('record_codec')[:1]
        for field, value in (('id', True), ('capacity', -1), ('capabilities', ['unknown'])):
            changed = copy.deepcopy(cases)
            changed[0][field] = value
            with self.assertRaises(ValueError): reference.observe('rust', 'record_codec', changed)
        original, source = Path.read_bytes, Path(build.__file__)
        def changed(path):
            raw = original(path)
            return raw + b'\n# source drift\n' if path == source else raw
        with patch.object(Path, 'read_bytes', changed), patch.object(build.process, 'run', side_effect=AssertionError('Worker')):
            with self.assertRaisesRegex(RuntimeError, 'Loaded'): build.build('rust', 'record_codec')

    def test_13_c17_controls_share_machine_observations_across_compilers(self):
        for compiler in build.COMPILERS:
            for family in build.FAMILIES:
                cases, oracle = reference.vectors(family), self.oracle(compiler, family)
                source = self.controls['buffer_solutions'].solution(family)
                if compiler == build.COMPILERS[0]:
                    grade = buffers.grade(source, cases, oracle)
                else:
                    with patch.object(executor, '_invoke', side_effect=AssertionError('Duplicate C machine observation')):
                        grade = buffers.grade(source, cases, oracle)
                self.assertEqual((grade['passed'], grade['total']), (len(cases), len(cases)))
                self.assertEqual(grade['return_provenance'], 'host_emulator_return_v1')

    def test_14_cpp20_controls_match_clang_originals(self):
        for family in build.FAMILIES:
            cases = reference.vectors(family)
            source = self.controls['cpp_buffer_solutions'].solution(family)
            grade = buffers.grade(source, cases, self.oracle('clang20', family), language='cpp')
            self.assertEqual((grade['passed'], grade['total']), (len(cases), len(cases)))

    def test_15_wrong_code_and_wrong_semantic_oracles_cannot_pass(self):
        cases = reference.vectors('record_codec')[:2]
        oracle = self.oracle('rust', 'record_codec', cases)
        source = '''#include <stdint.h>
#include <stddef.h>
int32_t reconstructed(const uint8_t *input, size_t size, uint8_t *output, size_t cap, size_t *length) {
    (void)input; (void)size; (void)output; (void)cap;
    *length = 0;
    return -77;
}
'''
        grade = buffers.grade(source, cases, oracle)
        self.assertEqual((grade['passed'], grade['total']), (0, 2))
        with patch.object(reference, 'value', return_value=0):
            with self.assertRaisesRegex(ValueError, 'independent Python'): reference.calibrate('rust', 'record_codec')

    def test_16_warm_builds_originals_and_c_grades_launch_no_workers(self):
        with patch.object(build.process, 'run', side_effect=AssertionError('Duplicate compiler/original worker')), \
             patch.object(executor, '_invoke', side_effect=AssertionError('Duplicate candidate worker')):
            for compiler in build.COMPILERS:
                for family in build.FAMILIES:
                    self.assertTrue(build.build(compiler, family)['cache_hit'])
                    cases, oracle = reference.vectors(family), self.oracle(compiler, family)
                    self.assertTrue(oracle['cache_hit'])
                    self.assertTrue(buffers.grade(self.controls['buffer_solutions'].solution(family), cases, oracle)['cache_hit'])


if __name__ == '__main__':
    started = evidence.begin()
    with offline.guard():
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MatchedBaselineControls))
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)
