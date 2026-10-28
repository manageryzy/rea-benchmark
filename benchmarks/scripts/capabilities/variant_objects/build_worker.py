"""Compile original endpoints without loading submitted descriptions or generators."""
import json
from pathlib import Path
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from capabilities.abi.compiler import compile_file, execute, link

request = json.load(sys.stdin)
if request != {'mode': 'original'}:
    raise ValueError('Invalid original variant-object compiler request')
prefix = '/environment/benchmarks/suites/capabilities-v1/sources/variants/'
Path('/work/record.hpp').write_bytes(Path(prefix + 'record.hpp').read_bytes())
Path('/work/fixtures.hpp').write_bytes(Path(prefix + 'fixtures.hpp').read_bytes())
compile_file(prefix + 'original.cpp', '/work/original.o')
Path('/work/default.cpp').write_text('''#include <cstdint>
extern "C" std::int64_t original_callee(void*, std::uint64_t);
extern "C" __attribute__((noinline,noipa)) std::int64_t recovered_slot(void* value, std::uint64_t nonce) {
    return original_callee(value, nonce);
}
''')
compile_file('/work/default.cpp', '/work/slot.o')
compile_file('/environment/benchmarks/suites/capabilities-v1/sources/abi/helpers.cpp', '/work/helpers.o')
result = link(['/work/original.o', '/work/helpers.o', '/work/slot.o'])
execute(['/tools/usr/bin/strip', '--strip-all', '-o', '/work/public.elf', '/work/private.elf'])
result.update(public_hex=Path('/work/public.elf').read_bytes().hex(),
              original_obj_hex=Path('/work/original.o').read_bytes().hex(),
              helpers_obj_hex=Path('/work/helpers.o').read_bytes().hex())
print(json.dumps(result, sort_keys=True))
