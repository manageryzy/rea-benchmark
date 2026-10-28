"""Admit the actual submission and generate probes inside the pinned C++ image."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from capabilities.abi.compiler import policy, limits, compile_file, link, host
from grading.artifacts import decode
from capabilities.abi import artifacts, codegen, contracts

request = json.load(sys.stdin)
if set(request) != {'mode', 'contract', 'artifact_hex', 'original_obj_hex', 'helpers_obj_hex'} or request['mode'] != 'probe':
    raise ValueError('Invalid submitted-description compiler operation')
contract = request['contract']
family, condition = contract['family'], contract['condition']
if family not in policy['families'] or condition not in contracts.CONDITIONS:
    raise ValueError('Unknown ABI probe contract')
try:
    value = decode(bytes.fromhex(request['artifact_hex']))
    value = (artifacts.abi if condition == 'abi_recover' else artifacts.types)(value)
except ValueError as error:
    print(json.dumps({'status': 'invalid_artifact', 'artifact_valid': False, 'reason': str(error), 'host': host()}))
    raise SystemExit(0)
reason = None
if value['binary_sha256'] != contract['binary_sha256']:
    reason = 'Artifact targets a different binary'
elif condition == 'abi_recover':
    if value['entry_rva'] != contract['entry_rva'] or not codegen.coherent_abi(value, family):
        reason = 'ABI machine-location claims are inconsistent with the declared convention or target entry'
    convention = value['convention']
    description = contracts.gold(family, contract['binary_sha256'])
else:
    convention, description = contract['convention'], value
    try:
        codegen.known_facts(description, family, condition)
    except ValueError as error:
        reason = str(error)
if reason:
    print(json.dumps({'status': 'incorrect_description', 'artifact_valid': True, 'reason': reason, 'host': host()}))
    raise SystemExit(0)
source = codegen.generate(description, family, convention, value if condition == 'abi_recover' else None).encode()
if len(source) > limits['source_bytes']:
    raise ValueError('Generated probe exceeds the bounded source profile')
Path('/work/probe.cpp').write_bytes(source)
for name in ('original', 'helpers'):
    raw = bytes.fromhex(request[name + '_obj_hex'])
    if not 1 <= len(raw) <= limits['object_bytes'] or hashlib.sha256(raw).hexdigest() != contract[name + '_obj_sha256']:
        raise ValueError('Frozen original object size/hash mismatch')
    Path('/work/' + name + '.o').write_bytes(raw)
compile_file('/work/probe.cpp', '/work/slot.o')
result = link(['/work/original.o', '/work/helpers.o', '/work/slot.o'])
result['generated_source_sha256'] = hashlib.sha256(source).hexdigest()
print(json.dumps(result, sort_keys=True))
