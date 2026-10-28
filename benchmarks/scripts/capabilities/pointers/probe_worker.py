"""Parse descriptions and compile original-object probes inside the closed image."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from capabilities.abi.compiler import compile_file, link, limits, host
from grading.artifacts import decode
from capabilities.pointers import artifacts, codegen

request = json.load(sys.stdin)
if set(request) != {'mode', 'artifact_hex', 'binary_sha256', 'original_obj_hex', 'helpers_obj_hex',
                    'original_obj_sha256', 'helpers_obj_sha256'} or request['mode'] != 'probe':
    raise ValueError('Invalid pointer-description compiler request')
try:
    value = artifacts.admit(decode(bytes.fromhex(request['artifact_hex'])))
except ValueError as error:
    print(json.dumps({'status': 'invalid_artifact', 'artifact_valid': False, 'reason': str(error), 'host': host()}))
    raise SystemExit(0)
if value['binary_sha256'] != request['binary_sha256']:
    print(json.dumps({'status': 'incorrect_description', 'artifact_valid': True,
                      'reason': 'Description targets a different binary', 'host': host()}))
    raise SystemExit(0)
source = codegen.generate(value).encode('utf-8')
if len(source) > limits['source_bytes']:
    raise ValueError('Generated pointer probe exceeds the source bound')
Path('/work/probe.cpp').write_bytes(source)
Path('/work/fixtures.hpp').write_bytes(Path('/environment/benchmarks/suites/capabilities-v1/sources/pointers/fixtures.hpp').read_bytes())
for name in ('original', 'helpers'):
    raw = bytes.fromhex(request[name + '_obj_hex'])
    if not 1 <= len(raw) <= limits['object_bytes'] or hashlib.sha256(raw).hexdigest() != request[name + '_obj_sha256']:
        raise ValueError('Frozen pointer-probe object size/hash mismatch')
    Path('/work/' + name + '.o').write_bytes(raw)
compile_file('/work/probe.cpp', '/work/slot.o')
result = link(['/work/original.o', '/work/helpers.o', '/work/slot.o'])
result['generated_source_sha256'] = hashlib.sha256(source).hexdigest()
print(json.dumps(result, sort_keys=True))
