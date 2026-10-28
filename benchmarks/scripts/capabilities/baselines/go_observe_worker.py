"""Sealed emulator accepts one Go candidate buffer invocation, never an oracle."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

if os.environ.get('REA_GO_BUFFER_OBSERVER') != '1':
    raise RuntimeError('Go buffer observation requires the closed worker')
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
from grading.c_executor.context import host
from capabilities.baselines.go_observer import observe

if unicorn.__version__ != profile['engine']['version'] or bindings.uclib._name != '/addon/unicorn/lib/libunicorn.so.2':
    raise RuntimeError('Go observer emulator/library binding differs')
request = json.load(sys.stdin)
if not isinstance(request, dict) or set(request) != {'invocation'}:
    raise ValueError('Go observer accepts one invocation without expected answers')
raw = Path('/environment/candidate.elf').read_bytes()
result = observe(raw, request['invocation'], limits)
result.update(engine_version=unicorn.__version__, host=host(),
              request_sha256=hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False,
                  separators=(',', ':'), allow_nan=False).encode()).hexdigest())
print(json.dumps(result, sort_keys=True))
