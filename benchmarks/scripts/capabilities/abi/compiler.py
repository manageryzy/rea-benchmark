"""Common bounded compiler operations; original/probe entrypoints are separate."""
import hashlib
import json
import os
from pathlib import Path
import resource

from grading import process
from grading.c_executor.context import host

if os.environ.get('REA_ABI_PROBE_WORKER') != '1':
    raise RuntimeError('ABI compilation requires the closed compiler worker')
policy = json.loads(Path('/environment/profile.json').read_bytes())
limits = policy['limits']
resource.setrlimit(resource.RLIMIT_CPU, (limits['compile_cpu_seconds'],) * 2)
resource.setrlimit(resource.RLIMIT_AS, (limits['compile_memory_bytes'],) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
commands = []


def execute(command):
    commands.append(command)
    reply = process.run(command, timeout=limits['compile_wall_seconds'], output_limit=262144)
    if reply.returncode:
        raise RuntimeError('Trusted ABI probe compiler failed: ' + reply.stderr.decode(errors='replace')[-6000:])
    return reply


def compile_file(source, output, *, extra=()):
    execute(['/usr/bin/g++', *policy['compiler_flags'], '-I/work', *extra, '-c', source, '-o', output])


def link(objects):
    execute(['/usr/bin/g++', *policy['link_flags'], *objects,
        '-Wl,-T,/environment/benchmarks/scripts/capabilities/abi/link.ld', '-Wl,-e,original_reference', '-o', '/work/private.elf'])
    raw = Path('/work/private.elf').read_bytes()
    if not 1 <= len(raw) <= limits['elf_bytes']:
        raise ValueError('Linked ABI image exceeds its profile')
    return {'status': 'compiled', 'artifact_valid': True, 'private_hex': raw.hex(), 'commands': commands,
        'original_obj_sha256': hashlib.sha256(Path('/work/original.o').read_bytes()).hexdigest(),
        'helpers_obj_sha256': hashlib.sha256(Path('/work/helpers.o').read_bytes()).hexdigest(), 'host': host()}
