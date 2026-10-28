"""Calibrate only trusted original Rust/Go programs, not submitted artifacts."""
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run

if os.environ.get('REA_NATIVE_ABI_WORKER') != '1':
    raise RuntimeError('Native ABI calibration requires a closed image')
request = json.load(sys.stdin)
if (set(request) != {'language', 'private_hex', 'vectors'} or request['language'] not in ('rust', 'go')
        or not isinstance(request['vectors'], list) or not 1 <= len(request['vectors']) <= 64):
    raise ValueError('Invalid native ABI calibration request')
for row in request['vectors']:
    if (not isinstance(row, dict) or set(row) != {'nonce', 'first', 'second', 'third'}
            or any(type(v) is not int or not 0 <= v < 2**64 for v in row.values())):
        raise ValueError('Invalid native ABI argument vector')
image = bytes.fromhex(request['private_hex'])
if not 1 <= len(image) <= 32 * 1024**2:
    raise ValueError('Native ABI original ELF exceeds its bound')
resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
Path('/work/original').write_bytes(image)
Path('/work/original').chmod(0o500)
inputs = [(selected, values) for values in request['vectors']
          for selected in range(3 if request['language'] == 'rust' else 2)]
payload = ''.join(f'{selected} ' + ' '.join(f'{values[name]:x}' for name in ('nonce', 'first', 'second', 'third'))
                  + '\n' for selected, values in inputs)
reply = run(['/work/original'], input=payload.encode(), timeout=20, output_limit=65536,
            env={**os.environ, 'GOMEMLIMIT': '256MiB', 'GOMAXPROCS': '2'})
lines = reply.stdout.splitlines()
if reply.returncode or len(lines) != len(inputs):
    raise RuntimeError('Native ABI original failed calibration: ' + reply.stderr.decode(errors='replace')[-2000:])
observations = []
for line, (selected, values) in zip(lines, inputs):
    fields = line.split(b' ')
    if len(fields) != 4 or fields[0] != str(selected).encode() or any(len(field) != 16 for field in fields[1:]):
        raise ValueError('Native ABI original emitted malformed calibration output')
    result = [int(field, 16) for field in fields[1:]]
    if any(not 0 <= value < 2**64 for value in result):
        raise ValueError('Native ABI original emitted non-uint64 output')
    observations.append({'selected': selected, 'arguments': values, 'values': result})
print(json.dumps({'observations': observations}, sort_keys=True))
