"""Offline controls for explicit baseline/delta provenance and grading publication."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache as ac
import incremental_validation as delta
from grading import store


class DeltaControls(unittest.TestCase):
    def test_platform_transition_must_be_exact_and_explicit(self):
        before = {'python': 'fixture', 'tools': 'host'}
        after = {'python': 'fixture', 'tools': 'pinned'}
        delta.check_platform(before, before, [])
        reviewed = [{'before': before, 'after': after}]
        delta.check_platform(before, after, reviewed)
        for actual in ({**after, 'tools': 'different'}, {**after, 'python': 'changed'}):
            with self.assertRaises(RuntimeError): delta.check_platform(before, actual, reviewed)
        with self.assertRaises(RuntimeError): delta.check_platform(before, after, [])

    def test_unknown_changed_added_removed_input_is_rejected(self):
        old = {'a': ac.digest(b'one')}; new = {'a': ac.digest(b'two')}
        review = {'a': {'before': old['a'], 'after': new['a']}}
        self.assertEqual(delta.compare_inputs(old, new, review), review)
        for other in ({}, {**new, 'extra': ac.digest(b'x')}, old):
            with self.assertRaises(RuntimeError): delta.compare_inputs(old, other, review)

    def test_baseline_requires_real_matching_cached_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = ac.Cache(Path(tmp)); common = ac.Action('deps', {'a': b'b'})
            cache.publish(common)
            action = ac.Action('check', {'dependencies.action.json': ac.canonical(common.descriptor), 'test.py': b'code'})
            cache.commit(action, {'check.json': ac.canonical({'test': 'test_one', 'action': action.key,
                'passed': True, 'model_api_calls': 0, 'provider_launches': 0})})
            receipt = {'schema_version': 2, 'passed': True, 'failed': 0, 'checks': 1,
                'model_api_calls': 0, 'provider_launches': 0, 'actions': {'test_one': action.key}}
            key = cache.put(ac.canonical(receipt))['hash']
            self.assertEqual(delta.baseline(cache, key)[0], receipt)
            (cache.root / 'ac' / action.key).unlink()
            with self.assertRaises(ValueError): delta.baseline(cache, key)

    def test_corrupt_or_nonpassing_baseline_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = ac.Cache(Path(tmp)); key = cache.put(ac.canonical({'schema_version': 2, 'passed': False}))['hash']
            with self.assertRaises(ValueError): delta.baseline(cache, key)
            (cache.root/'cas'/key).write_bytes(b'corrupt')
            with self.assertRaises(ValueError): delta.object_at(cache, key)

    def test_proof_binds_policy_and_implementation_bytes(self):
        original = {'policy': b'old', 'implementation': b'current'}
        first = ac.Action(delta.KIND, original)
        for name in original:
            self.assertNotEqual(first.key, ac.Action(delta.KIND, {**original, name: b'changed'}).key)


class PublicationControls(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rea-publish-control-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.work = self.root/'staging'; self.work.mkdir()
        self.dest = self.root/'record'
        self.payloads = {'grade.json': b'{"passed":1}', 'output.bin': b'\x00\xff\r\n'}
        for name, raw in self.payloads.items(): (self.work/name).write_bytes(raw)
        self.manifest = {'files_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in self.payloads.items()}}

    def publish(self): store.publish(self.work, self.dest, self.manifest)

    def test_exact_commit_and_idempotent_reuse(self):
        self.publish(); self.publish()
        for name, raw in self.payloads.items(): self.assertEqual((self.dest/name).read_bytes(), raw)

    def test_missing_staging_never_becomes_cache_hit(self):
        (self.work/'grade.json').unlink()
        with self.assertRaises(FileNotFoundError): self.publish()
        self.assertFalse(self.dest.exists())

    def test_corrupt_source_and_conflicting_destination_rejected(self):
        (self.work/'output.bin').write_bytes(b'bad')
        with self.assertRaises(ValueError): self.publish()
        (self.work/'output.bin').write_bytes(self.payloads['output.bin'])
        self.dest.mkdir(); (self.dest/'output.bin').write_bytes(b'conflict')
        with self.assertRaises(ValueError): self.publish()
        self.assertFalse((self.dest/'grade.json').exists())

    def test_resume_matching_partial_record_without_overwrite(self):
        self.dest.mkdir(); (self.dest/'output.bin').write_bytes(self.payloads['output.bin'])
        self.publish(); self.assertTrue((self.dest/'grade.json').is_file())

    def test_completion_marker_is_published_last(self):
        real = store.os.link; names = []
        def link(source, target): names.append(Path(target).name); return real(source, target)
        with patch.object(store.os, 'link', side_effect=link): self.publish()
        self.assertEqual(names[-1], 'grade.json')

    def test_symlink_destination_or_source_rejected(self):
        outside = self.root/'outside'; outside.mkdir(); self.dest.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError): self.publish()
        self.dest.unlink(); (self.work/'output.bin').unlink()
        (outside/'output.bin').write_bytes(self.payloads['output.bin'])
        (self.work/'output.bin').symlink_to(outside/'output.bin')
        with self.assertRaises(ValueError): self.publish()


if __name__ == '__main__': unittest.main()
