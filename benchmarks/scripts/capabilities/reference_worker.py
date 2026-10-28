"""Execute only the frozen trusted original; candidate execution is separate."""
import json
import os
from pathlib import Path
import resource
import sys

if os.environ.get('REA_CAPABILITY_REFERENCE') != '1':
    raise RuntimeError('Original reference requires the closed worker')
sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run
from grading.c_executor.context import host

resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
request = json.load(sys.stdin)
target = Path('/work/target')
target.write_bytes(bytes.fromhex(request['target_hex']))
target.chmod(0o500)
lines = []
for case in request['cases']:
    raw = bytes.fromhex(case['input_hex'])
    lines.append(f"{case['id']} {len(raw)} {case['capacity']} {case['output_offset']} {case['seed']} {raw.hex() or '-'}")
result = run([str(target)], input=('\n'.join(lines) + '\n').encode(), timeout=10, output_limit=4 * 1024**2)
if result.returncode or result.stderr:
    raise RuntimeError('Frozen original failed its reference protocol')
observations = []
for line in result.stdout.splitlines():
    fields = line.split()
    if len(fields) != 4:
        raise RuntimeError('Original reference emitted an invalid record')
    identity, status, length = (int(value) for value in fields[:3])
    memory = bytes.fromhex(fields[3].decode('ascii'))
    if len(memory) != 2048 or status not in (0, -1, -2) or not 0 <= length <= 520:
        raise RuntimeError('Original reference exceeded its buffer contract')
    observations.append({'id': identity, 'status': status, 'length': length, 'arena_hex': memory.hex()})
if [row['id'] for row in observations] != [row['id'] for row in request['cases']]:
    raise RuntimeError('Original reference records missing, duplicated or shifted')
print(json.dumps({'observations': observations, 'host': host()}, sort_keys=True))
