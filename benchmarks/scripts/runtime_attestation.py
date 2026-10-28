"""Local content attestation and frozen provider routes, without provider traffic.

This is a verifiable local receipt, not a remote signature or a claim about model
weights. Reviewed logical components (including runtime dependencies) are hashed;
credential values, local account identities and credential-file hashes never are.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import stat
import subprocess
import sys
import sysconfig
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import action_cache
import request_contract

ROOT = Path(__file__).resolve().parents[2]
CONFIG = 'benchmarks/config/runtime-attestation.json'
CONTROL = [CONFIG, 'benchmarks/scripts/runtime_attestation.py', 'benchmarks/harnesses/provider-auth.mjs',
           'benchmarks/scripts/runtime_snapshot.py',
           'benchmarks/scripts/capabilities/analysis_context.py',
           'benchmarks/scripts/runtime_storage.py', 'benchmarks/config/runtime-storage.json',
           'benchmarks/scripts/runtime_image.py', 'benchmarks/config/runtime-image.json']
RECEIPT = ROOT / 'benchmarks/config/runtime-attestation-validation.json'
PRIVATE = ROOT / '.cache/benchmark-runtime/attestation'
LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
INLINE_COMPONENT_BYTES = 16384


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def component_entries(component, read_blob):
    """Resolve a committed component map without probing today's installation."""
    if 'entries_digest' in component:
        if 'entries' in component:
            raise ValueError('Ambiguous runtime component representation')
        digest = component['entries_digest']
        action_cache.valid_digest(digest)
        raw = read_blob(digest)
        if len(raw) != digest['size_bytes'] or sha(raw) != digest['hash']:
            raise ValueError('Runtime component CAS content changed')
        entries = json.loads(raw)
    else:
        entries = component['entries']
        raw = action_cache.canonical(entries)
    if (not isinstance(entries, dict) or sha(raw) != component['sha256']
            or component['files'] != sum('sha256' in value for value in entries.values())
            or component['bytes'] != sum(value.get('size_bytes', 0) for value in entries.values())):
        raise ValueError('Runtime component identity or accounting changed')
    return entries


def compact_runtime(runtime, *, root=ROOT):
    """Store large file maps once in CAS; keep their hashes in the runtime root."""
    if runtime['identity'] != sha(action_cache.canonical(runtime['manifest'])):
        raise ValueError('Cannot compact an unverified runtime manifest')
    components, changed = {}, False
    local = None
    for name, component in runtime['manifest']['components'].items():
        value = dict(component)
        if 'entries' in value:
            raw = action_cache.canonical(value['entries'])
            if len(raw) > INLINE_COMPONENT_BYTES:
                if sha(raw) != value['sha256']:
                    raise ValueError('Runtime component changed before CAS publication')
                local = local or action_cache.Cache(Path(root) / '.cache/benchmark-actions/v3')
                value['entries_digest'] = local.put(raw)
                del value['entries']
                changed = True
        components[name] = value
    if not changed:
        return runtime
    manifest = {**runtime['manifest'], 'schema_version': 2, 'components': components}
    return {'identity': sha(action_cache.canonical(manifest)), 'manifest': manifest}


def component_blobs(attestation, read_blob):
    """Yield declared runtime inputs so inference actions retain the CAS closure."""
    import preparation_session
    owner = getattr(read_blob, '__self__', None)
    if isinstance(owner, action_cache.Cache):
        yield from preparation_session.once('runtime-component-blobs',
            [str(owner.root.absolute()), attestation['runtime']],
            lambda: list(_component_blobs(attestation, read_blob)))
    else:
        # Custom readers retain their validation/mutation semantics.
        yield from _component_blobs(attestation, read_blob)


def _component_blobs(attestation, read_blob):
    for name, component in attestation['runtime']['manifest'].get('components', {}).items():
        if 'entries_digest' not in component:
            continue
        raw = read_blob(component['entries_digest'])
        component_entries(component, lambda _digest: raw)
        yield name, raw


def config(root=ROOT):
    result = json.loads((Path(root) / CONFIG).read_bytes())
    if result['schema_version'] != 1:
        raise ValueError('Unsupported runtime attestation policy')
    return result


