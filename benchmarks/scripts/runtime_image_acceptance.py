"""Offline image admission: real runtime closure and launchers, never model prompts.

The image builder runs this inside its staged read-only mounts with --unshare-net.
It must finish before active.json can select a new image.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def required_paths(root=ROOT):
    policy = json.loads((root/'benchmarks/config/runtime-attestation.json').read_bytes())
    return sorted({str(Path(name) if Path(name).is_absolute() else root/name)
                   for item in policy['components'].values() for name in item['paths']})


def check_paths(paths):
    missing = [name for name in paths if not Path(name).exists()]
    if missing:
        raise RuntimeError('Missing declared runtime paths: ' + ', '.join(missing))


def check_session_compression(env):
    """Exercise the historical session codec before publishing a candidate image."""
    fixture = b'{"id":"offline-session","type":"session"}\n'
    encoded = subprocess.run(['/usr/bin/zstd', '-q', '-c'], input=fixture, env=env,
                             capture_output=True, check=True, timeout=30).stdout
    decoded = subprocess.run(['/usr/bin/zstd', '-d', '-c'], input=encoded, env=env,
                             capture_output=True, check=True, timeout=30).stdout
    if decoded != fixture:
        raise RuntimeError('Historical session zstd roundtrip changed bytes')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    sources = [Path(__file__), ROOT/'benchmarks/scripts/runtime_image_mcp_smoke.mjs',
               ROOT/'benchmarks/scripts/check_requests.py']
    source_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sources}
    paths = required_paths(); check_paths(paths)
    print('Runtime paths: all %d present' % len(paths), flush=True)
    results = {}
    with ExitStack() as guards, tempfile.TemporaryDirectory(prefix='runtime-acceptance-') as temporary:
        for method in ('connect', 'connect_ex'):
            guards.enter_context(patch.object(socket.socket, method,
                side_effect=RuntimeError('Network forbidden in runtime image acceptance')))
        home = Path(temporary)
        env = {key: os.environ[key] for key in ('PATH', 'JAVA_HOME') if key in os.environ}
        env.update(HOME=str(home), TMPDIR=str(home), XDG_CONFIG_HOME=str(home/'config'),
                   XDG_CACHE_HOME=str(home/'cache'), LANG='C.UTF-8', NO_COLOR='1',
                   DSH_HOME=str(home/'dsh'), ZCODE_DATA_BASE_DIR=str(home/'zcode'),
                   JAVA_TOOL_OPTIONS='-Duser.home='+str(home),
                   PYTHONPATH=str(ROOT/'benchmarks/scripts'),
                   GHIDRA_INSTALL_DIR=str(ROOT/'.tools/ghidra_12.1.4_PUBLIC'))
        tick = time.monotonic()
        check_session_compression(env)
        results['historical-session-zstd'] = {'passed': True, 'seconds': round(time.monotonic()-tick, 3)}
        print('historical-session-zstd: passed', flush=True)
        commands = {
            'shell-expr': ['/usr/bin/expr', '1', '+', '1'],
            'rea-cli': [str(ROOT/'bin/rea'), '--help'],
            'deepseek-cli': [str(ROOT/'.tools/node-v24.22.0-linux-x64/bin/node'),
                str(ROOT/'.tools/harness-0.2.1-rc.2/node_modules/@deepseek-ai/dsh/lib/bin.js'), '--help'],
            'zcode-cli': [str(ROOT/'.tools/node-v24.14.0-linux-x64/bin/node'),
                str(ROOT/'.tools/zcode-3.16.1/agent/zcode.cjs'), '--help'],
            'dsh-offline-request': ['/usr/bin/python3', '-m', 'unittest',
                'check_requests.TransportTests.test_actual_installed_dsh_premises_only_has_no_tools'],
            'zcode-offline-request': ['/usr/bin/python3', '-m', 'unittest',
                'check_requests.TransportTests.test_actual_installed_zcode_premises_only_has_no_tools'],
            'ghidra-analysis': [str(ROOT/'.tools/ghidra_12.1.4_PUBLIC/support/analyzeHeadless'),
                str(home), 'runtime-smoke', '-import', '/usr/bin/true',
                '-analysisTimeoutPerFile', '60', '-deleteProject'],
            'rea-mcp-analysis': [str(ROOT/'.tools/node-v24.22.0-linux-x64/bin/node'),
                str(ROOT/'benchmarks/scripts/runtime_image_mcp_smoke.mjs')],
        }
        for name, command in commands.items():
            tick = time.monotonic()
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
            if result.returncode:
                raise RuntimeError(name+' failed: '+result.stdout.decode(errors='replace')[-2000:])
            if name == 'shell-expr' and result.stdout.strip() != b'2':
                raise RuntimeError('expr returned incorrect output')
            if name == 'ghidra-analysis' and b'Analysis succeeded' not in result.stdout:
                raise RuntimeError('Ghidra did not confirm successful analysis')
            results[name] = {'passed': True, 'seconds': round(time.monotonic()-tick, 3)}
            print(name+': passed', flush=True)
        import runtime_attestation
        for harness in ('deepseek-harness', 'zcode'):
            tick = time.monotonic()
            runtime = runtime_attestation.capture(harness, fresh=True, memo=False,
                progress=lambda component, files: print(harness+': '+component+' (%d files)' % files, flush=True))
            results[harness+'-closure'] = {'passed': True, 'identity': runtime['identity'],
                'seconds': round(time.monotonic()-tick, 3)}
            print(harness+': full runtime capture passed', flush=True)
    if any(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest
           for name, digest in source_hashes.items()):
        raise RuntimeError('Runtime acceptance sources changed during execution')
    proof = {'schema_version': 1, 'passed': True, 'model_api_calls': 0,
             'source_sha256': source_hashes,
             'network': 'builder-unshare-net; Python socket connections denied',
             'declared_paths': len(paths), 'checks': results,
             'seconds': round(time.monotonic()-started, 3)}
    args.output.write_text(json.dumps(proof, indent=2)+'\n')
    print('OFFLINE FULL RUNTIME ACCEPTANCE PASSED; model calls=0', flush=True)


if __name__ == '__main__':
    main()
