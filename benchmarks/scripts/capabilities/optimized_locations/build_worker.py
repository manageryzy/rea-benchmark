"""Compile real optimization examples and retain same-build diagnostic evidence."""
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run

if os.environ.get('REA_OPTIMIZED_LOCATION_WORKER') != '1':
    raise RuntimeError('Optimized-location builds require the closed compiler image')
request = json.load(sys.stdin)
if (set(request) != {'mode', 'files', 'compiler_flags', 'link_flags'}
        or request['mode'] not in ('optimized', 'lto')
        or set(request['files']) != {'mixer.hpp', 'mixer.cpp', 'program.cpp'}
        or any(not isinstance(v, str) or len(v.encode()) > 262144 for v in request['files'].values())):
    raise ValueError('Invalid optimized-location build request')
for flags in (request['compiler_flags'], request['link_flags']):
    if not isinstance(flags, list) or any(not isinstance(v, str) or not v.startswith('-') for v in flags):
        raise ValueError('Invalid trusted compiler policy')
resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3,) * 2)
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
for name, text in request['files'].items():
    Path('/work', name).write_text(text)
commands = []


def execute(arguments, maximum=4 * 1024**2):
    commands.append(arguments)
    result = run(arguments, timeout=120, output_limit=maximum)
    if result.returncode:
        raise RuntimeError('Trusted optimized-location tool failed: ' + result.stderr.decode(errors='replace')[-6000:])
    return result.stdout


for name in ('program', 'mixer'):
    execute(['/usr/bin/g++', *request['compiler_flags'], '-frandom-seed=rea-l6-' + name,
             '-c', name + '.cpp', '-o', name + '.o'])
execute(['/usr/bin/g++', *request['link_flags'], 'program.o', 'mixer.o', '-o', 'private.elf'])
execute(['/tools/usr/bin/strip', '--strip-all', '-o', 'public.elf', 'private.elf'])
disassembly = execute(['/tools/usr/bin/objdump', '-d', '-w', 'private.elf']).decode()
debug_info = execute(['/tools/usr/bin/objdump', '--dwarf=info', 'private.elf']).decode()
debug_ranges = execute(['/tools/usr/bin/objdump', '--dwarf=Ranges', 'private.elf']).decode()
debug_lines = execute(['/tools/usr/bin/objdump', '--dwarf=decodedline', 'private.elf']).decode()
print(json.dumps({'private_hex': Path('private.elf').read_bytes().hex(),
                  'public_hex': Path('public.elf').read_bytes().hex(), 'commands': commands,
                  'disassembly': disassembly, 'debug_info': debug_info,
                  'debug_ranges': debug_ranges, 'debug_lines': debug_lines}, sort_keys=True))
