#!/usr/bin/python3
"""Focused native source-bridge controls; synthetic inputs, no network or provider."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import action_cache
import cache
import records


class NativeCacheBridgeChecks(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='native-cache-bridge-')))
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')))
        self.stack.enter_context(patch.object(action_cache.Cache, 'publish', side_effect=AssertionError('CAS publication forbidden')))
        self.stack.enter_context(patch.object(action_cache.Cache, 'commit', side_effect=AssertionError('AC mutation forbidden')))
        dependencies, inference = action_cache.dependencies, action_cache.inference
        self.stack.enter_context(patch.object(action_cache, 'dependencies', side_effect=lambda *a, **k: dependencies(*a, **{**k, 'root': self.root})))
        self.stack.enter_context(patch.object(action_cache, 'inference', side_effect=lambda *a, **k: inference(*a, **{**k, 'root': self.root})))
        self.stack.enter_context(patch.object(records, 'ROOT', self.root))
        self.stack.enter_context(patch.object(records, 'BENCH', self.root / 'benchmarks'))

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def fixture(self, harness='deepseek-harness'):
        adapter = 'benchmarks/harnesses/' + ('deepseek-harness.mjs' if harness == 'deepseek-harness' else 'zcode.py')
        self.adapter = adapter
        before, after = b'original native adapter\n', b'reviewed native adapter\n'
        delivery = b'{"dsh":{},"zcode":{}}'
        self.write(adapter, after)
        self.write('archive/adapter', before)
        self.write('benchmarks/config/evidence-delivery.json', delivery)
        self.write('benchmarks/config/environment-profile-archives.json', b'{"profiles":{}}')
        self.write('benchmarks/config/action-dependencies.json', json.dumps({'schema_version': 1,
            'inference': {'common': [], 'harness': {harness: [adapter]}}}).encode())
        self.migration = {'schema_version': 1, 'changes': {adapter: {
            'before_sha256': hashlib.sha256(before).hexdigest(),
            'after_sha256': hashlib.sha256(after).hexdigest(), 'archive': 'archive/adapter'}}}
        self.write('benchmarks/config/runtime-attestation-migration.json', json.dumps(self.migration).encode())
        inputs = {'prompt.txt': b'Recover behavior.', 'target': b'ELF fixture',
                  'tools.json': b'{"tools":[{"name":"decompile"}]}', 'manifest.json': b'{}'}
        hashes = {adapter: hashlib.sha256(after).hexdigest(),
                  'benchmarks/config/evidence-delivery.json': hashlib.sha256(delivery).hexdigest()}
        identity = {'schema_version': 2, 'experiment': 'fixture', 'case': 'rust',
            'condition': 'fixture', 'language': 'rust', 'model': {'id': 'fixture', 'reasoning': 'high'},
            'environment': {'id': 'fixture', 'harness': harness, 'files_sha256': hashes},
            **{key: hashlib.sha256(inputs[name]).hexdigest() for key, name in
               [('prompt', 'prompt.txt'), ('binary', 'target'), ('tools', 'tools.json'), ('manifest', 'manifest.json')]},
            'limits': [4, 30, 100], 'runtime': {'schema_version': 3,
                'harness_version': 'fixture', 'tool_timeout_ms': 1000, 'data_limits': {}}}
        expected = {'track': 'complex', 'inference_identity': identity}
        recorded = copy.deepcopy(identity)
        recorded['environment']['files_sha256'][adapter] = hashlib.sha256(before).hexdigest()
        original = action_cache.inference(recorded, inputs,
            recorded_files={adapter: before, 'benchmarks/config/evidence-delivery.json': delivery}, trial='fixture/trial')
        return expected, recorded, inputs, original

    def bridge(self, expected, recorded, inputs):
        return cache.historical_action(expected, recorded, inputs, 'fixture/trial')

    def test_two_native_harnesses_reproduce_original_action_without_writes(self):
        for harness in ('deepseek-harness', 'zcode'):
            with self.subTest(harness=harness):
                expected, recorded, inputs, original = self.fixture(harness)
                before = copy.deepcopy((expected, recorded, inputs))
                files = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                actual = self.bridge(expected, recorded, inputs)
                self.assertEqual(actual.descriptor, original.descriptor)
                self.assertEqual(actual.key, original.key)
                self.assertEqual(before, (expected, recorded, inputs))
                self.assertEqual(files, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_zero_missing_and_malformed_tool_catalogues_are_rejected(self):
        for raw in (b'{"tools":[]}', b'{}', b'{"tools":"decompile"}', b'{"tools":[{}]}', b'bad'):
            with self.subTest(raw=raw):
                expected, recorded, inputs, _ = self.fixture()
                inputs['tools.json'] = raw
                with self.assertRaisesRegex(ValueError, 'nonempty tool catalogue'):
                    self.bridge(expected, recorded, inputs)

    def test_seeded_and_withheld_paths_are_rejected(self):
        mutations = [lambda e, r, i: e.update(analysis_context_policy='declared-seeds-v1'),
            lambda e, r, i: e.update(evidence_access='premises_only'),
            lambda e, r, i: i.update({'analysis.seeds': b'0x1000'}),
            lambda e, r, i: i.update(target=b''),
            lambda e, r, i: e['inference_identity']['runtime'].update(analysis_context={}),
            lambda e, r, i: r.update(analysis_context_policy='declared-seeds-v1')]
        for number, mutation in enumerate(mutations):
            with self.subTest(number=number):
                expected, recorded, inputs, _ = self.fixture()
                mutation(expected, recorded, inputs)
                with self.assertRaisesRegex(ValueError, 'unseeded native binary'):
                    self.bridge(expected, recorded, inputs)

    def test_attested_paths_are_rejected(self):
        for location in ('expected', 'recorded', 'task'):
            with self.subTest(location=location):
                expected, recorded, inputs, _ = self.fixture()
                if location == 'task':
                    expected['runtime_attestation'] = {'identity': 'fixture'}
                else:
                    (recorded if location == 'recorded' else expected['inference_identity'])['attestation'] = {'identity': 'fixture'}
                with self.assertRaisesRegex(ValueError, 'cannot certify an attested action'):
                    self.bridge(expected, recorded, inputs)

    def test_typed_or_unknown_tracks_are_rejected(self):
        for track in ('capability', 'new-track', None):
            with self.subTest(track=track):
                expected, recorded, inputs, _ = self.fixture()
                expected['track'] = track
                with self.assertRaisesRegex(ValueError, 'unseeded native binary'):
                    self.bridge(expected, recorded, inputs)

    def test_genuinely_changed_inputs_never_match_the_old_action(self):
        for kind in ('prompt', 'binary', 'tools', 'manifest', 'model', 'limit', 'runtime'):
            with self.subTest(kind=kind):
                expected, recorded, inputs, original = self.fixture()
                identity = expected['inference_identity']
                if kind in ('prompt', 'binary', 'tools', 'manifest'):
                    name = {'prompt': 'prompt.txt', 'binary': 'target', 'tools': 'tools.json', 'manifest': 'manifest.json'}[kind]
                    inputs[name] = b'{"tools":[{"name":"strings"}]}' if kind == 'tools' else inputs[name] + b' '
                    identity[kind] = hashlib.sha256(inputs[name]).hexdigest()
                elif kind == 'model':
                    identity['model']['reasoning'] = 'low'
                elif kind == 'limit':
                    identity['limits'][0] += 1
                else:
                    identity['runtime']['tool_timeout_ms'] += 1
                self.assertNotEqual(self.bridge(expected, recorded, inputs).key, original.key)

    def test_unreviewed_later_adapter_revision_does_not_match(self):
        expected, recorded, inputs, original = self.fixture()
        later = b'unreviewed adapter revision\n'
        self.write(self.adapter, later)
        expected['inference_identity']['environment']['files_sha256'][self.adapter] = hashlib.sha256(later).hexdigest()
        self.assertNotEqual(self.bridge(expected, recorded, inputs).key, original.key)

    def test_corrupt_original_archive_is_rejected(self):
        expected, recorded, inputs, _ = self.fixture()
        self.write('archive/adapter', b'corruption')
        with self.assertRaisesRegex(ValueError, 'source archive changed'):
            self.bridge(expected, recorded, inputs)


if __name__ == '__main__':
    unittest.main(verbosity=2)
