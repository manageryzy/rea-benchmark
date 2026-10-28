"""Four matched C/C++ lifecycle conditions in the existing capability suite."""
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest
from grading.artifacts import digest, identifier, integer, object_fields
from . import build, lifecycle, lifecycle_build

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities'
PROFILES = {
    'c_lifecycle_recover': ('c_lifecycle', 'c', 'solution.c'),
    'c_lifecycle_codegen': ('c_lifecycle', 'c', 'solution.c'),
    'cpp_lifecycle_recover': ('cpp_lifecycle', 'cpp', 'solution.cpp'),
    'cpp_lifecycle_codegen': ('cpp_lifecycle', 'cpp', 'solution.cpp')}
CALL_CONTRACT = {'version': 1, 'state_bytes': 64, 'state_alignment': 8,
                 'output_bytes': 8, 'output_alignment': 8, 'interleaved_instances': 2,
                 'operations': {'create': 0, 'step': 1, 'reset': 2, 'destroy': 3, 'read': 4}}
COMMON_SEMANTICS = (
    'Operations above4 return -1 first. Create on an active instance returns -5. '
    'Any other operation on an inactive instance returns -2. These checks precede parameter checks. '
    'Create and reset then validate limit; an invalid limit returns -1. '
    'Create/reset initialize count to0. Destroy deactivates that instance. '
    'Every successful operation returns0. Read/destroy ignore value and limit; step ignores limit. '
    'Successful step/read outputs (uint64(checksum)<<32) OR count. '
    'All checksum/count arithmetic wraps modulo2^32. Every other operation and every error preserves output. '
    'Every error leaves logical instance state unchanged. '
    'Opaque internal representations may differ. Each independent scenario starts with zeroed instance storage; '
    'otherwise only an operation may change the selected instance. No other instance or guard bytes may change. ')
SEMANTICS = {
    'callback_policy': (
        'Valid limits are1..8 and represent remaining quota. Step values above255 return -1 '
        'before checking quota; an exhausted step returns -4. Read immediately writes the encoded value to output without callbacks. '
        'Create/reset invoke callback(operation,value,limit); step invokes callback(1,value,count before increment); '
        'destroy invokes callback(3,checksum,count). All callback arguments are uint64 zero extensions of uint32 values. '
        'Each such operation invokes its callback exactly once after all its parameter/quota checks. '
        'A UINT64_MAX response returns -3 and commits no state or output, including on create/reset/destroy. '
        'On successful create/reset set checksum=value XOR low32(response), count=0 and remaining=limit. '
        'Successful step consumes one quota, increments count and sets checksum=(checksum*33) XOR value XOR low32(response). '
        'Destroy requires a successful callback before deactivating. '),
    'session_state': (
        'Valid limits are1..31 and select a multiplier. No operation calls the callback. '
        'Successful create/reset sets checksum=value, count=0 and multiplier=limit. '
        'Step accepts the full uint32 value domain, increments count, then sets '
        'checksum=checksum*multiplier+value+count. Read preserves state and writes the encoded value to output. ')}


def profile_contract(profile):
    value = PROFILES.get(profile['id'])
    if value is None or profile['kind'] != value[0] or profile['output_language'] != value[1] or profile['artifact_schema'] != {value[2]: 1}:
        raise ValueError('Unregistered lifecycle condition or output contract')
    return value


def validate_manifest(value):
    object_fields(value, ('schema_version', 'family_id', 'build_id', 'task_id', 'capability_ids',
        'source_language', 'output_language', 'artifact_schema', 'binary_sha256', 'architecture',
        'hint_profile', 'entry_rva', 'condition', 'call_contract'))
    integer(value['schema_version'], 1, 1)
    for name in ('family_id', 'task_id'): identifier(value[name])
    for name in ('build_id', 'binary_sha256'): digest(value[name])
    integer(value['entry_rva'])
    profile = PROFILES.get(value['condition'])
    if (value['family_id'] not in lifecycle.FAMILIES or profile is None
            or value['capability_ids'] != lifecycle.FAMILIES[value['family_id']]
            or value['source_language'] != 'cpp' or value['output_language'] != profile[1]
            or value['artifact_schema'] != {profile[2]: 1} or type(value['artifact_schema'][profile[2]]) is not int
            or value['architecture'] != 'x86_64' or value['hint_profile'] != 'known_lifecycle_entry_v1'
            or action_cache.canonical(value['call_contract']) != action_cache.canonical(CALL_CONTRACT)):
        raise ValueError('Unsupported public lifecycle task contract')
    return value


