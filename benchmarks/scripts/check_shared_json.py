"""Focused shared-cache ownership and mutation checks; no model/build calls."""
import copy
import json
import operator
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from read_session import ReadSession
from shared_json import freeze, mutable_copy, ReadOnlyDict, ReadOnlyList


class SharedChecks(unittest.TestCase):
    def test_json_bytes_equality_and_alias_detachment(self):
        source = {'files': {'a': {'sha256': 'a' * 64, 'bytes': 12}},
                  'directories': ['usr', 'usr/bin'], 'nullable': None, 'elf': True}
        frozen = freeze(source)
        self.assertIsInstance(frozen, dict)
        self.assertIsInstance(frozen['directories'], list)
        self.assertEqual(source, frozen)
        self.assertEqual(json.dumps(source, sort_keys=True), json.dumps(frozen, sort_keys=True))
        source['files']['a']['bytes'] = 99
        source['directories'].append('changed')
        self.assertEqual(frozen['files']['a']['bytes'], 12)
        self.assertEqual(frozen['directories'], ['usr', 'usr/bin'])
        self.assertIs(freeze(frozen), frozen)

    def test_all_normal_mapping_and_sequence_mutators_are_refused(self):
        value = freeze({'files': {'a': {'bytes': 12}}, 'dirs': ['usr', 'bin']})
        mapping, sequence = value['files']['a'], value['dirs']
        mutations = [lambda: operator.setitem(mapping, 'bytes', 99),
            lambda: operator.delitem(mapping, 'bytes'), lambda: mapping.clear(),
            lambda: mapping.pop('bytes'), lambda: mapping.popitem(),
            lambda: mapping.setdefault('other', 1), lambda: mapping.update(bytes=2),
            lambda: operator.ior(mapping, {'x': 1}), lambda: mapping.__init__({'x': 1}),
            lambda: operator.setitem(sequence, 0, 'evil'),
            lambda: operator.setitem(sequence, slice(None), ['evil']),
            lambda: operator.delitem(sequence, 0), lambda: sequence.append('x'),
            lambda: sequence.extend(['x']), lambda: sequence.insert(0, 'x'),
            lambda: sequence.pop(), lambda: sequence.remove('usr'),
            lambda: sequence.reverse(), lambda: sequence.sort(), lambda: sequence.clear(),
            lambda: operator.iadd(sequence, ['x']), lambda: operator.imul(sequence, 2),
            lambda: sequence.__init__(['evil'])]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)), self.assertRaises(TypeError): mutation()
        self.assertEqual(value, {'files': {'a': {'bytes': 12}}, 'dirs': ['usr', 'bin']})

    def test_explicit_composition_copies_are_independent_and_mutable(self):
        value = freeze({'files': {'a': {'bytes': 12}}, 'directories': ['usr']})
        for clone in (mutable_copy(value), copy.deepcopy(value)):
            self.assertIs(type(clone), dict)
            clone['files']['a']['bytes'] = 99
            clone['files']['addon'] = {'bytes': 7}
            clone['directories'].append('new')
        self.assertEqual(value['files'], {'a': {'bytes': 12}})
        self.assertEqual(value['directories'], ['usr'])

    def test_non_json_and_cycles_are_refused(self):
        cyclic = {}; cyclic['self'] = cyclic
        with self.assertRaises(ValueError): freeze(cyclic)
        for value in ({1: 'key'}, {'custom': object()}, {'set': {1, 2}}):
            with self.assertRaises(TypeError): freeze(value)

    def test_shared_hit_avoids_deepcopy_and_detects_restored_mtime_writes(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            path = Path(tmp) / 'record.json'; path.write_text('{"value":[1]}')
            calls = []
            def load():
                calls.append(1); return json.loads(path.read_bytes())
            wrapped = session.memoize(load, shared=True)
            with patch('read_session.clone_cache_value', side_effect=AssertionError('shared deepcopy')):
                first = wrapped(); second = wrapped()
                self.assertIs(first, second)
                self.assertEqual(len(calls), 1)
                with self.assertRaises(TypeError): first['value'].append(2)
                stamp = path.stat(); path.write_text('{"value":[2]}')
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                third = wrapped()
                self.assertEqual(third['value'], [2]); self.assertIsNot(first, third)
                self.assertEqual(first['value'], [1])
                self.assertEqual(len(calls), 2)

    def test_shared_cache_keeps_symlink_and_failed_validation_guards(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            root = Path(tmp); a = root / 'a'; b = root / 'b'; link = root / 'link'
            a.write_text('{"ok":true}'); b.write_text('{"ok":false}'); link.symlink_to(a)
            def load():
                result = json.loads(link.read_bytes())
                if not result['ok']: raise ValueError('invalid metadata')
                return result
            wrapped = session.memoize(load, shared=True)
            first = wrapped(); self.assertIs(first, wrapped())
            link.unlink(); link.symlink_to(b)
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'invalid'): wrapped()
            self.assertFalse(session.pending)

    def test_same_function_cannot_mix_mutable_and_shared_cache_policies(self):
        with ReadSession({}) as session:
            def value(): return {'items': [1]}
            mutable = session.memoize(value)
            shared = session.memoize(value, shared=True)
            mutable()['items'].append(8)
            fixed = shared()
            with self.assertRaises(TypeError): fixed['items'].append(9)
            self.assertIs(shared(), fixed)
            self.assertEqual(mutable(), {'items': [1]})

    def test_mutable_apis_retain_defensive_copy_semantics(self):
        with ReadSession({}) as session:
            @session.memoize
            def result(): return {'items': [1]}
            original = result(); original['items'].append(9)
            hit = result(); hit['items'].append(3)
            self.assertEqual(result(), {'items': [1]})


if __name__ == '__main__': unittest.main()
