"""Focused offline startup reuse controls; no provider calls or test-suite replay."""
import copy
from contextlib import ExitStack
import json
from contextvars import ContextVar
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import preparation_session as batch
import read_session
from read_session import ReadSession
from capabilities import analysis_context, registration, coordinator


class StaleFrozenPreparation(unittest.TestCase):
    """A source revision is repairable; changed historical bytes are not."""
    def fixtures(self, root):
        import action_cache
        from capabilities.baselines import tasks as matched
        from capabilities.native_abi import tasks as native
        from capabilities.optimized_locations import tasks as locations
        cache = root / 'cache'
        files = {name: b'original bytes' for name in matched.FILES}
        hashes = {name: matched.sha(raw) for name, raw in sorted(files.items())}
        package = matched.sha(action_cache.canonical(hashes))
        public = cache / 'task-freezes' / package
        public.mkdir(parents=True)
        for name, raw in files.items(): (public / name).write_bytes(raw)
        raw = b'{}'; spec_id = matched.sha(raw)
        private = cache / 'grade-specs' / (spec_id + '.json')
        private.parent.mkdir(); private.write_bytes(raw)
        common = {'schema_version': 1, 'id': 'case', 'task_id': 'case', 'profile_id': 'profile',
            'public_package_sha256': package, 'grading_spec_sha256': spec_id,
            'public_directory': str(public.relative_to(root)), 'grading_spec': str(private.relative_to(root)),
            'public_files_sha256': hashes, 'source_inputs_sha256': {'source.py': '0' * 64}}
        cases = [
            (matched, ('clang20', 'c', 'recover'), 'selected_sources', {
                'family_id': 'record_codec', 'kind': 'matched_buffer', 'original_compiler': 'clang20',
                'output_language': 'c', 'capability_ids': matched.CAPABILITIES, 'source_language': 'cpp',
                'artifact_schema': {'solution.c': 1}, 'hint_profile': 'known_matched_buffer_entry_v1'}),
            (native, 'rust', 'sources', {'family_id': 'variable_objects',
                'lineage_id': 'native_aggregate_interfaces_v1', 'kind': 'native_abi', 'variant': 'rust',
                'capability_ids': ['T7'], 'source_language': 'rust', 'output_language': None,
                'artifact_schema': native.ARTIFACT, 'hint_profile': 'known_native_aggregate_endpoints_v1'}),
            (locations, 'lto', 'sources', {'family_id': 'dispatch_registry',
                'lineage_id': 'optimized_mix_locations_v1', 'kind': 'optimized_localization', 'variant': 'lto',
                'capability_ids': ['L6'], 'source_language': 'cpp', 'output_language': None,
                'artifact_schema': {'instances.json': 1}, 'hint_profile': 'no_entry_optimized_locations_v1'})]
        for module, contract, source_function, fields in cases:
            value = {**common, **fields}
            declaration = {'task_id': 'case', 'family_id': fields['family_id'], 'profile': {'id': 'profile'}}
            with ExitStack() as stack:
                stack.enter_context(patch.object(module, 'ROOT', root))
                stack.enter_context(patch.object(module, 'CACHE', cache))
                stack.enter_context(patch.object(module, 'contract', return_value=contract))
                source = stack.enter_context(patch.object(module, source_function, return_value={'source.py': '1' * 64}))
                yield module, value, declaration, source, public, private

    def test_source_revision_is_pending_not_invalid(self):
        from capabilities import preparation
        with tempfile.TemporaryDirectory() as tmp:
            for module, value, declaration, _, _, _ in self.fixtures(Path(tmp)):
                with self.subTest(kind=value['kind']), patch.object(preparation, 'action'), patch.object(
                        preparation.tasks, 'frozen', side_effect=lambda _: module.validate_frozen(value, declaration, {})):
                    result = preparation.inspect(declaration, cache=Mock())
                    self.assertEqual(result['status'], 'pending')
                    self.assertIn('source inputs changed', result['reason'])

    def test_staleness_never_hides_corrupt_public_or_private_bytes(self):
        from capabilities import preparation
        with tempfile.TemporaryDirectory() as tmp:
            for module, value, declaration, _, public, private in self.fixtures(Path(tmp)):
                for path in (public / 'target', private):
                    raw = path.read_bytes()
                    try:
                        path.write_bytes(b'corrupt')
                        with self.subTest(kind=value['kind'], file=path.name), patch.object(preparation, 'action'), patch.object(
                                preparation.tasks, 'frozen', side_effect=lambda _: module.validate_frozen(value, declaration, {})):
                            self.assertEqual(preparation.inspect(declaration, cache=Mock())['status'], 'invalid')
                    finally:
                        path.write_bytes(raw)

    def test_current_sources_continue_into_semantic_validation(self):
        class ReachedSemanticValidation(Exception): pass
        with tempfile.TemporaryDirectory() as tmp:
            for module, value, declaration, source, _, _ in self.fixtures(Path(tmp)):
                source.return_value = value['source_inputs_sha256']
                with self.subTest(kind=value['kind']), patch.object(json, 'loads', side_effect=ReachedSemanticValidation):
                    with self.assertRaises(ReachedSemanticValidation): module.validate_frozen(value, declaration, {})

    def test_malformed_source_manifest_remains_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            for module, value, declaration, _, _, _ in self.fixtures(Path(tmp)):
                for malformed in (None, {}, {'source.py': 'not-a-digest'}, {'../outside': '0' * 64}):
                    with self.subTest(kind=value['kind'], manifest=malformed), self.assertRaises(ValueError):
                        module.validate_frozen({**value, 'source_inputs_sha256': malformed}, declaration, {})


