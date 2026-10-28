"""Append-only typed submission grades, independent of language-code extraction."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest
from grading.artifact_capture import extract, MAX_ANSWER
from grading.structured import runtime as structured
from grading.c_executor import runtime as c_runtime
from . import buffers, submission

ROOT = action_cache.ROOT
VERSION = 'capability-artifacts-v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def spec(task):
    if task.get('track') != 'capability' or task.get('artifact_contract_version') != 1:
        raise ValueError('Unregistered capability task contract')
    path = ROOT.joinpath(*action_cache.relative(task['grading_spec']).parts)
    allowed = ROOT / '.cache/benchmark-capabilities/grade-specs'
    if path.is_symlink() or not path.resolve().is_relative_to(allowed.resolve()):
        raise ValueError('Capability grading specification escapes its private root')
    raw = path.read_bytes()
    if sha(raw) != task['grading_spec_sha256']:
        raise ValueError('Private capability grading specification changed')
    value = json.loads(raw)
    if (value.get('schema_version') != 1 or value.get('kind') not in ('localization', 'optimized_localization', 'native_abi', 'matched_buffer', 'c_buffer', 'cpp_buffer', 'abi_probe', 'pointer_probe', 'variant_object', 'indirect_call', 'c_lifecycle', 'cpp_lifecycle')
            or value.get('binary_sha256') != task['binary_sha256']):
        raise ValueError('Capability grading specification differs from target')
    expected = {'locations.json': 1} if value['kind'] == 'localization' else {'solution.c': 1}
    if value['kind'] in ('cpp_buffer', 'cpp_lifecycle'):
        expected = {'solution.cpp': 1}
    if value['kind'] == 'matched_buffer':
        from .baselines import tasks as matched_tasks
        if (value['family_id'] != task.get('family_id') or value['original_build_id'] != task.get('build_id')
                or value['output_language'] != task.get('output_language')
                or value['original_compiler'] != task.get('original_compiler')):
            raise ValueError('Matched private selection differs from its task')
        matched_tasks.validate_private(value)
        expected = {matched_tasks.FILENAMES[value['output_language']]: 1}
    if value['kind'] == 'optimized_localization':
        from .optimized_locations import tasks as optimized_tasks, scoring
        if (value.get('condition') != 'optimized_recover' or value.get('mode') not in ('optimized', 'lto')):
            raise ValueError('Unregistered optimized-location scoring condition')
        optimized_tasks.validate_original(value['target'], value['mode'])
        if action_cache.canonical(value.get('truth')) != action_cache.canonical(scoring.truth(value['target'], value['mode'])):
            raise ValueError('Optimized-location private truth differs from original')
        expected = {'instances.json': 1}
    if value['kind'] == 'native_abi':
        from .native_abi import tasks as native_tasks
        if value.get('condition') != 'native_abi_recover' or value.get('language') not in ('rust', 'go'):
            raise ValueError('Unregistered native ABI scoring condition')
        native_tasks.validate_original(value['target'], value['language'])
        native_tasks.vectors(value['nonces'])
        expected = native_tasks.ARTIFACT
    if value['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
        from . import lifecycle
        if (value.get('contract_version') != 'lifecycle-observable-v1' or value.get('family_id') not in lifecycle.FAMILIES
                or task.get('family_id', value['family_id']) != value['family_id']):
            raise ValueError('Unregistered lifecycle family or invocation contract')
        lifecycle.validate_oracle(value['scenarios'], value['oracle'])
    if value['kind'] == 'abi_probe':
        from .abi import contracts
        if value.get('condition') not in contracts.CONDITIONS:
            raise ValueError('Unregistered original-endpoint scoring condition')
        nonces = value.get('nonces')
        if (not isinstance(nonces, list) or not 1 <= len(nonces) <= 16
                or any(type(item) is not int or not 0 <= item < 2**64 for item in nonces)
                or len(set(nonces)) != len(nonces)):
            raise ValueError('Invalid original-endpoint vector contract')
        expected = {'abi.json' if value['condition'] == 'abi_recover' else 'types.json': 1}
    if value['kind'] == 'pointer_probe':
        from .pointers import runtime as pointer_runtime
        from .pointers.tasks import validate_original_binding
        if value.get('condition') != 'pointer_recover':
            raise ValueError('Unregistered pointer-object scoring condition')
        if not isinstance(value.get('nonces'), list):
            raise ValueError('Invalid pointer-object vector contract')
        pointer_runtime.vectors(value['nonces'])
        pointer_runtime.validate_target(value['target'])
        validate_original_binding(value['target'])
        expected = {'types.json': 1}
    if value['kind'] == 'variant_object':
        from .variant_objects import runtime as variant_runtime
        if value.get('condition') != 'variant_recover' or not isinstance(value.get('nonces'), list):
            raise ValueError('Invalid variant-object scoring condition or vectors')
        variant_runtime.vectors(value['nonces'])
        variant_runtime.validate_target(value['target'])
        expected = {'types.json': 1}
    if value['kind'] == 'indirect_call':
        from .indirect_calls import runtime as indirect_runtime
        if value.get('condition') != 'indirect_recover' or not isinstance(value.get('nonces'), list):
            raise ValueError('Invalid indirect-call scoring condition or vectors')
        indirect_runtime.vectors(value['nonces'])
        indirect_runtime.validate_target(value['target'])
        expected = {'calls.json': 1}
    if task.get('artifact_schema') != expected:
        raise ValueError('Capability artifact contract and grader disagree')
    supported = {'L1', 'L2', 'L3', 'L4', 'L5'} if value['kind'] == 'localization' else {'C1', 'C2', 'C3', 'C4'}
    if value['kind'] == 'abi_probe':
        supported = {contracts.CONDITIONS[value['condition']]}
    if value['kind'] == 'pointer_probe':
        supported = {'T3'}
    if value['kind'] == 'variant_object':
        supported = {'T4'}
    if value['kind'] == 'indirect_call':
        supported = {'T6'}
    if value['kind'] == 'optimized_localization':
        supported = {'L6'}
    if value['kind'] == 'native_abi':
        supported = {'T7'}
    if value['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
        supported = set(lifecycle.FAMILIES[value['family_id']])
    capabilities = task.get('capability_ids')
    if (not isinstance(capabilities, list) or not capabilities or any(not isinstance(item, str) for item in capabilities)
            or len(capabilities) != len(set(capabilities)) or set(capabilities) - supported):
        raise ValueError('Invalid capability score selection')
    oracle = (value['truth'] if value['kind'] in ('localization', 'optimized_localization') else
              {'binary_sha256': sha(bytes.fromhex(value['target']['public_hex']))} if value['kind'] == 'native_abi' else
              value['target'] if value['kind'] in ('abi_probe', 'pointer_probe', 'variant_object', 'indirect_call') else value['oracle'])
    if oracle.get('binary_sha256') != task['binary_sha256']:
        raise ValueError('Private oracle and task target differ')
    return value


def fixed_inputs(task):
    """The complete candidate-independent grading contract; no compilation or execution."""
    value = spec(task)
    inputs = {'grading-spec.json': action_cache.canonical(value),
              'artifact-schema.json': action_cache.canonical(task['artifact_schema']),
              'score-contract.json': action_cache.canonical({'capability_ids': task['capability_ids']}),
              'benchmarks/scripts/grading/artifact_capture.py': (ROOT / 'benchmarks/scripts/grading/artifact_capture.py').read_bytes(),
              'benchmarks/scripts/capabilities/submission.py': (ROOT / 'benchmarks/scripts/capabilities/submission.py').read_bytes()}
    for name in ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/artifacts.py',
                 'benchmarks/scripts/capabilities/__init__.py'):
        inputs[name] = (ROOT / name).read_bytes()
    if value['kind'] == 'matched_buffer':
        from .baselines import task_runtime as matched_runtime
        selected, environment = matched_runtime.fixed_inputs(value)
        inputs.update(selected)
        inputs['matched-task-binding.json'] = action_cache.canonical({key: task[key] for key in
            ('family_id', 'build_id', 'output_language', 'original_compiler')})
    elif value['kind'] == 'localization':
        inputs.update(structured.sources())
        inputs['policy.json'] = action_cache.canonical(structured.policy())
        environment = {'runtime_id': structured.image.record()['id'], **structured.host()}
    elif value['kind'] == 'optimized_localization':
        from .optimized_locations import runtime as optimized_runtime
        inputs.update({name: (ROOT / name).read_bytes() for name in optimized_runtime.SOURCES})
        inputs['optimized-profile.json'] = action_cache.canonical(structured.policy())
        environment = optimized_runtime._binding()
    elif value['kind'] == 'native_abi':
        from .native_abi import runtime as native_runtime, execute as native_execute
        inputs.update({name: (ROOT / name).read_bytes() for name in native_runtime.SOURCES})
        inputs['native-compile-profile.json'] = action_cache.canonical(c_runtime.compile_profile())
        inputs['native-execute-profile.json'] = action_cache.canonical(native_execute.profile())
        environment = {'compile': c_runtime.platform(), 'execution': c_runtime.platform(True),
                       'original': value['target']['runtime']}
    elif value['kind'] == 'indirect_call':
        from .indirect_calls import runtime as indirect_runtime
        inputs.update(indirect_runtime.sources('probe'))
        names = (indirect_runtime.engine.HOST_SOURCE, indirect_runtime.MODULE + 'runtime.py',
                 indirect_runtime.MODULE + 'contracts.py')
        inputs.update({name: (ROOT / name).read_bytes() for name in names})
        inputs.update(c_runtime.sources('c_execute'))
        inputs['indirect-profile.json'] = action_cache.canonical(indirect_runtime.policy())
        inputs['execute-profile.json'] = action_cache.canonical(c_runtime.execute_profile())
        environment = {'compiler': indirect_runtime.platform('probe'), 'execution': c_runtime.platform(True)}
    elif value['kind'] == 'variant_object':
        from .variant_objects import runtime as variant_runtime
        inputs.update(variant_runtime.sources('probe'))
        names = (variant_runtime.engine.HOST_SOURCE, variant_runtime.MODULE + 'runtime.py',
                 variant_runtime.MODULE + 'contracts.py')
        inputs.update({name: (ROOT / name).read_bytes() for name in names})
        inputs.update(c_runtime.sources('c_execute'))
        inputs['variant-profile.json'] = action_cache.canonical(variant_runtime.policy())
        inputs['execute-profile.json'] = action_cache.canonical(c_runtime.execute_profile())
        environment = {'compiler': variant_runtime.platform('probe'), 'execution': c_runtime.platform(True)}
    elif value['kind'] == 'pointer_probe':
        from .pointers import runtime as pointer_runtime
        inputs.update(pointer_runtime.sources('probe'))
        inputs['benchmarks/scripts/capabilities/pointers/tasks.py'] = (ROOT / 'benchmarks/scripts/capabilities/pointers/tasks.py').read_bytes()
        for name in ('runtime.py', 'contracts.py'):
            path = 'benchmarks/scripts/capabilities/pointers/'+name
            inputs[path] = (ROOT/path).read_bytes()
        inputs.update(c_runtime.sources('c_execute'))
        inputs['pointer-profile.json'] = action_cache.canonical(pointer_runtime.policy())
        inputs['execute-profile.json'] = action_cache.canonical(c_runtime.execute_profile())
        environment = {'compiler': pointer_runtime.platform('probe'), 'execution': c_runtime.platform(True)}
    elif value['kind'] == 'abi_probe':
        from .abi import runtime as abi_runtime
        inputs.update(abi_runtime.sources('probe'))
        inputs['benchmarks/scripts/capabilities/abi/runtime.py'] = (ROOT / 'benchmarks/scripts/capabilities/abi/runtime.py').read_bytes()
        inputs.update(c_runtime.sources('c_execute'))
        inputs['abi-profile.json'] = action_cache.canonical(abi_runtime.policy())
        inputs['execute-profile.json'] = action_cache.canonical(c_runtime.execute_profile())
        environment = {'compiler': abi_runtime.platform('probe'), 'execution': c_runtime.platform(True)}
    elif value['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
        inputs['benchmarks/scripts/capabilities/lifecycle.py'] = (ROOT / 'benchmarks/scripts/capabilities/lifecycle.py').read_bytes()
        inputs.update(c_runtime.sources('c_execute'))
        inputs['execute-profile.json'] = action_cache.canonical(c_runtime.execute_profile())
        if value['kind'] == 'cpp_lifecycle':
            from .cpp_lifecycle import runtime as cpp_runtime
            inputs.update(cpp_runtime.sources())
            inputs['compile-profile.json'] = action_cache.canonical(cpp_runtime.policy())
            environment = {'compile': cpp_runtime.platform(), 'execution': c_runtime.platform(True)}
        else:
            inputs.update(c_runtime.sources('c_compile'))
            inputs['compile-profile.json'] = action_cache.canonical(c_runtime.compile_profile())
            environment = c_runtime.platform(True)
    elif value['kind'] == 'cpp_buffer':
        from .cpp_buffer import runtime as cpp_runtime
        inputs.update(cpp_runtime.sources())
        inputs.update(c_runtime.sources('c_execute'))
        inputs['benchmarks/scripts/capabilities/buffers.py'] = (ROOT / 'benchmarks/scripts/capabilities/buffers.py').read_bytes()
        inputs['compile-profile.json'] = action_cache.canonical(cpp_runtime.policy())
        inputs['execute-profile.json'] = action_cache.canonical(c_runtime.execute_profile())
        environment = {'compile': cpp_runtime.platform(), 'execution': c_runtime.platform(True)}
    else:
        for stage in ('c_compile', 'c_execute'):
            inputs.update(c_runtime.sources(stage))
        inputs['benchmarks/scripts/capabilities/buffers.py'] = (ROOT / 'benchmarks/scripts/capabilities/buffers.py').read_bytes()
        inputs['compile-profile.json'] = action_cache.canonical(c_runtime.compile_profile())
        inputs['execute-profile.json'] = action_cache.canonical(c_runtime.execute_profile())
        environment = c_runtime.platform(True)
    return value, inputs, environment


def prepare(directory, task):
    directory = Path(directory)
    if any((directory/name).exists() for name in ('harness-failure.json', 'interrupted.json', 'budget-exhausted.json')):
        raise RuntimeError('Environment/interrupted attempt cannot receive capability correctness')
    value, inputs, environment = fixed_inputs(task)
    answer = (directory / 'answer.md').read_bytes()
    inputs['answer.md'] = answer
    budget_path = directory / 'request-budget.json'
    budget = json.loads(budget_path.read_bytes()) if budget_path.exists() else {}
    deadline = budget.get('status') == 'deadline_exceeded'
    if deadline:
        import request_contract
        if not request_contract.deadline_grade_allowed(budget, task.get('request_contract', {})):
            raise ValueError('Invalid deadline receipt')
        inputs['deadline-receipt.json'] = budget_path.read_bytes()
        inputs['deadline-policy.py'] = Path(request_contract.__file__).read_bytes()
    try:
        if deadline:
            raise ValueError('No complete submission before deadline')
        captured = extract(answer, task['artifact_schema'])
        inputs.update({'artifacts/' + name: raw for name, raw in captured.items()})
        inputs['capture-status'] = b'captured'
    except (ValueError, UnicodeError):
        inputs['capture-status'] = b'invalid-artifact-set'
    action = action_cache.Action('capability_submission_v1', inputs, arguments=[VERSION, value['kind']], platform=environment)
    return action, answer, value

def action(directory, task):
    return prepare(directory, task)[0]


def read_selected(directory, *, task=None):
    directory = Path(directory).resolve()
    index = directory / 'selected-grade.json'
    if not index.exists():
        return None, None
    selected = json.loads(index.read_bytes())
    relative = action_cache.relative(selected['path'])
    path = directory.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(directory / 'grades'):
        raise ValueError('Typed grading selection escapes its grade root')
    raw = path.read_bytes()
    if sha(raw) != selected['sha256']:
        raise ValueError('Selected typed grade was changed')
    manifest = json.loads(path.with_name('manifest.json').read_bytes())
    key = action_cache.digest(action_cache.canonical(manifest['action']))['hash']
    if key != selected['grading_id'] or key != manifest['grading_id']:
        raise ValueError('Typed grade action identity mismatch')
    if sha((directory / 'answer.md').read_bytes()) != manifest['answer_sha256']:
        raise ValueError('Saved typed answer changed since grading')
    binding = manifest.get('task_binding')
    if binding is not None:
        spec(binding)
    for name, expected in manifest['files_sha256'].items():
        artifact = path.parent.joinpath(*action_cache.relative(name).parts)
        if artifact.is_symlink() or not artifact.resolve().is_relative_to(path.parent) or sha(artifact.read_bytes()) != expected:
            raise ValueError('Typed grading artifact missing or changed')
    if task is not None:
        spec(task)
    return json.loads(raw), str(relative)


def grade(directory, task, *, select=True):
    from .evidence import require_validation
    require_validation()
    directory = Path(directory)
    planned, answer, value = prepare(directory, task)
    base = directory / 'grades' / VERSION
    destination = base / planned.key
    base.mkdir(parents=True, exist_ok=True)
    lock = base / (planned.key + '.lock')
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        if not destination.exists():
            with tempfile.TemporaryDirectory(prefix='pending-', dir=base) as temporary:
                work = Path(temporary)
                local = action_cache.Cache()
                hit = local.lookup(planned)
                if hit:
                    local.restore(hit, work)
                else:
                    deadline = json.loads((directory / 'request-budget.json').read_bytes()).get('status') == 'deadline_exceeded' if (directory / 'request-budget.json').exists() else False
                    result, files = submission.evaluate(b'' if deadline else answer, task, value)
                    if deadline:
                        result['failure'] = 'Deadline exceeded; no complete submission before the time limit'
                        result['completion_outcome'] = 'deadline_exceeded'
                    result.update(grading_id=planned.key, grading_version=VERSION,
                                  artifact_schema=task['artifact_schema'], capability_ids=task['capability_ids'])
                    (work / 'grade.json').write_bytes(action_cache.canonical(result))
                    for name, raw in files.items():
                        path = work / 'artifacts' / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(raw)
                    local.capture(planned, work)
                manifest = {'cache_schema': 3, 'action': planned.descriptor, 'grading_id': planned.key,
                    'answer_sha256': sha(answer), 'grading_spec_sha256': task['grading_spec_sha256'],
                    'task_binding': {key: task[key] for key in ('track', 'artifact_contract_version', 'grading_spec',
                        'grading_spec_sha256', 'binary_sha256', 'artifact_schema', 'capability_ids') +
                        (('family_id', 'build_id', 'output_language', 'original_compiler')
                         if value['kind'] == 'matched_buffer' else ())},
                    'files_sha256': {str(path.relative_to(work)): sha(path.read_bytes()) for path in work.rglob('*') if path.is_file()}}
                (work / 'manifest.json').write_bytes(action_cache.canonical(manifest))
                work.rename(destination)
        saved_manifest = json.loads((destination / 'manifest.json').read_bytes())
        if saved_manifest['action'] != planned.descriptor:
            raise ValueError('Typed grading directory differs from current action')
        for name, expected in saved_manifest['files_sha256'].items():
            if sha((destination / name).read_bytes()) != expected:
                raise ValueError('Typed grading artifact changed before selection')
        if select:
            index = {'grading_id': planned.key, 'path': str((destination / 'grade.json').relative_to(directory)),
                     'sha256': sha((destination / 'grade.json').read_bytes())}
            temporary = directory / ('selected-grade.json.tmp-' + str(os.getpid()))
            temporary.write_bytes(action_cache.canonical(index))
            temporary.replace(directory / 'selected-grade.json')
        return json.loads((destination / 'grade.json').read_bytes())
    finally:
        lock.unlink(missing_ok=True)
