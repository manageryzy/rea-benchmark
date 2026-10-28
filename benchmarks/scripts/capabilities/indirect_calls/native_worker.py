"""Independent native calibration: no candidate generator or artifact is loaded."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from capabilities.abi.compiler import execute, host
from grading.process import run

request = json.load(sys.stdin)
if set(request) != {'mode', 'original_obj_hex', 'helpers_obj_hex', 'original_obj_sha256',
                    'helpers_obj_sha256', 'nonces'} or request['mode'] != 'native':
    raise ValueError('Invalid native indirect-call-reference request')
if (not isinstance(request['nonces'], list) or not 1 <= len(request['nonces']) <= 32
        or any(type(n) is not int or not 0 <= n < 2**64 for n in request['nonces'])):
    raise ValueError('Invalid native indirect-call-reference vectors')
for name in ('original', 'helpers'):
    raw = bytes.fromhex(request[name + '_obj_hex'])
    if not 1 <= len(raw) <= 1048576 or hashlib.sha256(raw).hexdigest() != request[name + '_obj_sha256']:
        raise ValueError('Frozen native indirect-call-reference object mismatch')
    Path('/work/' + name + '.o').write_bytes(raw)
source = r'''
#include <cstdint>
#include <cstdio>
extern "C" std::int64_t original_reference(std::uint64_t, std::uint8_t*);
extern "C" std::int64_t recovered_slot(void*, std::uint64_t) { __builtin_trap(); }
int main() {
    unsigned long long nonce; unsigned id = 0;
    while (std::scanf("%llx", &nonce) == 1) {
        std::uint8_t output[64] = {};
        auto status = original_reference(nonce, output);
        std::printf("%u %lld ", id++, static_cast<long long>(status));
        for (unsigned i=0; i<64; ++i) std::printf("%02x", static_cast<unsigned>(output[i]));
        std::putchar('\n');
    }
}
'''
Path('/work/native.cpp').write_text(source)
execute(['/usr/bin/g++', '-std=c++20', '-O2', '-no-pie', '-Wl,--build-id=none',
         '/work/native.cpp', '/work/original.o', '/work/helpers.o', '-o', '/work/native-reference'])
reply = run(['/work/native-reference'], input=''.join(format(n, 'x') + '\n' for n in request['nonces']).encode(),
            timeout=8, output_limit=65536)
rows = reply.stdout.split(b'\n')
if reply.returncode or rows[-1:] != [b''] or len(rows) != len(request['nonces']) + 1:
    raise RuntimeError('Native original indirect-call-reference failed or returned malformed records')
observations = []
for index, line in enumerate(rows[:-1]):
    parts = line.split(b' ')
    if len(parts) != 3 or parts[0] != str(index).encode() or parts[1] != b'0' or len(parts[2]) != 128:
        raise RuntimeError('Incorrect native indirect-call-reference record')
    observations.append({'nonce': request['nonces'][index], 'output_hex': bytes.fromhex(parts[2].decode('ascii')).hex()})
print(json.dumps({'observations': observations, 'host': host(),
    **{name + '_obj_sha256': hashlib.sha256(Path('/work/' + name + '.o').read_bytes()).hexdigest()
       for name in ('original', 'helpers')}}, sort_keys=True))
