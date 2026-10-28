"""Build trusted originals using actual Rust-native and Go ABIInternal calls."""
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run

if os.environ.get('REA_NATIVE_ABI_WORKER') != '1':
    raise RuntimeError('Native ABI builds require a closed compiler image')
request = json.load(sys.stdin)
files = {'rust': {'interface.rs', 'library.rs', 'slot.rs', 'driver.rs'}, 'go': {'program.go'}}
if (set(request) != {'language', 'files', 'flags'} or request['language'] not in files
        or set(request['files']) != files[request['language']]
        or any(not isinstance(v, str) or len(v.encode()) > 262144 for v in request['files'].values())
        or not isinstance(request['flags'], list) or any(not isinstance(v, str) for v in request['flags'])):
    raise ValueError('Invalid native ABI build request')
resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
for name, text in request['files'].items():
    Path('/work', name).write_text(text)
commands = []


def execute(arguments, maximum=6 * 1024**2, environment=None):
    commands.append(arguments)
    result = run(arguments, timeout=200, output_limit=maximum, env=environment)
    if result.returncode:
        raise RuntimeError('Trusted native ABI tool failed: ' + result.stderr.decode(errors='replace')[-8000:])
    return result


extra = {}
if request['language'] == 'rust':
    compiler = '/opt/rust/bin/rustc'
    flags = request['flags']
    execute([compiler, *flags, '--crate-name', 'native_fixture', '--crate-type=rlib',
             '--emit=link,llvm-ir,obj', 'library.rs', '-o', 'libnative_fixture.rlib'])
    execute([compiler, *flags, '--crate-name', 'native_slot', '--crate-type=lib',
             '--emit=obj', 'slot.rs', '-o', 'native-slot.o'])
    execute([compiler, *flags, '--crate-name', 'native_driver', 'driver.rs',
             '--extern', 'native_fixture=/work/libnative_fixture.rlib',
             '-C', 'link-arg=/work/native-slot.o', '-o', 'private.elf'])
    extra = {'llvm_ir': Path('/work/libnative_fixture.ll').read_text(),
             'original_obj_hex': Path('/work/libnative_fixture.o').read_bytes().hex(),
             'default_slot_obj_hex': Path('/work/native-slot.o').read_bytes().hex()}
else:
    for name in ('tmp', 'go-cache', 'go-path', 'module-cache'):
        Path('/work', name).mkdir()
    environment = {**os.environ, 'CGO_ENABLED': '0', 'GO111MODULE': 'off', 'GOTOOLCHAIN': 'local',
                   'GOMAXPROCS': '2', 'GOAMD64': 'v1', 'GOCACHE': '/work/go-cache',
                   'GOTMPDIR': '/work/tmp', 'GOPATH': '/work/go-path', 'GOMODCACHE': '/work/module-cache'}
    compiled = execute(['/opt/go/bin/go', 'build', *request['flags'], '-o', 'private.elf', 'program.go'],
                       environment=environment)
    extra = {'compiler_assembly': compiled.stderr.decode()}
execute(['/tools/usr/bin/strip', '--strip-all', '-o', 'public.elf', 'private.elf'])
disassembly = execute(['/tools/usr/bin/objdump', '-d', '-w', 'private.elf'], maximum=32 * 1024**2).stdout.decode()
print(json.dumps({'private_hex': Path('/work/private.elf').read_bytes().hex(),
                  'public_hex': Path('/work/public.elf').read_bytes().hex(),
                  'commands': commands, 'disassembly': disassembly, **extra}, sort_keys=True))
