"""Emulator worker receives one invocation, never an oracle or future vectors."""
import json
import os
from pathlib import Path
import resource
import ctypes
import sys
import time

if os.environ.get('REA_C_EXECUTOR') != '1':
    raise RuntimeError('This entrypoint requires the sealed C executor')
profile = json.loads(Path('/environment/profile.json').read_bytes())
limits = profile['limits']
resource.setrlimit(resource.RLIMIT_CPU, (limits['worker_cpu_seconds'],) * 2)
resource.setrlimit(resource.RLIMIT_AS, (limits['worker_memory_bytes'],) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
sys.path[:0] = ['/environment/benchmarks/scripts', '/addon']
ctypes.CDLL('/addon/unicorn/lib/libunicorn.so.2')
import unicorn
from unicorn.unicorn_py3 import unicorn as bindings
from grading.c_executor.machine import Machine
from grading.c_executor.elf import UnsupportedELF
from grading.c_executor.context import host

if unicorn.__version__ != profile['engine']['version'] or bindings.uclib._name != '/addon/unicorn/lib/libunicorn.so.2':
    raise RuntimeError('Emulator version or loaded native library differs from its binding')
request = json.load(sys.stdin)
if not isinstance(request, dict) or set(request) != {'elf_hex', 'entry', 'invocation', 'state'}:
    raise ValueError('Emulator worker accepts one declared invocation only')
raw = bytes.fromhex(request['elf_hex'])
try:
    machine = Machine(raw, request['entry'], request['invocation'], limits, request['state'])
    Path('/work/phase-times.json').write_text(json.dumps({'execution_start_ns': time.monotonic_ns()})+'\n')
    result = machine.run()
except UnsupportedELF as error:
    result = {'schema_version': 1, 'status': 'unsupported_elf', 'reason': str(error),
              'invocation_id': request['invocation']['id'], 'return_registers': None,
              'return_record': None,
              'provenance': 'host_emulator_return_v1', 'regions': [], 'state': [], 'callbacks': [], 'instructions': 0}
result['engine_version'] = unicorn.__version__
result['host'] = host()
print(json.dumps(result, sort_keys=True))