class Inventory:
    """Stat metadata only accelerates hashing; keys contain content, never mtimes.

    Every scan enumerates directory entries anew. Strict launch verification reads
    every file again, bypassing the memo. Symlinks are resolved through reviewed
    roots and their target identity and contents are both included.
    """
    def __init__(self, root=ROOT, policy=None, memo=None, fresh=False):
        self.root = Path(root).resolve()
        self.policy = policy or config(root)
        self.fresh = fresh
        self.memo = None
        self.lock = threading.Lock()
        self.pending = {}
        if memo:
            Path(memo).parent.mkdir(parents=True, exist_ok=True)
            self.memo = sqlite3.connect(memo, timeout=120, check_same_thread=False)
            self.memo.execute('CREATE TABLE IF NOT EXISTS hashes (path TEXT PRIMARY KEY, signature TEXT, digest TEXT)')
        self.allowed = [self.path(p) for spec in self.policy['components'].values() for p in spec['paths']]
        self.allowed += [self.root / p for p in CONTROL]
        self.allowed += [Path(p).resolve() for p in self.policy['approved_system_roots']]
        self.native = set()
        self.physical = {}

    def close(self):
        if self.memo:
            # Do not hold a SQLite write transaction during filesystem work.
            # Different model queues can inventory the same installation.
            self.memo.executemany('INSERT OR REPLACE INTO hashes VALUES (?, ?, ?)', self.pending.values())
            self.memo.commit()
            self.memo.close()

    def logical(self, path):
        path = Path(os.path.abspath(path))
        if path.is_relative_to(self.root):
            return 'workspace/' + str(path.relative_to(self.root))
        return 'system/' + str(path).lstrip('/')

    def resolved(self, path):
        target = path.resolve(strict=False)
        if not any(target.is_relative_to(p) for p in self.allowed):
            raise ValueError('Runtime symlink escapes approved component roots')
        return target

    def path(self, name):
        return Path(name) if Path(name).is_absolute() else self.root / name

    def file(self, path, before=None):
        before = before or path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Runtime file changed type during inventory')
        signature = json.dumps([before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino, before.st_dev])
        key = str(path)
        prior = self.physical.get((key, signature))
        if prior:
            return prior
        with self.lock:
            cached = None if self.fresh or not self.memo else self.memo.execute('SELECT signature, digest FROM hashes WHERE path=?', (key,)).fetchone()
        header = None
        if cached and cached[0] == signature:
            digest = cached[1]
        else:
            h = hashlib.sha256()
            with path.open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    if header is None:
                        header = block[:20]
                    h.update(block)
            digest = h.hexdigest()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino, before.st_dev) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_dev):
                raise ValueError('Runtime file changed during inventory')
            if self.memo:
                with self.lock:
                    self.pending[key] = (key, signature, digest)
        value = {'sha256': digest, 'size_bytes': before.st_size, 'executable': bool(before.st_mode & 0o111)}
        self.physical[key, signature] = value
        if '.so' in path.name or path.suffix == '.node' or (value['executable'] and (path.suffix in ('', '.bin') or re.fullmatch(r'python\d+(?:\.\d+)*', path.name))):
            if header is None:
                with path.open('rb') as stream:
                    header = stream.read(20)
            # Other architectures/platforms remain hashed distribution inputs,
            # but their host-incompatible binaries are not loader dependencies.
            if header[:6] == b'\x7fELF\x02\x01' and header[18:20] == b'\x3e\x00':
                self.native.add(path)
        return value

    def tree(self, paths, exclude=()):
        entries, traversed = {}, set()
        def visit(path, regular=False):
            if path.name in exclude:
                return
            label = self.logical(path)
            if regular:
                entries[label] = workers.submit(self.file, path)
                return
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = self.resolved(path)
                link = os.readlink(path)
                entries[label] = {'symlink': self.logical(target), 'link_target': self.logical(Path(link)) if Path(link).is_absolute() else link}
                if target.exists():
                    visit(target)
                else:
                    entries[label]['target_missing'] = True
            elif stat.S_ISDIR(info.st_mode):
                if path in traversed:
                    return
                traversed.add(path)
                entries[label] = {'directory': True, 'executable': bool(info.st_mode & 0o111)}
                with os.scandir(path) as children:
                    children = sorted(children, key=lambda child: child.name)
                for child in children:
                    # DirEntry type information avoids a serialized getattr for
                    # every regular file on WSL's Windows-backed filesystem.
                    regular = not child.is_symlink() and child.is_file(follow_symlinks=False)
                    visit(path / child.name, regular=regular)
            elif stat.S_ISREG(info.st_mode):
                entries[label] = workers.submit(self.file, path, info)
            else:
                raise ValueError('Unsupported runtime file type')
        with ThreadPoolExecutor(max_workers=16) as workers:
            try:
                for name in paths:
                    visit(self.path(name))
                for name, value in entries.items():
                    if isinstance(value, Future):
                        entries[name] = value.result()
            except BaseException:
                workers.shutdown(wait=True, cancel_futures=True)
                raise
        raw = action_cache.canonical(entries)
        return {'sha256': sha(raw), 'files': sum('sha256' in v for v in entries.values()),
                'bytes': sum(v.get('size_bytes', 0) for v in entries.values()), 'entries': entries}

    def shared_libraries(self):
        """ldd is used only on the trusted installed runtime, never target ELFs."""
        def dependencies(native):
            if any(re.search(r'(^|[-_.])(openbsd|freebsd|netbsd|dragonfly|android|illumos|sunos|darwin|win32)($|[-_.])', part.lower()) for part in native.parts):
                return set()
            # Packages ship both glibc and musl builds. The pinned Linux Node/JVM
            # setup uses glibc; foreign variants remain hashed distribution files.
            if any(re.search(r'(^|[-_.])(?:linux)?musl($|[-_.])', part) for part in native.parts):
                return set()
            environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
            java = self.policy['runtime_environment'].get('JAVA_HOME')
            if java and native.is_relative_to(Path(java)):
                # JDK libraries resolve libjvm.so from the VM already loaded by
                # the Java launcher. A standalone ldd lacks that process context.
                environment['LD_LIBRARY_PATH'] = ':'.join(str(Path(java) / name) for name in ('lib/server', 'lib', 'lib/jli'))
            probe = subprocess.run(['/usr/bin/ldd', str(native)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=environment, timeout=30)
            output = probe.stdout.decode(errors='replace')
            if 'not found' in output:
                raise ValueError('Installed native runtime has an unresolved shared library')
            if probe.returncode and 'not a dynamic executable' not in output + probe.stderr.decode(errors='replace'):
                raise ValueError('Installed native runtime loader probe failed')
            paths = set()
            for line in output.splitlines():
                # Only resolved loader records, never diagnostic "file: error"
                # lines. Paths may contain spaces; addresses are not identity.
                match = re.search(r'(?:=>\s+|^\s*)(/.*?)\s+\(0x[0-9a-fA-F]+\)', line)
                if match:
                    path = Path(match.group(1)).resolve(strict=True)
                    if not any(path.is_relative_to(root) for root in self.allowed):
                        raise ValueError('Shared library outside approved runtime roots')
                    paths.add(path)
            return paths
        paths = set()
        # Loader probes are independent. Keep their complete resolved union and
        # fail on every probe error, while overlapping bounded process startup.
        with ThreadPoolExecutor(max_workers=8) as workers:
            for dependencies_for_native in workers.map(dependencies, sorted(self.native)):
                paths.update(dependencies_for_native)
        return self.tree(sorted(paths))


def capture(harness, *, root=ROOT, policy=None, fresh=False, memo=True, native=True, progress=None):
    root = Path(root)
    policy = policy or config(root)
    if root.resolve() == ROOT.resolve():
        if sha(Path(__file__).read_bytes()) != LOADED_SOURCE_SHA256:
            raise ValueError('Attestor source changed in this process; restart the coordinator')
        python_paths = policy['components']['coordinator-python']['paths']
        if sysconfig.get_path('stdlib') not in python_paths:
            raise ValueError('Actual Python standard library differs from the reviewed runtime policy')
        approved = [Path(p).resolve() for p in python_paths]
        approved += [Path(p).resolve() for p in policy['approved_system_roots'] if p.startswith('/etc/python')]
        for module in tuple(sys.modules.values()):
            file = getattr(module, '__file__', None)
            if file:
                path = Path(file).resolve()
                if not path.is_relative_to(ROOT) and not any(path.is_relative_to(p) for p in approved):
                    raise ValueError('Loaded Python dependency is outside reviewed runtime components')
    inventory = Inventory(root, policy, root / '.cache/benchmark-runtime/attestation/hashes.sqlite' if memo else None, fresh)
    try:
        components = {}
        for name, spec in policy['components'].items():
            if harness in spec['harnesses']:
                components[name] = inventory.tree(spec['paths'], spec.get('exclude', ()))
                if progress:
                    progress(name, components[name]['files'])
        components['attestation-control'] = inventory.tree(CONTROL)
        if native:
            components['native-loader-closure'] = inventory.shared_libraries()
    finally:
        inventory.close()
    if root.resolve() == ROOT.resolve() and sha(Path(__file__).read_bytes()) != LOADED_SOURCE_SHA256:
        raise ValueError('Attestor source changed during inventory; restart the coordinator')
    manifest = {'schema_version': 1, 'policy': policy['policy'], 'harness': harness,
                'components': components, 'environment': policy['runtime_environment'], 'network': policy['network'],
                'platform': {'system': platform.system(), 'machine': platform.machine(), 'kernel_release': platform.release(),
                             'libc': list(platform.libc_ver())},
                'scope': 'local-installed-content; remote-service-and-model-weights-unattested'}
    return compact_runtime({'identity': sha(action_cache.canonical(manifest)), 'manifest': manifest}, root=root)


def current_runtime(harness, *, fresh=False):
    """The configured startup-once policy trusts this process's admitted runtime."""
    import runtime_snapshot
    policy = config()
    if policy.get('verification_mode', 'strict_per_task') == 'startup_once':
        return runtime_snapshot.current(harness, root=ROOT, policy=policy, capture=capture)
    return capture(harness, fresh=fresh)


def provider(model):
    # Only the allowlisted public route is printed by this helper. No secret is
    # present in argv/stdout or any action input, even if credential lookup fails.
    child = subprocess.run([str(ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'),
                            str(ROOT / 'benchmarks/harnesses/provider-auth.mjs'), '--resolve', json.dumps(model)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                           env={k: v for k, v in os.environ.items() if k not in ('NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH')})
    if child.returncode:
        raise ValueError('Provider resolution failed; check credentials and endpoint configuration')
    return json.loads(child.stdout)


def prepare(model, configuration=None):
    if Path(sys.executable).resolve() != Path('/usr/bin/python3').resolve():
        raise ValueError('Attested execution requires the configured /usr/bin/python3 interpreter')
    route = provider(model)
    if configuration is not None:
        configuration = copy.deepcopy(configuration)
        configuration['harness']['request_contract'] = request_contract.build(
            route, model, configuration['gateway'], configuration['harness'])
    harness = model['harness']
    runtime = current_runtime(harness)
    result = {'schema_version': 1, 'runtime': runtime, 'provider': route}
    if configuration is not None:
        result['configuration'] = configuration
    result['identity'] = sha(action_cache.canonical(result))
    return result


def validate(attestation, *, read_blob=None):
    value = copy.deepcopy(attestation)
    if not isinstance(value, dict) or not all(key in value for key in ('identity', 'runtime', 'provider', 'schema_version')):
        raise ValueError('Task lacks complete runtime/provider attestation')
    identity = value.pop('identity')
    if identity != sha(action_cache.canonical(value)):
        raise ValueError('Runtime/provider attestation identity mismatch')
    runtime = attestation['runtime']
    if runtime['identity'] != sha(action_cache.canonical(runtime['manifest'])):
        raise ValueError('Runtime manifest identity mismatch')
    if runtime['manifest'].get('schema_version') == 2:
        for component in runtime['manifest']['components'].values():
            if 'entries_digest' in component:
                if 'entries' in component:
                    raise ValueError('Ambiguous runtime component representation')
                action_cache.valid_digest(component['entries_digest'])
                if component['entries_digest']['hash'] != component['sha256']:
                    raise ValueError('Runtime component reference differs from its identity')
            if read_blob is not None:
                component_entries(component, read_blob)
    return attestation


def verify(task, *, fresh=True):
    attestation = validate(task.get('runtime_attestation') or {})
    verify_configuration(task)
    expected = attestation['runtime']
    current = current_runtime(task['harness'], fresh=fresh)
    if current != expected:
        raise ValueError('Runtime content changed after preparation; provider launch/grade refused')
    route = provider({'id': task['model'], 'harness': task['harness']})
    if route != attestation['provider']:
        raise ValueError('Provider route changed after preparation; no fallback permitted')
    import runtime_snapshot
    return {'schema_version': 1, 'identity': attestation['identity'], 'runtime_identity': expected['identity'],
            'provider': route, 'status': 'verified_local_content_and_route',
            'verification_method': 'startup_once_trusted_environment' if runtime_snapshot.active(task['harness']) else 'runtime_capture',
            'runtime_identity_scope': expected['manifest']['scope'],
            'remote_model_identity': 'unattested'}


def verify_configuration(task):
    attestation = task['runtime_attestation']
    if attestation != task.get('inference_identity', {}).get('attestation'):
        raise ValueError('Runtime attestation differs from frozen inference inputs')
    configuration = attestation.get('configuration')
    if not configuration or set(configuration) != {'gateway', 'harness'}:
        raise ValueError('Task lacks frozen effective gateway/harness settings')
    directory = Path(task['input_directory'])
    for name, digest_name in [('prompt.txt', 'prompt'), ('target', 'binary'), ('tools.json', 'tools'), ('manifest.json', 'manifest')]:
        path = directory / name
        if path.is_symlink() or sha(path.read_bytes()) != task['inference_identity'][digest_name]:
            raise ValueError('Public task inputs changed after preparation')
    from capabilities import analysis_context
    analysis_context.verify_materialized(task, configuration)
    for key, name in [('gateway', 'gateway.json'), ('harness', 'harness-config.json')]:
        expected = copy.deepcopy(configuration[key])
        expected['analysis_tools_file'] = str(directory / 'tools.json')
        if key == 'gateway':
            expected['runtime_environment'] = environment(attestation, input_directory=directory)
        path = directory / name
        if path.is_symlink() or json.loads(path.read_bytes()) != expected:
            raise ValueError('Effective gateway/harness settings changed after preparation')


def environment(attestation, *, input_directory=None):
    validate(attestation)
    result = {key: value.replace('@workspace', str(ROOT)) for key, value in attestation['runtime']['manifest']['environment'].items()}
    context = attestation.get('configuration', {}).get('gateway', {}).get('analysis_context')
    if context is not None:
        if input_directory is None:
            raise ValueError('Declared seeds require the bound task input directory')
        from capabilities import analysis_context
        result.update(analysis_context.environment(context, input_directory))
    return result


def glm_credentials(attestation):
    # Retrieve from the frozen source only; never fall back after hashing.
    route = attestation['provider']
    if provider({'id': route['model'], 'harness': 'zcode'}) != route:
        raise ValueError('GLM provider selection changed after preparation')
    if route['credential_source'] == 'glm-environment':
        token = os.environ['GLM_AUTH_TOKEN']
    elif route['credential_source'] == 'zcode-prepaid':
        saved = json.loads((ROOT / '.zcode-state/.zcode/v2/provider_config.json').read_text())
        token = next(x['config']['access']['apiKey'] for x in saved['config']['providerConfigRules']['providerRules'] if x['providerId'] == 'zai-prepaid')
    else:
        raise ValueError('Unreviewed GLM credential source')
    return route['base_url'], token


def write_receipt(inventories):
    names = set(CONTROL)
    for spec in config()['components'].values():
        names.update(p for p in spec['paths'] if p.startswith(('benchmarks/', 'bin/')) or p == 'env.sh')
    sources = {name: sha((ROOT / name).read_bytes()) for name in sorted(names)}
    for runtime in inventories.values():
        for component in runtime['manifest']['components'].values():
            entries = component_entries(component, action_cache.Cache().get)
            for name, expected in sources.items():
                entry = entries.get('workspace/' + name)
                if entry and entry.get('sha256') != expected:
                    raise ValueError('Attestation sources changed during inventory; rerun the offline inventory')
    value = {'schema_version': 1, 'status': 'local_inventory_only', 'model_api_calls': 0,
             'historical_runtime_identity': 'unknown', 'files_sha256': sources,
             'runtimes': {name: {'identity': item['identity'], 'components': {
                 key: {k: component[k] for k in ('sha256', 'files', 'bytes')} for key, component in item['manifest']['components'].items()}}
                          for name, item in inventories.items()}}
    RECEIPT.write_text(json.dumps(value, indent=2) + '\n')
    PRIVATE.mkdir(parents=True, exist_ok=True)
    for item in inventories.values():
        action_cache.Cache._write(PRIVATE / (item['identity'] + '.json'), action_cache.canonical(item))
    return value


def for_report(read_bytes):
    """Validate a saved receipt without loading any runtime or credential file."""
    value = json.loads(read_bytes(RECEIPT)) if RECEIPT.is_file() else {'status': 'not_recorded', 'runtimes': {}, 'model_api_calls': 0}
    value['inputs_current'] = bool(value.get('files_sha256')) and all(sha(read_bytes(ROOT / name)) == expected for name, expected in value['files_sha256'].items())
    test_path = ROOT / 'benchmarks/config/runtime-attestation-tests.json'
    controls = json.loads(read_bytes(test_path)) if test_path.is_file() else {}
    value['controls_passed'] = bool(controls.get('passed'))
    value['controls_current'] = bool(controls.get('files_sha256')) and all(sha(read_bytes(ROOT / name)) == expected for name, expected in controls['files_sha256'].items())
    value['checks'] = controls.get('checks', 0)
    # Current code validates the receipt, not today's installation. Reports must
    # remain reproducible on machines where these runtimes are absent.
    return value


def observation(directory, read_bytes, started=False):
    """Read historical evidence only; never infer past facts from this machine."""
    directory = Path(directory)
    path = directory / 'task.json'
    if not path.is_file():
        return 'historical_unknown' if started else 'not_recorded'
    task = json.loads(read_bytes(path))
    if not task.get('runtime_attestation'):
        return 'historical_unknown'
    try:
        def read_component(digest):
            action_cache.valid_digest(digest)
            return read_bytes(ROOT / '.cache/benchmark-actions/v3/cas' / digest['hash'])
        attested = validate(task['runtime_attestation'], read_blob=read_component)
        if task['inference_identity']['attestation'] != attested:
            return 'unverified'
        complete = json.loads(read_bytes(directory / 'completed.json'))
        if complete['fingerprint'] != task['fingerprint']:
            return 'unverified'
        for name in ('task.json', 'result.json', 'runtime-verification-before.json', 'runtime-verification-after.json'):
            if sha(read_bytes(directory / name)) != complete['files_sha256'].get(name):
                return 'unverified'
        for phase in ('before', 'after'):
            proof = json.loads(read_bytes(directory / ('runtime-verification-' + phase + '.json')))
            if proof.get('identity') != attested['identity'] or proof.get('provider') != attested['provider'] or proof.get('status') != 'verified_local_content_and_route':
                return 'unverified'
        result = json.loads(read_bytes(directory / 'result.json'))
        if result.get('runtime_attestation_id') != attested['identity'] or result.get('provider') != attested['provider']['provider']:
            return 'unverified'
        return 'verified_at_execution'
    except (KeyError, ValueError, OSError):
        return 'unverified'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inventory', 'verify-task'])
    parser.add_argument('--task')
    args = parser.parse_args()
    if args.command == 'verify-task':
        print(json.dumps(verify(json.loads(Path(args.task).read_text()))))
    else:
        inventories = {}
        for harness in ('deepseek-harness', 'zcode'):
            print('Hashing reviewed runtime components:', harness, flush=True)
            inventories[harness] = capture(harness, progress=lambda name, count: print('  ', name, count, 'files', flush=True))
        receipt = write_receipt(inventories)
        print(json.dumps({'status': receipt['status'], 'runtimes': list(inventories), 'model_api_calls': 0}))


if __name__ == '__main__':
    main()
