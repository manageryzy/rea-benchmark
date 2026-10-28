"""Closed worker for the trusted original lifecycle frontend; no candidate."""
import json
import os
from pathlib import Path
import resource
import sys

if os.environ.get('REA_LIFECYCLE_REFERENCE') != '1':
    raise RuntimeError('Lifecycle original requires the closed worker')
sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run
from grading.c_executor.context import host

resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
request = json.load(sys.stdin)
if set(request) != {'target_hex', 'scenarios'} or len(request['target_hex']) > 64 * 1024**2:
    raise ValueError('Invalid original lifecycle request')
target = Path('/work/target')
target.write_bytes(bytes.fromhex(request['target_hex']))
target.chmod(0o500)
lines, ordered = [], []
if not 1 <= len(request['scenarios']) <= 12:
    raise ValueError('Unbounded original scenarios')
for scenario in request['scenarios']:
    if not 1 <= len(scenario['steps']) <= 32:
        raise ValueError('Unbounded original steps')
    for index, step in enumerate(scenario['steps']):
        identity = len(ordered)
        ordered.append((scenario['id'], step))
        lines.append(' '.join(str(item) for item in (identity, int(index == 0), step['instance'],
            step['operation'], step['value'], step['limit'], step['response'], step['seed'])))
if len(ordered) > 256:
    raise ValueError('Too many original steps')
reply = run([str(target)], input=('\n'.join(lines) + '\n').encode(), timeout=12, output_limit=4 * 1024**2)
if reply.returncode or reply.stderr:
    raise RuntimeError('Lifecycle original failed its protocol')
records = reply.stdout.splitlines()
if len(records) != len(ordered):
    raise ValueError('Missing or extra original lifecycle observations')
observations = []
for identity, (line, (scenario, step)) in enumerate(zip(records, ordered)):
    fields = line.split()
    if len(fields) < 4 or int(fields[0]) != identity:
        raise ValueError('Invalid or shifted original observation')
    status, arena, count = int(fields[1]), bytes.fromhex(fields[2].decode('ascii')), int(fields[3])
    if status not in (0, -1, -2, -3, -4, -5) or len(arena) != 512 or not 0 <= count <= 1 or len(fields) != 4 + 3 * count:
        raise ValueError('Invalid original lifecycle result')
    arguments = [int(value) for value in fields[4:]]
    if any(not 0 <= value < 2**64 for value in arguments):
        raise ValueError('Invalid original callback argument')
    callbacks = [{'arguments': arguments, 'return': step['response']}] if count else []
    observations.append({'scenario_id': scenario, 'id': step['id'], 'status': status,
                         'arena_hex': arena.hex(), 'callbacks': callbacks})
print(json.dumps({'observations': observations, 'host': host()}, sort_keys=True))
