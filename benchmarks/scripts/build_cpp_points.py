#!/usr/bin/python3
"""Build compiler/optimization points on existing C++ fixtures; never run models."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import records

ROOT, BENCH = records.ROOT, records.BENCH
CONFIG = BENCH / 'config/cpp-build-points.json'
OUTPUT = BENCH / 'config/cpp-build-validation.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, **kwargs):
    return subprocess.run([str(x) for x in command], capture_output=True, text=True, check=True, timeout=180, **kwargs)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    temp.replace(path)


def toolchain(spec):
    executable = Path(spec['executable'])
    version = run([executable, '--version']).stdout.splitlines()[0]
    assert str(spec['expected_major']) in version, 'Unexpected compiler major: ' + version
    dependencies = [executable.resolve(), Path('/usr/bin/ld').resolve(), Path('/usr/bin/as').resolve()]
    for line in run(['ldd', executable.resolve()]).stdout.splitlines():
        match = re.search(r'=> (/\S+)', line)
        if match:
            dependencies.append(Path(match[1]).resolve())
    gcc = '/usr/bin/g++'
    for option in ['-print-prog-name=cc1plus', '-print-file-name=libstdc++.so', '-print-file-name=libgcc_s.so']:
        path = Path(run([gcc, option]).stdout.strip()).resolve()
        if path.is_file():
            dependencies.append(path)
    for directory in ['/usr/include/c++/13', '/usr/include/x86_64-linux-gnu/c++/13']:
        dependencies.extend(p for p in Path(directory).rglob('*') if p.is_file())
    if spec['family'] == 'Clang':
        resource = Path(run([executable, '-print-resource-dir']).stdout.strip())
        dependencies.extend(p for p in resource.rglob('*') if p.is_file())
    hashes = {str(p): sha(p) for p in sorted(set(dependencies))}
    return {**spec, 'version': version, 'dependencies_sha256': hashes,
            'dependency_fingerprint': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
            'standard_library': 'libstdc++ 13'}


def original(family):
    experiment = next(e for e in records.registry()['experiments'] if e['id'] == family['experiment'])
    case = next(c for c in records.frozen_cases(experiment) if c['id'] == family['base_case'])
    public = records.public_directory(experiment, case)
    # Native basic prompts were frozen next to the original model submissions.
    prompt = BENCH / 'runs' / experiment['id'] / 'gpt-6.1-sol' / case['id'] / 'prompt.txt'
    if not prompt.exists():
        prompt = public / 'prompt.txt'
    return public, prompt


def inputs(family):
    suite = BENCH / 'suites' / family['suite']
    if family['suite'] == 'record-parser':
        sources = [suite / 'authoring/cpp/case.cpp']
        vectors = suite / 'grading/vectors.json'
    elif family['suite'] == 'complex-native-v1':
        sources = [suite / ('authoring/cpp/' + name) for name in ['case.cpp', 'abi.hpp', 'abi.cpp']]
        sources += [suite / 'private/build/cpp/driver.cpp']
        vectors = suite / 'private/vectors.json'
    else:
        sources = [suite / ('authoring/' + name) for name in ['containers.cpp', 'driver.cpp', 'grammar14.inc']]
        old = json.loads((suite / 'private/validation.json').read_text())
        vectors = Path(old['cache_directory']) / 'vectors.json'
    return suite, sources, vectors


def entries(binary, suite):
    symbols = run(['nm', '-n', '-C', binary]).stdout
    if suite == 'record-parser':
        return None
    if suite == 'complex-native-v1':
        patterns = ['word_counts(', 'ledger(', 'rank_records(', 'schedule(', 'abi_record(', 'apply_policy(', 'reconstructed(']
        output = {}
        for index, pattern in enumerate(patterns):
            matches = [line for line in symbols.splitlines() if re.match(r'^[0-9a-f]+ T ', line)
                       and line.split(' T ', 1)[1].replace('[abi:cxx11]', '').startswith(pattern)
                       and '[clone ' not in line and '::{lambda' not in line]
            assert len(matches) == 1, f'Entry hint unavailable: {pattern}: {matches}'
            output['dispatch' if index == 6 else f'P{index}'] = hex(int(matches[0].split()[0], 16))
        return output
    names = ['vector', 'array', 'deque', 'list', 'forward_list', 'set', 'multiset', 'map', 'multimap',
             'unordered_set', 'unordered_multiset', 'unordered_map', 'unordered_multimap', 'stack', 'queue',
             'priority_queue', 'vector_bool', 'string', 'bitset', 'valarray', 'grammar11', 'grammar14']
    found = {}
    for line in symbols.splitlines():
        if ' T stl_benchmark::' in line and '_workload' in line:
            name = line.split('stl_benchmark::', 1)[1].split('(')[0].split('[')[0]
            found[name] = hex(int(line.split()[0], 16))
    assert set(found) == {name + '_workload' for name in names}
    return {str(i): found[name + '_workload'] for i, name in enumerate(names)}


def build(family, compiler, optimization):
    suite, sources, vector_path = inputs(family)
    public, prompt_path = original(family)
    original_manifest = json.loads((public / 'manifest.json').read_text())
    source_hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    point_id = family['base_case'] + '--' + compiler['id'] + '--' + optimization
    identity = {**family, 'compiler_family': compiler['family'], 'compiler_version': compiler['version'],
                'compiler_id': compiler['id'], 'optimization': optimization, 'source_sha256': source_hashes,
                'toolchain_sha256': compiler['dependency_fingerprint'], 'vectors_sha256': sha(vector_path),
                'builder_sha256': sha(Path(__file__)), 'config_sha256': sha(CONFIG),
                'prompt_template_sha256': sha(prompt_path), 'standard_library': compiler['standard_library'],
                'compiler_diagnostic_flags': compiler.get('container_diagnostic_flags', []) if family['suite'] == 'stl-native-v2' else []}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    base = {'point_id': point_id, 'fingerprint': fingerprint, **identity, 'model_runs': 0}
    # Reuse the exact original GCC -O2 bytes rather than rebuild a nominal equivalent.
    if compiler['id'] == 'gcc13' and optimization == family['recorded_optimization']:
        target = public / 'target'
        assert sha(target) == original_manifest['binary_sha256']
        if family['suite'] == 'complex-native-v1':
            frozen = json.loads((suite / 'private/build/cpp/build-manifest.json').read_text())
            assert frozen['binary_sha256'] == sha(target)
            assert all(sha(suite / path) == h for path, h in frozen['source_hashes'].items())
            frozen_compiler = frozen['compiler']
        elif family['suite'] == 'stl-native-v2':
            frozen = json.loads((suite / 'private/validation.json').read_text())
            assert any(b['id'] == family['base_case'] and b['binary_sha256'] == sha(target) for b in frozen['builds'])
            assert all(sha(suite / path) == h for path, h in frozen['source_hashes'].items() if path.startswith('authoring/'))
            frozen_compiler = frozen['compiler']
        else:
            assert sha(sources[0]) == original_manifest['source_sha256']
            frozen_compiler = original_manifest['compiler']
        assert frozen_compiler == compiler['version'], 'Compiler changed; the old build cannot be relabeled'
        return {**base, 'status': 'reused_frozen_build', 'public_directory': str(public.relative_to(ROOT)),
                'binary_sha256': sha(target), 'original_case_id': family['base_case'],
                'note': 'Original frozen target retained, including its original extra flags and addresses.'}
    directory = suite / 'private/build-points' / fingerprint
    receipt = directory / 'validation.json'
    if receipt.exists():
        prior = json.loads(receipt.read_text())
        if prior.get('status') == 'locally_verified' and all(sha(ROOT / p) == h for p, h in prior['artifacts_sha256'].items()):
            return prior
    directory.mkdir(parents=True, exist_ok=True)
    symbols, target = directory / 'target-with-symbols', directory / 'target'
    command = [compiler['executable'], '-std=' + family['standard'], '-' + optimization, '-g0', *family['extra_flags']]
    if compiler['family'] == 'Clang':
        command += ['-stdlib=libstdc++', '--gcc-install-dir=/usr/lib/gcc/x86_64-linux-gnu/13']
    command += identity['compiler_diagnostic_flags']
    if family['suite'] == 'complex-native-v1':
        for name, source in [('candidate.cpp', sources[0]), ('abi.hpp', sources[1]), ('driver.cpp', sources[3])]:
            shutil.copy2(source, directory / name)
        command += [directory / 'driver.cpp', sources[2]]
    elif family['suite'] == 'stl-native-v2':
        command += [f"-D_GLIBCXX_USE_CXX11_ABI={family['string_abi']}", *sources[:2]]
    else:
        command += sources
    command += ['-o', symbols]
    # A reporting/validator revision must not trigger identical recompilation.
    # Reuse only a symbolized artifact with matching source, toolchain and all
    # code-generation/diagnostic settings; rerun the current independent checks.
    match_fields = ['suite', 'base_case', 'compiler_id', 'compiler_version', 'optimization', 'standard',
                    'string_abi', 'strip_mode', 'extra_flags', 'source_sha256', 'toolchain_sha256',
                    'compiler_diagnostic_flags']
    compiled_cached, compilation_origin = False, None
    for prior_path in sorted((suite / 'private/build-points').glob('*/validation.json')):
        prior = json.loads(prior_path.read_text())
        prior_binary = prior_path.parent / 'target-with-symbols'
        if prior.get('status') != 'locally_verified' or not prior_binary.exists():
            continue
        if not all(prior.get(k, [] if k == 'compiler_diagnostic_flags' else None) == base.get(k) for k in match_fields):
            continue
        expected = prior.get('artifacts_sha256', {}).get(str(prior_binary.relative_to(ROOT)))
        if expected and sha(prior_binary) == expected:
            shutil.copy2(prior_binary, symbols)
            shutil.copy2(prior_path.parent / 'compile.log', directory / 'compile.log')
            compilation_origin = {'validation_receipt': str(prior_path.relative_to(ROOT)),
                                  'symbolized_binary_sha256': expected}
            compiled_cached = True
            break
    if not compiled_cached:
        completed = subprocess.run([str(x) for x in command], capture_output=True, text=True, timeout=180)
        (directory / 'compile.log').write_text(completed.stdout + completed.stderr)
    if not compiled_cached and completed.returncode:
        result = {**base, 'status': 'build_rejected', 'command': [str(x) for x in command],
                  'diagnostic': completed.stderr[-4000:]}
        save(receipt, result)
        return result
    hints = entries(symbols, family['suite'])
    shutil.copy2(symbols, target)
    run(['strip', '--strip-' + family['strip_mode'], target])
    sections = run(['readelf', '-SW', target]).stdout
    assert '.debug_info' not in sections
    if family['strip_mode'] == 'all':
        assert '.symtab' not in sections
    tests = json.loads(vector_path.read_text())
    payload = ''.join(item['hex'] + '\n' for item in tests)
    expected = [str(item['expected'][family['standard']] if family['suite'] == 'stl-native-v2' else item['expected']) for item in tests]
    text_hashes = []
    for index, binary in enumerate([symbols, target]):
        observed = run([binary], input=payload).stdout.splitlines()
        assert observed == expected, point_id + ' differs from the existing independent oracle'
        section = directory / f'text-{index}.bin'
        run(['objcopy', '--dump-section', '.text=' + str(section), binary])
        text_hashes.append(sha(section))
    assert len(set(text_hashes)) == 1
    prompt = prompt_path.read_text()
    if optimization == 'O0':
        prompt = prompt.replace('optimized, stripped', 'unoptimized, stripped').replace('optimized stripped', 'unoptimized stripped')
    old_hints = original_manifest.get('entry_points', original_manifest.get('entries'))
    if hints:
        assert set(hints) == set(old_hints)
        replacement = {old_hints[k]: hints[k] for k in hints}
        prompt = re.sub(r'0x[0-9a-fA-F]+', lambda match: replacement.get(match[0], match[0]), prompt)
    destination = suite / 'public/build-points' / point_id
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, destination / 'target')
    (destination / 'prompt.txt').write_text(prompt)
    original_metadata = {k: v for k, v in original_manifest.items() if k not in ['command', 'commands', 'build_command', 'compiler']}
    manifest = {**original_metadata, **base, 'id': point_id, 'binary_sha256': sha(target), 'compiler': compiler['version'],
                'profile': 'debug-stripped' if family['strip_mode'] == 'debug' else 'unoptimized-stripped' if optimization == 'O0' else 'optimized-stripped',
                'entries' if family['suite'] == 'stl-native-v2' else 'entry_points': hints,
                'reproduction_command': [str(x) for x in command], 'checks': len(tests), 'text_unchanged_after_strip': True,
                'compilation_cache_hit': compiled_cached, 'compilation_origin': compilation_origin}
    save(directory / 'manifest.json', manifest)
    save(destination / 'manifest.json', manifest)
    artifacts = {str(p.relative_to(ROOT)): sha(p) for p in [symbols, target, destination / 'target', destination / 'prompt.txt', destination / 'manifest.json']}
    result = {**base, 'status': 'locally_verified', 'public_directory': str(destination.relative_to(ROOT)),
              'binary_sha256': sha(target), 'checks': len(tests), 'symbolized_and_stripped_pass': True,
              'text_unchanged_after_strip': True, 'artifacts_sha256': artifacts, 'compilation_cache_hit': compiled_cached,
              'compilation_origin': compilation_origin, 'reproduction_command': [str(x) for x in command]}
    save(receipt, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot-only', action='store_true')
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    compilers = [toolchain(c) for c in config['compilers']]
    families = [f for f in config['families'] if not args.pilot_only or f['suite'] == 'complex-native-v1']
    output = {'schema_version': 1, 'scope': 'Existing C++ fixture compiler/optimization build points; no model inference',
              'compiler_toolchains': compilers, 'config_sha256': sha(CONFIG), 'builder_sha256': sha(Path(__file__)),
              'model_api_calls': 0, 'points': [], 'model_execution': config['model_execution']}
    for family in families:
        for compiler in compilers:
            for optimization in config['optimization_levels']:
                point = build(family, compiler, optimization)
                output['points'].append(point)
                save(OUTPUT, output)
                print(point['point_id'] + ': ' + point['status'], flush=True)
    print(f"Saved {len(output['points'])} distinct compiler/optimization points; zero model calls", flush=True)


if __name__ == '__main__':
    main()
