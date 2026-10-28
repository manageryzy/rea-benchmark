"""Bazel-style local action cache and CAS, using versioned canonical JSON.

This is not the Bazel remote-execution wire protocol. Command/input-directory/action
digests are separate, and each action declares its dependencies. Storage code and
the complete dependency registry are intentionally not universal action inputs.
"""
import hashlib
import json
import os
import platform
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / 'benchmarks/config/action-dependencies.json'
DEFAULT = ROOT / '.cache/benchmark-actions/v3'
FORMAT = 'rea-action-json-v3'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(data):
    return {'hash': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}


def valid_digest(value):
    if not isinstance(value, dict) or set(value) != {'hash', 'size_bytes'} or not isinstance(value['hash'], str) or not re.fullmatch('[0-9a-f]{64}', value['hash']) or type(value['size_bytes']) is not int or value['size_bytes'] < 0:
        raise ValueError('Invalid content digest')
    return value


def relative(name):
    if not isinstance(name, str):
        raise ValueError('Action paths must be strings')
    value = PurePosixPath(name)
    if not isinstance(name, str) or not name or '\\' in name or value.is_absolute() or any(part in ('..', '.') for part in name.split('/')) or str(value) != name:
        raise ValueError('Action paths must be normalized relative paths')
    return value


def dependencies(stage, *, harness=None, condition=None, language=None, root=ROOT):
    registry = json.loads((Path(root) / REGISTRY.relative_to(ROOT)).read_text())
    if registry.get('schema_version') != 1:
        raise ValueError('Unknown action dependency schema')
    spec = registry[stage]
    selected = list(spec.get('common', []))
    for dimension, choice in [('harness', harness), ('condition', condition), ('language', language)]:
        selected.extend(spec.get(dimension, {}).get(choice, []))
    return sorted(set(selected))


def implementation_inputs(stage, *, root=ROOT, **selection):
    return {name: (Path(root) / name).read_bytes() for name in dependencies(stage, root=root, **selection)}


class Action:
    def __init__(self, kind, inputs, *, arguments=(), environment=None, platform=None, timeout_seconds=None):
        self.blobs = {}
        def add(data):
            value = digest(data)
            self.blobs[value['hash']] = data
            return value
        tree = {}
        for name, data in sorted(inputs.items()):
            path = relative(name)
            node = tree
            for part in path.parts[:-1]:
                child = node.setdefault(part, {})
                if not isinstance(child, dict):
                    raise ValueError('Input file/directory collision')
                node = child
            if path.name in node:
                raise ValueError('Duplicate action input')
            if not isinstance(data, bytes):
                raise TypeError('Action inputs must be exact bytes')
            node[path.name] = (add(data), False)
        def directory(node):
            files, directories = [], []
            for name, value in sorted(node.items()):
                if isinstance(value, dict):
                    directories.append({'name': name, 'digest': directory(value)})
                else:
                    files.append({'name': name, 'digest': value[0], 'is_executable': value[1]})
            return add(canonical({'files': files, 'directories': directories}))
        command = {'arguments': list(arguments),
                   'environment_variables': [{'name': name, 'value': value} for name, value in sorted((environment or {}).items())],
                   'platform': platform or {}}
        self.command = command
        self.descriptor = {'format': FORMAT, 'kind': kind, 'command_digest': add(canonical(command)),
                           'input_root_digest': directory(tree), 'timeout_seconds': timeout_seconds}
        self.key = digest(canonical(self.descriptor))['hash']
        add(canonical(self.descriptor))


