#!/usr/bin/python3
"""Typed artifact/coordinator controls using frozen tasks, never a model call."""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import action_cache
import benchmark_config
import cache
import records
import run
from capabilities import config, coordinator, evidence, locations, preparation, store, tasks
from grading.artifact_capture import extract
from capabilities.baselines.coordinator_checks import MatchedCoordinatorChecks, gold_source
from check_attempt_routing import AttemptRoutingChecks


class TypedCoordinatorControls(AttemptRoutingChecks, MatchedCoordinatorChecks, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = tasks.frozen('record_codec_localize')
        cls.spec = json.loads((action_cache.ROOT / cls.case['grading_spec']).read_bytes())
        cls.answer = locations.answer(cls.spec['truth'])
        cls.model = records.models()['deepseek-v4.1-flash'] if 'deepseek-v4.1-flash' in records.models() else next(
            value for value in records.models().values() if value['harness'] == 'deepseek-harness')

    def fixture(self, case_id=None, model=None, access=None, *, discoverable=False):
        model = model or self.model
        case = tasks.frozen(case_id) if case_id else self.case
        parent = action_cache.ROOT / '.cache/benchmark-runtime'
        if discoverable:
            # Exercise normal offline discovery with a real namespace/action
            # layout, rather than an out-of-tree fixture invisible to its scan.
            parent = action_cache.ROOT / 'benchmarks/runs/capabilities-v1' / model['id'] / case['id']
            parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='typed-coordinator-', dir=parent)
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        if discoverable:
            directory = directory / 'attempt'
            directory.mkdir()
        entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': case['id'],
                 'language': case['source_language'], 'model': model['id'], 'condition': 'paged-evidence-v2', 'status': 'cached',
                 'directory': str(directory.relative_to(action_cache.ROOT)), 'capability_case': case}
        if access is not None:
            entry['evidence_access'] = access
        task = coordinator.prepare(entry, model, materialize=True, offline_fixture=True)
        if case['kind'] == 'optimized_localization':
            from capabilities.optimized_locations import scoring
            answer = scoring.answer(store.spec(task)['truth'])
        elif case['kind'] == 'matched_buffer':
            answer = gold_source(case['output_language'], case['family_id'])
        elif case['kind'] == 'native_abi':
            from capabilities.native_abi import contracts
            answer = contracts.gold(case['source_language'], case['binary_sha256'])
        elif case['kind'] == 'indirect_call':
            from capabilities.indirect_calls import contracts
            spec = store.spec(task)
            answer = contracts.gold(spec['target']['binary_sha256'], spec['target']['variant'])
        elif case['kind'] == 'variant_object':
            from capabilities.variant_objects import contracts
            spec = store.spec(task)
            answer = contracts.gold(spec['target']['binary_sha256'], spec['target']['variant'])
        elif case['kind'] == 'pointer_probe':
            from capabilities.pointers import contracts
            spec = store.spec(task)
            answer = contracts.gold(spec['target']['binary_sha256'], spec['target']['variant'])
        elif case['kind'] == 'abi_probe':
            from capabilities.abi import contracts, variants
            spec = store.spec(task)
            target = spec['target']
            answer = (contracts.abi_claim(target['family'], target['convention'], target['binary_sha256'], target['entries']['original_callee'])
                      if spec['condition'] == 'abi_recover' else variants.gold(target['family'], target['binary_sha256'], target['variant'])
                      if target.get('variant') else contracts.gold(target['family'], target['binary_sha256']))
        elif case['kind'] == 'cpp_buffer':
            import importlib.util
            source = action_cache.ROOT / 'benchmarks/suites/capabilities-v1/controls/cpp_buffer_solutions.py'
            loader = importlib.util.spec_from_file_location('cpp_pair_fixture', source)
            module = importlib.util.module_from_spec(loader); loader.loader.exec_module(module)
            answer = module.solution(case['family_id'])
        elif case['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
            cpp = case['kind'] == 'cpp_lifecycle'
            answer = (tasks.build.SUITE / ('controls/lifecycle_cpp' if cpp else 'controls/lifecycle') /
                      (case['family_id'] + ('.cpp' if cpp else '.c'))).read_text()
        else:
            answer = self.answer
        filename = next(iter(task['artifact_schema']))
        payload = answer.encode() if isinstance(answer, str) else action_cache.canonical(answer)
        raw = b'Evidence explanation.\n```' + filename.encode() + b'\n' + payload + (b'' if payload.endswith(b'\n') else b'\n') + b'```\n'
        (directory / 'answer.md').write_bytes(raw)
        run.save(directory / 'task.json', task, True)
        result = {'model': model['id'], 'environment_profile': task['environment_profile'],
                  'binary_sha256': task['binary_sha256'], 'prompt_sha256': task['prompt_sha256'],
                  'language': task['language'], 'output_language': task['language'],
                  'exit_code': 0, 'timed_out': False}
        run.save(directory / 'result.json', result, True)
        run.save(directory / 'grade.json', {'original_control': 'preserved'}, True)
        grade = run.grade_saved(directory, task)
        completed = {'fingerprint': task['fingerprint'], 'environment_profile': task['environment_profile'],
            'grading_key': cache.grading_key(task, directory),
            'files_sha256': {name: records.sha(directory / name) for name in ('task.json', 'result.json', 'answer.md', 'grade.json')}}
        run.save(directory / 'completed.json', completed, True)
        return directory, entry, task, grade

    def test_92_legacy_hit_and_changed_action_use_distinct_immutable_attempts(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, old, grade = self.fixture()
        entry = {**entry, 'inference_trial': entry['directory']}
        originals = {name: records.sha(directory / name) for name in (
            'task.json', 'result.json', 'answer.md', 'grade.json', 'completed.json')}
        settings, families = config.load()
        changed = copy.deepcopy(settings)
        changed['runtime']['tool_timeout_ms'] += 1
        with patch.object(coordinator, 'attempt_directory', return_value=directory):
            expected = coordinator.prepare(entry, self.model, materialize=False, offline_fixture=True)
            self.assertEqual(old['fingerprint'], expected['fingerprint'])
            self.assertEqual(expected['directory'], entry['directory'])
            self.assertEqual(cache.validate(entry, self.model, expected)['status'], 'cache_hit')
            with patch.object(config, 'load', return_value=(changed, families)):
                task = coordinator.prepare(entry, self.model, offline_fixture=True)
                physical = action_cache.ROOT / task['directory']
                self.addCleanup(shutil.rmtree, physical, ignore_errors=True)
                self.assertEqual(physical.name, task['fingerprint'])
                self.assertNotEqual(task['fingerprint'], old['fingerprint'])
                self.assertEqual(cache.inference_trial(task), entry['directory'])
                physical.mkdir()
                run.save(physical / 'task.json', task, True)
                (physical / 'answer.md').write_bytes((directory / 'answer.md').read_bytes())
                run.save(physical / 'result.json', records.read(directory / 'result.json'), True)
                run.save(physical / 'grade.json', {'offline_routing_fixture': True}, True)
                run.grade_saved(physical, task)
                completed = {'fingerprint': task['fingerprint'], 'environment_profile': task['environment_profile'],
                    'grading_key': cache.grading_key(task, physical),
                    'files_sha256': {name: records.sha(physical / name) for name in originals if name != 'completed.json'}}
                run.save(physical / 'completed.json', completed, True)
                resolved = {**entry, 'directory': task['directory'], 'resolved_fingerprint': task['fingerprint']}
                self.assertEqual(run.execute(resolved, self.model)['status'], 'cache_hit')
                self.assertEqual(run.execute(resolved, self.model)['provider_launches'], 0)
        self.assertEqual(originals, {name: records.sha(directory / name) for name in originals})

    def test_01_named_blocks_preserve_exact_bytes(self):
        raw = b'Explanation\r\n```solution.c\r\nint a;\r\n/* x */\r\n```\r\n'
        self.assertEqual(extract(raw, {'solution.c': 1}), {'solution.c': b'int a;\r\n/* x */\r\n'})

    def test_02_duplicate_unknown_and_unclosed_blocks_fail(self):
        for raw in (b'```json\n{}\n```\n', b'```locations.json\n{}',
                    b'```locations.json\n{}\n```\n```locations.json\n{}\n```\n',
                    b'```locations.json\n{}\n```\n```solution.c\nint x;\n```\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                extract(raw, {'locations.json': 1})

    def test_03_existing_grade_router_uses_typed_artifacts(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, grade = self.fixture()
        self.assertTrue(grade['task_success'])
        self.assertEqual(grade['passed'], grade['total'])
        self.assertIsNone(task['output_language'])
        self.assertFalse((directory / 'reconstructed.cpp').exists())
        selected, path = store.read_selected(directory, task=task)
        self.assertEqual(selected['grading_id'], grade['grading_id'])
        self.assertTrue((directory / path).is_file())
        self.assertEqual(json.loads((directory / 'grade.json').read_bytes()), {'original_control': 'preserved'})

    def test_04_two_cache_only_coordinator_executions(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, grade = self.fixture()
        original = {name: records.sha(directory / name) for name in ('answer.md', 'task.json', 'result.json', 'grade.json', 'completed.json')}
        with (patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')) as launch,
              patch.object(socket.socket, 'connect', side_effect=AssertionError('Network'))):
            first = run.execute(entry, self.model)
            second = run.execute(entry, self.model)
        self.assertEqual(first['status'], 'cache_hit')
        self.assertEqual(second['status'], 'cache_hit')
        self.assertEqual(first['provider_launches'], second['provider_launches'], 0)
        launch.assert_not_called()
        self.assertEqual(original, {name: records.sha(directory / name) for name in original})

    def test_05_grader_revision_reuses_inference_and_keeps_old_grade(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, old = self.fixture()
        value = copy.deepcopy(self.spec)
        value['truth']['roots'] = [0]
        raw = action_cache.canonical(value)
        path = tasks.CACHE / 'grade-specs' / (hashlib.sha256(raw).hexdigest() + '.json')
        if not path.exists():
            path.write_bytes(raw)
        revised = {**self.case, 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                   'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
        original = {name: records.sha(directory / name) for name in ('answer.md', 'task.json', 'result.json', 'completed.json')}
        old_path = directory / 'grades' / store.VERSION / old['grading_id'] / 'grade.json'
        old_sha = records.sha(old_path)
        with (patch.object(tasks, 'frozen', return_value=revised),
              patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')) as launch):
            expected = run.prepare_task(entry, self.model, materialize=False)
            self.assertEqual(expected['fingerprint'], task['fingerprint'])
            self.assertTrue(cache.validate(entry, self.model, expected)['grading_pending'])
            result = run.execute(entry, self.model)
            self.assertFalse(result['grading_pending'])
            self.assertFalse(cache.validate(entry, self.model, expected)['grading_pending'])
        launch.assert_not_called()
        self.assertEqual(original, {name: records.sha(directory / name) for name in original})
        self.assertEqual(records.sha(old_path), old_sha)
        new, _ = store.read_selected(directory)
        self.assertNotEqual(new['grading_id'], old['grading_id'])
        self.assertFalse(new['capabilities']['L1']['top1'])

    def test_06_selected_artifact_tampering_is_rejected(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, grade = self.fixture()
        selection = json.loads((directory / 'selected-grade.json').read_bytes())
        path = directory / selection['path']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'changed'):
            store.read_selected(directory)

    def test_07_disabled_profiles_cannot_launch(self):
        settings, families = config.load()
        with patch.object(config, 'load', return_value=({**settings, 'inference_enabled': False}, families)):
            self.assertEqual(coordinator.entries(self.model), [])
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                coordinator.prepare({'directory': '.cache/no-provider', 'case': self.case['id']}, self.model)
        enabled = {**settings, 'inference_enabled': True, 'profiles': [
            {**profile, 'enabled': False if profile['id'] == self.case['profile_id'] else profile['enabled']}
            for profile in settings['profiles']]}
        with (patch.object(config, 'load', return_value=(enabled, families)),
              patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Disabled profile reached provider')) as provider,
              patch.object(action_cache.Cache, 'publish', side_effect=AssertionError('Disabled profile materialized')) as publish):
            with self.assertRaisesRegex(RuntimeError, 'profile.*disabled'):
                coordinator.prepare({'directory': '.cache/no-provider', 'case': self.case['id']}, self.model)
        provider.assert_not_called()
        publish.assert_not_called()

    def test_08_absence_has_no_public_answer_label(self):
        present, absent = tasks.frozen('record_codec_localize'), tasks.frozen('record_codec_absent_localize')
        first, second = [action_cache.ROOT / value['public_directory'] for value in (present, absent)]
        self.assertEqual((first / 'prompt.txt').read_bytes(), (second / 'prompt.txt').read_bytes())
        self.assertEqual((first / 'tools.json').read_bytes(), (second / 'tools.json').read_bytes())
        p, a = [json.loads((path / 'manifest.json').read_bytes()) for path in (first, second)]
        self.assertEqual({k: v for k, v in p.items() if k not in ('task_id', 'build_id', 'binary_sha256')},
                         {k: v for k, v in a.items() if k not in ('task_id', 'build_id', 'binary_sha256')})
        for path in (first, second):
            raw = (path / 'manifest.json').read_bytes() + (path / 'prompt.txt').read_bytes()
            self.assertNotIn(b'record_codec_absent', raw)
            self.assertNotIn(b'grading_spec', raw)
            self.assertNotIn(b'codec_decision', raw)

    def test_09_planning_is_read_only_and_does_not_spawn(self):
        settings, families = config.load()
        with (patch.object(subprocess, 'Popen', side_effect=AssertionError('Process during planning')),
              patch.object(socket.socket, 'connect', side_effect=AssertionError('Network during planning')),
              patch.object(config, 'load', return_value=({**settings, 'inference_enabled': False}, families))):
            preview = tasks.preview()
            selection = benchmark_config.load()
            selected = run.selected_entries(selection)
        self.assertEqual({row['task_id'] for row in preview}, {row['task_id'] for row in tasks.declared()})
        self.assertFalse(any(row['enabled'] for row in preview))
        self.assertTrue(selected)
        self.assertFalse(any(row['suite'] == 'capabilities-v1' for row in selected))

    def test_10_bulk_offline_route_keeps_typed_inference(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, old = self.fixture()
        source = {**entry, 'task': task}
        with (patch.object(coordinator, 'saved_entries', return_value=[source]),
              patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')) as launch):
            outcomes, failures, preserved = coordinator.regrade_saved(benchmark_config.load(), run.grade_saved)
        self.assertFalse(failures)
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(preserved)
        self.assertEqual(outcomes[0]['grading_id'], old['grading_id'])
        launch.assert_not_called()

    def test_11_adapter_language_alias_uses_output_contract_for_both_harnesses(self):
        models = (self.model, next(value for value in records.models().values() if value['harness'] == 'zcode'))
        with (patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')),
              patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider attestation')),
              patch.object(socket.socket, 'connect', side_effect=AssertionError('Network'))):
            for model in models:
                for case_id, output in (('record_codec_localize', None), ('record_codec_c_recover', 'c')):
                    with self.subTest(harness=model['harness'], case=case_id):
                        entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1',
                                 'case': case_id, 'directory': '.cache/no-provider'}
                        task = coordinator.prepare(entry, model, materialize=False, offline_fixture=True)
                        self.assertEqual(task['source_language'], 'cpp')
                        self.assertEqual(task['output_language'], output)
                        self.assertEqual(task['language'], output)
                        self.assertEqual(task['inference_identity']['language'], 'cpp')

    def test_12_cached_receipt_cannot_invent_output_language(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, grade = self.fixture()
        result = records.read(directory / 'result.json')
        result['language'] = result['output_language'] = 'cpp'
        run.save(directory / 'result.json', result)
        completed = records.read(directory / 'completed.json')
        completed['files_sha256']['result.json'] = records.sha(directory / 'result.json')
        run.save(directory / 'completed.json', completed)
        with self.assertRaisesRegex(ValueError, 'output language'):
            coordinator.validate_cache(entry, self.model, task)

    def test_13_score_selection_changes_only_grading_action(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, old = self.fixture()
        revised = {**task, 'capability_ids': ['L1']}
        preserved = {name: records.sha(directory / name) for name in ('answer.md', 'task.json', 'result.json', 'completed.json')}
        old_path = directory / 'grades' / store.VERSION / old['grading_id'] / 'grade.json'
        old_sha = records.sha(old_path)
        self.assertNotEqual(store.action(directory, task).key, store.action(directory, revised).key)
        new = store.grade(directory, revised)
        self.assertEqual(new['total'], 1)
        self.assertEqual(set(new['capabilities']), {'L1'})
        self.assertNotEqual(new['grading_id'], old['grading_id'])
        self.assertEqual(task['fingerprint'], revised['fingerprint'])
        self.assertEqual(old_sha, records.sha(old_path))
        self.assertEqual(preserved, {name: records.sha(directory / name) for name in preserved})

    def test_14_existing_native_preparation_keeps_optional_suite_field(self):
        experiment = next(item for item in records.registry()['experiments']
                          if item['suite'] == 'stl-native-v2' and item['role'] == 'primary')
        entry = {'experiment': experiment['id'], 'case': 'rust-data-v2', 'directory': 'unused-offline'}
        with (patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')),
              patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider attestation')),
              patch.object(socket.socket, 'connect', side_effect=AssertionError('Network'))):
            task = run.prepare_task(entry, records.models()['glm-5.3'], offline_fixture=True)
        self.assertEqual(task['language'], 'rust')
        self.assertEqual(task['track'], 'core')
        self.assertTrue(task['offline_fixture'])

    def test_15_type_and_abi_artifacts_use_existing_coordinator_and_cache(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        for profile, capability in (('layout_recover', 'T2'), ('abi_recover', 'T5')):
            with self.subTest(profile=profile):
                directory, entry, task, grade = self.fixture('record_codec_sysv_amd64_' + profile)
                self.assertTrue(grade['task_success'])
                self.assertEqual(grade['total'], 24)
                self.assertEqual(set(grade['capabilities']), {capability})
                with patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')) as launch:
                    first, second = run.execute(entry, self.model), run.execute(entry, self.model)
                launch.assert_not_called()
                self.assertEqual(first['status'], second['status'])
                self.assertEqual(first['status'], 'cache_hit')
                self.assertEqual(first['provider_launches'], second['provider_launches'], 0)
                self.assertIsNone(task['language'])

    def test_16_type_tasks_disclose_only_nonmeasured_premises(self):
        from capabilities.abi import tasks as abi_tasks
        for profile in ('scalar_recover', 'layout_recover', 'abi_recover'):
            metadata = []
            for convention in ('sysv_amd64', 'ms_x64'):
                case = tasks.frozen('record_codec_' + convention + '_' + profile)
                directory = action_cache.ROOT / case['public_directory']
                self.assertEqual({path.name for path in directory.iterdir()}, {'target', 'prompt.txt', 'tools.json', 'manifest.json'})
                value = json.loads((directory / 'manifest.json').read_bytes())
                abi_tasks.validate_manifest(value)
                metadata.append((directory, value))
                if profile == 'scalar_recover':
                    self.assertTrue(all('bits' not in item and 'signed' not in item for item in value['given_facts']))
                    self.assertNotIn('unsigned32', json.dumps(value['given_facts']))
                if profile == 'layout_recover':
                    self.assertTrue(all(not set(item) & {'offset', 'size', 'alignment', 'stride'} for item in value['given_facts']))
                if profile == 'abi_recover':
                    self.assertNotIn('known_abi', value)
                    bad = {**value, 'known_abi': convention}
                    with self.assertRaisesRegex(ValueError, 'disclose'):
                        abi_tasks.validate_manifest(bad)
                raw = (directory / 'manifest.json').read_bytes() + (directory / 'prompt.txt').read_bytes()
                for forbidden in (b'original_obj_hex', b'grading_spec', b'ORIGINAL_MS_ABI=', b'compile_worker.py'):
                    self.assertNotIn(forbidden, raw)
            if profile == 'abi_recover':
                self.assertEqual((metadata[0][0] / 'prompt.txt').read_bytes(), (metadata[1][0] / 'prompt.txt').read_bytes())

    def test_17_private_probe_vectors_regrade_without_new_inference_or_execution(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, old = self.fixture('numeric_aggregation_sysv_amd64_layout_recover')
        value = store.spec(task)
        value['nonces'] = value['nonces'][:4]
        raw = action_cache.canonical(value)
        path = tasks.CACHE / 'grade-specs' / (hashlib.sha256(raw).hexdigest() + '.json')
        if not path.exists(): path.write_bytes(raw)
        case = tasks.frozen(entry['case'])
        revised = {**case, 'grading_spec': str(path.relative_to(action_cache.ROOT)), 'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
        hashes = {name: records.sha(directory / name) for name in ('answer.md', 'task.json', 'result.json', 'completed.json')}
        from capabilities.abi import runtime as abi_runtime
        with (patch.object(tasks, 'frozen', return_value=revised),
              patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')),
              patch.object(abi_runtime, '_compile', side_effect=AssertionError('Probe compilation')),
              patch.object(abi_runtime.executor, '_invoke', side_effect=AssertionError('Executor launch'))):
            expected = run.prepare_task(entry, self.model, materialize=False)
            self.assertEqual(expected['fingerprint'], task['fingerprint'])
            self.assertTrue(cache.validate(entry, self.model, expected)['grading_pending'])
            run.execute(entry, self.model)
        selected, _ = store.read_selected(directory)
        self.assertEqual(selected['total'], 8)
        self.assertNotEqual(selected['grading_id'], old['grading_id'])
        self.assertEqual(hashes, {name: records.sha(directory / name) for name in hashes})

    def test_18_typed_admission_accepts_exact_type_and_abi_names(self):
        from capabilities.abi import contracts
        from grading import artifacts
        case = tasks.frozen('record_codec_sysv_amd64_abi_recover')
        target = store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1})['target']
        value = contracts.abi_claim('record_codec', 'sysv_amd64', target['binary_sha256'], target['entries']['original_callee'])
        admitted = artifacts.admit({'abi.json': action_cache.canonical(value)}, {'abi.json': 1})
        self.assertEqual(admitted['abi.json']['value'], value)

    def test_19_private_grading_data_is_checked_before_provider_or_materialization(self):
        settings, families = config.load()
        enabled = {**settings, 'inference_enabled': True}
        entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': self.case['id'],
                 'directory': '.cache/benchmark-runtime/private-spec-admission-control'}
        for defect in ('digest', 'missing'):
            with self.subTest(defect=defect):
                case = {**self.case}
                if defect == 'digest':
                    case['grading_spec_sha256'] = '0' * 64
                else:
                    case['grading_spec'] = '.cache/benchmark-capabilities/grade-specs/missing-before-provider.json'
                with (patch.object(tasks, 'frozen', return_value=case),
                      patch.object(config, 'load', return_value=(enabled, families)),
                      patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider preparation')) as provider,
                      patch.object(action_cache.Cache, 'publish', side_effect=AssertionError('Runtime materialization')) as publish):
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        coordinator.prepare(entry, self.model, materialize=True)
                provider.assert_not_called()
                publish.assert_not_called()

    def test_20_cpp_pair_uses_same_coordinator_and_valid_cache(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        for profile in ('cpp_recover', 'cpp_codegen'):
            directory, entry, task, grade = self.fixture('record_codec_' + profile)
            self.assertTrue(grade['task_success'])
            self.assertEqual(task['language'], 'cpp')
            self.assertEqual(task['artifact_schema'], {'solution.cpp': 1})
            with patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')) as launch:
                first, second = run.execute(entry, self.model), run.execute(entry, self.model)
            self.assertEqual(first['status'], second['status'])
            self.assertEqual(first['status'], 'cache_hit')
            launch.assert_not_called()

    def test_21_cpp_private_vector_revision_reuses_compile_and_execution(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities import buffers, build
        from capabilities.cpp_buffer import runtime as compiler
        from grading.c_executor import runtime as executor
        directory, entry, task, old = self.fixture('record_codec_cpp_recover')
        value = store.spec(task)
        value['cases'] = value['cases'][:2]
        oracle = buffers.reference(build.build('record_codec'), value['cases'])
        value['oracle'] = {key: item for key, item in oracle.items() if key != 'cache_hit'}
        raw = action_cache.canonical(value)
        path = tasks.CACHE / 'grade-specs' / (hashlib.sha256(raw).hexdigest() + '.json')
        if not path.exists(): path.write_bytes(raw)
        revised = {**tasks.frozen(entry['case']), 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                   'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
        original = {name: records.sha(directory / name) for name in ('answer.md', 'task.json', 'result.json', 'completed.json')}
        with (patch.object(tasks, 'frozen', return_value=revised),
              patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')),
              patch.object(compiler, '_compile', side_effect=AssertionError('Compiler launch')),
              patch.object(executor, '_invoke', side_effect=AssertionError('VM launch'))):
            expected = run.prepare_task(entry, self.model, materialize=False)
            self.assertEqual(expected['fingerprint'], task['fingerprint'])
            self.assertTrue(cache.validate(entry, self.model, expected)['grading_pending'])
            run.execute(entry, self.model)
        selected, _ = store.read_selected(directory)
        self.assertNotEqual(old['grading_id'], selected['grading_id'])
        self.assertEqual(selected['total'], 2)
        self.assertEqual(original, {name: records.sha(directory / name) for name in original})

    def test_22_cpp_artifact_and_output_language_cannot_be_mislabeled(self):
        case = tasks.frozen('record_codec_cpp_recover')
        with self.assertRaisesRegex(ValueError, 'contract'):
            store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1, 'artifact_schema': {'solution.c': 1}})
        metadata = json.loads((action_cache.ROOT / case['public_directory'] / 'manifest.json').read_bytes())
        from capabilities import public
        with self.assertRaisesRegex(ValueError, 'contract'):
            public.validate_buffer_manifest({**metadata, 'output_language': 'c'})

    def test_23_all_counterfactual_profiles_use_normal_cached_coordination(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.abi import contracts
        for family in contracts.FAMILIES:
            for convention in contracts.CONVENTIONS:
                for condition in ('scalar_recover', 'layout_recover'):
                    directory, entry, task, grade = self.fixture(family + '_' + convention + '_' + condition + '_counterfactual')
                    self.assertTrue(grade['task_success'], grade)
                    self.assertIsNone(task['language'])
                    with patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')) as launch:
                        first, second = run.execute(entry, self.model), run.execute(entry, self.model)
                    self.assertEqual(first['status'], 'cache_hit')
                    self.assertEqual(second['status'], 'cache_hit')
                    launch.assert_not_called()

    def preparation_fixture(self, name='record_codec_localize'):
        temporary = tempfile.TemporaryDirectory(prefix='capability-preparation-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        local = action_cache.Cache(Path(temporary.name) / 'actions')
        declaration = next(row for row in tasks.declared() if row['task_id'] == name)
        index = tasks.CACHE / 'task-index' / (name + '.json')
        original = index.read_bytes()
        self.addCleanup(index.write_bytes, original)
        value = preparation.prepare(declaration, cache=local)
        return Path(temporary.name), local, declaration, value

    def selection(self, names):
        value = benchmark_config.load()
        return {**value, 'models': [self.model['id']], 'suites': ['capabilities-v1'],
                'experiments': ['capabilities-v1'], 'cases': names, 'condition': 'active'}

    def test_24_unprepared_and_invalid_cells_remain_in_read_only_denominator(self):
        settings, families = config.load()
        names = ['record_codec_localize', 'dispatch_registry_localize', 'record_codec_c_recover']
        def state(row):
            self.assertIn(row['task_id'], names)
            return {'status': 'invalid' if row['task_id'] == names[-1] else 'pending', 'reason': 'controlled unavailable input'}
        with (patch.object(config, 'load', return_value=({**settings, 'inference_enabled': True}, families)),
              patch.object(preparation, 'inspect', side_effect=state),
              patch.object(preparation, 'prepare', side_effect=AssertionError('Preparation during planning')),
              patch.object(subprocess, 'Popen', side_effect=AssertionError('Planning process')),
              patch.object(socket.socket, 'connect', side_effect=AssertionError('Planning network'))):
            rows = coordinator.inspect_selected(run.selected_entries(self.selection(names)))
        self.assertEqual({row['case'] for row in rows}, set(names))
        self.assertTrue(all(row['directory'] is None for row in rows))
        counts = run.scheduled_counts(rows)
        self.assertEqual(counts['scheduled_cells'], 3)
        self.assertEqual(counts['preparation_pending'], 2)
        self.assertEqual(counts['preparation_invalid'], 1)

    def test_25_preparation_cas_restores_missing_package_and_rejects_corruption(self):
        temporary, local, declaration, value = self.preparation_fixture()
        selected = preparation.action(declaration)
        shadow = temporary / 'workspace'
        settings = config.load()[0]
        tools = shadow / settings['analysis_tools_file']
        tools.parent.mkdir(parents=True)
        shutil.copyfile(action_cache.ROOT / settings['analysis_tools_file'], tools)
        with (patch.object(preparation, 'ROOT', shadow), patch.object(tasks, 'ROOT', shadow),
              patch.object(store, 'ROOT', shadow), patch.object(tasks, 'CACHE', shadow / '.cache/benchmark-capabilities'),
              patch.object(preparation, 'action', return_value=selected),
              patch.object(tasks, 'freeze_one', side_effect=AssertionError('Cache restoration rebuilt')),
              patch.object(subprocess, 'Popen', side_effect=AssertionError('Restoration process')),
              patch.object(socket.socket, 'connect', side_effect=AssertionError('Restoration network'))):
            self.assertEqual(preparation.inspect(declaration, cache=local)['status'], 'pending')
            restored = preparation.prepare(declaration, cache=local)
            self.assertEqual(restored, value)
            self.assertEqual(preparation.inspect(declaration, cache=local)['status'], 'ready')
            path = shadow / restored['public_directory'] / 'target'
            damaged = path.read_bytes() + b'corruption'
            path.write_bytes(damaged)
            self.assertEqual(preparation.inspect(declaration, cache=local)['status'], 'invalid')
            with self.assertRaisesRegex(ValueError, 'Conflicting immutable'):
                preparation.prepare(declaration, cache=local)
            self.assertEqual(path.read_bytes(), damaged)

    def test_26_preparation_hash_excludes_reports_prices_run_limits_and_other_sources(self):
        declaration = next(row for row in tasks.declared() if row['task_id'] == 'record_codec_localize')
        expected = preparation.action(declaration).key
        read = Path.read_bytes
        def unrelated(path):
            raw = read(path)
            return raw + b'\n// unrelated change\n' if str(path).endswith(('report.py', 'numeric_aggregation.cpp', 'pricing-20261019.json')) else raw
        with patch.object(Path, 'read_bytes', unrelated):
            self.assertEqual(preparation.action(declaration).key, expected)
        settings, families = config.load()
        changed = copy.deepcopy(settings)
        changed['limits']['timeout_seconds'] += 1
        changed['inference_enabled'] = True
        with patch.object(config, 'load', return_value=(changed, families)):
            self.assertEqual(preparation.action(declaration).key, expected)
        def related(path):
            raw = read(path)
            return raw + b'\n// selected source change\n' if str(path).endswith('/record_codec.cpp') else raw
        with patch.object(Path, 'read_bytes', related):
            self.assertNotEqual(preparation.action(declaration).key, expected)

    def test_27_selected_preparation_runs_once_per_case_across_models(self):
        settings, families = config.load()
        other = next(value for value in records.models().values() if value['id'] != self.model['id'] and value.get('enabled', True))
        first = self.selection(['record_codec_localize'])
        second = {**first, 'models': [other['id']]}
        with patch.object(config, 'load', return_value=({**settings, 'inference_enabled': True}, families)):
            with patch.object(preparation, 'inspect', return_value={'status': 'pending', 'reason': 'missing'}):
                planned = coordinator.entries(self.model, selection=first) + coordinator.entries(other, selection=second)
            with (patch.object(preparation, 'inspect', return_value={'status': 'pending', 'reason': 'missing'}),
                  patch.object(preparation, 'prepare', return_value=self.case) as freeze,
                  patch('grading.toolchains.build'), patch.object(tasks.build, 'setup_tools'),
                  patch.object(tasks, 'pin_snapshot', side_effect=AssertionError('Full inventory during selected run'))):
                ready = coordinator.prepare_selected(planned)
        freeze.assert_called_once()
        self.assertEqual(len(ready), 2)
        self.assertEqual({row['model'] for row in ready}, {self.model['id'], other['id']})
        self.assertTrue(all(row['preparation_status'] == 'ready' for row in ready))

    def test_28_normal_offline_command_prepares_then_reuses_both_caches(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, grade = self.fixture(discoverable=True)
        before = {name: records.sha(directory / name) for name in ('answer.md', 'task.json', 'result.json', 'grade.json', 'completed.json')}
        with tempfile.TemporaryDirectory(prefix='preparation-cli-', dir=action_cache.ROOT / '.cache/benchmark-runtime') as temporary:
            local = action_cache.Cache(temporary)
            inspect, prepare = preparation.inspect, preparation.prepare
            settings, families = config.load()
            selection = self.selection([entry['case']])
            configuration = Path(temporary) / 'selection.json'
            configuration.write_bytes(action_cache.canonical(selection))
            status = Path(temporary) / 'benchmarks/config/benchmark-status.json'
            status.parent.mkdir(parents=True)
            (status.parent / 'execution.json').write_text('{"experiments_stopped":true}')
            with (patch.object(config, 'load', return_value=({**settings, 'inference_enabled': True}, families)),
                  patch.dict(os.environ),
                  patch.object(sys, 'argv', ['benchmark', 'run', '--config', str(configuration), '--offline', '--runtime-ready']),
                  patch.object(preparation, 'inspect', side_effect=lambda row: inspect(row, cache=local)),
                  patch.object(preparation, 'prepare', side_effect=lambda row: prepare(row, cache=local)) as freeze,
                  patch.object(coordinator, 'attempt_directory', return_value=directory),
                  patch.object(run, 'BENCH', Path(temporary) / 'benchmarks'),
                  patch.object(tasks, 'pin_snapshot') as snapshot,
                  patch.object(run, 'finalize') as refresh,
                  patch.object(run, 'launch_model', side_effect=AssertionError('Model launch')) as launch,
                  patch.object(socket.socket, 'connect', side_effect=AssertionError('Network'))):
                pending = coordinator.inspect_selected(run.selected_entries(selection))
                self.assertEqual(pending[0]['status'], 'preparation_pending')
                run.main()
                first = json.loads(status.read_bytes())
                current = coordinator.inspect_selected(run.selected_entries(selection))
                self.assertEqual(current[0]['status'], 'cached')
                run.main()
                second = json.loads(status.read_bytes())
            self.assertEqual(freeze.call_count, 1)
            snapshot.assert_not_called()
            self.assertEqual(refresh.call_count, 2)
            launch.assert_not_called()
            self.assertEqual(first['provider_launches'], second['provider_launches'], 0)
            self.assertEqual(first['cache_hits'], second['cache_hits'], 1)
            self.assertEqual(first['selected_tasks'], second['selected_tasks'], 1)
        self.assertEqual(before, {name: records.sha(directory / name) for name in before})

    def test_29_configuration_and_package_drift_rejected_before_provider_work(self):
        entry = {'suite': 'capabilities-v1', 'experiment': 'capabilities-v1', 'case': self.case['id'],
                 'directory': '.cache/no-provider', 'configuration_sha256': '0' * 64}
        with patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider')):
            with self.assertRaisesRegex(RuntimeError, 'configuration changed'):
                coordinator.prepare(entry, self.model, materialize=False, offline_fixture=True)
            entry.pop('configuration_sha256')
            entry['capability_case'] = {**self.case, 'public_package_sha256': '0' * 64}
            with self.assertRaisesRegex(RuntimeError, 'package changed'):
                coordinator.prepare(entry, self.model, materialize=False, offline_fixture=True)

    def test_30_stopped_cli_does_not_start_preparation(self):
        # This control owns its stop precondition even when invoked while the
        # user's real workspace is running a batch. Restore the exact bytes.
        stop_path = action_cache.ROOT / 'benchmarks/config/execution.json'
        original_stop = stop_path.read_bytes()
        self.addCleanup(stop_path.write_bytes, original_stop)
        run.save(stop_path, {**json.loads(original_stop), 'experiments_stopped': True})
        selected = [{'suite': 'capabilities-v1', 'case': self.case['id'], 'model': self.model['id'],
                     'status': 'preparation_pending', 'directory': None}]
        errors = io.StringIO()
        with (patch.object(sys, 'argv', ['benchmark', 'run']), patch.object(sys, 'stderr', errors),
              patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}),
              patch.object(run, 'selected_entries', return_value=selected),
              patch.object(run, 'needs_installed_runtime', side_effect=AssertionError('Stopped runtime preparation')),
              patch.object(coordinator, 'prepare_selected', side_effect=AssertionError('Stopped preparation')) as prepare,
              patch.object(run, 'launch_model', side_effect=AssertionError('Stopped model'))):
            with self.assertRaises(SystemExit):
                run.main()
        prepare.assert_not_called()
        self.assertIn('Experiments remain stopped', errors.getvalue())

    def test_31_preparation_failure_keeps_denominator_saves_status_and_releases_lock(self):
        selection = self.selection([self.case['id']])
        planned = [{'suite': 'capabilities-v1', 'experiment': 'capabilities-v1', 'case': self.case['id'],
                    'model': self.model['id'], 'condition': 'paged-evidence-v2',
                    'status': 'preparation_pending', 'directory': None}]
        with self.assertRaisesRegex(RuntimeError, 'preparation is incomplete'):
            run.execute(planned[0], self.model)
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            root = Path(temporary)
            refresh = unittest.mock.Mock()
            with (patch.object(run, 'ROOT', root), patch.object(run, 'BENCH', root / 'benchmarks'),
                  patch.object(coordinator, 'prepare_selected', side_effect=ValueError('controlled invalid preparation')),
                  patch.object(run, 'execute', side_effect=AssertionError('Candidate/model execution'))):
                with self.assertRaisesRegex(ValueError, 'controlled invalid'):
                    run.run_batch(planned, selection, offline=True, finalize=refresh)
            receipt = json.loads((root / 'benchmarks/config/benchmark-status.json').read_bytes())
            self.assertEqual(receipt['selected_tasks'], 1)
            self.assertEqual(len(receipt['scheduled_cells']), 1)
            self.assertEqual(receipt['unprocessed_tasks'], 1)
            self.assertEqual(receipt['model_api_calls'], 0)
            self.assertTrue(receipt['preparation_failed'])
            self.assertFalse((root / '.cache/benchmark-runtime/live-batch.lock').exists())
            refresh.assert_not_called()

    def test_32_preparation_drift_and_escaped_outputs_fail_before_index_publication(self):
        temporary, local, declaration, value = self.preparation_fixture()
        first = preparation.action(declaration)
        changed = action_cache.Action('capability_prepare_v1', {'drift': b'changed'})
        with (patch.object(preparation, 'action', side_effect=[first, changed]),
              patch.object(tasks, 'publish_index', side_effect=AssertionError('Published drifting preparation')) as publish):
            with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
                preparation.prepare(declaration, cache=local)
        publish.assert_not_called()
        files = preparation._outputs(value)
        files['record.json'] = action_cache.canonical({**value, 'public_directory': '../escape'})
        malicious = action_cache.Action('capability_prepare_v1', {'control': b'escaped output'})
        local.commit(malicious, files)
        with self.assertRaisesRegex(ValueError, 'immutable package/spec boundary'):
            preparation._read(local, local.lookup(malicious), declaration)

    def test_33_selection_excludes_disabled_unselected_and_wrong_condition_before_inspection(self):
        settings, families = config.load()
        selected = self.selection(['record_codec_localize'])
        enabled = {**settings, 'inference_enabled': True}
        with patch.object(config, 'load', return_value=(enabled, families)):
            with patch.object(preparation, 'inspect', return_value={'status': 'pending', 'reason': 'controlled'}) as inspect:
                self.assertEqual(len(coordinator.entries(self.model, selection=selected)), 1)
                self.assertEqual(inspect.call_count, 1)
            with patch.object(preparation, 'inspect', side_effect=AssertionError('Wrong condition inspected')):
                self.assertEqual(coordinator.entries(self.model, selection={**selected, 'condition': 'legacy-recorded'}), [])
        disabled = copy.deepcopy(enabled)
        next(row for row in disabled['profiles'] if row['id'] == 'localize')['enabled'] = False
        with (patch.object(config, 'load', return_value=(disabled, families)),
              patch.object(preparation, 'inspect', side_effect=AssertionError('Disabled profile inspected'))):
            self.assertEqual(coordinator.entries(self.model, selection=selected), [])

    def test_34_preparation_inventory_keeps_pending_and_invalid_definitions_without_builds(self):
        from capabilities import summary
        declarations = tasks.declared()
        absent = {declarations[0]['task_id']: 'pending', declarations[1]['task_id']: 'invalid'}
        def inspect(row):
            if row['task_id'] in absent:
                return {'status': absent[row['task_id']], 'reason': 'controlled incomplete definition'}
            return {'status': 'ready', 'case': tasks.frozen(row['task_id'])}
        with (patch.object(preparation, 'inspect', side_effect=inspect),
              patch.object(preparation, 'prepare', side_effect=AssertionError('Inventory built an unselected case')),
              patch.object(tasks, '_replace_json') as publish,
              patch.object(subprocess, 'Popen', side_effect=AssertionError('Inventory process')),
              patch.object(socket.socket, 'connect', side_effect=AssertionError('Inventory network'))):
            pins = tasks.pin_snapshot()
        publish.assert_called_once()
        self.assertEqual(pins['task_definitions'], len(declarations))
        self.assertEqual(len(pins['preparation_states']), len(declarations))
        self.assertEqual(len(pins['tasks']), len(declarations) - 2)
        def read(path):
            if Path(path) == evidence.RECEIPT:
                # Inventory reporting is independent of the live coordinator receipt schema.
                # Use an explicitly unvalidated offline fixture, never a claimed pass.
                return action_cache.canonical({'schema_version': 1, 'model_api_calls': 0,
                    'passed': False, 'checks': 0, 'failed': 0, 'inputs_sha256': {}})
            return action_cache.canonical(pins) if Path(path) == summary.PINS else Path(path).read_bytes()
        status = summary.for_report(read)
        self.assertEqual(status['task_definitions'], len(declarations))
        self.assertEqual(status['frozen_tasks'], len(declarations) - 2)
        self.assertFalse(status['frozen_inputs_current'])

    def test_35_termination_during_preparation_releases_lock_and_restores_handler(self):
        selected = [{'suite': 'capabilities-v1', 'experiment': 'capabilities-v1', 'case': self.case['id'],
                     'model': self.model['id'], 'condition': 'paged-evidence-v2',
                     'status': 'preparation_pending', 'directory': None}]
        prior = signal.getsignal(signal.SIGTERM)
        def terminate(rows):
            handler = signal.getsignal(signal.SIGTERM)
            self.assertTrue(callable(handler), 'Preparation has no termination handler')
            handler(signal.SIGTERM, None)
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            root = Path(temporary)
            refresh = unittest.mock.Mock()
            with (patch.object(run, 'ROOT', root), patch.object(run, 'BENCH', root / 'benchmarks'),
                  patch.object(coordinator, 'prepare_selected', side_effect=terminate),
                  patch.object(run, 'execute', side_effect=AssertionError('Execution after cancellation'))):
                with self.assertRaises(KeyboardInterrupt):
                    run.run_batch(selected, self.selection([self.case['id']]), offline=True, finalize=refresh)
            self.assertFalse((root / '.cache/benchmark-runtime/live-batch.lock').exists())
            receipt = json.loads((root / 'benchmarks/config/benchmark-status.json').read_bytes())
            self.assertEqual(receipt['model_api_calls'], 0)
            self.assertEqual(receipt['selected_tasks'], 1)
            refresh.assert_not_called()
        self.assertEqual(signal.getsignal(signal.SIGTERM), prior)

    def test_36_stop_request_during_preparation_cannot_be_cleared_before_model_queues(self):
        from capabilities import registration
        settings, families = config.load()
        enabled = {**settings, 'inference_enabled': True}
        declaration = next(row for row in tasks.declared() if row['task_id'] == self.case['id'])
        # Admission now requires a complete configured cell even when the
        # preparation callback will stop it. Exercise that real schedule path.
        selected = [coordinator._entry(self.model, declaration, enabled, {'status': 'ready', 'case': self.case})]
        selected[0]['status'] = 'preparation_pending'
        with tempfile.TemporaryDirectory(prefix='preparation-stop-', dir=action_cache.ROOT / '.cache/benchmark-runtime') as temporary:
            root = Path(temporary)
            state = root / 'benchmarks/config/execution.json'
            state.parent.mkdir(parents=True)
            state.write_text('{"experiments_stopped":true}')
            def stop(rows):
                self.assertFalse(json.loads(state.read_bytes())['experiments_stopped'])
                run.set_execution_stopped(True)
                return rows
            registry = registration.Registry(root)
            with (registration.validation_controls(),
                  patch.object(config, 'load', return_value=(enabled, families)),
                  patch.object(registration, 'Registry', return_value=registry),
                  patch.object(registration.runtime_attestation, 'provider', side_effect=AssertionError('Provider after preparation stop')),
                  patch.object(run, 'ROOT', root), patch.object(run, 'BENCH', root / 'benchmarks'),
                  patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}),
                  patch.object(coordinator, 'prepare_selected', side_effect=stop),
                  patch.object(run, 'execute', side_effect=AssertionError('Model after preparation stop'))):
                with self.assertRaisesRegex(InterruptedError, 'stopped during preparation'):
                    run.run_batch(selected, self.selection([self.case['id']]))
            self.assertTrue(json.loads(state.read_bytes())['experiments_stopped'])
            self.assertFalse((root / '.cache/benchmark-runtime/live-batch.lock').exists())
            receipt = json.loads((root / 'benchmarks/config/benchmark-status.json').read_bytes())
            self.assertEqual(receipt['provider_launches'], 0)
            self.assertEqual(receipt['selected_tasks'], 1)
            reference = receipt['measurement_registration']
            self.assertTrue(reference['registration_id'])
            self.assertIsNone(reference['binding_id'])
            _, origin = registry.load(reference['registration_id'])
            self.assertTrue(origin['offline_control'])

    def test_37_ready_public_target_cannot_change_during_automatic_preparation(self):
        settings, families = config.load()
        enabled = {**settings, 'inference_enabled': True}
        declaration = next(row for row in tasks.declared() if row['task_id'] == self.case['id'])
        planned = coordinator._entry(self.model, declaration, enabled, {'status': 'ready', 'case': self.case})
        with (patch.object(config, 'load', return_value=(enabled, families)),
              patch.object(preparation, 'inspect', return_value={'status': 'pending', 'reason': 'source changed'}),
              patch.object(preparation, 'prepare', return_value={**self.case, 'public_package_sha256': '0' * 64}),
              patch('grading.toolchains.build'), patch.object(tasks.build, 'setup_tools'),
              patch.object(tasks, 'pin_snapshot', side_effect=AssertionError('Published changed target selection'))):
            with self.assertRaisesRegex(RuntimeError, 'public package changed during preparation'):
                coordinator.prepare_selected([planned])

    def test_38_matched_lifecycle_conditions_use_one_original_and_exact_artifacts(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        for family in ('callback_policy', 'session_state'):
            binaries, entries, packages = set(), set(), set()
            for profile in ('c_lifecycle_recover', 'c_lifecycle_codegen', 'cpp_lifecycle_recover', 'cpp_lifecycle_codegen'):
                with self.subTest(family=family, profile=profile):
                    directory, entry, task, grade = self.fixture(family + '_' + profile)
                    binaries.add(task['binary_sha256'])
                    metadata = records.read(action_cache.ROOT / task['public_directory'] / 'manifest.json')
                    entries.add(metadata['entry_rva'])
                    packages.add(task['public_package_sha256'])
                    cpp = profile.startswith('cpp_')
                    self.assertEqual(task['language'], 'cpp' if cpp else 'c')
                    self.assertEqual(task['output_language'], task['language'])
                    self.assertEqual(task['artifact_schema'], {'solution.cpp' if cpp else 'solution.c': 1})
                    self.assertTrue(grade['task_success'])
                    self.assertEqual(grade['passed'], grade['total'])
                    self.assertEqual(grade['total'], 53)
                    self.assertEqual(grade['return_provenance'], 'host_emulator_return_v1')
            self.assertEqual(len(binaries), 1)
            self.assertEqual(len(entries), 1)
            self.assertEqual(len(packages), 4)

    def test_39_lifecycle_cache_reuse_and_output_labels_in_both_harnesses(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from grading.c_executor import runtime as executor
        from capabilities.cpp_lifecycle import runtime as cpp_compiler
        models = [next(model for model in records.models().values() if model['harness'] == harness)
                  for harness in ('deepseek-harness', 'zcode')]
        for model in models:
            for profile in ('c_lifecycle_recover', 'cpp_lifecycle_recover'):
                with self.subTest(harness=model['harness'], profile=profile):
                    directory, entry, task, grade = self.fixture('callback_policy_' + profile, model)
                    before = {name: records.sha(directory / name) for name in
                              ('task.json', 'result.json', 'answer.md', 'grade.json', 'completed.json')}
                    with patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')) as launch, \
                         patch.object(socket.socket, 'connect', side_effect=AssertionError('Network')), \
                         patch.object(executor, '_invoke', side_effect=AssertionError('Repeated C/VM worker')), \
                         patch.object(cpp_compiler, '_compile', side_effect=AssertionError('Repeated C++ compiler')):
                        first = run.execute(entry, model)
                        second = run.execute(entry, model)
                    self.assertEqual(first['status'], 'cache_hit')
                    self.assertEqual(second['status'], 'cache_hit')
                    launch.assert_not_called()
                    self.assertEqual(before, {name: records.sha(directory / name) for name in before})

    def test_40_lifecycle_public_packages_exclude_private_truth_and_isolate_codegen(self):
        from capabilities import lifecycle_tasks
        for family in ('callback_policy', 'session_state'):
            for profile in lifecycle_tasks.PROFILES:
                case = tasks.frozen(family + '_' + profile)
                directory = action_cache.ROOT / case['public_directory']
                self.assertEqual({path.name for path in directory.iterdir()}, {'target', 'manifest.json', 'prompt.txt', 'tools.json'})
                metadata = records.read(directory / 'manifest.json')
                lifecycle_tasks.validate_manifest(metadata)
                prompt = (directory / 'prompt.txt').read_text()
                self.assertEqual('Independent full-semantics generation condition' in prompt, profile.endswith('_codegen'))
                if profile.endswith('_recover'):
                    self.assertNotIn('checksum*33', prompt)
                    self.assertNotIn('checksum*multiplier', prompt)
                    self.assertNotIn('UINT64_MAX', prompt)
                for name in ('truth', 'oracle', 'vectors', 'gold', 'original_types'):
                    with self.subTest(field=name), self.assertRaises(ValueError):
                        lifecycle_tasks.validate_manifest({**metadata, name: {}})

    def test_41_malformed_lifecycle_oracles_fail_before_provider_or_inventory(self):
        case = tasks.frozen('callback_policy_c_lifecycle_recover')
        original = store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1})
        allowed = action_cache.ROOT / '.cache/benchmark-capabilities/grade-specs'
        with tempfile.TemporaryDirectory(prefix='invalid-lifecycle-', dir=allowed) as temporary:
            for defect in ('vectors', 'ids', 'memory', 'callback', 'contract', 'binary'):
                value = copy.deepcopy(original)
                if defect == 'vectors': value['oracle']['vectors_sha256'] = '0' * 64
                if defect == 'ids': value['oracle']['observations'][1]['id'] = value['oracle']['observations'][0]['id']
                if defect == 'memory': value['oracle']['observations'][0]['arena_hex'] = '00'
                if defect == 'callback':
                    next(row for row in value['oracle']['observations'] if row['callbacks'])['callbacks'][0]['arguments'].append(0)
                if defect == 'contract': value['contract_version'] = 'unknown'
                if defect == 'binary': value['oracle']['binary_sha256'] = '0' * 64
                raw = action_cache.canonical(value)
                path = Path(temporary) / (hashlib.sha256(raw).hexdigest() + '.json')
                path.write_bytes(raw)
                changed = {**case, 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                           'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
                entry = {'suite': 'capabilities-v1', 'case': case['id'], 'directory': '.cache/no-lifecycle-provider'}
                with self.subTest(defect=defect), patch.object(tasks, 'frozen', return_value=changed), \
                     patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider')), \
                     patch.object(coordinator.environment_profile, 'current', side_effect=AssertionError('Inventory')):
                    with self.assertRaises(ValueError):
                        coordinator.prepare(entry, self.model, materialize=False, offline_fixture=True)

    def test_42_lifecycle_private_regrade_reuses_inference_and_cached_machine_calls(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities import lifecycle
        from capabilities.cpp_lifecycle import runtime as cpp_compiler
        from grading.c_executor import runtime as executor
        for profile in ('c_lifecycle_recover', 'cpp_lifecycle_recover'):
            directory, entry, task, old = self.fixture('callback_policy_' + profile)
            value = copy.deepcopy(store.spec(task))
            value['scenarios'] = value['scenarios'][:1]
            oracle = lifecycle.reference(lifecycle.original('callback_policy'), value['scenarios'])
            value['oracle'] = {key: item for key, item in oracle.items() if key != 'cache_hit'}
            raw = action_cache.canonical(value)
            spec_id = hashlib.sha256(raw).hexdigest()
            path = action_cache.ROOT / '.cache/benchmark-capabilities/grade-specs' / (spec_id + '.json')
            if not path.exists(): path.write_bytes(raw)
            self.assertEqual(path.read_bytes(), raw)
            changed = {**entry['capability_case'], 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                       'grading_spec_sha256': spec_id}
            before = {name: records.sha(directory / name) for name in
                      ('task.json', 'result.json', 'answer.md', 'grade.json', 'completed.json')}
            with patch.object(tasks, 'frozen', return_value=changed), \
                 patch.object(run, 'launch_model', side_effect=AssertionError('Provider')), \
                 patch.object(executor, '_invoke', side_effect=AssertionError('Repeated C/VM worker')), \
                 patch.object(cpp_compiler, '_compile', side_effect=AssertionError('Repeated C++ compiler')):
                expected = coordinator.prepare({**entry, 'capability_case': changed}, self.model,
                                               materialize=False, offline_fixture=True)
                self.assertEqual(expected['fingerprint'], task['fingerprint'])
                newer = run.grade_saved(directory, expected)
            self.assertEqual(newer['passed'], newer['total'])
            self.assertEqual(newer['total'], 18)
            self.assertNotEqual(newer['grading_id'], old['grading_id'])
            self.assertEqual(before, {name: records.sha(directory / name) for name in before})

    def test_44_pointer_public_pairs_keep_nonmeasured_premises_and_hide_gold(self):
        from capabilities.pointers import tasks as pointer_tasks
        pairs = [tasks.frozen('variable_objects_'+profile) for profile in
                 ('pointer_recover', 'pointer_recover_counterfactual')]
        self.assertNotEqual(pairs[0]['binary_sha256'], pairs[1]['binary_sha256'])
        packages = [action_cache.ROOT/case['public_directory'] for case in pairs]
        for name in ('prompt.txt', 'tools.json'):
            self.assertEqual((packages[0]/name).read_bytes(), (packages[1]/name).read_bytes())
        a, b = [pointer_tasks.validate_manifest(records.read(path/'manifest.json')) for path in packages]
        for key in ('compiler', 'given_facts', 'known_abi', 'artifact_schema', 'condition'):
            self.assertEqual(a[key], b[key])
        for path, metadata in zip(packages, (a,b)):
            self.assertEqual({p.name for p in path.iterdir()}, {'target', 'prompt.txt', 'tools.json', 'manifest.json'})
            self.assertIsNone(metadata['output_language'])
            for key in ('gold', 'nonces', 'variant', 'original_sources', 'target_object'):
                with self.assertRaises(ValueError): pointer_tasks.validate_manifest({**metadata, key: {}})
            for name in ('prompt.txt', 'manifest.json', 'tools.json'):
                self.assertNotIn(b'counterfactual', (path/name).read_bytes())
                self.assertNotIn(b'reordered', (path/name).read_bytes())

    def test_45_pointer_artifacts_cache_through_both_harnesses_without_dispatch(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.pointers import runtime as pointer
        models = [next(m for m in records.models().values() if m['harness']==h)
                  for h in ('deepseek-harness', 'zcode')]
        for model in models:
            for name in ('variable_objects_pointer_recover', 'variable_objects_pointer_recover_counterfactual'):
                directory, entry, task, grade = self.fixture(name, model)
                self.assertTrue(grade['task_success'])
                self.assertIsNone(task['output_language'])
                self.assertEqual(grade['capability_ids'], ['T3'])
                self.assertEqual(grade['total'], 28)
                before = {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()}
                with patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch')), \
                     patch.object(pointer, '_run', side_effect=AssertionError('Compiler launch')), \
                     patch.object(pointer.executor, '_invoke', side_effect=AssertionError('VM launch')):
                    first, second = run.execute(entry, model), run.execute(entry, model)
                self.assertEqual(first['status'], 'cache_hit')
                self.assertEqual(second['provider_launches'], 0)
                self.assertEqual(before, {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()})

    def test_46_pointer_private_spec_rejected_before_provider_or_inventory(self):
        case = tasks.frozen('variable_objects_pointer_recover')
        original = store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1})
        allowed = action_cache.ROOT/'.cache/benchmark-capabilities/grade-specs'
        with tempfile.TemporaryDirectory(prefix='invalid-pointer-', dir=allowed) as temporary:
            for defect in ('vectors', 'contract', 'entry', 'object', 'rehashed_object', 'runtime'):
                value = copy.deepcopy(original)
                if defect == 'vectors': value['nonces'] = [True]
                elif defect == 'contract': value['condition'] = 'unknown'
                elif defect == 'entry': value['target']['entries']['original_callee'] += 1
                elif defect == 'runtime': value['target']['runtime']['policy_sha256'] = '0'*64
                else:
                    value['target']['original_obj_hex'] = '00'+value['target']['original_obj_hex'][2:]
                    if defect == 'rehashed_object':
                        value['target']['original_obj_sha256'] = hashlib.sha256(bytes.fromhex(value['target']['original_obj_hex'])).hexdigest()
                raw = action_cache.canonical(value)
                path = Path(temporary)/(hashlib.sha256(raw).hexdigest()+'.json'); path.write_bytes(raw)
                changed = {**case, 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                           'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
                entry = {'suite': 'capabilities-v1', 'case': case['id'], 'directory': '.cache/no-pointer-provider'}
                with patch.object(tasks, 'frozen', return_value=changed), \
                     patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider work')):
                    with self.assertRaises(ValueError): coordinator.prepare(entry, self.model, offline_fixture=True)

    def test_47_pointer_vector_revision_preserves_inference_and_budget_stop_is_ungraded(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.pointers import runtime as pointer
        directory, entry, task, old = self.fixture('variable_objects_pointer_recover')
        spec = store.spec(task); spec['nonces'] = [0, 1]
        allowed = action_cache.ROOT/'.cache/benchmark-capabilities/grade-specs'
        with tempfile.TemporaryDirectory(prefix='pointer-regrade-', dir=allowed) as temporary:
            raw = action_cache.canonical(spec); identity = hashlib.sha256(raw).hexdigest()
            path = Path(temporary)/(identity+'.json'); path.write_bytes(raw)
            changed = {**entry['capability_case'], 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                       'grading_spec_sha256': identity}
            with patch.object(tasks, 'frozen', return_value=changed), \
                 patch.object(pointer, '_run', side_effect=AssertionError('Compiler')), \
                 patch.object(pointer.executor, '_invoke', side_effect=AssertionError('VM')):
                expected = coordinator.prepare({**entry, 'capability_case': changed}, self.model,
                                               materialize=False, offline_fixture=True)
                self.assertEqual(expected['fingerprint'], task['fingerprint'])
                newer = run.grade_saved(directory, expected)
            self.assertEqual(newer['total'], 4)
            self.assertTrue(newer['task_success'])
            self.assertNotEqual(old['grading_id'], newer['grading_id'])
            (directory/'budget-exhausted.json').write_text('{}')
            with self.assertRaises(RuntimeError): store.prepare(directory, expected)

    def test_48_variant_public_pairs_withhold_layout_and_discriminant_answers(self):
        from capabilities.variant_objects import tasks as variant_tasks
        cases = [tasks.frozen('variable_objects_' + p) for p in
                 ('variant_recover', 'variant_recover_counterfactual')]
        self.assertNotEqual(cases[0]['binary_sha256'], cases[1]['binary_sha256'])
        paths = [action_cache.ROOT / c['public_directory'] for c in cases]
        for name in ('prompt.txt', 'tools.json'):
            self.assertEqual((paths[0] / name).read_bytes(), (paths[1] / name).read_bytes())
        a, b = [variant_tasks.validate_manifest(records.read(p / 'manifest.json')) for p in paths]
        for key in ('compiler', 'given_facts', 'known_abi', 'artifact_schema', 'condition'):
            self.assertEqual(a[key], b[key])
        for path, manifest in zip(paths, (a, b)):
            self.assertEqual({p.name for p in path.iterdir()}, {'target', 'prompt.txt', 'manifest.json', 'tools.json'})
            for key in ('variant', 'gold', 'nonces', 'original_objects', 'source'):
                with self.assertRaises(ValueError): variant_tasks.validate_manifest({**manifest, key: {}})
            for name in ('prompt.txt', 'manifest.json', 'tools.json'):
                for word in (b'baseline', b'reordered', b'counterfactual', b'grading_spec'):
                    self.assertNotIn(word, (path / name).read_bytes())

    def test_49_variant_cache_reuse_through_both_harnesses(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.variant_objects import runtime as variants
        models = [next(m for m in records.models().values() if m['harness'] == h)
                  for h in ('deepseek-harness', 'zcode')]
        for model in models:
            for name in ('variable_objects_variant_recover', 'variable_objects_variant_recover_counterfactual'):
                directory, entry, task, grade = self.fixture(name, model)
                self.assertTrue(grade['task_success'])
                self.assertEqual(grade['capability_ids'], ['T4'])
                self.assertEqual(grade['total'], 32)
                self.assertIsNone(task['output_language'])
                before = {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()}
                with patch.object(run, 'launch_model', side_effect=AssertionError('Provider')), \
                     patch.object(variants.engine, '_run', side_effect=AssertionError('Compiler')), \
                     patch.object(variants.executor, '_invoke', side_effect=AssertionError('Guest worker')):
                    first, second = run.execute(entry, model), run.execute(entry, model)
                self.assertEqual((first['status'], second['status']), ('cache_hit', 'cache_hit'))
                self.assertEqual((first['provider_launches'], second['provider_launches']), (0, 0))
                self.assertEqual(before, {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()})

    def test_50_variant_private_spec_tampering_stops_before_provider_or_inventory(self):
        case = tasks.frozen('variable_objects_variant_recover')
        original = store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1})
        allowed = action_cache.ROOT / '.cache/benchmark-capabilities/grade-specs'
        with tempfile.TemporaryDirectory(prefix='invalid-variant-', dir=allowed) as temporary:
            for defect in ('vectors', 'condition', 'entry', 'rehashed-object', 'family'):
                value = copy.deepcopy(original)
                if defect == 'vectors': value['nonces'] = [True]
                elif defect == 'condition': value['condition'] = 'unknown'
                elif defect == 'entry': value['target']['entries']['original_callee'] += 1
                elif defect == 'family': value['target']['family'] = 'other'
                else:
                    value['target']['original_obj_hex'] = '00' + value['target']['original_obj_hex'][2:]
                    value['target']['original_obj_sha256'] = hashlib.sha256(bytes.fromhex(value['target']['original_obj_hex'])).hexdigest()
                raw = action_cache.canonical(value)
                path = Path(temporary) / (hashlib.sha256(raw).hexdigest() + '.json'); path.write_bytes(raw)
                changed = {**case, 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                           'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
                entry = {'suite': 'capabilities-v1', 'case': case['id'], 'directory': '.cache/no-variant-provider'}
                with self.subTest(defect=defect), patch.object(tasks, 'frozen', return_value=changed), \
                     patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider')), \
                     patch.object(coordinator.environment_profile, 'current', side_effect=AssertionError('Inventory')):
                    with self.assertRaises(ValueError): coordinator.prepare(entry, self.model, offline_fixture=True)

    def test_51_variant_private_vector_revision_preserves_inference_and_execution(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.variant_objects import runtime as variants
        directory, entry, task, old = self.fixture('variable_objects_variant_recover')
        spec = store.spec(task); spec['nonces'] = [0, 1]
        raw = action_cache.canonical(spec)
        path = action_cache.ROOT / '.cache/benchmark-capabilities/grade-specs' / (hashlib.sha256(raw).hexdigest() + '.json')
        if not path.exists(): path.write_bytes(raw)
        self.assertEqual(path.read_bytes(), raw)
        changed = {**entry['capability_case'], 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                   'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
        protected = ('task.json', 'result.json', 'answer.md', 'grade.json', 'completed.json')
        before = {name: records.sha(directory / name) for name in protected}
        with patch.object(tasks, 'frozen', return_value=changed), \
             patch.object(variants.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(variants.executor, '_invoke', side_effect=AssertionError('Guest worker')):
            expected = coordinator.prepare({**entry, 'capability_case': changed}, self.model, materialize=False, offline_fixture=True)
            self.assertEqual(expected['fingerprint'], task['fingerprint'])
            newer = run.grade_saved(directory, expected)
        self.assertTrue(newer['task_success'])
        self.assertEqual(newer['total'], 4)
        self.assertNotEqual(newer['grading_id'], old['grading_id'])
        self.assertEqual(before, {name: records.sha(directory / name) for name in protected})

    def test_52_indirect_public_pairs_supply_only_shared_layout_and_call_premises(self):
        from capabilities.indirect_calls import tasks as indirect_tasks
        cases = [tasks.frozen('callback_policy_' + p) for p in
                 ('indirect_recover', 'indirect_recover_counterfactual')]
        self.assertNotEqual(cases[0]['binary_sha256'], cases[1]['binary_sha256'])
        paths = [action_cache.ROOT / c['public_directory'] for c in cases]
        for name in ('prompt.txt', 'tools.json'):
            self.assertEqual((paths[0] / name).read_bytes(), (paths[1] / name).read_bytes())
        manifests = [indirect_tasks.validate_manifest(records.read(p / 'manifest.json')) for p in paths]
        for name in ('compiler', 'given_facts', 'known_abi', 'artifact_schema', 'condition'):
            self.assertEqual(manifests[0][name], manifests[1][name])
        for path, manifest in zip(paths, manifests):
            self.assertEqual({p.name for p in path.iterdir()}, {'target', 'prompt.txt', 'manifest.json', 'tools.json'})
            self.assertEqual(set(manifest['entry_points']),
                             {'original_callee', 'original_caller', 'original_leaf_0', 'original_leaf_1'})
            for key in ('variant', 'gold', 'nonces', 'source', 'operations', 'this_adjustment', 'virtual_slot'):
                with self.assertRaises(ValueError):
                    indirect_tasks.validate_manifest({**manifest, key: 'private'})
            for name in ('prompt.txt', 'manifest.json', 'tools.json'):
                for word in (b'baseline', b'reordered', b'counterfactual', b'grading_spec'):
                    self.assertNotIn(word, (path / name).read_bytes())
            changed = copy.deepcopy(manifest)
            changed['given_facts']['vptr_offset'] = False
            with self.assertRaises(ValueError):
                indirect_tasks.validate_manifest(changed)
        # Even a self-consistent rehashed package cannot substitute a false
        # supplied implementation address for the same frozen original build.
        case, original_path = cases[0], paths[0]
        declaration = next(row for row in tasks.declared() if row['task_id'] == case['id'])
        settings = config.load()[0]
        with tempfile.TemporaryDirectory(prefix='indirect-public-binding-',
                                         dir=action_cache.ROOT / '.cache/benchmark-runtime') as temporary:
            cache = Path(temporary)
            original_spec = action_cache.ROOT / case['grading_spec']
            copied_spec = cache / 'grade-specs' / original_spec.name
            copied_spec.parent.mkdir()
            copied_spec.write_bytes(original_spec.read_bytes())
            files = {name: (original_path / name).read_bytes() for name in indirect_tasks.FILES}
            forged = records.read(original_path / 'manifest.json')
            forged['entry_points']['original_leaf_0'] += 1
            files['manifest.json'] = action_cache.canonical(forged)
            hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}
            package = hashlib.sha256(action_cache.canonical(hashes)).hexdigest()
            directory = cache / 'task-freezes' / package
            directory.mkdir(parents=True)
            for name, raw in files.items():
                (directory / name).write_bytes(raw)
            altered = {**case, 'public_directory': str(directory.relative_to(action_cache.ROOT)),
                       'public_package_sha256': package, 'public_files_sha256': hashes,
                       'grading_spec': str(copied_spec.relative_to(action_cache.ROOT))}
            with patch.object(indirect_tasks, 'CACHE', cache):
                with self.assertRaisesRegex(ValueError, 'premises/endpoints differ'):
                    indirect_tasks.validate_frozen(altered, declaration, settings)

    def test_53_indirect_calls_reuse_both_harness_caches_without_workers(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.indirect_calls import runtime as indirect
        models = [next(m for m in records.models().values() if m['harness'] == h)
                  for h in ('deepseek-harness', 'zcode')]
        for model in models:
            for name in ('callback_policy_indirect_recover', 'callback_policy_indirect_recover_counterfactual'):
                directory, entry, task, grade = self.fixture(name, model)
                self.assertTrue(grade['task_success'])
                self.assertEqual(grade['capability_ids'], ['T6'])
                self.assertEqual(grade['total'], 56)
                self.assertIsNone(task['output_language'])
                before = {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()}
                with patch.object(run, 'launch_model', side_effect=AssertionError('Provider')), \
                     patch.object(indirect.engine, '_run', side_effect=AssertionError('Compiler')), \
                     patch.object(indirect.executor, '_invoke', side_effect=AssertionError('Guest worker')):
                    first, second = run.execute(entry, model), run.execute(entry, model)
                self.assertEqual((first['status'], second['status']), ('cache_hit', 'cache_hit'))
                self.assertEqual((first['provider_launches'], second['provider_launches']), (0, 0))
                self.assertEqual(before, {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()})

    def test_54_indirect_private_spec_tampering_stops_before_provider_or_inventory(self):
        case = tasks.frozen('callback_policy_indirect_recover')
        original = store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1})
        allowed = action_cache.ROOT / '.cache/benchmark-capabilities/grade-specs'
        with tempfile.TemporaryDirectory(prefix='invalid-indirect-', dir=allowed) as temporary:
            for defect in ('vectors', 'condition', 'entry', 'rehashed-object', 'family'):
                value = copy.deepcopy(original)
                if defect == 'vectors': value['nonces'] = [True]
                elif defect == 'condition': value['condition'] = 'unknown'
                elif defect == 'entry': value['target']['entries']['original_callee'] += 1
                elif defect == 'family': value['target']['family'] = 'other'
                else:
                    value['target']['original_obj_hex'] = '00' + value['target']['original_obj_hex'][2:]
                    value['target']['original_obj_sha256'] = hashlib.sha256(bytes.fromhex(value['target']['original_obj_hex'])).hexdigest()
                raw = action_cache.canonical(value)
                path = Path(temporary) / (hashlib.sha256(raw).hexdigest() + '.json')
                path.write_bytes(raw)
                changed = {**case, 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                           'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
                entry = {'suite': 'capabilities-v1', 'case': case['id'], 'directory': '.cache/no-indirect-provider'}
                with self.subTest(defect=defect), patch.object(tasks, 'frozen', return_value=changed), \
                     patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider')), \
                     patch.object(coordinator.environment_profile, 'current', side_effect=AssertionError('Inventory')):
                    with self.assertRaises(ValueError):
                        coordinator.prepare(entry, self.model, offline_fixture=True)

    def test_55_indirect_vector_revision_reuses_inference_and_machine_calls(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.indirect_calls import runtime as indirect
        directory, entry, task, old = self.fixture('callback_policy_indirect_recover')
        spec = store.spec(task)
        spec['nonces'] = [0, 1, 2, 3]
        raw = action_cache.canonical(spec)
        path = action_cache.ROOT / '.cache/benchmark-capabilities/grade-specs' / (hashlib.sha256(raw).hexdigest() + '.json')
        if not path.exists():
            path.write_bytes(raw)
        self.assertEqual(path.read_bytes(), raw)
        changed = {**entry['capability_case'], 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                   'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
        protected = ('task.json', 'result.json', 'answer.md', 'grade.json', 'completed.json')
        before = {name: records.sha(directory / name) for name in protected}
        with patch.object(tasks, 'frozen', return_value=changed), \
             patch.object(indirect.engine, '_run', side_effect=AssertionError('Compiler')), \
             patch.object(indirect.executor, '_invoke', side_effect=AssertionError('Guest worker')):
            expected = coordinator.prepare({**entry, 'capability_case': changed}, self.model,
                                           materialize=False, offline_fixture=True)
            self.assertEqual(expected['fingerprint'], task['fingerprint'])
            newer = run.grade_saved(directory, expected)
        self.assertTrue(newer['task_success'])
        self.assertEqual(newer['total'], 8)
        self.assertNotEqual(newer['grading_id'], old['grading_id'])
        self.assertEqual(before, {name: records.sha(directory / name) for name in protected})

    def test_56_advanced_conditions_use_the_registered_frozen_boundaries(self):
        expected = {'dispatch_registry_optimized_recover': ('L6', 'cpp', False),
                    'dispatch_registry_lto_recover': ('L6', 'cpp', False),
                    'variable_objects_rust_native_recover': ('T7', 'rust', True),
                    'variable_objects_go_internal_recover': ('T7', 'go', True)}
        names = {row['task_id'] for row in tasks.declared()}
        self.assertTrue(set(expected) <= names)
        for name, (capability, language, entries) in expected.items():
            case = tasks.frozen(name)
            spec = store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1})
            metadata = json.loads((action_cache.ROOT / case['public_directory'] / 'manifest.json').read_bytes())
            self.assertEqual(case['capability_ids'], [capability])
            self.assertEqual(case['source_language'], language)
            self.assertIsNone(case['output_language'])
            self.assertEqual('entry_points' in metadata, entries)
            self.assertEqual(spec['binary_sha256'], case['binary_sha256'])
            declaration = next(row for row in tasks.declared() if row['task_id'] == name)
            for state in ({'status': 'pending'}, {'status': 'ready', 'case': case}):
                planned = coordinator._entry(self.model, declaration, config.load()[0], state)
                self.assertEqual(planned['language'], language)

    def test_57_optimized_conditions_reuse_both_harness_caches(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.optimized_locations import runtime
        models = [next(m for m in records.models().values() if m['harness'] == h)
                  for h in ('deepseek-harness', 'zcode')]
        for model in models:
            for name in ('dispatch_registry_optimized_recover', 'dispatch_registry_lto_recover'):
                directory, entry, task, grade = self.fixture(name, model)
                self.assertTrue(grade['task_success'])
                self.assertEqual((grade['passed'], grade['total']), (6, 6))
                self.assertEqual(set(grade['capabilities']), {'L6'})
                before = {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()}
                with patch.object(run, 'launch_model', side_effect=AssertionError('Provider')), \
                     patch.object(runtime, '_invoke', side_effect=AssertionError('Closed grader')):
                    first, second = run.execute(entry, model), run.execute(entry, model)
                self.assertEqual((first['status'], second['status']), ('cache_hit', 'cache_hit'))
                self.assertEqual((first['provider_launches'], second['provider_launches']), (0, 0))
                self.assertEqual(before, {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()})

    def test_58_native_conditions_reuse_both_harness_caches(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.native_abi import probe, execute
        models = [next(m for m in records.models().values() if m['harness'] == h)
                  for h in ('deepseek-harness', 'zcode')]
        for model in models:
            for name in ('variable_objects_rust_native_recover', 'variable_objects_go_internal_recover'):
                directory, entry, task, grade = self.fixture(name, model)
                self.assertTrue(grade['task_success'])
                self.assertEqual((grade['passed'], grade['total']), (24, 24))
                self.assertEqual(grade['return_provenance'], 'host_native_abi_return_v1')
                self.assertIsNone(task['output_language'])
                self.assertIn(task['source_language'], ('rust', 'go'))
                before = {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()}
                with patch.object(run, 'launch_model', side_effect=AssertionError('Provider')), \
                     patch.object(probe.process, 'run', side_effect=AssertionError('Compiler or observer')):
                    first, second = run.execute(entry, model), run.execute(entry, model)
                self.assertEqual((first['status'], second['status']), ('cache_hit', 'cache_hit'))
                self.assertEqual(before, {p.name: records.sha(p) for p in directory.iterdir() if p.is_file()})

    def test_59_advanced_invalid_artifacts_and_partial_results_do_not_pass(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, old = self.fixture('dispatch_registry_optimized_recover')
        spec = store.spec(task)
        from capabilities.optimized_locations import scoring
        value = scoring.answer(spec['truth'])
        value['instances'].pop()
        (directory / 'answer.md').write_bytes(b'```instances.json\n' + action_cache.canonical(value) + b'\n```\n')
        grade = run.grade_saved(directory, task)
        self.assertEqual((grade['passed'], grade['total']), (5, 6))
        self.assertFalse(grade['task_success'])
        from capabilities.native_abi import contracts
        directory, entry, task, old = self.fixture('variable_objects_rust_native_recover')
        value = contracts.gold('rust', task['binary_sha256'])
        value['nonce']['register'] = 'rcx'
        (directory / 'answer.md').write_bytes(b'```native-abi.json\n' + action_cache.canonical(value) + b'\n```\n')
        grade = run.grade_saved(directory, task)
        self.assertGreater(grade['passed'], 0)
        self.assertLess(grade['passed'], grade['total'])
        self.assertFalse(grade['task_success'])
        for raw in (b'```abi.json\n{}\n```\n', b'```native-abi.json\n{}\n```\n'):
            (directory / 'answer.md').write_bytes(raw)
            rejected = run.grade_saved(directory, task)
            self.assertFalse(rejected['task_success'])
            self.assertEqual((rejected['passed'], rejected['total']), (0, 24))
            self.assertEqual(rejected.get('return_provenance', 'not_executed'), 'not_executed')
        for case_id, filename, total in (
                ('dispatch_registry_optimized_recover', 'instances.json', 6),
                ('variable_objects_rust_native_recover', 'native-abi.json', 24)):
            directory, entry, task, old = self.fixture(case_id)
            (directory / 'answer.md').write_bytes(b'```' + filename.encode() + b'\n' + b' ' * 262145 + b'\n```\n')
            from capabilities.optimized_locations import runtime as optimized_runtime
            from capabilities.native_abi import runtime as native_runtime
            with patch.object(optimized_runtime, 'grade', side_effect=AssertionError('Closed grader')), \
                 patch.object(native_runtime, 'grade', side_effect=AssertionError('Native probes')):
                rejected = run.grade_saved(directory, task)
            self.assertEqual((rejected['passed'], rejected['total']), (0, total))
            self.assertEqual(rejected['return_provenance'], 'not_executed')
            self.assertFalse(rejected['artifact_valid'])

    def test_60_native_private_vectors_reuse_inference_compile_and_observations(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.native_abi import probe
        directory, entry, task, old = self.fixture('variable_objects_rust_native_recover')
        spec = store.spec(task)
        spec['nonces'] = [0, 1, 2, 3]
        raw = action_cache.canonical(spec)
        path = action_cache.ROOT / '.cache/benchmark-capabilities/grade-specs' / (hashlib.sha256(raw).hexdigest() + '.json')
        if not path.exists(): path.write_bytes(raw)
        changed = {**entry['capability_case'], 'grading_spec': str(path.relative_to(action_cache.ROOT)),
                   'grading_spec_sha256': hashlib.sha256(raw).hexdigest()}
        before = {name: records.sha(directory / name) for name in ('task.json', 'result.json', 'answer.md', 'grade.json', 'completed.json')}
        with patch.object(tasks, 'frozen', return_value=changed), \
             patch.object(probe.process, 'run', side_effect=AssertionError('Compiler or observer')):
            expected = coordinator.prepare({**entry, 'capability_case': changed}, self.model,
                                           materialize=False, offline_fixture=True)
            self.assertEqual(expected['fingerprint'], task['fingerprint'])
            newer = run.grade_saved(directory, expected)
        self.assertTrue(newer['task_success'])
        self.assertEqual(newer['total'], 8)
        self.assertNotEqual(newer['grading_id'], old['grading_id'])
        self.assertEqual(before, {name: records.sha(directory / name) for name in before})

    def test_61_advanced_acceptance_failure_precedes_provider_resolution(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.native_abi import evidence as native_gate
        from capabilities.optimized_locations import evidence as optimized_gate
        settings, families = config.load()
        for name, gate in (('dispatch_registry_optimized_recover', optimized_gate),
                           ('variable_objects_rust_native_recover', native_gate)):
            directory, entry, task, old = self.fixture(name)
            with patch.object(config, 'load', return_value=({**settings, 'inference_enabled': True}, families)), \
                 patch.object(preparation, 'inspect', return_value={'status': 'ready'}), \
                 patch.object(gate, 'require_validation', side_effect=RuntimeError('Advanced controls stale')), \
                 patch.object(coordinator.runtime_attestation, 'prepare', side_effect=AssertionError('Provider')):
                with self.assertRaisesRegex(RuntimeError, 'controls stale'):
                    coordinator.prepare(entry, self.model, materialize=True)

    def test_43_lifecycle_compile_errors_do_not_claim_executed_return_provenance(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        for profile in ('c_lifecycle_recover', 'cpp_lifecycle_recover'):
            directory, entry, task, old = self.fixture('callback_policy_' + profile)
            cpp = profile.startswith('cpp_')
            name = 'solution.cpp' if cpp else 'solution.c'
            source = 'extern "C" std::uint64_t reconstructed() { return 0; }' if cpp else 'int32_t reconstructed(void) { return 0; }'
            (directory / 'answer.md').write_text('```' + name + '\n' + source + '\n```\n')
            failed = run.grade_saved(directory, task)
            self.assertEqual(failed['status'], 'compile_error')
            self.assertFalse(failed['compiled'])
            self.assertTrue(failed['artifact_valid'])
            self.assertFalse(failed['task_success'])
            self.assertEqual(failed['return_provenance'], 'not_executed')


    def test_69_two_level_pointer_conditions_use_both_harnesses_and_warm_cache(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities.pointers import runtime as pointer_runtime
        models = [next(model for model in records.models().values() if model['harness'] == harness)
                  for harness in ('deepseek-harness', 'zcode')]
        for model in models:
            for profile in ('pointer_topology_recover', 'pointer_topology_recover_counterfactual'):
                directory, entry, task, grade = self.fixture('variable_objects_' + profile, model)
                self.assertEqual(task['artifact_schema'], {'types.json': 1})
                self.assertEqual(grade['capability_ids'], ['T3'])
                self.assertTrue(grade['task_success'])
                self.assertEqual((grade['passed'], grade['total']), (28, 28))
                self.assertEqual(grade['return_provenance'], 'host_emulator_return_v1')
                original = {name: records.sha(directory / name) for name in
                            ('answer.md', 'task.json', 'result.json', 'completed.json', 'grade.json')}
                with patch.object(run, 'launch_model', side_effect=AssertionError('Provider')), \
                     patch.object(socket.socket, 'connect', side_effect=AssertionError('Network')), \
                     patch.object(pointer_runtime, '_run', side_effect=AssertionError('Compiler/native worker')), \
                     patch.object(pointer_runtime.executor, '_invoke', side_effect=AssertionError('Guest worker')):
                    self.assertEqual(run.execute(entry, model)['status'], 'cache_hit')
                    self.assertEqual(run.execute(entry, model)['status'], 'cache_hit')
                self.assertEqual(original, {name: records.sha(directory / name) for name in original})

    def test_70_pointer_topology_conditions_keep_nonmeasured_public_premises_identical(self):
        profiles = ('pointer_recover', 'pointer_recover_counterfactual',
                    'pointer_topology_recover', 'pointer_topology_recover_counterfactual')
        values = [tasks.frozen('variable_objects_' + profile) for profile in profiles]
        packages = [action_cache.ROOT / value['public_directory'] for value in values]
        manifests = [json.loads((directory / 'manifest.json').read_bytes()) for directory in packages]
        self.assertEqual(len({value['binary_sha256'] for value in values}), 4)
        self.assertEqual(len({manifest['task_id'] for manifest in manifests}), 4)
        for directory, manifest in zip(packages, manifests):
            self.assertEqual((directory / 'prompt.txt').read_bytes(), (packages[0] / 'prompt.txt').read_bytes())
            self.assertEqual((directory / 'tools.json').read_bytes(), (packages[0] / 'tools.json').read_bytes())
            for name in ('given_facts', 'compiler', 'artifact_schema', 'condition', 'hint_profile'):
                self.assertEqual(manifest[name], manifests[0][name])
            raw = (directory / 'manifest.json').read_bytes() + (directory / 'prompt.txt').read_bytes()
            self.assertNotIn(b'"variant"', raw)
            self.assertNotIn(b'indirect_reordered', raw)
            self.assertNotIn(b'pointer_topology_recover', raw)
        self.assertEqual({value['lineage_id'] for value in values}, {'pointer_object_interfaces_v1'})


    def test_71_both_harnesses_receive_the_public_manifest_as_prompt_input(self):
        models = [next(model for model in records.models().values() if model['harness'] == harness)
                  for harness in ('deepseek-harness', 'zcode')]
        case_ids = ('record_codec_sysv_amd64_scalar_recover',
                    'record_codec_sysv_amd64_scalar_recover_counterfactual',
                    'record_codec_ms_x64_layout_recover',
                    'record_codec_localize', 'record_codec_c_recover',
                    'variable_objects_pointer_topology_recover',
                    'variable_objects_rust_native_recover')
        with patch.object(run, 'launch_model', side_effect=AssertionError('Provider')):
            for case_id in case_ids:
                case = tasks.frozen(case_id)
                package = action_cache.ROOT / case['public_directory']
                manifest = (package / 'manifest.json').read_bytes()
                metadata = json.loads(manifest)
                original = (package / 'prompt.txt').read_bytes()
                expected_hashes = dict(case['public_files_sha256'])
                prepared = []
                for model in models:
                    with self.subTest(case=case_id, harness=model['harness']):
                        entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1',
                                 'case': case_id, 'directory': '.cache/public-premise-control'}
                        task = coordinator.prepare(entry, model, materialize=True, offline_fixture=True)
                        delivered = (Path(task['input_directory']) / 'prompt.txt').read_bytes()
                        self.assertTrue(delivered.startswith(original))
                        # Public context/format notices legitimately follow the
                        # fenced manifest. Parse its boundary, not the prompt tail.
                        block = delivered.rsplit(b'```json\n', 1)[1].split(b'\n```', 1)[0]
                        self.assertEqual(json.loads(block), metadata)
                        self.assertIn(metadata['binary_sha256'].encode(), delivered)
                        self.assertEqual(hashlib.sha256(delivered).hexdigest(), task['prompt_sha256'])
                        self.assertEqual(task['inference_identity']['prompt'], task['prompt_sha256'])
                        if 'given_facts' in metadata:
                            self.assertEqual(json.loads(block)['given_facts'], metadata['given_facts'])
                        prepared.append(delivered)
                self.assertEqual(prepared[0], prepared[1])
                self.assertEqual({name: records.sha(package / name) for name in expected_hashes}, expected_hashes)


    def test_72_premises_only_withholds_bytes_and_all_analysis_tools_in_both_harnesses(self):
        models = [next(value for value in records.models().values() if value['harness'] == harness)
                  for harness in ('deepseek-harness', 'zcode')]
        names = ('record_codec_sysv_amd64_scalar_recover', 'record_codec_ms_x64_layout_recover',
                 'variable_objects_pointer_recover', 'variable_objects_variant_recover',
                 'callback_policy_indirect_recover', 'variable_objects_rust_native_recover')
        for model in models:
            for name in names:
                with self.subTest(harness=model['harness'], task=name):
                    case = tasks.frozen(name)
                    original = action_cache.ROOT / case['public_directory']
                    entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': name,
                             'directory': '.cache/premises-control/' + model['id'] + '/' + name,
                             'evidence_access': 'premises_only'}
                    task = coordinator.prepare(entry, model, materialize=True, offline_fixture=True)
                    runtime = Path(task['input_directory'])
                    self.assertEqual((runtime / 'target').read_bytes(), b'')
                    self.assertEqual(json.loads((runtime / 'tools.json').read_bytes())['tools'], [])
                    self.assertEqual(json.loads((runtime / 'harness-config.json').read_bytes())['allowed_tool_names'], [])
                    self.assertEqual((runtime / 'manifest.json').read_bytes(), (original / 'manifest.json').read_bytes())
                    self.assertIn((original / 'manifest.json').read_bytes(), (runtime / 'prompt.txt').read_bytes())
                    self.assertEqual(task['binary_sha256'], case['binary_sha256'])
                    self.assertEqual(task['inference_identity']['binary'], hashlib.sha256(b'').hexdigest())
                    self.assertEqual(task['tool_count'], 0)
                    self.assertEqual(task['evidence_access'], 'premises_only')

    def test_73_access_conditions_cannot_alias_or_reclassify_a_committed_cache(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        name = 'record_codec_sysv_amd64_scalar_recover'
        base = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': name,
                'directory': '.cache/evidence-invariance'}
        ordinary = coordinator.prepare(base, self.model, materialize=False, offline_fixture=True)
        explicit = coordinator.prepare({**base, 'evidence_access': 'binary'}, self.model,
                                       materialize=False, offline_fixture=True)
        withheld = coordinator.prepare({**base, 'evidence_access': 'premises_only'}, self.model,
                                       materialize=False, offline_fixture=True)
        self.assertEqual(ordinary['fingerprint'], explicit['fingerprint'])
        self.assertEqual(ordinary['inference_identity'], explicit['inference_identity'])
        self.assertNotEqual(ordinary['fingerprint'], withheld['fingerprint'])
        settings, _ = config.load()
        self.assertEqual(coordinator._namespace(settings, self.model), coordinator._namespace(settings, self.model, 'binary'))
        self.assertNotEqual(coordinator._namespace(settings, self.model), coordinator._namespace(settings, self.model, 'premises_only'))
        directory, entry, task, grade = self.fixture(name, access='premises_only')
        self.assertTrue(grade['task_success'])
        self.assertEqual(run.execute(entry, self.model)['status'], 'cache_hit')
        altered = {**task, 'evidence_access': 'binary'}
        run.save(directory / 'task.json', altered)
        completed = records.read(directory / 'completed.json')
        completed['files_sha256']['task.json'] = records.sha(directory / 'task.json')
        run.save(directory / 'completed.json', completed)
        with self.assertRaisesRegex(ValueError, 'evidence access'):
            coordinator.validate_cache(entry, self.model, task)
        with self.assertRaisesRegex(ValueError, 'type/ABI'):
            coordinator.prepare({**base, 'case': 'record_codec_c_codegen', 'evidence_access': 'premises_only'},
                                self.model, materialize=False, offline_fixture=True)

    def test_74_premises_only_configuration_and_startup_flag_require_explicit_type_tasks(self):
        from capabilities import evidence_access
        selection = benchmark_config.load()
        selection.update(models=[self.model['id']], suites=['capabilities-v1'], experiments=['capabilities-v1'],
                         cases=['record_codec_sysv_amd64_scalar_recover'])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'selection.json'
            path.write_text(json.dumps(selection))
            output = io.StringIO()
            with patch.object(sys, 'argv', ['benchmark', 'plan', '--config', str(path), '--evidence-access', 'premises_only']), \
                 patch.object(run, 'selected_entries', return_value=[]), patch.object(tasks, 'preview', return_value=[]), \
                 patch.object(sys, 'stdout', output):
                run.main()
            self.assertEqual(json.loads(output.getvalue())['selection']['evidence_access'], 'premises_only')
            for change in ({'cases': 'all'}, {'cases': ['record_codec_c_codegen']},
                           {'suites': 'all'}, {'evidence_access': False}):
                path.write_text(json.dumps({**selection, 'evidence_access': 'premises_only', **change}))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    benchmark_config.load(path)



    def test_75_public_entry_seeds_are_real_merkle_inputs_in_both_harnesses(self):
        from capabilities import analysis_context
        settings, families = config.load()
        settings = copy.deepcopy(settings)
        settings['runtime']['known_entry_seeds'] = True
        name = 'record_codec_sysv_amd64_scalar_recover'
        before = tasks.frozen(name)
        for model in (self.model, records.models()['glm-5.3']):
            entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1', 'case': name,
                     'directory': '.cache/context-controls/' + model['id'], 'condition': settings['condition']}
            with patch.object(config, 'load', return_value=(settings, families)):
                task = coordinator.prepare(entry, model, offline_fixture=True)
            directory = Path(task['input_directory'])
            raw = (directory / analysis_context.FILENAME).read_bytes()
            gateway = records.read(directory / 'gateway.json')
            self.assertEqual(task['analysis_context_policy'], analysis_context.POLICY)
            self.assertEqual(gateway['runtime_environment']['REA_GHIDRA_SEED_FILE'], str(directory / analysis_context.FILENAME))
            self.assertIn(b'0x10001b0', (directory / 'prompt.txt').read_bytes())
            cache_store = action_cache.Cache()
            node = json.loads(cache_store.get(task['action_descriptor']['input_root_digest']))
            node = json.loads(cache_store.get(next(row['digest'] for row in node['directories'] if row['name'] == 'task')))
            seed = next(row for row in node['files'] if row['name'] == analysis_context.FILENAME)
            self.assertEqual(cache_store.get(seed['digest']), raw)
            self.assertEqual(task['public_package_sha256'], before['public_package_sha256'])
            self.assertEqual(task['binary_sha256'], before['binary_sha256'])

    def test_76_seeds_never_reach_localization_or_premises_only(self):
        from capabilities import analysis_context, evidence_access
        settings, families = config.load()
        settings = copy.deepcopy(settings)
        settings['runtime']['known_entry_seeds'] = True
        base = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1',
                'case': 'record_codec_sysv_amd64_scalar_recover',
                'directory': '.cache/context-controls/binary', 'condition': settings['condition']}
        with patch.object(config, 'load', return_value=(settings, families)):
            binary = coordinator.prepare(base, self.model, offline_fixture=True)
            control = coordinator.prepare({**base, 'directory': '.cache/context-controls/control',
                'evidence_access': 'premises_only'}, self.model, offline_fixture=True)
            local = coordinator.prepare({**base, 'case': 'record_codec_localize',
                'directory': '.cache/context-controls/localization'}, self.model, offline_fixture=True)
        root = Path(control['input_directory'])
        self.assertEqual((root / 'target').read_bytes(), b'')
        self.assertFalse((root / analysis_context.FILENAME).exists())
        self.assertNotIn('analysis_context', records.read(root / 'gateway.json'))
        self.assertEqual((root / 'prompt.txt').read_bytes().removesuffix(evidence_access.NOTICE),
                         (Path(binary['input_directory']) / 'prompt.txt').read_bytes())
        self.assertNotIn('analysis_context_policy', local)
        self.assertFalse((Path(local['input_directory']) / analysis_context.FILENAME).exists())

    def test_77_seed_drift_is_refused_before_runtime_inventory_or_provider(self):
        import runtime_attestation
        from capabilities import analysis_context
        settings, families = config.load()
        settings = copy.deepcopy(settings); settings['runtime']['known_entry_seeds'] = True
        entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1',
                 'case': 'record_codec_sysv_amd64_scalar_recover',
                 'directory': '.cache/context-controls/drift', 'condition': settings['condition']}
        with patch.object(config, 'load', return_value=(settings, families)):
            task = coordinator.prepare(entry, self.model, offline_fixture=True)
        root = Path(task['input_directory'])
        gateway, harness = records.read(root / 'gateway.json'), records.read(root / 'harness-config.json')
        gateway.pop('runtime_environment'); gateway['analysis_tools_file'] = '@task/tools.json'
        harness['analysis_tools_file'] = '@task/tools.json'
        manifest = {'environment': {}}
        proof = {'schema_version': 1, 'runtime': {'manifest': manifest,
                 'identity': hashlib.sha256(action_cache.canonical(manifest)).hexdigest()},
                 'provider': {}, 'configuration': {'gateway': gateway, 'harness': harness}}
        proof['identity'] = hashlib.sha256(action_cache.canonical(proof)).hexdigest()
        task['runtime_attestation'] = proof; task['inference_identity']['attestation'] = proof
        runtime_attestation.verify_configuration(task)
        path = root / analysis_context.FILENAME; raw = path.read_bytes()
        self.addCleanup(path.write_bytes, raw)
        path.write_bytes(raw + b'0x1000000\tfunction\tprivate_name\n')
        with patch.object(runtime_attestation, 'capture', side_effect=AssertionError('Inventory')) as capture, \
             patch.object(runtime_attestation, 'provider', side_effect=AssertionError('Provider')) as provider:
            with self.assertRaises(ValueError): runtime_attestation.verify(task)
            capture.assert_not_called(); provider.assert_not_called()


    def test_78_explicit_role_contract_is_public_syntax_only_and_optional(self):
        from capabilities import artifact_format
        manifest = {'artifact_schema': {'types.json': 1}, 'condition': 'scalar_recover'}
        raw = action_cache.canonical(manifest)
        old = coordinator.model_prompt(b'Question', raw)
        explicit = coordinator.model_prompt(b'Question', raw, format_policy=artifact_format.POLICY)
        self.assertEqual(explicit, old + artifact_format.NOTICE)
        self.assertIn(b'"role":"member"', explicit)
        self.assertIn(b'"role":["member"]', explicit)
        for kind, schema in [('abi_probe', {'types.json': 1}), ('localization', {'locations.json': 1}),
                             ('c_buffer', {'solution.c': 1}), ('abi_probe', {'abi.json': 1})]:
            case = {'kind': kind, 'artifact_schema': schema}
            self.assertIsNone(artifact_format.policy(case, {}))
            expected = artifact_format.POLICY if kind == 'abi_probe' and schema == {'types.json': 1} else None
            self.assertEqual(artifact_format.policy(case, {'explicit_type_roles': True}), expected)
        for value in (1, 'true', None):
            with self.assertRaises(ValueError): artifact_format.enabled({'explicit_type_roles': value})

    def test_79_rejected_type_artifacts_do_not_claim_executed_returns(self):
        from capabilities import submission
        from capabilities.abi import contracts
        case = tasks.frozen('record_codec_sysv_amd64_scalar_recover')
        task = {**case, 'track': 'capability', 'artifact_contract_version': 1}
        value = store.spec(task)
        artifact = contracts.gold(case['family_id'], case['binary_sha256'])
        for node in artifact['types']:
            for field in node.get('fields', []): field['role'] = [field['role']]
        answer = b'```types.json\n' + action_cache.canonical(artifact) + b'\n```\n'
        result, _ = submission.evaluate(answer, task, value)
        self.assertFalse(result['artifact_valid'])
        self.assertEqual(result['passed'], 0)
        self.assertEqual(result['return_provenance'], 'not_executed')


    def test_80_sigint_cancels_active_queue_and_preserves_stop_status(self):
        script = r'''import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from unittest.mock import patch
import run
from capabilities.registration import Registry
from capabilities import coordinator
root = Path(sys.argv[1]); run.ROOT = root; run.BENCH = root / 'benchmarks'
(run.BENCH / 'config').mkdir(parents=True)
(run.BENCH / 'config/execution.json').write_text(json.dumps({'experiments_stopped': True}))
selected = [{'model': 'offline', 'case': 'control', 'experiment': 'control', 'suite': 'control',
             'condition': 'control', 'status': 'not_started'}]
selection = {'execution': {'max_parallel_models': 1}}
started = threading.Event()
def execute(entry, model, cancel, on_launch):
    started.set()
    if not cancel.wait(5): raise AssertionError('SIGINT did not cancel active work')
    raise InterruptedError('Offline task cancelled')
def interrupt():
    if not started.wait(5): return
    os.kill(os.getpid(), signal.SIGINT)
threading.Thread(target=interrupt, daemon=True).start()
previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
with (patch.object(run.records, 'models', return_value={'offline': {'id': 'offline'}}),
      patch.object(Registry, 'begin', return_value=None),
      patch.object(Registry, 'complete', return_value=(selected, None)),
      patch.object(coordinator, 'prepare_selected', return_value=selected),
      patch.object(run, 'execute', side_effect=execute),
      patch.object(socket.socket, 'connect', side_effect=AssertionError('Provider network'))):
    try:
        run.run_batch(selected, selection, finalize=lambda: (root / 'finalized').write_text('yes'))
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError('SIGINT was swallowed')
assert json.loads((run.BENCH / 'config/execution.json').read_text())['experiments_stopped']
assert not (root / '.cache/benchmark-runtime/live-batch.lock').exists()
assert not (root / 'finalized').exists()
status = json.loads((run.BENCH / 'config/benchmark-status.json').read_text())
assert status['selected_tasks'] == status['unprocessed_tasks'] == 1
assert status['interrupted'] and status['model_api_calls'] == 0
assert {number: signal.getsignal(number) for number in previous} == previous
'''
        with tempfile.TemporaryDirectory(prefix='sigint-control-', dir=action_cache.ROOT / '.cache/benchmark-runtime') as temporary:
            child = subprocess.run([sys.executable, '-c', script, temporary],
                cwd=action_cache.ROOT, env={**os.environ, 'PYTHONPATH': str(action_cache.ROOT / 'benchmarks/scripts')},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12)
            self.assertEqual(child.returncode, 0, child.stderr.decode())


    def test_81_both_harnesses_bind_explicit_roles_and_keep_control_inputs_matched(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        from capabilities import artifact_format, evidence_access
        settings, families = config.load()
        settings = copy.deepcopy(settings)
        settings['runtime']['explicit_type_roles'] = True
        settings['runtime'].pop('known_entry_seeds', None)
        models = [self.model, next(value for value in records.models().values() if value['harness'] == 'zcode')]
        with patch.object(config, 'load', return_value=(settings, families)):
            for model in models:
                directory, entry, task, grade = self.fixture('record_codec_sysv_amd64_scalar_recover', model)
                self.assertTrue(grade['task_success'])
                self.assertEqual(task['artifact_format_policy'], artifact_format.POLICY)
                root = Path(task['input_directory']); prompt = (root / 'prompt.txt').read_bytes()
                self.assertEqual(prompt.count(artifact_format.NOTICE), 1)
                self.assertTrue(json.loads((root / 'harness-config.json').read_bytes())['explicit_type_roles'])
                before = {name: records.sha(action_cache.ROOT / task['public_directory'] / name)
                          for name in ('target', 'prompt.txt', 'manifest.json', 'tools.json')}
                control_entry = {**entry, 'directory': entry['directory'] + '-premises', 'evidence_access': 'premises_only'}
                self.addCleanup(shutil.rmtree, action_cache.ROOT / control_entry['directory'], ignore_errors=True)
                control = coordinator.prepare(control_entry, model, materialize=True, offline_fixture=True)
                other = Path(control['input_directory'])
                self.assertEqual(control['artifact_format_policy'], artifact_format.POLICY)
                self.assertEqual((other / 'prompt.txt').read_bytes(), prompt + evidence_access.NOTICE)
                self.assertEqual((other / 'target').read_bytes(), b'')
                self.assertEqual(json.loads((other / 'tools.json').read_bytes())['tools'], [])
                self.assertNotEqual(control['fingerprint'], task['fingerprint'])
                self.assertEqual(before, {name: records.sha(action_cache.ROOT / task['public_directory'] / name) for name in before})


    def test_82_inspection_validates_original_binding_once(self):
        temporary, local, declaration, value = self.preparation_fixture()
        with (patch.object(tasks, 'frozen', wraps=tasks.frozen) as frozen,
              patch.object(preparation, '_validate', side_effect=AssertionError('Duplicate original validation'))):
            state = preparation.inspect(declaration, cache=local)
        self.assertEqual(state['status'], 'ready')
        self.assertEqual(state['case'], value)
        frozen.assert_called_once_with(declaration['task_id'])


    def test_83_seeded_cache_replay_and_publication_bind_the_seed_leaf(self):
        # This legacy-cache fixture declares v2; do not mix in the active v3 policy.
        from capabilities import config as fixture_config
        fixture_settings, fixture_families = fixture_config.load()
        self.enterContext(patch.object(fixture_config, 'load', return_value=(
            {**fixture_settings, 'condition': 'paged-evidence-v2'}, fixture_families)))
        directory, entry, task, grade = self.fixture('record_codec_c_recover')
        if not task.get('analysis_context_policy'):
            settings, families = config.load()
            settings = copy.deepcopy(settings); settings['runtime']['known_entry_seeds'] = True
            with patch.object(config, 'load', return_value=(settings, families)):
                directory, entry, task, grade = self.fixture('record_codec_c_recover')
        files = cache.inference_files(task)
        self.assertIn('analysis.seeds', files)
        self.assertEqual(action_cache.inference(task['inference_identity'], files, trial=entry['directory']).key,
                         task['fingerprint'])
        cache.register_inference(task, directory)
        seed = Path(task['input_directory']) / 'analysis.seeds'
        original = seed.read_bytes()
        try:
            seed.write_bytes(original + b'0x1234\tfunction\n')
            with self.assertRaises(ValueError): cache.inference_files(task)
        finally:
            seed.write_bytes(original)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=1,
                        help='independent offline worker processes (1-16)')
    parser.add_argument('--uncached', action='store_true', help='Explicit full legacy regression run')
    parser.add_argument('--tests', nargs='+', help='Execute/cache only named checks; does not admit the full suite')
    parser.add_argument('--plan', action='store_true', help='Show check action/cache status without workers')
    options = parser.parse_args(argv)
    if not 1 <= options.jobs <= 16:
        parser.error('--jobs must be between 1 and 16')
    if not options.uncached:
        import validation_cache
        names = unittest.defaultTestLoader.getTestCaseNames(TypedCoordinatorControls)
        chosen = options.tests or names
        if not set(chosen).issubset(names):
            parser.error('Unknown coordinator check')
        plan = validation_cache.Plan(action_cache.ROOT, evidence.SOURCES, chosen)
        if options.plan:
            cache_store = action_cache.Cache()
            print(json.dumps({'checks': [{'test': name, 'action': plan.actions[name].key,
                  'cache_hit': plan.lookup(cache_store, name) is not None} for name in chosen],
                  'model_api_calls': 0}, indent=2))
            return
        value = validation_cache.execute(plan, jobs=options.jobs)
        if set(chosen) == set(names):
            validation_cache.write_receipt(evidence.RECEIPT, plan, value)
        print(json.dumps(value))
        raise SystemExit(0 if value['passed'] else 1)
    if options.tests or options.plan:
        parser.error('--uncached cannot be combined with --tests or --plan')
    import parallel_controls
    started = evidence.begin()
    if options.jobs == 1:
        with (evidence.validation_controls(), parallel_controls.metadata_validation_cache(),
              patch.object(socket.socket, 'connect', side_effect=AssertionError('Provider network')),
              patch.object(run, 'launch_model', side_effect=AssertionError('Unmocked provider launch'))):
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(TypedCoordinatorControls)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        import parallel_controls
        names = unittest.defaultTestLoader.getTestCaseNames(TypedCoordinatorControls)
        directory = Path(tempfile.mkdtemp(prefix='coordinator-controls-',
                            dir=action_cache.ROOT / '.cache/benchmark-runtime'))
        aggregate = parallel_controls.run_isolated(action_cache.ROOT, names, evidence.SOURCES,
            directory, module='check_capability_coordinator', test_class='TypedCoordinatorControls',
            jobs=options.jobs)
        result = parallel_controls.AggregateResult(aggregate, directory.relative_to(action_cache.ROOT))
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__':
    main()
