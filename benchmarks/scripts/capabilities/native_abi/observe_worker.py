"""One closed native-ABI invocation, without an oracle or future vectors."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

if os.environ.get('REA_NATIVE_ABI_WORKER') != '1':
    raise RuntimeError('Native ABI observation requires its sealed worker')
profile = json.loads(Path('/environment/profile.json').read_bytes())
if profile.get('id') != 'bounded-native-aggregate-boundaries-v1':
    raise ValueError('Wrong native ABI execution profile')
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
from capabilities.native_abi.observer_worker import observe

if unicorn.__version__ != profile['engine']['version'] or bindings.uclib._name != '/addon/unicorn/lib/libunicorn.so.2':
    raise RuntimeError('Native ABI emulator differs from the pinned binding')
request = json.load(sys.stdin)
prefix = Path('/environment/target')
probe = (prefix / 'probe.elf').read_bytes()
result = observe((prefix / 'original.elf').read_bytes(), (prefix / 'public.elf').read_bytes(),
                 probe or None, request, limits)
result['host'] = host()
result['engine_version'] = unicorn.__version__
result['request_sha256'] = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
print(json.dumps(result, sort_keys=True))
