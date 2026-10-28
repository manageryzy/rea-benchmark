"""Freeze public task packages separately from private grading specifications."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from . import build, buffers, config, elf, locations, public

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities'
TRANSPORT = ('\nReturn each required artifact in exactly one Markdown code block labelled with its exact filename '
             '(for example ```locations.json or ```solution.c). Do not emit other code blocks. '
             'A brief evidence explanation may precede the named blocks.\n')
SEMANTICS = {
    'record_codec': 'Start with 65535. If at least four bytes exist and byte 0 is 167, replace that value with (byte1 * little-endian-u16(bytes2..3)) XOR 27957. Add byte4 when more than four bytes exist.',
    'dispatch_registry': 'For empty input the value is zero. Otherwise let choice=byte0 AND 1. For choice zero, start at 3 and add bytes from index1 onward. For choice one, start at 11 and XOR each later byte shifted left by 8*(its index modulo8). Finally add choice.',
    'numeric_aggregation': 'Decode at most eight complete little-endian signed-16 pairs. Sort ascending including duplicates. Sum value[index]*(index+1) modulo 2^32, then XOR the unsigned 32-bit result with 2654435769 and zero-extend to 64 bits.',
    'variable_objects': 'For fewer than four bytes return 65297. Otherwise read little-endian-u16 length from bytes0..1. If length exceeds remaining bytes after the four-byte header return 65298. Start a uint64 accumulator at byte2; for length payload bytes at index4 multiply by33 and add the byte modulo2^64. XOR byte3, then add17 modulo2^64.',
    'callback_policy': 'Start at zero; sum (byte[index]+1)*(index+3) over every input byte modulo2^64, then add9.',
    'session_state': 'Start uint32 count=0 and checksum=0, and uint64 value=0. For every byte increment count, update checksum=checksum*17+byte modulo2^32, and XOR (checksum XOR count) into value. Each invocation starts fresh.'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def buffer_prompt(family, profile, entry):
    cpp = profile['kind'] == 'cpp_buffer'
    from .cpp_buffer import runtime as cpp_runtime
    filename, language = ('solution.cpp', 'C++20') if cpp else ('solution.c', 'C17')
    declaration = cpp_runtime.header() if cpp else buffers.HEADER
    restrictions = ('Use freestanding C++20; nonallocating standard-library algorithms, std::array, std::span and std::string_view are available. '
        'No exceptions, RTTI, TLS, dynamic initialization, OS, heap, inline assembly, external libraries or embedded executable. ') if cpp else (
        'Use freestanding C17 and standard integer/size types; no OS, heap, inline assembly, external libraries or embedded executable. ')
    text = (f'Recover the behavior of the supplied optimized stripped C++ target as {language}.\n'
        f'The known buffer-function RVA is {entry}. RVAs start at the lowest PT_LOAD page.\n'
        f'Submit {filename} with this exact public declaration and no main function:\n' + declaration +
        'Inputs contain 0..513 arbitrary bytes. Capacity is 0..520. Pointers are non-null even for zero lengths. '
        'The output-length pointer names one aligned size_t in separate memory. Input/output buffers can overlap. '
        'Read only the input range, initial output-capacity range and output length; write only output capacity and output length. '
        'Preserve every other byte, including unused output capacity. Recover exact signed status, length, output bytes and error effects. ' +
        restrictions +
        'memcpy/memmove/memset/memcmp/strlen helpers are available; source limit 262144 bytes. '
        'The candidate receives one invocation only, no original target, hidden vectors or expected answers.\n'
        'Use only supplied static REA/Ghidra tools; no original execution, compiler feedback, previous attempts or source access. '
        'Close the binary when finished. Produce readable, well-formatted code with descriptive helpers.\n')
    if profile['id'] in ('c_codegen', 'cpp_codegen'):
        text += ('\nIndependent full-semantics code-generation control. No reverse engineering is required. '
            'For input length above512 set output length to0, return -2 and preserve output. Otherwise compute the 64-bit value below. '
            'Required length is8+input length; set output length accordingly. If capacity is too small return -1 without changing output. '
            'On success return0 and write the little-endian 8-byte value followed by each original input byte XOR the value byte at (index modulo8). '
            'Input/output overlap must behave as if all input bytes were snapshotted before any output write. '
            'All 64-bit arithmetic wraps modulo2^64 except where stated.\nValue formula: ' + SEMANTICS[family] + '\n')
    return text + (TRANSPORT.replace('```solution.c', '```solution.cpp') if cpp else TRANSPORT)


def declared():
    settings, families = config.load()
    tasks = []
    for family in families['families']:
        for profile in settings['profiles']:
            if 'families' in profile and family['id'] not in profile['families']:
                continue
            if profile['kind'] == 'abi_probe':
                if family['id'] in profile['families']:
                    for convention in profile['conventions']:
                        tasks.append({'task_id': family['id'] + '_' + convention + '_' + profile['id'],
                            'family_id': family['id'], 'profile': profile, 'absent': False, 'convention': convention})
                continue
            tasks.append({'task_id': family['id'] + '_' + profile['id'], 'family_id': family['id'],
                          'profile': profile, 'absent': False})
    profile = next(row for row in settings['profiles'] if row['kind'] == 'localization')
    tasks.append({'task_id': 'record_codec_absent_' + profile['id'], 'family_id': 'record_codec',
                  'profile': profile, 'absent': True})
    return tasks


def localization_prompt(family):
    definition = next(row for row in config.load()[1]['families'] if row['id'] == family)
    return (locations.prompt(family, 'L1') +
            '\nAlso supply all listed decision/update roles, observed dispatch/data relations and the bounded call chain.\n' +
            'Semantic roles: ' + ', '.join(definition['roles']) + '.\n' + TRANSPORT)


def freeze_one(declaration, *, publish=True):
    settings, _ = config.load()
    if declaration['profile']['kind'] == 'matched_buffer':
        from .baselines.tasks import freeze
        return freeze(declaration, settings, publish=publish)
    if declaration['profile']['kind'] == 'optimized_localization':
        from .optimized_locations.tasks import freeze
        return freeze(declaration, settings, publish=publish)
    if declaration['profile']['kind'] == 'native_abi':
        from .native_abi.tasks import freeze
        return freeze(declaration, settings, publish=publish)
    if declaration['profile']['kind'] == 'indirect_call':
        from .indirect_calls.tasks import freeze
        return freeze(declaration, settings, publish=publish)
    if declaration['profile']['kind'] == 'variant_object':
        from .variant_objects.tasks import freeze
        return freeze(declaration, settings, publish=publish)
    if declaration['profile']['kind'] == 'pointer_probe':
        from .pointers.tasks import freeze
        return freeze(declaration, settings, publish=publish)
    if declaration['profile']['kind'] == 'abi_probe':
        from .abi.tasks import freeze
        return freeze(declaration, settings, publish=publish)
    if declaration['profile']['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
        from .lifecycle_tasks import freeze
        return freeze(declaration, settings, publish=publish)
    family, profile = declaration['family_id'], declaration['profile']
    original = build.build(family, absent=declaration['absent'])
    private, stripped = elf.stripped_matches(bytes.fromhex(original['private_hex']), bytes.fromhex(original['public_hex']))
    binary = bytes.fromhex(original['public_hex'])
    public_id = 'task_' + sha(binary + profile['id'].encode())[:20]
    if profile['kind'] == 'localization':
        truth = locations.private_truth(family, original, absent=declaration['absent'])
        # Presence/absence is private. Both variants ask exactly the same
        # question and declare the same stages; an opaque binary-derived ID
        # never contains an answer label such as "absent".
        capabilities = ['L1', 'L2', 'L5'] + (['L3'] if family in ('dispatch_registry', 'callback_policy') else []) + (['L4'] if family in ('record_codec', 'dispatch_registry') else [])
        metadata = public.manifest(locations.metadata(family, original, truth), capability='L1', task_id=public_id)
        # L1–L5 form a separately declared combined condition, scored by stages.
        metadata['capability_ids'] = sorted(capabilities)
        prompt = localization_prompt(family)
        public.validate_manifest(metadata)
        grading = {'schema_version': 1, 'kind': 'localization', 'binary_sha256': sha(binary), 'truth': truth}
    else:
        capabilities = ['C1', 'C2', 'C3', 'C4']
        entry = private.symbols()['family_buffer']['rva']
        metadata = {'schema_version': 1, 'family_id': family, 'build_id': original['build_id'],
            'task_id': public_id, 'capability_ids': capabilities, 'source_language': 'cpp',
            'output_language': profile['output_language'], 'artifact_schema': profile['artifact_schema'], 'binary_sha256': sha(binary),
            'architecture': 'x86_64', 'hint_profile': 'known_buffer_entry_v1', 'entry_rva': entry,
            'condition': profile['id']}
        public.validate_buffer_manifest(metadata)
        prompt = buffer_prompt(family, profile, entry)
        cases = buffers.vectors(family)
        oracle = buffers.reference(original, cases)
        grading = {'schema_version': 1, 'kind': profile['kind'], 'binary_sha256': sha(binary), 'cases': cases,
                   'oracle': {key: value for key, value in oracle.items() if key != 'cache_hit'}}
    files = {'target': binary, 'prompt.txt': prompt.encode('utf-8'),
             'tools.json': (ROOT / settings['analysis_tools_file']).read_bytes(),
             'manifest.json': action_cache.canonical(metadata)}
    hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    package_id = sha(action_cache.canonical(hashes))
    destination = CACHE / 'task-freezes' / package_id
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pending-', dir=destination.parent) as temporary:
            work = Path(temporary)
            for name, raw in files.items():
                (work / name).write_bytes(raw)
            work.rename(destination)
    for name, raw in files.items():
        if (destination / name).is_symlink() or (destination / name).read_bytes() != raw:
            raise ValueError('Frozen public capability package changed')
    raw_spec = action_cache.canonical(grading)
    grading_id = sha(raw_spec)
    spec_path = CACHE / 'grade-specs' / (grading_id + '.json')
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    if spec_path.exists() and spec_path.read_bytes() != raw_spec:
        raise ValueError('Frozen capability grading specification changed')
    if not spec_path.exists():
        spec_path.write_bytes(raw_spec)
    record = {'schema_version': 1, 'id': declaration['task_id'], 'family_id': family,
        'build_id': original['build_id'], 'task_id': declaration['task_id'], 'capability_ids': sorted(capabilities),
        'source_language': 'cpp', 'output_language': profile['output_language'], 'artifact_schema': profile['artifact_schema'],
        'kind': profile['kind'], 'profile_id': profile['id'], 'hint_profile': metadata['hint_profile'],
        'binary_sha256': sha(binary), 'public_package_sha256': package_id,
        'public_directory': str(destination.relative_to(ROOT)), 'public_files_sha256': hashes,
        'grading_spec': str(spec_path.relative_to(ROOT)), 'grading_spec_sha256': grading_id,
        'source_sha256': sha((build.SUITE / ('sources/' + family + '.cpp')).read_bytes()),
        'header_sha256': sha((build.SUITE / 'sources/common.hpp').read_bytes())}
    if publish:
        publish_index(record)
    return record


def publish_index(record):
    """Select a checked preparation without replacing immutable packages/specs."""
    index = CACHE / 'task-index' / (record['task_id'] + '.json')
    _replace_json(index, record)


def _replace_json(index, value):
    index.parent.mkdir(parents=True, exist_ok=True)
    if index.is_symlink():
        raise ValueError('Capability task index is a symlink')
    with tempfile.NamedTemporaryFile(dir=index.parent, prefix=index.name + '-', delete=False) as stream:
        stream.write(action_cache.canonical(value))
        temporary = Path(stream.name)
    try:
        temporary.replace(index)
    finally:
        temporary.unlink(missing_ok=True)


def frozen(task_id):
    declaration = next((row for row in declared() if row['task_id'] == task_id), None)
    if declaration is None:
        raise ValueError('Unregistered capability task')
    index = CACHE / 'task-index' / (task_id + '.json')
    if index.is_symlink():
        raise ValueError('Capability task index is a symlink')
    if not index.exists():
        raise RuntimeError('Capability task not prepared; run make prepare-capabilities')
    return validate_frozen(json.loads(index.read_bytes()), declaration)


def validate_frozen(value, declaration):
    """Validate a restored record before publishing its mutable task index."""
    task_id = declaration['task_id']
    if declaration['profile']['kind'] == 'matched_buffer':
        from .baselines.tasks import validate_frozen
        return validate_frozen(value, declaration, config.load()[0])
    if declaration['profile']['kind'] == 'optimized_localization':
        from .optimized_locations.tasks import validate_frozen
        return validate_frozen(value, declaration, config.load()[0])
    if declaration['profile']['kind'] == 'native_abi':
        from .native_abi.tasks import validate_frozen
        return validate_frozen(value, declaration, config.load()[0])
    if declaration['profile']['kind'] == 'indirect_call':
        from .indirect_calls.tasks import validate_frozen
        return validate_frozen(value, declaration, config.load()[0])
    if declaration['profile']['kind'] == 'variant_object':
        from .variant_objects.tasks import validate_frozen
        return validate_frozen(value, declaration, config.load()[0])
    if declaration['profile']['kind'] == 'pointer_probe':
        from .pointers.tasks import validate_frozen
        return validate_frozen(value, declaration, config.load()[0])
    if declaration['profile']['kind'] == 'abi_probe':
        from .abi.tasks import validate_frozen
        return validate_frozen(value, declaration, config.load()[0])
    if declaration['profile']['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
        from .lifecycle_tasks import validate_frozen
        return validate_frozen(value, declaration, config.load()[0])
    family = declaration['family_id']
    if (value['id'] != task_id or value['source_sha256'] != sha((build.SUITE / ('sources/' + family + '.cpp')).read_bytes())
            or value['header_sha256'] != sha((build.SUITE / 'sources/common.hpp').read_bytes())):
        raise RuntimeError('Capability target sources changed; run make prepare-capabilities')
    public_directory = ROOT / value['public_directory']
    if not public_directory.resolve().is_relative_to((CACHE / 'task-freezes').resolve()):
        raise ValueError('Capability public package escapes its frozen root')
    if {path.name for path in public_directory.iterdir()} != {'target', 'prompt.txt', 'tools.json', 'manifest.json'}:
        raise ValueError('Frozen public package contains an unapproved file')
    files = {name: (public_directory / name).read_bytes() for name in ('target', 'prompt.txt', 'tools.json', 'manifest.json')}
    hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    if hashes != value['public_files_sha256'] or sha(action_cache.canonical(hashes)) != value['public_package_sha256']:
        raise ValueError('Frozen capability public package changed')
    metadata = json.loads(files['manifest.json'])
    profile = declaration['profile']
    if profile['kind'] == 'localization':
        public.validate_manifest(metadata)
        expected_prompt = localization_prompt(family)
    else:
        public.validate_buffer_manifest(metadata)
        expected_prompt = buffer_prompt(family, profile, metadata['entry_rva'])
    settings, _ = config.load()
    if (files['prompt.txt'] != expected_prompt.encode('utf-8')
            or files['tools.json'] != (ROOT / settings['analysis_tools_file']).read_bytes()
            or value['artifact_schema'] != profile['artifact_schema'] or value['kind'] != profile['kind']):
        raise RuntimeError('Capability public contract changed; run make prepare-capabilities')
    return value


def preview():
    from . import preparation
    settings, _ = config.load()
    result = []
    for row in declared():
        state = preparation.inspect(row)
        value = state.get('case', {})
        status = 'frozen' if state['status'] == 'ready' else 'preparation_' + state['status']
        result.append({'task_id': row['task_id'], 'family_id': row['family_id'], 'profile_id': row['profile']['id'],
                       'enabled': settings['inference_enabled'] and row['profile']['enabled'],
                       'status': status, 'reason': state.get('reason'), 'public_package_sha256': value.get('public_package_sha256'),
                       'model_calls': 0})
    return result


def freeze_all():
    from . import preparation
    result = []
    for row in declared():
        value = preparation.prepare(row)
        result.append(value)
        print('CAPABILITY TASK FROZEN:', value['task_id'], value['public_package_sha256'], flush=True)
    pin_snapshot()
    return result


def pin_snapshot():
    """Record all preparation states without building unselected declarations."""
    from . import preparation
    declarations = declared()
    names = ['benchmarks/config/capabilities.json', 'benchmarks/suites/capabilities-v1/config/families.json',
             'benchmarks/scripts/capabilities/tasks.py', 'benchmarks/scripts/capabilities/public.py',
             'benchmarks/scripts/capabilities/locations.py', 'benchmarks/scripts/capabilities/preparation.py']
    names += [str(path.relative_to(ROOT)) for path in (build.SUITE / 'sources').iterdir() if path.is_file()]
    names += ['benchmarks/config/abi-probes.json', 'benchmarks/scripts/capabilities/abi/tasks.py',
              'benchmarks/scripts/capabilities/abi/contracts.py', 'benchmarks/scripts/capabilities/abi/variants.py',
              'benchmarks/suites/capabilities-v1/config/abi-variants.json']
    names += ['benchmarks/scripts/capabilities/cpp_buffer/contract.hpp']
    from .baselines import tasks as matched_tasks
    for row in declarations:
        if row['profile']['kind'] == 'matched_buffer':
            profile = row['profile']
            names += list(matched_tasks.selected_sources(profile['original_compiler'],
                          profile['output_language'], row['family_id']))
    names += ['benchmarks/scripts/capabilities/lifecycle.py', 'benchmarks/scripts/capabilities/lifecycle_tasks.py',
              'benchmarks/scripts/capabilities/lifecycle_build.py', 'benchmarks/scripts/capabilities/lifecycle_reference_worker.py',
              'benchmarks/scripts/capabilities/cpp_lifecycle/contract.hpp',
              'benchmarks/suites/capabilities-v1/sources/lifecycle/common.hpp']
    names += ['benchmarks/suites/capabilities-v1/sources/lifecycle/' + row['family_id'] + '.cpp'
              for row in declarations if row['profile']['kind'] in ('c_lifecycle', 'cpp_lifecycle')]
    names += [str(path.relative_to(ROOT)) for path in (build.SUITE / 'sources/abi').glob('*') if path.is_file()]
    names += [str(path.relative_to(ROOT)) for path in (build.SUITE / 'sources/abi/variants').glob('*.hpp')]
    from .pointers import runtime as pointer_runtime
    for row in declarations:
        if row['profile']['kind'] == 'pointer_probe':
            names += list(pointer_runtime.sources('original', row['profile']['variant']))
            names += ['benchmarks/scripts/capabilities/pointers/' + name for name in ('tasks.py', 'runtime.py', 'contracts.py')]
            names += ['benchmarks/config/pointer-probes.json']
    from .variant_objects import runtime as variant_runtime
    for row in declarations:
        if row['profile']['kind'] == 'variant_object':
            names += list(variant_runtime.sources('original', row['profile']['variant']))
            names += ['benchmarks/scripts/capabilities/variant_objects/' + name for name in ('tasks.py', 'runtime.py', 'contracts.py')]
            names += [variant_runtime.engine.HOST_SOURCE, 'benchmarks/config/variant-object-probes.json']
    from .abi import runtime as abi_runtime
    from .optimized_locations import tasks as optimized_tasks
    from .native_abi import tasks as native_tasks
    for row in declarations:
        if row['profile']['kind'] == 'optimized_localization':
            names += list(optimized_tasks.sources())
        elif row['profile']['kind'] == 'native_abi':
            names += list(native_tasks.sources(row['profile']['variant']))
    from .indirect_calls import runtime as indirect_runtime
    for row in declarations:
        if row['profile']['kind'] == 'indirect_call':
            names += list(indirect_runtime.sources('original', row['profile']['variant']))
            names += ['benchmarks/scripts/capabilities/indirect_calls/' + name for name in ('tasks.py', 'runtime.py', 'contracts.py')]
            names += [indirect_runtime.engine.HOST_SOURCE, 'benchmarks/config/indirect-call-probes.json']
    names += ['benchmarks/scripts/capabilities/abi/runtime.py']
    for family in abi_runtime.contracts.FAMILIES:
        try:
            names += list(abi_runtime.sources('original', family))
        except FileNotFoundError:
            # The affected declaration remains pending below. Never silently
            # select an old package when a required original input is missing.
            pass
    names += ['benchmarks/suites/capabilities-v1/sources/' + row['family_id'] + '.cpp'
              for row in declarations if row['profile']['kind'] != 'abi_probe']
    names = sorted(set(names))
    def hashes():
        values = {}
        for name in names:
            try:
                values[name] = sha((ROOT / name).read_bytes())
            except FileNotFoundError:
                values[name] = None
        return values
    before = hashes()
    result, states = [], []
    for row in declarations:
        state = preparation.inspect(row)
        states.append({'task_id': row['task_id'], 'status': state['status']})
        if state['status'] == 'ready':
            result.append(state['case'])
    if hashes() != before:
        raise RuntimeError('Capability source inputs changed during preparation inventory')
    pins = {'schema_version': 1, 'families': len({row['family_id'] for row in declarations}),
        'task_definitions': len(declarations), 'model_api_calls': 0, 'preparation_states': states,
        'tasks': [{key: row[key] for key in ('family_id', 'task_id', 'profile_id', 'public_package_sha256',
                                          'binary_sha256', 'capability_ids')} for row in result],
        'inputs_sha256': before}
    path = ROOT / 'benchmarks/config/capability-task-pins.json'
    _replace_json(path, pins)
    return pins
