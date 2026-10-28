"""Offline compile/run smoke, never invokes a model or downloads dependencies."""
import os
import json
from pathlib import Path
import sqlite3
import ssl
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parents[2]
started = time.monotonic()
# Admission walks these roots even if a compiler-only smoke never imports them.
# Check inside the container before publishing the image, rather than finding a
# missing system package directory after a full harness inventory.
attestation = json.loads((root/'benchmarks/config/runtime-attestation.json').read_text())
required = sorted({name for component in attestation['components'].values()
                   for name in component['paths'] if Path(name).is_absolute()})
missing = [name for name in required if not Path(name).exists()]
if missing:
    raise RuntimeError('Missing attestation roots in runtime image: ' + ', '.join(missing))
print('all declared system attestation roots: present', flush=True)
with tempfile.TemporaryDirectory(prefix='tool-smoke-') as tmp:
    work = Path(tmp)
    env = dict(os.environ, GOPROXY='off', GOSUMDB='off', GOTOOLCHAIN='local',
               GOCACHE=str(work/'go-cache'), GO111MODULE='off', CARGO_NET_OFFLINE='true')
    (work/'main.cpp').write_text('#include <iostream>\n#include <vector>\nint main(){std::vector<int> x{2,3}; std::cout << x[0]+x[1];}\n')
    (work/'main.rs').write_text('fn main(){let x=vec![2,3]; print!("{}",x.iter().sum::<i32>());}\n')
    (work/'main.go').write_text('package main\nimport "fmt"\nfunc main(){x:=[]int{2,3};fmt.Print(x[0]+x[1])}\n')
    for name, command in [
        ('gcc',['g++','-std=c++14','main.cpp','-o','gcc-test']),
        ('clang',['clang++-20','-std=c++14','main.cpp','-o','clang-test']),
        ('rust',['rustc','main.rs','-o','rust-test']),
        ('go',['go','build','-o','go-test','main.go'])]:
        subprocess.run(command, cwd=work, env=env, check=True, timeout=90)
        assert subprocess.check_output([str(work/(name+'-test'))],env=env) == b'5', name
        print(name+': compile + execute passed', flush=True)
    subprocess.run(['java','-version'],check=True,timeout=20)
    subprocess.run(['node','-e','if (2+3!==5) process.exit(1)'],check=True,timeout=20)
    subprocess.run(['bwrap','--unshare-user','--ro-bind','/','/','--dev','/dev','--proc','/proc',
        '/usr/bin/python3','-c','open("/dev/null","w").write("ok")'],check=True,timeout=20)
    try:
        with open('/usr/bin/gcc','r+b'): pass
    except OSError: print('runtime mounts: read-only',flush=True)
    else: raise AssertionError('Compiler image is writable')
print('OFFLINE SMOKE PASSED; model calls=0; seconds=%.3f' % (time.monotonic()-started))
