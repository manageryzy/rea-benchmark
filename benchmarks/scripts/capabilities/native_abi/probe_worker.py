"""Generate and compile a native ABI description in a closed C image."""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

if os.environ.get('REA_NATIVE_ABI_WORKER') != '1':
    raise RuntimeError('Native ABI probe requires the closed compiler image')
sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run
from grading.c_executor.context import host
from capabilities.native_abi import artifacts, codegen

profile = json.loads(Path('/environment/profile.json').read_bytes())
request = json.load(sys.stdin)
if set(request) != {'artifact_hex', 'binary_sha256', 'callee_address'}:
    raise ValueError('Invalid native ABI probe request')
try:
    value = artifacts.admit(bytes.fromhex(request['artifact_hex']))
except ValueError as error:
    print(json.dumps({'status': 'invalid_artifact', 'reason': str(error), 'host': host()}))
    raise SystemExit(0)
if value['binary_sha256'] != request['binary_sha256']:
    print(json.dumps({'status': 'wrong_binary', 'host': host()}))
    raise SystemExit(0)
assembly, helper = codegen.generate(value, request['callee_address'])
source = helper + '\n__asm__(' + json.dumps(assembly) + ');\n'
if len(source.encode()) > profile['max_source_bytes']:
    raise ValueError('Generated native ABI source exceeds its bound')
Path('/work/probe.c').write_text(source)
resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
prefix = '/environment/benchmarks/scripts/grading/c_executor/'
command = ['/usr/bin/gcc', *profile['compiler_flags'], '/work/probe.c', prefix + 'helpers.c',
           '-Wl,-T,' + prefix + 'link.ld', '-Wl,--build-id=none', '-Wl,--no-undefined',
           '-Wl,-e,__rea_native_replacement', '-o', '/work/probe.elf']
result = run(command, timeout=120, output_limit=262144, env=dict(os.environ))
compiled = result.returncode == 0 and Path('/work/probe.elf').is_file()
raw = Path('/work/probe.elf').read_bytes() if compiled else b''
print(json.dumps({'status': 'compiled' if compiled else 'compile_error', 'elf_hex': raw.hex(),
    'elf_sha256': hashlib.sha256(raw).hexdigest() if compiled else None,
    'generated_source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'command': command,
    'log': (result.stdout + result.stderr).decode(errors='replace'), 'host': host()}, sort_keys=True))