class StartupPreparation(unittest.TestCase):
    def test_guards_once_and_fresh_after_scope(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            path = Path(tmp) / 'data'; path.write_bytes(b'first')
            calls = []
            @session.memoize
            def read():
                calls.append(1)
                return path.read_bytes()
            with patch.object(read_session, 'signature', wraps=read_session.signature) as stat:
                with session.startup_scope():
                    self.assertEqual(read(), b'first')
                    count = stat.call_count
                    for _ in range(306): self.assertEqual(read(), b'first')
                    self.assertEqual(stat.call_count, count)
                path.write_bytes(b'other')
                self.assertEqual(read(), b'other')
            self.assertEqual(len(calls), 2)

    def test_new_batch_revalidates_and_failures_never_cache(self):
        fn = Mock(side_effect=[ValueError('bad'), {'ok': 1}, {'ok': 2}])
        with ReadSession({}), batch.scope(True):
            with self.assertRaises(ValueError): batch.once('test', 1, fn)
            result = batch.once('test', 1, fn); result['ok'] = 99
            self.assertEqual(batch.once('test', 1, fn), {'ok': 1})
        with ReadSession({}), batch.scope(True):
            self.assertEqual(batch.once('test', 1, fn), {'ok': 2})
        self.assertEqual(fn.call_count, 3)

    def test_context_validation_once_per_runtime_for_all_153_tasks(self):
        ids = {str(i) for i in range(153)}
        with ReadSession({}), batch.scope(True), patch.object(analysis_context, 'require_validation', return_value={'passed': True}) as check:
            batch.declare_context(ids)
            for harness in ('dsh', 'zcode'):
                for task in ids:
                    batch.require_context(task_ids={task})
                    batch.require_context({'identity': harness}, task_ids={task})
            self.assertEqual(check.call_count, 3)
            self.assertTrue(all(call.kwargs['task_ids'] == ids for call in check.call_args_list))
            batch.require_context({'identity': 'changed'}, task_ids={'0'})
            self.assertEqual(check.call_count, 4)

    def test_undeclared_context_is_not_authorized_by_subset(self):
        with ReadSession({}), batch.scope(True), patch.object(analysis_context, 'require_validation', side_effect=ValueError('unknown task')) as check:
            batch.declare_context({'known'})
            with self.assertRaises(ValueError): batch.require_context(task_ids={'unknown'})
            check.assert_called_once_with(None, task_ids={'unknown'})

    def test_prepared_tasks_isolate_models_settings_routes_and_caller_mutations(self):
        entry = {'case': 'test', 'configuration_sha256': 'config', 'directory': 'run', 'resolved_fingerprint': 'key'}
        model = {'id': 'a', 'reasoning': 'high'}
        task = {'fingerprint': 'key', 'directory': 'run', 'payload': {'x': 1}}
        with ReadSession({}), batch.scope(True):
            batch.remember_task(entry, model, task)
            result = batch.resolved_task(entry, model); result['payload']['x'] = 2
            self.assertEqual(batch.resolved_task(entry, model), task)
            self.assertIsNone(batch.resolved_task(entry, {**model, 'id': 'b'}))
            self.assertIsNone(batch.resolved_task({**entry, 'configuration_sha256': 'new'}, model))
            with self.assertRaises(ValueError): batch.resolved_task({**entry, 'directory': 'other'}, model)
            with self.assertRaises(ValueError): batch.resolved_task({**entry, 'resolved_fingerprint': 'other'}, model)
        self.assertIsNone(batch.resolved_task(entry, model))

    def test_scoring_is_shared_across_models_but_not_changed_contracts(self):
        task = {'grading_spec_sha256': 'one', 'binary_sha256': 'binary', 'output_language': 'c'}
        create = Mock(return_value={'score': 1})
        with ReadSession({}), batch.scope(True):
            for model in ('a', 'b'):
                batch.scoring({**task, 'model': model}, create)
            self.assertEqual(create.call_count, 1)
            batch.scoring({**task, 'grading_spec_sha256': 'two'}, create)
            batch.scoring({**task, 'output_language': 'rust'}, create)
            self.assertEqual(create.call_count, 3)

    def test_exceptions_leave_no_trusted_state(self):
        with ReadSession({}) as session:
            with self.assertRaises(RuntimeError):
                with batch.scope(True):
                    raise RuntimeError('cancelled')
            self.assertIsNone(session.startup_epoch)
            self.assertIsNone(batch._BATCH.get())
            self.assertFalse(session.cache)

    def test_strict_policy_does_not_reuse(self):
        create = Mock(return_value=1)
        with batch.scope(False):
            batch.once('test', 1, create); batch.once('test', 1, create)
        self.assertEqual(create.call_count, 2)

    def test_cold_build_does_not_reuse_missing_input_or_old_task(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session, batch.scope(True):
            path = Path(tmp) / 'new'
            @session.memoize
            def inspect(): return path.read_bytes() if path.exists() else None
            self.assertIsNone(inspect())
            with batch.input_writes():
                path.write_bytes(b'built')
                self.assertEqual(inspect(), b'built')
            self.assertEqual(inspect(), b'built')

    def test_control_context_cannot_supply_a_production_pass(self):
        control = ContextVar('offline_fixture_scope', default=False)
        fn = Mock(side_effect=lambda: control.get())
        with ReadSession({}), batch.scope(True):
            token = control.set(True)
            self.assertTrue(batch.once('gate', 'same', fn))
            control.reset(token)
            self.assertFalse(batch.once('gate', 'same', fn))
        self.assertEqual(fn.call_count, 2)

    def test_environment_profile_key_shares_tasks_but_guards_changed_files(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            path = Path(tmp) / 'source'; path.write_bytes(b'one')
            calls = []
            def profile(identity):
                calls.append(1)
                return {'bytes': path.read_bytes()}
            cached = session.memoize(profile, arguments=read_session.profile_arguments)
            identity = {'environment': {'id': 'same'}, 'condition': 'same', 'case': 'a'}
            self.assertEqual(cached(identity), {'bytes': b'one'})
            self.assertEqual(cached({**identity, 'case': 'b'}), {'bytes': b'one'})
            self.assertEqual(len(calls), 1)
            path.write_bytes(b'two')
            self.assertEqual(cached(identity), {'bytes': b'two'})
            self.assertEqual(len(calls), 2)
            cached({**identity, 'condition': 'other'})
            self.assertEqual(len(calls), 3)

    def test_case_gates_shared_by_kind_and_language(self):
        with ReadSession({}), batch.scope(True), patch.object(coordinator, '_require_case_validation') as check:
            for i in range(153):
                coordinator.require_case_validation({'id': str(i), 'kind': 'matched_buffer', 'output_language': 'c'})
            coordinator.require_case_validation({'kind': 'matched_buffer', 'output_language': 'rust'})
            self.assertEqual(check.call_count, 2)

    def test_public_projection_shares_models_but_not_runtime_or_access(self):
        result = ({'target': b'ELF', 'prompt.txt': b'prompt'}, {'seed': 'public'}, None)
        with ReadSession({}), batch.scope(True), patch.object(coordinator, '_public_inputs', return_value=result) as project:
            case = {'id': 'one', 'public_package_sha256': 'hash'}
            first = coordinator.public_inputs(case, {'tools': 1}, 'binary')
            first[0]['prompt.txt'] = b'edited'
            self.assertEqual(coordinator.public_inputs(case, {'tools': 1}, 'binary'), result)
            self.assertEqual(project.call_count, 1)
            coordinator.public_inputs(case, {'tools': 2}, 'binary')
            coordinator.public_inputs(case, {'tools': 1}, 'premises_only')
            self.assertEqual(project.call_count, 3)

    def test_adapter_file_bytes_shared_only_with_exact_profile(self):
        import cache
        identity = {'environment': {'harness': 'dsh', 'files_sha256': {'x': 'sha'}}, 'condition': 'one', 'case': 'a'}
        with ReadSession({}), batch.scope(True), patch.object(cache, 'profile_bytes', return_value={'x': b'code'}) as read:
            self.assertEqual(batch.inference_files(identity), {'x': b'code'})
            batch.inference_files({**identity, 'case': 'b'})
            self.assertEqual(read.call_count, 1)
            batch.inference_files({**identity, 'condition': 'two'})
            self.assertEqual(read.call_count, 2)
        self.assertIsNone(batch.inference_files(identity))

    def test_runtime_component_blobs_cached_by_storage_root_and_runtime(self):
        import action_cache as ac
        import runtime_attestation as runtime
        with tempfile.TemporaryDirectory() as tmp:
            store = ac.Cache(Path(tmp) / 'one'); other = ac.Cache(Path(tmp) / 'two')
            raw = ac.canonical({'file': {'sha256': 'a' * 64, 'size_bytes': 3}})
            digest = store.put(raw)
            component = {'entries_digest': digest, 'sha256': digest['hash'], 'files': 1, 'bytes': 3}
            proof = {'runtime': {'identity': 'fixture', 'manifest': {'components': {'sdk': component}}}}
            with ReadSession({}), batch.scope(True):
                self.assertEqual(list(runtime.component_blobs(proof, store.get)), [('sdk', raw)])
                # Same digest in a different store is not an admitted component.
                with self.assertRaises(FileNotFoundError): list(runtime.component_blobs(proof, other.get))
                (store.root / 'cas' / digest['hash']).write_bytes(b'bad')
                self.assertEqual(list(runtime.component_blobs(proof, store.get)), [('sdk', raw)])
            with self.assertRaises(ValueError): list(runtime.component_blobs(proof, store.get))

    def test_real_binding_reuses_prepared_tasks_and_preserves_identity(self):
        # Reuse one real saved task/build plus the existing isolated registration
        # fixture. Provider/runtime responses are local stubs, never API calls.
        from check_capability_registration import RegistrationControls
        control = RegistrationControls()
        control.setUp()
        try:
            with (ReadSession({}), batch.scope(True),
                  patch.object(coordinator, 'require_case_validation'),
                  patch.object(analysis_context, 'require_validation'),
                  patch.object(registration.runtime_attestation, 'current_runtime', side_effect=control.capture)):
                entries = []
                for original in control.selected:
                    model = control.models[original['model']]
                    proof = lambda chosen, configuration: registration.seal(chosen, configuration,
                        control.capture(chosen['harness']), control.provider(chosen))
                    task = control.prepare(original, model, materialize=False, attestor=proof)
                    entry = {**original, 'directory': task['directory'], 'resolved_fingerprint': task['fingerprint'],
                             '_resolved_attestation': task['runtime_attestation']}
                    batch.remember_task(entry, model, task)
                    entries.append(entry)
                reference = control.registry.begin(entries, control.selection)
                forbidden = Mock(side_effect=AssertionError('Prepared task repeated'))
                selected, bound = control.registry.complete(reference, entries, control.selection, forbidden)
                forbidden.assert_not_called()
                self.assertEqual(len(selected), 2)
                for entry in entries:
                    task = batch.resolved_task(entry, control.models[entry['model']])
                    self.assertEqual(task['fingerprint'], entry['resolved_fingerprint'])
                self.assertIn('binding_id', bound)
        finally:
            control.doCleanups()


if __name__ == '__main__':
    unittest.main()
