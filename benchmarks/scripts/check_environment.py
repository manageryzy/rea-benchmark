#!/usr/bin/python3
"""Replay saved evidence offline, then record a reproducible validation receipt."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import records
import environment_profile

ROOT, BENCH = records.ROOT, records.BENCH
OUTPUT = BENCH / 'config/evidence-validation.json'


def main():
    assert records.read(BENCH / 'config/execution.json')['experiments_stopped']
    rows = [r for r in records.collect()['records'] if r['role'] == 'primary']
    paths = sorted({p for row in rows for p in (ROOT / row['directory']).glob('evidence-[0-9]*.json')})
    assert paths, 'No cached evidence available'
    sources = [Path(__file__), BENCH / 'scripts/replay-evidence.mjs', BENCH / 'scripts/evidence_validation.py',
               BENCH / 'scripts/smoke-evidence.mjs', BENCH / 'scripts/replay-mcp.mjs',
               BENCH / 'scripts/offline-network.cjs', BENCH / 'scripts/tool-catalog.mjs',
               BENCH / 'scripts/policy.mjs', BENCH / 'scripts/records.py',
               BENCH / 'suites/complex-native-v1/config/rea-tools-data-v2.json',
               ROOT / 'upstream/rea/scripts/verify/clients/model-fixture.mjs']
    profiles = {h: environment_profile.current(h) for h in ['deepseek-harness', 'zcode']}
    for profile in profiles.values():
        sources.extend(ROOT / p for p in profile['files_sha256'])
    inputs = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(sources + paths))}
    # A failed replay cannot leave a fresh-looking old PASS receipt.
    receipt = {'schema_version': 1, 'passed': False, 'model_api_calls': 0, 'profiles': profiles,
               'inputs_sha256': inputs, 'historical_scores_changed': False}
    OUTPUT.write_text(json.dumps(receipt, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='rea-evidence-replay-', dir='/tmp') as temporary:
        temporary = Path(temporary)
        request = {'evidence': [str(p.relative_to(ROOT)) for p in paths],
                   'temporary': str(temporary / 'dsh-patch'), 'output': str(temporary / 'result.json')}
        (temporary / 'input.json').write_text(json.dumps(request))
        result = subprocess.run([str(ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'),
                                 str(BENCH / 'scripts/replay-evidence.mjs'), str(temporary / 'input.json')],
                                cwd=ROOT, capture_output=True, text=True, timeout=180,
                                env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(temporary)})
        if result.returncode:
            raise RuntimeError(result.stderr[-6000:] or result.stdout[-6000:])
        replay = json.loads((temporary / 'result.json').read_text())
        receipt.update(replay)
        print(result.stdout.strip(), flush=True)
        # Exercise complete, real clients on the largest composite result, the
        # previously clipped C++14 procedure and Rust data inspection evidence.
        checks = replay['checks']
        largest = lambda subset: max(subset, key=lambda x: x['primary_bytes'])
        chosen = [largest([x for x in checks if not x['is_error']]),
                  largest([x for x in checks if Path(x['path']).parent.name == 'cpp14-string-abi1' and 'procedure_pseudo_code' in x['path']]),
                  largest([x for x in checks if Path(x['path']).parent.name == 'rust-data-v2' and 'read_bytes' in x['path']])]
        import run
        steps = []
        for check in chosen:
            path = ROOT / check['path']
            sequence = int(path.stem.split('-', 2)[1])
            trace = records.read_trace(path.parent / 'tool-calls.jsonl')
            call = next(c for c in trace if c['sequence'] == sequence)
            steps.append({'tool': call['tool'], 'arguments': call['arguments'], 'result_path': check['path']})
            receipt['inputs_sha256'][str((path.parent / 'tool-calls.jsonl').relative_to(ROOT))] = hashlib.sha256((path.parent / 'tool-calls.jsonl').read_bytes()).hexdigest()
        stl = next(e for e in records.registry()['experiments'] if e['suite'] == 'stl-native-v2' and e['role'] == 'primary')
        task = run.prepare_task({'experiment': stl['id'], 'case': 'rust-data-v2', 'directory': 'unused-offline'}, records.models()['glm-5.3'])
        tools = json.loads((Path(task['input_directory']) / 'tools.json').read_text())
        receipt['wire_tool_catalog_sha256'] = hashlib.sha256((Path(task['input_directory']) / 'tools.json').read_bytes()).hexdigest()
        # Use a temporary fixture input directory; never alter the frozen suite.
        spec = temporary / 'replay.json'
        spec.write_text(json.dumps({'tools': str(Path(task['input_directory']) / 'tools.json'), 'steps': steps}))
        dsh_directory = temporary / 'dsh'
        dsh_directory.mkdir()
        smoke_input = {'directory': str(dsh_directory), 'steps': steps, 'spec': str(spec), 'output': str(temporary / 'dsh.json')}
        (temporary / 'smoke.json').write_text(json.dumps(smoke_input))
        smoke = subprocess.run([str(ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'),
                                str(BENCH / 'scripts/smoke-evidence.mjs'), str(temporary / 'smoke.json')],
                               cwd=ROOT, capture_output=True, text=True, timeout=150,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(temporary),
                                    'REA_BENCHMARK_CONFIG': str(Path(task['input_directory']) / 'harness-config.json'),
                                    'NODE_OPTIONS': '--require ' + str(BENCH / 'scripts/offline-network.cjs')})
        if smoke.returncode:
            raise RuntimeError(smoke.stderr[-6000:] or smoke.stdout[-6000:])
        receipt['dsh_wire_smoke'] = json.loads((temporary / 'dsh.json').read_text())
        print(smoke.stdout.strip(), flush=True)
        backend = run.load_module(BENCH / 'harnesses/zcode.py', 'offline_zcode')
        assert backend.valid_output_ceiling(task['max_output_tokens'], task['max_output_tokens'])
        assert backend.valid_output_ceiling(1024, task['max_output_tokens'])
        for invalid in [0, -1, None, True, '128000', task['max_output_tokens'] + 1]:
            assert not backend.valid_output_ceiling(invalid, task['max_output_tokens'])
        task['tool_count'] = len(tools['tools'])
        directory = temporary / 'zcode'
        raw = backend.launch(directory, task, fixture=True, replay=steps)
        assert raw['exit_code'] == 0 and not raw['timed_out'], 'Offline ZCode CLI failed: ' + (directory/'stderr.log').read_text()[-1800:]
        assert not raw['upstream_error'] and not raw['policy_violations']
        requests = sorted((directory / 'wire').glob('request-*.json'))
        assert len(requests) == len(steps) + 1
        body = json.loads(requests[-1].read_text())
        delivered = []
        for i, check in enumerate(chosen, 1):
            message = next(m for m in body['messages'] if m.get('role') == 'tool' and m.get('tool_call_id') == f'fixture-{i}')
            assert hashlib.sha256(message['content'].encode()).hexdigest() == check['zcode_model_sha256'], 'ZCode wire changed projected evidence at step ' + str(i) + ': ' + message['content'][:180]
            delivered.append(len(message['content'].encode()))
        receipt['zcode_wire_smoke'] = {'passed': True, 'model_api_calls': 0, 'requests': len(requests),
            'tool_results': len(delivered), 'model_bytes': delivered,
            'delivered_sha256': [c['zcode_model_sha256'] for c in chosen],
            'last_request_sha256': hashlib.sha256(requests[-1].read_bytes()).hexdigest(),
            'output_token_caps': [json.loads(p.read_text()).get('max_tokens') for p in requests],
            'configured_output_ceiling': task['max_output_tokens'],
            'verification_layer': 'Actual ZCode CLI outgoing loopback HTTP messages'}
        receipt['wire_smoke_inputs'] = [c['path'] for c in chosen]
        print(f'PASS: ZCode CLI model transport preserves all {len(delivered)} cached bodies; no provider calls')
    OUTPUT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')


if __name__ == '__main__':
    main()
