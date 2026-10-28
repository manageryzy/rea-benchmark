"""Bounded trusted-original CLI observation; no candidate or expected answers."""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run
from grading.c_executor.context import host

if os.environ.get('REA_BASELINE_REFERENCE_WORKER') != '1':
    raise RuntimeError('Baseline observation requires the closed worker')
profile = json.loads(Path('/environment/profile.json').read_bytes())
if set(profile) != {'memory_bytes', 'wall_seconds', 'cpu_seconds', 'output_bytes'}:
    raise ValueError('Unknown baseline observation policy')
resource.setrlimit(resource.RLIMIT_CPU, (profile['cpu_seconds'],) * 2)
resource.setrlimit(resource.RLIMIT_AS, (profile['memory_bytes'],) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
raw = sys.stdin.buffer.read(262145)
if len(raw) > 262144:
    raise ValueError('Baseline invocation list exceeds its bound')
request = json.loads(raw)
if set(request) != {'cases'} or not isinstance(request['cases'], list) or not 1 <= len(request['cases']) <= 128:
    raise ValueError('Invalid baseline invocation list')
lines = []
for case in request['cases']:
    if (set(case) != {'id', 'input_hex', 'capacity', 'output_offset', 'seed', 'capabilities'}
            or any(type(case[n]) is not int for n in ('id', 'capacity', 'output_offset', 'seed'))
            or not 0 <= case['id'] < 10**12 or not 0 <= case['capacity'] <= 520
            or not 0 <= case['output_offset'] <= 1528 or not 0 <= case['seed'] <= 255):
        raise ValueError('Invalid baseline invocation fields')
    data = bytes.fromhex(case['input_hex'])
    if len(data) > 513:
        raise ValueError('Baseline input exceeds its declared size')
    tags = case['capabilities']
    if (not isinstance(tags, list) or not 1 <= len(tags) <= 4
            or any(not isinstance(tag, str) for tag in tags)
            or len(set(tags)) != len(tags) or set(tags) - {'C1', 'C2', 'C3', 'C4'}):
        raise ValueError('Invalid baseline score tags')
    lines.append(f"{case['id']} {len(data)} {case['capacity']} {case['output_offset']} {case['seed']} {data.hex() or '-'}")
if len({row['id'] for row in request['cases']}) != len(lines):
    raise ValueError('Duplicate baseline invocation IDs')
target = Path('/environment/target.elf').read_bytes()
if not 1 <= len(target) <= 32 * 1024**2:
    raise ValueError('Baseline original exceeds its byte bound')
Path('/work/target').write_bytes(target)
Path('/work/target').chmod(0o500)
reply = run(['/work/target'], input=('\n'.join(lines) + '\n').encode(),
            timeout=profile['wall_seconds'], output_limit=profile['output_bytes'],
            env={**os.environ, 'GOMEMLIMIT': '256MiB', 'GOMAXPROCS': '2'})
if reply.returncode or reply.stderr:
    raise RuntimeError('Trusted baseline failed its reference protocol: ' + reply.stderr.decode(errors='replace')[-2000:])
observations = []
for line in reply.stdout.splitlines():
    fields = line.split()
    if len(fields) != 4:
        raise ValueError('Malformed trusted-original record')
    identity, status, length = map(int, fields[:3])
    arena = bytes.fromhex(fields[3].decode('ascii'))
    if len(arena) != 2048 or status not in (0, -1, -2) or not 0 <= length <= 520:
        raise ValueError('Trusted-original record exceeds its contract')
    observations.append({'id': identity, 'status': status, 'length': length, 'arena_hex': arena.hex()})
if [row['id'] for row in observations] != [row['id'] for row in request['cases']]:
    raise ValueError('Original records are missing, duplicated or shifted')
print(json.dumps({'binary_sha256': hashlib.sha256(target).hexdigest(),
                  'observations': observations, 'host': host(), 'scope': 'trusted_original_cli_only'}, sort_keys=True))
