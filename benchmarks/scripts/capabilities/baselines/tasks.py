"""Matched buffer packages over cached originals; no inference or new suite."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest
from grading.artifacts import digest, integer, object_fields
from grading.c_executor.context import host
from capabilities import buffers, tasks as common_tasks
from . import build, reference

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities'
HOST_SOURCE = build.MODULE + 'tasks.py'
LOADED = (ROOT / HOST_SOURCE).read_bytes()
FILES = ('target', 'prompt.txt', 'manifest.json', 'tools.json')
LANGUAGES = {'clang20': ('c', 'cpp'), 'rust': ('c', 'rust'), 'go': ('c', 'go')}
FILENAMES = {'c': 'solution.c', 'cpp': 'solution.cpp', 'rust': 'solution.rs', 'go': 'solution.go'}
CAPABILITIES = ['C1', 'C2', 'C3', 'C4']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def contract(declaration):
    if not isinstance(declaration, dict) or not isinstance(declaration.get('profile'), dict):
        raise ValueError('Invalid matched buffer declaration')
    profile = declaration['profile']
    object_fields(profile, ('id', 'kind', 'enabled', 'output_language', 'artifact_schema',
                            'original_compiler', 'condition'), ('families',))
    compiler, language, condition = profile['original_compiler'], profile['output_language'], profile['condition']
    if (any(not isinstance(item, str) for item in (compiler, language, condition))
            or compiler not in LANGUAGES or language not in LANGUAGES[compiler] or condition not in ('recover', 'codegen')
            or profile['kind'] != 'matched_buffer' or type(profile['enabled']) is not bool
            or profile['id'] != compiler + '_' + language + '_' + condition
            or declaration.get('family_id') not in build.FAMILIES or declaration.get('absent') is not False
            or declaration.get('task_id') != declaration['family_id'] + '_' + profile['id']
            or profile['artifact_schema'] != {FILENAMES[language]: 1}
            or type(profile['artifact_schema'][FILENAMES[language]]) is not int):
        raise ValueError('Unsupported matched buffer declaration')
    return compiler, language, condition


def sources(compiler, language):
    names = set(build.SOURCES) | set(reference.SOURCES) | {
        HOST_SOURCE, build.MODULE + 'build.py', build.MODULE + 'reference.py',
        'benchmarks/scripts/capabilities/tasks.py', 'benchmarks/scripts/capabilities/buffers.py',
        'benchmarks/scripts/capabilities/build.py', 'benchmarks/scripts/capabilities/native_abi/images.py',
        'benchmarks/scripts/grading/image_manifest.py', 'benchmarks/scripts/grading/c_executor/context.py'}
    if compiler == 'clang20': names.add(build.MODULE + 'tooling.py')
    if language == 'cpp': names.add('benchmarks/scripts/capabilities/cpp_buffer/contract.hpp')
    if language in ('rust', 'go'): names.add('benchmarks/config/' + language + '-buffer.json')
    return {name: sha((ROOT / name).read_bytes()) for name in sorted(names)}


def selected_sources(compiler, language, family):
    return {**sources(compiler, language), **{str(path.relative_to(ROOT)): sha(path.read_bytes())
                                            for path in build.inputs(compiler, family).values()}}


def cached_original(compiler, family):
    """Validate a build result without launching a compiler on a cache miss."""
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded matched task validator changed')
    selected, _, _, environment = build.action(compiler, family)
    local = action_cache.Cache()
    saved = local.lookup(selected)
    if not saved:
        raise RuntimeError('Matched original build is unavailable; prepare the selected task first')
    value = json.loads(local.get(saved['output_files']['result.json']['digest']))
    if value.get('runtime') != environment:
        raise ValueError('Matched original runtime binding differs')
    entry = build.validate(value, compiler)
    build._guard(compiler, family, selected.key)
    return {**value, 'build_id': selected.key, 'buffer_entry': entry['rva']}


def validate_oracle(compiler, family, original, cases, oracle):
    """Authenticate the reference CAS action; never execute an original here."""
    if not isinstance(cases, list) or not 1 <= len(cases) <= 128:
        raise ValueError('Invalid matched buffer cases')
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError('Invalid matched buffer case')
        buffers.initial(case)
        tags = case['capabilities']
        if (not isinstance(tags, list) or not tags or len(tags) > 4 or any(not isinstance(tag, str) for tag in tags)
                or len(tags) != len(set(tags))
                or any(tag not in CAPABILITIES for tag in tags)):
            raise ValueError('Invalid matched buffer capability tags')
    if len({case['id'] for case in cases}) != len(cases):
        raise ValueError('Duplicate matched buffer cases')
    binary = bytes.fromhex(original['public_hex'])
    language = 'cpp' if compiler == 'clang20' else compiler
    environment = {'runtime_image': image_manifest.load(language)['id'], **host()}
    selected = action_cache.Action('capability_baseline_reference_v1',
        {**{name: (ROOT / name).read_bytes() for name in reference.SOURCES}, 'target.elf': binary,
         'cases.json': action_cache.canonical(cases),
         'profile.json': action_cache.canonical(reference.profile(compiler))},
        arguments=['closed-matched-original-buffer-reference'], platform=environment, timeout_seconds=20)
    local = action_cache.Cache()
    saved = local.lookup(selected)
    if not saved:
        raise RuntimeError('Matched reference action is unavailable; prepare the selected task first')
    raw = json.loads(local.get(saved['output_files']['reference.json']['digest']))
    reference._validate(raw, binary, cases, environment)
    expected = {**raw, 'reference_id': selected.key, 'original_build_id': original['build_id'],
                'vectors_sha256': sha(action_cache.canonical(cases))}
    if not isinstance(oracle, dict) or {key: value for key, value in oracle.items() if key != 'cache_hit'} != expected:
        raise ValueError('Matched private oracle differs from its exact original reference action')
    return expected


def prompt(family, compiler, language, condition, entry):
    filename = FILENAMES[language]
    if language == 'cpp':
        from capabilities.cpp_buffer import runtime as cpp
        declaration = cpp.header()
        limits = 'Use freestanding, nonallocating C++20; no exceptions, RTTI, TLS or dynamic initialization. '
    elif language == 'c':
        declaration = buffers.HEADER
        limits = 'Use freestanding C17 with standard integer and size types. '
    elif language == 'rust':
        declaration = ('#[no_mangle]\npub unsafe extern "C" fn reconstructed(input: *const u8, input_len: usize, '
            'output: *mut u8, output_capacity: usize, output_len: *mut usize) -> i32;\n')
        limits = ('Use a Rust 2021 module without main or crate-level attributes. The trusted wrapper supplies '
                  'no_std and a panic handler. Nonallocating core operations are available. ')
    else:
        declaration = ('package main\nimport "unsafe"\nfunc reconstructed(input unsafe.Pointer, inputLen uintptr, '
            'output unsafe.Pointer, outputCapacity uintptr, outputLen *uintptr) int32\n')
        limits = ('Submit package main with this function and no main. Imports are limited to unsafe, encoding/binary, '
                  'math, math/bits, sort, slices and cmp; no compiler directives. Initialization is not executed. '
                  'The host supplies Go ABIInternal arguments, R14 context and spill slots; image writes are forbidden. ')
    original_abi = ('Go ABIInternal: input in RAX, length in RBX, output in RCX, capacity in RDI, '
                    'output-length pointer in RSI; signed status in EAX.' if compiler == 'go'
                    else 'The original buffer endpoint uses the x86-64 SysV C integer/pointer boundary.')
    text = (f'Recover the behavior of this optimized stripped {compiler} target as {language}.\n'
        f'The known buffer-function RVA is {entry}, relative to the lowest PT_LOAD page. {original_abi}\n'
        f'Submit {filename} with this public signature:\n' + declaration +
        'Input contains 0..513 arbitrary bytes; output capacity is 0..520. All pointers are non-null, including '
        'zero lengths. The output-length pointer names one aligned 64-bit size in separate memory. Input and output '
        'can overlap. Read only the input range, initial output-capacity range and output length; write only output '
        'capacity and output length. Preserve all other bytes and unused capacity. Recover exact signed status, '
        'length, output bytes and error effects. ' + limits +
        'No OS services, syscalls, heap/GC, inline assembly or embedded executable. Source limit is 262144 UTF-8 '
        'bytes. The host captures actual returns and memory effects. One candidate invocation receives no target, '
        'future cases or expected answers. Produce readable, well-formatted code with descriptive helpers.\n'
        'Use only supplied static REA/Ghidra tools; no original execution, compiler feedback, source access or '
        'previous attempts. Close the binary session when finished.\n')
    if condition == 'codegen':
        text += ('\nIndependent full-semantics generation control; reverse engineering is not required. '
            'For input longer than512 set output length to0, return -2 and preserve output. Otherwise compute the '
            '64-bit value below. Set output length to8+input length. If capacity is insufficient return -1 without '
            'changing output. On success return0 and emit the little-endian 8-byte value followed by each original '
            'input byte XOR the value byte at index modulo8. Snapshot overlapping input before output writes. '
            'Unsigned 64-bit arithmetic wraps modulo2^64 unless otherwise stated.\nValue formula: '
            + common_tasks.SEMANTICS[family] + '\n')
    return text + common_tasks.TRANSPORT.replace('```solution.c', '```' + filename)


def freeze(declaration, settings, *, publish=True):
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded matched task packager changed')
    compiler, language, condition = contract(declaration)
    family = declaration['family_id']
    before = selected_sources(compiler, language, family)
    original = build.build(compiler, family)
    cases = reference.vectors(family)
    oracle = reference.observe(compiler, family, cases)
    oracle = validate_oracle(compiler, family, original, cases, oracle)
    binary = bytes.fromhex(original['public_hex'])
    source_language = 'cpp' if compiler == 'clang20' else compiler
    public_id = 'task_' + sha(binary + declaration['profile']['id'].encode())[:20]
    metadata = {'schema_version': 1, 'family_id': family, 'build_id': original['build_id'],
        'task_id': public_id, 'capability_ids': CAPABILITIES, 'source_language': source_language,
        'output_language': language, 'artifact_schema': {FILENAMES[language]: 1}, 'binary_sha256': sha(binary),
        'architecture': 'x86_64', 'hint_profile': 'known_matched_buffer_entry_v1',
        'entry_rva': original['buffer_entry'], 'condition': condition, 'original_compiler': compiler}
    files = {'target': binary, 'manifest.json': action_cache.canonical(metadata),
        'prompt.txt': prompt(family, compiler, language, condition, original['buffer_entry']).encode(),
        'tools.json': (ROOT / settings['analysis_tools_file']).read_bytes()}
    hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    package = sha(action_cache.canonical(hashes))
    directory = CACHE / 'task-freezes' / package
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pending-', dir=directory.parent) as temporary:
            work = Path(temporary)
            for name, raw in files.items(): (work / name).write_bytes(raw)
            work.rename(directory)
    if (directory.is_symlink() or {path.name for path in directory.iterdir()} != set(FILES)
            or any((directory / name).is_symlink() or (directory / name).read_bytes() != raw for name, raw in files.items())):
        raise ValueError('Matched public package changed')
    spec = {'schema_version': 1, 'kind': 'matched_buffer', 'family_id': family,
        'original_compiler': compiler, 'output_language': language, 'condition': condition,
        'binary_sha256': sha(binary), 'original_build_id': original['build_id'], 'cases': cases, 'oracle': oracle}
    raw = action_cache.canonical(spec)
    spec_id = sha(raw)
    path = CACHE / 'grade-specs' / (spec_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists(): path.write_bytes(raw)
    if (path.is_symlink() or path.read_bytes() != raw or selected_sources(compiler, language, family) != before
            or (ROOT / HOST_SOURCE).read_bytes() != LOADED):
        raise ValueError('Matched task inputs changed during freezing')
    record = {'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': family, 'build_id': original['build_id'], 'capability_ids': CAPABILITIES,
        'source_language': source_language, 'output_language': language, 'artifact_schema': {FILENAMES[language]: 1},
        'kind': 'matched_buffer', 'profile_id': declaration['profile']['id'], 'original_compiler': compiler,
        'hint_profile': metadata['hint_profile'], 'binary_sha256': sha(binary), 'public_package_sha256': package,
        'public_directory': str(directory.relative_to(ROOT)), 'public_files_sha256': hashes,
        'grading_spec': str(path.relative_to(ROOT)), 'grading_spec_sha256': spec_id, 'source_inputs_sha256': before}
    if publish: common_tasks.publish_index(record)
    return record


def validate_private(value):
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded matched task validator changed')
    object_fields(value, ('schema_version', 'kind', 'family_id', 'original_compiler', 'output_language',
                         'condition', 'binary_sha256', 'original_build_id', 'cases', 'oracle'))
    integer(value['schema_version'], 1, 1)
    compiler, language = value['original_compiler'], value['output_language']
    if (not isinstance(compiler, str) or not isinstance(language, str)
            or value['kind'] != 'matched_buffer' or compiler not in LANGUAGES or language not in LANGUAGES[compiler]
            or value['family_id'] not in build.FAMILIES or value['condition'] not in ('recover', 'codegen')):
        raise ValueError('Unregistered matched private contract')
    digest(value['binary_sha256'])
    digest(value['original_build_id'])
    original = cached_original(compiler, value['family_id'])
    if (original['build_id'] != value['original_build_id']
            or sha(bytes.fromhex(original['public_hex'])) != value['binary_sha256']):
        raise ValueError('Matched private contract differs from its original')
    validate_oracle(compiler, value['family_id'], original, value['cases'], value['oracle'])
    return original


def validate_frozen(value, declaration, settings):
    compiler, language, condition = contract(declaration)
    family = declaration['family_id']
    for name, expected in {'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': family, 'kind': 'matched_buffer', 'profile_id': declaration['profile']['id'],
        'original_compiler': compiler, 'output_language': language, 'capability_ids': CAPABILITIES,
        'source_language': 'cpp' if compiler == 'clang20' else compiler,
        'artifact_schema': {FILENAMES[language]: 1}, 'hint_profile': 'known_matched_buffer_entry_v1'}.items():
        if action_cache.canonical(value.get(name)) != action_cache.canonical(expected):
            raise ValueError('Matched frozen contract differs: ' + name)
    package, spec_id = digest(value['public_package_sha256']), digest(value['grading_spec_sha256'])
    directory = ROOT.joinpath(*action_cache.relative(value['public_directory']).parts)
    path = ROOT.joinpath(*action_cache.relative(value['grading_spec']).parts)
    if (directory.is_symlink() or directory.resolve() != (CACHE / 'task-freezes' / package).resolve()
            or path.is_symlink() or path.resolve() != (CACHE / 'grade-specs' / (spec_id + '.json')).resolve()
            or {item.name for item in directory.iterdir()} != set(FILES)
            or any((directory / name).is_symlink() for name in FILES)):
        raise ValueError('Matched package or private-spec path differs')
    files = {name: (directory / name).read_bytes() for name in FILES}
    hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    if hashes != value['public_files_sha256'] or sha(action_cache.canonical(hashes)) != package:
        raise ValueError('Matched package bytes changed')
    raw = path.read_bytes()
    if sha(raw) != spec_id:
        raise ValueError('Matched private specification changed')
    # Source revisions make a preparation stale, not corrupt. Authenticate the
    # historical bytes first, then let prepare() produce a new action/index.
    recorded_sources = value.get('source_inputs_sha256')
    if not isinstance(recorded_sources, dict) or not recorded_sources:
        raise ValueError('Invalid matched source input manifest')
    for name, source_digest in recorded_sources.items():
        action_cache.relative(name)
        digest(source_digest)
    if recorded_sources != selected_sources(compiler, language, family):
        raise RuntimeError('Matched source inputs changed; prepare the selected task first')
    spec = json.loads(raw)
    original = validate_private(spec)
    if (spec['family_id'] != family or spec['original_compiler'] != compiler or spec['output_language'] != language
            or spec['condition'] != condition or spec['original_build_id'] != value['build_id']
            or spec['binary_sha256'] != value['binary_sha256']
            or files['target'] != bytes.fromhex(original['public_hex'])
            or files['tools.json'] != (ROOT / settings['analysis_tools_file']).read_bytes()
            or files['prompt.txt'] != prompt(family, compiler, language, condition, original['buffer_entry']).encode()):
        raise ValueError('Matched frozen selection differs from its prepared original or contract')
    metadata = json.loads(files['manifest.json'])
    expected = {'schema_version': 1, 'family_id': family, 'build_id': original['build_id'],
        'task_id': 'task_' + sha(files['target'] + declaration['profile']['id'].encode())[:20],
        'capability_ids': CAPABILITIES, 'source_language': value['source_language'], 'output_language': language,
        'artifact_schema': {FILENAMES[language]: 1}, 'binary_sha256': value['binary_sha256'], 'architecture': 'x86_64',
        'hint_profile': value['hint_profile'], 'entry_rva': original['buffer_entry'], 'condition': condition,
        'original_compiler': compiler}
    if action_cache.canonical(metadata) != action_cache.canonical(expected) or Path(__file__).read_bytes() != LOADED:
        raise ValueError('Matched public manifest or loaded task code differs')
    return value
