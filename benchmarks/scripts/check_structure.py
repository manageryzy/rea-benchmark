#!/usr/bin/python3
"""Verify the migration without running models, graders or compilers."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import records

ROOT, BENCH = records.ROOT, records.BENCH


def main():
    migration = records.read(BENCH / 'config/structure-migration.json')
    snapshot = records.read(BENCH / 'config/preserved-artifacts.json')
    assert records.read(BENCH / 'config/execution.json')['experiments_stopped'], 'Benchmark execution was unexpectedly enabled'
    preserved = 0
    for path, expected in snapshot['runs_sha256'].items():
        actual = ROOT / path
        assert actual.exists() and hashlib.sha256(actual.read_bytes()).hexdigest() == expected, 'Changed raw artifact: ' + path
        preserved += 1
    cleanup = records.read(BENCH / 'config/cleanup-preservation.json')
    for name, expected in cleanup['files_sha256'].items():
        assert records.sha(ROOT / name) == expected, 'Cleanup changed a saved run artifact: ' + name
    fixtures = 0
    for path, expected in snapshot['case_inputs_sha256'].items():
        if path in snapshot['fixture_control_code_updated']:
            continue
        target = path
        for source, destination in migration['directory_moves'].items():
            if path.startswith(source + '/'):
                target = destination + path[len(source):]
        actual = ROOT / target
        assert actual.exists() and hashlib.sha256(actual.read_bytes()).hexdigest() == expected, 'Changed fixture: ' + path
        fixtures += 1
    for name in ['authoring', 'grading', 'zcode']:
        assert not (BENCH / name).exists(), 'Old duplicate structure remains: ' + name
    # public/ now contains the allowlisted report snapshot, not old binary inputs.
    assert {str(p.relative_to(BENCH / 'public')) for p in (BENCH / 'public').rglob('*') if p.is_file()} == {'summary.json', 'README.md'}
    assert not (BENCH / 'scripts/runtime.mjs').exists()
    sources = (list((BENCH / 'scripts').glob('*.py'))
               + list((BENCH / 'scripts/grading').rglob('*.py'))
               + list((BENCH / 'scripts/capabilities').rglob('*.py'))
               + list((BENCH / 'harnesses').glob('*.py')) + list(BENCH.glob('suites/*/scripts/*.py')))
    for path in sources:
        ast.parse(path.read_text(), filename=str(path))
    node = ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'
    modules = list((BENCH / 'scripts').glob('*.mjs')) + list((BENCH / 'harnesses').glob('*.mjs')) + list(BENCH.glob('suites/*/scripts/*.mjs')) + list((BENCH / 'scripts').glob('*.cjs')) + list((BENCH / 'harnesses').glob('*.cjs'))
    for path in modules:
        subprocess.run([str(node), '--check', str(path)], check=True, capture_output=True, text=True)
        for target in re.findall(r'(?:from\s+|import\s*)[\'"](\.[^\'"]+)[\'"]', path.read_text()):
            assert (path.parent / target).resolve().exists(), 'Broken module import: ' + str(path) + ' ' + target
    # Importing a backend must not authenticate or start a model.
    subprocess.run([str(node), '--input-type=module', '-e',
        "await import('./benchmarks/harnesses/deepseek-harness.mjs');"], cwd=ROOT, check=True, capture_output=True, text=True)
    archived = records.read(BENCH / 'archive/entrypoint-cleanup/manifest.json')
    for old, item in archived['files'].items():
        assert not (ROOT / old).exists(), 'Archived entrypoint remains active: ' + old
        assert records.sha(ROOT / item['archive']) == item['sha256'], 'Archived historical source changed'
    result = subprocess.run([str(ROOT / 'bin/benchmark'), 'run', '--model', 'glm-5.3'],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0 and 'Experiments remain stopped' in result.stderr
    data = records.collect()
    assert not any('/reports/' in path for path in data['input_files_sha256']), 'Generated report used as input'
    primary = [r for r in data['records'] if r['role'] == 'primary']
    assert len([r for r in primary if r['harness'] != 'zcode' and r['grade']]) == 22
    assert len([r for r in primary if r['harness'] == 'zcode' and r['grade']]) >= 2
    assert not any(r['status'] == 'running' for r in data['records'])
    print(f'PASS: {preserved} original artifacts, {len(cleanup["files_sha256"])} cleanup-preserved artifacts and {fixtures} fixture inputs unchanged; '
          f'{len(sources)} Python / {len(modules)} Node sources; imports and stopped-state guards; no inference')


if __name__ == '__main__':
    main()
