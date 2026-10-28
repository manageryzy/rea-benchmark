"""Build the reviewed original endpoints; no submitted-description code is loaded."""
import json
from pathlib import Path
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from capabilities.abi.compiler import policy, compile_file, execute, link

request = json.load(sys.stdin)
if set(request) != {'mode', 'family', 'convention'} or request['mode'] != 'original':
    raise ValueError('Invalid original ABI build operation')
family, convention = request['family'], request['convention']
if family not in policy['families'] or convention not in policy['conventions']:
    raise ValueError('Unregistered original ABI build')
prefix = '/environment/benchmarks/suites/capabilities-v1/sources/abi/'
Path('/work/original.hpp').write_bytes(Path(prefix + family + '.hpp').read_bytes())
extra = ['-DORIGINAL_MS_ABI=' + str(int(convention == 'ms_x64'))]
compile_file(prefix + 'original.cpp', '/work/original.o', extra=extra)
compile_file(prefix + 'default_slot.cpp', '/work/slot.o', extra=extra)
compile_file(prefix + 'helpers.cpp', '/work/helpers.o')
result = link(['/work/original.o', '/work/helpers.o', '/work/slot.o'])
execute(['/tools/usr/bin/strip', '--strip-all', '-o', '/work/public.elf', '/work/private.elf'])
result.update(public_hex=Path('/work/public.elf').read_bytes().hex(),
    original_obj_hex=Path('/work/original.o').read_bytes().hex(), helpers_obj_hex=Path('/work/helpers.o').read_bytes().hex())
result['disassembly'] = execute(['/tools/usr/bin/objdump', '-d', '-w', '/work/private.elf']).stdout.decode()
print(json.dumps(result, sort_keys=True))
