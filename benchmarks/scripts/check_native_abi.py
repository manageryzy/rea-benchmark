#!/usr/bin/python3
"""Native aggregate ABI acceptance: real Rust/Go endpoints, no model calls."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities.native_abi import artifacts, build, codegen, contracts, evidence, execute, images, native, probe, reference, runtime, tasks
from capabilities import config
from grading import image_manifest, offline
from grading.c_executor import runtime as executor


class NativeABIControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = {language: build.build(language) for language in ('rust', 'go')}
        cls.gold = {language: contracts.gold(language, hashlib.sha256(bytes.fromhex(t['public_hex'])).hexdigest())
                    for language, t in cls.targets.items()}
        cls.probes = {language: probe.compile(language, action_cache.canonical(value)) for language, value in cls.gold.items()}
        for result in cls.probes.values():
            if result['status'] != 'compiled':
                raise RuntimeError('Generated native ABI control did not compile: ' + result.get('log', ''))

    def rows(self, language, value=None, nonces=(0, 1, 0xffffffffffffffe1)):
        compiled = (self.probes[language] if value is None else probe.compile(language, action_cache.canonical(value)))
        self.assertEqual(compiled['status'], 'compiled', compiled.get('log'))
        result = []
        for nonce in nonces:
            values = reference.arguments(nonce)
            for direction in ('replacement', 'bridge'):
                observation = execute.observe(language, values, direction=direction, probe=compiled['elf'])
                expected = reference.expected(1 if direction == 'replacement' else 0, values)
                self.assertTrue(observation['original_endpoints_preserved'])
                result.append((direction, observation, observation['status'] == 'returned' and observation['values'] == expected))
        return result

    def bad(self, language, value):
        rows = self.rows(language, value)
        self.assertEqual({d for d, _, _ in rows}, {'replacement', 'bridge'})
        self.assertFalse(any(passed for _, _, passed in rows), rows)

    def test_01_exact_pinned_compilers_have_separate_original_actions(self):
        self.assertIn('rustc 1.91.1', image_manifest.load('rust')['manifest']['compiler']['version'])
        self.assertIn('go1.23.2', image_manifest.load('go')['manifest']['compiler']['version'])
        self.assertNotEqual(self.targets['rust']['build_id'], self.targets['go']['build_id'])
        for language, target in self.targets.items():
            self.assertEqual(target['runtime']['compiler_image'], image_manifest.load(language)['id'])

    def test_02_real_stripped_images_preserve_effective_load_memory(self):
        for language, target in self.targets.items():
            original, public = images.validate(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']), language)
            self.assertEqual(original.allocated(), public.allocated())
            self.assertFalse(any(s[1] == 2 for s in public.sections))
            self.assertFalse(any(public.section_name(s) == '.debug_info' for s in public.sections))

    def test_03_native_originals_and_ffi_control_match_60_python_observations(self):
        count = 0
        for language in self.targets:
            result = native.calibrate(language)
            for row in result['observations']:
                self.assertEqual(row['values'], reference.expected(row['selected'], row['arguments']))
                count += 1
        self.assertEqual(count, 60)

    def test_04_emulated_originals_match_all_24_native_reference_vectors(self):
        count = 0
        for language in self.targets:
            for nonce in reference.NONCES:
                values = reference.arguments(nonce)
                row = execute.observe(language, values)
                self.assertEqual(row['status'], 'returned', row['error'])
                self.assertEqual(row['values'], reference.expected(0, values))
                self.assertEqual(row['provenance'], 'host_native_abi_return_v1')
                self.assertTrue(row['original_endpoints_preserved'])
                count += 1
        self.assertEqual(count, 24)

    def test_05_gold_passes_all_48_bidirectional_original_endpoint_checks(self):
        count = 0
        for language in self.targets:
            rows = self.rows(language, nonces=reference.NONCES)
            self.assertTrue(all(passed for _, _, passed in rows), rows)
            count += len(rows)
        self.assertEqual(count, 48)

    def test_06_wrong_packet_binding_fails_both_original_directions(self):
        for language in self.targets:
            value = copy.deepcopy(self.gold[language])
            value['packet'] = ({'kind': 'indirect', 'register': 'r8'} if language == 'rust'
                               else {'kind': 'registers', 'registers': ['rbx', 'rax', 'rcx']})
            self.bad(language, value)

    def test_07_wrong_nonce_binding_remains_partial_and_fails_whole_task(self):
        for language in self.targets:
            value = copy.deepcopy(self.gold[language])
            value['nonce']['register'] = 'rcx' if language == 'rust' else 'rsi'
            rows = self.rows(language, value)
            for direction in ('replacement', 'bridge'):
                self.assertFalse(all(passed for d, _, passed in rows if d == direction))
            if language == 'rust':
                # RCX happens to be zero for nonce zero in both calibrated
                # contexts. Keep those genuine observations as partial credit.
                self.assertEqual(sum(passed for _, _, passed in rows), 2)
                self.assertEqual(len(rows), 6)

    def test_08_wrong_reply_binding_fails_both_original_directions(self):
        for language in self.targets:
            value = copy.deepcopy(self.gold[language])
            value['reply'] = ({'kind': 'indirect', 'register': 'r8'} if language == 'rust'
                              else {'kind': 'registers', 'registers': ['rax', 'rcx', 'rbx']})
            self.bad(language, value)

    def test_09_correct_c_ffi_control_is_wrong_for_the_rust_native_boundary(self):
        self.bad('rust', contracts.ffi_control(self.gold['rust']['binary_sha256']))

    def test_10_cross_language_bindings_fail_even_with_the_right_binary_id(self):
        for language, other in (('rust', 'go'), ('go', 'rust')):
            value = {**copy.deepcopy(self.gold[other]), 'binary_sha256': self.gold[language]['binary_sha256']}
            self.bad(language, value)

    def test_11_json_order_and_whitespace_have_equivalent_machine_behavior(self):
        for language in self.targets:
            value = dict(reversed(list(self.gold[language].items())))
            raw = json.dumps(value, indent=2).encode() + b'\n'
            compiled = probe.compile(language, raw)
            self.assertEqual(compiled['elf'], self.probes[language]['elf'])
            row = execute.observe(language, reference.arguments(0), direction='bridge', probe=compiled['elf'])
            self.assertTrue(row['cache_hit'])

    def test_12_unknown_fields_boolean_versions_and_arbitrary_addresses_rejected(self):
        base = self.gold['rust']
        for value in ({**base, 'schema_version': True}, {**base, 'schema_version': 2},
                      {**base, 'source': 'hidden code'}, {**base, 'callee_address': 0x400000},
                      {**base, 'binary_sha256': 'x' * 64}):
            with self.assertRaises(ValueError):
                artifacts.admit(action_cache.canonical(value))
        with self.assertRaises(ValueError):
            artifacts.admit(b'{"schema_version":1,"schema_version":1}')

    def test_13_wrong_binary_fails_before_probe_or_guest_workers(self):
        value = {**self.gold['rust'], 'binary_sha256': '0' * 64}
        with patch.object(probe.process, 'run', side_effect=AssertionError('Unexpected worker')):
            self.assertEqual(probe.compile('rust', action_cache.canonical(value))['status'], 'wrong_binary')

    def test_14_invalid_invocation_fails_before_worker(self):
        values = reference.arguments(0)
        for invalid in ({**values, 'nonce': True}, {**values, 'first': -1}, {**values, 'third': 2**64},
                        {**values, 'oracle': [1, 2, 3]}, []):
            with patch.object(execute.process, 'run', side_effect=AssertionError('Unexpected worker')):
                with self.assertRaises(ValueError):
                    execute.observe('rust', invalid)

    def test_15_location_and_transport_bounds_reject_impossible_artifacts(self):
        for binding in ({'kind': 'stack', 'offset': True}, {'kind': 'stack', 'offset': 0},
                        {'kind': 'stack', 'offset': 520}, {'kind': 'stack', 'offset': 9},
                        {'kind': 'registers', 'registers': ['rax'] * 3},
                        {'kind': 'indirect', 'register': 'r14'}, {'kind': 'indirect', 'register': 'rsp'}):
            value = {**self.gold['rust'], 'packet': binding}
            with self.assertRaises(ValueError):
                artifacts.admit(action_cache.canonical(value))
        with self.assertRaises(ValueError):
            artifacts.admit(b' ' * 8193)

    def test_16_host_generator_patch_cannot_replace_closed_description_generation(self):
        value = copy.deepcopy(self.gold['go'])
        value['nonce']['register'] = 'rsi'
        source = codegen.generate(self.gold['go'], 0x400000)
        with patch.object(codegen, 'generate', return_value=source):
            compiled = probe.compile('go', action_cache.canonical(value) + b'\n')
        row = execute.observe('go', reference.arguments(0), direction='bridge', probe=compiled['elf'])
        self.assertTrue(row['status'] != 'returned' or row['values'] != reference.expected(0, reference.arguments(0)))

    def test_17_guest_syscall_cannot_forge_a_host_return_record(self):
        source = 'void __rea_native_replacement(void) { __asm__ volatile("syscall"); }\nvoid __rea_native_bridge(void) {}\n'
        compiled = executor.compile(source, ('__rea_native_replacement', '__rea_native_bridge'))
        self.assertEqual(compiled['status'], 'compiled')
        row = execute.observe('rust', reference.arguments(0), direction='replacement', probe=compiled['elf'])
        self.assertEqual(row['status'], 'forbidden_syscall')
        self.assertIsNone(row['return_record'])
        # This frozen original caller uses PUSH RBX, SUB 32 and CALL: the
        # replacement entry is 48 bytes below the root return address. Verify
        # the private bytes before using that reviewed frame relationship.
        image, _ = images.validate(bytes.fromhex(self.targets['rust']['private_hex']),
                                    bytes.fromhex(self.targets['rust']['public_hex']), 'rust')
        caller = images.selected_symbols(image, ('native_caller',))['native_caller']
        self.assertEqual(image.bytes_at(caller['rva'], 5), bytes.fromhex('534883ec20'))
        source = ('__attribute__((naked)) void __rea_native_replacement(void) { '
                  '__asm__("movq $0,48(%rsp); ret"); }\nvoid __rea_native_bridge(void) {}\n')
        compiled = executor.compile(source, ('__rea_native_replacement', '__rea_native_bridge'))
        self.assertEqual(compiled['status'], 'compiled')
        row = execute.observe('rust', reference.arguments(0), direction='replacement', probe=compiled['elf'])
        self.assertEqual(row['status'], 'forbidden_memory_write')
        self.assertIsNone(row['return_record'])

    def test_18_cold_original_calibration_is_independent_of_candidate_generator(self):
        for language in self.targets:
            vector = reference.arguments(0x11739da40b26e8cf)
            with patch.object(codegen, 'generate', side_effect=AssertionError('Candidate generator')):
                result = native.calibrate(language, vectors=[vector])
            for row in result['observations']:
                self.assertEqual(row['values'], reference.expected(row['selected'], vector))

    def test_19_fetch_outside_whitelisted_originals_is_forbidden(self):
        original, _ = images.validate(bytes.fromhex(self.targets['rust']['private_hex']),
                                      bytes.fromhex(self.targets['rust']['public_hex']), 'rust')
        address = images.selected_symbols(original, ('ffi_transform',))['ffi_transform']['address']
        source = ('__attribute__((naked)) void __rea_native_replacement(void) { __asm__("movabs $'
                  + str(address) + ', %rax; jmp *%rax"); }\nvoid __rea_native_bridge(void) {}\n')
        compiled = executor.compile(source, ('__rea_native_replacement', '__rea_native_bridge'))
        self.assertEqual(compiled['status'], 'compiled')
        row = execute.observe('rust', reference.arguments(0), direction='replacement', probe=compiled['elf'])
        self.assertEqual(row['status'], 'forbidden_instruction_fetch')
        self.assertIsNone(row['return_record'])

    def test_20_nonzero_trimmed_go_padding_is_not_normalized_away(self):
        original = images.elf.ELF(bytes.fromhex(self.targets['go']['private_hex']))
        public_raw = bytes.fromhex(self.targets['go']['public_hex'])
        public = images.elf.ELF(public_raw)
        pair = next((a, b) for a, b in zip(original.programs, public.programs) if a != b)
        a, b = pair
        self.assertGreater(a[5], b[5])
        raw = bytearray(original.raw)
        raw[a[2] + a[5] - 1] = 127
        with self.assertRaisesRegex(ValueError, 'zero-filled'):
            images.validate(bytes(raw), public_raw, 'go')

    def test_21_rust_autoloader_allowlist_accepts_only_the_exact_marker(self):
        target = self.targets['rust']
        changed = []
        for name in ('private_hex', 'public_hex'):
            image = images.elf.ELF(bytes.fromhex(target[name]))
            section = next(s for s in image.sections if image.section_name(s) == '.debug_gdb_scripts')
            raw = bytearray(image.raw)
            raw[section[4] + 1] ^= 1
            changed.append(bytes(raw))
        with self.assertRaisesRegex(ValueError, 'auto-loader'):
            images.validate(*changed, 'rust')

    def test_22_probe_and_execution_inputs_exclude_private_answers(self):
        for names in (build.SOURCES, probe.SOURCES, execute.SOURCES):
            self.assertNotIn(build.MODULE + 'contracts.py', names)
            self.assertNotIn(build.MODULE + 'reference.py', names)
        self.assertNotIn(build.MODULE + 'codegen.py', execute.SOURCES)
        self.assertNotIn(build.MODULE + 'native_worker.py', probe.SOURCES)

    def test_23_cached_original_compilation_and_observations_launch_no_workers(self):
        with patch.object(probe.process, 'run', side_effect=AssertionError('Unexpected worker')):
            for language in self.targets:
                self.assertTrue(build.build(language)['cache_hit'])
                self.assertTrue(native.calibrate(language)['cache_hit'])
                compiled = probe.compile(language, action_cache.canonical(self.gold[language]))
                self.assertTrue(compiled['cache_hit'])
                for direction in ('reference', 'replacement', 'bridge'):
                    row = execute.observe(language, reference.arguments(0), direction=direction,
                                          probe=None if direction == 'reference' else compiled['elf'])
                    self.assertTrue(row['cache_hit'])

    def test_24_reordered_private_vector_selection_reuses_individual_actions(self):
        with patch.object(execute.process, 'run', side_effect=AssertionError('Unexpected worker')):
            for language in self.targets:
                for nonce in (reference.NONCES[-1], 0, reference.NONCES[3]):
                    for direction in ('replacement', 'bridge'):
                        result = execute.observe(language, reference.arguments(nonce), direction=direction, probe=self.probes[language]['elf'])
                        self.assertTrue(result['cache_hit'])

    def test_25_cached_result_cannot_outlive_loaded_host_source(self):
        old = Path.read_bytes
        wanted = build.ROOT / execute.HOST_SOURCE
        def changed(path):
            raw = old(path)
            return raw + b'\n# drift\n' if path == wanted else raw
        with patch.object(Path, 'read_bytes', changed), patch.object(execute.process, 'run', side_effect=AssertionError('Worker')):
            with self.assertRaisesRegex(RuntimeError, 'Loaded'):
                execute.observe('rust', reference.arguments(0))

    def test_26_late_source_drift_prevents_execution_cache_commit(self):
        old_read, old_run = Path.read_bytes, execute.process.run
        drift = [False]
        def changed(path):
            raw = old_read(path)
            return raw + b'\n# late drift\n' if drift[0] and path == build.ROOT / execute.HOST_SOURCE else raw
        def completed(*args, **kwargs):
            result = old_run(*args, **kwargs)
            drift[0] = True
            return result
        with tempfile.TemporaryDirectory(prefix='native-late-drift-') as temporary:
            local = action_cache.Cache(temporary)
            with patch.object(Path, 'read_bytes', changed), patch.object(execute.process, 'run', side_effect=completed), \
                 patch.object(local, 'commit', side_effect=AssertionError('Stale execution committed')):
                with self.assertRaisesRegex(RuntimeError, 'sources/runtime changed'):
                    execute.observe('rust', reference.arguments(0), invocation_id=987123, cache=local)

    def test_27_unrelated_script_changes_preserve_both_original_actions(self):
        before = {language: build.action(language)[0].key for language in self.targets}
        old = Path.read_bytes
        unrelated = {build.ROOT / (build.MODULE + n) for n in ('reference.py', 'contracts.py', 'codegen.py')}
        unrelated.add(build.ROOT / 'benchmarks/scripts/report.py')
        def changed(path):
            raw = old(path)
            return raw + b'\n# unrelated\n' if path in unrelated else raw
        with patch.object(Path, 'read_bytes', changed):
            self.assertEqual({language: build.action(language)[0].key for language in self.targets}, before)

    def test_28_rust_source_revision_does_not_change_go_original_identity(self):
        before = {language: build.action(language)[0].key for language in self.targets}
        old = Path.read_text
        wanted = build.ROOT / build.PREFIX / 'rust/library.rs'
        def changed(path, *args, **kwargs):
            raw = old(path, *args, **kwargs)
            return raw + '\n// selected Rust source revision\n' if path == wanted else raw
        with patch.object(Path, 'read_text', changed):
            self.assertNotEqual(build.action('rust')[0].key, before['rust'])
            self.assertEqual(build.action('go')[0].key, before['go'])

    def test_29_compiler_lowering_evidence_is_native_and_separate_from_c_ffi(self):
        ir = self.targets['rust']['llvm_ir']
        native_line = next(line for line in ir.splitlines() if line.startswith('define ') and '@native_transform(' in line)
        ffi_line = next(line for line in ir.splitlines() if line.startswith('define ') and '@ffi_transform(' in line)
        self.assertIn('sret([24 x i8])', native_line)
        self.assertNotIn('byval', native_line)
        self.assertIn('byval([24 x i8])', ffi_line)
        assembly = self.targets['go']['compiler_assembly']
        self.assertTrue(any('TEXT' in line and 'main.nativeTransform(SB)' in line and 'ABIInternal' in line
                            for line in assembly.splitlines()))
        self.assertTrue(any('TEXT' in line and 'main.nativeCaller(SB)' in line and 'ABIInternal' in line
                            for line in assembly.splitlines()))

    def test_30_native_gate_rejects_missing_stale_incomplete_or_release_receipts(self):
        token = evidence._controls.set(False)
        try:
            with tempfile.TemporaryDirectory(prefix='native-gate-') as temporary, \
                 patch.object(evidence, 'RECEIPT', Path(temporary) / 'receipt.json'):
                with self.assertRaisesRegex(RuntimeError, 'missing or stale'):
                    evidence.require_validation()
                valid = {'schema_version': 1, 'passed': True, 'checks': evidence.MINIMUM_CHECKS, 'failed': 0, 'model_api_calls': 0,
                         'release_accepted': False, 'scope': 'native_abi_preparation_only',
                         'inputs_sha256': evidence.fingerprints(), 'pins': evidence.pins()}
                evidence.RECEIPT.write_bytes(action_cache.canonical(valid))
                evidence.require_validation()
                for key, value in (('checks', evidence.MINIMUM_CHECKS - 1), ('checks', True), ('passed', False), ('failed', 1),
                                   ('model_api_calls', 1), ('release_accepted', True), ('pins', {}), ('inputs_sha256', {})):
                    evidence.RECEIPT.write_bytes(action_cache.canonical({**valid, key: value}))
                    with self.assertRaisesRegex(RuntimeError, 'missing or stale'):
                        evidence.require_validation()
        finally:
            evidence._controls.reset(token)

    def test_31_wrong_pointer_claim_cannot_write_original_global_or_context_storage(self):
        value = copy.deepcopy(self.gold['go'])
        value['reply'] = {'kind': 'indirect', 'register': 'rdx'}
        compiled = probe.compile('go', action_cache.canonical(value))
        row = execute.observe('go', reference.arguments(0), direction='replacement', probe=compiled['elf'])
        self.assertEqual(row['status'], 'forbidden_memory_write')
        self.assertTrue(row['original_endpoints_preserved'])

    def test_32_only_one_invocation_and_no_oracle_are_sent_to_the_worker(self):
        original = execute.process.run
        captured = []
        def checked(command, **kwargs):
            request = json.loads(kwargs['input'])
            self.assertEqual(set(request), {'language', 'direction', 'values', 'id'})
            self.assertEqual(set(request['values']), {'nonce', 'first', 'second', 'third'})
            self.assertNotIn('expected', request)
            captured.append(request)
            return original(command, **kwargs)
        with tempfile.TemporaryDirectory(prefix='native-single-invocation-') as temporary, \
             patch.object(execute.process, 'run', side_effect=checked):
            result = execute.observe('go', reference.arguments(0), cache=action_cache.Cache(temporary))
        self.assertEqual(len(captured), 1)
        self.assertEqual(result['values'], reference.expected(0, reference.arguments(0)))

    def test_33_aggregate_correctness_uses_both_original_directions(self):
        for language in self.targets:
            result = runtime.grade(language, action_cache.canonical(self.gold[language]), nonces=[0, 1, 0xffffffffffffffe1])
            self.assertEqual((result['passed'], result['total']), (6, 6))
            self.assertTrue(result['task_success'])
            self.assertEqual({row['direction'] for row in result['checks']}, {'replacement', 'bridge'})

    def test_34_coincidental_zero_nonce_credit_does_not_pass_the_task(self):
        value = copy.deepcopy(self.gold['rust'])
        value['nonce']['register'] = 'rcx'
        result = runtime.grade('rust', action_cache.canonical(value), nonces=[0, 1, 0xffffffffffffffe1])
        self.assertEqual((result['passed'], result['total']), (2, 6))
        self.assertFalse(result['task_success'])
        self.assertFalse(result['program_success'])
        self.assertAlmostEqual(result['capabilities']['T7']['accuracy'], 1 / 3)

    def test_35_grade_and_private_vector_revisions_reuse_compile_and_execution(self):
        raw = action_cache.canonical(self.gold['rust'])
        with patch.object(execute.process, 'run', side_effect=AssertionError('Unexpected worker')):
            first = runtime.grade('rust', raw, nonces=[0, 1, 0xffffffffffffffe1])
            self.assertTrue(first['cache_hit'])
            revised = runtime.grade('rust', raw, nonces=[0xffffffffffffffe1, 0])
            self.assertEqual((revised['passed'], revised['total']), (4, 4))
            self.assertNotEqual(first['grading_id'], revised['grading_id'])
            self.assertTrue(runtime.grade('rust', raw, nonces=[0, 1, 0xffffffffffffffe1])['cache_hit'])

    def test_36_invalid_and_wrong_binary_artifacts_never_launch_probes(self):
        with patch.object(probe.process, 'run', side_effect=AssertionError('Unexpected worker')):
            invalid = runtime.grade('rust', b'{}', nonces=[0])
            self.assertFalse(invalid['artifact_valid'])
            self.assertEqual((invalid['passed'], invalid['total']), (0, 2))
            self.assertEqual(invalid['return_provenance'], 'not_executed')
            value = {**self.gold['rust'], 'binary_sha256': '0' * 64}
            wrong = runtime.grade('rust', action_cache.canonical(value), nonces=[0])
            self.assertTrue(wrong['artifact_valid'])
            self.assertEqual(wrong['status'], 'wrong_binary')
            self.assertFalse(wrong['task_success'])
            self.assertEqual(wrong['return_provenance'], 'not_executed')


    def declaration(self, language='rust'):
        name = next(k for k, v in tasks.LANGUAGES.items() if v == language)
        return {'task_id': 'variable_objects_' + name, 'family_id': 'variable_objects', 'absent': False,
                'profile': {'id': name, 'kind': 'native_abi', 'variant': language, 'output_language': None,
                            'artifact_schema': {'native-abi.json': 1}}}

    def test_37_native_packages_reuse_originals_and_supply_no_measured_bindings(self):
        settings = config.load()[0]
        for language in ('rust', 'go'):
            declaration = self.declaration(language)
            with patch.object(build.process, 'run', side_effect=AssertionError('Original compiler worker')):
                record = tasks.freeze(declaration, settings, publish=False)
                self.assertEqual(tasks.validate_frozen(record, declaration, settings), record)
            directory = build.ROOT / record['public_directory']
            metadata = json.loads((directory / 'manifest.json').read_bytes())
            self.assertEqual(record['build_id'], self.targets[language]['build_id'])
            self.assertEqual(record['artifact_schema'], {'native-abi.json': 1})
            self.assertEqual(record['source_language'], language)
            self.assertEqual(metadata['capability_ids'], ['T7'])
            self.assertNotIn('packet', metadata)
            self.assertNotIn('reply', metadata)
            self.assertNotIn('nonce', metadata)
            self.assertEqual(metadata['given_facts']['caller_reply_xor'], reference.CALLER_XOR)
            self.assertEqual(set(metadata['entry_points']), {'original_callee', 'original_caller'})
            self.assertEqual({p.name for p in directory.iterdir()}, set(tasks.FILES))

    def test_38_rehashed_native_public_false_premises_are_rejected(self):
        declaration, settings = self.declaration(), config.load()[0]
        with tempfile.TemporaryDirectory(prefix='native-premises-', dir=build.ROOT / '.cache') as temporary, \
             patch.object(tasks, 'CACHE', Path(temporary)):
            record = tasks.freeze(declaration, settings, publish=False)
            source = build.ROOT / record['public_directory']
            files = {name: (source / name).read_bytes() for name in tasks.FILES}
            metadata = json.loads(files['manifest.json'])
            metadata['given_facts']['field_offsets'][1] = 16
            files['manifest.json'] = action_cache.canonical(metadata)
            hashes = {name: tasks.sha(raw) for name, raw in sorted(files.items())}
            key = tasks.sha(action_cache.canonical(hashes))
            directory = tasks.CACHE / 'task-freezes' / key
            directory.mkdir()
            for name, raw in files.items(): (directory / name).write_bytes(raw)
            changed = {**record, 'public_package_sha256': key, 'public_files_sha256': hashes,
                       'public_directory': str(directory.relative_to(build.ROOT))}
            with self.assertRaisesRegex(ValueError, 'premises'):
                tasks.validate_frozen(changed, declaration, settings)

    def test_39_native_private_vector_and_declaration_admission(self):
        for value in ([], [True], [0, 0], [-1], [2**64]):
            with self.assertRaises(ValueError): tasks.vectors(value)
        for name, value in (('variant', 'unknown'), ('output_language', 'rust'), ('artifact_schema', {'native-abi.json': True})):
            declaration = self.declaration()
            declaration['profile'][name] = value
            with self.assertRaises(ValueError): tasks.contract(declaration)
        with tempfile.TemporaryDirectory(prefix='native-boundary-', dir=build.ROOT / '.cache') as temporary, \
             patch.object(tasks, 'CACHE', Path(temporary)):
            declaration, settings = self.declaration(), config.load()[0]
            record = tasks.freeze(declaration, settings, publish=False)
            (build.ROOT / record['public_directory'] / 'extra.txt').write_text('unapproved')
            with self.assertRaisesRegex(ValueError, 'boundary'):
                tasks.validate_frozen(record, declaration, settings)

    def test_40_frozen_original_substitution_stops_before_probe_compilation(self):
        target = {k: v for k, v in self.targets['rust'].items() if k != 'cache_hit'}
        target['disassembly'] += '\n# substituted diagnostics\n'
        with patch.object(probe, 'compile', side_effect=AssertionError('Candidate compiler')):
            with self.assertRaisesRegex(ValueError, 'AC/CAS'):
                runtime.grade('rust', action_cache.canonical(self.gold['rust']), target=target)


def main():
    started = evidence.begin()
    with evidence.validation_controls(), offline.guard():
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeABIControls))
    receipt = evidence.finish(started, result)
    raise SystemExit(0 if receipt['passed'] else 1)


if __name__ == '__main__':
    main()
