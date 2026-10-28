"""Pinned Python worker for structured capability artifacts."""
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import sys

if os.environ.get('REA_STRUCTURED_WORKER') != '1':
    raise RuntimeError('Structured artifacts require the closed worker')
sys.path.insert(0, '/environment/benchmarks/scripts')
from grading import artifacts, localization
from grading.c_executor.context import host

record = json.loads(Path('/environment/runtime.json').read_bytes())
profile = json.loads(Path('/environment/profile.json').read_bytes())
if 'Python ' + platform.python_version() != record['python_version']:
    raise RuntimeError('Structured-worker interpreter differs from its pinned image')
limits = profile['limits']
resource.setrlimit(resource.RLIMIT_CPU, (limits['cpu_seconds'], limits['cpu_seconds']))
resource.setrlimit(resource.RLIMIT_AS, (limits['memory_bytes'], limits['memory_bytes']))
resource.setrlimit(resource.RLIMIT_FSIZE, (limits['output_bytes'], limits['output_bytes']))
resource.setrlimit(resource.RLIMIT_NOFILE, (limits['open_files'], limits['open_files']))
raw = sys.stdin.buffer.read(limits['input_bytes'] + 1)
if len(raw) > limits['input_bytes']:
    raise RuntimeError('Structured request exceeds its declared byte limit')
request = json.loads(raw)
if set(request) != {'kind', 'files_hex', 'truth'} or request['kind'] != 'localization':
    raise RuntimeError('Unregistered structured grading operation')
truth = request['truth']
if truth.get('schema_version') != 1 or truth.get('validated') is not True:
    raise RuntimeError('Invalid private truth; no model correctness grade')
files = {name: bytes.fromhex(value) for name, value in request['files_hex'].items()}
try:
    admitted = artifacts.admit(files, {'locations.json': 1})
except ValueError:
    grade = {'status': 'invalid_artifact', 'artifact_valid': False, 'capabilities': {}}
else:
    value = admitted['locations.json']['value']
    if value['binary_sha256'] != truth['binary_sha256']:
        grade = {'status': 'wrong_target', 'artifact_valid': False, 'capabilities': {}}
    else:
        grade = {'status': 'graded', **localization.grade(value, truth)}
sources = {name: hashlib.sha256(Path('/environment', name).read_bytes()).hexdigest()
           for name in json.loads(Path('/environment/sources.json').read_bytes())}
reply = {'result': grade, 'runtime_id': record['id'], 'host': host(), 'source_inputs_sha256': sources}
encoded = json.dumps(reply, sort_keys=True, separators=(',', ':')).encode()
if len(encoded) > limits['output_bytes']:
    raise RuntimeError('Structured result exceeds its output bound')
sys.stdout.buffer.write(encoded)
