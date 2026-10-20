#!/usr/bin/python3
"""Validate common lossless paging on cached evidence and both real clients offline."""
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
import subprocess
import tempfile

import records
import run
import environment_profile

ROOT, BENCH = records.ROOT, records.BENCH
OUTPUT = BENCH / 'config/evidence-paging-validation.json'
NODE = ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'


def execute(command, env=None):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)
    if result.returncode:
        source=Path(command[-1]).parent
        if source.is_relative_to(Path('/tmp')) and source.name.startswith('rea-paged-replay-'):
            destination=ROOT/'.cache/benchmark-runtime'/('paging-debug-'+str(time.time_ns()))
            shutil.copytree(source,destination)
            print('Saved offline failure diagnostics:',destination,flush=True)
        raise RuntimeError(result.stderr[-5000:] or result.stdout[-5000:])
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)


def main():
    data = records.collect()
    assert data['execution']['experiments_stopped']
    rows = [r for r in data['records'] + data['condition_records'] if r['role'] == 'primary']
    paths = sorted({p for r in rows for p in (ROOT/r['directory']).glob('evidence-[0-9]*.json')})
    source = BENCH/'runs/complex-pilot-common-budget-20261019/conditions/lossless-evidence-v1/gpt-6.1-sol/cpp/evidence-04-analyze_function.json'
    trace = records.read_trace(source.parent/'tool-calls.jsonl')
    arguments = next(c['arguments'] for c in trace if c['sequence'] == 4)
    profiles = {h: environment_profile.current(h, 'paged-evidence-v2') for h in ['deepseek-harness', 'zcode']}
    files = paths + [Path(__file__), BENCH/'scripts/replay-paging.mjs', BENCH/'scripts/replay-mcp.mjs',
                    BENCH/'scripts/smoke-evidence.mjs', BENCH/'scripts/offline-network.cjs', source.parent/'tool-calls.jsonl']
    for profile in profiles.values():
        files += [ROOT/name for name in profile['files_sha256']]
    inputs = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
    receipt = {'schema_version': 1, 'passed': False, 'model_api_calls': 0, 'inputs_sha256': inputs, 'profiles': profiles}
    OUTPUT.write_text(json.dumps(receipt, indent=2)+'\n')
    with tempfile.TemporaryDirectory(prefix='rea-paged-replay-',dir='/tmp') as temp:
        temp = Path(temp)
        request = {'root':str(ROOT),'temporary':str(temp),'evidence':[str(p.relative_to(ROOT)) for p in paths],
                   'smoke_source':str(source.relative_to(ROOT)),'smoke_arguments':arguments,'output':str(temp/'result.json')}
        (temp/'input.json').write_text(json.dumps(request))
        execute([str(NODE),str(BENCH/'scripts/replay-paging.mjs'),str(temp/'input.json')])
        result = json.loads((temp/'result.json').read_text())
        receipt.update(result)
        steps = result['steps']
        entry = next(e for e in run.plan(model='glm-5.3',condition='paged-evidence-v2') if e['suite']=='complex-native-v1' and e['case']=='cpp')
        task = run.prepare_task(entry, records.models()['glm-5.3'])
        frozen = Path(task['input_directory'])
        (temp/'spec.json').write_text(json.dumps({'tools':str(frozen/'tools.json'),'steps':steps,
            'evidence_paging':records.read(BENCH/'config/evidence-paging.json')}))
        directory = temp/'dsh'
        directory.mkdir()
        (temp/'smoke.json').write_text(json.dumps({'directory':str(directory),'steps':steps,'spec':str(temp/'spec.json'),'output':str(temp/'dsh.json')}))
        env = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':str(temp),
               'REA_BENCHMARK_CONFIG':str(frozen/'harness-config.json'),
               'NODE_OPTIONS':'--require '+str(BENCH/'scripts/offline-network.cjs')}
        execute([str(NODE),str(BENCH/'scripts/smoke-evidence.mjs'),str(temp/'smoke.json')],env)
        receipt['dsh_wire_smoke']=json.loads((temp/'dsh.json').read_text())
        backend = run.load_module(BENCH/'harnesses/zcode.py','paging_zcode')
        directory = temp/'zcode'
        raw = backend.launch(directory,task,fixture=True,replay=steps)
        assert raw['exit_code']==0 and not raw['timed_out'] and not raw['policy_violations'], 'ZCode paging fixture failed'
        requests=sorted((directory/'wire').glob('request-*.json'))
        assert len(requests)==4
        body=json.loads(requests[-1].read_text())
        observed=[]
        for i,expected in enumerate(result['expected_model_sha256'],1):
            message=next(m for m in body['messages'] if m.get('tool_call_id')==f'fixture-{i}' and m['role']=='tool')
            digest=hashlib.sha256(message['content'].encode()).hexdigest()
            assert digest==expected,'Actual ZCode paging output changed'
            observed.append(digest)
        receipt['zcode_wire_smoke']={'passed':True,'tool_results':3,'requests':4,'delivered_sha256':observed,'model_api_calls':0}
        receipt.pop('steps',None)
        receipt['smoke_source']=str(source.relative_to(ROOT))
        print('PASS: real DSH and ZCode clients preserve the same projected result and reachable read_evidence pages; zero provider calls',flush=True)
    OUTPUT.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    main()
