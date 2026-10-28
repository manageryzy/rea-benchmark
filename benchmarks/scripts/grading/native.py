"""One native evaluator for the existing basic, complex, STL and Rust data tasks."""
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import fcntl
import time

from . import process, returns

BENCH = Path(__file__).resolve().parents[2]
_helper_lock = threading.Lock()


@functools.lru_cache()
def isolation():
    spec = importlib.util.spec_from_file_location('grading_isolation', BENCH / 'scripts/grade.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cpp_driver(track, kind):
    argument = 'bytes' if track == 'stl' else 'std::span<const std::uint8_t>(bytes.data(), bytes.size())'
    value = 'std::to_string(reconstructed(' + argument + '))' if kind == 'int' else 'reconstructed(' + argument + ')'
    return r'''
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>
SPAN_INCLUDE
#include "candidate.cpp"
int main() {
    std::string line;
    std::size_t sequence = 0;
    const char digits[] = "0123456789abcdef";
    while (std::getline(std::cin, line)) {
        std::vector<std::uint8_t> bytes;
        if (line != "-") for (std::size_t i = 0; i + 1 < line.size(); i += 2)
            bytes.push_back(static_cast<std::uint8_t>(std::stoul(line.substr(i, 2), nullptr, 16)));
        const std::string value = VALUE;
        std::string hex;
        for (unsigned char byte : value) { hex += digits[byte >> 4]; hex += digits[byte & 15]; }
        const std::string record = "__REA_RETURN_V2__:" + std::to_string(sequence++) +
            ":ok:KIND:" + std::to_string(value.size()) + ":" + hex + "\n";
        std::fwrite(record.data(), 1, record.size(), stdout);
        std::fflush(stdout);
    }
}
'''.replace('SPAN_INCLUDE', '' if track == 'stl' else '#include <span>').replace('VALUE', value).replace('KIND', kind)


C_DRIVER = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "candidate.c"
int main(void) {
    char line[16384]; uint8_t bytes[8192]; size_t sequence = 0;
    while (fgets(line, sizeof(line), stdin)) {
        size_t size = strcspn(line, "\r\n"), count = 0;
        if (line[0] != '-') for (size_t i = 0; i + 1 < size; i += 2) {
            char pair[3] = {line[i], line[i+1], 0};
            bytes[count++] = (uint8_t)strtoul(pair, NULL, 16);
        }
        char value[32];
        int length = snprintf(value, sizeof(value), "%lld", (long long)reconstructed(bytes, count));
        printf("__REA_RETURN_V2__:%zu:ok:int:%d:", sequence++, length);
        for (int i = 0; i < length; ++i) printf("%02x", (unsigned char)value[i]);
        putchar('\n'); fflush(stdout);
    }
    return 0;
}
'''


def rust_driver(kind):
    return r'''
use std::io::{self, BufRead, Write};
extern crate recovered;
fn main() {
    for (sequence, line) in io::stdin().lock().lines().enumerate() {
        let line = line.unwrap();
        let bytes: Vec<u8> = if line == "-" { Vec::new() } else {
            (0..line.len()).step_by(2)
                .map(|i| u8::from_str_radix(&line[i..i+2], 16).unwrap()).collect()
        };
        let value = VALUE;
        let hex: String = value.as_bytes().iter().map(|v| format!("{:02x}", v)).collect();
        let record = format!("__REA_RETURN_V2__:{}:ok:KIND:{}:{}\n", sequence, value.len(), hex);
        io::stdout().write_all(record.as_bytes()).unwrap();
        io::stdout().flush().unwrap();
    }
}
'''.replace('VALUE', 'recovered::reconstructed(&bytes).to_string()' if kind == 'int' else 'recovered::reconstructed(&bytes)').replace('KIND', kind)


def go_driver(kind):
    return r'''
package main
import ("bufio"; "encoding/hex"; "fmt"; "os")
func main() {
    scanner := bufio.NewScanner(os.Stdin)
    scanner.Buffer(make([]byte, 4096), 4*1024*1024)
    sequence := 0
    for scanner.Scan() {
        line := scanner.Text()
        var data []byte
        if line != "-" { var err error; data, err = hex.DecodeString(line); if err != nil { panic(err) } }
        value := VALUE
        record := fmt.Sprintf("__REA_RETURN_V2__:%d:ok:KIND:%d:%s\n", sequence, len(value), hex.EncodeToString([]byte(value)))
        if _, err := os.Stdout.Write([]byte(record)); err != nil { panic(err) }
        sequence++
    }
    if err := scanner.Err(); err != nil { panic(err) }
}
'''.replace('VALUE', 'fmt.Sprintf("%d", reconstructed(data))' if kind == 'int' else 'reconstructed(data)').replace('KIND', kind)


def compiler_identity(language):
    if os.environ.get('REA_GRADING_WORKER') == '1':
        record = json.loads(Path('/environment/manifest.json').read_bytes())
        return {**record['manifest']['compiler'], 'image_sha256': record['id'],
                'python_version': record['manifest']['python_version'], 'scope': 'closed_toolchain_python_and_executor_v1',
                **host_identity()}
    from .runtime import identity
    return identity(language)


def host_identity():
    # Frequency, counters, processor numbering and wall time are not action
    # inputs. CPU capabilities and kernel can change generated/native behavior.
    fields = {'vendor_id', 'cpu family', 'model', 'model name', 'stepping', 'flags', 'Features', 'CPU architecture'}
    records = set()
    for block in Path('/proc/cpuinfo').read_text().split('\n\n'):
        values = {}
        for line in block.splitlines():
            if ':' in line:
                key, value = (part.strip() for part in line.split(':', 1))
                if key in fields:
                    values[key] = value
        if values:
            records.add(json.dumps(values, sort_keys=True))
    return {'platform': os.uname().machine, 'kernel': os.uname().release,
            'cpu_capabilities_sha256': hashlib.sha256(json.dumps(sorted(records)).encode()).hexdigest()}


def historical_compiler_identity(language):
    """Legacy executable-only identity; never certify an old grade with it."""
    native = isolation()
    name = {'c': 'gcc', 'cpp': 'g++', 'rust': 'rustc', 'go': 'go'}[language]
    path = native.toolchain(language) / 'bin' / name if language in ('go', 'rust') else Path(shutil.which(name))
    path = path.resolve()
    version = subprocess.check_output([str(path), 'version' if language == 'go' else '--version'], timeout=10).decode(errors='replace')
    return {'executable_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'version': version,
            'platform': os.uname().machine, 'scope': 'compiler_executable_and_version'}


def go_policy(code):
    if os.environ.get('REA_GRADING_WORKER') != '1':
        from .runtime import invoke
        return invoke('go_policy', 'go', {'code': code})
    native = isolation()
    source = Path(__file__).with_name('go_imports.go')
    identity = json.dumps({'source': hashlib.sha256(source.read_bytes()).hexdigest(),
                           'compiler': compiler_identity('go')}, sort_keys=True).encode()
    cache = BENCH.parent / '.cache/benchmark-runtime/go-imports' / hashlib.sha256(identity).hexdigest()
    cache.parent.mkdir(parents=True, exist_ok=True)
    with _helper_lock, cache.with_suffix('.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt_path = cache.with_suffix('.json')
        if cache.exists():
            if not receipt_path.exists() or json.loads(receipt_path.read_bytes()).get('sha256') != hashlib.sha256(cache.read_bytes()).hexdigest():
                raise RuntimeError('Trusted Go import helper cache is corrupt')
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='go-policy-') as temporary:
                work = Path(temporary)
                shutil.copy2(source, work / 'imports.go')
                os.utime(work / 'imports.go', (0, 0))
                built = process.run(native.sandbox(work, ['/opt/go/bin/go', 'build', '-trimpath', '-o', 'imports', 'imports.go'], 'go'),
                                    env=native.clean_environment(), timeout=120, output_limit=262144,
                                    preexec_fn=native.resource_limits('go', True))
                if built.returncode:
                    raise RuntimeError('Go policy helper compilation failed: ' + built.stderr.decode(errors='replace')[:2000])
                # Cache only this trusted helper, never candidate executables.
                temporary_cache = cache.with_name(cache.name + '-' + str(os.getpid()))
                shutil.copy2(work / 'imports', temporary_cache)
                os.replace(temporary_cache, cache)
                receipt_path.write_text(json.dumps({'sha256': hashlib.sha256(cache.read_bytes()).hexdigest()}) + '\n')
    checked = process.run([str(cache)], input=code.encode(), timeout=10, output_limit=262144)
    if checked.returncode:
        raise RuntimeError('Go policy helper failed')
    return json.loads(checked.stdout)


def evaluate(code, language, cases, track, standard, abi, artifact):
    if os.environ.get('REA_GRADING_WORKER') != '1':
        from .runtime import invoke
        values = [{**case, 'expected': {'raw_bytes_hex': case['expected'].hex()} if isinstance(case['expected'], bytes) else case['expected']} for case in cases]
        return invoke('evaluate', language, {'code': code, 'cases': values, 'track': track, 'standard': standard, 'abi': abi}, artifact)
    artifact = Path(artifact)
    artifact.mkdir(parents=True, exist_ok=True)
    native = isolation()
    kinds = {returns.expected_bytes(case, standard)[0] for case in cases}
    if len(kinds) != 1:
        raise ValueError('A task must declare one return value kind')
    kind = kinds.pop()
    if language == 'c' and kind != 'int':
        raise ValueError('Existing C function contract returns an integer')
    result = {'compiled': False, 'passed': 0, 'return_value_matches': 0, 'total': len(cases),
              'failure': None, 'points': {}, 'categories': {}, 'candidate_modified': False,
              'output_language': language, 'grading_protocol': returns.PROTOCOL,
              'return_provenance': 'native_process_unauthenticated',
              'candidate_source_sha256': hashlib.sha256(code.encode()).hexdigest(),
              'compiler_identity': compiler_identity(language)}
    for case in cases:
        point = str(case.get('point', 'behavior'))
        result['points'].setdefault(point, {'passed': 0, 'total': 0})['total'] += 1
        result['categories'].setdefault(case['category'], {'passed': 0, 'total': 0})['total'] += 1
    if language == 'go':
        policy = go_policy(code)
        result['import_check'] = policy
        result['failure'] = {'parse_error': 'go_parse_error', 'forbidden_import': 'forbidden_unsafe_or_ffi',
                             'source_limit': 'source_limit'}.get(policy['status'])
    if not result['failure']:
        extension = {'c': 'c', 'cpp': 'cpp', 'rust': 'rs', 'go': 'go'}[language]
        driver = {'c': lambda: C_DRIVER, 'cpp': lambda: cpp_driver(track, kind),
                  'rust': lambda: rust_driver(kind), 'go': lambda: go_driver(kind)}[language]()
        with tempfile.TemporaryDirectory(prefix='rea-exact-grade-') as temporary:
            work = Path(temporary)
            (work / f'candidate.{extension}').write_text(code)
            (work / f'driver.{extension}').write_text(driver)
            for path in work.iterdir():
                os.utime(path, (0, 0))
                shutil.copy2(path, artifact / path.name)
            if language in ('c', 'cpp'):
                command = ['gcc' if language == 'c' else 'g++', '-std=' + ('c11' if language == 'c' else standard), '-O2', '-Wall', '-Wextra']
                if language == 'cpp' and abi is not None:
                    command.append(f'-D_GLIBCXX_USE_CXX11_ABI={abi}')
                commands = [command + [f'driver.{extension}', '-o', 'candidate']]
            elif language == 'rust':
                options = ['/opt/rust/bin/rustc', '--sysroot', '/opt/rust', '--edition=2021', '-C', 'linker=/usr/bin/gcc', '-F', 'unsafe_code', '-C', 'opt-level=2']
                commands = [options + ['--crate-name', 'recovered', '--crate-type=rlib', 'candidate.rs', '-o', 'librecovered.rlib'],
                            options + ['driver.rs', '--extern', 'recovered=/work/librecovered.rlib', '-o', 'candidate']]
            else:
                commands = [['/opt/go/bin/go', 'build', '-trimpath', '-ldflags=-s -w', '-o', 'candidate', 'driver.go', 'candidate.go']]
            result['compile_commands'] = commands
            phase_times = {}
            def stamp(phase):
                phase_times[phase] = time.monotonic_ns()
                (artifact / 'phase-times.json').write_text(json.dumps(phase_times) + '\n')
            try:
                logs = bytearray()
                for command in commands:
                    if 'compile_start_ns' not in phase_times:
                        stamp('compile_start_ns')
                    completed = process.run(native.sandbox(work, command, language), env=native.clean_environment(), timeout=120,
                                            output_limit=262144, preexec_fn=native.resource_limits(language, True))
                    logs.extend(completed.stdout + completed.stderr)
                    if completed.returncode:
                        break
                stamp('compile_end_ns')
                (artifact / 'compile.log').write_bytes(logs)
                if completed.returncode:
                    result['failure'] = 'compile_error'
                else:
                    result['compiled'] = True
                    shutil.copy2(work / 'candidate', artifact / 'candidate')
                    shutil.rmtree(work / 'go-cache', ignore_errors=True)
                    stamp('execution_start_ns')
                    completed = process.run(native.sandbox(work, ['/work/candidate'], language), env=native.clean_environment(),
                                            input=''.join(case['hex'] + '\n' for case in cases).encode(), timeout=20,
                                            preexec_fn=native.resource_limits(language))
                    (artifact / 'stdout.bin').write_bytes(completed.stdout)
                    (artifact / 'stderr.bin').write_bytes(completed.stderr)
                    stamp('execution_end_ns')
                    compared = returns.compare(completed.stdout, cases, standard)
                    result.update(candidate_exit_code=completed.returncode, protocol_errors=compared['errors'],
                                  returned_frames=compared['returned_frames'])
                    result['failure'] = f'candidate_exit_{completed.returncode}' if completed.returncode else next(iter(compared['errors']), None)
                    result['first_mismatches'] = [row for row in compared['records'] if not row['passed']][:24]
                    (artifact / 'returns.json').write_text(json.dumps(compared, indent=2) + '\n')
                    for case, row in zip(cases, compared['records']):
                        result['passed'] += int(row['passed'])
                        result['return_value_matches'] += int(row['value_matches'])
                        result['points'][str(case.get('point', 'behavior'))]['passed'] += int(row['passed'])
                        result['categories'][case['category']]['passed'] += int(row['passed'])
            except subprocess.TimeoutExpired:
                result['failure'] = 'candidate_timeout' if result['compiled'] else 'compile_timeout'
            except process.OutputLimitExceeded:
                result['failure'] = 'candidate_output_limit' if result['compiled'] else 'compile_output_limit'
    excluded = {'dispatch'} | ({'21'} if track == 'stl' and standard == 'c++11' else set())
    result['active_point_count'] = sum(key not in excluded for key in result['points'])
    result['active_points_passed'] = sum(row['passed'] == row['total'] for key, row in result['points'].items() if key not in excluded)
    result['fully_correct_points'] = result['active_points_passed']
    result['total_function_points'] = result['active_point_count']
    result['pass_rate'] = result['passed'] / len(cases) if cases else 0.0
    result['program_success'] = result['compiled'] and result['passed'] == len(cases) and not result['failure']
    return result
