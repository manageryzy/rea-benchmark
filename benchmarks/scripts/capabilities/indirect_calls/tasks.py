"""T6 public premises, immutable packages and private original-call contracts."""
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest
from grading.artifacts import object_fields, digest, identifier, integer
from capabilities import elf
from . import runtime, contracts

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities'
FILES = ('target', 'prompt.txt', 'manifest.json', 'tools.json')


def compiler_metadata():
    record = image_manifest.load('cpp')
    return {'family': 'gcc', 'version': record['manifest']['compiler']['version'].splitlines()[0],
            'image_sha256': record['id'], 'standard': 'c++20', 'optimization': 'O2',
            'library': 'freestanding_no_native_runtime', 'link': 'static_ET_EXEC',
            'architecture': 'x86_64'}


def public_facts():
    return {
        'signature': 'int64_t original_callee(CallRoot *root, uint64_t nonce)',
        'root': {'size': 56, 'alignment': 8, 'receiver_offsets': [0, 8],
                 'callback_offsets': [16, 24], 'context_offsets': [32, 40],
                 'seal_offset': 48, 'pointer_bytes': 8},
        'callback_signature': 'uint64_t callback(void *context, uint64_t method_result)',
        'method_signature': 'uint64_t method(void *interface_receiver, uint64_t nonce)',
        'implementation_signature': 'uint64_t implementation(void *owner, uint64_t nonce)',
        'vptr_offset': 0, 'vtable_slots': 4, 'operation': 'nonce & 1',
        'adjustment_probe': '(nonce & 2) != 0', 'missing_argument_result': -9,
        'selections_are_permutations': True,
    }


def manifest(target):
    symbols = elf.ELF(bytes.fromhex(target['private_hex'])).symbols()
    return {
        'schema_version': 1, 'family_id': 'callback_policy', 'build_id': target['build_id'],
        'task_id': 'task_' + runtime.sha(bytes.fromhex(target['public_hex']) + b'indirect_recover')[:20],
        'capability_ids': ['T6'], 'source_language': 'cpp', 'output_language': None,
        'artifact_schema': {'calls.json': 1}, 'binary_sha256': target['binary_sha256'],
        'architecture': 'x86_64', 'hint_profile': 'known_indirect_endpoints_v1',
        'condition': 'indirect_recover', 'known_abi': 'sysv_amd64',
        'entry_points': {**{n: target['entries'][n] for n in ('original_callee', 'original_caller')},
                         **{n: symbols[n]['rva'] for n in ('original_leaf_0', 'original_leaf_1')}},
        'compiler': compiler_metadata(), 'given_facts': public_facts(),
    }


def validate_manifest(value):
    object_fields(value, ('schema_version', 'family_id', 'build_id', 'task_id', 'capability_ids',
                          'source_language', 'output_language', 'artifact_schema', 'binary_sha256',
                          'architecture', 'hint_profile', 'condition', 'known_abi', 'entry_points',
                          'compiler', 'given_facts'))
    integer(value['schema_version'], 1, 1)
    identifier(value['task_id'])
    digest(value['build_id'])
    digest(value['binary_sha256'])
    expected = {'family_id': 'callback_policy', 'capability_ids': ['T6'], 'source_language': 'cpp',
                'output_language': None, 'artifact_schema': {'calls.json': 1}, 'architecture': 'x86_64',
                'condition': 'indirect_recover', 'hint_profile': 'known_indirect_endpoints_v1',
                'known_abi': 'sysv_amd64', 'given_facts': public_facts(), 'compiler': compiler_metadata()}
    if (any(action_cache.canonical(value[name]) != action_cache.canonical(item)
            for name, item in expected.items()) or type(value['artifact_schema']['calls.json']) is not int):
        raise ValueError('Unregistered or answer-bearing public indirect-call condition')
    names = ('original_callee', 'original_caller', 'original_leaf_0', 'original_leaf_1')
    object_fields(value['entry_points'], names)
    for number in value['entry_points'].values():
        integer(number)
    if len(set(value['entry_points'].values())) != len(names):
        raise ValueError('Indirect-call public endpoints overlap')
    return value