def prompt(metadata):
    validate_manifest(metadata)
    _, language, filename = PROFILES[metadata['condition']]
    if language == 'cpp':
        from .cpp_lifecycle import runtime as compiler
        declaration, standard = compiler.header(), 'C++20'
        permitted = ('Header-only nonallocating standard-library algorithms, arrays, spans, '
                     'lambdas and local class methods are supported. No exceptions, RTTI, TLS or dynamic initialization. ')
    else:
        declaration, standard, permitted = lifecycle.HEADER, 'C17', ''
    text = (f'Recover the behavior of the supplied optimized stripped C++ lifecycle interface as {standard}.\n'
        f'The known function RVA is {metadata["entry_rva"]}; RVAs start at the lowest PT_LOAD page.\n'
        f'Submit {filename} with this exact declaration and no main function:\n' + declaration +
        'Operations are create=0, step=1, reset=2, destroy=3 and read=4; other uint32 operation values are possible. '
        'value and limit can contain any uint32; callbacks return any uint64. '
        'State is a non-null pointer to64 initially zero bytes aligned to8. Its representation is opaque. '
        'Output is a separate non-null aligned uint64; it never aliases state. '
        'Read/write only the selected state allocation and output; preserve all other memory, including another instance. '
        'Two independently created instances may receive interleaved calls. '
        'Recover exact signed status, output/error effects, callback arguments/order/count, and persistent lifecycle behavior. '
        'Both your instance storage and writable guest globals persist within a scenario; independent scenarios start fresh. '
        'Do not depend on internal state bytes matching the original; equivalent representations are permitted. ' + permitted +
        'Use freestanding code with no OS, heap, inline assembly, external library or embedded executable. '
        'The memcpy/memmove/memset/memcmp/strlen helpers are available. Candidate source limit is262144 UTF-8 bytes. '
        'The candidate worker receives only the current invocation, its own prior state and current callback responses. '
        'It receives no original ELF, hidden future inputs or expected answers. '
        'Use only supplied static REA/Ghidra tools; no source, previous answers, target execution or compiler feedback. '
        'Close the binary when finished. Produce human-readable, well-formatted code and descriptive helpers.\n')
    if metadata['condition'].endswith('_codegen'):
        text += ('\nIndependent full-semantics generation condition. No reverse engineering is required. '
                 + COMMON_SEMANTICS + SEMANTICS[metadata['family_id']] + '\n')
    return text + ('Return exactly one Markdown code block labelled ' + filename + '. '
                   'Brief evidence prose may precede it; do not emit any other code blocks.\n')


def original_sources(family):
    names = set(build.SOURCES) | {'benchmarks/scripts/capabilities/lifecycle_build.py',
        'benchmarks/suites/capabilities-v1/sources/lifecycle/common.hpp',
        'benchmarks/suites/capabilities-v1/sources/lifecycle/' + family + '.cpp'}
    return {name: lifecycle.sha((ROOT / name).read_bytes()) for name in sorted(names)}


