#!/usr/bin/python3
"""Cache frontend feature probes separately from paid recovery benchmarks."""
import hashlib
import json
from pathlib import Path
import subprocess

import build_cpp_points as builds

ROOT, BENCH = builds.ROOT, builds.BENCH
SOURCE = BENCH / 'suites/stl-native-v2/authoring/features'
OUTPUT = BENCH / 'config/cpp-feature-validation.json'


def main():
    config = json.loads(builds.CONFIG.read_text())
    probes = []
    compilers = [builds.toolchain(spec) for spec in config['compilers']]
    for compiler in compilers:
        for source in sorted(SOURCE.glob('*.cpp')):
            standard = 'c++20' if source.name.startswith('cpp20') else 'c++23' if source.name.startswith('cpp23') else 'c++2c'
            identity = {'source_sha256': builds.sha(source), 'compiler_fingerprint': compiler['dependency_fingerprint'],
                        'standard': standard, 'flags': ['-O2', '-g0', '-pedantic-errors', '-Wall', '-Wextra', '-Werror'],
                        'validator_sha256': builds.sha(Path(__file__))}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            directory = SOURCE.parents[1] / 'private/feature-probes' / key
            receipt, target = directory / 'validation.json', directory / 'target'
            if receipt.exists():
                saved = json.loads(receipt.read_text())
                if saved['status'] == 'unsupported_by_selected_toolchain' or (target.exists() and builds.sha(target) == saved.get('binary_sha256')):
                    probes.append(saved)
                    print(compiler['id'], source.stem, saved['status'], '(cached)', flush=True)
                    continue
            directory.mkdir(parents=True, exist_ok=True)
            command = [compiler['executable'], '-std=' + standard, *identity['flags']]
            if compiler['family'] == 'Clang':
                command += ['-stdlib=libstdc++', '--gcc-install-dir=/usr/lib/gcc/x86_64-linux-gnu/13']
            command += [str(source), '-o', str(target)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            (directory / 'compile.log').write_text(result.stdout + result.stderr)
            saved = {**identity, 'compiler_id': compiler['id'], 'compiler_version': compiler['version'],
                     'feature': source.stem, 'source': str(source.relative_to(ROOT)), 'command': command,
                     'compile_exit': result.returncode, 'diagnostic': result.stderr[-2500:],
                     'model_attempts': 0, 'status': 'unsupported_by_selected_toolchain'}
            if not result.returncode:
                control = subprocess.run([str(target)], capture_output=True, timeout=10)
                assert control.returncode == 0, source.stem + ' failed the explicit local control'
                saved.update(status='compiled_and_local_control_passed', binary_sha256=builds.sha(target), local_control_exit=0)
            builds.save(receipt, saved)
            probes.append(saved)
            print(compiler['id'], source.stem, saved['status'], flush=True)
    builds.save(OUTPUT, {'schema_version': 1, 'scope': 'Language-feature availability only; no model recovery scores',
                        'model_api_calls': 0, 'compilers': compilers, 'probes': probes,
                        'validator_sha256': builds.sha(Path(__file__))})


if __name__ == '__main__':
    main()
