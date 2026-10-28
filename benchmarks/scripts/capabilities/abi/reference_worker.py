"""Run only frozen original-reference code natively in the pinned C++ image."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from capabilities.abi.compiler import execute, host
from grading.process import run

request = json.load(sys.stdin)
if set(request) != {'original_obj_hex', 'helpers_obj_hex', 'width', 'nonces', 'compile_flags'}:
    raise ValueError('Invalid original-native calibration request')
if request['width'] not in (16, 48) or not 1 <= len(request['nonces']) <= 16:
    raise ValueError('Unbounded native calibration contract')
for name in ('original', 'helpers'):
    raw = bytes.fromhex(request[name + '_obj_hex'])
    if not 1 <= len(raw) <= 1048576:
        raise ValueError('Invalid frozen original object size')
    Path('/work/' + name + '.o').write_bytes(raw)

# The unresolved replacement is a trap stub. The original reference never calls
# it; no candidate description or generated replacement enters this executable.
source = r'''
#include <cstdint>
#include <cstdio>
extern "C" std::int64_t original_reference(std::uint64_t, std::uint8_t*);
extern "C" void recovered_slot() { __builtin_trap(); }
int main() {
    unsigned long long nonce;
    unsigned id = 0;
    while (std::scanf("%llx", &nonce) == 1) {
        std::uint8_t out[48] = {};
        auto status = original_reference(static_cast<std::uint64_t>(nonce), out);
        std::printf("%u %lld ", id++, static_cast<long long>(status));
        for (unsigned i = 0; i < WIDTH; ++i) std::printf("%02x", static_cast<unsigned>(out[i]));
        std::putchar('\n');
    }
    return 0;
}
'''.replace('WIDTH', str(request['width']))
Path('/work/native.cpp').write_text(source)
execute(['/usr/bin/g++', *request['compile_flags'], '/work/native.cpp', '/work/original.o', '/work/helpers.o', '-o', '/work/native-reference'])
reply = run(['/work/native-reference'], input=''.join(format(nonce, 'x') + '\n' for nonce in request['nonces']).encode(),
            timeout=8, output_limit=65536)
if reply.returncode:
    raise RuntimeError('Frozen original failed native calibration: ' + reply.stderr.decode(errors='replace')[-1500:])
rows = reply.stdout.split(b'\n')
if not rows or rows[-1] != b'' or len(rows) != len(request['nonces']) + 1:
    raise RuntimeError('Malformed original-native calibration output')
observations = []
for index, line in enumerate(rows[:-1]):
    parts = line.split(b' ')
    if len(parts) != 3 or parts[0] != str(index).encode() or parts[1] != b'0' or len(parts[2]) != 2 * request['width']:
        raise RuntimeError('Incorrect original-native call record')
    raw = bytes.fromhex(parts[2].decode('ascii'))
    observations.append({'nonce': request['nonces'][index], 'output_hex': raw.hex()})
print(json.dumps({'observations': observations, 'host': host(),
    'original_obj_sha256': hashlib.sha256(Path('/work/original.o').read_bytes()).hexdigest(),
    'helpers_obj_sha256': hashlib.sha256(Path('/work/helpers.o').read_bytes()).hexdigest()}, sort_keys=True))
