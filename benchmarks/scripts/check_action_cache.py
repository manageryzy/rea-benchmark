#!/usr/bin/python3
"""Action/CAS controls: declared-input mutations only, no providers or execution."""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import action_cache as ac
import usage
from grading import store, native


class ActionControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='action-controls-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        names = set()
        registry = json.loads(ac.REGISTRY.read_text())
        for stage in registry.values():
            if isinstance(stage, dict):
                names.update(stage.get('common', []))
                for dimension in ('language', 'harness', 'condition'):
                    for group in stage.get(dimension, {}).values():
                        names.update(group)
        names.update(['benchmarks/config/action-dependencies.json', 'benchmarks/config/evidence-delivery.json',
                      'benchmarks/config/evidence-paging.json'])
        names.update('benchmarks/scripts/report_assets/' + name for name in ['page.html', 'report.css', 'report.js'])
        names.add('benchmarks/suites/stl-native-v2/config/coverage.json')
        for name in names:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ac.ROOT / name, destination)
        self.public = {'prompt.txt': b'instructions', 'target': b'ELF fixture', 'tools.json': b'{}', 'manifest.json': b'{}'}

    def identity(self, harness='deepseek-harness'):
        names = ac.dependencies('inference', root=self.root, harness=harness, condition='paged-evidence-v2')
        names += ['benchmarks/config/evidence-delivery.json', 'benchmarks/config/evidence-paging.json']
        return {'experiment': 'fixture', 'case': 'case', 'condition': 'paged-evidence-v2', 'language': 'cpp',
                'model': {'id': 'fixture', 'harness': harness, 'reasoning': 'high'}, 'limits': [60, 1800, 128000],
                'runtime': {'tool_timeout_ms': 1000},
                'environment': {'harness': harness, 'files_sha256': {name: ac.digest((self.root / name).read_bytes())['hash'] for name in names}},
                **{key: ac.digest(self.public[name])['hash'] for name, key in [('prompt.txt', 'prompt'), ('target', 'binary'), ('tools.json', 'tools'), ('manifest.json', 'manifest')]}}

    def inference(self, harness='deepseek-harness'):
        return ac.inference(self.identity(harness), self.public, root=self.root, trial='runs/fixture')

    def grading(self, language='cpp', **changes):
        context = dict(protocol='fixture', language=language, track='complex', vectors_sha256='frozen',
                       standard='c++20', string_abi=1, compiler={'sha256': 'compiler'}, limits={'execute_seconds': 20}, trial='runs/fixture')
        context.update(changes)
        return ac.grading(context, 'candidate', [{'expected': 'value'}], root=self.root)

    def test_canonical_merkle_and_command(self):
        first = ac.Action('test', {'b/input': b'b', 'a': b'a'}, arguments=['tool'], environment={'B': '2', 'A': '1'})
        same = ac.Action('test', {'a': b'a', 'b/input': b'b'}, arguments=['tool'], environment={'A': '1', 'B': '2'})
        self.assertEqual(first.key, same.key)
        self.assertEqual(first.key, ac.digest(ac.canonical(first.descriptor))['hash'])
        for changed in [ac.Action('test', {'a': b'changed', 'b/input': b'b'}),
                        ac.Action('test', {'a': b'a', 'b/input': b'b'}, arguments=['other']),
                        ac.Action('test', {'a': b'a', 'b/input': b'b'}, platform={'cpu': 'other'}),
                        ac.Action('test', {'a': b'a', 'b/input': b'b'}, timeout_seconds=1)]:
            self.assertNotEqual(first.key, changed.key)
        with self.assertRaises(ValueError):
            ac.Action('test', {'a': b'a', 'a/b': b'b'})

    def test_unrelated_files_and_registry_entries(self):
        infer, grade = self.inference().key, self.grading().key
        for name in ['benchmarks/scripts/report.py', 'benchmarks/scripts/grading/store.py',
                     'benchmarks/scripts/grading/offline.py', 'benchmarks/scripts/run.py',
                     'benchmarks/scripts/grading/toolchains.py', 'benchmarks/scripts/grading/evidence.py',
                     'benchmarks/scripts/action_cache.py', 'benchmarks/scripts/check_action_cache.py', 'README.md']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Unrelated mutation')
            self.assertEqual(infer, self.inference().key, name)
            self.assertEqual(grade, self.grading().key, name)
        registry = json.loads((self.root / ac.REGISTRY.relative_to(ac.ROOT)).read_text())
        registry['report']['common'].append('README.md')
        (self.root / ac.REGISTRY.relative_to(ac.ROOT)).write_text(json.dumps(registry))
        self.assertEqual(infer, self.inference().key)
        self.assertEqual(grade, self.grading().key)

    def test_language_and_harness_dependencies(self):
        cpp, go = self.grading().key, self.grading('go').key
        helper = self.root / 'benchmarks/scripts/grading/go_imports.go'
        helper.write_bytes(helper.read_bytes() + b'\n// mutation\n')
        self.assertEqual(cpp, self.grading().key)
        self.assertNotEqual(go, self.grading('go').key)
        self.assertEqual(self.grading('go', standard='Go').key, self.grading('go', standard='2021').key)
        dsh, zcode = self.inference().key, self.inference('zcode').key
        path = self.root / 'benchmarks/config/evidence-delivery.json'
        delivery = json.loads(path.read_text())
        delivery['zcode']['fixture_change'] = True
        path.write_text(json.dumps(delivery))
        self.assertEqual(dsh, self.inference().key)
        self.assertNotEqual(zcode, self.inference('zcode').key)
        self.assertEqual(cpp, self.grading().key)
        admission = self.root / 'benchmarks/scripts/grading/image_manifest.py'
        admission.write_bytes(admission.read_bytes() + b'\n# Admission policy mutation\n')
        self.assertNotEqual(cpp, self.grading().key)
        admitted_cpp = self.grading().key
        self.assertEqual(dsh, self.inference().key)
        gateway = self.root / 'benchmarks/scripts/rea-gateway.mjs'
        gateway.write_bytes(gateway.read_bytes() + b'\n// mutation\n')
        self.assertNotEqual(dsh, self.inference().key)
        self.assertEqual(admitted_cpp, self.grading().key)

    def test_semantic_inputs_invalidate_only_own_stage(self):
        original = self.inference().key
        for key in ['limits', 'model', 'runtime']:
            changed = self.identity()
            changed[key] = [61, 1800, 128000] if key == 'limits' else {**changed[key], 'mutation': True}
            self.assertNotEqual(original, ac.inference(changed, self.public, root=self.root, trial='runs/fixture').key)
        self.public['prompt.txt'] += b' revised'
        self.assertNotEqual(original, self.inference().key)

    def test_attested_and_historical_actions_never_alias(self):
        identity = self.identity()
        old = self.inference().key
        identity['attestation'] = {'runtime': {'identity': 'runtime-v1'}, 'provider': {'provider': 'openai'}}
        attested = ac.inference(identity, self.public, root=self.root, trial='runs/fixture').key
        self.assertNotEqual(old, attested)
        for change in ({'runtime': {'identity': 'runtime-v2'}, 'provider': {'provider': 'openai'}},
                       {'runtime': {'identity': 'runtime-v1'}, 'provider': {'provider': 'openai-codex'}}):
            identity['attestation'] = change
            self.assertNotEqual(attested, ac.inference(identity, self.public, root=self.root, trial='runs/fixture').key)
        grade = self.grading().key
        for changes in [{'standard': 'c++14'}, {'string_abi': 0}, {'compiler': {'sha256': 'other'}}, {'trial': 'different'}]:
            self.assertNotEqual(grade, self.grading(**changes).key)
        self.assertEqual(grade, self.grading().key)

    def test_verified_cas_restore_and_corruption(self):
        cache = ac.Cache(self.root / 'cache')
        action = ac.Action('fixture', {'input': b'same'})
        self.assertIsNone(cache.lookup(action))
        result = cache.commit(action, {'artifact/output': b'content'})
        self.assertEqual(result, cache.lookup(action))
        destination = self.root / 'restored'
        cache.restore(result, destination)
        self.assertEqual((destination / 'artifact/output').read_bytes(), b'content')
        with self.assertRaises(ValueError):
            cache.restore(result, destination)
        with self.assertRaises(ValueError):
            cache.commit(action, {'artifact/output': b'conflicting'})
        blob = result['output_files']['artifact/output']['digest']
        (cache.root / 'cas' / blob['hash']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Corrupt'):
            cache.lookup(action)
        for name in ['../escape', '/absolute', 'a//b', 'a/./b', 'a\\b', 1]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ac.relative(name)

    def test_reporting_and_accounting_do_not_invalidate_inference(self):
        before, grade = self.inference().key, self.grading().key
        report = ac.reporting({'result': 1}, root=self.root).key
        with patch.object(ac.sys, 'version', 'different Python runtime'):
            self.assertNotEqual(report, ac.reporting({'result': 1}, root=self.root).key)
            self.assertEqual(before, self.inference().key)
            self.assertEqual(grade, self.grading().key)
        css = self.root / 'benchmarks/scripts/report_assets/report.css'
        css.write_bytes(css.read_bytes() + b'\n/* visual edit */\n')
        self.assertNotEqual(report, ac.reporting({'result': 1}, root=self.root).key)
        self.assertEqual(before, self.inference().key)
        self.assertEqual(grade, self.grading().key)
        attempt = self.root / 'accounting-attempt'
        attempt.mkdir()
        receipt = {'model': 'fixture', 'reported_usage': {'inputTokens': 10, 'outputTokens': 2}}
        (attempt / 'result.json').write_text(json.dumps(receipt))
        first = usage.save(attempt, {'unit_tokens': 1000000}, {'low_usd': 0.1})
        # Other models and receipt presentation have no effect on this ledger.
        receipt['elapsed_seconds'] = 30
        (attempt / 'result.json').write_text(json.dumps(receipt))
        second = usage.save(attempt, {'unit_tokens': 1000000, 'other-model': {'price': 999}}, {'low_usd': 0.1})
        self.assertEqual(first, second)
        self.assertNotEqual(first, usage.save(attempt, {'unit_tokens': 1000}, {'low_usd': 100}))
        self.assertEqual(before, self.inference().key)
        self.assertEqual(grade, self.grading().key)

    def test_legacy_grade_compatibility_ignores_storage_only(self):
        previous = dict(version=store.VERSION, protocol='v2', language='cpp', track='complex',
                        candidate_source_sha256='source', vectors_sha256='vectors', standard='c++20', string_abi=1,
                        compiler={'hash': 'compiler'}, limits={'seconds': 20},
                        files_sha256={'scripts/grading/native.py': 'native', 'scripts/grading/store.py': 'old'})
        current = copy.deepcopy(previous)
        del current['files_sha256']['scripts/grading/store.py']
        self.assertTrue(store.compatible_legacy(previous, current))
        current['files_sha256']['scripts/grading/native.py'] = 'changed'
        self.assertFalse(store.compatible_legacy(previous, current))

    def test_legacy_migration_and_cas_restore_never_evaluate(self):
        directory = self.root / 'attempt'
        directory.mkdir()
        (directory / 'reconstructed.c').write_text('candidate')
        (directory / 'answer.md').write_text('```c\ncandidate\n```\n')
        original = {'compiled': True, 'passed': 1, 'total': 1, 'program_success': True}
        (directory / 'grade.json').write_text(json.dumps(original))
        task = {'language': 'c', 'track': 'basic'}
        local = ac.Cache(self.root / 'local-cache')
        with patch.object(store, 'vectors', return_value=([{'expected': 1}], 'fixture')), \
             patch.object(native, 'compiler_identity', return_value={'compiler': 'fixture'}), \
             patch.object(ac, 'Cache', return_value=local), \
             patch.object(native, 'evaluate', side_effect=AssertionError('Native evaluation forbidden')):
            identity = store.identity(directory, task)[0]
            identity.pop('trial')
            identity['files_sha256']['scripts/grading/store.py'] = 'legacy-storage-code'
            legacy_key = store.digest(identity)
            legacy = directory / 'grades' / store.VERSION / legacy_key
            legacy.mkdir(parents=True)
            (legacy / 'grade.json').write_text(json.dumps(original))
            manifest = {'identity': identity, 'grading_id': legacy_key, 'original_source': 'reconstructed.c',
                        'files_sha256': {'grade.json': store.sha(legacy / 'grade.json')}}
            (legacy / 'manifest.json').write_text(json.dumps(manifest))
            (directory / 'selected-grade.json').write_text(json.dumps({
                'path': str((legacy / 'grade.json').relative_to(directory)), 'grading_id': legacy_key,
                'sha256': store.sha(legacy / 'grade.json')}))
            migrated = store.grade(directory, task)
            self.assertNotEqual(legacy_key, migrated['grading_id'])
            selected = directory / store.load(directory / 'selected-grade.json')['path']
            self.assertEqual(store.load(selected.parent / 'manifest.json')['cache_schema'], 3)
            shutil.rmtree(selected.parent)
            (directory / 'selected-grade.json').unlink()
            self.assertEqual(migrated, store.grade(directory, task))
            self.assertEqual(store.load(directory / 'grade.json'), original)
            self.assertTrue((legacy / 'grade.json').exists())


class GradePublication(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='grade-publication-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.work = self.root/'stage'; self.work.mkdir()
        (self.work/'artifacts').mkdir()
        (self.work/'artifacts/candidate').write_bytes(b'fixture executable')
        (self.work/'artifacts/candidate').chmod(0o755)
        (self.work/'grade.json').write_text('{"passed":1,"total":1}\n')
        self.manifest = {'files_sha256': {str(p.relative_to(self.work)):store.sha(p)
            for p in self.work.rglob('*') if p.is_file()}}
        self.destination = self.root/'grades'/'record'

    def test_commit_last_without_directory_rename(self):
        linked = []; original = os.link
        def link(source, target):
            linked.append(Path(target).name)
            return original(source, target)
        with patch.object(Path, 'rename', side_effect=PermissionError('directory rename denied')), \
             patch.object(os, 'link', side_effect=link):
            store.publish(self.work, self.destination, self.manifest)
        self.assertEqual(linked[-1], 'grade.json')
        self.assertTrue((self.destination/'artifacts/candidate').stat().st_mode & 0o111)
        self.assertEqual(store.load(self.destination/'grade.json')['passed'], 1)

    def test_interrupted_publication_resumes_verified_files(self):
        original = os.link
        def fail_manifest(source, target):
            if Path(target).name == 'manifest.json': raise PermissionError('injected')
            return original(source, target)
        with patch.object(os, 'link', side_effect=fail_manifest), self.assertRaises(PermissionError):
            store.publish(self.work, self.destination, self.manifest)
        self.assertFalse((self.destination/'grade.json').exists())
        self.assertFalse(list(self.destination.rglob('.publish-*')))
        old_inode = (self.destination/'artifacts/candidate').stat().st_ino
        store.publish(self.work, self.destination, self.manifest)
        self.assertEqual((self.destination/'artifacts/candidate').stat().st_ino, old_inode)
        store.publish(self.work, self.destination, self.manifest)

    def test_deleted_source_is_explicit_and_not_published(self):
        shutil.rmtree(self.work)
        with self.assertRaises(FileNotFoundError):
            store.publish(self.work, self.destination, self.manifest)
        self.assertFalse(self.destination.exists())

    def test_changed_source_is_not_published(self):
        (self.work/'grade.json').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            store.publish(self.work, self.destination, self.manifest)
        self.assertFalse(self.destination.exists())

    def test_conflicting_partial_and_symlink_are_rejected(self):
        self.destination.mkdir(parents=True)
        (self.destination/'grade.json').write_text('unrelated')
        with self.assertRaisesRegex(ValueError, 'Conflicting partial'):
            store.publish(self.work, self.destination, self.manifest)
        self.assertEqual((self.destination/'grade.json').read_text(), 'unrelated')
        (self.destination/'grade.json').unlink()
        (self.destination/'artifacts').symlink_to(self.work/'artifacts', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Unexpected artifact'):
            store.publish(self.work, self.destination, self.manifest)

    def test_deleted_published_file_does_not_commit_grade(self):
        original = os.link
        def remove_artifact(source, target):
            result = original(source, target)
            if Path(target).name == 'manifest.json':
                (self.destination/'artifacts/candidate').unlink()
            return result
        with patch.object(os, 'link', side_effect=remove_artifact), self.assertRaises(FileNotFoundError):
            store.publish(self.work, self.destination, self.manifest)
        self.assertFalse((self.destination/'grade.json').exists())
        store.publish(self.work, self.destination, self.manifest)


if __name__ == '__main__':
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
         patch.object(subprocess, 'Popen', side_effect=AssertionError('Process/model execution forbidden')):
        unittest.main(verbosity=2)
