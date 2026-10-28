"""Closed L6 data-only grading from exact original bytes and fresh review code."""
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import sys

if os.environ.get('REA_OPTIMIZED_GRADE_WORKER') != '1':
    raise RuntimeError('Optimized location grading requires the closed image')
sys.path.insert(0, '/environment/benchmarks/scripts')
from capabilities.optimized_locations import artifact, scoring
from grading.c_executor.context import host

profile = json.loads(Path('/environment/profile.json').read_bytes())
record = json.loads(Path('/environment/runtime.json').read_bytes())
if 'Python ' + platform.python_version() != record['python_version']:
    raise RuntimeError('Optimized grading interpreter differs from its binding')
limits = profile['limits']
resource.setrlimit(resource.RLIMIT_CPU, (limits['cpu_seconds'],) * 2)
resource.setrlimit(resource.RLIMIT_AS, (limits['memory_bytes'],) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (limits['output_bytes'],) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (limits['open_files'],) * 2)
raw = sys.stdin.buffer.read(limits['input_bytes'] + 1)
if len(raw) > limits['input_bytes']:
    raise ValueError('Optimized grading request exceeds its bound')
request = json.loads(raw)
if set(request) != {'artifact_hex', 'target', 'mode'} or request['mode'] not in ('optimized', 'lto'):
    raise ValueError('Unknown optimized grading request')
# Host-side function patches cannot substitute private truth or a score: the
# child reconstructs reviewed truth from the sealed final binary diagnostics.
truth = scoring.truth(request['target'], request['mode'])
raw_artifact = bytes.fromhex(request['artifact_hex'])
try:
    value = artifact.admit(raw_artifact)
except ValueError:
    value = None
count = len(truth['realizations'])
if value is None or value['binary_sha256'] != truth['binary_sha256']:
    result = {'schema_version': 1, 'status': 'invalid_artifact' if value is None else 'wrong_binary',
              'artifact_valid': value is not None, 'compiled': False, 'passed': 0, 'total': count,
              'task_success': False, 'program_success': False, 'capability_ids': ['L6'],
              'capabilities': {'L6': {'f1': 0.0, 'precision': 0.0, 'coverage': 0.0, 'task_success': False}},
              'diagnostic_only': True, 'release_accepted': False}
else:
    score = scoring.grade(raw_artifact, truth)
    result = {**score, 'status': 'graded', 'compiled': True,
              'passed': score['complete_instances'], 'total': max(count, score['submitted_instances']),
              'task_success': score['complete'], 'program_success': score['complete'],
              'capabilities': {'L6': {key: score[key] for key in ('f1', 'precision', 'coverage')} | {'task_success': score['complete']}}}
sources = {name: hashlib.sha256(Path('/environment', name).read_bytes()).hexdigest()
           for name in json.loads(Path('/environment/sources.json').read_bytes())}
output = {'result': result, 'runtime_id': record['id'], 'host': host(), 'source_inputs_sha256': sources}
encoded = json.dumps(output, sort_keys=True, separators=(',', ':')).encode()
if len(encoded) > limits['output_bytes']:
    raise ValueError('Optimized grading response exceeds its bound')
sys.stdout.buffer.write(encoded)
