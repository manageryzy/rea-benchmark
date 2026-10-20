"""Native STL-track grading in the existing isolated, network-free sandbox."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]
spec = importlib.util.spec_from_file_location('native_grader', BENCH / 'scripts/grade.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def grade(directory, case_id):
    directory = Path(directory).resolve()
    validation = json.loads((SUITE / 'private/validation.json').read_text())
    build = next(row for row in validation['builds'] if row['id'] == case_id)
    vector_path = Path(validation['cache_directory']) / 'vectors.json'
    assert sha(vector_path) == validation['artifacts_sha256']['vectors.json']
    cases = json.loads(vector_path.read_text())
    driver = SUITE / 'authoring/driver.cpp'
    answer = (directory / 'answer.md').read_text()
    fingerprint = hashlib.sha256(json.dumps({'answer': answer, 'case': case_id, 'vectors': sha(vector_path),
        'grader': sha(Path(__file__)), 'sandbox': sha(BENCH / 'scripts/grade.py'), 'driver': sha(driver),
        'toolchain': validation['toolchain_dependency_digest']}, sort_keys=True).encode()).hexdigest()
    receipt = directory / 'grade.json'
    if receipt.exists():
        prior = json.loads(receipt.read_text())
        if prior.get('fingerprint') == fingerprint and all(sha(directory / name) == digest for name, digest in prior['artifacts_sha256'].items()):
            return {**prior, 'cache_hit': True}
        raise RuntimeError('Grade input changed: use a new grading directory; original candidate/receipt preserved')
    blocks = re.findall(r'^```([^\n]*)\n(.*?)^```\s*$', answer, re.M | re.S)
    result = {'fingerprint': fingerprint, 'case_id': case_id, 'compiled': False, 'passed': 0, 'total': len(cases),
        'failure': None, 'candidate_modified': False, 'points': {}, 'cache_hit': False, 'artifacts_sha256': {}}
    for case in cases:
        point = str(case['point'])
        result['points'].setdefault(point, {'passed': 0, 'total': 0})['total'] += 1
    if len(blocks) != 1 or not re.fullmatch(r'cpp|c\+\+', blocks[0][0].strip(), re.I):
        result['failure'] = 'expected_one_cpp_code_block'
    else:
        code = blocks[0][1]
        (directory / 'reconstructed.cpp').write_text(code)
        result['candidate_source_sha256'] = hashlib.sha256(code.encode()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='rea-stl-grade-') as temporary:
            work = Path(temporary)
            (work / 'candidate.cpp').write_text(code)
            (work / 'driver.cpp').write_text(driver.read_text())
            command = ['g++', f"-std={build['standard']}", '-pedantic-errors', '-O2', '-Wall', '-Wextra',
                f"-D_GLIBCXX_USE_CXX11_ABI={build['string_abi']}", 'candidate.cpp', 'driver.cpp', '-o', 'candidate']
            result['compile_command'] = command
            try:
                compiled = subprocess.run(native.sandbox(work, command, 'cpp'), env=native.clean_environment(),
                    text=True, capture_output=True, timeout=120, preexec_fn=native.resource_limits('cpp', True))
                (directory / 'compile.log').write_text((compiled.stdout + compiled.stderr)[:262144])
                if compiled.returncode: result['failure'] = 'compile_error'
                else:
                    result['compiled'] = True
                    completed = subprocess.run(native.sandbox(work, ['/work/candidate'], 'cpp'), env=native.clean_environment(),
                        text=True, capture_output=True, timeout=20, preexec_fn=native.resource_limits('cpp'),
                        input=''.join(item['hex'] + '\n' for item in cases))
                    actual = completed.stdout.splitlines()
                    (directory / 'actual.json').write_text(json.dumps(actual, indent=2) + '\n')
                    (directory / 'stderr.log').write_text(completed.stderr[:65536])
                    if completed.returncode: result['failure'] = f'candidate_exit_{completed.returncode}'
                    elif len(actual) != len(cases): result['failure'] = 'wrong_output_count'
                    for index, case in enumerate(cases):
                        passed = index < len(actual) and actual[index] == case['expected'][build['standard']]
                        result['passed'] += int(passed)
                        result['points'][str(case['point'])]['passed'] += int(passed)
            except subprocess.TimeoutExpired:
                result['failure'] = 'candidate_timeout' if result['compiled'] else 'compile_timeout'
    result['program_success'] = result['compiled'] and result['passed'] == result['total'] and result['failure'] is None
    result['active_point_count'] = build['active_points']
    result['active_points_passed'] = sum(group['passed'] == group['total'] for key, group in result['points'].items()
        if key != 'dispatch' and (build['standard'] != 'c++11' or key != '21'))
    for name in ('answer.md', 'reconstructed.cpp', 'compile.log', 'actual.json', 'stderr.log'):
        if (directory / name).is_file(): result['artifacts_sha256'][name] = sha(directory / name)
    receipt.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    print(json.dumps(grade(sys.argv[1], sys.argv[2]), indent=2))
