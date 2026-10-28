#!/usr/bin/python3
"""Typed report controls using saved-record fixtures; no compilers or providers."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import action_cache
import usage
from capabilities import config, measurements, tasks
from capabilities.coordinator import _namespace

ROOT = action_cache.ROOT


class MeasurementControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rea-typed-report-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        original = json.loads((ROOT / 'benchmarks/config/capabilities.json').read_text())
        self.settings = {**original, 'inference_enabled': True,
                         'runtime': {**original['runtime'], 'known_entry_seeds': False, 'explicit_type_roles': False},
                         'profiles': [row for row in original['profiles'] if row['id'] in ('localize', 'c_recover')]}
        # Historical fixture conditions must not inherit deployment switches.
        # Dedicated controls below explicitly exercise seeded and format policies.
        self.families = {'families': [{'id': 'record_codec'}]}
        self.models = {name: {'id': name, 'label': name, 'harness': 'deepseek-harness', 'reasoning': 'low'}
                       for name in ('model-a', 'model-b')}
        self.selection = {'models': 'all', 'suites': 'all', 'experiments': 'primary',
                          'cases': 'all', 'condition': 'active', 'capabilities': True}
        self.write('benchmarks/config/capabilities.json', self.settings)
        self.write('benchmarks/config/capability-measurement-policy.json',
                   (ROOT / 'benchmarks/config/capability-measurement-policy.json').read_bytes())
        self.write(self.settings['families_file'], self.families)
        self.write('benchmarks/config/execution.json', {'experiments_stopped': True})
        self.write(self.settings['pricing_file'], {'unit_tokens': 1000000, **{name: {
            'short': {'input': 1, 'cache_read': 1, 'cache_write': 1, 'output': 1},
            'short_context_max_input': 10000, 'long_multipliers': {key: 1 for key in ('input', 'cache_read', 'cache_write', 'output')}}
            for name in self.models}})
        self.addCleanup(patch.stopall)
        patch.object(config, 'load', return_value=(self.settings, self.families)).start()
        for obj, method in [(socket.socket, 'connect'), (socket, 'create_connection'),
                            (subprocess, 'Popen'), (subprocess, 'run'), (action_cache.Cache, 'commit')]:
            patch.object(obj, method, side_effect=AssertionError('Reporting tried to execute, connect or populate a cache')).start()
        self.cases = {}
        for declaration in tasks.declared():
            self.freeze(declaration)
        pins = []
        for name, case in self.cases.items():
            pins.append({'task_id': name, **{key: case[key] for key in ('family_id', 'profile_id', 'binary_sha256',
                                                                      'public_package_sha256', 'capability_ids')}})
        self.write('benchmarks/config/capability-task-pins.json', {'schema_version': 1, 'model_api_calls': 0,
            'tasks': pins, 'inputs_sha256': {'benchmarks/config/capabilities.json':
                measurements.digest((self.root / 'benchmarks/config/capabilities.json').read_bytes())}})

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else action_cache.canonical(value))
        return path

    def freeze(self, declaration):
        profile, name = declaration['profile'], declaration['task_id']
        life = profile['kind'] in ('c_lifecycle', 'cpp_lifecycle')
        pointer = profile['kind'] == 'pointer_probe'
        variant = profile['kind'] == 'variant_object'
        indirect = profile['kind'] == 'indirect_call'
        optimized = profile['kind'] == 'optimized_localization'
        native = profile['kind'] == 'native_abi'
        matched = profile['kind'] == 'matched_buffer'
        ids = ['L6'] if optimized else ['T7'] if native else ['T6'] if indirect else ['T4'] if variant else ['T3'] if pointer else (['C5', 'C6'] if declaration['family_id'] == 'callback_policy' else ['C6']) if life else (
            ['C1', 'C2', 'C3', 'C4'] if profile['kind'] in ('c_buffer', 'cpp_buffer', 'matched_buffer') else ['L1', 'L2', 'L4', 'L5'])
        hint = 'no_entry_optimized_locations_v1' if optimized else 'known_native_aggregate_endpoints_v1' if native else 'known_indirect_endpoints_v1' if indirect else 'known_variant_endpoints_v1' if variant else 'known_pointer_endpoints_v1' if pointer else 'known_lifecycle_entry_v1' if life else 'known_buffer_entry_v1' if profile['kind'] == 'c_buffer' else 'no_business_entry_hints_v1'
        if matched: hint = 'known_matched_buffer_entry_v1'
        source_language = profile['variant'] if native else (
            'cpp' if profile['original_compiler'] == 'clang20' else profile['original_compiler']) if matched else 'cpp'
        metadata = {'task_id': 'task_' + measurements.digest(name.encode())[:20],
            'binary_sha256': measurements.digest(name.encode()), 'build_id': measurements.digest(name.encode()),
            'source_language': source_language, 'output_language': profile['output_language'], 'artifact_schema': profile['artifact_schema'],
            'capability_ids': ids, 'hint_profile': hint}
        files = {'target': name.encode(), 'prompt.txt': b'Public question', 'tools.json': b'{"tools":[]}',
                 'manifest.json': action_cache.canonical(metadata)}
        hashes = {key: measurements.digest(raw) for key, raw in files.items()}
        package = measurements.digest(action_cache.canonical(hashes))
        directory = '.cache/benchmark-capabilities/task-freezes/' + package
        for key, raw in files.items():
            self.write(directory + '/' + key, raw)
        spec = {'kind': profile['kind'], 'binary_sha256': hashes['target']}
        spec_hash = measurements.digest(action_cache.canonical(spec))
        spec_name = '.cache/benchmark-capabilities/grade-specs/' + spec_hash + '.json'
        self.write(spec_name, spec)
        case = {'id': name, 'task_id': name, 'family_id': declaration['family_id'], 'profile_id': profile['id'],
            'kind': profile['kind'], 'build_id': measurements.digest(name.encode()),
            'public_package_sha256': package, 'public_directory': directory, 'public_files_sha256': hashes,
            'binary_sha256': hashes['target'], 'artifact_schema': profile['artifact_schema'],
            'capability_ids': ids,
            'source_language': metadata['source_language'], 'output_language': profile['output_language'],
            'hint_profile': hint,
            'grading_spec': spec_name, 'grading_spec_sha256': spec_hash}
        self.write('.cache/benchmark-capabilities/task-index/' + name + '.json', case)
        self.cases[name] = case

    def attempt(self, model='model-a', name='record_codec_c_recover', *, fixture=False, access='binary'):
        case = self.cases[name]
        namespace = _namespace(self.settings, self.models[model], access)
        directory = 'benchmarks/runs/capabilities-v1/' + model + '/' + name + '/' + case['public_package_sha256'] + '/' + namespace
        task = {**case, 'track': 'capability', 'artifact_contract_version': 1, 'model': model,
                'fingerprint': measurements.digest(directory.encode())}
        if access != 'binary':
            from capabilities import evidence_access
            from capabilities.coordinator import model_prompt
            package = self.root / case['public_directory']
            inputs = {key: (package / key).read_bytes() for key in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
            inputs['prompt.txt'] = model_prompt(inputs['prompt.txt'], inputs['manifest.json'])
            inputs = evidence_access.project(inputs, access)
            relative = '.cache/benchmark-runtime/tasks/' + task['fingerprint']
            task.update(evidence_access=access, input_directory=str(self.root / relative),
                        request_contract={'allowed_tool_names': []},
                        inference_identity={key: measurements.digest(inputs[name]) for name, key in
                            (('target', 'binary'), ('prompt.txt', 'prompt'), ('tools.json', 'tools'), ('manifest.json', 'manifest'))})
            for key, raw in inputs.items():
                self.write(relative + '/' + key, raw)
        if fixture:
            task['offline_fixture'] = True
        self.write(directory + '/task.json', task)
        self.write(directory + '/answer.md', b'Private answer and reasoning must never be published')
        self.write(directory + '/result.json', {'model': model, 'output_language': case['output_language'],
            'started_at': '2026-10-21T00:00:00Z', 'completed_at': '2026-10-21T00:00:01Z', 'usage_complete': True,
            'requests': [{'request_id': 'r1', 'finished_at': '2026-10-21T00:00:01Z', 'forwarded': True}],
            'usage_observations': [{'request_id': 'r1', 'usage': {'inputTokens': 3, 'outputTokens': 2, 'totalTokens': 5}}]})
        self.grade(directory, task, passed=1)
        return directory, task

    def addressed_attempt(self, directory, task, revision):
        action = action_cache.Action('inference', {'configuration/effective.json':
            action_cache.canonical({'trial': directory, 'runtime_revision': revision})})
        for key, raw in action.blobs.items():
            self.write('.cache/benchmark-actions/v3/cas/' + key, raw)
        physical = str(Path(directory).parent / action.key)
        shutil.copytree(self.root / directory, self.root / physical)
        task = {**task, 'directory': physical, 'inference_trial': directory,
                'fingerprint': action.key, 'action_descriptor': action.descriptor}
        self.write(physical + '/task.json', task)
        return physical, task

    def test_49_action_addressed_observation_is_associated_with_configured_cell(self):
        directory, task = self.attempt()
        physical, task = self.addressed_attempt(directory, task, 'runtime-v2')
        shutil.rmtree(self.root / directory)
        before = self.hashes()
        value = self.collect()
        cell = next(row for row in value['cells'] if row['model'] == task['model'] and row['profile_id'] == task['profile_id'])
        self.assertEqual(cell['observation']['attempt_id'], task['fingerprint'])
        self.assertEqual(cell['status'], 'graded')
        self.assertEqual(value['invalid_attempt_records'], 0)
        self.assertEqual(before, self.hashes())

    def test_50_multiple_runtime_actions_keep_history_without_selecting_best_or_newest(self):
        directory, task = self.attempt()
        physical, changed = self.addressed_attempt(directory, task, 'runtime-v2')
        value = self.collect()
        cell = next(row for row in value['cells'] if row['model'] == task['model'] and row['profile_id'] == task['profile_id'])
        self.assertEqual(len(value['history']), 2)
        self.assertEqual(cell['status'], 'action_selection_required')
        self.assertIsNone(cell['observation'])
        self.assertEqual(value['invalid_attempt_records'], 0)

    def test_51_uncommitted_logical_trial_cannot_reassign_a_saved_action(self):
        directory, task = self.attempt()
        physical, task = self.addressed_attempt(directory, task, 'runtime-v2')
        shutil.rmtree(self.root / directory)
        task['inference_trial'] = str(Path(directory).parent / ('f' * 64))
        self.write(physical + '/task.json', task)
        value = self.collect()
        self.assertEqual(value['invalid_attempt_records'], 1)
        self.assertEqual(value['history'], [])

    def grade(self, directory, task, *, passed):
        answer = (self.root / directory / 'answer.md').read_bytes()
        action = action_cache.Action('capability_submission_v1', {'answer.md': answer, 'revision': str(passed).encode()})
        grade = {'grading_id': action.key, 'grading_version': 'capability-artifacts-v1',
                 'artifact_schema': task['artifact_schema'], 'capability_ids': task['capability_ids'],
                 'artifact_valid': True, 'task_success': passed == 1, 'compiled': True,
                 'passed': passed, 'total': 1, 'return_provenance': 'host_emulator_return_v1',
                 'capabilities': {key: {'passed': passed, 'total': 1} for key in task['capability_ids']}}
        if task['kind'] == 'optimized_localization':
            grade['capabilities'] = {'L6': {'f1': float(passed), 'precision': float(passed), 'coverage': float(passed)}}
            grade['return_provenance'] = 'structured_artifact'
        if task['kind'] == 'native_abi':
            grade['return_provenance'] = 'host_native_abi_return_v1'
        source = b'int32_t reconstructed(void) { return 0; }'
        relative = 'grades/capability-artifacts-v1/' + action.key + '/grade.json'
        base = str(Path(relative).parent)
        self.write(directory + '/' + relative, grade)
        artifact = 'instances.json' if task['kind'] == 'optimized_localization' else 'native-abi.json' if task['kind'] == 'native_abi' else 'calls.json' if task.get('kind') == 'indirect_call' else 'types.json' if task.get('kind') in ('pointer_probe', 'variant_object') else 'solution.cpp' if task['output_language'] == 'cpp' else 'solution.c'
        if task['kind'] == 'matched_buffer':
            artifact = {'c': 'solution.c', 'cpp': 'solution.cpp', 'rust': 'solution.rs', 'go': 'solution.go'}[task['output_language']]
        self.write(directory + '/' + base + '/artifacts/' + artifact, source)
        files = {'grade.json': measurements.digest(action_cache.canonical(grade)),
                 'artifacts/' + artifact: measurements.digest(source)}
        binding = {key: task[key] for key in ('binary_sha256', 'artifact_schema', 'capability_ids', 'grading_spec', 'grading_spec_sha256')}
        self.write(directory + '/' + base + '/manifest.json', {'cache_schema': 3, 'action': action.descriptor,
            'grading_id': action.key, 'answer_sha256': measurements.digest(answer), 'task_binding': binding, 'files_sha256': files})
        self.write(directory + '/selected-grade.json', {'grading_id': action.key, 'path': relative, 'sha256': files['grade.json']})
        return relative

    def collect(self, current=True):
        return measurements.collect(Path.read_bytes, self.models, self.selection, preparation_current=current, root=self.root)

    def hashes(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def test_01_missing_cells_keep_denominator_without_observations(self):
        value = self.collect()
        self.assertEqual(value['configured_cells'], 6)
        self.assertEqual(value['scheduled_cells'], 6)
        self.assertEqual(value['statuses'], {'not_started': 6})
        self.assertEqual(value['saved_observations'], 0)
        self.assertTrue(all(row['complete_macro'] is None for row in value['macro_vectors']))
        self.assertEqual(value['released_capabilities'], 0)

    def test_02_disabled_inventory_is_not_a_measured_or_scheduled_denominator(self):
        self.settings['inference_enabled'] = False
        self.write('benchmarks/config/capabilities.json', self.settings)
        self.attempt()
        value = self.collect()
        self.assertEqual((value['configured_cells'], value['disabled_cells'], value['scheduled_cells']), (6, 6, 0))
        self.assertEqual(value['saved_observations'], 1)
        self.assertEqual(value['macro_vectors'], [])

    def test_03_cached_observation_is_read_only_and_has_request_accounting(self):
        self.attempt()
        before = self.hashes()
        first, second = self.collect(), self.collect()
        self.assertEqual(first, second)
        self.assertEqual(self.hashes(), before)
        history = first['history'][0]
        self.assertEqual(history['status'], 'graded')
        self.assertEqual(history['known_tokens']['totalTokens'], 5)
        self.assertEqual(history['usage_status'], 'complete')
        self.assertAlmostEqual(history['cost']['low_usd'], 0.000005)
        self.assertEqual(history['readability']['provenance'], 'not_reviewed')
        self.assertNotIn('Private answer', json.dumps(first))
        self.assertNotIn('record_codec_absent', json.dumps(first))

    def test_04_grading_revisions_are_explicit_and_do_not_add_samples(self):
        directory, task = self.attempt()
        self.grade(directory, task, passed=0)
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(value['saved_observations'], 1)
        self.assertEqual(len(row['grade_revisions']), 2)
        self.assertEqual(sum(item['selected'] for item in row['grade_revisions']), 1)
        self.assertEqual(row['grade']['passed'], 0)
        self.assertEqual(row['grade']['scores']['C1']['score'], 0)

    def test_05_changed_answer_or_selected_grade_is_not_correctness(self):
        directory, _ = self.attempt()
        self.write(directory + '/answer.md', b'changed')
        row = self.collect()['history'][0]
        self.assertEqual(row['status'], 'record_invalid')
        self.assertIsNone(row['grade'])
        self.assertEqual(row['known_tokens']['totalTokens'], 5)

    def test_06_environment_failure_preserves_usage_but_removes_correctness(self):
        directory, _ = self.attempt()
        self.write(directory + '/harness-failure.json', {'error': 'PRIVATE error'})
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(row['status'], 'environment_failure')
        self.assertIsNone(row['grade'])
        self.assertEqual(row['known_tokens']['totalTokens'], 5)
        self.assertNotIn('PRIVATE error', json.dumps(value))

    def test_07_incomplete_usage_survives_a_successful_grade(self):
        directory, _ = self.attempt()
        receipt = json.loads((self.root / directory / 'result.json').read_bytes())
        self.write(directory + '/result.json', {**receipt, 'usage_complete': False})
        row = self.collect()['history'][0]
        self.assertEqual(row['status'], 'graded')
        self.assertEqual(row['usage_status'], 'partial')
        self.assertFalse(row['cost']['usage_complete'])

    def test_08_fixture_attempts_are_never_model_observations(self):
        self.attempt(fixture=True)
        value = self.collect()
        self.assertEqual(value['saved_observations'], 0)
        self.assertEqual(value['statuses'], {'not_started': 6})

    def test_09_duplicate_saved_copies_are_not_additional_observations(self):
        directory, _ = self.attempt()
        shutil.copytree(self.root / directory, self.root / (directory + '-alias'))
        value = self.collect()
        self.assertEqual(value['saved_observations'], 1)
        self.assertEqual(value['duplicate_saved_copies'], 1)

    def test_10_pending_invalid_and_stale_preparation_remain_visible(self):
        name = 'record_codec_c_recover'
        index = self.root / ('.cache/benchmark-capabilities/task-index/' + name + '.json')
        index.unlink()
        value = self.collect()
        self.assertEqual(value['statuses']['preparation_pending'], 2)
        index.write_bytes(b'not JSON')
        value = self.collect()
        self.assertEqual(value['statuses']['preparation_invalid'], 2)
        self.assertEqual(value['scheduled_cells'], 6)
        self.assertEqual(self.collect(False)['statuses']['preparation_stale'], 4)

    def test_11_invalid_readability_cannot_remove_behavioral_scores(self):
        directory, _ = self.attempt()
        self.write(directory + '/readability-review.json', {'candidate_source_sha256': '0' * 64, 'scores': {}})
        row = self.collect()['history'][0]
        self.assertEqual(row['status'], 'graded')
        self.assertEqual(row['readability']['provenance'], 'invalid_review')

    def test_12_public_package_and_selection_escapes_fail_closed(self):
        directory, _ = self.attempt()
        self.write(directory + '/selected-grade.json', {'path': '../../outside/grade.json'})
        self.assertEqual(self.collect()['history'][0]['status'], 'record_invalid')
        public = self.root / self.cases['record_codec_c_recover']['public_directory'] / 'target'
        public.unlink()
        public.symlink_to('/etc/passwd')
        value = self.collect()
        self.assertEqual(value['statuses']['preparation_invalid'], 2)

    def test_13_current_selection_and_snapshot_do_not_claim_prerun_freezing(self):
        all_cells = self.collect()
        self.selection['cases'] = ['record_codec_c_recover']
        selected = self.collect()
        self.assertEqual(selected['scheduled_cells'], 2)
        self.assertNotEqual(selected['schedule_id'], all_cells['schedule_id'])
        self.assertEqual(selected['schedule_status'], 'configuration_snapshot_only')
        self.assertEqual(selected['released_capabilities'], 0)

    def test_14_localization_preserves_component_metrics_and_not_applicable(self):
        grade = {'artifact_valid': True, 'capabilities': {'L1': {'top1': True},
            'L2': {'f1': 0.5, 'precision': 1, 'coverage': 1/3},
            'L5': {'root_correct': True, 'nodes': {'f1': 1}, 'edges': {'f1': 0.5}}}}
        values = measurements.score_vector(grade, ['L1', 'L2', 'L3', 'L5'])
        self.assertEqual(values['L1']['score'], 1)
        self.assertEqual(values['L2']['score'], 0.5)
        self.assertIsNone(values['L3']['score'])
        self.assertEqual(values['L5']['score'], 0)
        self.assertEqual(values['L5']['components']['edges_f1'], 0.5)

    def test_15_macro_coverage_matched_cases_and_lineage_weights(self):
        def cell(model, family, package, score, lineage=None):
            return {'scheduled': True, 'model': model, 'profile_id': 'c_recover', 'capability_ids': ['C1'],
                    'family_id': family, 'lineage_id': lineage or family, 'public_package_sha256': package,
                    'observation': {'grade': {'scores': {'C1': {'score': score}}}} if score is not None else None}
        cells = [cell('a', 'f1', 'p1', 1), cell('a', 'f2', 'p2', 0),
                 cell('b', 'f1', 'p1', 1), cell('b', 'f2', 'p2', None)]
        a, b = measurements.macro_vectors(cells, ['a', 'b'])
        self.assertEqual(a['observed_macro'], 0.5)
        self.assertEqual(a['complete_macro'], 0.5)
        self.assertEqual(a['matched_macro'], b['matched_macro'])
        self.assertEqual(a['matched_macro'], 1)
        self.assertIsNone(b['complete_macro'])
        rows = [cell('a', 'f1', 'p1', 1, 'shared'), cell('a', 'f1', 'p1-opt', 1, 'shared'),
                cell('a', 'f2', 'p2', 1, 'shared'), cell('a', 'f3', 'p3', 0, 'other')]
        value = measurements.macro_vectors(rows, ['a'])[0]
        self.assertEqual(value['observed_macro'], 0.5)
        self.assertEqual(value['observed_families'], 3)
        self.assertEqual(value['observed_lineages'], 2)

    def test_16_projection_is_allowlisted_and_rejects_score_or_denominator_changes(self):
        self.attempt()
        raw = self.collect()
        raw['private_truth'] = {'locations': 'PRIVATE'}
        raw['history'][0]['reasoning'] = 'PRIVATE'
        value = measurements.public_projection(raw)
        self.assertNotIn('PRIVATE', json.dumps(value))
        measurements.validate_projection(value)
        import public_report
        public_report.validate_value(value, public_report.SCHEMA['capability_measurements'])
        for mutation in ('scheduled_cells', 'released_capabilities'):
            bad = copy.deepcopy(value)
            bad[mutation] += 1
            with self.assertRaises(ValueError):
                measurements.validate_projection(bad)
        bad = copy.deepcopy(value)
        bad['macro_vectors'][0]['complete_macro'] = 0 if value['macro_vectors'][0]['complete_macro'] == 1 else 1
        with self.assertRaises(ValueError):
            measurements.validate_projection(bad)
        bad = copy.deepcopy(value)
        bad['validation'] = {'passed': True, 'current': True, 'checks': 0, 'model_api_calls': 0}
        with self.assertRaises(ValueError):
            measurements.validate_projection(bad)

    def test_17_corrupted_accounting_revision_does_not_hide_behavior(self):
        directory, _ = self.attempt()
        self.write(directory + '/accounting/request-ledger-v1/' + '0'*64 + '/record.json', {'identity': {}})
        row = self.collect()['history'][0]
        self.assertEqual(row['status'], 'graded')
        self.assertEqual(row['invalid_accounting_revisions'], 1)

    def test_18_empty_usage_is_unknown_and_not_a_zero_bill(self):
        directory, _ = self.attempt()
        receipt = json.loads((self.root / directory / 'result.json').read_bytes())
        receipt.pop('requests')
        receipt.pop('usage_observations')
        self.write(directory + '/result.json', receipt)
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(row['usage_status'], 'unknown')
        self.assertEqual(row['known_tokens'], {})
        self.assertIsNone(row['cost']['low_usd'])
        total = next(row for row in value['totals'] if row['model'] == 'model-a')
        self.assertIsNone(total['known_tokens']['totalTokens'])
        self.assertFalse(total['cost']['usage_complete'])

    def test_19_retired_configuration_observation_stays_in_history(self):
        directory, _ = self.attempt()
        path = self.root / directory
        path.rename(path.with_name('old-settings'))
        value = self.collect()
        self.assertEqual(value['saved_observations'], 1)
        self.assertTrue(all(row['observation'] is None for row in value['cells']))
        self.assertTrue(all(row['complete_macro'] is None for row in value['macro_vectors']))

    def test_20_localization_absence_and_output_contracts_are_not_source_readability(self):
        value = self.collect()
        structured = [row for row in value['cells'] if row['output_language'] is None]
        self.assertEqual(len(structured), 4)
        missing = measurements.score_vector({'artifact_valid': True, 'capabilities': {'L1': {'top1': True}}}, ['L1', 'L2', 'L5'])
        self.assertEqual(missing['L1']['score'], 1)
        self.assertIsNone(missing['L2']['score'])
        self.assertIsNone(missing['L5']['score'])

    def test_21_accounting_revisions_bind_payloads_to_original_merkle_inputs(self):
        directory, _ = self.attempt()
        receipt = (self.root / directory / 'result.json').read_bytes()
        ledger = usage.ledger(json.loads(receipt))
        cost = {'low_usd': 0.000005, 'high_usd': 0.000005, 'usage_complete': True}
        action = action_cache.Action('accounting', {
            'evidence/ledger.json': action_cache.canonical({key: value for key, value in ledger.items() if key != 'receipt_sha256'}),
            'evidence/cost.json': action_cache.canonical(cost)})
        for key, raw in action.blobs.items():
            self.write('.cache/benchmark-actions/v3/cas/' + key, raw)
        path = directory + '/accounting/request-ledger-v1/' + action.key + '/record.json'
        value = {'identity': {'action': action.descriptor, 'version': usage.VERSION, 'receipt_sha256': measurements.digest(receipt)},
                 'ledger': ledger, 'cost': cost}
        self.write(path, value)
        row = self.collect()['history'][0]
        self.assertEqual(len(row['accounting_revisions']), 1)
        self.assertEqual(row['invalid_accounting_revisions'], 0)
        self.write(path, {**value, 'cost': {**cost, 'low_usd': 9}})
        row = self.collect()['history'][0]
        self.assertEqual(len(row['accounting_revisions']), 0)
        self.assertEqual(row['invalid_accounting_revisions'], 1)
        self.assertEqual(row['status'], 'graded')

    def test_22_conflicting_copies_do_not_choose_a_sample_by_filename(self):
        directory, _ = self.attempt()
        alias = directory + '-alias'
        shutil.copytree(self.root / directory, self.root / alias)
        receipt = json.loads((self.root / alias / 'result.json').read_bytes())
        self.write(alias + '/result.json', {**receipt, 'usage_complete': False})
        value = self.collect()
        self.assertEqual(value['saved_observations'], 0)
        self.assertEqual(value['invalid_attempt_records'], 2)
        self.assertEqual(value['statuses']['record_invalid'], 1)
        self.assertTrue(all(row['cost']['low_usd'] is None for row in value['totals']))

    def test_23_partial_pins_validate_other_prepared_cells_without_inventing_new_pins(self):
        path = 'benchmarks/config/capability-task-pins.json'
        pins = json.loads((self.root / path).read_bytes())
        pins['tasks'] = [row for row in pins['tasks'] if row['task_id'] != 'record_codec_c_recover']
        self.write(path, pins)
        self.assertEqual(self.collect()['statuses']['preparation_stale'], 2)
        (self.root / '.cache/benchmark-capabilities/task-index/record_codec_c_recover.json').unlink()
        value = self.collect()
        self.assertEqual(value['statuses'], {'not_started': 4, 'preparation_pending': 2})
        self.assertEqual(value['scheduled_cells'], 6)

    def test_24_typed_index_cannot_relabel_verified_public_build_metadata(self):
        path = '.cache/benchmark-capabilities/task-index/record_codec_c_recover.json'
        case = json.loads((self.root / path).read_bytes())
        self.write(path, {**case, 'build_id': '0' * 64})
        value = self.collect()
        self.assertEqual(value['statuses']['preparation_invalid'], 2)
        self.assertEqual(value['scheduled_cells'], 6)

    def lifecycle_inventory(self):
        original = json.loads((ROOT / 'benchmarks/config/capabilities.json').read_text())
        self.settings['profiles'].extend(copy.deepcopy(row) for row in original['profiles']
                                         if row['kind'] in ('c_lifecycle', 'cpp_lifecycle'))
        self.families['families'].append({'id': 'callback_policy'})
        self.write('benchmarks/config/capabilities.json', self.settings)
        self.write(self.settings['families_file'], self.families)
        for declaration in tasks.declared():
            if declaration['task_id'] not in self.cases: self.freeze(declaration)
        pins = [{'task_id': name, **{key: case[key] for key in ('family_id', 'profile_id', 'binary_sha256',
                 'public_package_sha256', 'capability_ids')}} for name, case in self.cases.items()]
        self.write('benchmarks/config/capability-task-pins.json', {'schema_version': 1, 'model_api_calls': 0,
            'tasks': pins, 'inputs_sha256': {'benchmarks/config/capabilities.json':
                measurements.digest((self.root / 'benchmarks/config/capabilities.json').read_bytes())}})

    def test_25_unprepared_lifecycle_conditions_keep_language_and_capability_denominators(self):
        self.lifecycle_inventory()
        for name, case in self.cases.items():
            if case['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
                (self.root / '.cache/benchmark-capabilities/task-index' / (name + '.json')).unlink()
        value = self.collect()
        self.assertEqual(value['configured_cells'], 18)
        self.assertEqual(value['scheduled_cells'], 18)
        self.assertEqual(value['statuses']['preparation_pending'], 8)
        cells = [row for row in value['cells'] if 'lifecycle' in row['profile_id']]
        self.assertEqual(len(cells), 8)
        self.assertTrue(all(row['capability_ids'] == ['C5', 'C6'] for row in cells))
        self.assertEqual({row['output_language'] for row in cells}, {'c', 'cpp'})
        self.assertEqual(value['saved_observations'], 0)

    def pointer_inventory(self):
        profiles = json.loads((ROOT/'benchmarks/config/capabilities.json').read_bytes())['profiles']
        self.settings['profiles'].extend(copy.deepcopy(p) for p in profiles if p['kind']=='pointer_probe')
        self.families['families'].append({'id': 'variable_objects'})
        self.write('benchmarks/config/capabilities.json', self.settings)
        self.write(self.settings['families_file'], self.families)
        for declaration in tasks.declared():
            if declaration['task_id'] not in self.cases: self.freeze(declaration)
        pins = [{'task_id': name, **{k: case[k] for k in ('family_id','profile_id','binary_sha256','public_package_sha256','capability_ids')}}
                for name, case in self.cases.items()]
        self.write('benchmarks/config/capability-task-pins.json', {'schema_version':1,'model_api_calls':0,'tasks':pins,
            'inputs_sha256': {'benchmarks/config/capabilities.json': measurements.digest((self.root/'benchmarks/config/capabilities.json').read_bytes())}})
    def test_28_pointer_inventory_and_saved_json_scores_keep_their_lineage(self):
        self.pointer_inventory()
        self.attempt(name='variable_objects_pointer_recover')
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(row['lineage_id'], 'pointer_object_interfaces_v1')
        self.assertEqual(row['grade']['scores']['T3'], {'score':1.0,'components':{}})
        self.assertFalse(row['readability']['applicable'])
        self.assertIn('types.json', row['grade']['artifacts'])
        pointers = [c for c in value['cells'] if c['profile_id'].startswith('pointer_recover')]
        self.assertEqual(len(pointers), 4)
        self.assertTrue(all(c['capability_ids']==['T3'] and c['lineage_id']=='pointer_object_interfaces_v1' for c in pointers))

    def test_29_variant_inventory_and_saved_scores_use_scoped_controls(self):
        profiles = json.loads((ROOT / 'benchmarks/config/capabilities.json').read_bytes())['profiles']
        self.settings['profiles'].extend(copy.deepcopy(p) for p in profiles if p['kind'] == 'variant_object')
        self.families['families'].append({'id': 'variable_objects'})
        self.write('benchmarks/config/capabilities.json', self.settings)
        self.write(self.settings['families_file'], self.families)
        for declaration in tasks.declared():
            if declaration['task_id'] not in self.cases: self.freeze(declaration)
        pins = [{'task_id': name, **{k: case[k] for k in
                ('family_id', 'profile_id', 'binary_sha256', 'public_package_sha256', 'capability_ids')}}
                for name, case in self.cases.items()]
        self.write('benchmarks/config/capability-task-pins.json', {'schema_version': 1, 'model_api_calls': 0,
            'tasks': pins, 'inputs_sha256': {'benchmarks/config/capabilities.json':
            measurements.digest((self.root / 'benchmarks/config/capabilities.json').read_bytes())}})
        self.attempt(name='variable_objects_variant_recover')
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(row['lineage_id'], 'variant_object_interfaces_v1')
        self.assertEqual(row['grade']['scores']['T4'], {'score': 1.0, 'components': {}})
        self.assertFalse(row['readability']['applicable'])
        self.assertIn('types.json', row['grade']['artifacts'])
        cells = [c for c in value['cells'] if c['profile_id'].startswith('variant_recover')]
        self.assertEqual(len(cells), 4)
        self.assertTrue(all(c['capability_ids'] == ['T4'] and c['lineage_id'] == 'variant_object_interfaces_v1' for c in cells))
        exported = measurements.public_projection(value)
        exported['validation'].update(passed=True, checks=28)
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(exported)
        exported['validation']['checks'] = 29
        measurements.validate_projection(exported)

    def test_30_indirect_inventory_and_saved_calls_keep_scope_and_lineage(self):
        profiles = json.loads((ROOT / 'benchmarks/config/capabilities.json').read_bytes())['profiles']
        self.settings['profiles'].extend(copy.deepcopy(p) for p in profiles if p['kind'] == 'indirect_call')
        self.families['families'].append({'id': 'callback_policy'})
        self.write('benchmarks/config/capabilities.json', self.settings)
        self.write(self.settings['families_file'], self.families)
        for declaration in tasks.declared():
            if declaration['task_id'] not in self.cases:
                self.freeze(declaration)
        pins = [{'task_id': name, **{k: case[k] for k in
                ('family_id', 'profile_id', 'binary_sha256', 'public_package_sha256', 'capability_ids')}}
                for name, case in self.cases.items()]
        self.write('benchmarks/config/capability-task-pins.json', {'schema_version': 1, 'model_api_calls': 0,
            'tasks': pins, 'inputs_sha256': {'benchmarks/config/capabilities.json':
            measurements.digest((self.root / 'benchmarks/config/capabilities.json').read_bytes())}})
        self.attempt(name='callback_policy_indirect_recover')
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(row['lineage_id'], 'indirect_call_interfaces_v1')
        self.assertEqual(row['grade']['scores']['T6'], {'score': 1.0, 'components': {}})
        self.assertFalse(row['readability']['applicable'])
        self.assertIn('calls.json', row['grade']['artifacts'])
        cells = [c for c in value['cells'] if c['profile_id'].startswith('indirect_recover')]
        self.assertEqual(len(cells), 4)
        self.assertTrue(all(c['capability_ids'] == ['T6'] and c['lineage_id'] == 'indirect_call_interfaces_v1' for c in cells))
        exported = measurements.public_projection(value)
        exported['validation'].update(passed=True, checks=29)
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(exported)
        exported['validation']['checks'] = 30
        measurements.validate_projection(exported)

    def advanced_inventory(self, kind, family):
        profiles = json.loads((ROOT / 'benchmarks/config/capabilities.json').read_bytes())['profiles']
        self.settings['profiles'].extend(copy.deepcopy(p) for p in profiles if p['kind'] == kind)
        self.families['families'].append({'id': family})
        self.write('benchmarks/config/capabilities.json', self.settings)
        self.write(self.settings['families_file'], self.families)
        for declaration in tasks.declared():
            if declaration['task_id'] not in self.cases: self.freeze(declaration)
        pins = [{'task_id': name, **{k: case[k] for k in
                ('family_id', 'profile_id', 'binary_sha256', 'public_package_sha256', 'capability_ids')}}
                for name, case in self.cases.items()]
        self.write('benchmarks/config/capability-task-pins.json', {'schema_version': 1, 'model_api_calls': 0,
            'tasks': pins, 'inputs_sha256': {'benchmarks/config/capabilities.json':
            measurements.digest((self.root / 'benchmarks/config/capabilities.json').read_bytes())}})

    def test_31_optimized_instances_keep_f1_components_and_json_readability_scope(self):
        self.advanced_inventory('optimized_localization', 'dispatch_registry')
        self.attempt(name='dispatch_registry_optimized_recover')
        before = self.hashes()
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(row['lineage_id'], 'optimized_mix_locations_v1')
        self.assertEqual(row['grade']['scores']['L6'], {'score': 1.0, 'components': {'precision': 1.0, 'coverage': 1.0}})
        self.assertEqual(row['grade']['return_provenance'], 'structured_artifact')
        self.assertFalse(row['readability']['applicable'])
        self.assertIn('instances.json', row['grade']['artifacts'])
        exported = measurements.public_projection(value)
        exported['validation'].update(passed=True, checks=30)
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(exported)
        exported['validation']['checks'] = 31
        measurements.validate_projection(exported)
        self.assertEqual(before, self.hashes())

    def test_32_native_inventory_preserves_source_languages_and_real_return_scope(self):
        self.advanced_inventory('native_abi', 'variable_objects')
        for name in ('variable_objects_rust_native_recover', 'variable_objects_go_internal_recover'):
            self.attempt(name=name)
        before = self.hashes()
        value = self.collect()
        self.assertEqual({row['source_language'] for row in value['history']}, {'rust', 'go'})
        for row in value['history']:
            self.assertEqual(row['lineage_id'], 'native_aggregate_interfaces_v1')
            self.assertEqual(row['grade']['scores']['T7'], {'score': 1.0, 'components': {}})
            self.assertEqual(row['grade']['return_provenance'], 'host_native_abi_return_v1')
            self.assertFalse(row['readability']['applicable'])
            self.assertIn('native-abi.json', row['grade']['artifacts'])
        exported = measurements.public_projection(value)
        exported['validation'].update(passed=True, checks=31)
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(exported)
        exported['validation']['checks'] = 32
        measurements.validate_projection(exported)
        self.assertEqual(before, self.hashes())

    def test_27_budget_exhaustion_stays_ungraded_and_keeps_observed_spending(self):
        directory, _ = self.attempt()
        self.write(directory + '/budget-exhausted.json', {'reason': 'model_turn_limit_reached'})
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(row['status'], 'budget_exhausted')
        self.assertIsNone(row['grade'])
        self.assertTrue(row['known_tokens'])
        self.assertEqual(value['statuses'].get('environment_failure', 0), 0)
        self.assertEqual(value['statuses']['budget_exhausted'], 1)

    def observed_budget_fixture(self, *, exceeded=True):
        from check_report_budget import completed_fixture
        directory, task = self.attempt()
        frozen, certificate = completed_fixture(exceeded=exceeded)
        proof = {'configuration': {'harness': {'request_contract': frozen['request_contract']}}}
        action = action_cache.Action('inference', {'configuration/effective.json':
            action_cache.canonical({'attestation': proof})})
        for key, raw in action.blobs.items():
            self.write('.cache/benchmark-actions/v3/cas/' + key, raw)
        task.update(request_contract=frozen['request_contract'], runtime_attestation=proof,
                    inference_identity={'attestation': proof}, action_descriptor=action.descriptor, fingerprint=action.key)
        self.write(directory + '/task.json', task)
        self.write(directory + '/request-budget.json', certificate)
        return directory, task, certificate

    def test_completed_over_budget_observation_keeps_grade_and_spending(self):
        directory, task, _ = self.observed_budget_fixture()
        self.write(directory + '/budget-exhausted.json', {'reason': 'task_observed_token_limit_reached'})
        before = self.hashes()
        row = measurements.observe(measurements.Reader(self.root, Path.read_bytes), directory, task,
                                   self.models, self.settings, True)
        self.assertEqual(row['status'], 'completed_over_budget')
        self.assertEqual(row['budget_status'], 'completed_over_budget')
        self.assertTrue(row['budget_outcome']['queue_stop_required'])
        self.assertEqual(row['grade']['passed'], 1)
        self.assertTrue(row['known_tokens'])
        self.assertIsNotNone(row['cost']['low_usd'])
        from public_report import TYPED_OBSERVATION, validate_value
        value = measurements.empty(); value['history'] = [row]
        exported = measurements.public_projection(value)['history'][0]
        validate_value(exported, TYPED_OBSERVATION)
        self.assertEqual(exported['budget_outcome']['status'], 'completed_over_budget')
        self.assertEqual(exported['grade']['passed'], 1)
        self.assertEqual(before, self.hashes())

    def test_partial_over_budget_observation_cannot_gain_grade(self):
        directory, task, certificate = self.observed_budget_fixture()
        certificate['final_response_complete'] = False
        self.write(directory + '/request-budget.json', certificate)
        row = measurements.observe(measurements.Reader(self.root, Path.read_bytes), directory, task,
                                   self.models, self.settings, True)
        self.assertEqual(row['status'], 'budget_exhausted')
        self.assertEqual(row['budget_status'], 'unverified')
        self.assertIsNone(row['grade'])

    def test_exact_threshold_observation_keeps_grade_but_holds_queue(self):
        directory, task, _ = self.observed_budget_fixture(exceeded=False)
        row = measurements.observe(measurements.Reader(self.root, Path.read_bytes), directory, task,
                                   self.models, self.settings, True)
        self.assertEqual(row['status'], 'completed_queue_held')
        self.assertEqual(row['budget_status'], 'wire_enforced')
        self.assertFalse(row['budget_outcome']['budget_exceeded'])
        self.assertTrue(row['grade']['task_success'])

    def test_26_cached_cpp_lifecycle_observation_retains_typed_scores_and_artifact(self):
        self.lifecycle_inventory()
        self.attempt(name='callback_policy_cpp_lifecycle_recover')
        before = self.hashes()
        value = self.collect()
        row = value['history'][0]
        self.assertEqual(row['status'], 'graded')
        self.assertEqual(row['source_language'], 'cpp')
        self.assertEqual(row['output_language'], 'cpp')
        self.assertEqual(set(row['grade']['scores']), {'C5', 'C6'})
        self.assertIn('solution.cpp', row['grade']['artifacts'])
        self.assertEqual(value['saved_observations'], 1)
        self.assertEqual(before, self.hashes())

    def matched_inventory(self):
        profiles = json.loads((ROOT / 'benchmarks/config/capabilities.json').read_bytes())['profiles']
        self.settings['profiles'].extend(copy.deepcopy(row) for row in profiles if row['kind'] == 'matched_buffer')
        self.write('benchmarks/config/capabilities.json', self.settings)
        for declaration in tasks.declared():
            if declaration['task_id'] not in self.cases: self.freeze(declaration)

    def test_33_matched_inventory_keeps_compiler_conditions_and_source_languages(self):
        self.matched_inventory()
        before = self.hashes()
        value = self.collect()
        rows = [row for row in value['cells'] if row['hint_profile'] == 'known_matched_buffer_entry_v1']
        self.assertEqual(len(rows), 24)
        self.assertEqual({row['source_language'] for row in rows}, {'cpp', 'rust', 'go'})
        self.assertEqual({row['output_language'] for row in rows}, {'c', 'cpp', 'rust', 'go'})
        self.assertEqual({row['lineage_id'] for row in rows}, {'record_codec'})
        self.assertTrue(all(row['capability_ids'] == ['C1', 'C2', 'C3', 'C4'] for row in rows))
        self.assertEqual(before, self.hashes())

    def test_34_matched_native_sources_preserve_readability_and_real_host_provenance(self):
        self.matched_inventory()
        for compiler in ('rust', 'go'):
            self.attempt(name='record_codec_' + compiler + '_' + compiler + '_recover')
        value = self.collect()
        self.assertEqual({row['output_language'] for row in value['history']}, {'rust', 'go'})
        for row in value['history']:
            self.assertTrue(row['readability']['applicable'])
            self.assertEqual(row['readability']['provenance'], 'not_reviewed')
            self.assertEqual(row['grade']['return_provenance'], 'host_emulator_return_v1')
            self.assertEqual(set(row['grade']['scores']), {'C1', 'C2', 'C3', 'C4'})
            self.assertIn('solution.rs' if row['output_language'] == 'rust' else 'solution.go', row['grade']['artifacts'])
        exported = measurements.public_projection(value)
        exported['validation'].update(passed=True, checks=32)
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(exported)
        exported['validation']['checks'] = 35
        measurements.validate_projection(exported)

    def test_35_unprepared_matched_conditions_keep_their_source_language(self):
        self.matched_inventory()
        for name, case in self.cases.items():
            if case['kind'] == 'matched_buffer':
                (self.root / '.cache/benchmark-capabilities/task-index' / (name + '.json')).unlink()
        value = self.collect()
        rows = [row for row in value['cells'] if row['profile_id'].startswith(('clang20_', 'rust_c_', 'rust_rust_', 'go_c_', 'go_go_'))]
        self.assertEqual(len(rows), 24)
        self.assertEqual({row['source_language'] for row in rows}, {'cpp', 'rust', 'go'})
        self.assertTrue(all(row['status'] == 'preparation_pending' for row in rows))


    def test_36_pointer_cell_conditions_keep_one_lineage_and_require_current_control_scope(self):
        self.pointer_inventory()
        for profile in ('pointer_topology_recover', 'pointer_topology_recover_counterfactual'):
            self.attempt(name='variable_objects_' + profile)
        before = self.hashes()
        value = self.collect()
        cells = [row for row in value['cells'] if row['profile_id'].startswith('pointer_')]
        self.assertEqual(len(cells), 8)
        self.assertEqual(len({row['task_id'] for row in cells}), 4)
        self.assertEqual({row['lineage_id'] for row in cells}, {'pointer_object_interfaces_v1'})
        self.assertTrue(all(row['capability_ids'] == ['T3'] for row in cells))
        self.assertEqual(len(value['history']), 2)
        self.assertTrue(all(row['grade']['scores']['T3']['score'] == 1.0 and not row['readability']['applicable']
                            for row in value['history']))
        exported = measurements.public_projection(value)
        exported['validation'].update(passed=True, checks=35)
        with self.assertRaisesRegex(ValueError, 'collection controls'):
            measurements.validate_projection(exported)
        exported['validation']['checks'] = 36
        measurements.validate_projection(exported)
        self.assertEqual(before, self.hashes())


    def test_37_premises_controls_remain_separate_from_recovery_cells_and_macros(self):
        self.pointer_inventory()
        name = 'variable_objects_pointer_recover'
        _, normal = self.attempt(name=name)
        _, control = self.attempt(name=name, access='premises_only')
        value = self.collect()
        self.assertEqual(value['saved_observations'], 2)
        self.assertEqual({row['analysis_condition'] for row in value['history']},
                         {'paged-evidence-v2', 'premises-only-v1'})
        selected = [row for row in value['cells'] if row['observation']]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['observation']['attempt_id'], normal['fingerprint'])
        self.assertNotEqual(normal['fingerprint'], control['fingerprint'])
        self.selection.update(suites=['capabilities-v1'], experiments=['capabilities-v1'],
                              cases=[name], evidence_access='premises_only')
        projected = self.collect()
        self.assertEqual(projected['configured_cells'], 2)
        self.assertEqual(projected['scheduled_cells'], 2)
        self.assertEqual({row['analysis_condition'] for row in projected['cells']}, {'premises-only-v1'})
        selected = [row for row in projected['cells'] if row['observation']]
        self.assertEqual(selected[0]['observation']['attempt_id'], control['fingerprint'])
        measurements.validate_projection(measurements.public_projection(projected))

    def test_38_control_label_without_withheld_bytes_or_identity_is_rejected(self):
        self.pointer_inventory()
        directory, task = self.attempt(name='variable_objects_pointer_recover', access='premises_only')
        target = Path(task['input_directory']) / 'target'
        target.write_bytes(b'Forbidden binary evidence')
        value = self.collect()
        self.assertEqual(value['saved_observations'], 0)
        self.assertEqual(value['invalid_attempt_records'], 1)
        target.write_bytes(b'')
        task['evidence_access'] = 'binary'
        self.write(directory + '/task.json', task)
        value = self.collect()
        self.assertEqual(value['saved_observations'], 0)
        self.assertEqual(value['invalid_attempt_records'], 1)

    def test_39_mixed_evidence_conditions_cannot_enter_one_capability_macro(self):
        self.pointer_inventory()
        name = 'variable_objects_pointer_recover'
        self.attempt(name=name)
        self.attempt(name=name, access='premises_only')
        value = self.collect()
        row = next(row for row in value['cells'] if row['observation'])
        row['observation'] = next(item for item in value['history'] if item['analysis_condition'] == 'premises-only-v1')
        with self.assertRaises(ValueError):
            measurements.validate_projection(measurements.public_projection(value))
        value = self.collect()
        value['cells'][0]['analysis_condition'] = 'premises-only-v1'
        with self.assertRaisesRegex(ValueError, 'separate capability macros'):
            measurements.macro_vectors(value['cells'], list(self.models))


    def test_40_valid_budget_stop_is_certified_without_a_correctness_score(self):
        import request_contract
        from check_requests import task as request_task, payload
        task = request_task(); gate = request_contract.Gate(task['request_contract'])
        for turn in range(gate.contract['max_requests']):
            raw = json.dumps({**payload(), 'metadata': {'control_turn': turn}}).encode()
            row, _ = gate.begin(gate.contract['path'], raw)
            gate.forward(row); gate.finish(row, status=200)
        with self.assertRaises(request_contract.Violation):
            gate.begin(gate.contract['path'], json.dumps(payload()).encode())
        certificate = gate.summary()
        self.assertEqual(certificate['status'], 'budget_exhausted')
        self.assertTrue(measurements.certified_budget(task, certificate))
        for bad in ({**certificate, 'stop_reason': 'unknown'},
                    {**certificate, 'contract_sha256': '0' * 64},
                    {**certificate, 'requests': [{**certificate['requests'][0], 'budget_violation': 'exceeded'}]}):
            self.assertFalse(measurements.certified_budget(task, bad))
        self.assertFalse(measurements.certified_budget({}, {}))

    def test_48_verified_budget_stop_does_not_mask_runtime_drift_or_invalid_proof(self):
        import request_contract
        from check_requests import task as request_task, payload
        directory, task = self.attempt()
        wire = request_task()
        wire['model'] = task['model']
        proof = wire['runtime_attestation']
        proof['provider']['model'] = task['model']
        cfg = proof['configuration']
        wire['request_contract'] = request_contract.build(proof['provider'],
            {'id': task['model'], 'reasoning': wire['reasoning']}, cfg['gateway'], cfg['harness'])
        cfg['harness']['request_contract'] = wire['request_contract']
        action = action_cache.Action('inference', {
            'configuration/effective.json': action_cache.canonical({'attestation': proof})})
        for key, raw in action.blobs.items():
            self.write('.cache/benchmark-actions/v3/cas/' + key, raw)
        task.update(wire, fingerprint=action.key, action_descriptor=action.descriptor,
                    inference_identity={'attestation': proof})
        self.write(directory + '/task.json', task)
        gate = request_contract.Gate(wire['request_contract'])
        for turn in range(gate.contract['max_requests']):
            raw = json.dumps({**payload(), 'model': task['model'], 'metadata': {'turn': turn}}).encode()
            row, _ = gate.begin(gate.contract['path'], raw)
            gate.forward(row); gate.finish(row, status=200)
        with self.assertRaises(request_contract.Violation):
            gate.begin(gate.contract['path'], json.dumps({**payload(), 'model': task['model'],
                'metadata': {'turn': gate.contract['max_requests']}}).encode())
        certificate = gate.summary()
        self.assertTrue(measurements.certified_budget(task, certificate))
        self.write(directory + '/request-budget.json', certificate)
        self.write(directory + '/budget-exhausted.json', {
            'classification': 'task_budget_exhausted', 'reason': certificate['stop_reason']})
        self.write(directory + '/harness-failure.json', {'classification': 'harness_or_provider_failure'})
        before = self.hashes()
        row = self.collect()['history'][0]
        self.assertEqual(row['status'], 'budget_exhausted')
        self.assertIsNone(row['grade'])
        self.assertEqual(self.hashes(), before)
        self.write(directory + '/harness-failure.json', {'classification': 'runtime_or_provider_drift'})
        self.assertEqual(self.collect()['history'][0]['status'], 'environment_failure')
        self.write(directory + '/harness-failure.json', {'classification': 'harness_or_provider_failure'})
        self.write(directory + '/request-budget.json', {**certificate, 'contract_sha256': '0' * 64})
        self.assertEqual(self.collect()['history'][0]['status'], 'environment_failure')

    def seeded_observation_fixture(self):
        from capabilities import analysis_context
        case = json.loads((ROOT / '.cache/benchmark-capabilities/task-index/record_codec_sysv_amd64_scalar_recover.json').read_bytes())
        public = ROOT / case['public_directory']
        raw = (public / 'target').read_bytes(); manifest_raw = (public / 'manifest.json').read_bytes()
        descriptor = analysis_context.derive(raw, json.loads(manifest_raw), 'abi_probe')
        proof = {'configuration': {'gateway': {'analysis_context': descriptor}, 'harness': {'known_entry_seeds': True}}}
        inputs = {'task/target': raw, 'task/manifest.json': manifest_raw,
                  'task/' + analysis_context.FILENAME: descriptor['seed_text'].encode(),
                  'configuration/effective.json': action_cache.canonical({'attestation': proof})}
        action = action_cache.Action('inference', inputs)
        for key, blob in action.blobs.items():
            self.write('.cache/benchmark-actions/v3/cas/' + key, blob)
        directory = '.cache/benchmark-runtime/tasks/' + action.key
        for name, blob in inputs.items():
            self.write(directory + '/' + name.removeprefix('task/'), blob)
        task = {'kind': 'abi_probe', 'fingerprint': action.key, 'analysis_context_policy': analysis_context.POLICY,
                'input_directory': str(self.root / directory), 'runtime_attestation': proof,
                'inference_identity': {'runtime': {'known_entry_seeds': True}, 'attestation': proof},
                'action_descriptor': action.descriptor}
        return task, descriptor

    def test_41_seeded_observation_requires_its_public_context_and_merkle_leaf(self):
        from capabilities import analysis_context
        task, descriptor = self.seeded_observation_fixture()
        reader = measurements.Reader(self.root, Path.read_bytes)
        self.assertEqual(measurements.observed_analysis_context(reader, task, 'binary'), analysis_context.POLICY)
        seed = measurements.digest(descriptor['seed_text'].encode())
        (self.root / '.cache/benchmark-actions/v3/cas' / seed).unlink()
        with self.assertRaises((ValueError, OSError)):
            measurements.observed_analysis_context(reader, task, 'binary')

    def test_42_analysis_labels_and_seeded_inputs_cannot_be_forged(self):
        task, descriptor = self.seeded_observation_fixture()
        reader = measurements.Reader(self.root, Path.read_bytes)
        altered = copy.deepcopy(task); altered['runtime_attestation']['configuration']['harness']['known_entry_seeds'] = False
        with self.assertRaises(ValueError): measurements.observed_analysis_context(reader, altered, 'binary')
        altered = copy.deepcopy(task); altered.pop('analysis_context_policy')
        with self.assertRaises(ValueError): measurements.observed_analysis_context(reader, altered, 'binary')
        path = Path(task['input_directory']) / 'analysis.seeds'; path.write_bytes(b'0x1000000\tfunction\n')
        with self.assertRaises(ValueError): measurements.observed_analysis_context(reader, task, 'binary')

    def test_43_current_conditions_keep_localization_unseeded(self):
        from capabilities import analysis_context
        self.settings['runtime'] = {**self.settings['runtime'], 'known_entry_seeds': True}
        self.write('benchmarks/config/capabilities.json', self.settings)
        value = self.collect()
        known = [row for row in value['cells'] if row['output_language'] == 'c']
        local = [row for row in value['cells'] if row['output_language'] is None]
        self.assertTrue(known and local)
        self.assertEqual({row['analysis_condition'] for row in known}, {analysis_context.POLICY})
        self.assertEqual({row['analysis_condition'] for row in local}, {self.settings['condition']})
        self.assertEqual(value['saved_observations'], 0)

    def test_44_actual_frozen_harness_flags_are_used_instead_of_legacy_runtime_projection(self):
        from capabilities import analysis_context
        task, _ = self.seeded_observation_fixture()
        task['inference_identity']['runtime'].clear()
        task['runtime_attestation']['configuration']['harness'] = {'known_entry_seeds': True}
        reader = measurements.Reader(self.root, Path.read_bytes)
        self.assertEqual(measurements.observed_analysis_context(reader, task, 'binary'), analysis_context.POLICY)
        tampered = copy.deepcopy(task)
        tampered['runtime_attestation'] = copy.deepcopy(task['runtime_attestation'])
        tampered['runtime_attestation']['configuration']['harness']['known_entry_seeds'] = False
        with self.assertRaisesRegex(ValueError, 'frozen inference'):
            measurements.observed_analysis_context(reader, tampered, 'binary')

    def test_45_explicit_format_is_bound_to_actual_prompt_and_harness_not_a_label(self):
        from capabilities import artifact_format
        task, _ = self.seeded_observation_fixture()
        task['artifact_schema'] = {'types.json': 1}
        task['runtime_attestation']['configuration']['harness'] = {'known_entry_seeds': True, 'explicit_type_roles': True}
        old = Path(task['input_directory'])
        inputs = {'task/' + name: (old / name).read_bytes() for name in ('target', 'manifest.json', 'analysis.seeds')}
        inputs['task/prompt.txt'] = b'Public question' + artifact_format.NOTICE
        inputs['configuration/effective.json'] = action_cache.canonical({'attestation': task['runtime_attestation']})
        action = action_cache.Action('inference', inputs)
        for key, blob in action.blobs.items(): self.write('.cache/benchmark-actions/v3/cas/' + key, blob)
        directory = '.cache/benchmark-runtime/tasks/' + action.key
        for name, blob in inputs.items(): self.write(directory + '/' + name.removeprefix('task/'), blob)
        task.update(fingerprint=action.key, input_directory=str(self.root / directory),
                    action_descriptor=action.descriptor, artifact_format_policy=artifact_format.POLICY)
        reader = measurements.Reader(self.root, Path.read_bytes)
        self.assertEqual(measurements.observed_artifact_format(reader, task), artifact_format.POLICY)
        relabeled = copy.deepcopy(task); relabeled.pop('artifact_format_policy')
        relabeled['runtime_attestation']['configuration']['harness']['explicit_type_roles'] = False
        with self.assertRaisesRegex(ValueError, 'frozen inference action'):
            measurements.observed_artifact_format(reader, relabeled)
        wrong = copy.deepcopy(task); wrong.pop('artifact_format_policy')
        with self.assertRaises(ValueError): measurements.observed_artifact_format(reader, wrong)
        (self.root / directory / 'prompt.txt').write_bytes(b'Public question')
        with self.assertRaises(ValueError): measurements.observed_artifact_format(reader, task)

    def test_46_format_condition_keeps_other_profiles_and_old_observations_separate(self):
        from capabilities import artifact_format
        self.settings['runtime'] = {**self.settings['runtime'], 'explicit_type_roles': True}
        self.settings['profiles'].append({'id': 'scalar_recover', 'kind': 'abi_probe', 'enabled': True,
            'output_language': None, 'artifact_schema': {'types.json': 1},
            'families': ['record_codec'], 'conventions': ['sysv_amd64']})
        self.write('benchmarks/config/capabilities.json', self.settings)
        value = self.collect()
        typed = [row for row in value['cells'] if row['profile_id'] == 'scalar_recover']
        other = [row for row in value['cells'] if row['profile_id'] != 'scalar_recover']
        self.assertTrue(typed and other)
        self.assertEqual({row['analysis_condition'] for row in typed}, {self.settings['condition'] + artifact_format.SUFFIX})
        self.assertEqual({row['analysis_condition'] for row in other}, {self.settings['condition']})
        self.assertEqual(value['saved_observations'], 0)

    def test_47_legacy_invalid_artifact_tag_is_not_return_execution_evidence(self):
        directory, task = self.attempt()
        relative = self.grade(directory, task, passed=0)
        path = self.root / directory / relative
        grade = json.loads(path.read_bytes())
        grade.update(artifact_valid=False, status='invalid_artifact', compiled=False, checks=[])
        self.write(directory + '/' + relative, grade)
        digest = measurements.digest(path.read_bytes())
        manifest_path = str(Path(relative).parent / 'manifest.json')
        manifest = json.loads((self.root / directory / manifest_path).read_bytes())
        manifest['files_sha256']['grade.json'] = digest
        self.write(directory + '/' + manifest_path, manifest)
        selected = json.loads((self.root / directory / 'selected-grade.json').read_bytes())
        selected['sha256'] = digest; self.write(directory + '/selected-grade.json', selected)
        before = self.hashes()
        value = self.collect()
        observed = value['history'][0]['grade']
        self.assertEqual((observed['passed'], observed['total']), (0, 1))
        self.assertEqual(observed['return_provenance'], 'not_executed')
        self.assertEqual(json.loads(path.read_bytes())['return_provenance'], 'host_emulator_return_v1')
        self.assertEqual(self.hashes(), before)


if __name__ == '__main__':
    inputs = measurements.fingerprints()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MeasurementControls))
    if measurements.fingerprints() != inputs:
        raise RuntimeError('Typed report sources changed during validation')
    receipt = {'schema_version': 1, 'passed': result.wasSuccessful(), 'checks': result.testsRun,
               'failed': len(result.failures) + len(result.errors), 'model_api_calls': 0,
               'provider_launches': 0, 'inputs_sha256': inputs}
    if measurements.RECEIPT.is_symlink():
        raise ValueError('Typed report receipt is a symlink')
    with tempfile.NamedTemporaryFile(dir=measurements.RECEIPT.parent, delete=False) as temporary:
        temporary.write(action_cache.canonical(receipt))
    Path(temporary.name).replace(measurements.RECEIPT)
    raise SystemExit(0 if result.wasSuccessful() else 1)
