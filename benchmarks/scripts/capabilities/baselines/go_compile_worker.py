"""Parse and compile one Go candidate in the sealed Go SDK, with no execution."""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run
from grading.c_executor.context import host

if os.environ.get('REA_GO_BUFFER_COMPILER') != '1':
    raise RuntimeError('Go candidate compilation requires the closed worker')
profile = json.loads(Path('/environment/profile.json').read_bytes())
request = json.load(sys.stdin)
if set(request) != {'source'} or not isinstance(request['source'], str):
    raise ValueError('Go compiler accepts candidate source only')
source = request['source'].encode()
if not 1 <= len(source) <= profile['max_source_bytes'] or b'\0' in source:
    raise ValueError('Go candidate source exceeds its contract')
resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
for name in ('tmp', 'go-cache', 'go-path', 'module-cache'): Path('/work', name).mkdir()
environment = {**os.environ, 'CGO_ENABLED': '0', 'GO111MODULE': 'off', 'GOTOOLCHAIN': 'local',
    'GOAMD64': 'v1', 'GOMAXPROCS': '2', 'GOCACHE': '/work/go-cache', 'GOTMPDIR': '/work/tmp',
    'GOPATH': '/work/go-path', 'GOMODCACHE': '/work/module-cache'}
commands, log = [], bytearray()


def execute(arguments, *, data=b''):
    commands.append(arguments)
    reply = run(arguments, input=data, timeout=180, output_limit=262144, env=environment)
    log.extend(reply.stdout + reply.stderr)
    return reply


helper = '/environment/benchmarks/scripts/capabilities/baselines/go_policy.go'
built = execute(['/opt/go/bin/go', 'build', '-trimpath', '-o', 'policy', helper])
if built.returncode:
    raise RuntimeError('Trusted Go AST policy failed to build: ' + built.stderr.decode(errors='replace')[-3000:])
admitted = execute(['/work/policy'], data=source)
if admitted.returncode:
    raise RuntimeError('Trusted Go AST policy failed to parse its input')
admission = json.loads(admitted.stdout)
status = admission['status']
if status == 'ok':
    Path('/work/solution.go').write_bytes(source)
    Path('/work/entry.go').write_text('''package main
import "unsafe"
var _ func(unsafe.Pointer, uintptr, unsafe.Pointer, uintptr, *uintptr) int32 = reconstructed
func main() {
    var input [8]byte
    var output [8]byte
    var length uintptr
    if reconstructed(unsafe.Pointer(&input[0]), 0, unsafe.Pointer(&output[0]), 8, &length) == 0x12345678 {
        for {}
    }
}
''')
    for name in ('solution.go', 'entry.go'): os.utime('/work/' + name, (0, 0))
    reply = execute(['/opt/go/bin/go', 'build', *profile['compiler_flags'], '-o', 'private.elf', 'entry.go', 'solution.go'])
    status = 'compiled' if reply.returncode == 0 else 'compile_error'
    if status == 'compiled':
        reply = execute(['/tools/usr/bin/strip', '--strip-all', '--keep-symbol=main.reconstructed',
                         '-o', 'candidate.elf', 'private.elf'])
        if reply.returncode:
            raise RuntimeError('Trusted Go candidate stripping failed')
Path('/work/compile.log').write_bytes(log)
output = Path('/work/candidate.elf')
print(json.dumps({'schema_version': 1, 'status': status, 'admission': admission,
    'source_sha256': hashlib.sha256(source).hexdigest(), 'commands': commands,
    'elf_sha256': hashlib.sha256(output.read_bytes()).hexdigest() if status == 'compiled' else None,
    'host': host()}, sort_keys=True))
