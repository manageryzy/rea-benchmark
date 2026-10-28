"""Execute only the frozen trusted L6 original, inside the pinned C++ image."""
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run

if os.environ.get('REA_OPTIMIZED_LOCATION_WORKER') != '1':
    raise RuntimeError('Optimized-location calibration requires the closed image')
request = json.load(sys.stdin)
if (set(request) != {'private_hex', 'nonces'} or not isinstance(request['nonces'], list)
        or not 1 <= len(request['nonces']) <= 64
        or any(type(n) is not int or not 0 <= n < 2**64 for n in request['nonces'])):
    raise ValueError('Invalid original calibration request')
image = bytes.fromhex(request['private_hex'])
if not 1 <= len(image) <= 4 * 1024**2:
    raise ValueError('Original calibration ELF exceeds its bound')
resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
resource.setrlimit(resource.RLIMIT_AS, (1024**3,) * 2)
Path('/work/original').write_bytes(image)
Path('/work/original').chmod(0o500)
inputs = [(selected, nonce) for nonce in request['nonces'] for selected in range(9)]
reply = run(['/work/original'], input=''.join(f'{selected} {nonce:x}\n' for selected, nonce in inputs).encode(),
            timeout=12, output_limit=65536)
rows = reply.stdout.splitlines()
if reply.returncode or len(rows) != len(inputs):
    raise RuntimeError('Original optimized-location calibration failed')
observations = []
for row, (selected, nonce) in zip(rows, inputs):
    fields = row.split(b' ')
    if len(fields) != 2 or fields[0] != str(selected).encode() or len(fields[1]) != 16:
        raise ValueError('Original calibration emitted malformed records')
    value = int(fields[1], 16)
    observations.append({'selected': selected, 'nonce': nonce, 'value': value})
print(json.dumps({'observations': observations}, sort_keys=True))
