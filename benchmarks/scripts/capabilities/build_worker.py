"""Compile reviewed original family sources, strip a copy, collect disassembly.

Runs in the closed C++ image with a separate read-only pinned binutils addon.
Never receives a candidate, hidden input vector, model credential or provider URL.
"""
import json
import os
from pathlib import Path
import resource
import sys

sys.path.insert(0, '/environment/benchmarks/scripts')
from grading.process import run

if os.environ.get('REA_CAPABILITY_BUILD') != '1':
    raise RuntimeError('Capability target build requires the isolated worker')

request = json.load(sys.stdin)
if (set(request) != {'source', 'header', 'absent', 'pie'}
        or type(request['absent']) is not bool or type(request['pie']) is not bool
        or any(not isinstance(request[key], str) or len(request[key].encode()) > 262144 for key in ('source', 'header'))):
    raise ValueError('Invalid target build request')
resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
Path('family.cpp').write_text(request['source'])
Path('common.hpp').write_text(request['header'])
command = ['/usr/bin/g++', '-std=c++20', '-O2', '-g', '-gdwarf-4', '-Wall', '-Wextra',
           '-fno-omit-frame-pointer', '-fno-optimize-sibling-calls', '-fno-ipa-cp',
           '-ffile-prefix-map=/work=.', '-fdebug-prefix-map=/work=.', '-Wl,--build-id=none',
           *(['-fPIE', '-pie'] if request['pie'] else ['-fno-pie', '-no-pie']),
           *(['-DABSENT_TARGET=1'] if request['absent'] else []), 'family.cpp', '-o', 'private.elf']
result = run(command, timeout=110, output_limit=1048576)
if result.returncode:
    raise RuntimeError('Target build failed: ' + result.stderr.decode(errors='replace'))
debug = Path('private.elf').read_bytes()
stripped = run(['/tools/usr/bin/strip', '--strip-all', '-o', '/work/target', '/work/private.elf'],
               timeout=30, output_limit=1048576)
if stripped.returncode:
    raise RuntimeError('Target strip failed: ' + stripped.stderr.decode(errors='replace'))
disassembly = run(['/tools/usr/bin/objdump', '-d', '-w', '/work/private.elf'], timeout=30, output_limit=4194304)
if disassembly.returncode:
    raise RuntimeError('Target disassembly failed')
sys.stdout.write(json.dumps({'compiler_command': command, 'private_hex': debug.hex(),
    'public_hex': Path('target').read_bytes().hex(), 'disassembly': disassembly.stdout.decode('utf-8')}, sort_keys=True))
