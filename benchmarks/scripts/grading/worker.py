"""Trusted stdin/JSON grader inside the pinned image; no provider capabilities."""
import json
import os
from pathlib import Path
import sys
import platform

if os.environ.get('REA_GRADING_WORKER') != '1':
    raise RuntimeError('This entrypoint requires the closed grading runtime')
sys.path.insert(0, '/environment/benchmarks/scripts')
from grading import native

request = json.load(sys.stdin)
record = json.loads(Path('/environment/manifest.json').read_bytes())
if 'Python ' + platform.python_version() != record['manifest']['python_version']:
    raise RuntimeError('Pinned grading interpreter version mismatch')
if request['operation'] == 'go_policy':
    result = native.go_policy(request['code'])
elif request['operation'] == 'evaluate':
    # Arbitrary byte-return controls need an explicit binary representation;
    # ordinary hidden string/integer vectors retain their existing contracts.
    for case in request['cases']:
        if isinstance(case['expected'], dict) and set(case['expected']) == {'raw_bytes_hex'}:
            case['expected'] = bytes.fromhex(case['expected']['raw_bytes_hex'])
    result = native.evaluate(request['code'], request['language'], request['cases'], request['track'],
                             request['standard'], request.get('abi'), Path('/work/artifacts'))
else:
    raise ValueError('Unsupported grading operation')
sys.stdout.write(json.dumps({'environment_id': record['id'], 'result': result}, sort_keys=True) + '\n')
