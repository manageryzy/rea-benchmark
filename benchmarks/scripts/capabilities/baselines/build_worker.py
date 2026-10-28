"""Compile only trusted matched baseline fixtures in a sealed compiler image."""
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run

if os.environ.get('REA_BASELINE_BUILD_WORKER') != '1':
    raise RuntimeError('Baseline compilation requires the closed worker')
request = json.load(sys.stdin)
inventories = {'clang20': {'family.cpp', 'common.hpp'}, 'rust': {'program.rs', 'family.rs'},
               'go': {'program.go', 'family.go'}}
if (set(request) != {'compiler', 'family', 'files', 'flags'} or request['compiler'] not in inventories
        or set(request['files']) != inventories[request['compiler']]
        or any(not isinstance(value, str) or len(value.encode()) > 262144 for value in request['files'].values())
        or not isinstance(request['flags'], list) or any(not isinstance(flag, str) for flag in request['flags'])):
    raise ValueError('Invalid trusted baseline build request')
resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
for name, value in request['files'].items(): Path('/work', name).write_text(value)
commands = []


def execute(arguments, environment=None, maximum=8 * 1024**2):
    commands.append(arguments)
    reply = run(arguments, timeout=200, output_limit=maximum, env=environment)
    if reply.returncode:
        raise RuntimeError('Trusted baseline tool failed: ' + reply.stderr.decode(errors='replace')[-10000:])
    return reply


extra = {}
if request['compiler'] == 'clang20':
    execute(['/usr/bin/clang++-20', *request['flags'], 'family.cpp', '-o', 'private.elf'])
elif request['compiler'] == 'rust':
    execute(['/opt/rust/bin/rustc', *request['flags'], '--crate-name', 'buffer_baseline', 'program.rs', '-o', 'private.elf'])
else:
    for directory in ('tmp', 'go-cache', 'go-path', 'module-cache'): Path('/work', directory).mkdir()
    environment = {**os.environ, 'CGO_ENABLED': '0', 'GO111MODULE': 'off', 'GOTOOLCHAIN': 'local',
                   'GOAMD64': 'v1', 'GOMAXPROCS': '2', 'GOCACHE': '/work/go-cache',
                   'GOTMPDIR': '/work/tmp', 'GOPATH': '/work/go-path', 'GOMODCACHE': '/work/module-cache'}
    reply = execute(['/opt/go/bin/go', 'build', *request['flags'], '-o', 'private.elf', 'program.go', 'family.go'], environment)
    extra['compiler_assembly'] = reply.stderr.decode()
execute(['/tools/usr/bin/strip', '--strip-all', '-o', 'public.elf', 'private.elf'])
disassembly = execute(['/tools/usr/bin/objdump', '-d', '-w', 'private.elf'], maximum=48 * 1024**2).stdout.decode()
print(json.dumps({'private_hex': Path('/work/private.elf').read_bytes().hex(),
                  'public_hex': Path('/work/public.elf').read_bytes().hex(),
                  'commands': commands, 'disassembly': disassembly, **extra}, sort_keys=True))
