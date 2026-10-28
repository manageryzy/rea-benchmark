#!/usr/bin/python3
"""Offline capability acceptance: real stripped originals, no model experiments.

The current gate covers artifact admission and L1–L5 scoring primitives. It does
not certify independent C reconstruction or type/ABI artifacts, nor enable tasks.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import build, elf, locations, public
from grading import artifacts, localization, offline

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/capability-validation.json'


def source_hashes():
    names = ['benchmarks/scripts/check_capabilities.py', 'benchmarks/scripts/grading/artifacts.py',
             'benchmarks/scripts/grading/localization.py', 'benchmarks/scripts/grading/process.py',
             'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
             'benchmarks/scripts/grading/c_executor/context.py', 'benchmarks/suites/capabilities-v1/config/families.json']
    names += build.SOURCES
    names += ['benchmarks/scripts/capabilities/' + name for name in
              ('__init__.py', 'build.py', 'build_worker.py', 'elf.py', 'locations.py', 'public.py')]
    names += [str(path.relative_to(ROOT)) for path in (build.SUITE / 'sources').iterdir() if path.is_file()]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(set(names))}


class CapabilityControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((build.SUITE / 'config/families.json').read_bytes())
        cls.originals, cls.truths = {}, {}
        with offline.guard():
            for family in cls.config['families']:
                name = family['id']
                value = build.build(name)
                cls.originals[name] = value
                cls.truths[name] = locations.private_truth(name, value)
            cls.absent = build.build('record_codec', absent=True)
            cls.absent_truth = locations.private_truth('record_codec', cls.absent, absent=True)
            cls.pie = build.build('dispatch_registry', pie=True)
            cls.pie_truth = locations.private_truth('dispatch_registry', cls.pie)

    def answer(self, family='record_codec'):
        return copy.deepcopy(locations.answer(self.truths[family]))

    def test_01_correct_and_equivalent_six_real_originals(self):
        for name, truth in self.truths.items():
            with self.subTest(family=name):
                candidate = self.answer(name)
                admitted = artifacts.admit({'locations.json': action_cache.canonical(candidate)}, {'locations.json': 1})
                score = localization.grade(admitted['locations.json']['value'], truth)['capabilities']
                self.assertTrue(score['L1']['top1'])
                self.assertEqual(score['L2']['f1'], 1)
                self.assertEqual(score['L5']['nodes']['f1'], 1)
                self.assertEqual(score['L5']['edges']['f1'], 1)
                for key in ('L3', 'L4'):
                    if score[key]['expected']:
                        self.assertEqual(score[key]['f1'], 1)
                equivalent = copy.deepcopy(candidate)
                equivalent['sites'].reverse()
                equivalent['chain']['nodes'].reverse()
                equivalent['chain']['edges'].reverse()
                self.assertEqual(localization.grade(equivalent, truth), localization.grade(candidate, truth))

    def test_02_absent_target_removes_behavior_not_just_labels(self):
        private, stripped = elf.stripped_matches(bytes.fromhex(self.absent['private_hex']), bytes.fromhex(self.absent['public_hex']))
        self.assertNotIn('decode_item', private.symbols())
        self.assertFalse(any(name.startswith('codec_decision_') for name in private.symbols()))
        self.assertNotEqual(stripped.raw, bytes.fromhex(self.originals['record_codec']['public_hex']))
        self.assertTrue(localization.grade(locations.answer(self.absent_truth), self.absent_truth)['capabilities']['L1']['top1'])
        claimed = self.answer()
        claimed['binary_sha256'] = self.absent_truth['binary_sha256']
        self.assertFalse(localization.grade(claimed, self.absent_truth)['capabilities']['L1']['top1'])

    def test_03_top1_not_top3_score(self):
        candidate = self.answer()
        correct = candidate['roots'][0]
        symbols = elf.ELF(bytes.fromhex(self.originals['record_codec']['private_hex'])).symbols()
        candidate['roots'] = [symbols['distractor']['rva'], correct]
        score = localization.grade(candidate, self.truths['record_codec'])['capabilities']['L1']
        self.assertFalse(score['top1'])
        self.assertTrue(score['alternative_hit'])

    def test_22_complete_operation_rejects_helper_roots(self):
        # Reviewed against each authored scalar wrapper. This checks the frozen
        # root distinction, not independently validated L2 semantic regions.
        for family in self.config['families']:
            name = family['id']
            symbols = elf.ELF(bytes.fromhex(self.originals[name]['private_hex'])).symbols()
            for helper in family['chain'][1:]:
                with self.subTest(family=name, helper=helper):
                    candidate = self.answer(name)
                    candidate['roots'] = [symbols[helper]['rva']]
                    result = localization.grade(candidate, self.truths[name])['capabilities']['L1']
                    self.assertFalse(result['top1'])
            prompt = locations.prompt(name, 'L1')
            self.assertIn(family['behavior'], prompt)
            self.assertIn('complete requested operation', prompt)
            self.assertNotIn(family['root'], prompt)
            for helper in family['chain'][1:]: self.assertNotIn(helper, prompt)

    def test_04_wrong_neighbor_branch_is_not_role_match(self):
        candidate = self.answer()
        # GCC moved the default-error initialization under the former check
        # anchor. It is real code, but not either input-rejection decision.
        candidate['sites'][0].update(start=0x1854, end=0x1859)
        score = localization.grade(candidate, self.truths['record_codec'])['capabilities']['L2']
        self.assertLess(score['coverage'], 1)
        self.assertLess(score['precision'], 1)

    def test_05_overreported_ranges_lower_precision(self):
        candidate = self.answer()
        candidate['sites'][0]['end'] += 20
        score = localization.grade(candidate, self.truths['record_codec'])['capabilities']['L2']
        self.assertEqual(score['coverage'], 1)
        self.assertLess(score['precision'], 1)

    def test_06_wrong_dispatch_condition(self):
        candidate = self.answer('dispatch_registry')
        self.assertEqual(len(candidate['dispatch']), 2)
        candidate['dispatch'][0]['condition'] = 'wrong_condition'
        score = localization.grade(candidate, self.truths['dispatch_registry'])['capabilities']['L3']
        self.assertEqual(score['correct'], 1)
        self.assertLess(score['f1'], 1)

    def test_07_wrong_data_direction(self):
        candidate = self.answer()
        self.assertTrue(candidate['references'])
        candidate['references'][0]['access'] = 'write'
        score = localization.grade(candidate, self.truths['record_codec'])['capabilities']['L4']
        self.assertEqual(score['correct'], 0)

    def test_08_wrong_data_object(self):
        candidate = self.answer()
        candidate['references'][0]['object'] += 1
        self.assertEqual(localization.grade(candidate, self.truths['record_codec'])['capabilities']['L4']['correct'], 0)

    def test_09_reversed_chain_and_unrelated_nodes(self):
        candidate = self.answer()
        candidate['chain']['edges'][0].reverse()
        self.assertEqual(localization.grade(candidate, self.truths['record_codec'])['capabilities']['L5']['edges']['correct'], 0)
        candidate = self.answer()
        candidate['chain']['nodes'].append(0)
        self.assertLess(localization.grade(candidate, self.truths['record_codec'])['capabilities']['L5']['nodes']['precision'], 1)

    def test_10_pie_and_exec_normalize_from_lowest_load_page(self):
        score = localization.grade(locations.answer(self.pie_truth), self.pie_truth)
        self.assertTrue(score['capabilities']['L1']['top1'])
        private = elf.ELF(bytes.fromhex(self.pie['private_hex']))
        self.assertEqual(private.kind, 3)
        self.assertEqual(private.base, 0)
        other = elf.ELF(bytes.fromhex(self.originals['dispatch_registry']['private_hex']))
        self.assertEqual(other.kind, 2)
        self.assertEqual(other.base, 0x400000)
        candidate = self.answer('dispatch_registry')
        candidate['roots'][0] += other.base
        self.assertFalse(localization.grade(candidate, self.truths['dispatch_registry'])['capabilities']['L1']['top1'])

    def test_11_tampered_stripped_code_is_rejected(self):
        value = self.originals['record_codec']
        raw = bytearray.fromhex(value['public_hex'])
        target = elf.ELF(bytes(raw))
        text = next(section for section in target.sections if target.section_name(section) == '.text')
        raw[text[4]] ^= 1
        with self.assertRaisesRegex(ValueError, 'allocated'):
            elf.stripped_matches(bytes.fromhex(value['private_hex']), bytes(raw))
        raw = bytearray.fromhex(value['public_hex'])
        header = elf.HEADER.unpack_from(raw)
        offset = header[5]  # Program-header offset; change a retained mapping.
        program = list(elf.PROGRAM.unpack_from(raw, offset))
        program[3] += 4096
        elf.PROGRAM.pack_into(raw, offset, *program)
        with self.assertRaisesRegex(ValueError, 'allocated'):
            elf.stripped_matches(bytes.fromhex(value['private_hex']), bytes(raw))

    def test_12_wrong_binary_and_unvalidated_truth(self):
        candidate = self.answer()
        candidate['binary_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'different binary'):
            localization.grade(candidate, self.truths['record_codec'])
        with self.assertRaisesRegex(ValueError, 'validated private'):
            localization.grade(self.answer(), {**self.truths['record_codec'], 'validated': False})

    def test_13_duplicate_unknown_and_ambiguous_json(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1.5}', b'\xff', b'[' * 2000):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                artifacts.decode(raw)
        candidate = self.answer()
        candidate['private_hint'] = 123
        with self.assertRaises(ValueError):
            artifacts.locations(candidate)

    def test_14_boolean_address_or_version_rejected(self):
        candidate = self.answer()
        candidate['roots'][0] = True
        with self.assertRaises(ValueError):
            artifacts.locations(candidate)
        candidate = self.answer()
        candidate['schema_version'] = True
        with self.assertRaises(ValueError):
            artifacts.locations(candidate)

    def test_15_limits_reject_whole_program_lists(self):
        for change in ('roots', 'sites', 'chain'):
            candidate = self.answer()
            if change == 'roots':
                candidate['roots'] = list(range(4))
            elif change == 'sites':
                candidate['sites'][0]['end'] = candidate['sites'][0]['start'] + 1000
            else:
                candidate['chain']['nodes'] = list(range(1000))
            with self.subTest(change=change), self.assertRaises(ValueError):
                artifacts.locations(candidate)

    def test_16_absent_unanswered_cannot_attach_guesses(self):
        candidate = self.answer()
        for status in ('absent', 'unanswered'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                artifacts.locations({**candidate, 'status': status})
        unanswered = {**locations.answer(self.absent_truth), 'status': 'unanswered'}
        score = localization.grade(unanswered, self.absent_truth)
        self.assertFalse(score['answered'])
        self.assertFalse(score['capabilities']['L1']['top1'])

    def test_17_exact_artifact_set_no_code_fence_or_path_traversal(self):
        for files, contract in [({'../locations.json': b'{}'}, {'../locations.json': 1}),
                                ({'types.json': b'{}'}, {'types.json': 1}),
                                ({'solution.c': b'\xff'}, {'solution.c': 1}),
                                ({'solution.c': b'int\0'}, {'solution.c': 1}),
                                ({'locations.json': b'```json\n{}\n```'}, {'locations.json': 1}),
                                ({'locations.json': b'{}', 'answer.txt': b'x'}, {'locations.json': 1})]:
            with self.subTest(contract=contract), self.assertRaises(ValueError):
                artifacts.admit(files, contract)
        raw = b'int reconstructed(void) { return 7; }\n'
        self.assertEqual(artifacts.admit({'solution.c': raw}, {'solution.c': 1})['solution.c']['value'].encode(), raw)

    def test_18_public_projection_drops_nested_private_fields(self):
        private = locations.metadata('record_codec', self.originals['record_codec'], self.truths['record_codec'])
        private.update(entry_points={'secret': 1234}, types={'private_name': 'SECRET_CANARY'}, handlers=['decode_item'])
        projected = public.manifest(private, capability='L1', task_id='record_codec_L1')
        self.assertEqual(set(projected), set(public.FIELDS))
        raw = json.dumps(projected)
        self.assertNotIn('secret', raw)
        self.assertNotIn('decode_item', raw)
        for field in ('entry_points', 'handlers', 'types'):
            with self.assertRaises(ValueError):
                public.validate_manifest({**projected, field: private[field]})
        with self.assertRaises(ValueError):
            public.validate_manifest({**projected, 'artifact_schema': {'locations.json': True}})

    def test_19_all_public_packages_have_only_allowlisted_files(self):
        tools = (ROOT / 'benchmarks/config/rea-tools.json').read_bytes()
        with tempfile.TemporaryDirectory(prefix='rea-public-capability-', dir='/tmp') as temporary:
            for family in self.config['families']:
                name = family['id']
                original, truth = self.originals[name], self.truths[name]
                for capability in ('L1', 'L2', 'L3', 'L4', 'L5'):
                    destination = Path(temporary) / (name + capability)
                    public.package(destination, private=locations.metadata(name, original, truth), capability=capability,
                                   task_id=name + '_' + capability, prompt=locations.prompt(name, capability),
                                   binary=bytes.fromhex(original['public_hex']), tools=tools)
                    self.assertEqual({p.name for p in destination.iterdir()}, {'manifest.json', 'target', 'prompt.txt', 'tools.json'})
                    metadata = b'\n'.join((destination / item).read_bytes() for item in ('manifest.json', 'prompt.txt'))
                    for symbol in (*family['chain'], *family['roles'].values()):
                        self.assertNotIn(symbol.encode(), metadata)
                        self.assertNotIn(symbol.encode(), (destination / 'target').read_bytes())
                    with self.assertRaisesRegex(ValueError, 'empty directory'):
                        public.package(destination, private=locations.metadata(name, original, truth), capability=capability,
                                       task_id=name + '_' + capability, prompt='x', binary=bytes.fromhex(original['public_hex']), tools=tools)

    def test_20_grade_key_ignores_unrelated_scripts_and_binds_artifacts(self):
        truth = self.truths['record_codec']
        candidate = self.answer()
        files = {'locations.json': action_cache.canonical(candidate)}
        original = locations.action(files, truth).key
        candidate['roots'][0] += 1
        self.assertNotEqual(original, locations.action({'locations.json': action_cache.canonical(candidate)}, truth).key)
        changed_truth = {**truth, 'roots': [truth['roots'][0] + 1]}
        self.assertNotEqual(original, locations.action(files, changed_truth).key)
        # A shadow root contains only declared grading sources: unrelated files
        # cannot be read accidentally or become implicit universal dependencies.
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            shadow = Path(temporary)
            for name in ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/artifacts.py',
                         'benchmarks/scripts/grading/localization.py'):
                path = shadow / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            with patch.object(locations, 'ROOT', shadow):
                self.assertEqual(original, locations.action(files, truth).key)
                (shadow / 'README.md').write_text('Unrelated mutation')
                self.assertEqual(original, locations.action(files, truth).key)

    def test_21_build_cache_reuse_without_compiler_launch(self):
        with patch.object(build.process, 'run', side_effect=AssertionError('Duplicate build worker')):
            reused = build.build('record_codec')
        self.assertTrue(reused['cache_hit'])
        self.assertEqual(reused['build_id'], self.originals['record_codec']['build_id'])

    def test_23_codec_check_and_xor_regressions(self):
        candidate = self.answer()
        checks = [item for item in candidate['sites'] if item['role'] == 'record_check']
        self.assertEqual({item['start'] for item in checks}, {0x185d, 0x1862})
        candidate['sites'] = [item for item in candidate['sites'] if item['start'] != 0x1875]
        score = localization.grade(candidate, self.truths['record_codec'])['capabilities']['L2']
        self.assertLess(score['coverage'], 1)
        self.assertLess(score['f1'], 1)
        candidate = self.answer()
        candidate['sites'] = [{'role': 'record_check', 'start': 0x1854, 'end': 0x1859}]
        score = localization.grade(candidate, self.truths['record_codec'])['capabilities']['L2']
        self.assertEqual(score['correct'], 0)
        self.assertEqual(score['precision'], 0)

    def test_24_optional_context_accepts_legitimate_ranges_but_cannot_replace_operations(self):
        for name, truth in self.truths.items():
            with self.subTest(family=name):
                context = [{'role': role, **region} for role, ranges in truth['role_context'].items() for region in ranges]
                candidate = self.answer(name)
                candidate['sites'] += context
                score = localization.grade(candidate, truth)['capabilities']['L2']
                self.assertEqual(score['f1'], 1)
                self.assertEqual(score['accepted'], score['submitted'])
                candidate['sites'] = context
                score = localization.grade(candidate, truth)['capabilities']['L2']
                self.assertEqual(score['coverage'], 0)
                self.assertEqual(score['f1'], 0)
        # A single contiguous cmp+branch range is also legitimate.
        candidate = self.answer()
        for site in candidate['sites']:
            if site['start'] == 0x185d: site['start'] = 0x1859
            if site['start'] == 0x1862: site['start'] = 0x185f
        self.assertEqual(localization.grade(candidate, self.truths['record_codec'])['capabilities']['L2']['f1'], 1)

    def test_25_review_is_exact_binary_and_instruction_byte_bound(self):
        catalog = copy.deepcopy(self.config)
        reviewed = next(row for row in catalog['semantic_review']['builds'] if row['family_id'] == 'record_codec')
        reviewed['binary_sha256'] = '0' * 64
        with patch.object(locations.json, 'loads', return_value=catalog), self.assertRaisesRegex(ValueError, 'review missing'):
            locations.private_truth('record_codec', self.originals['record_codec'])
        catalog = copy.deepcopy(self.config)
        reviewed = next(row for row in catalog['semantic_review']['builds'] if row['family_id'] == 'record_codec')
        reviewed['roles']['record_check']['required'][0]['bytes'] = '90'
        with patch.object(locations.json, 'loads', return_value=catalog), self.assertRaisesRegex(ValueError, 'instruction bytes'):
            locations.private_truth('record_codec', self.originals['record_codec'])

    def test_26_joint_prompt_defines_roles_and_dispatch_condition_ids(self):
        for family in self.config['families']:
            prompt = locations.prompt(family['id'], 'L1')
            for role, description in family['role_descriptions'].items():
                self.assertIn(role + ': ' + description, prompt)
            for condition in ('low_bit_zero', 'low_bit_one', 'each_byte'):
                self.assertIn(condition, prompt)
            self.assertIn('optional context', prompt)
        catalog = copy.deepcopy(self.config)
        del catalog['families'][0]['role_descriptions']['record_check']
        with patch.object(locations.json, 'loads', return_value=catalog):
            with self.assertRaisesRegex(ValueError, 'role descriptions'):
                locations.prompt('record_codec', 'L1')
            with self.assertRaisesRegex(ValueError, 'role descriptions'):
                locations.private_truth('record_codec', self.originals['record_codec'])

    def test_27_partial_instructions_and_legacy_anchor_truth_cannot_full_pass(self):
        candidate = self.answer()
        candidate['sites'][0]['end'] -= 1
        self.assertLess(localization.grade(candidate, self.truths['record_codec'])['capabilities']['L2']['f1'], 1)
        legacy = copy.deepcopy(self.truths['record_codec'])
        del legacy['semantic_policy']
        with self.assertRaisesRegex(ValueError, 'validated private truth'):
            localization.grade(self.answer(), legacy)


def main():
    before = source_hashes()
    RECEIPT.write_text(json.dumps({'schema_version': 1, 'passed': False, 'reason': 'checks running'}, indent=2) + '\n')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CapabilityControls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if before != source_hashes():
        raise RuntimeError('Capability acceptance inputs changed during checks')
    record = {'schema_version': 1, 'passed': result.wasSuccessful(), 'checks': result.testsRun,
              'failed': len(result.errors) + len(result.failures), 'model_api_calls': 0,
              'provider_forwards': 0, 'inference_enabled': False,
              'scope': 'artifact_admission_and_L1_L5_primitives_v1', 'inputs_sha256': before,
              'build_ids': {name: value['build_id'] for name, value in getattr(CapabilityControls, 'originals', {}).items()}}
    RECEIPT.write_text(json.dumps(record, indent=2) + '\n')
    raise SystemExit(0 if record['passed'] else 1)


if __name__ == '__main__':
    main()
