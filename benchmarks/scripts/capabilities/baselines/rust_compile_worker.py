"""Compile a restricted Rust module and trusted helpers in the sealed Rust SDK."""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run
from grading.c_executor.context import host

if os.environ.get('REA_RUST_BUFFER_WORKER') != '1':
    raise RuntimeError('Rust candidate compilation requires the closed worker')
profile = json.loads(Path('/environment/profile.json').read_bytes())
request = json.load(sys.stdin)
if set(request) != {'source'} or not isinstance(request['source'], str):
    raise ValueError('Rust compiler accepts candidate source only')
source = request['source'].encode()
if not 1 <= len(source) <= profile['max_source_bytes'] or b'\0' in source:
    raise ValueError('Rust candidate source exceeds its contract')
Path('/work/solution.rs').write_bytes(source)
Path('/work/entry.rs').write_text('''#![no_std]
#[panic_handler]
fn panic_handler(_: &core::panic::PanicInfo<'_>) -> ! {
    loop { core::hint::spin_loop(); }
}
include!("solution.rs");
const _: unsafe extern "C" fn(*const u8, usize, *mut u8, usize, *mut usize) -> i32 = reconstructed;
''')
for name in ('solution.rs', 'entry.rs'): os.utime('/work/' + name, (0, 0))
resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
commands, log = [], bytearray()


def execute(arguments):
    commands.append(arguments)
    reply = run(arguments, timeout=120, output_limit=262144)
    log.extend(reply.stdout + reply.stderr)
    return reply.returncode == 0


compiled = execute(['/opt/rust/bin/rustc', *profile['rust_flags'], '--crate-name', 'buffer_candidate',
                    'entry.rs', '-o', 'candidate.a'])
prefix = '/environment/benchmarks/scripts/grading/c_executor/'
if compiled:
    compiled = execute(['/usr/bin/gcc', *profile['c_link_flags'], prefix + 'helpers.c', '/work/candidate.a',
        '-Wl,-T,' + prefix + 'link.ld', '-Wl,--build-id=none', '-Wl,--no-undefined', '-Wl,--gc-sections',
        '-Wl,--undefined=reconstructed', '-Wl,-e,reconstructed', '-o', '/work/private.elf'])
if compiled:
    compiled = execute(['/tools/usr/bin/strip', '--strip-all', '--keep-symbol=reconstructed',
                        '-o', '/work/candidate.elf', '/work/private.elf'])
output = Path('/work/candidate.elf')
Path('/work/compile.log').write_bytes(log)
print(json.dumps({'schema_version': 1, 'status': 'compiled' if compiled else 'compile_error',
                  'source_sha256': hashlib.sha256(source).hexdigest(), 'commands': commands,
                  'elf_sha256': hashlib.sha256(output.read_bytes()).hexdigest() if compiled else None,
                  'host': host()}, sort_keys=True))
