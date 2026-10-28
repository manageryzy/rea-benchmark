#!/usr/bin/python3
"""Matched C++20 lifecycle controls, sharing the C17 original oracle and VM."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import action_cache
from capabilities import build, lifecycle, lifecycle_evidence
from capabilities.cpp_lifecycle import runtime, evidence
from capabilities.cpp_buffer import runtime as buffer_compiler
from grading import offline
from grading.c_executor import runtime as executor


class CppLifecycleControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = action_cache.Cache()
        cls.targets, cls.scenarios, cls.oracles, cls.sources = {}, {}, {}, {}
        for family in lifecycle.FAMILIES:
            cls.targets[family] = lifecycle.original(family, cache=cls.cache)
            cls.scenarios[family] = lifecycle.vectors(family)
            cls.oracles[family] = lifecycle.reference(cls.targets[family], cls.scenarios[family], cache=cls.cache)
            cls.sources[family] = (build.SUITE / 'controls/lifecycle_cpp' / (family + '.cpp')).read_text()

    def grade(self, family, source=None, scenarios=None):
        selected = self.scenarios[family] if scenarios is None else scenarios
        oracle = self.oracles[family] if scenarios is None else lifecycle.reference(self.targets[family], selected, cache=self.cache)
        return lifecycle.grade(self.sources[family] if source is None else source, family, selected, oracle,
                               language='cpp', cache=self.cache)

    def test_01_gold_classes_match_all_original_calls(self):
        for family in lifecycle.FAMILIES:
            with self.subTest(family=family):
                result = self.grade(family)
                self.assertEqual(result['passed'], result['total'], result)
                self.assertTrue(result['compiled'])
                self.assertEqual(result['return_provenance'], 'host_emulator_return_v1')

    def test_02_equivalent_cpp_arithmetic(self):
        source = self.sources['session_state'].replace('checksum_ * multiplier_ + input + count_',
                                                       'input + count_ + checksum_ * multiplier_')
        self.assertNotEqual(source, self.sources['session_state'])
        result = self.grade('session_state', source, self.scenarios['session_state'][:1])
        self.assertEqual(result['passed'], result['total'])

    def test_03_cpp_callback_argument_defect_is_separate_from_state_score(self):
        source = self.sources['callback_policy'].replace('callback(operation, argument, index)',
                                                       'callback(operation, index, argument)')
        self.assertNotEqual(source, self.sources['callback_policy'])
        result = self.grade('callback_policy', source, self.scenarios['callback_policy'][:1])
        self.assertLess(result['passed'], result['total'])
        self.assertLess(result['capabilities']['C5']['passed'], result['capabilities']['C5']['total'])
        self.assertEqual(result['capabilities']['C6']['passed'], result['capabilities']['C6']['total'])

    def test_04_cpp_reset_defect_fails(self):
        source = self.sources['session_state'].replace('count_ = 0;', '/* defect: retain count */')
        self.assertNotEqual(source, self.sources['session_state'])
        result = self.grade('session_state', source, self.scenarios['session_state'][:1])
        self.assertLess(result['passed'], result['total'])

    def test_05_cached_grade_does_not_compile_or_execute_again(self):
        first = self.grade('session_state')
        with patch.object(runtime, 'compile', side_effect=AssertionError('Repeated C++ compile')), \
             patch.object(executor, 'invoke', side_effect=AssertionError('Repeated VM')):
            second = self.grade('session_state')
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])

    def test_06_cpp_header_changes_do_not_invalidate_old_buffer_compilation(self):
        first = buffer_compiler.compile_action('int unused;')[0]
        lifecycle_first = runtime.compile_action(self.sources['session_state'])[0]
        real = Path.read_bytes
        def changed(path):
            value = real(path)
            return value + b'\n// new lifecycle declaration revision\n' if path == runtime.ROOT / (runtime.PREFIX + 'contract.hpp') else value
        with patch.object(Path, 'read_bytes', changed):
            self.assertEqual(first.key, buffer_compiler.compile_action('int unused;')[0].key)
            self.assertNotEqual(lifecycle_first.key, runtime.compile_action(self.sources['session_state'])[0].key)

    def test_07_old_buffer_header_is_not_a_lifecycle_input(self):
        first = runtime.compile_action(self.sources['session_state'])[0]
        real = Path.read_bytes
        def missing(path):
            if path == runtime.ROOT / runtime.ALIAS:
                raise FileNotFoundError('Unused buffer declaration is unavailable')
            return real(path)
        with patch.object(Path, 'read_bytes', missing):
            second = runtime.compile_action(self.sources['session_state'])[0]
        self.assertEqual(first.key, second.key)

    def test_08_declared_alias_and_compiled_source_are_bound(self):
        value = runtime.compile(self.sources['session_state'], cache=self.cache)
        self.assertEqual(value['status'], 'compiled')
        self.assertEqual(value['runtime']['sources_sha256'][runtime.ALIAS],
                         runtime.base.sha(runtime.header().encode()))
        self.assertEqual(runtime.policy()['source_aliases'], {runtime.ALIAS: runtime.PREFIX + 'contract.hpp'})

    def test_09_cpp_source_budget_excludes_trusted_header(self):
        source = self.sources['session_state']
        maximum = runtime.policy()['limits']['source_bytes']
        bounded = source + '\n/*' + 'x' * (maximum - len(source.encode()) - 5) + '*/'
        self.assertEqual(len(bounded.encode()), maximum)
        runtime.compile_action(bounded)
        with self.assertRaises(ValueError): runtime.compile_action(bounded + 'x')
        with self.assertRaises(ValueError): runtime.compile_action(source + '\0')

    def test_10_compiler_drift_is_rejected_before_worker_launch(self):
        action, request, frozen, platform, policy = runtime.compile_action(self.sources['session_state'])
        changed = {**frozen, runtime.ALIAS: frozen[runtime.ALIAS] + b'\n// changed'}
        with patch.object(runtime, 'sources', return_value=changed), \
             patch.object(runtime.images, 'image', side_effect=AssertionError('Worker launched after drift')):
            with self.assertRaisesRegex(RuntimeError, 'changed before launch'):
                runtime._compile(request, frozen, platform, policy)

    def test_11_mismatched_public_function_signature_is_compile_failure(self):
        source = 'extern "C" std::uint64_t reconstructed() { return 0; }'
        value = runtime.compile(source, cache=self.cache)
        self.assertEqual(value['status'], 'compile_error')
        self.assertIsNone(value['elf'])

    def test_12_source_bound_gate_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / 'receipt.json'
            sources, pins = {'fixture': 'a' * 64}, {'fixture': 'image'}
            valid = {'schema_version': 1, 'passed': True, 'checks': 12, 'failed': 0,
                     'model_api_calls': 0, 'inputs_sha256': sources, 'pins': pins}
            token = evidence._controls.set(False)
            try:
                with patch.object(evidence, 'RECEIPT', receipt), patch.object(evidence, 'fingerprints', return_value=sources), \
                     patch.object(evidence, 'pins', return_value=pins):
                    for key, wrong in [('passed', False), ('checks', 11), ('failed', 1), ('inputs_sha256', {}), ('pins', {})]:
                        receipt.write_bytes(action_cache.canonical({**valid, key: wrong}))
                        with self.subTest(field=key), self.assertRaises(RuntimeError): evidence.require_validation()
                    receipt.write_bytes(action_cache.canonical(valid))
                    evidence.require_validation()
            finally:
                evidence._controls.reset(token)


def main():
    started = evidence.begin()
    with evidence.validation_controls(), lifecycle_evidence.validation_controls(), offline.guard():
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(CppLifecycleControls)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence.finish(started, result)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
