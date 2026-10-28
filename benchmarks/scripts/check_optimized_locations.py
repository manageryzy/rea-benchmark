#!/usr/bin/python3
"""Real GCC inline/clone/cold/thunk/LTO preparation; no model experiments."""
import copy
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import action_cache
from capabilities import elf
from capabilities.optimized_locations import artifact, build, dwarf, evidence, native, reference, review, scoring, tasks, runtime
from capabilities import config
import json
import tempfile
from grading import offline


class OptimizedLocationControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets, cls.reviews, cls.calibrations = {}, {}, {}
        for mode in ('optimized', 'lto'):
            cls.targets[mode] = build.build(mode)
            cls.reviews[mode] = review.inspect(cls.targets[mode], mode)
            cls.calibrations[mode] = native.calibrate(mode)

    def target(self, mode='optimized'):
        return self.targets[mode]

    def reader(self, mode='optimized'):
        return dwarf.Locations(self.target(mode))

    def mutated(self, address, byte, mode='optimized'):
        """Change final code and diagnostic bytes together to test semantics."""
        value = copy.deepcopy(self.target(mode))
        for key in ('private_hex', 'public_hex'):
            image = elf.ELF(bytes.fromhex(value[key]))
            raw = bytearray(image.raw)
            segment = next(p for p in image.programs if p[0] == 1 and p[3] <= image.base + address < p[3] + p[5])
            raw[segment[2] + image.base + address - segment[3]] = byte
            value[key] = raw.hex()
        image = elf.ELF(bytes.fromhex(value['private_hex']))
        pattern = re.compile(r'^(\s*)([0-9a-f]+):(\s+)((?:[0-9a-f]{2} )*[0-9a-f]{2})(\s+)(.+)$')
        lines = []
        for line in value['disassembly'].splitlines():
            match = pattern.match(line)
            if match:
                start = int(match[2], 16) - image.base
                size = len(bytes.fromhex(match[4]))
                if start <= address < start + size:
                    line = (match[1] + match[2] + ':' + match[3]
                            + image.bytes_at(start, size).hex(' ') + match[5] + match[6])
            lines.append(line)
        value['disassembly'] = '\n'.join(lines)
        return value

    def test_01_strip_preserves_exact_loadable_code(self):
        for target in self.targets.values():
            private, public = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
            self.assertEqual(private.allocated(), public.allocated())
            self.assertFalse(public.symbols())

    def test_02_real_originals_match_separate_reference_216_calls(self):
        count = 0
        for calibration in self.calibrations.values():
            for row in calibration['observations']:
                self.assertEqual(row['value'], reference.expected(row['selected'], row['nonce']))
                if row['selected'] >= 7:
                    self.assertNotEqual(row['value'], reference.expected(0, row['nonce']))
                count += 1
        self.assertEqual(count, 216)

    def test_03_actual_dwarf4_and_lto_dwarf5_units(self):
        self.assertEqual(self.reviews['optimized']['dwarf_versions'], [4])
        self.assertEqual(self.reviews['lto']['dwarf_versions'], [4, 5])
        for mode in self.targets:
            candidates = self.reader(mode).candidates()
            self.assertGreater(len(candidates), 20)
            self.assertTrue(all(row['ranges'] and row['semantic_reviewed'] is False for row in candidates))

    def test_04_two_separate_real_inline_instances(self):
        for value in self.reviews.values():
            parts = value['groups']['inline']
            self.assertEqual(len(parts), 2)
            self.assertNotEqual(parts[0]['ranges'][0]['start'], parts[1]['ranges'][0]['start'])
            self.assertTrue(all(bytes.fromhex(p['ranges'][0]['bytes_hex']) == review.arithmetic() for p in parts))

    def test_05_specialized_clone_accepts_only_equivalent_salt(self):
        for value in self.reviews.values():
            clone = value['groups']['clone'][0]['ranges'][0]
            self.assertEqual(bytes.fromhex(clone['bytes_hex']), review.arithmetic())
            self.assertNotEqual(clone['bytes_hex'], value['near_miss_ranges'][0]['bytes_hex'])

    def test_06_shared_clone_has_two_distinct_actual_roots(self):
        for value in self.reviews.values():
            paths = value['groups']['clone'][0]['entry_paths']
            self.assertEqual(len(paths), 2)
            self.assertNotEqual(paths[0]['start'], paths[1]['start'])

    def test_07_cold_path_includes_branch_fragment_and_helper(self):
        for value in self.reviews.values():
            parts = value['groups']['cold'][0]['ranges']
            self.assertEqual(len(parts), 3)
            self.assertLess(parts[1]['start'], parts[0]['start'])
            self.assertEqual(bytes.fromhex(parts[2]['bytes_hex']), review.arithmetic(cold=True))

    def test_08_thunk_equivalence_has_explicit_receiver_premise(self):
        for value in self.reviews.values():
            thunk = value['groups']['thunk'][0]
            self.assertEqual(thunk['this_adjustment'], -16)
            self.assertIn('owner_cookie_at_8_equals_salt', thunk['calling_premise'])
            self.assertEqual(len(thunk['ranges']), 2)

    def test_09_lto_erases_cross_unit_boundary_with_inline_origin(self):
        self.assertIn('cross_unit_boundary', self.reviews['optimized']['groups'])
        self.assertNotIn('lto', self.reviews['optimized']['groups'])
        self.assertIn('lto', self.reviews['lto']['groups'])
        self.assertNotIn('cross_unit_boundary', self.reviews['lto']['groups'])

    def test_10_rotation_near_miss_fails_semantic_review(self):
        part = self.reviews['optimized']['groups']['inline'][0]['ranges'][0]
        with self.assertRaisesRegex(ValueError, 'arithmetic'):
            review.inspect(self.mutated(part['start'] + 16, 9), 'optimized')

    def test_11_salt_near_miss_fails_semantic_review(self):
        part = self.reviews['optimized']['groups']['inline'][0]['ranges'][0]
        with self.assertRaisesRegex(ValueError, 'arithmetic'):
            review.inspect(self.mutated(part['start'] + 2, 0x8d), 'optimized')

    def test_12_wrong_cold_branch_rejected(self):
        part = self.reviews['optimized']['groups']['cold'][0]['ranges'][0]
        with self.assertRaisesRegex(ValueError, 'Cold partition'):
            review.inspect(self.mutated(part['start'] + 6, 0), 'optimized')

    def test_13_wrong_thunk_adjustment_rejected(self):
        part = self.reviews['optimized']['groups']['thunk'][0]['ranges'][0]
        with self.assertRaisesRegex(ValueError, 'thunk'):
            review.inspect(self.mutated(part['start'] + 3, 8), 'optimized')

    def test_14_debug_origin_cannot_invent_physical_ranges(self):
        reader = self.reader()
        candidate = next(row for row in reader.candidates() if row['inlined'])
        die = reader.dies[candidate['die']]
        die['attributes'] = {'abstract_origin': '<0x%x>' % next(row['die'] for row in reader.candidates() if not row['inlined'])}
        self.assertEqual(reader.ranges(candidate['die']), [])

    def test_15_duplicate_dwarf_die_rejected(self):
        target = copy.deepcopy(self.target())
        line = next(line for line in target['debug_info'].splitlines() if dwarf.DIE.match(line) and 'DW_TAG_compile_unit' in line)
        target['debug_info'] += '\n' + line
        with self.assertRaisesRegex(ValueError, 'DIE structure'):
            dwarf.Locations(target)

    def test_16_missing_and_cyclic_origins_rejected(self):
        for reference_value in ('<0xffffffff>', None):
            reader = self.reader()
            offset = next(row['die'] for row in reader.candidates() if row['inlined'])
            reader.dies[offset]['attributes']['abstract_origin'] = reference_value or ('<0x%x>' % offset)
            with self.assertRaisesRegex(ValueError, 'Cyclic or missing'):
                reader.attributes(offset)

    def test_17_dwarf_range_offsets_are_bounded(self):
        for mode in self.targets:
            reader = self.reader(mode)
            offset = next(offset for offset, row in reader.dies.items() if 'ranges' in row['attributes'])
            reader.dies[offset]['attributes']['ranges'] = '0xffffffff'
            with self.assertRaises(ValueError):
                reader.ranges(offset)

    def test_18_unsupported_dwarf5_range_opcodes_fail_closed(self):
        reader = self.reader('lto')
        offset = next(offset for offset, row in reader.dies.items() if 'ranges' in row['attributes']
                      and any(start <= offset < end and version == 5 for start, end, version in reader.units))
        index = dwarf.number(reader.dies[offset]['attributes']['ranges'])
        data = bytearray(reader.sections['.debug_rnglists'])
        data[index] = 255
        reader.sections['.debug_rnglists'] = bytes(data)
        with self.assertRaisesRegex(ValueError, 'opcode'):
            reader.ranges(offset)

    def test_19_cross_binary_disassembly_cannot_supply_review(self):
        target = {**self.target(), 'disassembly': self.target('lto')['disassembly']}
        with self.assertRaisesRegex(ValueError, 'Disassembly'):
            review.inspect(target, 'optimized')

    def test_20_cached_builds_and_native_calls_launch_no_workers(self):
        with patch.object(build.process, 'run', side_effect=AssertionError('Unexpected worker')):
            for mode in self.targets:
                self.assertTrue(build.build(mode)['cache_hit'])
                self.assertTrue(native.calibrate(mode)['cache_hit'])

    def test_21_private_vectors_and_review_do_not_enter_build_actions(self):
        for mode in self.targets:
            before = build.action(mode)[0].key
            original = Path.read_bytes
            unrelated = {build.ROOT / (build.MODULE + name) for name in ('reference.py', 'review.py', 'scoring.py')}
            unrelated.add(build.ROOT / 'benchmarks/scripts/report.py')

            def changed(path):
                data = original(path)
                return data + b'\n# unrelated control revision\n' if path in unrelated else data

            with patch.object(Path, 'read_bytes', changed):
                self.assertEqual(build.action(mode)[0].key, before)

            def changed_worker(path):
                data = original(path)
                return data + b'\n# selected worker revision\n' if path == build.ROOT / (build.MODULE + 'build_worker.py') else data

            with patch.object(Path, 'read_bytes', changed_worker):
                self.assertNotEqual(build.action(mode)[0].key, before)
            self.assertNotIn(build.MODULE + 'reference.py', build.SOURCES)
            self.assertNotIn(build.MODULE + 'review.py', build.SOURCES)
            self.assertNotIn(build.MODULE + 'dwarf.py', build.SOURCES)

    def test_22_old_compiler_identity_is_not_replaced_by_lto_addon(self):
        plain, lto = build.action('optimized')[3], build.action('lto')[3]
        self.assertEqual(plain['compiler_image'], lto['compiler_image'])
        self.assertNotIn('lto_image_id', plain)
        self.assertEqual(lto['lto_image_id'], evidence.pins()['lto_image_id'])

    def test_23_source_bound_receipt_cannot_be_a_release(self):
        for value in self.reviews.values():
            self.assertIs(value['independent_review'], False)
            self.assertIs(value['release_accepted'], False)
        with self.assertRaises(ValueError):
            review.inspect(self.target(), 'unknown')

    def test_24_integer_and_transfer_bounds(self):
        for salt, shift in ((True, 7), (-1, 7), (2**64, 7), (0, True), (0, 0), (0, 64)):
            with self.assertRaises(ValueError):
                review.arithmetic(salt, shift)
        for raw in (b'', b'\xc3', b'\xeb', b'\xe9\0', b'\xe8\0\0\0\0'):
            with self.assertRaises(ValueError):
                review.branch(raw, 4096)
        with self.assertRaises(ValueError):
            native.calibrate('optimized', nonces=[True])

    def test_25_all_reviewed_realizations_and_optional_return_context(self):
        for mode, target in self.targets.items():
            private = scoring.truth(target, mode)
            value = scoring.answer(private)
            result = scoring.grade(action_cache.canonical(value), private)
            self.assertTrue(result['complete'])
            self.assertEqual(result['f1'], 1)
            self.assertEqual(result['complete_instances'], 6)
            image = elf.ELF(bytes.fromhex(target['private_hex']))
            for instance in value['instances']:
                for part in instance['ranges']:
                    if image.bytes_at(part['end'] - 1, 1) == b'\xc3':
                        part['end'] -= 1
            value['instances'].reverse()
            for instance in value['instances']:
                instance['ranges'].reverse()
            self.assertTrue(scoring.grade(action_cache.canonical(value), private)['complete'])

    def test_26_overclaims_and_near_misses_reduce_precision(self):
        private = scoring.truth(self.target(), 'optimized')
        for part in self.reviews['optimized']['near_miss_ranges']:
            value = scoring.answer(private)
            value['instances'].append({'ranges': [{'start': part['start'], 'end': part['end']}]})
            result = scoring.grade(action_cache.canonical(value), private)
            self.assertFalse(result['complete'])
            self.assertLess(result['precision'], 1)
            self.assertEqual(result['coverage'], 1)

    def test_27_partial_cold_and_missing_adjustment_are_partial(self):
        private = scoring.truth(self.target(), 'optimized')
        for kind, index in (('cold', 1), ('thunk', 0)):
            value = copy.deepcopy(scoring.answer(private))
            row = next(i for i, r in enumerate(private['realizations']) if r['kind'] == kind)
            del value['instances'][row]['ranges'][index]
            result = scoring.grade(action_cache.canonical(value), private)
            self.assertFalse(result['complete'])
            self.assertEqual(result['complete_instances'], 5)
            self.assertTrue(0 < result['coverage'] < 1)

    def test_28_one_instance_cannot_satisfy_multiple_realizations(self):
        private = scoring.truth(self.target(), 'optimized')
        value = scoring.answer(private)
        a, b = value['instances'][:2]
        value['instances'] = [{'ranges': a['ranges'] + b['ranges']}] + value['instances'][2:]
        result = scoring.grade(action_cache.canonical(value), private)
        self.assertFalse(result['complete'])
        self.assertLess(result['coverage'], 1)
        self.assertEqual(result['complete_instances'], 4)

    def test_29_misaligned_and_outside_code_ranges_do_not_pass(self):
        private = scoring.truth(self.target(), 'optimized')
        value = scoring.answer(private)
        value['instances'][0]['ranges'][0]['start'] += 1
        result = scoring.grade(action_cache.canonical(value), private)
        self.assertFalse(result['complete'])
        self.assertLess(result['precision'], 1)
        self.assertLess(result['coverage'], 1)
        value = scoring.answer(private)
        value['instances'].append({'ranges': [{'start': 0, 'end': 8}]})
        self.assertLess(scoring.grade(action_cache.canonical(value), private)['precision'], 1)

    def test_30_wrong_binary_and_blind_cross_build_locations_fail(self):
        private = scoring.truth(self.target('lto'), 'lto')
        value = scoring.answer(scoring.truth(self.target(), 'optimized'))
        with self.assertRaisesRegex(ValueError, 'another binary'):
            scoring.grade(action_cache.canonical(value), private)
        value['binary_sha256'] = private['binary_sha256']
        self.assertFalse(scoring.grade(action_cache.canonical(value), private)['complete'])

    def test_31_malformed_duplicate_and_unbounded_artifacts_rejected(self):
        gold = scoring.answer(scoring.truth(self.target(), 'optimized'))
        cases = []
        for key, value in (('schema_version', True), ('schema_version', 2), ('binary_sha256', 'x' * 64),
                           ('instances', [{}] * 129)):
            cases.append({**gold, key: value})
        value = copy.deepcopy(gold)
        value['instances'].append(value['instances'][0])
        cases.append(value)
        for part in ({'start': True, 'end': 10}, {'start': -1, 'end': 10}, {'start': 0, 'end': 4097},
                     {'start': 2, 'end': 1}, {'start': 0, 'end': 2**48}):
            cases.append({**gold, 'instances': [{'ranges': [part]}]})
        cases.append({**gold, 'instances': [{'ranges': [{'start': 1, 'end': 9}, {'start': 5, 'end': 10}]}]})
        for value in cases:
            with self.assertRaises(ValueError):
                artifact.admit(action_cache.canonical(value))
        with self.assertRaises(ValueError):
            artifact.admit(b'{"schema_version":1,"schema_version":1}')
        self.assertEqual(scoring.grade(action_cache.canonical({**gold, 'instances': []}),
                                      scoring.truth(self.target(), 'optimized'))['f1'], 0)

    def test_32_artifact_does_not_require_unobservable_compiler_history(self):
        private = scoring.truth(self.target(), 'optimized')
        value = scoring.answer(private)
        self.assertTrue(all(set(row) == {'ranges'} for row in value['instances']))
        self.assertIn('Do not guess original symbol names or compiler transformation labels', scoring.prompt())
        for target in self.targets.values():
            for name in elf.ELF(bytes.fromhex(target['private_hex'])).symbols():
                if name.startswith(('operation_', '_ZL15clone_')):
                    self.assertNotIn(name, scoring.prompt())

    def test_33_verified_receiver_wrapper_and_dispatch_are_valid_context(self):
        for mode, target in self.targets.items():
            private = scoring.truth(target, mode)
            value = scoring.answer(private)
            index = next(i for i, row in enumerate(private['realizations']) if row['kind'] == 'thunk')
            paths = self.reviews[mode]['groups']['thunk'][0]['entry_paths']
            value['instances'][index]['ranges'] += [{'start': p['start'], 'end': p['end']} for p in paths]
            self.assertTrue(scoring.grade(action_cache.canonical(value), private)['complete'])

    def test_34_cross_unit_tail_entry_is_optional_context(self):
        private = scoring.truth(self.target(), 'optimized')
        value = scoring.answer(private)
        index = next(i for i, row in enumerate(private['realizations']) if row['kind'] == 'cross_unit_boundary')
        self.assertEqual(len(value['instances'][index]['ranges']), 1)
        self.assertTrue(scoring.grade(action_cache.canonical(value), private)['complete'])
        path = self.reviews['optimized']['groups']['cross_unit_boundary'][0]['entry_paths'][0]
        value['instances'][index]['ranges'].append({'start': path['start'], 'end': path['end']})
        self.assertTrue(scoring.grade(action_cache.canonical(value), private)['complete'])

    def test_35_lto_inline_adjustment_is_a_complete_thunk_alternative(self):
        private = scoring.truth(self.target('lto'), 'lto')
        value = scoring.answer(private)
        index = next(i for i, row in enumerate(private['realizations']) if row['kind'] == 'thunk')
        alternatives = private['realizations'][index]['alternatives']
        self.assertEqual(len(alternatives), 2)
        value['instances'][index]['ranges'] = copy.deepcopy(alternatives[1]['ranges'])
        score = scoring.grade(action_cache.canonical(value), private)
        self.assertTrue(score['complete'])
        self.assertEqual(score['coverage'], 1)
        # The method alone still cannot substitute for either adjustment path.
        value['instances'][index]['ranges'].pop(0)
        score = scoring.grade(action_cache.canonical(value), private)
        self.assertFalse(score['complete'])
        self.assertLess(score['coverage'], 1)

    def public_declaration(self, mode='optimized'):
        profile = 'optimized_recover' if mode == 'optimized' else 'lto_recover'
        return {'task_id': 'dispatch_registry_' + profile, 'family_id': 'dispatch_registry',
                'profile': {'id': profile, 'kind': 'optimized_localization', 'variant': mode,
                            'output_language': None, 'artifact_schema': {'instances.json': 1}}}

    def test_36_public_packages_are_hint_free_and_reuse_original_actions(self):
        settings = config.load()[0]
        for mode in self.targets:
            declaration = self.public_declaration(mode)
            with patch.object(build.process, 'run', side_effect=AssertionError('Unexpected worker')):
                record = tasks.freeze(declaration, settings, publish=False)
                self.assertEqual(tasks.validate_frozen(record, declaration, settings), record)
            directory = build.ROOT / record['public_directory']
            metadata = json.loads((directory / 'manifest.json').read_bytes())
            self.assertFalse({'entry_rva', 'entry_points', 'truth', 'realizations', 'groups'} & set(metadata))
            self.assertEqual({p.name for p in directory.iterdir()}, set(tasks.FILES))
            self.assertNotIn(b'origin_die', (directory / 'prompt.txt').read_bytes())
            names = [n for n in elf.ELF(bytes.fromhex(self.target(mode)['private_hex'])).symbols()
                     if n.startswith(('operation_', '_ZL15clone_', '_ZThn'))]
            for name in names:
                self.assertNotIn(name, (directory / 'prompt.txt').read_text())

    def test_37_rehashed_public_false_premises_and_private_truth_are_rejected(self):
        settings = config.load()[0]
        declaration = self.public_declaration()
        with tempfile.TemporaryDirectory(prefix='optimized-packages-', dir=build.ROOT / '.cache') as temporary, \
             patch.object(tasks, 'CACHE', Path(temporary)):
            record = tasks.freeze(declaration, settings, publish=False)
            self.assertEqual(tasks.validate_frozen(record, declaration, settings), record)
            directory = build.ROOT / record['public_directory']
            files = {name: (directory / name).read_bytes() for name in tasks.FILES}
            metadata = json.loads(files['manifest.json'])
            metadata['given_facts']['pointer_bytes'] = True
            files['manifest.json'] = action_cache.canonical(metadata)
            hashes = {name: tasks.sha(data) for name, data in sorted(files.items())}
            key = tasks.sha(action_cache.canonical(hashes))
            changed_directory = tasks.CACHE / 'task-freezes' / key
            changed_directory.mkdir()
            for name, data in files.items():
                (changed_directory / name).write_bytes(data)
            changed = {**record, 'public_package_sha256': key, 'public_files_sha256': hashes,
                       'public_directory': str(changed_directory.relative_to(build.ROOT))}
            with self.assertRaisesRegex(ValueError, 'premises'):
                tasks.validate_frozen(changed, declaration, settings)
            path = build.ROOT / record['grading_spec']
            spec = json.loads(path.read_bytes())
            spec['truth']['realizations'][0]['required'].pop()
            data = action_cache.canonical(spec)
            key = tasks.sha(data)
            changed_path = tasks.CACHE / 'grade-specs' / (key + '.json')
            changed_path.write_bytes(data)
            changed = {**record, 'grading_spec': str(changed_path.relative_to(build.ROOT)), 'grading_spec_sha256': key}
            with self.assertRaisesRegex(ValueError, 'truth'):
                tasks.validate_frozen(changed, declaration, settings)

    def test_38_declaration_and_exact_public_boundaries_fail_closed(self):
        settings = config.load()[0]
        declaration = self.public_declaration()
        for name, value in (('variant', 'unknown'), ('output_language', 'cpp'), ('artifact_schema', {'instances.json': True})):
            bad = copy.deepcopy(declaration)
            bad['profile'][name] = value
            with self.assertRaises(ValueError):
                tasks.contract(bad)
        with tempfile.TemporaryDirectory(prefix='optimized-boundaries-', dir=build.ROOT / '.cache') as temporary, \
             patch.object(tasks, 'CACHE', Path(temporary)):
            record = tasks.freeze(declaration, settings, publish=False)
            directory = build.ROOT / record['public_directory']
            (directory / 'extra.txt').write_text('unexpected file')
            with self.assertRaisesRegex(ValueError, 'boundary'):
                tasks.validate_frozen(record, declaration, settings)

    def test_39_closed_worker_grades_both_builds_and_the_lto_alternative(self):
        for mode, target in self.targets.items():
            private = scoring.truth(target, mode)
            value = scoring.answer(private)
            result = runtime.grade(action_cache.canonical(value), mode)
            self.assertEqual((result['passed'], result['total']), (6, 6))
            self.assertTrue(result['task_success'])
            self.assertEqual(result['grading_runtime']['scope'], 'closed_optimized_data_only_v1')
            if mode == 'lto':
                index = next(i for i, row in enumerate(private['realizations']) if row['kind'] == 'thunk')
                value['instances'][index]['ranges'] = private['realizations'][index]['alternatives'][1]['ranges']
                self.assertTrue(runtime.grade(action_cache.canonical(value), mode)['task_success'])

    def test_40_extra_instances_cannot_get_whole_task_success(self):
        private = scoring.truth(self.target(), 'optimized')
        value = scoring.answer(private)
        part = self.reviews['optimized']['near_miss_ranges'][0]
        value['instances'].append({'ranges': [{'start': part['start'], 'end': part['end']}]})
        result = runtime.grade(action_cache.canonical(value), 'optimized')
        self.assertEqual((result['passed'], result['total']), (6, 7))
        self.assertFalse(result['task_success'])
        self.assertLess(result['capabilities']['L6']['precision'], 1)

    def test_41_host_truth_or_score_patch_cannot_substitute_closed_grading(self):
        private = scoring.truth(self.target(), 'optimized')
        value = {'schema_version': 1, 'binary_sha256': private['binary_sha256'], 'instances': []}
        with patch.object(scoring, 'truth', return_value={'forged': True}), \
             patch.object(scoring, 'grade', return_value={'complete': True}):
            result = runtime.grade(action_cache.canonical(value) + b'\n', 'optimized')
        self.assertEqual((result['passed'], result['total']), (0, 6))
        self.assertFalse(result['task_success'])

    def test_42_cached_grade_and_original_binding_need_no_new_workers(self):
        value = scoring.answer(scoring.truth(self.target(), 'optimized'))
        with patch.object(runtime.process, 'run', side_effect=AssertionError('Unexpected worker')):
            self.assertTrue(runtime.grade(action_cache.canonical(value), 'optimized')['cache_hit'])
            bad = {k: v for k, v in self.target().items() if k != 'cache_hit'}
            bad['disassembly'] += '\n# substituted diagnostic\n'
            with self.assertRaisesRegex(ValueError, 'AC/CAS'):
                runtime.grade(action_cache.canonical(value), 'optimized', target=bad)


def main():
    started = evidence.begin()
    with evidence.validation_controls(), offline.guard():
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OptimizedLocationControls))
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__':
    main()
