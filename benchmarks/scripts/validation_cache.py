"""Content-addressed coordinator checks, isolated execution only on cache misses.

Test modules are projected to the selected check before execution as well as
hashing. Historical suite receipts are never imported as newly attested actions.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import sysconfig
import tempfile

import action_cache as ac

VERSION = 'coordinator-check-v1'
PROJECTED = (
    'benchmarks/scripts/check_capability_coordinator.py',
    'benchmarks/scripts/check_attempt_routing.py',
    'benchmarks/scripts/capabilities/baselines/coordinator_checks.py',
)
SDK_ROOTS = (
    '.cache/benchmark-grading/v1', '.cache/benchmark-c-executor/v1',
    '.cache/benchmark-capabilities/tooling-v1',
    '.cache/benchmark-capabilities/baseline-clang20-tooling-v1',
    '.cache/benchmark-capabilities/optimized-lto-tooling-v1',
)


def project(raw, selected):
    """Execute precisely the code represented by the test action's input leaf."""
    tree = ast.parse(raw)
    methods = {node.name: node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_')}
    keep = set(selected)
    # Include explicitly called sibling checks, including literal getattr names.
    while True:
        nodes = [node for name, node in methods.items() if name in keep]
        nodes += [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and not node.name.startswith('test_') and node.name != 'main']
        refs = {n.attr for node in nodes for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        refs |= {n.value for node in nodes for n in ast.walk(node)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        expanded = keep | (refs & methods.keys())
        if expanded == keep:
            break
        keep = expanded
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            node.body = [child for child in node.body if not (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name.startswith('test_') and child.name not in keep)] or [ast.Pass()]
    # The CLI driver is not imported by a worker. Its implementation is bound
    # separately through this module and parallel_controls.py.
    tree.body = [n for n in tree.body if not (
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'main')
        and not (isinstance(n, ast.If) and '__name__' in ast.unparse(n.test))]
    return (ast.unparse(ast.fix_missing_locations(tree)) + '\n').encode()


def read_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Validation input must be a regular file: ' + str(path))
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after):
        raise ValueError('Validation input changed during read: ' + str(path))
    return raw


def declared_inputs(root, sources):
    """Conservative shared closure; reports, attempts and AC contents excluded.

SDK metadata declares pinned image identities. The existing SDK loaders still
    verify actual image contents before execution; this is not runtime attestation.
    """
    root = Path(root)
    names = set(sources) - set(PROJECTED) - {'benchmarks/scripts/check_parallel_controls.py'}
    names.add('benchmarks/scripts/validation_cache.py')
    # The coordinator imports the native registry through records/config.
    # Bind this new dependency instead of reusing checks across registry edits.
    names.update(('benchmarks/scripts/records.py', 'benchmarks/scripts/native_case_registry.py',
                  'benchmarks/config/native-case-extensions.json', 'benchmarks/config/experiments.json'))
    # Bind implementation and fixtures used by the typed graders. Keeping this
    # shared is conservative: grading changes invalidate all coordinator checks.
    for prefix in ('benchmarks/scripts/grading', 'benchmarks/suites/capabilities-v1'):
        names.update(str(p.relative_to(root)) for p in (root / prefix).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    names.update(str(p.relative_to(root)) for p in (root / 'benchmarks/scripts/capabilities').rglob('*.py')
                 if not p.name.endswith('_report.py') and str(p.relative_to(root)) not in PROJECTED)
    for prefix in ('task-index', 'task-freezes', 'grade-specs'):
        names.update(str(p.relative_to(root)) for p in
                     (root / '.cache/benchmark-capabilities' / prefix).rglob('*') if p.is_file())
    for prefix in SDK_ROOTS:
        # Read the manifests/active selections, never worker-state or archives.
        def metadata(directory):
            for p in directory.iterdir():
                if p.is_symlink() or p.name in ('archives', 'worker-state', 'validated', 'provenance'):
                    continue
                if p.is_file() and p.suffix == '.json':
                    names.add(str(p.relative_to(root)))
                elif p.is_dir() and not (len(p.name) == 64 and all(c in '0123456789abcdef' for c in p.name)):
                    metadata(p)
        metadata(root / prefix)
    for name in ('capabilities.json', 'models.json', 'evidence-paging.json', 'evidence-paging-compact.json',
                 'request-contract.json', 'action-dependencies.json', 'runtime-attestation.json'):
        names.add('benchmarks/config/' + name)
    names.update(('benchmarks/scripts/evidence-paging.mjs', 'benchmarks/scripts/tool-catalog.mjs'))
    ordered = sorted(names)
    with ThreadPoolExecutor(max_workers=16) as pool:
        raws = list(pool.map(lambda name: read_file(root / name), ordered))
    values = dict(zip(ordered, raws))
    return values


def host_tools(root):
    paths = {Path(sys.executable).resolve(), Path('/usr/bin/bwrap'),
             Path(root) / '.tools/node-v24.22.0-linux-x64/bin/node'}
    library = Path(sysconfig.get_path('stdlib'))
    paths.update(p for p in library.rglob('*') if p.suffix in ('.py', '.so') and p.is_file()
                 and not {'__pycache__', 'site-packages', 'dist-packages'}.intersection(p.parts))
    ordered = sorted(paths)
    with ThreadPoolExecutor(max_workers=16) as pool:
        hashes = list(pool.map(lambda p: hashlib.sha256(p.read_bytes()).hexdigest(), ordered))
    return hashlib.sha256(ac.canonical(dict(zip(
        ['workspace/' + str(p.relative_to(root)) if p.is_relative_to(root) else 'system/' + str(p).lstrip('/') for p in ordered],
        hashes)))).hexdigest()


class Plan:
    def __init__(self, root, sources, tests, *, inputs=None, projections=None):
        self.root = Path(root)
        self.sources = tuple(sources)
        self.explicit_inputs = inputs is not None
        self.tests = sorted(tests)
        if len(self.tests) != len(set(self.tests)) or not self.tests:
            raise ValueError('Duplicate or empty validation selection')
        self.inputs = declared_inputs(self.root, sources) if inputs is None else inputs
        self.projections = ({name: read_file(self.root / name) for name in PROJECTED}
                            if projections is None else projections)
        self.platform = {'python': platform.python_version(), 'implementation': sys.implementation.name,
                         'system': platform.system(), 'machine': platform.machine(), 'libc': list(platform.libc_ver()),
                         'kernel_release': platform.release()}
        if not self.explicit_inputs:
            self.platform['host_tools_sha256'] = host_tools(self.root)
        action_inputs = dict(self.inputs)
        with ThreadPoolExecutor(max_workers=16) as pool:
            modes = list(pool.map(lambda name: bool((self.root / name).stat().st_mode & 0o111), self.inputs))
        self.modes = dict(zip(self.inputs, modes))
        action_inputs['validation-input-modes.json'] = ac.canonical(self.modes)
        registry = 'benchmarks/config/action-dependencies.json'
        if registry in action_inputs:
            policy = json.loads(action_inputs[registry])
            for key in ('report', 'public_report'):
                policy.pop(key, None)
            action_inputs[registry] = ac.canonical(policy)
        self.common = ac.Action('validation-dependencies-v1', action_inputs, platform=self.platform)
        self.actions, self.code = {}, {}
        for name in self.tests:
            code = {path: project(raw, {name}) for path, raw in self.projections.items()}
            self.code[name] = code
            self.actions[name] = ac.Action(VERSION,
                {**code, 'dependencies.action.json': ac.canonical(self.common.descriptor)},
                arguments=['TypedCoordinatorControls', name], platform=self.platform,
                environment={'provider_network': 'forbidden', 'model_launch': 'forbidden'})

    def lookup(self, cache, name):
        action = self.actions[name]
        result = cache.lookup(action)
        if result is None:
            return None
        if set(result['output_files']) != {'check.json'}:
            raise ValueError('Unexpected validation outputs')
        value = json.loads(cache.get(result['output_files']['check.json']['digest']))
        if (value.get('schema_version') != 1 or value.get('test') != name
                or value.get('action') != action.key or value.get('passed') is not True
                or value.get('provider_launches') != 0 or value.get('model_api_calls') != 0):
            raise ValueError('Invalid cached validation result')
        return value

    def commit(self, cache, name, execution):
        if execution.get('passed') is not True or execution.get('checks') != 1:
            raise ValueError('Only a completed passing check is reusable')
        rows = execution.get('tests', [])
        if (len(rows) != 1 or rows[0].get('name') != name or rows[0].get('status') != 'passed'
                or execution.get('provider_launches') != 0 or execution.get('model_api_calls') != 0):
            raise ValueError('Validation execution does not match action')
        cache.publish(self.common)
        value = {'schema_version': 1, 'test': name, 'action': self.actions[name].key,
                 'passed': True, 'provider_launches': 0, 'model_api_calls': 0,
                 'execution': execution}
        cache.commit(self.actions[name], {'check.json': ac.canonical(value)})

    def unchanged(self):
        current = Plan(self.root, self.sources, self.tests,
                       inputs={name: read_file(self.root / name) for name in self.inputs} if self.explicit_inputs else None)
        if {n: a.key for n, a in current.actions.items()} != {n: a.key for n, a in self.actions.items()}:
            raise ValueError('Validation dependencies changed during execution')


def execute(plan, *, jobs=4, cache=None, snapshot=None):
    """No workspace copies, compiler setup or model calls for a full cache hit."""
    import parallel_controls as parallel
    import control_workspace
    cache = cache or ac.Cache()
    hits = {name: plan.lookup(cache, name) for name in plan.tests}
    missing = [name for name, value in hits.items() if value is None]
    print(f'VALIDATION CACHE: {len(plan.tests)-len(missing)}/{len(plan.tests)} hits; {len(missing)} checks to execute', flush=True)
    if not missing:
        plan.unchanged()
        return {'passed': True, 'checks': len(plan.tests), 'cache_hits': len(plan.tests), 'executed': 0,
                'actions': {name: action.key for name, action in plan.actions.items()}}
    snapshot = snapshot or control_workspace.snapshot
    count = min(jobs, len(missing))
    if not 1 <= count <= 16:
        raise ValueError('Invalid validation worker count')
    result_root = Path(tempfile.mkdtemp(prefix='cached-coordinator-', dir=plan.root / '.cache/benchmark-runtime'))
    executions = {}
    with tempfile.TemporaryDirectory(prefix='rea-validation-') as temporary:
        temporary = Path(temporary)
        seed = temporary / 'worker-0'
        snapshot(plan.root, seed)
        roots = [seed]
        for index in range(1, count):
            target = temporary / ('worker-' + str(index))
            shutil.copytree(seed, target, symlinks=True)
            roots.append(target)
        def worker(index):
            root = roots[index]
            for name in missing[index::count]:
                for relative, expected in plan.inputs.items():
                    if (read_file(root / relative) != expected or
                            bool((root / relative).stat().st_mode & 0o111) != plan.modes[relative]):
                        raise ValueError('Worker dependency differs from action: ' + relative)
                for relative, raw in plan.code[name].items():
                    (root / relative).write_bytes(raw)
                source_names = sorted(set(plan.sources) | set(plan.code[name]))
                value = parallel.run_prepared([root], [name], source_names, result_root / name,
                    module='check_capability_coordinator', test_class='TypedCoordinatorControls')
                for relative, expected in plan.inputs.items():
                    if (read_file(root / relative) != expected or
                            bool((root / relative).stat().st_mode & 0o111) != plan.modes[relative]):
                        raise ValueError('Worker changed declared dependency: ' + relative)
                executions[name] = value
                print('VALIDATION:', name, 'passed' if value['passed'] else 'failed', flush=True)
        with ThreadPoolExecutor(max_workers=count) as pool:
            list(pool.map(worker, range(count)))
    # Publish nothing against inputs that changed while workers were running.
    plan.unchanged()
    for name, value in executions.items():
        if value['passed']:
            plan.commit(cache, name, value)
    return {'passed': all(v['passed'] for v in executions.values()), 'checks': len(plan.tests),
            'cache_hits': len(plan.tests)-len(missing), 'executed': len(executions),
            'actions': {name: action.key for name, action in plan.actions.items()},
            'results_directory': str(result_root.relative_to(plan.root))}


def write_receipt(path, plan, outcome):
    path = Path(path)
    if path.exists():
        raw = path.read_bytes()
        archive = plan.root / '.cache/benchmark-runtime/validation-receipts' / (hashlib.sha256(raw).hexdigest() + '.json')
        ac.Cache._write(archive, raw)
    value = {'schema_version': 2, 'kind': VERSION, **outcome,
             'model_api_calls': 0, 'provider_launches': 0,
             'failed': 0 if outcome['passed'] else 1,
             # Reporting provenance only. Admission below resolves individual
             # current actions, never uses this broad map as a cache key.
             'inputs_sha256': {name: hashlib.sha256(read_file(plan.root / name)).hexdigest()
                              for name in plan.sources}}
    from parallel_controls import write_json
    write_json(path, value)


def require(plan, receipt, cache=None):
    cache = cache or ac.Cache()
    if (receipt.get('schema_version') != 2 or receipt.get('kind') != VERSION
            or receipt.get('passed') is not True or receipt.get('checks') != len(plan.tests)
            or receipt.get('provider_launches') != 0 or receipt.get('model_api_calls') != 0
            or receipt.get('failed') != 0):
        raise RuntimeError('Incomplete cached coordinator validation')
    # The receipt is a selection/progress record, never the authority for a hit.
    for name in plan.tests:
        if plan.lookup(cache, name) is None:
            raise RuntimeError('Coordinator check needs validation: ' + name)
