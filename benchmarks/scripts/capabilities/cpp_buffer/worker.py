"""Compile the candidate and public helpers only, within a pinned C++ image."""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading import process
from grading.c_executor import elf
from grading.c_executor.context import host

if os.environ.get('REA_CPP_BUFFER_WORKER') != '1':
    raise RuntimeError('C++ buffer compilation requires the closed worker')
profile = json.loads(Path('/environment/profile.json').read_bytes())
request = json.load(sys.stdin)
if set(request) != {'source'} or not isinstance(request['source'], str):
    raise ValueError('C++ compiler accepts candidate source only')
raw = request['source'].encode('utf-8')
if not 1 <= len(raw) <= profile['limits']['source_bytes'] or b'\0' in raw:
    raise ValueError('Invalid C++ source byte transport')
Path('/work/solution.cpp').write_bytes(raw)
os.utime('/work/solution.cpp', (0, 0))
prefix = '/environment/benchmarks/scripts/capabilities/cpp_buffer/'
command = ['/usr/bin/g++', *profile['compiler_flags'], '-include', prefix + 'contract.hpp',
    '/work/solution.cpp', prefix + 'helpers.cpp',
    '-Wl,-T,/environment/benchmarks/scripts/grading/c_executor/link.ld',
    '-Wl,--build-id=none', '-Wl,--no-undefined', '-Wl,-e,reconstructed', '-o', '/work/candidate.elf']

def limits():
    values = profile['limits']
    resource.setrlimit(resource.RLIMIT_CPU, (values['compile_cpu_seconds'],) * 2)
    resource.setrlimit(resource.RLIMIT_AS, (values['compile_memory_bytes'],) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

reply = process.run(command, timeout=profile['limits']['compile_wall_seconds'], output_limit=262144,
                    env=dict(os.environ), preexec_fn=limits)
output = Path('/work/candidate.elf')
result = {'status': 'compile_error', 'source_sha256': hashlib.sha256(raw).hexdigest(),
          'command': command, 'compile_log': (reply.stdout + reply.stderr).decode(errors='replace'), 'host': host()}
if reply.returncode == 0 and output.is_file():
    image = output.read_bytes()
    try:
        elf.load(image, 'reconstructed', profile['elf_limits'])
    except elf.UnsupportedELF as error:
        result.update(status='unsupported_image', reason=str(error))
    else:
        result.update(status='compiled', elf_hex=image.hex(), elf_sha256=hashlib.sha256(image).hexdigest())
print(json.dumps(result, sort_keys=True))
