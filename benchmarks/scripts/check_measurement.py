#!/usr/bin/python3
"""Offline measurement controls, including real compilers and isolated execution."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from grading import native, process, returns, store
import pricing
import usage
import cache
from grading.offline import guard


def cases(values):
    return [{'hex': f'{index:02x}', 'point': 'control', 'category': 'directed', 'expected': value}
            for index, value in enumerate(values)]


class ProtocolControls(unittest.TestCase):
    def test_byte_boundaries_and_ids(self):
        values = [b'', b'A\0B', b'\xff\xfe', b'A\nextra', b'B\r\n', '你好'.encode()]
        controls = cases(values)
        output = b''.join(returns.encode(i, value) for i, value in reversed(list(enumerate(values))))
        result = returns.compare(output, controls)
        self.assertFalse(result['errors'])
        self.assertTrue(all(row['passed'] for row in result['records']))
        wrong = returns.compare(b''.join(returns.encode(i, value + b'\r') for i, value in enumerate(values)), controls)
        self.assertEqual(sum(row['passed'] for row in wrong['records']), 0)

    def test_newline_cannot_shift_later_calls(self):
        result = returns.compare(returns.encode(0, b'A\nextra') + returns.encode(1, b'B') + returns.encode(2, b'C'), cases(['A', 'B', 'C']))
        self.assertEqual([row['passed'] for row in result['records']], [False, True, True])

    def test_invalid_records(self):
        good = returns.encode(0, b'A')
        for data, category in [(good + good, 'duplicate_return_id'), (b'', 'missing_return_ids'),
                               (good + returns.encode(1, b'B'), 'extra_return_ids'),
                               (good.replace(b':1:41', b':2:41'), 'invalid_return_length'),
                               (b'noise\n' + good, 'unexpected_stdout'), (good + b'tail', 'unexpected_stdout'),
                               (good.replace(b':ok:', b':error:'), 'missing_return_ids')]:
            with self.subTest(category=category):
                self.assertIn(category, returns.compare(data, cases(['A']))['errors'])
        self.assertFalse(returns.compare(returns.encode(0, b'01', 'bytes').replace(b':bytes:', b':int:'), cases([1]))['records'][0]['passed'])

    def test_pipe_limits_and_timeout(self):
        for pipe in ('stdout', 'stderr'):
            with self.subTest(pipe=pipe), self.assertRaises(process.OutputLimitExceeded):
                process.run([sys.executable, '-c', f'import sys; sys.{pipe}.write("x"*1000000)'], output_limit=1024, timeout=5)
        with self.assertRaises(subprocess.TimeoutExpired):
            process.run([sys.executable, '-c', 'while True: pass'], timeout=0.1)


class NativeControls(unittest.TestCase):
    def evaluate(self, code, language, values, track='complex', standard='c++20', abi=None):
        with tempfile.TemporaryDirectory(prefix='measurement-native-') as temporary:
            return native.evaluate(code, language, cases(values), track, standard, abi, Path(temporary))

    def test_cpp_string_boundaries(self):
        template = '#include <string>\n#include <span>\n#include <cstdint>\nstd::string reconstructed(std::span<const std::uint8_t> data) { const char *values[] = {"A", VALUE, "C"}; return values[data[0]]; }\n'
        for value, expected in [('"B"', 3), ('"B\\r"', 2), ('"B\\nextra"', 2), ('""', 2)]:
            with self.subTest(value=value):
                result = self.evaluate(template.replace('VALUE', value), 'cpp', ['A', 'B', 'C'])
                self.assertTrue(result['compiled'], result)
                self.assertEqual(result['passed'], expected, result)
                self.assertEqual(result['program_success'], expected == 3)

    def test_cpp_container_standards(self):
        code = '#include <string>\n#include <vector>\n#include <cstdint>\nstd::string reconstructed(const std::vector<std::uint8_t>& data) { return std::to_string(data[0]); }\n'
        for standard, abi in [('c++11', 0), ('c++14', 1)]:
            result = self.evaluate(code, 'cpp', ['0', '1'], 'stl', standard, abi)
            self.assertTrue(result['program_success'], result)

    def test_cpp_arbitrary_bytes(self):
        code = '#include <string>\nstd::string reconstructed(std::span<const std::uint8_t> data) { return data[0] == 0 ? std::string() : std::string("A\\0B\\xff", 4); }\n'
        result = self.evaluate(code, 'cpp', [b'', b'A\0B\xff'])
        self.assertTrue(result['program_success'], result)

    def test_c_integer_contract(self):
        code = 'int64_t reconstructed(const uint8_t *data, size_t size) { return size ? (int64_t)data[0]-1 : -1; }\n'
        result = self.evaluate(code, 'c', [-1, 0, 1], 'basic')
        self.assertTrue(result['program_success'], result)

    def test_rust_original_language(self):
        code = '//! A valid inner crate comment.\npub fn reconstructed(data: &[u8]) -> String { let values = ["A", "B\\nextra", "C"]; values[data[0] as usize].to_owned() }\n'
        result = self.evaluate(code, 'rust', ['A', 'B', 'C'], 'core')
        self.assertTrue(result['compiled'], result)
        self.assertEqual(result['passed'], 2, result)

    def test_go_original_language_and_ast(self):
        code = 'package main\n// A discussion of "unsafe" and "C" is legal.\nfunc reconstructed(data []byte) string { values := []string{"A", "B\\r", "C"}; return values[data[0]] }\n'
        result = self.evaluate(code, 'go', ['A', 'B', 'C'])
        self.assertTrue(result['compiled'], result)
        self.assertEqual(result['passed'], 2, result)
        legal = ['package main\n// "unsafe"\n', 'package main\nconst explanation = "unsafe"\n',
                 'package main\nimport f "fmt"\nvar _ = f.Println\n']
        for source in legal:
            self.assertEqual(native.go_policy(source)['status'], 'ok')
        forbidden = ['import "unsafe"', 'import _ `unsafe`', 'import alias "\\x75nsafe"',
                     'import ( . "unsafe"; _ "fmt" )', 'import "C"']
        for declaration in forbidden:
            with self.subTest(declaration=declaration):
                self.assertEqual(native.go_policy('package main\n' + declaration + '\n')['status'], 'forbidden_import')
        self.assertEqual(native.go_policy('package main\nfunc broken(')['status'], 'parse_error')

    def test_stdout_noise_is_not_success(self):
        code = '#include <string>\n#include <cstdio>\nstd::string reconstructed(std::span<const std::uint8_t>) { std::puts("noise"); return "A"; }'
        result = self.evaluate(code, 'cpp', ['A'])
        self.assertFalse(result['program_success'])
        self.assertIn('unexpected_stdout', result['protocol_errors'])
        self.assertEqual(result['return_provenance'], 'native_process_unauthenticated')
        # Framing deliberately does not claim to authenticate a same-process return.
        self.assertTrue(returns.compare(returns.encode(0, b'A'), cases(['A']))['records'][0]['passed'])

    def test_early_exit_without_returns_is_not_success(self):
        template = '#include <string>\n#include <cstdlib>\nstd::string reconstructed(std::span<const std::uint8_t>) { std::_Exit(EXIT_CODE); }'
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                result = self.evaluate(template.replace('EXIT_CODE', str(exit_code)), 'cpp', ['A'])
                self.assertTrue(result['compiled'], result)
                self.assertFalse(result['program_success'], result)
                self.assertEqual(result['candidate_exit_code'], exit_code)
                self.assertIn('missing_return_ids', result['protocol_errors'])

    def test_forged_native_frame_exposes_pending_return_provenance(self):
        # R6 is unfinished: the function never returns, yet it can impersonate
        # the in-process wrapper. Keep this explicit until a host executor exists.
        code = r'''
#include <string>
#include <cstdio>
#include <cstdlib>
std::string reconstructed(std::span<const std::uint8_t>) {
    std::fputs("__REA_RETURN_V2__:0:ok:bytes:1:41\n", stdout);
    std::fflush(stdout);
    std::_Exit(0);
}
'''
        result = self.evaluate(code, 'cpp', ['A'])
        self.assertTrue(result['program_success'], result)
        self.assertEqual(result['return_provenance'], 'native_process_unauthenticated')


class UsageControls(unittest.TestCase):
    def receipt(self):
        return {'model': 'test', 'provider': 'api', 'started_at': '2026-10-20T00:00:00Z',
                'exit_code': 1, 'requests': [{'sequence': 1, 'http_status': 200, 'finished_at': '2026-10-20T00:00:01Z'}],
                'usage_observations': [{'sequence': 1, 'finished_at': '2026-10-20T00:00:01Z',
                                       'usage': {'inputTokens': 10, 'outputTokens': 5, 'totalTokens': 15}}]}

    def test_explicit_incomplete_and_task_failure_independent(self):
        receipt = self.receipt()
        self.assertEqual(usage.ledger(receipt)['status'], 'complete')
        receipt.update(exit_code=0, usage_complete=False)
        self.assertEqual(usage.ledger(receipt)['status'], 'partial')
        receipt['requests'].append({'sequence': 2, 'http_status': 200})
        self.assertFalse(usage.ledger(receipt)['usage_complete'])
        self.assertEqual(usage.ledger({'exit_code': 0, 'usage_complete': True})['status'], 'unknown')

    def test_stream_dedup_and_retry_identity(self):
        receipt = self.receipt()
        receipt['usage_observations'].append(copy.deepcopy(receipt['usage_observations'][0]))
        self.assertEqual(usage.ledger(receipt)['reported_usage']['totalTokens'], 15)
        receipt['requests'].append({'sequence': 2, 'finished_at': '2026-10-20T00:00:02Z'})
        receipt['usage_observations'].append({**receipt['usage_observations'][0], 'sequence': 2})
        self.assertEqual(usage.ledger(receipt)['reported_usage']['totalTokens'], 30)
        self.assertTrue(usage.ledger(receipt)['usage_complete'])
        receipt['usage_observations'][0]['usage']['inputTokens'] = -1
        with self.assertRaises(ValueError):
            usage.ledger(receipt)

    def test_raw_glm_stream_and_cache(self):
        receipt = self.receipt()
        event = {'sequence': 1, 'finished_at': '2026-10-20T00:00:01Z',
                 'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25,
                           'prompt_tokens_details': {'cached_tokens': 12}}}
        receipt['usage_observations_raw'] = [event, event]
        result = usage.ledger(receipt)
        self.assertEqual(result['reported_usage'], {'inputTokens': 8, 'cacheReadTokens': 12, 'outputTokens': 5, 'totalTokens': 25})
        self.assertTrue(result['usage_complete'])
        del receipt['usage_observations_raw']
        del receipt['usage_observations'][0]['sequence']
        self.assertEqual(usage.ledger(receipt)['status'], 'partial')

    def test_missing_prices_and_known_subtotal(self):
        receipt = self.receipt()
        self.assertIsNone(pricing.estimate(receipt, {})['low_usd'])
        self.assertFalse(usage.annotate_cost({'low_usd': 0.1, 'high_usd': 0.2}, usage.ledger({}))['usage_complete'])
        self.assertIsNone(usage.annotate_cost({'low_usd': 0.1}, usage.ledger({}))['estimated_total_low_usd'])
        self.assertEqual(usage.ledger({'reported_usage': {'inputTokens': 0, 'outputTokens': 0}})['status'], 'unknown')
        with tempfile.TemporaryDirectory(prefix='accounting-store-') as temporary:
            directory = Path(temporary)
            raw = json.dumps(receipt)
            (directory / 'result.json').write_text(raw)
            first = usage.save(directory, {'unit_tokens': 1000000}, {'low_usd': 0.1})
            self.assertEqual(usage.save(directory, {'unit_tokens': 1000000}, {'low_usd': 0.1}), first)
            second = usage.save(directory, {'unit_tokens': 1000}, {'low_usd': 100.0})
            self.assertNotEqual(first, second)
            self.assertEqual((directory / 'result.json').read_text(), raw)

    def test_offline_guard(self):
        with guard():
            for command in [['node', 'model-harness'], ['dsh', '--version'], ['/bin/sh', '-c', 'provider']]:
                # A provider cannot be admitted merely by using a --version flag.
                with self.assertRaisesRegex(RuntimeError, 'refused'):
                    subprocess.Popen(command)
            with self.assertRaises(RuntimeError):
                with socket.socket() as connection:
                    connection.connect(('127.0.0.1', 9))


class StoreControls(unittest.TestCase):
    def test_cache_file_confinement(self):
        runtime = cache.records.ROOT / '.cache/benchmark-runtime'
        runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='confined-files-', dir=runtime) as temporary:
            base = Path(temporary)
            root = base / 'root'
            root.mkdir()
            (root / 'kept').write_bytes(b'kept')
            (base / 'outside').write_bytes(b'outside')
            digest = hashlib.sha256(b'kept').hexdigest()
            cache.verify_files(root, {'kept': digest})
            (root / 'escape').symlink_to(base / 'outside')
            for name in ['escape', '../outside', str(base / 'outside')]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    cache.verify_files(root, {name: digest})

    def test_append_only_and_reuse(self):
        values = cases([-1, 0, 1])
        code = 'int64_t reconstructed(const uint8_t *data, size_t size) { return size ? (int64_t)data[0]-1 : -1; }\n'
        with tempfile.TemporaryDirectory(prefix='measurement-store-') as temporary:
            directory = Path(temporary)
            (directory / 'reconstructed.c').write_text(code)
            (directory / 'answer.md').write_text('```c\n' + code + '```\n')
            original = {'compiled': True, 'passed': 3, 'total': 3, 'failure': None,
                        'candidate_source_sha256': hashlib.sha256(code.encode()).hexdigest()}
            (directory / 'grade.json').write_text(json.dumps(original))
            before = {name: (directory / name).read_bytes() for name in ['grade.json', 'answer.md', 'reconstructed.c']}
            task = {'language': 'c', 'track': 'basic'}
            with patch.object(store, 'vectors', return_value=(values, 'fixture-v1')):
                first = store.grade(directory, task)
                with patch.object(native, 'evaluate', side_effect=AssertionError('Duplicate grading')):
                    second = store.grade(directory, task)
                self.assertEqual(first, second)
                self.assertEqual(store.read_selected(directory)[0], first)
                self.assertTrue(first['program_success'])
            changed = cases([-1, 0, 9])
            with patch.object(store, 'vectors', return_value=(changed, 'fixture-v2')):
                revised = store.grade(directory, task)
            self.assertEqual(revised['passed'], 2)
            self.assertNotEqual(first['grading_id'], revised['grading_id'])
            for name, data in before.items():
                self.assertEqual((directory / name).read_bytes(), data)
            (directory / 'reconstructed.c').write_text(code + '\n')
            with self.assertRaisesRegex(ValueError, 'candidate changed'):
                store.read_selected(directory)


if __name__ == '__main__':
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network/model access forbidden')):
        unittest.main(verbosity=2)
