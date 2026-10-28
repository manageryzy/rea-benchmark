#!/usr/bin/python3
"""Offline controls for immutable schedules, complete bindings and dispatch order."""
from concurrent.futures import Future
from contextlib import ExitStack
import copy
import errno
import hashlib
import json
from pathlib import Path
import shutil
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

import action_cache
import cache
import environment_profile
import records
import run
from capabilities import config, registration, registration_report, tasks, store
from capabilities.build_metadata import public_projection as public_build
from capabilities.measurements import Reader
from capabilities.coordinator import _namespace

ROOT = action_cache.ROOT


class ImmediateExecutor:
    """Exercise the real batch queues deterministically with local callbacks."""
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        return future


class RegistrationControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rea-registration-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = registration.Registry(self.root)
        self.settings, self.families = config.load()
        self.settings = {**self.settings, 'inference_enabled': True}
        self.models = {name: {'id': name, 'label': name, 'harness': 'deepseek-harness', 'reasoning': 'high'}
                       for name in ('registration-a', 'registration-b')}
        self.selection = {'models': 'all', 'suites': 'all', 'experiments': 'primary', 'cases': 'all',
            'condition': 'active', 'capabilities': True, 'execution': {'max_parallel_models': 2, 'report_every': 0}}
        self.case = tasks.frozen('record_codec_c_recover')
        self.inputs = {name: (ROOT / self.case['public_directory'] / name).read_bytes()
                       for name in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
        self.score = registration.scoring_state({**self.case, 'track': 'capability', 'artifact_contract_version': 1})
        self.real_scoring_state = registration.scoring_state
        self.events = []
        self.addCleanup(patch.stopall)
        patch.object(config, 'load', return_value=(self.settings, self.families)).start()
        patch.object(records, 'models', return_value=self.models).start()
        patch.object(registration, 'scoring_state', side_effect=lambda task: copy.deepcopy(self.score)).start()
        patch.object(registration.runtime_attestation, 'provider', side_effect=self.provider).start()
        patch.object(registration.runtime_attestation, 'capture', side_effect=self.capture).start()
        # Registration drift controls deliberately use strict runtime semantics;
        # startup-once lifetime/reuse is covered by check_runtime_reuse.
        policy = registration.runtime_attestation.config()
        patch.object(registration.runtime_attestation, 'config',
                     return_value={**policy, 'verification_mode': 'strict_per_task'}).start()
        patch.object(socket.socket, 'connect', side_effect=AssertionError('Provider network')).start()
        patch.object(run, 'launch_model', side_effect=AssertionError('Model dispatch outside explicit local callback')).start()
        self.scope = registration.validation_controls()
        self.scope.__enter__()
        self.addCleanup(lambda: self.scope.__exit__(None, None, None))
        self.selected = [self.entry(model) for model in self.models.values()]

    def provider(self, model):
        self.events.append('provider:' + model['id'])
        return {'provider': 'offline-control', 'protocol': 'openai-responses', 'model': model['id'],
                'credential_source': 'offline-control'}

    def capture(self, harness, **kwargs):
        self.events.append('capture:' + harness)
        manifest = {'harness': harness, 'environment': {}, 'components': {}, 'scope': 'offline-control'}
        return {'identity': registration.digest(action_cache.canonical(manifest)), 'manifest': manifest}

    def entry(self, model):
        access = self.selection.get('evidence_access', 'binary')
        namespace = _namespace(self.settings, model, access)
        return {'suite': 'capabilities-v1', 'experiment': 'capabilities-v1', 'case': self.case['id'],
            'model': model['id'], 'language': self.case['source_language'], 'condition': self.settings['condition'],
            'configuration_sha256': namespace, 'preparation_status': 'ready', 'status': 'not_started',
            'capability_case': self.case,
            'directory': 'benchmarks/runs/capabilities-v1/' + model['id'] + '/' + self.case['id'] + '/' + self.case['public_package_sha256'] + '/' + namespace,
            **({'evidence_access': access} if access != 'binary' else {})}

    def prepare(self, entry, model, materialize=True, *, attestor=None, **kwargs):
        from capabilities import evidence_access
        access = entry.get('evidence_access', 'binary')
        inputs = self.inputs
        if access != 'binary':
            from capabilities.coordinator import model_prompt
            inputs = evidence_access.project({**inputs, 'prompt.txt': model_prompt(inputs['prompt.txt'], inputs['manifest.json'])}, access)
        if attestor is None:
            attestor = self.registry.attestor(entry['measurement_registration'])
        gateway = {'max_tool_calls': self.settings['limits']['max_tool_calls'], 'analysis_tools_file': '@task/tools.json',
                   'tool_timeout_ms': self.settings['runtime']['tool_timeout_ms'], 'data_limits': {}}
        harness = {**self.settings['runtime'], **self.settings['limits'], 'analysis_tools_file': '@task/tools.json',
                   'allowed_tool_names': []}
        proof = attestor(model, configuration={'gateway': gateway, 'harness': harness})
        case = {**self.case, **self.settings['limits'], 'language': self.case['source_language']}
        profile = environment_profile.current(model['harness'], entry['condition'])
        identity = cache.inference_identity(entry, model, case, profile, inputs['prompt.txt'], inputs['target'],
            inputs['tools.json'], inputs['manifest.json'], self.settings['runtime'])
        identity['attestation'] = proof
        action = action_cache.inference(identity, inputs, trial=cache.inference_trial(entry))
        from capabilities import attempts
        directory = attempts.route(entry, action.key, root=self.root)
        workspace = self.root / 'inputs' / model['id'] / action.key
        workspace.mkdir(parents=True, exist_ok=True)
        for name, raw in inputs.items():
            (workspace / name).write_bytes(raw)
        return {**case, 'track': 'capability', 'artifact_contract_version': 1, 'language': self.case['output_language'],
            'model': model['id'], 'label': model['label'], 'harness': model['harness'], 'reasoning': model['reasoning'],
            'condition': entry['condition'], 'directory': directory, 'input_directory': str(workspace),
            **({'inference_trial': entry['inference_trial']} if 'inference_trial' in entry else {}),
            'fingerprint': action.key, 'action_descriptor': action.descriptor, 'inference_identity': identity,
            'runtime_attestation': proof, 'request_contract': proof['configuration']['harness']['request_contract'],
            'environment_profile': profile, 'prompt_sha256': registration.digest(inputs['prompt.txt']),
            **({'evidence_access': access} if access != 'binary' else {})}

    def complete(self):
        reference = self.registry.begin(self.selected, self.selection)
        selected, reference = self.registry.complete(reference, self.selected, self.selection, self.prepare)
        return selected, reference

    def task(self, selected):
        entry = selected[0]
        task = self.prepare(entry, self.models[entry['model']])
        return {**task, 'measurement_registration': entry['measurement_registration']}

    def test_01_schedule_precedes_binding_and_preserves_all_cells(self):
        reference = self.registry.begin(self.selected, self.selection)
        value, _ = self.registry.load(reference['registration_id'])
        self.assertEqual(value['scheduled_cells'], 2)
        self.assertEqual(value['prospective_cells'], 2)
        self.assertIsNone(reference['binding_id'])
        self.assertEqual(self.events, [])

    def test_02_complete_binding_resolves_every_model_before_dispatch(self):
        selected, reference = self.complete()
        value, _ = self.registry.load(reference['registration_id'], reference['binding_id'])
        self.assertEqual(len(value['cells']), 2)
        self.assertEqual(set(self.events[:2]), {'provider:registration-a', 'provider:registration-b'})
        self.assertEqual(self.events[2:], ['capture:deepseek-harness'])
        self.assertTrue(all(row['attestation']['configuration']['harness']['request_contract']['mode'] == 'certified' for row in value['cells']))
        self.assertTrue(all(row['measurement_registration']['binding_id'] == reference['binding_id'] for row in selected))

    def test_03_identical_registration_reuses_ids_and_first_creation(self):
        first, reference = self.complete()
        original = self.registry.load(reference['registration_id'])[1]
        second, repeated = self.complete()
        self.assertEqual(reference, repeated)
        self.assertEqual(first, second)
        self.assertEqual(original, self.registry.load(repeated['registration_id'])[1])

    def test_04_offline_or_only_prior_results_never_resolve_credentials(self):
        self.assertIsNone(self.registry.begin(self.selected, self.selection, offline=True))
        saved = [{**row, 'status': 'cached'} for row in self.selected]
        self.assertIsNone(self.registry.begin(saved, self.selection))
        self.assertEqual(self.events, [])

    def test_05_disabled_condition_and_duplicate_cell_fail_before_resolution(self):
        self.settings['inference_enabled'] = False
        with self.assertRaises(RuntimeError):
            self.registry.begin(self.selected, self.selection)
        self.settings['inference_enabled'] = True
        with self.assertRaises(ValueError):
            self.registry.begin(self.selected + self.selected[:1], self.selection)
        self.assertEqual(self.events, [])

    def test_06_selection_and_definition_drift_refuse_binding(self):
        reference = self.registry.begin(self.selected, self.selection)
        self.selection['cases'] = [self.case['id']]
        with self.assertRaises(RuntimeError):
            self.registry.complete(reference, self.selected, self.selection, self.prepare)
        self.assertEqual(self.events, [])

    def test_07_model_settings_drift_refuses_binding(self):
        reference = self.registry.begin(self.selected, self.selection)
        self.models['registration-a']['reasoning'] = 'low'
        with self.assertRaises((ValueError, RuntimeError)):
            self.registry.complete(reference, self.selected, self.selection, self.prepare)
        self.assertEqual(self.events, [])

    def test_08_unsupported_budget_fails_before_runtime_inventory(self):
        reference = self.registry.begin(self.selected, self.selection)
        with patch.object(registration.runtime_attestation, 'provider', return_value={'protocol': 'unsupported-codex-oauth'}):
            with self.assertRaises(ValueError):
                self.registry.complete(reference, self.selected, self.selection, self.prepare)
        self.assertEqual(self.events, [])

    def test_09_binding_cannot_be_published_with_missing_participant(self):
        reference = self.registry.begin(self.selected, self.selection)
        def missing(entry, model, **kwargs):
            if model['id'] == 'registration-b':
                raise ValueError('Offline missing participant')
            return self.prepare(entry, model, **kwargs)
        with self.assertRaises(ValueError):
            self.registry.complete(reference, self.selected, self.selection, missing)
        base = self.registry.path(self.registry.directory(reference['registration_id']))
        self.assertFalse((base / 'bindings').exists())

    def test_10_effective_settings_drift_refuses_task_materialization(self):
        selected, _ = self.complete()
        self.settings['limits']['max_output_tokens'] -= 1
        with self.assertRaises(ValueError):
            self.task(selected)

    def test_11_private_scoring_drift_refuses_dispatch(self):
        selected, _ = self.complete()
        task = self.task(selected)
        self.score['grading_spec_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.registry.claim(task)

    def test_12_task_signature_drift_refuses_dispatch(self):
        selected, _ = self.complete()
        task = self.task(selected)
        task['output_language'] = 'cpp'
        with self.assertRaises(ValueError):
            self.registry.claim(task)

    def test_13_dispatch_intent_is_single_use_and_precedes_callback(self):
        selected, _ = self.complete()
        task = self.task(selected)
        receipt = self.registry.claim(task)
        self.assertEqual(receipt['phase'], 'before_provider_dispatch')
        with self.assertRaises(FileExistsError):
            self.registry.claim(task)

    def test_14_corrupt_schedule_or_binding_fails_closed(self):
        selected, reference = self.complete()
        path = self.registry.path(self.registry.directory(reference['registration_id'], reference['binding_id']) + '/record.json')
        path.write_bytes(b'{}')
        with self.assertRaises(ValueError):
            self.registry.attestor(selected[0]['measurement_registration'])

    def test_15_missing_origin_does_not_gain_prerun_provenance(self):
        _, reference = self.complete()
        self.registry.path(self.registry.directory(reference['registration_id']) + '/created.json').unlink()
        with self.assertRaises(OSError):
            self.registry.load(reference['registration_id'])

    def test_16_offline_control_records_cannot_authorize_production(self):
        selected, _ = self.complete()
        task = self.task(selected)
        token = registration._controls.set(False)
        try:
            with self.assertRaises(ValueError):
                self.registry.claim(task)
        finally:
            registration._controls.reset(token)

    def test_17_stage_identity_excludes_labels_prices_and_report_frequency(self):
        _, first = self.complete()
        for model in self.models.values():
            model['label'] = 'Display only'
            model['pricing_file'] = 'display-only.json'
        self.selection['execution']['report_every'] = 99
        _, second = self.complete()
        self.assertEqual(first, second)

    def test_18_registration_provenance_does_not_change_inference_identity(self):
        selected, _ = self.complete()
        task = self.task(selected)
        identity = copy.deepcopy(task['inference_identity'])
        before = action_cache.inference(identity, self.inputs, trial=task['directory']).key
        task['measurement_registration'] = {'registration_id': 'f' * 64}
        after = action_cache.inference(task['inference_identity'], self.inputs, trial=task['directory']).key
        self.assertEqual(before, after)

    def test_19_symlinked_registration_storage_cannot_be_used(self):
        reference = self.registry.begin(self.selected, self.selection)
        path = self.registry.path(self.registry.directory(reference['registration_id']) + '/record.json')
        saved = path.read_bytes()
        path.unlink()
        other = self.root / 'other.json'
        other.write_bytes(saved)
        path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.registry.load(reference['registration_id'])

    def test_20_prior_observation_in_mixed_batch_is_not_preregistered(self):
        self.selected[1] = {**self.selected[1], 'status': 'cached'}
        selected, reference = self.complete()
        self.assertNotIn('measurement_registration', selected[1])
        value, _ = self.registry.load(reference['registration_id'], reference['binding_id'])
        self.assertEqual(sum(row['prospective'] for row in value['cells']), 1)

    def test_21_scoring_drift_during_binding_prevents_ready_publication(self):
        reference = self.registry.begin(self.selected, self.selection)
        calls = []
        def changing(entry, model, **kwargs):
            calls.append(model['id'])
            if len(calls) == 2:
                self.score['contract_sha256'] = 'f' * 64
            return self.prepare(entry, model, **kwargs)
        with self.assertRaises(RuntimeError):
            self.registry.complete(reference, self.selected, self.selection, changing)

    def test_22_missing_registration_is_refused_before_provider_preparation(self):
        with patch.object(run, 'prepare_task', side_effect=AssertionError('Provider preparation')):
            with self.assertRaisesRegex(RuntimeError, 'pre-run measurement registration'):
                run.execute(self.selected[0], self.models['registration-a'])

    def test_23_real_batch_freezes_every_binding_before_local_dispatch(self):
        self.batch()

    def batch(self, stop=False):
        state = {'experiments_stopped': True}
        source_read = records.read
        def read(path, default=None):
            if Path(path) == self.root / 'benchmarks/config/execution.json':
                return dict(state)
            return source_read(path, default)
        def launch(directory, task, model, cancel_event=None):
            reference = task['measurement_registration']
            ready, _ = self.registry.load(reference['registration_id'], reference['binding_id'])
            self.assertEqual(len(ready['cells']), 2)
            claim = self.registry.read(self.registry.directory(reference['registration_id'], reference['binding_id']) + '/dispatches/' + task['fingerprint'] + '.json')
            self.assertEqual(claim['task_signature'], registration.task_signature(task))
            run.save(directory / 'answer.md', {'offline': 'local orchestration fixture'})
            run.save(directory / 'result.json', {'fixture': True})
        finalize_calls = []
        complete = self.registry.complete
        def bind(*args, **kwargs):
            result = complete(*args, **kwargs)
            if stop:
                state['experiments_stopped'] = True
            return result
        with patch.object(run, 'ROOT', self.root), patch.object(run, 'BENCH', self.root / 'benchmarks'), \
             patch.object(records, 'ROOT', self.root), patch.object(records, 'read', side_effect=read), \
             patch.object(run, 'set_execution_stopped', side_effect=lambda value, **kwargs: state.update(experiments_stopped=value)), \
             patch.object(registration, 'Registry', return_value=self.registry), \
             patch.object(self.registry, 'complete', side_effect=bind), \
             patch.object(run, 'prepare_task', side_effect=self.prepare), \
             patch.object(run, 'ThreadPoolExecutor', ImmediateExecutor), \
             patch('capabilities.coordinator.prepare_selected', side_effect=lambda rows: rows), \
             patch.object(run, 'launch_model', side_effect=launch) as provider, \
             patch.object(run, 'grade_saved', return_value={'passed': 1, 'total': 1}), \
             patch.object(run, 'account_saved', return_value='offline-accounting'), \
             patch.object(cache, 'grading_key', return_value='offline-grade'), \
             patch.object(cache, 'register_inference'), patch.object(action_cache.Cache, 'lookup', return_value=None):
            if stop:
                with self.assertRaises(InterruptedError):
                    run.run_batch(self.selected, self.selection, finalize=lambda: finalize_calls.append(True))
                self.assertEqual(provider.call_count, 0)
            else:
                receipt = run.run_batch(self.selected, self.selection, finalize=lambda: finalize_calls.append(True))
                self.assertEqual(provider.call_count, 2)
                self.assertTrue(receipt['measurement_registration']['binding_id'])
        self.assertTrue(state['experiments_stopped'])
        self.assertEqual(finalize_calls, [] if stop else [True])
        self.assertFalse((self.root / '.cache/benchmark-runtime/live-batch.lock').exists())

    def test_24_stop_after_binding_prevents_dispatch_and_cleans_batch_lock(self):
        self.batch(stop=True)

    def test_25_public_build_conditions_do_not_disclose_private_commands_or_macros(self):
        value = self.registry.builds.target(self.case)
        manifest = json.loads(self.inputs['manifest.json'])
        first = public_build(value, manifest)
        changed = copy.deepcopy(value)
        changed['compiler_commands'][0] += ['-DABSENT_TARGET=1', '-DORIGINAL_MS_ABI=1', '-Wl,-e,private_business_root']
        self.assertEqual(first, public_build(changed, manifest))
        self.assertNotIn('compiler_commands_sha256', first)
        self.assertEqual(first['optimization'], '-O2')
        hidden = public_build(value, {**manifest, 'condition': 'abi_recover'})
        self.assertEqual(hidden['abi'], 'measured_not_disclosed')
        for name in ('variable_objects_pointer_recover', 'variable_objects_pointer_recover_counterfactual',
                     'variable_objects_variant_recover', 'variable_objects_variant_recover_counterfactual',
                     'callback_policy_indirect_recover', 'callback_policy_indirect_recover_counterfactual'):
            with self.subTest(object_task=name):
                case = tasks.frozen(name)
                metadata = self.registry.builds.target(case)
                public = json.loads((ROOT / case['public_directory'] / 'manifest.json').read_bytes())
                projection = public_build(metadata, public)
                self.assertEqual(projection['binary_sha256'], case['binary_sha256'])
                self.assertEqual(projection['build_id'], case['build_id'])
                self.assertEqual(projection['compiler_family'], 'gcc')
                self.assertEqual(projection['optimization'], '-O2')
                self.assertEqual(projection['strip_mode'], 'strip_all_verified')
                self.assertEqual(projection['abi'], 'sysv_amd64')
                self.assertNotIn('compiler_commands', projection)
                self.assertFalse(any('BASELINE' in f or 'REORDERED' in f for f in projection['flag_summary']))

    def test_26_old_observation_does_not_gain_registration(self):
        selected, _ = self.complete()
        task = self.task(selected)
        task.pop('measurement_registration')
        value = registration_report.observation(Reader(self.root, Path.read_bytes), self.registry, task['directory'], task)
        self.assertEqual(value['status'], 'historical_unregistered')
        self.assertIsNone(value['registration_id'])

    def test_27_registration_reports_distinguish_original_grading_and_later_revisions(self):
        selected, _ = self.complete()
        task = self.task(selected)
        self.registry.claim(task)
        reader = Reader(self.root, Path.read_bytes)
        directory = task['directory']
        source_load = self.registry.load
        def origin(*args):
            value, created = source_load(*args)
            return value, {**created, 'offline_control': False}
        def write(name, value):
            path = self.root / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(action_cache.canonical(value))
        _, inputs, environment = store.fixed_inputs(task)
        action = action_cache.Action('capability_submission_v1', inputs,
            arguments=[store.VERSION, task['kind']], platform=environment)
        for key, raw in action.blobs.items():
            path = self.root / '.cache/benchmark-actions/v3/cas' / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        relative = 'grades/' + store.VERSION + '/' + action.key + '/grade.json'
        grade = {'grading_id': action.key, 'passed': 0, 'total': 1}
        write(relative, grade)
        write('selected-grade.json', {'path': relative, 'grading_id': action.key, 'sha256': registration.digest(action_cache.canonical(grade))})
        manifest = {'grading_id': action.key, 'action': action.descriptor, 'grading_spec_sha256': task['grading_spec_sha256']}
        write(str(Path(relative).parent) + '/manifest.json', manifest)
        with patch.object(self.registry, 'load', side_effect=origin):
            value = registration_report.observation(reader, self.registry, directory, task)
            self.assertEqual(value['status'], 'registered_before_dispatch')
            write(str(Path(relative).parent) + '/manifest.json', {**manifest, 'grading_spec_sha256': 'f'*64})
            value = registration_report.observation(reader, self.registry, directory, task)
            self.assertEqual(value['status'], 'registered_inference_regraded')

    def test_28_registration_controls_and_corrupt_intents_are_not_measurements(self):
        selected, _ = self.complete()
        task = self.task(selected)
        self.registry.claim(task)
        reader = Reader(self.root, Path.read_bytes)
        value = registration_report.observation(reader, self.registry, task['directory'], task)
        self.assertEqual(value['status'], 'offline_control_excluded')
        source_load = self.registry.load
        def origin(*args):
            document, created = source_load(*args)
            return document, {**created, 'offline_control': False}
        reference = task['measurement_registration']
        path = self.registry.path(self.registry.directory(reference['registration_id'], reference['binding_id']) + '/dispatches/' + task['fingerprint'] + '.json')
        path.write_bytes(action_cache.canonical({'fingerprint': '0'*64}))
        with patch.object(self.registry, 'load', side_effect=origin):
            value = registration_report.observation(reader, self.registry, task['directory'], task)
        self.assertEqual(value['status'], 'unverified')


    def test_29_advanced_original_metadata_comes_from_saved_actions(self):
        from capabilities.build_metadata import SavedBuilds
        saved = SavedBuilds()
        for name, family, optimization in (
                ('dispatch_registry_optimized_recover', 'gcc', '-O3'),
                ('dispatch_registry_lto_recover', 'gcc', '-O3'),
                ('variable_objects_rust_native_recover', 'rustc', '-O2'),
                ('variable_objects_go_internal_recover', 'gc', 'compiler_default')):
            case = tasks.frozen(name)
            manifest = json.loads((ROOT / case['public_directory'] / 'manifest.json').read_bytes())
            value = public_build(saved.target(case), manifest)
            self.assertEqual((value['compiler_family'], value['optimization']), (family, optimization))
            self.assertEqual(value['source_language'], case['source_language'])
            self.assertEqual(value['binary_sha256'], case['binary_sha256'])
            self.assertEqual(value['build_id'], case['build_id'])
            self.assertFalse(any('/' in flag or 'original_' in flag for flag in value['flag_summary']))
            if name == 'dispatch_registry_lto_recover': self.assertIn('-flto=1', value['flag_summary'])
            if family in ('rustc', 'gc'):
                self.assertIn(value['abi'], ('rust_native', 'go_abi_internal'))
                self.assertNotIn('compiler_commands', value)

    def test_30_advanced_schedules_bind_real_scoring_and_source_language_before_dispatch(self):
        for name in ('dispatch_registry_optimized_recover', 'dispatch_registry_lto_recover',
                     'variable_objects_rust_native_recover', 'variable_objects_go_internal_recover'):
            self.case = tasks.frozen(name)
            self.inputs = {key: (ROOT / self.case['public_directory'] / key).read_bytes()
                           for key in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
            self.score = self.real_scoring_state({**self.case, 'track': 'capability', 'artifact_contract_version': 1})
            self.selected = [self.entry(model) for model in self.models.values()]
            selected, reference = self.complete()
            ready, _ = self.registry.load(reference['registration_id'], reference['binding_id'])
            self.assertEqual(len(ready['cells']), 2)
            for cell in ready['cells']:
                self.assertEqual(cell['scoring'], self.score)
                self.assertEqual(cell['build']['source_language'], self.case['source_language'])
            task = self.task(selected)
            self.assertIsNone(task['output_language'])
            self.assertEqual(task['source_language'], self.case['source_language'])
            self.assertEqual(self.registry.claim(task)['phase'], 'before_provider_dispatch')

    def test_31_matched_build_rows_use_actual_clang_rust_and_go_compilers(self):
        from capabilities.build_metadata import SavedBuilds
        saved = SavedBuilds()
        for compiler, language, family, optimization, abi in (
                ('clang20', 'cpp', 'clang', '-O2', 'linux_x86_64_sysv'),
                ('rust', 'rust', 'rustc', '-O2', 'linux_x86_64_sysv'),
                ('go', 'go', 'gc', 'compiler_default', 'go_abi_internal')):
            case = tasks.frozen('record_codec_' + compiler + '_' + language + '_recover')
            manifest = json.loads((ROOT / case['public_directory'] / 'manifest.json').read_bytes())
            value = public_build(saved.target(case), manifest)
            self.assertEqual((value['compiler_family'], value['optimization'], value['abi']), (family, optimization, abi))
            self.assertEqual(value['source_language'], case['source_language'])
            self.assertEqual(value['binary_sha256'], case['binary_sha256'])
            self.assertNotIn('compiler_commands', value)
            self.assertFalse(any('/' in flag for flag in value['flag_summary']))
            if compiler == 'clang20': self.assertIn('clang version 20.', value['compiler_version'])

    def test_32_matched_registrations_keep_original_and_output_language_separate(self):
        for compiler, output in (('clang20', 'c'), ('rust', 'c'), ('go', 'go')):
            self.case = tasks.frozen('record_codec_' + compiler + '_' + output + '_recover')
            self.inputs = {key: (ROOT / self.case['public_directory'] / key).read_bytes()
                           for key in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
            self.score = self.real_scoring_state({**self.case, 'track': 'capability', 'artifact_contract_version': 1})
            self.selected = [self.entry(model) for model in self.models.values()]
            selected, reference = self.complete()
            ready, _ = self.registry.load(reference['registration_id'], reference['binding_id'])
            for cell in ready['cells']:
                self.assertEqual(cell['build']['source_language'], self.case['source_language'])
                self.assertEqual(cell['scoring'], self.score)
            task = self.task(selected)
            self.assertEqual(task['output_language'], output)
            self.assertEqual(task['source_language'], self.case['source_language'])
            self.assertEqual(self.registry.claim(task)['phase'], 'before_provider_dispatch')


    def test_33_pointer_cell_builds_and_scoring_bind_before_dispatch(self):
        from capabilities.build_metadata import SavedBuilds
        saved = SavedBuilds()
        for profile in ('pointer_topology_recover', 'pointer_topology_recover_counterfactual'):
            self.case = tasks.frozen('variable_objects_' + profile)
            self.inputs = {name: (ROOT / self.case['public_directory'] / name).read_bytes()
                           for name in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
            metadata = public_build(saved.target(self.case), json.loads(self.inputs['manifest.json']))
            self.assertEqual((metadata['compiler_family'], metadata['optimization'], metadata['source_language']),
                             ('gcc', '-O2', 'cpp'))
            self.assertEqual(metadata['abi'], 'sysv_amd64')
            self.assertEqual(metadata['binary_sha256'], self.case['binary_sha256'])
            self.assertNotIn('compiler_commands', metadata)
            self.assertNotIn('indirect_original', json.dumps(metadata))
            self.score = self.real_scoring_state({**self.case, 'track': 'capability', 'artifact_contract_version': 1})
            self.selected = [self.entry(model) for model in self.models.values()]
            selected, reference = self.complete()
            ready, _ = self.registry.load(reference['registration_id'], reference['binding_id'])
            self.assertEqual(len(ready['cells']), 2)
            for cell in ready['cells']:
                self.assertEqual(cell['scoring'], self.score)
                self.assertEqual(cell['build']['build_id'], self.case['build_id'])
            task = self.task(selected)
            self.assertEqual(task['source_language'], 'cpp')
            self.assertIsNone(task['output_language'])
            self.assertEqual(self.registry.claim(task)['phase'], 'before_provider_dispatch')


    def premises_selection(self):
        self.case = tasks.frozen('record_codec_sysv_amd64_scalar_recover')
        self.inputs = {name: (ROOT / self.case['public_directory'] / name).read_bytes()
                       for name in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
        self.score = self.real_scoring_state({**self.case, 'track': 'capability', 'artifact_contract_version': 1})
        self.selection.update(suites=['capabilities-v1'], experiments=['capabilities-v1'],
                              cases=[self.case['id']], evidence_access='premises_only')
        self.selected = [self.entry(model) for model in self.models.values()]

    def test_34_premises_access_is_frozen_in_selection_and_task_signature(self):
        self.premises_selection()
        selected, reference = self.complete()
        schedule, _ = self.registry.load(reference['registration_id'])
        self.assertEqual(schedule['selection']['evidence_access'], 'premises_only')
        task = self.task(selected)
        self.assertEqual(task['evidence_access'], 'premises_only')
        self.assertEqual((Path(task['input_directory']) / 'target').read_bytes(), b'')
        self.assertEqual(self.registry.verify_task(task)['fingerprint'], task['fingerprint'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.registry.verify_task({**task, 'evidence_access': 'binary'})

    def test_35_mixed_or_post_schedule_access_cannot_authorize_dispatch(self):
        self.premises_selection()
        reference = self.registry.begin(self.selected, self.selection)
        self.selection['evidence_access'] = 'binary'
        with self.assertRaises(ValueError):
            self.registry.complete(reference, self.selected, self.selection, self.prepare)
        self.assertEqual(self.events, [])
        self.selection['evidence_access'] = 'premises_only'
        self.selected[0]['evidence_access'] = 'binary'
        with self.assertRaises(ValueError):
            self.registry.begin(self.selected, self.selection)
        self.assertEqual(self.events, [])

    def compact_captured_runtime(self):
        import runtime_attestation
        entries = {f'workspace/large-sdk/module-{index:05d}':
                   {'sha256': 'b' * 64, 'size_bytes': 8, 'executable': False} for index in range(5000)}
        manifest = {'schema_version': 1, 'harness': 'deepseek-harness', 'environment': {},
                    'components': {'sdk': {'sha256': registration.digest(action_cache.canonical(entries)),
                        'files': len(entries), 'bytes': 8 * len(entries), 'entries': entries}}, 'scope': 'offline-control'}
        return runtime_attestation.compact_runtime({'identity': registration.digest(action_cache.canonical(manifest)), 'manifest': manifest})

    def test_36_realistic_component_maps_do_not_expand_binding_or_saved_tasks(self):
        captured = self.compact_captured_runtime()
        with patch.object(registration.runtime_attestation, 'capture', return_value=captured):
            selected, reference = self.complete()
            ready, _ = self.registry.load(reference['registration_id'], reference['binding_id'])
            self.assertLess(len(action_cache.canonical(ready)), 100000)
            self.assertLess(len(json.dumps(self.task(selected), indent=2)), 100000)
            self.assertTrue(all('entries' not in row['attestation']['runtime']['manifest']['components']['sdk'] for row in ready['cells']))

    def test_37_missing_component_cas_prevents_complete_binding(self):
        captured = self.compact_captured_runtime()
        local = action_cache.Cache()
        digest = captured['manifest']['components']['sdk']['entries_digest']
        raw = local.get(digest)
        path = local._path('cas', digest['hash'])
        self.addCleanup(local.put, raw)
        path.unlink()
        with patch.object(registration.runtime_attestation, 'capture', return_value=captured):
            with self.assertRaises((ValueError, OSError)):
                self.complete()
        self.assertFalse(list(self.root.glob('**/dispatches/*.json')))
        self.assertFalse(list(self.root.glob('**/bindings/*/record.json')))


    def test_38_analysis_context_claim_is_part_of_the_dispatch_signature(self):
        selected, reference = self.complete()
        task = self.task(selected)
        forged = {**task, 'analysis_context_policy': 'public-given-entrypoints-v1'}
        self.assertNotEqual(registration.task_signature(task), registration.task_signature(forged))
        with self.assertRaisesRegex(ValueError, 'Registered task'):
            self.registry.claim(forged)


    def test_39_shared_admission_runs_once_per_kind_and_harness(self):
        from capabilities import coordinator, analysis_context
        reference = self.registry.begin(self.selected, self.selection)
        token = registration._controls.set(False)
        try:
            with (patch.object(coordinator, 'require_case_validation') as case_gate,
                  patch.object(analysis_context, 'require_validation') as context_gate):
                self.registry.complete(reference, self.selected, self.selection, self.prepare)
            case_gate.assert_called_once_with(self.case)
            self.assertEqual(context_gate.call_count, 2)
            self.assertTrue(all(call.kwargs == {'task_ids': {self.case['id']}}
                                for call in context_gate.call_args_list))
        finally:
            registration._controls.reset(token)

    def routed_entries(self):
        return [{**self.entry(model), 'inference_trial': self.entry(model)['directory']}
                for model in self.models.values()]

    def routed_batch(self, *, prepare=None, begin=None, production_prepare=False):
        """Real resolution, registration and queues with only local dispatch."""
        from capabilities import coordinator, analysis_context
        prepare = prepare or self.prepare
        state = {'experiments_stopped': True}
        original_read = records.read
        started = []
        def read(path, default=None):
            if Path(path) == self.root / 'benchmarks/config/execution.json':
                return dict(state)
            return original_read(path, default)
        def launch(directory, task, model, cancel_event=None):
            bound = self.registry.verify_task(task)
            reference = task['measurement_registration']
            ready, _ = self.registry.load(reference['registration_id'], reference['binding_id'])
            self.assertEqual(len(ready['cells']), len(self.models))
            claim = self.registry.read(self.registry.directory(reference['registration_id'], reference['binding_id'])
                + '/dispatches/' + task['fingerprint'] + '.json')
            self.assertEqual(claim['task_signature'], bound['task_signature'])
            started.append(copy.deepcopy(task))
            run.save(directory / 'answer.md', {'offline': 'routed dispatch control'})
            run.save(directory / 'result.json', {'fixture': True})
        with ExitStack() as stack:
            for replacement in (
                    patch.object(run, 'ROOT', self.root), patch.object(run, 'BENCH', self.root / 'benchmarks'),
                    patch.object(records, 'ROOT', ROOT if production_prepare else self.root),
                    patch.object(records, 'read', side_effect=read),
                    patch.object(run, 'set_execution_stopped', side_effect=lambda value, **kwargs: state.update(experiments_stopped=value)),
                    patch.object(registration, 'Registry', return_value=self.registry),
                    patch.object(coordinator, 'ROOT', ROOT if production_prepare else self.root),
                    patch.object(coordinator, 'prepare', side_effect=prepare),
                    patch.object(coordinator, 'require_case_validation'),
                    patch.object(coordinator.preparation, 'inspect', return_value={'status': 'ready', 'case': self.case}),
                    patch.object(analysis_context, 'require_validation'),
                    patch.object(coordinator, 'prepare_selected', side_effect=lambda rows: rows),
                    patch.object(run, 'prepare_task', side_effect=prepare),
                    patch.object(self.registry, 'begin', side_effect=begin or self.registry.begin),
                    patch.object(run, 'ThreadPoolExecutor', ImmediateExecutor),
                    patch.object(run, 'grade_saved', return_value={'passed': 1, 'total': 1}),
                    patch.object(run, 'account_saved', return_value='offline-accounting'),
                    patch.object(cache, 'grading_key', return_value='offline-grade'),
                    patch.object(cache, 'register_inference'), patch.object(action_cache.Cache, 'lookup', return_value=None)):
                stack.enter_context(replacement)
            launch_model = stack.enter_context(patch.object(run, 'launch_model', side_effect=launch))
            try:
                receipt = run.run_batch(self.routed_entries(), self.selection)
            finally:
                self.last_routed_launches = launch_model.call_count
                self.assertFalse((self.root / '.cache/benchmark-runtime/live-batch.lock').exists())
        self.assertTrue(state['experiments_stopped'])
        return receipt, started

    def test_40_same_configuration_provider_and_runtime_changes_bind_distinct_actions(self):
        first_receipt, first = self.routed_batch()
        original = {str(path.relative_to(self.root)): path.read_bytes()
                    for task in first for path in (self.root / task['directory']).rglob('*') if path.is_file()}
        original_namespaces = [row['configuration_sha256'] for row in self.routed_entries()]
        provider = self.provider
        def changed_provider(model):
            return {**provider(model), 'endpoint': 'http://offline.invalid/second-route'}
        with patch.object(registration.runtime_attestation, 'provider', side_effect=changed_provider):
            second_receipt, second = self.routed_batch()
            captured = self.capture('deepseek-harness')
            captured['manifest']['scope'] = 'offline-control-updated-runtime'
            captured['identity'] = registration.digest(action_cache.canonical(captured['manifest']))
            with patch.object(registration.runtime_attestation, 'capture', return_value=captured):
                third_receipt, third = self.routed_batch()
        self.assertEqual(original_namespaces, [row['configuration_sha256'] for row in self.routed_entries()])
        for group in zip(first, second, third):
            self.assertEqual(len({task['inference_trial'] for task in group}), 1)
            self.assertEqual(len({task['fingerprint'] for task in group}), 3)
            self.assertEqual(len({task['directory'] for task in group}), 3)
            for task in group:
                self.assertEqual(Path(task['directory']).name, task['fingerprint'])
                self.assertEqual(self.registry.verify_task(task)['fingerprint'], task['fingerprint'])
        self.assertEqual([receipt['provider_launches'] for receipt in (first_receipt, second_receipt, third_receipt)], [2, 2, 2])
        self.assertEqual(original, {name: (self.root / name).read_bytes() for name in original})
        for field in ('directory', 'inference_trial'):
            with self.assertRaisesRegex(ValueError, 'Registered task'):
                self.registry.verify_task({**first[0], field: first[0][field] + '-changed'})

    def test_41_provider_drift_after_resolution_prevents_binding_and_dispatch(self):
        from capabilities import coordinator
        real_prepare = coordinator.prepare
        paged_tools = run.paged_tools(self.inputs['tools.json'])
        begin = self.registry.begin
        provider = self.provider
        def drift_after_resolution(selected, selection, **kwargs):
            self.assertTrue(all(row.get('resolved_fingerprint') for row in selected))
            value = begin(selected, selection, **kwargs)
            patch.object(registration.runtime_attestation, 'provider', side_effect=lambda model:
                {**provider(model), 'endpoint': 'http://offline.invalid/changed-after-resolution'}).start()
            return value
        with patch.object(run, 'paged_tools', return_value=paged_tools), \
             self.assertRaisesRegex(ValueError, 'Resolved runtime/provider differs|action changed after attempt resolution'):
            self.routed_batch(prepare=real_prepare, begin=drift_after_resolution, production_prepare=True)
        self.assertEqual(self.last_routed_launches, 0)
        self.assertFalse(list(self.root.glob('**/bindings/*/record.json')))
        self.assertFalse(list(self.root.glob('**/dispatches/*.json')))

    def test_42_same_action_claim_survives_missing_results_and_prevents_redispatch(self):
        _, started = self.routed_batch()
        claims = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in (self.root / '.cache/benchmark-attempt-registry').glob('shared-*.json')}
        self.assertEqual(len(claims), 2)
        for task in started:
            shutil.rmtree(self.root / task['directory'])
        with self.assertRaisesRegex(RuntimeError, 'Benchmark incomplete'):
            self.routed_batch()
        self.assertEqual(self.last_routed_launches, 0)
        self.assertEqual(claims, {name: (self.root / name).read_bytes() for name in claims})
        status = json.loads((self.root / 'benchmarks/config/benchmark-status.json').read_bytes())
        self.assertEqual(status['provider_launches'], 0)
        self.assertEqual(status['model_api_calls'], 0)
        self.assertEqual(len(status['failures']), 2)

    def test_43_resolved_inventory_is_reused_for_binding_without_another_capture(self):
        self.events.clear()
        receipt, started = self.routed_batch()
        self.assertEqual(receipt['provider_launches'], 2)  # local callbacks only
        self.assertEqual(len(started), 2)
        self.assertEqual(self.events.count('capture:deepseek-harness'), 1)
        for model in self.models:
            # Binding still independently checks the selected public route.
            self.assertEqual(self.events.count('provider:' + model), 2)

    def test_44_conflicting_resolved_runtimes_cannot_be_bound(self):
        begin = self.registry.begin
        def conflict(selected, selection, **kwargs):
            reference = begin(selected, selection, **kwargs)
            proof = copy.deepcopy(selected[-1]['_resolved_attestation'])
            runtime = proof['runtime']
            runtime['manifest']['scope'] = 'conflicting-control-inventory'
            runtime['identity'] = registration.digest(action_cache.canonical(runtime['manifest']))
            proof.pop('identity')
            proof['identity'] = registration.digest(action_cache.canonical(proof))
            selected[-1]['_resolved_attestation'] = proof
            return reference
        with self.assertRaisesRegex(ValueError, 'different runtimes'):
            self.routed_batch(begin=conflict)
        self.assertEqual(self.last_routed_launches, 0)
        self.assertFalse(list(self.root.glob('**/bindings/*/record.json')))


class PublicationControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='registration-publish-control-')
        self.addCleanup(self.temporary.cleanup)
        self.registry = registration.Registry(Path(self.temporary.name))
        token = registration._controls.set(True)
        self.addCleanup(registration._controls.reset, token)
        self.document = {'control': 'offline-publish'}
        self.identity = registration.digest(action_cache.canonical(self.document))
        self.destination = self.registry.path(self.registry.directory(self.identity))
        self.rename = Path.rename

    def test_transient_denials_then_success(self):
        calls = []
        def rename(path, destination):
            calls.append(1)
            if len(calls) < 3: raise PermissionError(errno.EACCES, 'transient test denial')
            return self.rename(path, destination)
        with patch.object(Path, 'rename', rename), patch.object(time, 'sleep') as sleep:
            self.assertEqual(self.registry.publish(self.document), self.identity)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        saved, origin = self.registry.load(self.identity)
        self.assertEqual(saved, self.document)
        self.assertTrue(origin['offline_control'])

    def test_transient_missing_with_intact_source_then_success(self):
        calls = []
        def rename(path, destination):
            calls.append(1)
            if len(calls) == 1: raise FileNotFoundError(errno.ENOENT, 'DrvFs handle simulation')
            return self.rename(path, destination)
        with patch.object(Path, 'rename', rename), patch.object(time, 'sleep'):
            self.assertEqual(self.registry.publish(self.document), self.identity)
        self.assertEqual(len(calls), 2)

    def test_permanent_denial_is_bounded_and_cleans_pending(self):
        with patch.object(Path, 'rename', side_effect=PermissionError(errno.EACCES, 'permanent')) as rename, patch.object(time, 'sleep'):
            with self.assertRaises(PermissionError): self.registry.publish(self.document)
        self.assertEqual(rename.call_count, 6)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.glob('pending-*')), [])

    def test_unrelated_errors_are_not_retried(self):
        with patch.object(Path, 'rename', side_effect=OSError(errno.EIO, 'I/O failure')) as rename, patch.object(time, 'sleep') as sleep:
            with self.assertRaises(OSError): self.registry.publish(self.document)
        self.assertEqual(rename.call_count, 1)
        sleep.assert_not_called()

    def test_missing_source_is_not_retried(self):
        def rename(path, destination):
            for child in path.iterdir(): child.unlink()
            path.rmdir()
            raise FileNotFoundError(errno.ENOENT, 'source removed')
        with patch.object(Path, 'rename', rename), patch.object(time, 'sleep') as sleep:
            with self.assertRaises(FileNotFoundError): self.registry.publish(self.document)
        sleep.assert_not_called()

    def test_winning_publisher_is_still_validated(self):
        def rename(path, destination):
            self.rename(path, destination)
            raise PermissionError(errno.EACCES, 'concurrent publication')
        with patch.object(Path, 'rename', rename):
            self.assertEqual(self.registry.publish(self.document), self.identity)
        self.assertEqual(self.registry.load(self.identity)[0], self.document)

    def test_conflicting_winner_is_rejected(self):
        def rename(path, destination):
            self.rename(path, destination)
            (destination / 'record.json').write_text('{"different": true}')
            raise PermissionError(errno.EACCES, 'concurrent publication')
        with patch.object(Path, 'rename', rename):
            with self.assertRaisesRegex(ValueError, 'content identity changed'):
                self.registry.publish(self.document)


if __name__ == '__main__':
    inputs = registration.fingerprints()
    from grading.metadata_admission import metadata_validation_cache
    with metadata_validation_cache():
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RegistrationControls))
    if registration.fingerprints() != inputs:
        raise RuntimeError('Registration sources changed during validation')
    value = {'schema_version': 1, 'passed': result.wasSuccessful(), 'checks': result.testsRun,
             'failed': len(result.failures) + len(result.errors), 'model_api_calls': 0, 'provider_launches': 0,
             'inputs_sha256': inputs}
    registration.RECEIPT.write_bytes(action_cache.canonical(value))
    raise SystemExit(0 if result.wasSuccessful() else 1)