def prompt():
    return '''Recover the indirect/object-call relationships of two operations from this optimized stripped C++ binary. Use only the supplied static REA/Ghidra tools: no target execution, shell, source, prior answers or compiler feedback. RVAs are relative to the lowest PT_LOAD page. The manifest identifies the original callee/caller and each operation's normalized implementation endpoint. All call signatures, the complete root layout and the vptr convention are supplied premises; they do not receive type/layout/ABI-recovery credit.

CallRoot contains two opaque interface receivers at offsets 0/8, two callback function pointers at offsets 16/24, two opaque callback contexts at offsets 32/40 and a preserved uint64 seal at offset 48. Its size/alignment are 56/8 and pointers occupy eight bytes. The operation is nonce & 1. Each operation selects a distinct field in each array. Infer those receiver/function/context associations from actual calls. A receiver's first word is a vptr at the known vtable address point, with four method slots numbered 0..3. Recover the selected method slot and signed byte adjustment from interface receiver to the supplied implementation endpoint's owner. Nominal C++ class names, inheritance declarations and opaque object internals are not requested.

The original operation requires a nonnull receiver, callback and context; a missing required argument returns int64 -9 without changing object/context state. Otherwise it calls the selected virtual method with (interface_receiver, nonce), calls the selected callback with (context, method_result), and returns the callback's uint64 bits reinterpreted as int64. The callback and implementation bodies remain frozen originals. The seal and protected descriptors/guards must remain preserved.

Submit calls.json with schema_version:1, binary_sha256:<manifest digest> and operations:<two entries>. Every entry has operation:<0 or 1>, receiver_slot:<0 or 1>, callback_slot:<0 or 1>, context_slot:<0 or 1>, virtual_slot:<0..3>, this_adjustment:<signed multiple of eight between -128 and 128>. Both operation IDs and each array's selected slots must be unique. Entry order has no meaning. No source, expressions, identifiers, extra fields or arbitrary function addresses are allowed.

Descriptions drive real calls in two directions. The frozen caller invokes a generated replacement. With nonce & 2 clear, it loads and calls the claimed virtual slot using the interface receiver. With that bit set, it adds the claimed this_adjustment to the interface receiver and calls the supplied original implementation for that operation. Both paths must match the original virtual call. A separate generated bridge constructs the known root from canonical opaque fixtures using the claimed associations, then invokes the frozen original callee. The slot/adjustment probes are complementary to this bridge; every check in both directions must pass. Wrong hypotheses may cause machine faults and do not earn behavioral credit merely by passing schema admission.

The host records return bits, both object states, both context states, callback counts and preservation checks after a real return. Guest stdout cannot supply a score. Native calibration, emulator execution and an independent semantic reference validate the originals. These are supplied machine-call premises, not a request to infer the callback's transformation or to reconstruct the root's known layout. Explain the binary evidence for your associations, then emit exactly one fenced calls.json artifact.
'''


