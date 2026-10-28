#!/usr/bin/python3
"""Adversarial offline controls for runtime/provider attestation; no model calls."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import action_cache
import runtime_attestation as runtime
import runtime_storage


class RuntimeStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rea-runtime-controls-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'workspace'
        self.source = self.root / 'node_modules'
        self.source.mkdir(parents=True)
        (self.source / 'entry').write_bytes(b'installed public module')
        (self.source / 'link').symlink_to('entry')
        (self.source / 'entry').chmod(0o755)
        self.store = self.base / 'packages'
        self.configuration = {'mode': 'linux_temp', 'temporary_root': str(self.base), 'cleanup_grace_seconds': 1}

    def package(self):
        return runtime_storage.package(self.root, self.store, paths=['node_modules'])

    def leftovers(self):
        return list(self.base.glob('rea-runtime-*'))

    def test_wsl_and_most_specific_mount_detection(self):
        self.assertTrue(runtime_storage.is_wsl(release='6.6-Microsoft-standard-WSL2'))
        self.assertFalse(runtime_storage.is_wsl(release='6.6-generic'))
        mounts = '1 0 0:1 / / rw - ext4 root rw\n2 1 0:2 / /mnt/h rw - 9p host rw\n3 2 0:3 / /mnt/h/native rw - ext4 disk rw\n'
        self.assertTrue(runtime_storage.windows_mount('/mnt/h/work', mountinfo=mounts))
        self.assertFalse(runtime_storage.windows_mount('/mnt/h/native/work', mountinfo=mounts))
        self.assertFalse(runtime_storage.windows_mount('/home/user', mountinfo=mounts))

    def test_auto_mode_does_not_stage_native_linux_or_disabled_runs(self):
        with patch.object(runtime_storage, 'is_wsl', return_value=False):
            self.assertFalse(runtime_storage.automatic(self.root, {'mode': 'auto'}))
        with patch.object(runtime_storage, 'is_wsl', return_value=True), patch.object(runtime_storage, 'windows_mount', return_value=True):
            self.assertTrue(runtime_storage.automatic(self.root, {'mode': 'auto'}))
            self.assertFalse(runtime_storage.automatic(self.root, {'mode': 'off'}))

    def test_package_is_reused_and_private_paths_are_not_allowed(self):
        value, archive = self.package()
        again, other = self.package()
        self.assertEqual((value, archive), (again, other))
        self.assertEqual(value['model_api_calls'], 0)
        with self.assertRaises(ValueError):
            runtime_storage.package(self.root, self.store, paths=['.zcode-state'])
        self.assertEqual(list(self.store.glob('pack-*')), [])

    def test_temporary_tree_preserves_bytes_and_is_removed(self):
        value, archive = self.package()
        with runtime_storage.temporary_runtime(value, archive, temporary_root=self.base) as directory:
            self.assertEqual((directory / 'node_modules/entry').read_bytes(), b'installed public module')
            self.assertTrue((directory / 'node_modules/link').is_symlink())
            self.assertTrue((directory / 'node_modules/entry').stat().st_mode & 0o111)
        self.assertFalse(directory.exists())
        self.assertEqual(self.leftovers(), [])

    def test_failure_and_interrupt_remove_temporary_tree(self):
        value, archive = self.package()
        for error in (RuntimeError('failure'), KeyboardInterrupt()):
            with self.assertRaises(type(error)):
                with runtime_storage.temporary_runtime(value, archive, temporary_root=self.base):
                    raise error
            self.assertEqual(self.leftovers(), [])

    def test_archive_corruption_is_rejected(self):
        value, archive = self.package()
        with archive.open('ab') as stream: stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'corrupt'): self.package()
        with self.assertRaises(ValueError):
            with runtime_storage.temporary_runtime(value, archive, temporary_root=self.base): pass
        self.assertEqual(self.leftovers(), [])

    def test_private_member_rejected_even_with_matching_archive_digest(self):
        import io, tarfile
        value, archive = self.package()
        with tarfile.open(archive, 'a') as stream:
            member = tarfile.TarInfo('.zcode-state/private'); member.size = 6
            stream.addfile(member, io.BytesIO(b'SECRET'))
        value = {**value, 'sha256': runtime_storage.sha_file(archive)}
        with self.assertRaises(ValueError):
            with runtime_storage.temporary_runtime(value, archive, temporary_root=self.base): pass
        self.assertEqual(self.leftovers(), [])

    def test_process_launch_failure_still_cleans_up(self):
        value, archive = self.package()
        with patch.object(runtime_storage, 'package', return_value=(value, archive)), patch.object(runtime_storage, 'guarded_process', side_effect=OSError('launch failed')):
            with self.assertRaises(OSError):
                runtime_storage.execute(['/usr/bin/true'], root=self.root, configuration=self.configuration)
        self.assertEqual(self.leftovers(), [])

    def test_process_signal_reaps_child_and_restores_handler(self):
        import sys, time
        previous = signal.getsignal(signal.SIGTERM)
        timer = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                runtime_storage.guarded_process([sys.executable, '-c', 'import time; time.sleep(30)'], grace=1)
        finally:
            timer.cancel(); timer.join()
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_real_mount_keeps_logical_paths_and_removes_temporary_files(self):
        value, archive = self.package()
        (self.source / 'entry').write_bytes(b'outer installation changed after pinning')
        target = str(self.source / 'entry')
        script = "from pathlib import Path; p=Path(" + repr(target) + "); assert p.read_bytes()==b'installed public module'; print('stable-path-linux-runtime')"
        with patch.object(runtime_storage, 'package', return_value=(value, archive)):
            code = runtime_storage.execute(['/usr/bin/python3', '-c', script], root=self.root, configuration=self.configuration)
        self.assertEqual(code, 0)
        self.assertEqual((self.source / 'entry').read_bytes(), b'outer installation changed after pinning')
        self.assertEqual(self.leftovers(), [])

    def test_private_devices_allow_nested_child_null_streams(self):
        value, archive = self.package()
        script = ('import subprocess; '
            'sink=open("/dev/null","wb"); sink.write(b"discarded"); sink.close(); '
            'source=open("/dev/null","rb"); assert source.read()==b""; source.close(); '
            'subprocess.run(["/usr/bin/python3","-c","print(123)"], '
            'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)')
        with patch.object(runtime_storage, 'package', return_value=(value, archive)):
            code = runtime_storage.execute(['/usr/bin/python3', '-c', script],
                                          root=self.root, configuration=self.configuration)
        self.assertEqual(code, 0)
        self.assertEqual(self.leftovers(), [])

    def test_inventory_receipts_survive_but_temporary_sqlite_does_not(self):
        value, archive = self.package()
        persistent = self.root / '.cache/benchmark-runtime/attestation'
        persistent.mkdir(parents=True)
        (persistent / 'hashes.sqlite').write_bytes(b'original memo')
        manifest = {'schema_version': 1, 'components': {}}
        identity = runtime.sha(runtime_storage.canonical(manifest))
        raw = runtime_storage.canonical({'identity': identity, 'manifest': manifest})
        def child(command, **kwargs):
            index = command.index(str(persistent)); memo = Path(command[index - 1])
            (memo / 'hashes.sqlite').write_bytes(b'temporary memo')
            (memo / (identity + '.json')).write_bytes(raw)
            return 0
        with patch.object(runtime_storage, 'package', return_value=(value, archive)), patch.object(runtime_storage, 'guarded_process', side_effect=child):
            self.assertEqual(runtime_storage.execute(['/usr/bin/true'], root=self.root, configuration=self.configuration), 0)
        self.assertEqual((persistent / (identity + '.json')).read_bytes(), raw)
        self.assertEqual((persistent / 'hashes.sqlite').read_bytes(), b'original memo')
        self.assertEqual(self.leftovers(), [])

    def test_descendant_scratch_files_are_private_and_removed(self):
        import uuid
        with tempfile.TemporaryDirectory(prefix='scratch-control-', dir=runtime.ROOT / '.cache/benchmark-runtime') as temporary:
            root = Path(temporary)
            (root / 'node_modules').mkdir()
            (root / 'node_modules/entry').write_bytes(b'public runtime')
            value, archive = runtime_storage.package(root, self.store, paths=['node_modules'])
            sentinel = '/tmp/rea-scratch-control-' + uuid.uuid4().hex
            script = 'from pathlib import Path; p=Path(' + repr(sentinel) + '); p.write_text("scratch"); assert p.exists()'
            with patch.object(runtime_storage, 'package', return_value=(value, archive)):
                self.assertEqual(runtime_storage.execute(['/usr/bin/python3', '-c', script], root=root, configuration=self.configuration), 0)
            self.assertFalse(Path(sentinel).exists())
            self.assertEqual(self.leftovers(), [])

    def test_stale_owner_is_removed_but_live_owner_is_preserved(self):
        import fcntl
        stale = self.base / 'rea-runtime-stale'; stale.mkdir()
        (stale / '.owner.lock').write_text('')
        value, archive = self.package()
        with runtime_storage.temporary_runtime(value, archive, temporary_root=self.base) as live:
            runtime_storage.reap_stale(self.base)
            self.assertFalse(stale.exists())
            self.assertTrue(live.exists())
        self.assertEqual(self.leftovers(), [])

    def test_real_attestation_and_action_key_ignore_random_temporary_paths(self):
        for name in runtime.CONTROL:
            file = self.root / name; file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('fixed offline control')
        policy = {'policy': 'storage-control', 'components': {'runtime': {'paths': ['node_modules'],
            'harnesses': ['fixture']}}, 'runtime_environment': {}, 'network': {}, 'approved_system_roots': []}
        before = runtime.capture('fixture', root=self.root, policy=policy, native=False, memo=False, fresh=True)
        expected = action_cache.Action('storage_identity_control', {'runtime': action_cache.canonical(before)}).key
        value, archive = self.package()
        output = self.base / 'attested.json'
        script = ('import sys,json; from pathlib import Path; sys.path.insert(0,' + repr(str(runtime.ROOT / 'benchmarks/scripts'))
            + '); import runtime_attestation as r; value=r.capture("fixture", root=Path(' + repr(str(self.root))
            + '), policy=' + repr(policy) + ',native=False,memo=False,fresh=True); Path('
            + repr(str(output)) + ').write_text(json.dumps(value))')
        with patch.object(runtime_storage, 'package', return_value=(value, archive)):
            for _ in range(2):
                self.assertEqual(runtime_storage.execute(['/usr/bin/python3', '-c', script], root=self.root,
                    configuration=self.configuration), 0)
                actual = json.loads(output.read_bytes())
                self.assertEqual(actual, before)
                self.assertEqual(action_cache.Action('storage_identity_control', {'runtime': action_cache.canonical(actual)}).key, expected)
        self.assertEqual(self.leftovers(), [])


def seal(value):
    return {**value, 'identity': runtime.sha(action_cache.canonical(value))}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rea-attestation-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in runtime.CONTROL:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture control')
        (self.root / 'components').mkdir()
        for name in ('rea', 'ghidra', 'java', 'node', 'harness', 'dependency'):
            (self.root / 'components' / name).write_text(name)
        self.policy = {'schema_version': 1, 'policy': 'test', 'approved_system_roots': [],
            'components': {'runtime': {'paths': ['components'], 'harnesses': ['deepseek-harness']}},
            'runtime_environment': {'JAVA_HOME': 'reviewed-java'}, 'network': {'proxy': 'disabled'}}

    def capture(self, **kwargs):
        return runtime.capture('deepseek-harness', root=self.root, policy=self.policy, native=False, memo=False, **kwargs)

    def test_all_runtime_components_invalidate(self):
        initial = self.capture()
        for name in ('rea', 'ghidra', 'java', 'node', 'harness', 'dependency'):
            path = self.root / 'components' / name
            raw = path.read_bytes()
            path.write_bytes(raw + b'changed')
            self.assertNotEqual(initial['identity'], self.capture()['identity'], name)
            path.write_bytes(raw)
        self.assertEqual(initial, self.capture())

    def test_execution_storage_source_and_configuration_are_attested(self):
        initial = self.capture()
        for name in ('benchmarks/scripts/runtime_storage.py', 'benchmarks/config/runtime-storage.json'):
            self.assertIn(name, runtime.CONTROL)
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b'changed execution storage')
            self.assertNotEqual(initial['identity'], self.capture()['identity'])
            path.write_bytes(original)
        self.assertEqual(initial, self.capture())

    def test_unrelated_changes_invariant_and_no_mtimes_in_key(self):
        initial = self.capture()
        for name in ('report.py', 'pricing.json', 'runner-test.py', 'storage.py', 'README.md'):
            (self.root / name).write_text('unrelated')
        os.utime(self.root / 'components/rea', None)
        self.assertEqual(initial, self.capture())

    def test_added_removed_file_and_executable_mode(self):
        initial = self.capture()
        path = self.root / 'components/new-plugin'
        path.write_text('new')
        self.assertNotEqual(initial, self.capture())
        path.unlink()
        self.assertEqual(initial, self.capture())
        path = self.root / 'components/node'
        path.chmod(0o755)
        self.assertNotEqual(initial, self.capture())

    def test_dangling_dependency_is_recorded_and_appearance_changes_key(self):
        path = self.root / 'components/optional'
        path.symlink_to('not-installed')
        initial = self.capture()
        entries = initial['manifest']['components']['runtime']['entries']
        self.assertTrue(entries['workspace/components/optional']['target_missing'])
        (self.root / 'components/not-installed').write_text('now installed')
        self.assertNotEqual(initial, self.capture())
        (self.root / 'components/not-installed').unlink()
        self.assertEqual(initial, self.capture())

    def test_symlink_target_and_content(self):
        path = self.root / 'components/selected'
        path.symlink_to('rea')
        initial = self.capture()
        path.unlink()
        path.symlink_to('java')
        self.assertNotEqual(initial, self.capture())
        path.unlink()
        path.symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.capture()
        private = self.root / 'private-account-state'
        private.write_text('DO-NOT-HASH-CREDENTIALS')
        path.unlink()
        path.symlink_to(private)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.capture()

    def test_sealed_environment_changes_identity(self):
        initial = self.capture()
        self.policy['runtime_environment']['JAVA_HOME'] = 'another-reviewed-java'
        self.assertNotEqual(initial, self.capture())

    def test_provider_resolved_before_runtime_and_each_preparation_rescans(self):
        events = []
        model = {'id': 'fixture', 'harness': 'deepseek-harness'}
        def provider(_):
            events.append('provider')
            return {'provider': 'fixture'}
        with patch.object(runtime, 'provider', side_effect=provider), \
             patch.object(runtime, 'config', return_value={'verification_mode': 'strict_per_task'}), \
             patch.object(runtime, 'capture', side_effect=lambda _, **kw: (events.append('runtime') or {'identity': 'fixture'})):
            runtime.prepare(model)
            runtime.prepare(model)
        self.assertEqual(events, ['provider', 'runtime', 'provider', 'runtime'])

    def test_memo_cannot_mask_equal_length_change_with_restored_mtime(self):
        def memo_capture():
            return runtime.capture('deepseek-harness', root=self.root, policy=self.policy, native=False, memo=True)
        initial = memo_capture()
        path = self.root / 'components/rea'
        original = path.stat()
        path.write_text('REA')
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertNotEqual(initial, memo_capture())

    def test_parallel_captures_share_memo_without_long_write_lock(self):
        def capture(_):
            return runtime.capture('deepseek-harness', root=self.root, policy=self.policy, native=False, memo=True)
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(capture, range(4)))
        self.assertTrue(all(value == results[0] for value in results))

    def test_native_loader_parses_records_not_diagnostic_paths(self):
        inventory = runtime.Inventory(self.root, self.policy)
        native = self.root / 'components/node'
        library = self.root / 'components/library.so'
        library.write_text('trusted library fixture')
        inventory.native = {native}
        stdout = f'libfixture.so => {library} (0x1234)\n{native}: diagnostic text\n'.encode()
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout, b'')):
            result = inventory.shared_libraries()
        self.assertEqual(result['files'], 1)
        inventory.native = {self.root / 'components/musl/system.node', self.root / 'components/musl_x64/koffi.node',
                            self.root / 'components/linuxmusl-x64/library.so', self.root / 'components/openbsd_x64/koffi.node'}
        with patch.object(subprocess, 'run', side_effect=AssertionError('Foreign libc is not loaded')):
            self.assertEqual(inventory.shared_libraries()['files'], 0)

    def test_jvm_loader_context_is_explicit_and_not_inherited(self):
        java = self.root / 'components/java-home'
        self.policy['runtime_environment']['JAVA_HOME'] = str(java)
        inventory = runtime.Inventory(self.root, self.policy)
        inventory.native = {java / 'lib/libjava.so'}
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as probe:
            inventory.shared_libraries()
        env = probe.call_args.kwargs['env']
        self.assertIn(str(java / 'lib/server'), env['LD_LIBRARY_PATH'])
        self.assertNotIn('HTTPS_PROXY', env)

    def attestation(self):
        return seal({'schema_version': 1, 'runtime': self.capture(),
                     'provider': {'provider': 'openai', 'model': 'fixture', 'credential_mode': 'api-key'}})

    def test_drift_refused_without_model_launch(self):
        with patch.object(runtime, 'config', return_value={'verification_mode': 'strict_per_task'}):
            task = {'harness': 'deepseek-harness', 'model': 'fixture', 'runtime_attestation': self.attestation()}
            expected = task['runtime_attestation']['runtime']
            with patch.object(runtime, 'verify_configuration'), patch.object(runtime, 'capture', return_value=expected), patch.object(runtime, 'provider', return_value=task['runtime_attestation']['provider']):
                self.assertEqual(runtime.verify(task)['status'], 'verified_local_content_and_route')
            with patch.object(runtime, 'verify_configuration'), patch.object(runtime, 'capture', return_value={'changed': True}), patch.object(runtime, 'provider') as provider:
                with self.assertRaisesRegex(ValueError, 'content changed'):
                    runtime.verify(task)
                provider.assert_not_called()
            with patch.object(runtime, 'verify_configuration'), patch.object(runtime, 'capture', return_value=expected), patch.object(runtime, 'provider', return_value={'provider': 'openai-codex'}):
                with self.assertRaisesRegex(ValueError, 'route changed'):
                    runtime.verify(task)

    def test_manifest_and_attestation_tampering_rejected(self):
        value = self.attestation()
        value['runtime']['manifest']['components'] = {}
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            runtime.validate(value)

    def test_generated_configuration_and_public_inputs_are_frozen(self):
        directory = self.root / 'frozen'
        directory.mkdir()
        identity = {}
        for name, key in [('prompt.txt', 'prompt'), ('target', 'binary'), ('tools.json', 'tools'), ('manifest.json', 'manifest')]:
            raw = name.encode()
            (directory / name).write_bytes(raw)
            identity[key] = runtime.sha(raw)
        attestation = self.attestation()
        attestation.pop('identity')
        attestation['configuration'] = {'gateway': {'max_tool_calls': 5, 'analysis_tools_file': '@task/tools.json'},
            'harness': {'max_output_tokens': 100, 'analysis_tools_file': '@task/tools.json'}}
        attestation = seal(attestation)
        identity['attestation'] = attestation
        task = {'input_directory': str(directory), 'runtime_attestation': attestation, 'inference_identity': identity}
        files = {'gateway.json': {**attestation['configuration']['gateway'], 'analysis_tools_file': str(directory / 'tools.json'),
                                 'runtime_environment': runtime.environment(attestation)},
                 'harness-config.json': {**attestation['configuration']['harness'], 'analysis_tools_file': str(directory / 'tools.json')}}
        for name, data in files.items():
            (directory / name).write_text(json.dumps(data))
        runtime.verify_configuration(task)
        for name in files:
            raw = (directory / name).read_bytes()
            (directory / name).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'settings changed'):
                runtime.verify_configuration(task)
            (directory / name).write_bytes(raw)
        (directory / 'tools.json').write_text('altered catalog')
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            runtime.verify_configuration(task)

    def test_attestation_changes_action_namespace(self):
        a = action_cache.Action('inference', {'runtime.json': action_cache.canonical(self.attestation())})
        changed = self.attestation()
        changed['provider']['provider'] = 'openai-codex'
        b = action_cache.Action('inference', {'runtime.json': action_cache.canonical(changed)})
        self.assertNotEqual(a.key, b.key)

    def test_historical_observation_never_uses_current_installation(self):
        directory = self.root / 'historical'
        directory.mkdir()
        (directory / 'task.json').write_text('{"model":"old-run"}')
        with patch.object(runtime, 'capture', side_effect=AssertionError('Do not inspect current runtime')), \
             patch.object(runtime, 'provider', side_effect=AssertionError('Do not inspect credentials')):
            self.assertEqual(runtime.observation(directory, lambda p: p.read_bytes()), 'historical_unknown')

    def large_runtime(self):
        for index in range(160):
            (self.root / 'components' / f'module-{index:04d}').write_text('retained runtime dependency')
        return self.capture()

    def test_large_component_maps_use_shared_cas_without_losing_entries(self):
        value = self.large_runtime()
        self.assertEqual(value['manifest']['schema_version'], 2)
        component = value['manifest']['components']['runtime']
        self.assertNotIn('entries', component)
        local = action_cache.Cache(self.root / '.cache/benchmark-actions/v3')
        entries = runtime.component_entries(component, local.get)
        self.assertEqual(component['files'], len([item for item in entries.values() if 'sha256' in item]))
        self.assertIn('workspace/components/module-0159', entries)
        self.assertLess(len(action_cache.canonical(value)), 16384)
        os.utime(self.root / 'components/module-0000', None)
        self.assertEqual(value, self.capture())
        self.assertEqual(runtime.compact_runtime(value, root=self.root), value)

    def test_missing_or_corrupt_runtime_component_is_not_valid_evidence(self):
        value = self.large_runtime()
        proof = seal({'schema_version': 1, 'runtime': value, 'provider': {'provider': 'offline'}})
        local = action_cache.Cache(self.root / '.cache/benchmark-actions/v3')
        runtime.validate(proof, read_blob=local.get)
        component = value['manifest']['components']['runtime']
        path = local._path('cas', component['entries_digest']['hash'])
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + b'!')
        with self.assertRaises((ValueError, OSError)):
            runtime.validate(proof, read_blob=local.get)
        path.write_bytes(raw)
        path.unlink()
        with self.assertRaises((ValueError, OSError)):
            runtime.validate(proof, read_blob=local.get)

    def test_component_maps_are_declared_merkle_inputs_not_only_json_references(self):
        import cache, environment_profile, records
        entries = {f'workspace/synthetic/module-{index:04d}':
                   {'sha256': 'a' * 64, 'size_bytes': 3, 'executable': False} for index in range(200)}
        component = {'sha256': runtime.sha(action_cache.canonical(entries)), 'files': 200, 'bytes': 600, 'entries': entries}
        manifest = {'schema_version': 1, 'harness': 'deepseek-harness', 'components': {'synthetic': component}, 'environment': {}}
        compact = runtime.compact_runtime({'identity': runtime.sha(action_cache.canonical(manifest)), 'manifest': manifest})
        proof = seal({'schema_version': 1, 'runtime': compact, 'provider': {'provider': 'offline'}})
        model = records.models()['deepseek-flash']
        profile = environment_profile.current(model['harness'], 'paged-evidence-v2')
        entry = {'experiment': 'runtime-component-control', 'condition': 'paged-evidence-v2'}
        case = {'id': 'runtime-component-control', 'language': 'cpp', 'max_tool_calls': 3,
                'timeout_seconds': 60, 'max_output_tokens': 128}
        files = {'target': b'fixture target', 'prompt.txt': b'fixture question', 'tools.json': b'{"tools":[]}', 'manifest.json': b'{}'}
        identity = cache.inference_identity(entry, model, case, profile, files['prompt.txt'], files['target'],
                                           files['tools.json'], files['manifest.json'], {})
        identity['attestation'] = proof
        action = action_cache.inference(identity, files)
        tree = action.descriptor['input_root_digest']
        for name in ('runtime', 'components'):
            node = json.loads(action.blobs[tree['hash']])
            tree = next(item['digest'] for item in node['directories'] if item['name'] == name)
        node = json.loads(action.blobs[tree['hash']])
        leaf = next(item for item in node['files'] if item['name'] == 'synthetic.json')
        self.assertEqual(leaf['digest'], compact['manifest']['components']['synthetic']['entries_digest'])
        self.assertEqual(json.loads(action.blobs[leaf['digest']['hash']]), entries)


class DispatchTests(unittest.TestCase):
    def test_cancel_during_inventory_cannot_launch_model(self):
        spec = importlib.util.spec_from_file_location('attestation_cancel_fixture', runtime.ROOT / 'benchmarks/harnesses/dispatch.py')
        dispatch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatch)
        cancelled = threading.Event()
        def verify(_):
            cancelled.set()
            return {'status': 'verified_local_content_and_route'}
        with patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}), patch.object(runtime, 'verify', side_effect=verify), \
             patch.object(dispatch, '_launch') as launch:
            with self.assertRaisesRegex(InterruptedError, 'no provider launch'):
                dispatch.launch(Path('/tmp'), {'model': 'fixture', 'harness': 'zcode'}, {'id': 'fixture', 'harness': 'zcode'}, cancelled)
            launch.assert_not_called()

    def test_actual_dsh_patches_pin_endpoints_and_environment_excludes_overrides(self):
        script = r'''
import assert from 'node:assert/strict';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {parse} from 'yaml';
import {makePatch,environment} from './benchmarks/harnesses/deepseek-harness.mjs';
const dir=await mkdtemp('/tmp/rea-attested-patches-');
try{
 for(const provider of ['openai','openai-codex','deepseek-official']){
  const baseURL='https://frozen.example/'+provider;
  const model={id:provider==='deepseek-official'?'deepseek-flash':'gpt-6.1-sol',label:'Offline fixture',reasoning:'high',provider,baseURL};
  const patches=parse(await readFile(await makePatch('cpp',model,dir,{publicDir:dir}),'utf8'));
  assert.equal(patches.find(p=>p.id==='agent-default-model').config.provider,provider);
  const settings=provider==='deepseek-official'?patches.find(p=>p.id==='llm-deepseek').config:patches.find(p=>p.id==='llm-pi-ai').config.providers[provider];
  assert.equal(settings.baseURL,baseURL);
 }
 const env=environment({JAVA_HOME:'/reviewed/java',SSL_CERT_FILE:'/reviewed/ca'});
 for(const name of ['OPENAI_API_KEY','DEEPSEEK_API_KEY','HTTPS_PROXY','NODE_OPTIONS','LD_PRELOAD'])assert.equal(env[name],undefined);
 assert.equal(env.JAVA_HOME,'/reviewed/java');
}finally{await rm(dir,{recursive:true,force:true});}
'''
        child = subprocess.run([str(runtime.ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'),
            '--require', str(runtime.ROOT / 'benchmarks/scripts/offline-network.cjs'), '--input-type=module', '-e', script],
            cwd=runtime.ROOT, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HTTPS_PROXY': 'PRIVATE-CANARY',
                'OPENAI_API_KEY': 'PRIVATE-CANARY'}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(child.returncode, 0, child.stderr.decode())
        self.assertNotIn('PRIVATE-CANARY', child.stdout.decode())

    def test_zcode_proxy_redirect_and_tls_policy(self):
        spec = importlib.util.spec_from_file_location('attestation_zcode_fixture', runtime.ROOT / 'benchmarks/harnesses/zcode.py')
        zcode = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(zcode)
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://ignored.invalid'}):
            opener = zcode.direct_opener('/etc/ssl/certs/ca-certificates.crt')
        for handler in opener.handlers:
            if isinstance(handler, zcode.urllib.request.ProxyHandler):
                self.assertFalse(handler.proxies)
        self.assertIsNone(zcode.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_offline_fixture_cannot_dispatch(self):
        spec = importlib.util.spec_from_file_location('attestation_dispatch_guard', runtime.ROOT / 'benchmarks/harnesses/dispatch.py')
        dispatch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatch)
        with patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}), patch.object(runtime, 'verify') as verify:
            with self.assertRaisesRegex(ValueError, 'Offline fixture'):
                dispatch.launch(Path('/tmp'), {'offline_fixture': True}, {})
            verify.assert_not_called()

    def test_preflight_drift_prevents_dispatch_and_postflight_drift_blocks_grade(self):
        spec = importlib.util.spec_from_file_location('attestation_dispatch_fixture', runtime.ROOT / 'benchmarks/harnesses/dispatch.py')
        dispatch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatch)
        task = {'model': 'fixture', 'harness': 'zcode'}
        model = {'id': 'fixture', 'harness': 'zcode'}
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}):
            directory = Path(temporary)
            with patch.object(runtime, 'verify', side_effect=ValueError('content changed')), patch.object(dispatch, '_launch') as launch:
                with self.assertRaisesRegex(ValueError, 'content changed'):
                    dispatch.launch(directory, task, model)
                launch.assert_not_called()
            proof = {'identity': 'fixture', 'status': 'verified_local_content_and_route'}
            with patch.object(runtime, 'verify', side_effect=[proof, ValueError('content changed')]), patch.object(dispatch, '_launch') as launch:
                with self.assertRaisesRegex(RuntimeError, 'without correctness grading'):
                    dispatch.launch(directory, task, model)
                self.assertEqual(launch.call_count, 1)
                self.assertFalse(json.loads((directory / 'harness-failure.json').read_text())['model_correctness_grade_allowed'])
                import run
                with self.assertRaisesRegex(RuntimeError, 'cannot receive'):
                    run.grade_saved(directory, task)

    def test_provider_modes_rotation_endpoint_and_no_unrelated_credentials(self):
        script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {resolveProvider,credentialsFor,endpoint} from './benchmarks/harnesses/provider-auth.mjs';
const policy=JSON.parse(fs.readFileSync('./benchmarks/config/runtime-attestation.json','utf8'));
delete policy.gpt_provider; // Exercise the default resolution separately from explicit account selection.
const readJSON=async()=>({tokens:{access_token:'header.'+Buffer.from(JSON.stringify({exp:Date.now()/1000+7200})).toString('base64url')+'.signature',refresh_token:'PRIVATE-CANARY'}});
const readYAML=async()=>{throw new Error('Unrelated DeepSeek credentials must not be read');};
const options={env:{OPENAI_API_KEY:'PRIVATE-CANARY'},readJSON,readYAML,policy};
const model={id:'gpt-6.1-sol',harness:'deepseek-harness'};
const api=await resolveProvider(model,options);
assert.equal(api.plan.provider,'openai');
assert(!JSON.stringify(api.plan).includes('PRIVATE-CANARY'));
const rotated=await resolveProvider(model,{...options,env:{OPENAI_API_KEY:'ROTATED-CANARY'}});
assert.deepEqual(rotated.plan,api.plan);
assert.equal((await credentialsFor(api.plan,{...options,env:{OPENAI_API_KEY:'ROTATED-CANARY'}})).refs.OPENAI_API_KEY,'ROTATED-CANARY');
const oauth=await resolveProvider(model,{...options,env:{HOME:'/fixture'}});
assert.equal(oauth.plan.provider,'openai-codex');
await assert.rejects(credentialsFor(api.plan,{...options,env:{HOME:'/fixture'}}),/no fallback/);
const alternate=await resolveProvider(model,{...options,env:{OPENAI_API_KEY:'PRIVATE-CANARY',OPENAI_BASE_URL:'https://provider.example/v1'}});
assert.notDeepEqual(alternate.plan,api.plan);
const ds=await resolveProvider({id:'deepseek-v4-pro',harness:'deepseek-harness'},{...options,env:{DEEPSEEK_API_KEY:'PRIVATE-CANARY'},readJSON:async()=>{throw Error('No GPT credentials needed');}});
assert.equal(ds.plan.protocol,'anthropic-messages');
const glm=await resolveProvider({id:'glm-5.3',harness:'zcode'},{...options,env:{GLM_AUTH_TOKEN:'PRIVATE-CANARY',GLM_BASE_URL:'https://glm.example/v4'}});
assert.equal(glm.plan.credential_source,'glm-environment');
for(const url of ['http://unsafe.example','https://user:password@example','https://example?token=secret','https://example/#secret'])assert.throws(()=>endpoint(url));
await assert.rejects(resolveProvider(model,{...options,env:{OPENAI_API_KEY:'PRIVATE-CANARY',OPENAI_BASE_URL:'https://example/PRIVATE-CANARY'}}),/Credentials must not/);
process.stdout.write('PASS: provider freezing, credential rotation, endpoint validation, no unrelated credentials or secret leakage\n');
'''
        child = subprocess.run([str(runtime.ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'),
                               '--require', str(runtime.ROOT / 'benchmarks/scripts/offline-network.cjs'), '--input-type=module', '-e', script],
                               cwd=runtime.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(child.returncode, 0, child.stderr.decode())
        self.assertNotIn('PRIVATE-CANARY', child.stdout.decode())


if __name__ == '__main__':
    output = runtime.ROOT / 'benchmarks/config/runtime-attestation-tests.json'
    output.write_text(json.dumps({'schema_version': 1, 'passed': False, 'checks': 0, 'model_api_calls': 0}) + '\n')
    names = set(runtime.CONTROL + ['benchmarks/scripts/check_attestation.py', 'benchmarks/scripts/runtime_storage.py', 'benchmarks/scripts/action_cache.py',
        'benchmarks/scripts/cache.py', 'benchmarks/scripts/run.py', 'benchmarks/config/runtime-attestation-migration.json'])
    for spec in runtime.config()['components'].values():
        names.update(p for p in spec['paths'] if p.startswith('benchmarks/'))
    started = {name: runtime.sha((runtime.ROOT / name).read_bytes()) for name in sorted(names)}
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        result = unittest.main(verbosity=2, exit=False).result
    if result.wasSuccessful():
        assert started == {name: runtime.sha((runtime.ROOT / name).read_bytes()) for name in sorted(names)}, 'Sources changed during attestation checks; rerun offline validation'
        receipt = {'schema_version': 1, 'passed': True, 'checks': result.testsRun, 'model_api_calls': 0,
                   'files_sha256': started}
        output.write_text(json.dumps(receipt, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
