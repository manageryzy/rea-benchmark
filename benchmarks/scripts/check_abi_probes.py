#!/usr/bin/python3
"""Offline controls for artifact-driven T1/T2/T5 and genuine original endpoints."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import elf
from capabilities.abi import artifacts, codegen, contracts, evidence, runtime, variants


class ABIProbeControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = {(family, convention): runtime.original(family, convention)
                       for family in contracts.FAMILIES for convention in contracts.CONVENTIONS}
        cls.counterfactuals = {}

    def counterfactual(self, family, convention, variant):
        key = family, convention, variant
        if key not in self.counterfactuals:
            self.counterfactuals[key] = runtime.original(family, convention, variant=variant)
        return self.counterfactuals[key]

    def pairs(self):
        for variant, row in variants.catalog().items():
            for family in contracts.FAMILIES:
                for convention in contracts.CONVENTIONS:
                    yield variant, row['condition'], family, convention

    def target(self, family='record_codec', convention='sysv_amd64'):
        return self.targets[family, convention]

    def types(self, family='record_codec', convention='sysv_amd64'):
        target = self.target(family, convention)
        return contracts.gold(family, target['binary_sha256'])

    def abi(self, family='record_codec', convention='sysv_amd64', *, claim=None):
        target = self.target(family, convention)
        return contracts.abi_claim(family, claim or convention, target['binary_sha256'], target['entries']['original_callee'])

    def grade(self, value, condition, family='record_codec', convention='sysv_amd64', **kw):
        return runtime.grade(action_cache.canonical(value), self.target(family, convention), condition, **kw)

    def test_01_six_frozen_originals_preserve_loaded_bytes_and_distinct_boundaries(self):
        for target in self.targets.values():
            with self.subTest(family=target['family'], convention=target['convention']):
                original, stripped = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
                self.assertEqual(original.programs, stripped.programs)
                self.assertEqual(original.allocated(), stripped.allocated())
                self.assertEqual(len(set(target['entries'].values())), 4)
                self.assertEqual(target['original_obj_sha256'], runtime.sha(bytes.fromhex(target['original_obj_hex'])))
                self.assertTrue(target['runtime']['compiler_image'])

    def test_02_all_three_conditions_all_builds_and_both_directions(self):
        for (family, convention), target in self.targets.items():
            for condition in contracts.CONDITIONS:
                with self.subTest(family=family, convention=convention, condition=condition):
                    print('\nPROBE:', family, convention, condition, flush=True)
                    value = self.abi(family, convention) if condition == 'abi_recover' else self.types(family, convention)
                    result = self.grade(value, condition, family, convention)
                    self.assertTrue(result['task_success'], result)
                    self.assertEqual(result['total'], 2 * len(contracts.NONCES))
                    self.assertEqual({check['direction'] for check in result['checks']}, {'original_caller_to_replacement', 'bridge_to_original_callee'})
                    self.assertEqual(result['original_obj_sha256'], target['original_obj_sha256'])

    def test_03_equivalent_names_field_order_and_unobservable_padding(self):
        value = self.types()
        root = next(node for node in value['types'] if node['id'] == value['root'])
        root['fields'].append({'role': 'spare', 'offset': 2, 'type': 'unsigned16'})
        root['fields'].reverse()
        names = {node['id']: 'different_' + node['id'] for node in value['types']}
        for node in value['types']:
            node['id'] = names[node['id']]
            if node['kind'] == 'record':
                for item in node['fields']:
                    item['type'] = names[item['type']]
            elif node['kind'] == 'array':
                node['element'] = names[node['element']]
        value['root'] = names[value['root']]
        self.assertTrue(self.grade(value, 'layout_recover')['task_success'])

    def test_04_alignment_claim_accepted_when_observationally_equivalent(self):
        value = self.types()
        next(node for node in value['types'] if node['id'] == value['root'])['alignment'] = 8
        self.assertTrue(self.grade(value, 'layout_recover')['task_success'])

    def test_05_wrong_signedness_is_observable(self):
        value = self.types()
        next(node for node in value['types'] if node['id'] == 'signed16')['signed'] = False
        result = self.grade(value, 'scalar_recover')
        self.assertTrue(result['artifact_valid'])
        self.assertLess(result['passed'], result['total'])

    def test_06_wrong_scalar_width_is_observable(self):
        value = self.types()
        next(node for node in value['types'] if node['id'] == 'signed16')['bits'] = 32
        self.assertFalse(self.grade(value, 'scalar_recover')['task_success'])

    def test_07_wrong_field_offset_fails_actual_interoperation(self):
        value = self.types()
        root = next(node for node in value['types'] if node['id'] == 'root')
        next(item for item in root['fields'] if item['role'] == 'flags')['offset'] = 9
        result = self.grade(value, 'layout_recover')
        self.assertTrue(result['artifact_valid'])
        self.assertEqual({check['direction'] for check in result['checks'] if not check['passed']}, {'original_caller_to_replacement', 'bridge_to_original_callee'})

    def test_08_wrong_stride_and_sret_size_corrupt_original_guard(self):
        value = self.types()
        next(node for node in value['types'] if node['id'] == 'values')['stride'] = 8
        next(node for node in value['types'] if node['id'] == 'root')['size'] = 36
        result = self.grade(value, 'layout_recover')
        self.assertTrue(result['artifact_valid'])
        self.assertFalse(result['task_success'])
        self.assertTrue(any(check['direction'] == 'original_caller_to_replacement' and
                            check['observation']['returned'] and check['observation']['status'] == 1 for check in result['checks']))

    def test_09_wrong_nested_object_offset(self):
        value = self.types('variable_objects')
        root = next(node for node in value['types'] if node['id'] == 'root')
        next(item for item in root['fields'] if item['role'] == 'items')['offset'] = 4
        self.assertFalse(self.grade(value, 'layout_recover', 'variable_objects')['task_success'])

    def test_10_self_consistent_wrong_convention_still_fails_original_endpoints(self):
        result = self.grade(self.abi(claim='ms_x64'), 'abi_recover')
        self.assertTrue(result['artifact_valid'])
        self.assertEqual(result['status'], 'graded')
        self.assertFalse(result['task_success'])
        self.assertEqual({check['direction'] for check in result['checks'] if not check['passed']}, {'original_caller_to_replacement', 'bridge_to_original_callee'})

    def test_11_machine_location_preservation_and_stack_claims_not_ignored(self):
        for field in ('register', 'hidden', 'preserved', 'rsp', 'alignment', 'shadow'):
            with self.subTest(field=field):
                value = self.abi()
                if field == 'register': value['arguments'][1]['locations'][0]['name'] = 'rax'
                if field == 'hidden': value['return']['hidden_pointer'] = 'rdx'
                if field == 'preserved': value['preserved'].remove('rbx')
                if field == 'rsp': value['preserved'].remove('rsp')
                if field == 'alignment': value['stack_alignment'] = 8
                if field == 'shadow': value['shadow_space'] = 32
                result = self.grade(value, 'abi_recover')
                self.assertTrue(result['artifact_valid'])
                self.assertEqual(result['status'], 'incorrect_description')
                self.assertEqual(result['passed'], 0)

    def test_12_known_nonmeasured_facts_cannot_be_silently_changed(self):
        value = self.types()
        root = next(node for node in value['types'] if node['id'] == 'root')
        next(item for item in root['fields'] if item['role'] == 'flags')['offset'] = 9
        self.assertEqual(self.grade(value, 'scalar_recover')['status'], 'incorrect_description')
        value = self.types()
        next(node for node in value['types'] if node['id'] == 'signed16')['signed'] = False
        self.assertEqual(self.grade(value, 'layout_recover')['status'], 'incorrect_description')

    def test_13_abi_claim_order_is_not_semantic(self):
        value = self.abi('variable_objects', 'ms_x64')
        value['preserved'].reverse()
        value['arguments'].reverse()
        self.assertTrue(self.grade(value, 'abi_recover', 'variable_objects', 'ms_x64')['task_success'])

    def test_14_bad_json_graph_bounds_and_wrong_target_are_not_infrastructure_errors(self):
        samples = [b'{"schema_version":1,"schema_version":1}', b'[]', b'{"size":1.5}', b'\xff']
        cycle = self.types()
        cycle['types'].append({'id': 'cycle', 'kind': 'array', 'element': 'cycle', 'count': 1, 'stride': 1, 'alignment': 1})
        samples.append(action_cache.canonical(cycle))
        malformed = self.types(); malformed['types'] = [[]]
        samples.append(action_cache.canonical(malformed))
        overlap = self.types(); next(n for n in overlap['types'] if n['id'] == 'root')['fields'][1]['offset'] = 0
        samples.append(action_cache.canonical(overlap))
        for raw in samples:
            with self.subTest(raw=raw[:40]):
                result = runtime.probe(raw, self.target(), 'layout_recover')
                self.assertFalse(result['artifact_valid'])
                self.assertEqual(result['status'], 'invalid_artifact')
        wrong = self.types(); wrong['binary_sha256'] = '0' * 64
        self.assertEqual(self.grade(wrong, 'layout_recover')['passed'], 0)

    def test_15_frozen_object_tampering_cannot_reuse_a_valid_grade(self):
        target = copy.deepcopy(self.target())
        target['original_obj_hex'] = target['original_obj_hex'][:-2] + ('00' if target['original_obj_hex'][-2:] != '00' else '01')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            runtime.grade(action_cache.canonical(self.types()), target, 'layout_recover')

    def test_16_host_codegen_monkeypatch_does_not_enter_closed_worker(self):
        value = self.types()
        root = next(node for node in value['types'] if node['id'] == 'root')
        next(item for item in root['fields'] if item['role'] == 'flags')['offset'] = 10
        with patch.object(codegen, 'generate', return_value=codegen.generate(self.types(), 'record_codec', 'sysv_amd64')):
            result = runtime.grade(action_cache.canonical(value) + b' ', self.target(), 'layout_recover')
        self.assertFalse(result['task_success'])

    def test_17_parent_cache_reuse_launches_no_compiler_or_executor(self):
        value = self.types(); first = self.grade(value, 'layout_recover')
        with (patch.object(runtime, '_compile', side_effect=AssertionError('Compiler launch')),
              patch.object(runtime.executor, 'invoke', side_effect=AssertionError('Executor launch'))):
            second = self.grade(value, 'layout_recover')
            target = runtime.original('record_codec', 'sysv_amd64')
        self.assertTrue(second['cache_hit'])
        self.assertTrue(target['cache_hit'])
        self.assertEqual(first['grading_id'], second['grading_id'])

    def test_18_stage_cache_dependencies_exclude_unrelated_and_grader_edits(self):
        original = runtime.original_action('record_codec', 'sysv_amd64')[0].key
        raw = action_cache.canonical(self.types())
        probe = runtime.probe_action(raw, self.target(), 'layout_recover')[0].key
        read = Path.read_bytes
        def changed(path):
            raw = read(path)
            if str(path).endswith(('capabilities/abi/codegen.py', 'scripts/report.py')):
                raw += b'\n# unrelated or probe-only control\n'
            return raw
        with patch.object(Path, 'read_bytes', changed):
            self.assertEqual(original, runtime.original_action('record_codec', 'sysv_amd64')[0].key)
            self.assertNotEqual(probe, runtime.probe_action(raw, self.target(), 'layout_recover')[0].key)

    def test_19_missing_acceptance_evidence_stops_before_compilation(self):
        token = evidence._controls.set(False)
        try:
            with tempfile.TemporaryDirectory() as temporary, patch.object(evidence, 'RECEIPT', Path(temporary) / 'missing'), patch.object(runtime, '_compile', side_effect=AssertionError('Compiler launch')):
                with self.assertRaisesRegex(RuntimeError, 'controls'):
                    self.grade(self.types(), 'layout_recover')
                receipt = {'schema_version': 1, 'passed': True, 'checks': 26, 'failed': 0, 'model_api_calls': 0,
                           'inputs_sha256': evidence.fingerprints(), 'pins': evidence.pins()}
                for change in ({'checks': 25}, {'checks': True}, {'failed': 1}):
                    evidence.RECEIPT.write_bytes(action_cache.canonical({**receipt, **change}))
                    with self.assertRaisesRegex(RuntimeError, 'controls'):
                        self.grade(self.types(), 'layout_recover')
        finally:
            evidence._controls.reset(token)

    def test_20_prelaunch_source_drift_rejected(self):
        _, request, frozen, environment, values = runtime.probe_action(action_cache.canonical(self.types()), self.target(), 'layout_recover')
        changed = {**frozen, 'benchmarks/scripts/capabilities/abi/codegen.py': b'changed'}
        with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
            runtime._compile(request, changed, environment, values)

    def fault_injected_claim(self, value, convention='sysv_amd64'):
        # Deliberately break only the consistency checker in a distinct declared
        # worker action. The real original endpoints must still reject bad
        # machine transfers; a mutually consistent table is not enough evidence.
        read = Path.read_bytes
        def bypass(path):
            raw = read(path)
            if str(path).endswith('/capabilities/abi/codegen.py'):
                old = b'return normalized(value) == normalized(expected)'
                self.assertEqual(raw.count(old), 1)
                raw = raw.replace(old, b'return True  # controlled admission fault injection')
            return raw
        with patch.object(Path, 'read_bytes', bypass):
            return self.grade(value, 'abi_recover', convention=convention, nonces=[0x7fff, 0xffffffffffffffff])

    def test_21_real_machine_transfers_reject_bad_maps_with_admission_checker_broken(self):
        for field in ('nonce', 'stack', 'sret', 'return'):
            with self.subTest(field=field):
                value = self.abi()
                if field == 'nonce': value['arguments'][1]['locations'][0]['name'] = 'r8'
                if field == 'stack': value['arguments'][0]['locations'][0]['offset'] += 8
                if field == 'sret': value['return']['hidden_pointer'] = 'rcx'
                if field == 'return': value['return']['locations'][0]['name'] = 'rdx'
                result = self.fault_injected_claim(value)
                self.assertEqual(result['status'], 'graded')
                self.assertFalse(result['task_success'])

    def test_22_original_caller_rejects_missing_register_preservation(self):
        value = self.abi()
        value['preserved'].remove('r12')
        result = self.fault_injected_claim(value)
        self.assertTrue(any(check['direction'] == 'original_caller_to_replacement' and
            check['observation']['returned'] and check['observation']['status'] == 2 for check in result['checks']))

    def test_23_original_alignment_and_home_slot_witnesses_are_effective(self):
        value = self.abi()
        value['stack_alignment'] = 8
        result = self.fault_injected_claim(value)
        self.assertTrue(any(check['direction'] == 'bridge_to_original_callee' and not check['passed'] for check in result['checks']))
        value = self.abi(convention='ms_x64')
        value['shadow_space'] = 0
        result = self.fault_injected_claim(value, convention='ms_x64')
        self.assertTrue(any(check['direction'] == 'bridge_to_original_callee' and
            check['observation']['returned'] and check['observation']['status'] == 1 for check in result['checks']))

    def test_24_all_original_references_agree_with_native_execution(self):
        from capabilities.abi import reference
        for target in self.targets.values():
            result = reference.native(target)
            self.assertEqual(len(result['observations']), len(contracts.NONCES))
            for item in result['observations']:
                observed = runtime.observe(bytes.fromhex(target['private_hex']), 'original_reference', item['nonce'], target['family'])
                self.assertTrue(observed['returned'])
                self.assertEqual(observed['status'], 0)
                self.assertEqual(observed['output_hex'], item['output_hex'])
            with patch.object(reference.process, 'run', side_effect=AssertionError('Native calibration rerun')):
                reused = reference.native(target)
            self.assertTrue(reused['cache_hit'])

    def test_25_register_case_is_semantic_equivalence_and_raw_artifact_is_preserved(self):
        value = self.abi()
        value['convention'] = value['convention'].upper()
        for argument in value['arguments']:
            for location in argument['locations']:
                if location['kind'] == 'register': location['name'] = location['name'].upper()
        value['return']['hidden_pointer'] = value['return']['hidden_pointer'].upper()
        for location in value['return']['locations']: location['name'] = location['name'].upper()
        value['preserved'] = [name.upper() for name in value['preserved']]
        before = copy.deepcopy(value)
        admitted = artifacts.abi(value)
        self.assertEqual(value, before)
        self.assertIn('rsp', admitted['preserved'])
        self.assertTrue(self.grade(value, 'abi_recover')['task_success'])

    def test_26_graph_depth_bound_is_independent_of_node_order(self):
        allowed = self.types()
        previous = 'unsigned32'
        for index in range(7):
            name = 'nested_' + str(index)
            allowed['types'].append({'id': name, 'kind': 'array', 'element': previous, 'count': 1, 'stride': 4, 'alignment': 4})
            previous = name
        for reverse in (False, True):
            value = copy.deepcopy(allowed)
            if reverse: value['types'].reverse()
            artifacts.types(value)
        rejected = copy.deepcopy(allowed)
        rejected['types'].append({'id': 'too_deep', 'kind': 'array', 'element': previous, 'count': 1, 'stride': 4, 'alignment': 4})
        for reverse in (False, True):
            value = copy.deepcopy(rejected)
            if reverse: value['types'].reverse()
            with self.assertRaisesRegex(ValueError, 'eight levels'):
                artifacts.types(value)
            result = runtime.probe(action_cache.canonical(value), self.target(), 'layout_recover')
            self.assertEqual(result['status'], 'invalid_artifact')

    def test_27_counterfactuals_keep_exactly_the_supplied_premises(self):
        from capabilities.abi.tasks import project_facts
        for variant, condition, family, convention in self.pairs():
            baseline = contracts.gold(family, '0' * 64)
            changed = variants.gold(family, '0' * 64, variant)
            self.assertNotEqual(baseline, changed)
            self.assertEqual(project_facts(baseline, condition), project_facts(changed, condition))
            codegen.known_facts(changed, family, condition)

    def test_28_all_twelve_counterfactual_conditions_pass_both_original_endpoints(self):
        count = 0
        for variant, condition, family, convention in self.pairs():
            with self.subTest(variant=variant, family=family, convention=convention):
                print('\nCOUNTERFACTUAL:', family, convention, condition, flush=True)
                target = self.counterfactual(family, convention, variant)
                value = variants.gold(family, target['binary_sha256'], variant)
                result = runtime.grade(action_cache.canonical(value), target, condition)
                self.assertTrue(result['task_success'], result)
                self.assertEqual(result['total'], 24)
                self.assertNotEqual(target['binary_sha256'], self.target(family, convention)['binary_sha256'])
                self.assertEqual(target['original_obj_sha256'], result['original_obj_sha256'])
                count += 1
        self.assertEqual(count, 12)

    def test_29_blind_baseline_descriptions_fail_the_counterfactual(self):
        for variant, condition, family, convention in self.pairs():
            with self.subTest(variant=variant, family=family, convention=convention):
                target = self.counterfactual(family, convention, variant)
                # Rebind only the opaque artifact identity. No binary-derived
                # facts enter this fixed baseline guess.
                guess = contracts.gold(family, target['binary_sha256'])
                result = runtime.grade(action_cache.canonical(guess), target, condition,
                    nonces=[contracts.NONCES[index] for index in (0, 3, 9)])
                self.assertTrue(result['artifact_valid'], result)
                self.assertEqual(result['status'], 'graded', result)
                self.assertFalse(result['task_success'], result)
                self.assertEqual({check['direction'] for check in result['checks'] if not check['passed']},
                    {'original_caller_to_replacement', 'bridge_to_original_callee'})

    def test_30_blind_counterfactual_descriptions_fail_the_baseline(self):
        for variant, condition, family, convention in self.pairs():
            with self.subTest(variant=variant, family=family, convention=convention):
                target = self.target(family, convention)
                guess = variants.gold(family, target['binary_sha256'], variant)
                result = runtime.grade(action_cache.canonical(guess), target, condition,
                    nonces=[contracts.NONCES[index] for index in (0, 3, 9)])
                self.assertTrue(result['artifact_valid'], result)
                self.assertEqual(result['status'], 'graded', result)
                self.assertFalse(result['task_success'], result)
                self.assertEqual({check['direction'] for check in result['checks'] if not check['passed']},
                    {'original_caller_to_replacement', 'bridge_to_original_callee'})

    def test_31_counterfactual_reference_agrees_with_native_execution(self):
        from capabilities.abi import reference
        count = 0
        for variant, condition, family, convention in self.pairs():
            target = self.counterfactual(family, convention, variant)
            result = reference.native(target)
            for item in result['observations']:
                observed = runtime.observe(bytes.fromhex(target['private_hex']), 'original_reference', item['nonce'], family)
                self.assertTrue(observed['returned'])
                self.assertEqual(observed['status'], 0)
                self.assertEqual(observed['output_hex'], item['output_hex'])
                count += 1
        self.assertEqual(count, 144)

    def test_32_original_actions_bind_only_the_selected_header_and_alias(self):
        family, convention, variant = 'record_codec', 'sysv_amd64', 'scalar_counterfactual'
        baseline = runtime.original_action(family, convention)[0].key
        action, request, frozen, _, policy = runtime.original_action(family, convention, variant)
        selected = variants.select(family, variant)['header']
        self.assertEqual(policy['source_aliases'], {runtime.PREFIX + family + '.hpp': selected})
        self.assertIn(selected, frozen)
        self.assertNotIn(str(variants.CATALOG.relative_to(action_cache.ROOT)), frozen)
        self.assertNotIn(runtime.PREFIX + family + '.hpp', frozen)
        read = Path.read_bytes
        def unrelated(path):
            raw = read(path)
            if str(path).endswith(('report.py', 'numeric_aggregation_layout.hpp')):
                return raw + b'\n// unrelated edit\n'
            return raw
        with patch.object(Path, 'read_bytes', unrelated):
            self.assertEqual(baseline, runtime.original_action(family, convention)[0].key)
            self.assertEqual(action.key, runtime.original_action(family, convention, variant)[0].key)
        def related(path):
            raw = read(path)
            return raw + b'\n// selected header edit\n' if str(path).endswith(selected) else raw
        with patch.object(Path, 'read_bytes', related):
            self.assertEqual(baseline, runtime.original_action(family, convention)[0].key)
            self.assertNotEqual(action.key, runtime.original_action(family, convention, variant)[0].key)
        def facts_only(path):
            raw = read(path)
            if path == variants.CATALOG:
                value = json.loads(raw)
                value['variants'][0]['families'][family]['scalars']['signed16']['id'] = 'gold_control_label_changed'
                return action_cache.canonical(value)
            return raw
        with patch.object(Path, 'read_bytes', facts_only):
            self.assertEqual(baseline, runtime.original_action(family, convention)[0].key)
            self.assertEqual(action.key, runtime.original_action(family, convention, variant)[0].key)

    def test_33_frozen_pairs_hide_variant_labels_and_reject_a_wrong_selector(self):
        from capabilities import config, tasks
        from capabilities.abi import tasks as typed
        declarations = {row['task_id']: row for row in tasks.declared()}
        settings = config.load()[0]
        for variant, condition, family, convention in self.pairs():
            prefix = family + '_' + convention + '_'
            first, second = [tasks.freeze_one(declarations[prefix + suffix]) for suffix in
                (condition, condition + '_counterfactual')]
            a, b = [action_cache.ROOT / row['public_directory'] for row in (first, second)]
            self.assertEqual((a / 'prompt.txt').read_bytes(), (b / 'prompt.txt').read_bytes())
            self.assertEqual((a / 'tools.json').read_bytes(), (b / 'tools.json').read_bytes())
            ma, mb = [json.loads((path / 'manifest.json').read_bytes()) for path in (a, b)]
            for key in ('compiler', 'given_facts', 'known_abi', 'artifact_schema', 'capability_ids', 'condition'):
                self.assertEqual(ma[key], mb[key], key)
            for filename in ('manifest.json', 'prompt.txt', 'tools.json'):
                self.assertNotIn(b'counterfactual', (b / filename).read_bytes())
                self.assertNotIn(b'/variants/', (b / filename).read_bytes())
            bad = {**second, 'variant': None}
            with self.assertRaisesRegex(RuntimeError, 'selection'):
                typed.validate_frozen(bad, declarations[prefix + condition + '_counterfactual'], settings)

    def test_34_undeclared_alias_or_source_drift_stops_before_worker(self):
        variant = 'scalar_counterfactual'
        _, request, frozen, platform, policy = runtime.original_action('record_codec', 'sysv_amd64', variant)
        changed = copy.deepcopy(policy)
        changed['source_aliases'][runtime.PREFIX + 'record_codec.hpp'] = 'benchmarks/scripts/grade.py'
        with patch.object(runtime.images, 'image', side_effect=AssertionError('Worker launch')):
            with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                runtime._compile(request, frozen, platform, changed, variant=variant)
            with self.assertRaisesRegex(RuntimeError, 'before worker launch'):
                runtime._compile(request, frozen, platform, policy, variant='layout_counterfactual')

    def test_35_equivalent_description_order_passes_all_counterfactuals(self):
        for variant, condition, family, convention in self.pairs():
            target = self.counterfactual(family, convention, variant)
            value = variants.gold(family, target['binary_sha256'], variant)
            value['types'].reverse()
            for node in value['types']:
                if node['kind'] == 'record': node['fields'].reverse()
            result = runtime.grade(action_cache.canonical(value), target, condition,
                nonces=[contracts.NONCES[index] for index in (3, 9)])
            self.assertTrue(result['task_success'], result)


def main():
    started = evidence.begin()
    with evidence.validation_controls(), patch.object(socket.socket, 'connect', side_effect=AssertionError('Network/model access')):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ABIProbeControls)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    value = evidence.finish(started, result)
    raise SystemExit(0 if value['passed'] else 1)


if __name__ == '__main__':
    main()
