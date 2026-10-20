"""Offline grading amendment: numbered hex frames preserve exact return bytes.

No model calls or candidate edits. Original batch-line grades remain untouched.
"""
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]
COHORT = BENCH / 'runs/stl-rust-data-v2-20261019'
spec = importlib.util.spec_from_file_location('sandbox_native', BENCH / 'scripts/grade.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
PREFIX = '__REA_RETURN_FRAME_V1__:'
CPP_DRIVER = r'''
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
std::string reconstructed(const std::vector<std::uint8_t>&);
int main() {
  std::string line;
  std::size_t sequence = 0;
  const char digits[] = "0123456789abcdef";
  while (std::getline(std::cin, line)) {
    std::vector<std::uint8_t> bytes;
    for (std::size_t i = 0; i + 1 < line.size(); i += 2)
      bytes.push_back(static_cast<std::uint8_t>(std::stoul(line.substr(i, 2), nullptr, 16)));
    const std::string value = reconstructed(bytes);
    std::string hex;
    for (unsigned char byte : value) { hex += digits[byte >> 4]; hex += digits[byte & 15]; }
    std::cout << "__REA_RETURN_FRAME_V1__:" << std::to_string(sequence++) << ':' << hex << '\n';
    std::cout.flush();
  }
}
'''
RUST_DRIVER = r'''
use std::io::{self, BufRead, Write};
extern crate recovered;
fn main() {
  for (sequence, line) in io::stdin().lock().lines().enumerate() {
    let line = line.unwrap();
    let bytes: Vec<u8> = if line == "-" { Vec::new() } else {
      (0..line.len()).step_by(2)
        .map(|i| u8::from_str_radix(&line[i..i+2], 16).unwrap()).collect()
    };
    let value = recovered::reconstructed(&bytes);
    let hex: String = value.as_bytes().iter().map(|v| format!("{:02x}", v)).collect();
    println!("__REA_RETURN_FRAME_V1__:{}:{}", sequence, hex);
    io::stdout().flush().unwrap();
  }
}
'''


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    return sha_bytes(path.read_bytes())


def parse_frames(output):
    pattern = re.compile(rb'__REA_RETURN_FRAME_V1__:(\d+):([0-9a-f]*)\n')
    frames, noise, previous = {}, {}, 0
    duplicate = False
    for match in pattern.finditer(output):
        index = int(match[1])
        if index in frames: duplicate = True
        try: value = bytes.fromhex(match[2].decode('ascii'))
        except ValueError: value = None
        frames[index] = value
        noise[index] = output[previous:match.start()]
        previous = match.end()
    return frames, noise, output[previous:], duplicate


def evaluate(code, language, cases, standard, abi, artifact):
    artifact.mkdir(parents=True, exist_ok=True)
    extension = 'cpp' if language == 'cpp' else 'rs'
    commands = ([['g++', f'-std={standard}', '-pedantic-errors', '-O2', '-Wall', '-Wextra',
        f'-D_GLIBCXX_USE_CXX11_ABI={abi}', 'candidate.cpp', 'driver.cpp', '-o', 'candidate']] if language == 'cpp' else [
        ['/opt/rust/bin/rustc', '--sysroot', '/opt/rust', '--edition=2021', '-C', 'linker=/usr/bin/gcc', '-F', 'unsafe_code',
         '-C', 'opt-level=2', '--crate-name', 'recovered', '--crate-type=rlib', 'candidate.rs', '-o', 'librecovered.rlib'],
        ['/opt/rust/bin/rustc', '--sysroot', '/opt/rust', '--edition=2021', '-C', 'linker=/usr/bin/gcc', '-F', 'unsafe_code',
         '-C', 'opt-level=2', 'driver.rs', '--extern', 'recovered=/work/librecovered.rlib', '-o', 'candidate']])
    result = {'compiled': False, 'passed': 0, 'return_value_matches': 0, 'total': len(cases), 'failure': None,
        'points': {}, 'categories': {}, 'candidate_modified': False, 'grading_protocol': 'numbered-hex-return-frames-v1',
        'candidate_source_sha256': sha_bytes(code.encode()), 'compile_commands': commands}
    for case in cases:
        result['points'].setdefault(str(case['point']), {'passed': 0, 'total': 0})['total'] += 1
        result['categories'].setdefault(case['category'], {'passed': 0, 'total': 0})['total'] += 1
    with tempfile.TemporaryDirectory(prefix='rea-frame-grade-') as temporary:
        work = Path(temporary)
        (work / f'candidate.{extension}').write_text(code)
        driver = CPP_DRIVER if language == 'cpp' else RUST_DRIVER
        (work / f'driver.{extension}').write_text(driver)
        (artifact / f'driver.{extension}').write_text(driver)
        try:
            logs = []
            for command in commands:
                compiled = subprocess.run(native.sandbox(work, command, language), env=native.clean_environment(),
                    text=True, capture_output=True, timeout=120, preexec_fn=native.resource_limits(language, True))
                logs.append(compiled.stdout + compiled.stderr)
                if compiled.returncode: break
            (artifact / 'compile.log').write_text('\n'.join(logs)[:262144])
            if compiled.returncode: result['failure'] = 'compile_error'
            else:
                result['compiled'] = True
                completed = subprocess.run(native.sandbox(work, ['/work/candidate'], language), env=native.clean_environment(),
                    input=''.join(case['hex'] + '\n' for case in cases).encode(), capture_output=True, timeout=20,
                    preexec_fn=native.resource_limits(language))
                (artifact / 'stdout.bin').write_bytes(completed.stdout)
                (artifact / 'stderr.log').write_text(completed.stderr.decode(errors='replace')[:65536])
                frames, noise, tail, duplicate = parse_frames(completed.stdout)
                result.update(candidate_exit_code=completed.returncode, returned_frames=len(frames),
                    requests_with_unexpected_stdout=sum(bool(value) for value in noise.values()),
                    unexpected_stdout_tail_bytes=len(tail), duplicate_frame_ids=duplicate)
                if completed.returncode: result['failure'] = f'candidate_exit_{completed.returncode}'
                elif len(frames) != len(cases) or set(frames) != set(range(len(cases))): result['failure'] = 'missing_or_extra_return_frames'
                elif any(noise.values()) or tail: result['failure'] = 'unexpected_stdout'
                elif duplicate: result['failure'] = 'duplicate_return_frames'
                mismatches, actual = [], []
                for index, case in enumerate(cases):
                    expected = case['expected'][standard] if isinstance(case['expected'], dict) else case['expected']
                    value = frames.get(index)
                    value_matches = value == expected.encode()
                    passed = value_matches and not noise.get(index) and not duplicate
                    result['passed'] += int(passed)
                    result['return_value_matches'] += int(value_matches)
                    result['points'][str(case['point'])]['passed'] += int(passed)
                    result['categories'][case['category']]['passed'] += int(passed)
                    actual.append({'sequence': index, 'returned_hex': value.hex() if value is not None else None,
                        'returned_text': value.decode('utf8', errors='replace') if value is not None else None,
                        'unexpected_stdout_hex': noise.get(index, b'').hex(), 'passed': bool(passed)})
                    if not passed and len(mismatches) < 24:
                        mismatches.append({'index': index, 'point': case['point'], 'category': case['category'],
                            'expected': expected, 'actual': actual[-1]['returned_text'], 'unexpected_stdout_bytes': len(noise.get(index, b''))})
                result['first_mismatches'] = mismatches
                (artifact / 'actual-framed.json').write_text(json.dumps(actual, indent=2) + '\n')
        except subprocess.TimeoutExpired:
            result['failure'] = 'candidate_timeout' if result['compiled'] else 'compile_timeout'
    excluded = {'dispatch'} | ({'21'} if language == 'cpp' and standard == 'c++11' else set())
    result['active_point_count'] = sum(key not in excluded for key in result['points'])
    result['active_points_passed'] = sum(group['passed'] == group['total'] for key, group in result['points'].items() if key not in excluded)
    result['fully_correct_points'] = result['active_points_passed']
    result['total_function_points'] = result['active_point_count']
    result['program_success'] = result['compiled'] and result['passed'] == result['total'] and result['failure'] is None
    return result


def frozen_cases(track):
    provenance = json.loads((COHORT / 'input-provenance.json').read_text())
    marker = '/stl-native-v2/private/build-cache/' if track == 'stl' else '/complex-native-v1/private/'
    for index, (path, digest) in enumerate(provenance['protocol_files_sha256'].items()):
        if marker in path and path.endswith('/vectors.json'):
            archive = COHORT / 'pipeline' / f'{index:02d}-vectors.json'
            assert sha(archive) == digest
            return json.loads(archive.read_text()), digest
    raise RuntimeError('Missing frozen vectors')


def selftest():
    cases = [{'hex': payload, 'point': 'control', 'category': 'directed', 'expected': answer}
             for payload, answer in [('', '-'), ('-', '-'), ('00', 'A'), ('01', 'B'), ('02', 'C'), ('ff', 'ERR')]]
    cpp = '#include <string>\n#include <vector>\n#include <cstdint>\n#include <iostream>\n#include <cstdlib>\nstd::string reconstructed(const std::vector<std::uint8_t>& d){ if(d.empty())return "-"; if(d[0]==0)return "A"; if(d[0]==1)return VALUE; if(d[0]==2){ ACTION return "C";} return "ERR";}\n'
    rust = 'pub fn reconstructed(d:&[u8])->String { if d.is_empty(){return "-".into();} match d[0] {0=>"A".into(),1=>VALUE,2=>{ACTION "C".into()},_=>"ERR".into()} }\n'
    results = []
    key = sha(Path(__file__))
    for language, standard, abi in [('cpp', 'c++11', 0), ('cpp', 'c++14', 1), ('rust', '2021', 0)]:
        for kind in ['correct', 'newline-return', 'stdout-noise', 'crash']:
            value = '"B"' if language == 'cpp' else '"B".into()'
            action = ''
            if kind == 'newline-return': value = '"B\\n"' if language == 'cpp' else '"B\\n".into()'
            if kind == 'stdout-noise': action = 'std::cout << "noise\\n";' if language == 'cpp' else 'println!("noise");'
            if kind == 'crash': action = 'std::abort();' if language == 'cpp' else 'panic!("controlled crash");'
            code = (cpp if language == 'cpp' else rust).replace('VALUE', value).replace('ACTION', action)
            directory = SUITE / 'private/framed-controls' / key / f'{language}-{standard}-{abi}-{kind}'
            receipt = directory / 'grade.json'
            if receipt.exists(): result = json.loads(receipt.read_text())
            else:
                result = evaluate(code, language, cases, standard, abi, directory)
                receipt.write_text(json.dumps(result, indent=2) + '\n')
            expected = len(cases) if kind == 'correct' else len(cases) - 2 if kind == 'crash' else len(cases) - 1
            assert result['compiled'] and result['passed'] == expected, (language, standard, kind, result)
            assert result['program_success'] == (kind == 'correct')
            results.append({'language': language, 'standard': standard, 'kind': kind, 'passed': True, 'receipt_sha256': sha(receipt)})
            print(f'FRAME CONTROL {language}/{standard}/{kind}: PASS', flush=True)
    validation = {'passed': True, 'grader_sha256': key, 'controls': results, 'model_calls': 0,
        'purpose': 'Newlines in returned strings and stray stdout cannot shift later request scores; crashes retain earlier flushed frames.'}
    destination = SUITE / 'private/framed-validation.json'
    destination.write_text(json.dumps(validation, indent=2) + '\n')
    return validation


def main():
    validation = selftest()
    manifest = json.loads((COHORT / 'run-manifest.json').read_text())
    amended = []
    for case, model in manifest['configuration']['run_order']:
        directory = COHORT / model / case
        if not (directory / 'completed.json').exists(): continue
        receipt = json.loads((directory / 'result.json').read_text())
        old = json.loads((directory / 'grade.json').read_text())
        extension = 'cpp' if receipt['language'] == 'cpp' else 'rs'
        source = directory / f'reconstructed.{extension}'
        cases, vector_digest = frozen_cases(receipt['track'])
        destination = directory / 'grade-framed.json'
        artifact = directory / 'grade-framed'
        source_digest = sha(source) if source.exists() else None
        if source_digest: assert source_digest == old['candidate_source_sha256']
        fingerprint = sha_bytes(json.dumps({'source': source_digest, 'vectors': vector_digest, 'case': case,
            'grader': sha(Path(__file__)), 'sandbox': sha(BENCH / 'scripts/grade.py')}, sort_keys=True).encode())
        if destination.exists():
            result = json.loads(destination.read_text())
            assert result['fingerprint'] == fingerprint
            for name, digest in result['artifacts_sha256'].items(): assert sha(artifact / name) == digest
            print(f'FRAME CACHE {model}/{case}', flush=True)
        else:
            if source.exists(): result = evaluate(source.read_text(), receipt['language'], cases, receipt['standard'], receipt['string_abi'], artifact)
            else: result = {**old, 'grading_protocol': 'numbered-hex-return-frames-v1', 'candidate_modified': False}
            assert not source.exists() or sha(source) == source_digest
            result.update(fingerprint=fingerprint, original_batch_grade_sha256=sha(directory / 'grade.json'),
                original_source_sha256=source_digest, vectors_sha256=vector_digest,
                artifacts_sha256={str(p.relative_to(artifact)): sha(p) for p in artifact.rglob('*') if p.is_file()} if artifact.exists() else {})
            destination.write_text(json.dumps(result, indent=2) + '\n')
            print(f'FRAME GRADE {model}/{case}: {result["passed"]}/{result["total"]}; points {result.get("active_points_passed", result.get("fully_correct_points"))}; failure {result["failure"]}', flush=True)
        amended.append({'model': model, 'case': case, 'batch_passed': old['passed'], 'framed_passed': result['passed'],
            'source_sha256': source_digest, 'old_grade_sha256': sha(directory / 'grade.json'), 'new_grade_sha256': sha(destination)})
    amendment = {'status': 'complete' if len(amended) == 10 else 'in_progress', 'grading_amendment': 'numbered-hex-return-frames-v1',
        'reason': 'Bare line output cannot faithfully score an unchanged candidate returning newlines; return values now use numbered byte frames and stray stdout is scored separately.',
        'candidate_edits': 0, 'new_model_calls': 0, 'grader_sha256': sha(Path(__file__)), 'validation': validation,
        'grades': amended, 'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'original_batch_grades_preserved': True, 'original_inference_protocol_preserved': True,
        'resources': 'Same compiler settings, sandbox and CPU/memory/wall limits; only fixed output framing/flush differs.'}
    (COHORT / 'grading-amendment-framed.json').write_text(json.dumps(amendment, indent=2) + '\n')


if __name__ == '__main__': main()