def freeze(declaration, settings, *, publish=True):
    variant = declaration['profile']['variant']
    target = runtime.original(variant)
    metadata = manifest(target)
    files = {'target': bytes.fromhex(target['public_hex']), 'prompt.txt': prompt().encode(),
             'manifest.json': action_cache.canonical(metadata),
             'tools.json': (ROOT / settings['analysis_tools_file']).read_bytes()}
    hashes = {name: runtime.sha(raw) for name, raw in sorted(files.items())}
    package = runtime.sha(action_cache.canonical(hashes))
    destination = CACHE / 'task-freezes' / package
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pending-', dir=destination.parent) as temporary:
            work = Path(temporary)
            for name, raw in files.items():
                (work / name).write_bytes(raw)
            work.rename(destination)
    if (destination.is_symlink() or {p.name for p in destination.iterdir()} != set(FILES)
            or any((destination / name).is_symlink() or (destination / name).read_bytes() != raw
                   for name, raw in files.items())):
        raise ValueError('Frozen indirect-call public package changed')
    spec = {'schema_version': 1, 'kind': 'indirect_call', 'binary_sha256': target['binary_sha256'],
            'condition': 'indirect_recover', 'target': {k: v for k, v in target.items() if k != 'cache_hit'},
            'nonces': list(contracts.NONCES)}
    raw = action_cache.canonical(spec)
    spec_id = runtime.sha(raw)
    path = CACHE / 'grade-specs' / (spec_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    if path.is_symlink() or path.read_bytes() != raw:
        raise ValueError('Frozen indirect-call grading spec changed')
    sources = {name: runtime.sha(raw) for name, raw in runtime.sources('original', variant).items()}
    sources[str(runtime.POLICY.relative_to(ROOT))] = runtime.sha(runtime.POLICY.read_bytes())
    record = {
        'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': 'callback_policy', 'lineage_id': 'indirect_call_interfaces_v1',
        'build_id': target['build_id'], 'capability_ids': ['T6'], 'source_language': 'cpp',
        'output_language': None, 'artifact_schema': {'calls.json': 1}, 'kind': 'indirect_call',
        'variant': variant, 'profile_id': declaration['profile']['id'],
        'hint_profile': metadata['hint_profile'], 'binary_sha256': target['binary_sha256'],
        'public_package_sha256': package, 'public_directory': str(destination.relative_to(ROOT)),
        'public_files_sha256': hashes, 'grading_spec': str(path.relative_to(ROOT)),
        'grading_spec_sha256': spec_id, 'original_sources_sha256': sources,
        'compiler_image': target['runtime']['compiler_image'],
    }
    if publish:
        from ..tasks import publish_index
        publish_index(record)
    return record


def validate_original_binding(target):
    runtime.validate_target(target)


def validate_frozen(value, declaration, settings):
    variant = declaration['profile']['variant']
    expected_sources = set(runtime.sources('original', variant)) | {str(runtime.POLICY.relative_to(ROOT))}
    expected = {'id': declaration['task_id'], 'task_id': declaration['task_id'],
                'family_id': 'callback_policy', 'lineage_id': 'indirect_call_interfaces_v1',
                'profile_id': declaration['profile']['id'], 'variant': variant, 'kind': 'indirect_call',
                'artifact_schema': {'calls.json': 1}, 'capability_ids': ['T6'], 'output_language': None,
                'source_language': 'cpp', 'compiler_image': image_manifest.load('cpp')['id'],
                'hint_profile': 'known_indirect_endpoints_v1',
                'build_id': runtime.compile_action('original', {'mode': 'original'}, variant)[0].key}
    if (any(value.get(name) != item for name, item in expected.items())
            or set(value.get('original_sources_sha256', {})) != expected_sources):
        raise RuntimeError('Indirect-call compiler/profile selection changed; run make prepare-capabilities')
    if any(runtime.sha((ROOT / name).read_bytes()) != h for name, h in value['original_sources_sha256'].items()):
        raise RuntimeError('Original indirect-call sources changed; run make prepare-capabilities')
    directory = ROOT.joinpath(*action_cache.relative(value['public_directory']).parts)
    digest(value['public_package_sha256'])
    if (directory.is_symlink() or directory != CACHE / 'task-freezes' / value['public_package_sha256']
            or not directory.resolve().is_relative_to((CACHE / 'task-freezes').resolve())
            or {p.name for p in directory.iterdir()} != set(FILES)
            or set(value['public_files_sha256']) != set(FILES)):
        raise ValueError('Indirect-call package escapes its exact frozen boundary')
    files = {name: (directory / name).read_bytes() for name in FILES}
    hashes = {name: runtime.sha(raw) for name, raw in sorted(files.items())}
    if (any((directory / name).is_symlink() for name in FILES)
            or hashes != value['public_files_sha256']
            or runtime.sha(action_cache.canonical(hashes)) != value['public_package_sha256']):
        raise ValueError('Indirect-call public package changed')
    spec_path = ROOT.joinpath(*action_cache.relative(value['grading_spec']).parts)
    spec_id = value['grading_spec_sha256']
    digest(spec_id)
    if (spec_path.is_symlink() or spec_path.resolve().parent != (CACHE / 'grade-specs').resolve()
            or spec_path.name != spec_id + '.json'):
        raise ValueError('Indirect-call grading spec escapes its frozen boundary')
    spec_raw = spec_path.read_bytes()
    if runtime.sha(spec_raw) != spec_id:
        raise ValueError('Indirect-call grading spec changed')
    spec = json.loads(spec_raw)
    object_fields(spec, ('schema_version', 'kind', 'binary_sha256', 'condition', 'target', 'nonces'))
    if (type(spec['schema_version']) is not int or spec['schema_version'] != 1
            or spec['kind'] != 'indirect_call' or spec['condition'] != 'indirect_recover'
            or not isinstance(spec['nonces'], list) or spec['target']['variant'] != variant
            or spec['binary_sha256'] != value['binary_sha256']):
        raise ValueError('Indirect-call private and public contracts disagree')
    runtime.vectors(spec['nonces'])
    validate_original_binding(spec['target'])
    metadata = validate_manifest(json.loads(files['manifest.json']))
    if (action_cache.canonical(metadata) != action_cache.canonical(manifest(spec['target']))
            or metadata['build_id'] != value['build_id']
            or metadata['binary_sha256'] != value['binary_sha256']
            or metadata['task_id'] != 'task_' + runtime.sha(files['target'] + b'indirect_recover')[:20]
            or runtime.sha(files['target']) != value['binary_sha256']
            or files['prompt.txt'] != prompt().encode()
            or files['tools.json'] != (ROOT / settings['analysis_tools_file']).read_bytes()):
        raise ValueError('Indirect-call public premises/endpoints differ from the frozen original')
    return value