class Cache:
    def __init__(self, root=DEFAULT):
        self.root = Path(root)

    def _path(self, section, key):
        if not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('Invalid action/content key')
        return self.root / section / key

    @staticmethod
    def _write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError('Cache symlink refused')
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError('Conflicting immutable cache entry')
            return
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            stream.write(data)
            temporary = Path(stream.name)
        try:
            # link creates atomically without replacing another writer's entry.
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError('Conflicting concurrent cache entry')
        finally:
            temporary.unlink()

    def put(self, data):
        value = digest(data)
        self._write(self._path('cas', value['hash']), data)
        return value

    def get(self, value):
        valid_digest(value)
        path = self._path('cas', value['hash'])
        if path.is_symlink():
            raise ValueError('Cache symlink refused')
        data = path.read_bytes()
        if digest(data) != value:
            raise ValueError('Corrupt CAS blob; refusing cache reuse')
        return data

    def publish(self, action):
        for data in action.blobs.values():
            self.put(data)

    def lookup(self, action):
        path = self._path('ac', action.key)
        if not path.exists():
            return None
        if path.is_symlink():
            raise ValueError('Cache symlink refused')
        result = json.loads(path.read_bytes())
        if result['format'] != FORMAT or result['action'] != action.descriptor:
            raise ValueError('Action cache identity mismatch')
        if result['exit_code'] != 0:
            raise ValueError('Unsuccessful action is not a reusable result')
        for name, output in result['output_files'].items():
            relative(name)
            self.get(output['digest'])
            if type(output['is_executable']) is not bool:
                raise ValueError('Invalid output mode')
        return result

    def commit(self, action, outputs):
        self.publish(action)
        files = {}
        for name, value in sorted(outputs.items()):
            relative(name)
            data, executable = value if isinstance(value, tuple) else (value, False)
            files[name] = {'digest': self.put(data), 'is_executable': bool(executable)}
        result = {'format': FORMAT, 'action': action.descriptor, 'exit_code': 0, 'output_files': files}
        self._write(self._path('ac', action.key), canonical(result))
        return result

    def capture(self, action, directory, names=None):
        directory = Path(directory)
        root = directory.resolve()
        parents = {directory: root}
        paths = [directory.joinpath(*relative(name).parts) for name in names] if names is not None else sorted(directory.rglob('*'))
        outputs = {}
        for path in paths:
            if path.parent not in parents:
                parents[path.parent] = path.parent.resolve()
            if path.is_symlink() or not parents[path.parent].is_relative_to(root):
                raise ValueError('Output symlinks are unsupported')
            if path.is_file():
                name = str(path.relative_to(directory))
                outputs[name] = (path.read_bytes(), bool(path.stat().st_mode & 0o111))
        return self.commit(action, outputs)

    def restore(self, result, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if any(directory.iterdir()):
            raise ValueError('Action outputs restore only into an empty directory')
        for name, output in result['output_files'].items():
            path = directory.joinpath(*relative(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Copy bytes; published HTML must never share CAS hardlinks.
            with path.open('xb') as stream:
                stream.write(self.get(output['digest']))
            path.chmod(0o755 if output['is_executable'] else 0o644)


def inference(identity, public_inputs, *, root=ROOT, recorded_files=None, trial=None):
    harness = identity['environment']['harness']
    condition = identity['condition']
    inputs = {'task/' + name: data for name, data in public_inputs.items()}
    names = dependencies('inference', root=root, harness=harness, condition=condition)
    read = lambda name: recorded_files[name] if recorded_files is not None else (Path(root) / name).read_bytes()
    selected = {name: read(name) for name in names}
    expected = identity['environment']['files_sha256']
    for name, data in selected.items():
        if digest(data)['hash'] != expected.get(name):
            raise ValueError('Declared inference dependency differs from the verified profile: ' + name)
    inputs.update(selected)
    def configuration(name):
        raw = read(name)
        if digest(raw)['hash'] != expected.get(name):
            raise ValueError('Declared inference configuration differs from verified profile: ' + name)
        return json.loads(raw)
    delivery = configuration('benchmarks/config/evidence-delivery.json')
    effective = {key: identity[key] for key in ['experiment', 'case', 'condition', 'language', 'model', 'limits', 'runtime']}
    effective['trial'] = trial
    effective['evidence_delivery'] = delivery['dsh' if harness == 'deepseek-harness' else 'zcode']
    effective['runtime_identity_scope'] = 'recorded_profile_only; full_runtime_closure_not_certified'
    if identity.get('attestation'):
        # Historical keys retain their exact encoding. Attested executions have
        # their own namespace; an old observation cannot satisfy this action.
        effective['runtime_identity_scope'] = 'local-content-and-provider-v1'
        if identity['attestation']['runtime']['manifest'].get('policy') == 'trusted-local-pinned-image-v1':
            effective['runtime_identity_scope'] = 'trusted-local-pinned-image-v1'
        effective['attestation'] = identity['attestation']
        if identity['attestation'].get('runtime', {}).get('manifest', {}).get('schema_version') == 2:
            from runtime_attestation import component_blobs
            reader = Cache(Path(root) / '.cache/benchmark-actions/v3').get
            for name, raw in component_blobs(identity['attestation'], reader):
                inputs['runtime/components/' + name + '.json'] = raw
    if condition == 'paged-evidence-v2':
        effective['paging'] = configuration('benchmarks/config/evidence-paging.json')
    for name, key in [('prompt.txt', 'prompt'), ('target', 'binary'), ('tools.json', 'tools'), ('manifest.json', 'manifest')]:
        if digest(public_inputs[name])['hash'] != identity[key]:
            raise ValueError('Inference input fingerprint mismatch: ' + name)
    inputs['configuration/effective.json'] = canonical(effective)
    return Action('inference', inputs, arguments=['rea-benchmark', 'infer', harness],
                  platform={'target': 'linux-x86_64', 'identity_scope': effective['runtime_identity_scope']},
                  timeout_seconds=identity['limits'][1])


def grading_standard(language, standard):
    # These are the commands the current evaluator actually executes. Display
    # labels such as "Go" and an unused default "2021" are not compiler flags.
    return {'c': 'c11', 'rust': '2021', 'go': 'toolchain-default'}.get(language, standard)


def grading(context, code, cases, *, root=ROOT):
    language = context['language']
    files = implementation_inputs('grading', root=root, language=language)
    inputs = {**files, 'candidate/source': code.encode('utf-8') if code is not None else b'',
              'oracle/vectors.json': canonical(cases)}
    effective = {key: context[key] for key in ['protocol', 'language', 'track', 'vectors_sha256', 'standard', 'string_abi', 'compiler', 'limits']}
    effective['standard'] = grading_standard(language, context['standard'])
    effective['candidate_present'] = code is not None
    # Reusing an observation is not another independent stochastic/native trial.
    effective['trial'] = context['trial']
    inputs['configuration/effective.json'] = canonical(effective)
    return Action('grading', inputs, arguments=['rea-benchmark', 'grade', language, context['track']],
                  platform=context['compiler'], timeout_seconds=context['limits']['execute_seconds'])


def reporting(data, *, public=False, root=ROOT):
    stage = 'public_report' if public else 'report'
    inputs = implementation_inputs(stage, root=root)
    inputs['data/collected.json'] = canonical(data)
    assets = Path(root) / 'benchmarks/scripts/report_assets'
    for name in ['page.html', 'report.css', 'report.js']:
        inputs['assets/' + name] = (assets / name).read_bytes()
    if public:
        name = 'benchmarks/suites/stl-native-v2/config/coverage.json'
        inputs[name] = (Path(root) / name).read_bytes()
    runtime = {'python': sys.version, 'implementation': sys.implementation.name,
               'python_executable_sha256': digest(Path(sys.executable).resolve().read_bytes())['hash'],
               'zlib': zlib.ZLIB_RUNTIME_VERSION, 'system': platform.system(), 'machine': platform.machine()}
    return Action(stage, inputs, arguments=['rea-benchmark', 'report', 'public' if public else 'private'], platform=runtime)
