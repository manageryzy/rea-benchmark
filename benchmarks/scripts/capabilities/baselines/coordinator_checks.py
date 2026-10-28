"""Matched-baseline checks mixed into the existing typed coordinator suite."""
import copy
import importlib.util
import json
import socket
from unittest.mock import patch

import action_cache
import records
import run
from capabilities import config, coordinator, store, tasks as common_tasks
from . import build, tasks, task_runtime, reference, rust_controls, go_controls


def gold_source(language, family):
    if language == 'rust': return rust_controls.solution(family)
    if language == 'go': return go_controls.solution(family)
    name = 'cpp_buffer_solutions' if language == 'cpp' else 'buffer_solutions'
    path = tasks.ROOT / 'benchmarks/suites/capabilities-v1/controls' / (name + '.py')
    selected = importlib.util.spec_from_file_location('matched_' + name, path)
    module = importlib.util.module_from_spec(selected)
    selected.loader.exec_module(module)
    return module.solution(family)


class MatchedCoordinatorChecks:
    def test_62_matched_inventory_has_72_paired_conditions_in_six_families(self):
        selected = [row for row in common_tasks.declared() if row['profile']['kind'] == 'matched_buffer']
        self.assertEqual(len(selected), 72)
        self.assertEqual(len({row['family_id'] for row in selected}), 6)
        for family in build.FAMILIES:
            for compiler, languages in tasks.LANGUAGES.items():
                points = [common_tasks.frozen(family + '_' + compiler + '_' + language + '_' + condition)
                          for language in languages for condition in ('recover', 'codegen')]
                self.assertEqual(len({row['build_id'] for row in points}), 1)
                self.assertEqual(len({row['binary_sha256'] for row in points}), 1)
                self.assertEqual(len({row['public_package_sha256'] for row in points}), 4)
                self.assertEqual(points[0]['build_id'], build.action(compiler, family)[0].key)
                for row in points:
                    self.assertEqual(row['source_language'], 'cpp' if compiler == 'clang20' else compiler)
                    self.assertEqual(row['capability_ids'], ['C1', 'C2', 'C3', 'C4'])

    def test_63_matched_artifacts_and_cache_reuse_work_in_both_harnesses(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from grading.c_executor import runtime as executor
        from . import go_execute, go_runtime, rust_runtime
        models = [next(model for model in records.models().values() if model['harness'] == harness)
                  for harness in ('deepseek-harness', 'zcode')]
        for model in models:
            for profile, language in (('clang20_c_recover', 'c'), ('clang20_cpp_recover', 'cpp'),
                                      ('rust_rust_recover', 'rust'), ('go_go_recover', 'go')):
                with self.subTest(harness=model['harness'], profile=profile):
                    directory, entry, task, grade = self.fixture('record_codec_' + profile, model)
                    self.assertEqual(task['language'], language)
                    self.assertEqual(task['output_language'], language)
                    self.assertEqual(task['artifact_schema'], {tasks.FILENAMES[language]: 1})
                    self.assertTrue(grade['task_success'])
                    selected, path = store.read_selected(directory, task=task)
                    self.assertTrue(selected['task_success'])
                    binding = records.read((directory / path).with_name('manifest.json'))['task_binding']
                    for field in ('family_id', 'build_id', 'output_language', 'original_compiler'):
                        self.assertEqual(binding[field], task[field])
                        incomplete = {key: value for key, value in binding.items() if key != field}
                        with self.assertRaisesRegex(ValueError, 'Matched private selection'):
                            store.spec(incomplete)
                    before = {name: records.sha(directory / name) for name in
                              ('answer.md', 'task.json', 'result.json', 'completed.json', 'grade.json')}
                    with patch.object(run, 'launch_model', side_effect=AssertionError('Provider')) as launch, \
                         patch.object(socket.socket, 'connect', side_effect=AssertionError('Network')), \
                         patch.object(executor, '_invoke', side_effect=AssertionError('C worker')), \
                         patch.object(go_execute, '_invoke', side_effect=AssertionError('Go worker')), \
                         patch.object(go_runtime, '_invoke', side_effect=AssertionError('Go compiler')), \
                         patch.object(rust_runtime, '_invoke', side_effect=AssertionError('Rust compiler')):
                        self.assertEqual(run.execute(entry, model)['status'], 'cache_hit')
                        self.assertEqual(run.execute(entry, model)['status'], 'cache_hit')
                    launch.assert_not_called()
                    self.assertEqual(before, {name: records.sha(directory / name) for name in before})

    def test_64_foreign_originals_pair_c_and_native_generation_controls(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        for compiler, native in (('rust', 'rust'), ('go', 'go')):
            values = [self.fixture('numeric_aggregation_' + compiler + '_' + language + '_codegen')
                      for language in ('c', native)]
            self.assertEqual(values[0][2]['binary_sha256'], values[1][2]['binary_sha256'])
            for _, _, task, grade in values:
                self.assertTrue(grade['task_success'])
                self.assertEqual(grade['return_provenance'], 'host_emulator_return_v1')
                self.assertEqual(task['source_language'], compiler)

    def test_65_modified_matched_oracle_stops_before_provider_resolution(self):
        case = common_tasks.frozen('record_codec_go_go_recover')
        value = json.loads((tasks.ROOT / case['grading_spec']).read_bytes())
        value['oracle']['observations'][0]['status'] = -77
        raw = action_cache.canonical(value)
        path = tasks.CACHE / 'grade-specs' / (tasks.sha(raw) + '.json')
        if not path.exists(): path.write_bytes(raw)
        revised = {**case, 'grading_spec': str(path.relative_to(tasks.ROOT)), 'grading_spec_sha256': tasks.sha(raw)}
        entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': case['id'],
                 'language': 'go', 'model': self.model['id'], 'condition': 'paged-evidence-v2',
                 'directory': '.cache/benchmark-runtime/rejected-matched-control'}
        settings, families = config.load()
        with patch.object(common_tasks, 'frozen', return_value=revised), \
             patch.object(config, 'load', return_value=({**settings, 'inference_enabled': True}, families)), \
             patch('capabilities.preparation.inspect', return_value={'status': 'ready', 'case': revised}):
            with self.assertRaisesRegex(ValueError, 'oracle'):
                coordinator.prepare(entry, self.model, materialize=True,
                                    attestor=lambda *a, **k: self.fail('Provider resolved before invalid oracle'))

    def test_66_matched_vector_revision_reuses_source_and_machine_observations(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from . import go_execute, go_runtime
        from capabilities import buffers
        from grading.artifact_capture import extract
        directory, entry, task, old = self.fixture('record_codec_go_go_recover')
        original = {name: records.sha(directory / name) for name in ('answer.md', 'task.json', 'result.json', 'completed.json')}
        case = common_tasks.frozen(entry['case'])
        value = copy.deepcopy(store.spec(task))
        value['cases'] = [value['cases'][index] for index in (0, 2, 1)]
        # A whole-grade cache hit need not carry the lower observation cache.
        # Establish that precondition here rather than relying on test ordering.
        source = extract((directory / 'answer.md').read_bytes(), task['artifact_schema'])['solution.go'].decode()
        compiled = go_runtime.compile(source)
        self.assertEqual(compiled['status'], 'compiled')
        for invocation_case in value['cases']:
            go_execute.invoke(compiled['elf'], buffers.invocation(invocation_case))
        value['oracle'] = reference.observe('go', 'record_codec', value['cases'])
        raw = action_cache.canonical(value)
        path = tasks.CACHE / 'grade-specs' / (tasks.sha(raw) + '.json')
        if not path.exists(): path.write_bytes(raw)
        revised = {**case, 'grading_spec': str(path.relative_to(tasks.ROOT)), 'grading_spec_sha256': tasks.sha(raw)}
        with patch.object(common_tasks, 'frozen', return_value=revised), \
             patch.object(go_execute, '_invoke', side_effect=AssertionError('Repeated machine observation')), \
             patch.object(go_runtime, '_invoke', side_effect=AssertionError('Repeated compilation')), \
             patch.object(run, 'launch_model', side_effect=AssertionError('Provider')):
            changed = run.execute(entry, self.model)
            selected, _ = store.read_selected(directory)
        self.assertEqual(changed['status'], 'cache_hit')
        self.assertTrue(changed['offline_regraded'])
        self.assertEqual((selected['passed'], selected['total']), (3, 3))
        self.assertNotEqual(selected['grading_id'], old['grading_id'])
        self.assertEqual(original, {name: records.sha(directory / name) for name in original})

    def test_67_stale_native_candidate_gate_stops_before_attestation(self):
        from . import go_evidence
        case = common_tasks.frozen('record_codec_go_go_recover')
        entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': case['id'],
                 'language': 'go', 'model': self.model['id'], 'condition': 'paged-evidence-v2',
                 'directory': '.cache/benchmark-runtime/rejected-matched-control'}
        settings, families = config.load()
        with patch.object(go_evidence, 'current', return_value=False), \
             patch.object(config, 'load', return_value=({**settings, 'inference_enabled': True}, families)), \
             patch('capabilities.preparation.inspect', return_value={'status': 'ready', 'case': case}):
            with self.assertRaisesRegex(RuntimeError, 'Go candidate controls'):
                coordinator.prepare(entry, self.model, materialize=True,
                                    attestor=lambda *a, **k: self.fail('Provider resolved before stale candidate gate'))

    def test_68_matched_source_and_malformed_profiles_fail_without_workers(self):
        from capabilities import submission
        from . import go_runtime
        case = common_tasks.frozen('record_codec_go_go_recover')
        value = json.loads((tasks.ROOT / case['grading_spec']).read_bytes())
        with patch.object(go_runtime, '_invoke', side_effect=AssertionError('Compiler')):
            for answer in ('No source artifact.', '```solution.c\nint x;\n```\n'):
                rejected, files = submission.evaluate(answer, case, value)
                self.assertFalse(rejected['artifact_valid'])
                self.assertEqual((rejected['passed'], rejected['total']), (0, len(value['cases'])))
                self.assertIs(rejected['compiled'], False)
                self.assertEqual(rejected['return_provenance'], 'not_executed')
                self.assertEqual(files, {})
            for kind, extra, total in (
                    ('c_buffer', {'cases': [{}, {}, {}]}, 3),
                    ('cpp_buffer', {'cases': [{}, {}, {}]}, 3),
                    ('c_lifecycle', {'scenarios': [{'steps': [0, 1]}, {'steps': [2]}]}, 3),
                    ('cpp_lifecycle', {'scenarios': [{'steps': [0, 1]}, {'steps': [2]}]}, 3)):
                rejected, files = submission.evaluate('Missing artifact.', case, {'kind': kind, **extra})
                self.assertEqual((rejected['passed'], rejected['total']), (0, total))
                self.assertIs(rejected['compiled'], False)
                self.assertEqual(rejected['return_provenance'], 'not_executed')
                self.assertEqual(files, {})
            for raw in (b'', b'\xff', b'\0', b' ' * 262145):
                result = task_runtime.grade({'solution.go': raw}, value)
                self.assertFalse(result['artifact_valid'])
                self.assertEqual(result['return_provenance'], 'not_executed')
        declaration = next(row for row in common_tasks.declared() if row['task_id'] == case['id'])
        for field, replacement in (('original_compiler', 'unknown'), ('output_language', 'cpp'), ('condition', 'unknown')):
            changed = copy.deepcopy(declaration)
            changed['profile'][field] = replacement
            with self.assertRaises(ValueError): tasks.contract(changed)
