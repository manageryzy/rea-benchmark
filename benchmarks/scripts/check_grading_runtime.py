#!/usr/bin/python3
"""Offline controls for closed grading images and their real execution boundary."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from grading import native, runtime
from grading import image_manifest as toolchains
from grading.offline import guard


class ImageControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='grading-image-controls-')
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name) / 'cache'
        self.cache.mkdir()
        self.payloads = {'usr/bin/gcc': b'compiler', 'usr/bin/bwrap': b'executor',
                         'usr/bin/python3': b'interpreter', 'usr/lib64/ld-linux-x86-64.so.2': b'loader',
                         'usr/lib/libc.so.6': b'library'}
        self.manifest = {
            'schema_version': 1, 'language': 'c', 'policy_sha256': toolchains.digest(toolchains.policy('c')),
            'compiler': {'command': '/usr/bin/gcc', 'version': 'gcc 13.3.0',
                         'executable_sha256': hashlib.sha256(b'compiler').hexdigest()},
            'files': {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'mode': 0o555, 'elf': True}
                      for name, raw in self.payloads.items()},
            'directories': ['usr', 'usr/bin', 'lib', 'lib64', 'opt', 'empty/nested'],
            'loader': 'usr/lib64/ld-linux-x86-64.so.2', 'root_aliases': {'lib': 'usr/lib', 'lib64': 'usr/lib64'}}

    def archive(self, *, extra=None, omit=None, payloads=None):
        archive = self.cache / 'fixture.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            for name, raw in (payloads or self.payloads).items():
                if name == omit:
                    continue
                member = tarfile.TarInfo('blobs/' + self.manifest['files'][name]['sha256'])
                member.size = len(raw)
                stream.addfile(member, io.BytesIO(raw))
            if extra is not None:
                member = tarfile.TarInfo(extra)
                member.size = 1
                stream.addfile(member, io.BytesIO(b'x'))
        archive_sha = toolchains.file_sha(archive)
        target = self.cache / 'archives' / (archive_sha + '.tar.gz')
        target.parent.mkdir(exist_ok=True)
        archive.replace(target)
        record = {'id': toolchains.digest(self.manifest), 'manifest': copy.deepcopy(self.manifest),
                  'archive_sha256': archive_sha, 'files': len(self.payloads),
                  'bytes': sum(map(len, self.payloads.values()))}
        return record

    def test_exact_image_and_directory_closure(self):
        record = self.archive()
        root = Path(self.temporary.name) / 'image'
        runtime.extract(record, root, cache=self.cache)
        runtime.verify_image(root, record['manifest'])
        (root / 'empty/nested').rmdir()
        with self.assertRaisesRegex(ValueError, 'directory'):
            runtime.verify_image(root, record['manifest'])

    def test_archive_digest_and_payload_digest(self):
        record = self.archive()
        archive = self.cache / 'archives' / (record['archive_sha256'] + '.tar.gz')
        archive.write_bytes(archive.read_bytes() + b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Corrupt'):
            runtime.extract(record, Path(self.temporary.name) / 'bad-hash', cache=self.cache)
        changed = dict(self.payloads, **{'usr/bin/gcc': b'COMPILER'})
        record = self.archive(payloads=changed)
        with self.assertRaisesRegex(ValueError, 'payload mismatch'):
            runtime.extract(record, Path(self.temporary.name) / 'bad-bytes', cache=self.cache)

    def test_unexpected_missing_and_traversal_members(self):
        for index, name in enumerate(('../escape', '/escape', 'blobs/unknown')):
            record = self.archive(extra=name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                runtime.extract(record, Path(self.temporary.name) / str(index), cache=self.cache)
        record = self.archive(omit='usr/bin/gcc')
        with self.assertRaisesRegex(ValueError, 'Missing'):
            runtime.extract(record, Path(self.temporary.name) / 'missing', cache=self.cache)

    def test_archive_links_and_duplicate_blobs_are_refused(self):
        for index, kind in enumerate((tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.REGTYPE)):
            record = self.archive()
            archive = self.cache / 'malformed.tar.gz'
            name = 'blobs/' + self.manifest['files']['usr/bin/gcc']['sha256']
            with tarfile.open(archive, 'w:gz') as stream:
                member = tarfile.TarInfo(name)
                if kind == tarfile.REGTYPE:
                    member.size = len(b'compiler')
                    stream.addfile(member, io.BytesIO(b'compiler'))
                member = tarfile.TarInfo(name)
                member.type, member.linkname = kind, '/usr/bin/gcc'
                stream.addfile(member)
            record['archive_sha256'] = toolchains.file_sha(archive)
            archive.replace(self.cache / 'archives' / (record['archive_sha256'] + '.tar.gz'))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                runtime.extract(record, Path(self.temporary.name) / ('links-' + str(index)), cache=self.cache)

    def test_metadata_paths_and_language_are_validated(self):
        record = self.archive()
        for key, bad in [('archive_sha256', '../outside'), ('files', 0)]:
            value = copy.deepcopy(record)
            value[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                toolchains.validate_record(value, 'c')
        for key, bad in [('loader', '../loader'), ('root_aliases', {'lib': '../../host'}),
                         ('language', 'rust'), ('directories', ['../outside'])]:
            value = copy.deepcopy(record)
            value['manifest'][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                toolchains.validate_record(value, 'c')
        for name in (None, '', '.', '/a', '../a', 'a/../b', 'a//b', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                toolchains.relative(name)

    def test_compiler_library_loader_and_executor_affect_identity(self):
        record = self.archive()
        original = record['id']
        for name in self.payloads:
            changed = copy.deepcopy(record['manifest'])
            changed['files'][name]['sha256'] = hashlib.sha256(b'changed').hexdigest()
            self.assertNotEqual(original, toolchains.digest(changed), name)
        # Compression, archive storage paths and capture bookkeeping are not inputs.
        changed = {**record, 'archive_sha256': '0' * 64}
        self.assertEqual(original, toolchains.digest(changed['manifest']))

    def test_extra_files_symlinks_modes_and_timestamps_refused(self):
        record = self.archive()
        root = Path(self.temporary.name) / 'image'
        runtime.extract(record, root, cache=self.cache)
        extra = root / 'unexpected'
        extra.write_bytes(b'outside inventory')
        with self.assertRaisesRegex(ValueError, 'extra or missing'):
            runtime.verify_image(root, record['manifest'])
        extra.unlink()
        compiler = root / 'usr/bin/gcc'
        compiler.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'mode'):
            runtime.verify_image(root, record['manifest'])
        compiler.chmod(0o555)
        os.utime(compiler, (1, 1))
        with self.assertRaisesRegex(ValueError, 'timestamp'):
            runtime.verify_image(root, record['manifest'])
        compiler.unlink()
        compiler.symlink_to('/usr/bin/gcc')
        with self.assertRaisesRegex(ValueError, 'bytes'):
            runtime.verify_image(root, record['manifest'])

    def test_cached_image_drift_before_and_after_execution(self):
        record = self.archive()
        with patch.object(toolchains, 'load', return_value=record), \
             patch.object(runtime, 'extract', side_effect=lambda value, root: original_extract(value, root, cache=self.cache)):
            for name in self.payloads:
                with runtime.image('c') as (root, _):
                    component = root / name
                component.chmod(0o755)
                component.write_bytes(b'X' * len(self.payloads[name]))
                component.chmod(0o555)
                os.utime(component, (0, 0))
                with self.subTest(component=name), self.assertRaisesRegex(ValueError, 'before execution'):
                    with runtime.image('c'):
                        self.fail('Changed image was admitted')
                temporary, _, _ = runtime._images.pop(record['id'])
                temporary.cleanup()
            with self.assertRaisesRegex(ValueError, 'during execution'):
                with runtime.image('c') as (root, _):
                    (root / 'unexpected').write_bytes(b'changed')
            temporary, _, _ = runtime._images.pop(record['id'])
            temporary.cleanup()

    def test_active_profile_identity_cannot_escape_cache(self):
        (self.cache / 'active.json').write_text(json.dumps({'c': {
            'id': '../outside', 'policy_sha256': toolchains.digest(toolchains.policy('c'))}}))
        with self.assertRaisesRegex(ValueError, 'active grading image identity'):
            toolchains.load('c', cache=self.cache)

    def test_language_policy_and_unrelated_source_isolation(self):
        config = json.loads(toolchains.CONFIG.read_bytes())
        path = Path(self.temporary.name) / 'config.json'
        path.write_text(json.dumps(config))
        before_cpp, before_c = toolchains.digest(toolchains.policy('cpp')), toolchains.digest(toolchains.policy('c'))
        config['languages']['c']['version'] = 'different'
        path.write_text(json.dumps(config))
        with patch.object(toolchains, 'CONFIG', path):
            self.assertEqual(before_cpp, toolchains.digest(toolchains.policy('cpp')))
            self.assertNotEqual(before_c, toolchains.digest(toolchains.policy('c')))
        names = action_cache.dependencies('grading', language='cpp')
        self.assertNotIn('benchmarks/scripts/grading/toolchains.py', names)
        self.assertNotIn('benchmarks/scripts/report.py', names)
        self.assertIn('benchmarks/scripts/grading/runtime.py', names)
        self.assertIn('benchmarks/scripts/grading/image_manifest.py', names)
        self.assertNotIn('benchmarks/scripts/grading/go_imports.go', names)


original_extract = runtime.extract


class ExecutionControls(unittest.TestCase):
    def test_all_pinned_runtimes_pass_real_compilation_and_execution(self):
        with guard():
            for language in ('c', 'cpp', 'rust', 'go'):
                with self.subTest(language=language):
                    runtime.require_validation(language)
                    runtime.validate_profile(language)
                    record = toolchains.load(language)
                    manifest = record['manifest']
                    self.assertIn('usr/bin/python3', manifest['files'])
                    self.assertIn('usr/bin/bwrap', manifest['files'])
                    self.assertFalse(any('__pycache__' in name for name in manifest['files']))

    def test_candidate_cannot_read_host_files_or_worker_sources(self):
        with tempfile.TemporaryDirectory(prefix='private-host-marker-') as temporary:
            marker = Path(temporary) / 'private-marker'
            marker.write_bytes(b'private')
            code = '#include <stdio.h>\nint64_t reconstructed(const uint8_t *data, size_t size) {\n'
            for path in (str(marker), str(action_cache.ROOT / 'benchmarks/config/benchmark.json'), '/environment/manifest.json'):
                code += 'if (fopen(' + json.dumps(path) + ', "rb")) return 1;\n'
            code += 'return 0; }\n'
            cases = [{'hex': '00', 'point': 'visibility', 'category': 'directed', 'expected': 0}]
            with guard():
                result = native.evaluate(code, 'c', cases, 'basic', 'c11', None, Path(temporary) / 'artifacts')
            self.assertTrue(result['program_success'], result)
            self.assertEqual(result['compiler_identity']['scope'], 'closed_toolchain_python_and_executor_v1')
            self.assertEqual(result['grading_runtime']['id'], toolchains.load('c')['id'])

    def test_stale_control_receipt_prevents_execution(self):
        with patch.object(runtime, 'validation_sources', return_value={'changed-source': 'new'}):
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                runtime.require_validation('c')

    def test_offline_guard_rejects_non_runtime_commands_and_network(self):
        import socket
        import subprocess
        with guard():
            with self.assertRaisesRegex(RuntimeError, 'refused'):
                subprocess.run(['/usr/bin/python3', '-c', 'print(1)'])
            with self.assertRaisesRegex(RuntimeError, 'Network forbidden'):
                socket.create_connection(('127.0.0.1', 9))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    from grading import evidence
    evidence.save(result.testsRun)
    print('CLOSED GRADING RUNTIME: PASS;', result.testsRun, 'controls; zero model API calls')
