#!/usr/bin/python3
"""Real freestanding compilation and adversarial host-return controls; no models."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from grading import returns
from grading.c_executor import elf, runtime
from grading.offline import guard


def region(name, value, *, readable=None, writable=None):
    return {'id': name, 'data_hex': value.hex(), 'readable': [[0, len(value)]] if readable is None else readable,
            'writable': [] if writable is None else writable}


def pointer(name, offset=0):
    return {'region': name, 'offset': offset}


class ExecutorControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='c-executor-controls-')
        # These are synthetic fixture actions, outside the benchmark registry.
        # Unchanged compiler/VM actions can be reused across control runs.
        cls.cache = action_cache.Cache(action_cache.ROOT / '.cache/benchmark-c-executor/control-actions-v1')
        cls.compiled = {}
        cls.sequence = 0

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def build(self, source, exports=('reconstructed',), header=''):
        key = (source, tuple(exports), header)
        if key not in self.compiled:
            self.compiled[key] = runtime.compile(source, exports, header=header, cache=self.cache)
        result = self.compiled[key]
        self.assertEqual(result['status'], 'compiled', result)
        return result['elf']

    def call(self, source, arguments=(), regions=(), *, callbacks=(), state=None, entry='reconstructed', exports=None, header=''):
        raw = self.build(source, exports or (entry,), header)
        type(self).sequence += 1
        result = runtime.invoke(raw, {'id': self.sequence, 'arguments': list(arguments), 'regions': list(regions),
                                      'callbacks': list(callbacks)}, entry=entry, state=state, cache=self.cache)
        self.assertEqual(result['invocation_id'], self.sequence)
        self.assertEqual(result['provenance'], 'host_emulator_return_v1')
        return result

    def test_real_return_is_host_owned(self):
        # One cold positive always verifies actual installed bytes and transport,
        # even when other unchanged fixture actions have valid cache entries.
        cold = action_cache.Cache(Path(self.temporary.name) / 'cold-positive')
        built = runtime.compile('uint64_t reconstructed(uint64_t value) { return value + 7; }', cache=cold)
        result = runtime.invoke(built['elf'], {'id': 0, 'arguments': [3], 'regions': []}, cache=cold)
        self.assertEqual(result['status'], 'returned', result)
        self.assertEqual(result['return_registers']['rax'], 10)
        self.assertEqual(result['engine_version'], '2.1.4')
        self.assertGreater(result['instructions'], 0)
        self.assertNotIn('stdout', result)
        record = result['return_record']
        self.assertEqual(record['invocation_id'], result['invocation_id'])
        self.assertEqual(record['length'], 2)
        self.assertEqual(bytes.fromhex(record['value_hex']), b'10')

    def test_typed_returns_use_shared_exact_byte_records(self):
        raw = self.build('int32_t reconstructed(void) { return -2; }')
        result = runtime.invoke(raw, {'id': 0, 'regions': [], 'arguments': [], 'return_type': 'i32'}, cache=self.cache)
        frame = bytes.fromhex(result['return_record']['frame_hex'])
        compared = returns.compare(frame, [{'expected': -2}])
        self.assertFalse(compared['errors'])
        self.assertTrue(compared['records'][0]['passed'])
        void = self.build('void reconstructed(void) { }')
        result = runtime.invoke(void, {'id': 0, 'regions': [], 'arguments': [], 'return_type': 'void'}, cache=self.cache)
        self.assertEqual(result['return_record']['length'], 0)
        self.assertEqual(result['return_record']['value_hex'], '')

    def test_sse2_and_x87_intermediates_work_with_integer_abi(self):
        sources = [
            ('uint64_t reconstructed(uint64_t value) { volatile double scale=1.25; return (uint64_t)((double)value*scale); }', 10),
            ('uint64_t reconstructed(uint64_t value) { volatile long double add=0.75L; return (uint64_t)((long double)value+add); }', 8),
        ]
        for source, expected in sources:
            result = self.call(source, [8])
            self.assertEqual(result['status'], 'returned', result)
            self.assertEqual(result['return_registers']['rax'], expected)

    def test_exact_binary_output_and_preserved_bytes(self):
        source = '''
int32_t reconstructed(const uint8_t *input, size_t length, uint8_t *output,
                      size_t capacity, size_t *output_length) {
    if (capacity < length) return -2;
    for (size_t index = 0; index < length; ++index) output[index] = input[index];
    *output_length = length;
    return 0;
}
'''
        value = b'A\0B\xff\r\n'
        output = b'!' * (len(value) + 2)
        result = self.call(source, [pointer('input'), len(value), pointer('output', 1), len(value), pointer('length')],
                           [region('input', value), region('output', output, writable=[[1, len(value)]]),
                            region('length', bytes(8), writable=[[0, 8]])])
        self.assertEqual(result['status'], 'returned', result)
        values = {r['id']: bytes.fromhex(r['data_hex']) for r in result['regions']}
        self.assertEqual(values['input'], value)
        self.assertEqual(values['output'], b'!' + value + b'!')
        self.assertEqual(values['length'], struct.pack('<Q', len(value)))

    def test_overlapping_buffers_and_equivalent_implementation(self):
        template = '''
extern void *memmove(void *, const void *, size_t);
uint64_t reconstructed(uint8_t *output, const uint8_t *input, size_t length) {
    BODY
    return length;
}
'''
        initial = b'ABCDEFGH!!'
        for body in ('memmove(output, input, length);',
                     'for (size_t index = length; index; --index) output[index-1] = input[index-1];'):
            result = self.call(template.replace('BODY', body), [pointer('arena', 2), pointer('arena'), 6],
                               [region('arena', initial, writable=[[2, 6]])])
            self.assertEqual(result['status'], 'returned', result)
            self.assertEqual(bytes.fromhex(result['regions'][0]['data_hex']), b'ABABCDEF!!')

    def test_stack_passed_arguments_and_empty_allocations(self):
        source = 'uint64_t reconstructed(uint64_t a,uint64_t b,uint64_t c,uint64_t d,uint64_t e,uint64_t f,uint64_t g,uint64_t h) { return a+2*b+3*c+4*d+5*e+6*f+7*g+8*h; }'
        result = self.call(source, list(range(1, 9)), [region('empty', b'')])
        self.assertEqual(result['status'], 'returned', result)
        self.assertEqual(result['return_registers']['rax'], 204)
        self.assertEqual(result['regions'][0]['data_hex'], '')

    def test_stdout_forgery_and_exit_syscalls_are_rejected(self):
        source = r'''
uint64_t reconstructed(void) {
    static const char forged[] = "__REA_RETURN_V2__:0:ok:int:1:37\n";
    __asm__ volatile("syscall" :: "a"(1), "D"(1), "S"(forged), "d"(sizeof(forged)-1) : "rcx", "r11", "memory");
    __asm__ volatile("syscall" :: "a"(60), "D"(0) : "rcx", "r11", "memory");
    return 7;
}
'''
        result = self.call(source)
        self.assertEqual(result['status'], 'forbidden_syscall', result)
        self.assertIsNone(result['return_registers'])
        self.assertIsNone(result['return_record'])
        result = self.call('uint64_t reconstructed(void) { __asm__ volatile("syscall" :: "a"(60), "D"(0) : "rcx", "r11", "memory"); return 7; }')
        self.assertEqual(result['status'], 'forbidden_syscall', result)

    def test_traps_halt_and_time_instructions_cannot_return(self):
        for instruction in ('ud2', 'int $0x80', 'hlt', 'rdtsc', 'sysenter'):
            with self.subTest(instruction=instruction):
                result = self.call('uint64_t reconstructed(void) { __asm__ volatile("' + instruction + '"); return 7; }')
                self.assertNotEqual(result['status'], 'returned', result)
                self.assertIsNone(result['return_registers'])

    def test_out_of_bounds_read_inside_a_mapped_page_is_rejected(self):
        result = self.call('uint64_t reconstructed(const uint8_t *p) { return p[1]; }', [pointer('data')],
                           [region('data', b'AB', readable=[[0, 1]])])
        self.assertEqual(result['status'], 'forbidden_memory_read', result)

    def test_out_of_bounds_and_readonly_writes_are_rejected(self):
        for statement in ('p[-1] = 9;', 'p[1] = 9;'):
            result = self.call('uint64_t reconstructed(uint8_t *p) { ' + statement + ' return 7; }', [pointer('data', 1)],
                               [region('data', b'ABC', writable=[[1, 1]])])
            self.assertEqual(result['status'], 'forbidden_memory_write', result)
        result = self.call('uint64_t reconstructed(uint8_t *p) { p[0] = 9; return 7; }', [pointer('data')],
                           [region('data', b'ABC')])
        self.assertEqual(result['status'], 'forbidden_memory_write', result)

    def test_code_and_caller_return_slot_cannot_be_modified(self):
        sources = [
            'uint64_t reconstructed(void) { ((volatile unsigned char *)(void *)reconstructed)[0] = 0; return 7; }',
            '__attribute__((naked)) uint64_t reconstructed(void) { __asm__("movq $0, (%rsp); ret"); }',
        ]
        for source in sources:
            result = self.call(source)
            self.assertNotEqual(result['status'], 'returned', result)
            self.assertIsNone(result['return_registers'])

    def test_callee_saved_registers_direction_flag_and_control_state(self):
        sources = [
            ('__attribute__((naked)) uint64_t reconstructed(void) { __asm__("mov $1, %rbx; mov $7, %rax; ret"); }', 'callee_saved_register_corruption'),
            ('uint64_t reconstructed(void) { __asm__ volatile("std"); return 7; }', 'direction_flag_corruption'),
            ('uint64_t reconstructed(void) { unsigned v=0x3f80; __asm__ volatile("ldmxcsr %0" :: "m"(v)); return 7; }', 'caller_control_state_corruption'),
        ]
        for source, expected in sources:
            self.assertEqual(self.call(source)['status'], expected)

    def test_boundary_requires_valid_stack_but_accepts_equivalent_return(self):
        wrong = '__attribute__((naked)) uint64_t reconstructed(void) { __asm__("mov $0x70000000, %eax; jmp *%rax"); }'
        self.assertEqual(self.call(wrong)['status'], 'caller_stack_corruption')
        equivalent = '__attribute__((naked)) uint64_t reconstructed(void) { __asm__("mov $7, %eax; add $8, %rsp; mov $0x70000000, %edx; jmp *%rdx"); }'
        result = self.call(equivalent)
        self.assertEqual(result['status'], 'returned', result)
        self.assertEqual(result['return_registers']['rax'], 7)

    def test_instruction_budget_exhaustion_is_explicit(self):
        values = runtime.execute_profile()
        values['limits']['instructions'] = 25
        with patch.object(runtime, 'execute_profile', return_value=values):
            result = self.call('uint64_t reconstructed(void) { for (;;) __asm__ volatile(""); }')
        self.assertEqual(result['status'], 'instruction_budget', result)
        self.assertIsNone(result['return_registers'])

    def test_callbacks_tail_calls_and_callback_budget(self):
        for expression, expected in [('callback(value, 2) + 1', 10), ('callback(value, 2)', 9)]:
            source = 'uint64_t reconstructed(uint64_t value, uint64_t (*callback)(uint64_t,uint64_t)) { return ' + expression + '; }'
            result = self.call(source, [3, {'callback': 'policy'}], callbacks=[{'id': 'policy', 'returns': [9]}])
            self.assertEqual(result['status'], 'returned', result)
            self.assertEqual(result['return_registers']['rax'], expected)
            self.assertEqual(result['callbacks'][0]['arguments'][:2], [3, 2])
        source = 'uint64_t reconstructed(uint64_t (*callback)(void)) { callback(); return callback(); }'
        result = self.call(source, [{'callback': 'policy'}], callbacks=[{'id': 'policy', 'returns': [9]}])
        self.assertEqual(result['status'], 'callback_budget', result)

    def test_persistent_image_state_is_explicit_and_not_future_input(self):
        source = 'static uint64_t count; uint64_t reconstructed(uint64_t value) { count += value; return count; }'
        first = self.call(source, [2])
        second = self.call(source, [3], state=first['state'])
        fresh = self.call(source, [3])
        self.assertEqual(first['return_registers']['rax'], 2)
        self.assertEqual(second['return_registers']['rax'], 5)
        self.assertEqual(fresh['return_registers']['rax'], 3)

    def test_multiple_exports_and_contract_prototypes(self):
        source = 'uint64_t first(uint64_t value) { return value+1; } uint64_t second(uint64_t value) { return value+2; }'
        result = self.call(source, [4], entry='second', exports=('first', 'second'))
        self.assertEqual(result['return_registers']['rax'], 6)
        result = runtime.compile('uint64_t reconstructed(uint64_t value) { return value; }',
                                 header='int32_t reconstructed(const uint8_t *, size_t, uint8_t *, size_t, size_t *);', cache=self.cache)
        self.assertEqual(result['status'], 'compile_error')

    def test_no_syscalls_means_no_original_binary_or_host_file_access(self):
        # openat cannot access a pathname, even if the filename is embedded in
        # the candidate. The guest has no host descriptors or filesystem API.
        source = 'uint64_t reconstructed(void) { static const char path[]="/environment/profile.json"; uint64_t result; __asm__ volatile("syscall" : "=a"(result) : "a"(257), "D"(-100), "S"(path), "d"(0) : "rcx", "r11", "memory"); return result; }'
        result = self.call(source)
        self.assertEqual(result['status'], 'forbidden_syscall', result)

    def test_oracle_fields_and_undeclared_inputs_are_refused(self):
        raw = self.build('uint64_t reconstructed(void) { return 7; }')
        bad = {'id': 1, 'regions': [], 'arguments': [], 'expected': 7}
        with self.assertRaisesRegex(RuntimeError, 'oracle data is forbidden'):
            runtime.invoke(raw, bad, cache=self.cache)

    def test_compile_and_execution_actions_reuse_valid_cache(self):
        source = 'uint64_t reconstructed(uint64_t value) { return value ^ 0x1234; }'
        first = runtime.compile(source, cache=self.cache)
        with patch.object(runtime, '_invoke', side_effect=AssertionError('Unexpected worker')):
            again = runtime.compile(source, cache=self.cache)
        self.assertEqual(first['action_id'], again['action_id'])
        self.assertTrue(again['cache_hit'])
        invocation = {'id': 987, 'regions': [], 'arguments': [11]}
        first_call = runtime.invoke(first['elf'], invocation, cache=self.cache)
        with patch.object(runtime, '_invoke', side_effect=AssertionError('Unexpected worker')):
            cached = runtime.invoke(first['elf'], invocation, cache=self.cache)
        self.assertEqual(first_call['action_id'], cached['action_id'])
        self.assertTrue(cached['cache_hit'])

    def test_corrupt_or_unsupported_elf_never_produces_a_return(self):
        raw = self.build('uint64_t reconstructed(void) { return 7; }')
        bad_values = [b'not an ELF', raw[:20], raw[:4] + b'\x01' + raw[5:]]
        pie = bytearray(raw)
        struct.pack_into('<H', pie, 16, 3)
        bad_values.append(bytes(pie))
        overflow = bytearray(raw)
        struct.pack_into('<Q', overflow, 32, (1 << 64) - 1)
        bad_values.append(bytes(overflow))
        program_offset = struct.unpack_from('<Q', raw, 32)[0]
        dynamic = bytearray(raw)
        struct.pack_into('<I', dynamic, program_offset, 2)
        bad_values.append(bytes(dynamic))
        wx = bytearray(raw)
        struct.pack_into('<I', wx, program_offset + 4, 7)
        bad_values.append(bytes(wx))
        for bad in bad_values:
            with self.subTest(sha=hashlib.sha256(bad).hexdigest()[:8]):
                result = runtime.invoke(bad, {'id': 999, 'regions': [], 'arguments': []}, cache=self.cache)
                self.assertEqual(result['status'], 'unsupported_elf', result)
                self.assertIsNone(result['return_registers'])

    def test_unresolved_runtime_helpers_are_compile_errors(self):
        result = runtime.compile('extern void exit(int); uint64_t reconstructed(void) { exit(0); return 7; }', cache=self.cache)
        self.assertEqual(result['status'], 'compile_error')

    def test_public_header_does_not_reduce_candidate_source_budget(self):
        prefix = 'uint64_t reconstructed(void) { return 42; }\n'
        maximum = runtime.compile_profile()['max_source_bytes']
        source = prefix + '/*' + ' ' * (maximum - len(prefix) - 4) + '*/'
        self.assertEqual(len(source.encode()), maximum)
        compiled = runtime.compile(source, header='typedef unsigned int public_header_type;\n', cache=self.cache)
        self.assertEqual(compiled['status'], 'compiled')

    def test_effective_profiles_exclude_download_bookkeeping(self):
        original = runtime.execute_profile()
        changed = runtime.policy()
        changed['engine']['url'] = 'https://mirror.example.invalid/same-wheel'
        changed['engine']['wheel'] = 'same-bytes-another-filename.whl'
        with patch.object(runtime, 'policy', return_value=changed):
            self.assertEqual(original, runtime.execute_profile())
        native = action_cache.dependencies('grading', language='cpp')
        self.assertFalse(any('/c_executor/' in name for name in native))
        self.assertNotIn('benchmarks/scripts/grading/c_executor/machine.py', action_cache.dependencies('c_compile'))
        self.assertNotIn('benchmarks/scripts/grading/c_executor/helpers.c', action_cache.dependencies('c_execute'))

    def test_incomplete_controls_block_production_before_any_worker(self):
        from grading.c_executor import evidence
        token = runtime._controls.set(False)
        try:
            with patch.object(evidence, 'for_report', return_value={'controls_passed': False, 'controls_current': False}), \
                 patch.object(runtime, '_invoke', side_effect=AssertionError('Unexpected execution')):
                with self.assertRaisesRegex(RuntimeError, 'controls are missing or stale'):
                    runtime.compile('uint64_t reconstructed(void) { return 1; }', cache=self.cache)
        finally:
            runtime._controls.reset(token)

    def test_runtime_changed_after_controls_is_refused(self):
        from grading.c_executor import evidence, image
        identity, record = runtime.engine_record()
        saved = {'controls_passed': True, 'controls_current': True,
                 'engine_id': identity, 'base_image': record['base_image']}
        with patch.object(evidence, 'for_report', return_value=saved), \
             patch.object(runtime, 'engine_record', return_value=('f' * 64, record)):
            with self.assertRaisesRegex(RuntimeError, 'runtime changed after validation'):
                evidence.require_validation()
        with patch.object(evidence, 'for_report', return_value=saved), \
             patch.object(runtime, 'engine_record', return_value=(identity, record)), \
             patch.object(image, 'record', return_value={'id': 'f' * 64}):
            with self.assertRaisesRegex(RuntimeError, 'runtime changed after validation'):
                evidence.require_validation()

    def test_module_drift_is_rejected_even_with_restored_timestamp(self):
        with tempfile.TemporaryDirectory(prefix='executor-drift-') as temporary:
            cache = Path(temporary)
            identity = 'e' * 64
            source = cache / identity / 'unicorn/test.py'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'value = 1\n')
            record = {'files': {'unicorn/test.py': {'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'bytes': source.stat().st_size}}}
            with patch.object(runtime, 'CACHE', cache), patch.object(runtime, 'engine_record', return_value=(identity, record)):
                with runtime.addon() as (root, _, _):
                    loaded = root / 'unicorn/test.py'
                    timestamp = loaded.stat().st_mtime_ns
                loaded.chmod(0o644)
                loaded.write_bytes(b'value = 2\n')
                loaded.chmod(0o444)
                os.utime(loaded, ns=(timestamp, timestamp))
                with self.assertRaisesRegex(ValueError, 'changed before execution'):
                    with runtime.addon():
                        self.fail('Changed module was admitted')
                sealed, _, _ = runtime._addons.pop(identity)
                sealed.cleanup()

    def test_source_drift_is_refused_before_worker_launch(self):
        environment = runtime.platform()
        with patch.object(runtime.process, 'run', side_effect=AssertionError('Unexpected worker')):
            with self.assertRaisesRegex(RuntimeError, 'source changed after action preparation'):
                runtime._invoke('c_compile', {}, environment, runtime.compile_profile(), None, {})

    def test_zero_never_disables_a_budget(self):
        value = runtime.policy()
        with tempfile.TemporaryDirectory(prefix='executor-policy-') as temporary:
            path = Path(temporary) / 'policy.json'
            for key in ('instructions', 'emulator_timeout_us', 'worker_wall_seconds', 'worker_memory_bytes'):
                changed = copy.deepcopy(value)
                changed['limits'][key] = 0
                path.write_text(json.dumps(changed))
                with self.subTest(limit=key), patch.object(runtime, 'CONFIG', path), self.assertRaisesRegex(ValueError, 'positive integers'):
                    runtime.policy()

    def test_relocations_tls_and_overlapping_segments_are_not_silently_loaded(self):
        raw = self.build('uint64_t reconstructed(void) { return 7; }')
        header = elf.HEADER.unpack_from(raw)
        program_offset, program_size = header[5], header[9]
        for ptype in (3, 7):
            changed = bytearray(raw)
            struct.pack_into('<I', changed, program_offset, ptype)
            with self.subTest(type=ptype), self.assertRaises(elf.UnsupportedELF):
                elf.load(bytes(changed), 'reconstructed', runtime.policy()['limits'])
        overlap = bytearray(raw)
        overlap[program_offset + program_size:program_offset + program_size * 2] = raw[program_offset:program_offset + program_size]
        with self.assertRaisesRegex(elf.UnsupportedELF, 'Overlapping'):
            elf.load(bytes(overlap), 'reconstructed', runtime.policy()['limits'])
        section_offset, section_size = header[6], header[11]
        reloc = bytearray(raw)
        struct.pack_into('<I', reloc, section_offset + section_size + 4, 9)
        with self.assertRaisesRegex(elf.UnsupportedELF, 'relocations'):
            elf.load(bytes(reloc), 'reconstructed', runtime.policy()['limits'])


if __name__ == '__main__':
    from grading.c_executor import evidence
    started = evidence.begin()
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    with guard(), runtime.validation_controls():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence.finish(started, result)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('AUTHENTICATED C EXECUTOR: PASS;', result.testsRun, 'controls; zero model API calls')