def freeze(declaration, settings, *, publish=True):
    profile_contract(declaration['profile'])
    family, profile = declaration['family_id'], declaration['profile']
    target = lifecycle.original(family)
    binary = bytes.fromhex(target['public_hex'])
    metadata = validate_manifest({'schema_version': 1, 'family_id': family, 'build_id': target['build_id'],
        'task_id': 'task_' + lifecycle.sha(binary + profile['id'].encode())[:20],
        'capability_ids': lifecycle.FAMILIES[family], 'source_language': 'cpp',
        'output_language': profile['output_language'], 'artifact_schema': profile['artifact_schema'],
        'binary_sha256': lifecycle.sha(binary), 'architecture': 'x86_64', 'hint_profile': 'known_lifecycle_entry_v1',
        'entry_rva': target['entry_rva'], 'condition': profile['id'], 'call_contract': CALL_CONTRACT})
    files = {'target': binary, 'manifest.json': action_cache.canonical(metadata),
             'prompt.txt': prompt(metadata).encode(), 'tools.json': (ROOT / settings['analysis_tools_file']).read_bytes()}
    hashes = {name: lifecycle.sha(raw) for name, raw in sorted(files.items())}
    package_id = lifecycle.sha(action_cache.canonical(hashes))
    destination = CACHE / 'task-freezes' / package_id
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pending-', dir=destination.parent) as temporary:
            work = Path(temporary)
            for name, raw in files.items(): (work / name).write_bytes(raw)
            work.rename(destination)
    if destination.is_symlink() or {path.name for path in destination.iterdir()} != set(files) or any(
            (destination / name).is_symlink() or (destination / name).read_bytes() != raw for name, raw in files.items()):
        raise ValueError('Frozen lifecycle public package changed')
    scenarios = lifecycle.vectors(family)
    oracle = lifecycle.reference(target, scenarios)
    spec = {'schema_version': 1, 'kind': profile['kind'], 'binary_sha256': metadata['binary_sha256'],
        'contract_version': 'lifecycle-observable-v1', 'family_id': family, 'scenarios': scenarios,
        'oracle': {name: value for name, value in oracle.items() if name != 'cache_hit'}}
    raw = action_cache.canonical(spec)
    spec_id = lifecycle.sha(raw)
    path = CACHE / 'grade-specs' / (spec_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists(): path.write_bytes(raw)
    if path.is_symlink() or path.read_bytes() != raw: raise ValueError('Frozen lifecycle grading spec changed')
    record = {'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': family, 'build_id': target['build_id'], 'capability_ids': metadata['capability_ids'],
        'source_language': 'cpp', 'output_language': profile['output_language'], 'artifact_schema': profile['artifact_schema'],
        'kind': profile['kind'], 'profile_id': profile['id'], 'hint_profile': metadata['hint_profile'],
        'binary_sha256': metadata['binary_sha256'], 'public_package_sha256': package_id,
        'public_directory': str(destination.relative_to(ROOT)), 'public_files_sha256': hashes,
        'grading_spec': str(path.relative_to(ROOT)), 'grading_spec_sha256': spec_id,
        'original_sources_sha256': original_sources(family), 'compiler_image': image_manifest.load('cpp')['id']}
    if publish:
        from .tasks import publish_index
        publish_index(record)
    return record


def validate_frozen(value, declaration, settings):
    profile_contract(declaration['profile'])
    profile, family = declaration['profile'], declaration['family_id']
    if (value.get('id') != declaration['task_id'] or value.get('task_id') != declaration['task_id']
            or value.get('family_id') != family or value.get('profile_id') != profile['id']
            or value.get('source_language') != 'cpp' or value.get('output_language') != profile['output_language']
            or value.get('capability_ids') != lifecycle.FAMILIES[family] or value.get('hint_profile') != 'known_lifecycle_entry_v1'
            or value.get('original_sources_sha256') != original_sources(family)
            or value.get('compiler_image') != image_manifest.load('cpp')['id']
            or value.get('build_id') != lifecycle_build.action(family)[0].key):
        raise RuntimeError('Lifecycle original sources/compiler changed; run make prepare-capabilities')
    directory = ROOT.joinpath(*action_cache.relative(value['public_directory']).parts)
    if directory.is_symlink() or not directory.resolve().is_relative_to((CACHE / 'task-freezes').resolve()) or {
            path.name for path in directory.iterdir()} != {'target', 'prompt.txt', 'manifest.json', 'tools.json'}:
        raise ValueError('Lifecycle package escapes its exact frozen boundary')
    files = {name: (directory / name).read_bytes() for name in ('target', 'prompt.txt', 'manifest.json', 'tools.json')}
    if any((directory / name).is_symlink() for name in files): raise ValueError('Lifecycle package contains a symlink')
    hashes = {name: lifecycle.sha(raw) for name, raw in sorted(files.items())}
    if hashes != value['public_files_sha256'] or lifecycle.sha(action_cache.canonical(hashes)) != value['public_package_sha256']:
        raise ValueError('Frozen lifecycle public package changed')
    metadata = validate_manifest(json.loads(files['manifest.json']))
    if (files['prompt.txt'] != prompt(metadata).encode() or files['tools.json'] != (ROOT / settings['analysis_tools_file']).read_bytes()
            or value['artifact_schema'] != profile['artifact_schema'] or value['kind'] != profile['kind']
            or metadata['condition'] != profile['id'] or metadata['build_id'] != value['build_id']
            or metadata['binary_sha256'] != value['binary_sha256'] or metadata['family_id'] != family):
        raise RuntimeError('Lifecycle public task contract changed; run make prepare-capabilities')
    return value
