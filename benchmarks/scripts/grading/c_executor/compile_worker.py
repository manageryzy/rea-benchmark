"""Compile only the submitted C and public guest helpers inside the pinned image."""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import sys
import time

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading import process
from grading.c_executor.context import host

profile = json.loads(Path('/environment/profile.json').read_bytes())
request = json.load(sys.stdin)
if set(request) != {'source', 'exports', 'header'} or not isinstance(request['source'], str) or not isinstance(request['header'], str):
    raise ValueError('Compiler accepts source and declared exports only')
source = request['source'].encode('utf-8')
if len(source) > profile['max_source_bytes'] or len(request['header'].encode('utf-8')) > profile['max_source_bytes']:
    raise ValueError('Candidate source exceeds the profile')
exports = request['exports']
if (not isinstance(exports, list) or not exports or len(exports) > 32
        or any(not isinstance(name, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', name) for name in exports)):
    raise ValueError('Invalid exported C entry names')
Path('/work/solution.c').write_bytes(source)
Path('/work/types.h').write_text('#include <stdint.h>\n#include <stddef.h>\n' + request['header'])
for name in ('solution.c', 'types.h'):
    os.utime('/work/' + name, (0, 0))
prefix = '/environment/benchmarks/scripts/grading/c_executor/'
command = ['/usr/bin/gcc', *profile['compiler_flags'], '-include', '/work/types.h',
           '/work/solution.c', prefix + 'helpers.c', '-Wl,-T,' + prefix + 'link.ld',
           '-Wl,--build-id=none', '-Wl,--no-undefined', '-Wl,-e,' + exports[0], '-o', '/work/candidate.elf']


def limits():
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


Path('/work/phase-times.json').write_text(json.dumps({'compile_start_ns': time.monotonic_ns()})+'\n')
result = process.run(command, timeout=120, output_limit=262144, env=dict(os.environ), preexec_fn=limits)
Path('/work/compile.log').write_bytes(result.stdout + result.stderr)
output = Path('/work/candidate.elf')
compiled = result.returncode == 0 and output.is_file()
response = {'schema_version': 1, 'status': 'compiled' if compiled else 'compile_error',
            'source_sha256': hashlib.sha256(source).hexdigest(), 'command': command, 'exports': exports,
            'elf_sha256': hashlib.sha256(output.read_bytes()).hexdigest() if compiled else None}
response['host'] = host()
print(json.dumps(response, sort_keys=True))
