"""T3 public packages and private original-endpoint specifications."""
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest
from grading.artifacts import object_fields, digest, identifier, integer
from . import runtime, contracts

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities'
FILES = ('target', 'prompt.txt', 'manifest.json', 'tools.json')


def compiler_metadata():
    record = image_manifest.load('cpp')
    return {'family': 'gcc', 'version': record['manifest']['compiler']['version'].splitlines()[0],
            'image_sha256': record['id'], 'standard': 'c++20', 'optimization': 'O2',
            'library': 'freestanding_no_native_runtime', 'link': 'static_ET_EXEC', 'architecture': 'x86_64'}


def public_facts():
    # Physical offsets, pointer depth, extent bindings and alias policy are
    # measured. This projection never reads a selected private gold description.
    return {'signature': 'int64_t original_callee(void *object, uint64_t nonce)',
            'scalar_roles': ['capacity', 'length', 'produced'], 'scalar_kind': 'u32',
            'pointer_roles': ['destination', 'source'], 'pointer_bits': 64,
            'maximum_object_bytes': 128, 'maximum_buffer_extent': 32}


def manifest(target):
    return {'schema_version': 1, 'family_id': 'variable_objects', 'build_id': target['build_id'],
        'task_id': 'task_' + runtime.sha(bytes.fromhex(target['public_hex']) + b'pointer_recover')[:20],
        'capability_ids': ['T3'], 'source_language': 'cpp', 'output_language': None,
        'artifact_schema': {'types.json': 1}, 'binary_sha256': target['binary_sha256'],
        'architecture': 'x86_64', 'hint_profile': 'known_pointer_endpoints_v1', 'condition': 'pointer_recover',
        'known_abi': 'sysv_amd64', 'entry_points': {name: target['entries'][name]
            for name in ('original_callee', 'original_caller')},
        'compiler': compiler_metadata(), 'given_facts': public_facts()}


def validate_manifest(value):
    object_fields(value, ('schema_version', 'family_id', 'build_id', 'task_id', 'capability_ids',
        'source_language', 'output_language', 'artifact_schema', 'binary_sha256', 'architecture',
        'hint_profile', 'condition', 'known_abi', 'entry_points', 'compiler', 'given_facts'))
    integer(value['schema_version'], 1, 1)
    identifier(value['task_id'])
    digest(value['build_id']); digest(value['binary_sha256'])
    if (value['family_id'] != 'variable_objects' or value['capability_ids'] != ['T3']
            or value['source_language'] != 'cpp' or value['output_language'] is not None
            or value['artifact_schema'] != {'types.json': 1} or type(value['artifact_schema']['types.json']) is not int
            or value['architecture'] != 'x86_64' or value['condition'] != 'pointer_recover'
            or value['hint_profile'] != 'known_pointer_endpoints_v1' or value['known_abi'] != 'sysv_amd64'
            or value['given_facts'] != public_facts() or value['compiler'] != compiler_metadata()):
        raise ValueError('Unregistered or answer-bearing public pointer condition')
    object_fields(value['entry_points'], ('original_callee', 'original_caller'))
    for number in value['entry_points'].values(): integer(number)
    return value


def prompt():
    return ('Recover pointer and object roles from the supplied optimized stripped C++ binary. '
        'Use only supplied static REA/Ghidra tools; no original execution, shell, source, earlier answers or compiler feedback. '
        'RVAs start at the lowest PT_LOAD page. The manifest identifies an original callee and a caller of that same boundary. '
        'The known inner convention is SysV AMD64. The function takes an object pointer and an unsigned 64-bit nonce, '
        'returns a signed 64-bit status, and transforms a bounded byte sequence into a destination buffer. '
        'The three unsigned-32 roles are length, capacity and produced; the pointer roles are source and destination. '
        'Roles describe observable use, not original source names. Recover physical field offsets, pointer depth, '
        'read/write permissions, the scalar role bounding each buffer, and supported overlap. '
        'Infer the buffer transformation and error paths from the target as evidence for these claims; do not submit source code.\n'
        'Submit types.json using the task-scoped pointer-object schema: '
        'schema_version:1, binary_sha256:<manifest digest>, size:<object extent>, alignment:<1/2/4/8/16>, '
        'fields:<five entries>, aliasing:<may_overlap/disjoint/identical_or_disjoint>. '
        'Each field has role, kind and offset. Scalar roles have kind:u32; pointer roles have kind:pointer plus '
        'indirections (1 or 2), access (read/write/read_write), and extent (length/capacity/produced). '
        'Offsets are bytes from the object base. Pointer depth one names byte storage directly; depth two names a pointer cell. '
        'Every role occurs exactly once, fields cannot overlap, and the object is at most128 bytes. '
        'Field-list order does not encode declaration order. Extra unused tail space, compatible alignment and '
        'observationally equivalent permissions are accepted; this condition does not recover erased ownership or exact unused allocation size.\n'
        'The worker generates bounded access probes from your actual description and links unchanged original object code in both directions: '
        'original caller to replacement, and generated bridge to original callee. It compares returns, defined lengths, '
        'buffer bytes and preserved object/guard bytes, including errors and aliasing; no guest stdout can provide scores. '
        'Close the binary before answering. Return exactly one Markdown block labelled types.json containing the JSON document. '
        'A brief evidence explanation may precede it; no other code blocks.\n')


def validate_original_binding(target):
    """Private specs may not substitute self-consistently rehashed endpoints.

    Only read the original action's verified outputs. A missing original is an
    admission failure, never permission to build or resolve a provider here.
    """
    action = runtime.compile_action('original', {'mode': 'original'}, target['variant'])[0]
    local = action_cache.Cache()
    hit = local.lookup(action)
    if hit is None or set(hit['output_files']) != {'result.json'}:
        raise ValueError('Pointer original build receipt missing or invalid')
    saved = json.loads(local.get(hit['output_files']['result.json']['digest']))
    names = ('private_hex', 'public_hex', 'original_obj_hex', 'original_obj_sha256',
             'helpers_obj_hex', 'helpers_obj_sha256', 'runtime')
    if (target.get('action_id') != action.key or target.get('build_id') != action.key
            or any(target.get(name) != saved.get(name) for name in names)):
        raise ValueError('Pointer endpoints differ from the frozen original build outputs')


def freeze(declaration, settings, *, publish=True):
    variant = declaration['profile']['variant']
    target = runtime.original(variant)
    metadata = validate_manifest(manifest(target))
    files = {'target': bytes.fromhex(target['public_hex']), 'manifest.json': action_cache.canonical(metadata),
             'prompt.txt': prompt().encode(), 'tools.json': (ROOT/settings['analysis_tools_file']).read_bytes()}
    hashes = {name: runtime.sha(raw) for name, raw in sorted(files.items())}
    package = runtime.sha(action_cache.canonical(hashes))
    destination = CACHE/'task-freezes'/package
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pending-', dir=destination.parent) as temporary:
            work = Path(temporary)
            for name, raw in files.items(): (work/name).write_bytes(raw)
            work.rename(destination)
    if ({p.name for p in destination.iterdir()} != set(FILES)
            or any((destination/name).is_symlink() or (destination/name).read_bytes() != raw for name, raw in files.items())):
        raise ValueError('Frozen pointer public package changed')
    spec = {'schema_version': 1, 'kind': 'pointer_probe', 'binary_sha256': target['binary_sha256'],
            'condition': 'pointer_recover', 'target': {k: v for k, v in target.items() if k != 'cache_hit'},
            'nonces': list(contracts.NONCES)}
    raw = action_cache.canonical(spec); spec_id = runtime.sha(raw)
    path = CACHE/'grade-specs'/(spec_id+'.json'); path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists(): path.write_bytes(raw)
    if path.is_symlink() or path.read_bytes() != raw: raise ValueError('Frozen pointer grading spec changed')
    sources = {name: runtime.sha(raw) for name, raw in runtime.sources('original', variant).items()}
    sources[str(runtime.POLICY.relative_to(ROOT))] = runtime.sha(runtime.POLICY.read_bytes())
    record = {'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': 'variable_objects', 'lineage_id': 'pointer_object_interfaces_v1',
        'build_id': target['build_id'], 'capability_ids': ['T3'], 'source_language': 'cpp', 'output_language': None,
        'artifact_schema': {'types.json': 1}, 'kind': 'pointer_probe', 'variant': variant,
        'profile_id': declaration['profile']['id'], 'hint_profile': metadata['hint_profile'],
        'binary_sha256': target['binary_sha256'], 'public_package_sha256': package,
        'public_directory': str(destination.relative_to(ROOT)), 'public_files_sha256': hashes,
        'grading_spec': str(path.relative_to(ROOT)), 'grading_spec_sha256': spec_id,
        'original_sources_sha256': sources, 'compiler_image': target['runtime']['compiler_image']}
    if publish:
        from ..tasks import publish_index
        publish_index(record)
    return record


def validate_frozen(value, declaration, settings):
    variant = declaration['profile']['variant']
    expected = set(runtime.sources('original', variant)) | {str(runtime.POLICY.relative_to(ROOT))}
    if (value.get('id') != declaration['task_id'] or value.get('task_id') != declaration['task_id']
            or value.get('family_id') != 'variable_objects' or value.get('lineage_id') != 'pointer_object_interfaces_v1'
            or value.get('profile_id') != declaration['profile']['id'] or value.get('variant') != variant
            or value.get('kind') != 'pointer_probe' or value.get('artifact_schema') != {'types.json': 1}
            or value.get('capability_ids') != ['T3'] or value.get('output_language') is not None
            or value.get('compiler_image') != image_manifest.load('cpp')['id']
            or value.get('build_id') != runtime.compile_action('original', {'mode': 'original'}, variant)[0].key
            or set(value.get('original_sources_sha256', {})) != expected):
        raise RuntimeError('Pointer task compiler/profile selection changed; run make prepare-capabilities')
    if any(runtime.sha((ROOT/name).read_bytes()) != h for name, h in value['original_sources_sha256'].items()):
        raise RuntimeError('Original pointer sources changed; run make prepare-capabilities')
    directory = ROOT.joinpath(*action_cache.relative(value['public_directory']).parts)
    if (directory.is_symlink() or not directory.resolve().is_relative_to((CACHE/'task-freezes').resolve())
            or {p.name for p in directory.iterdir()} != set(FILES)
            or set(value['public_files_sha256']) != set(FILES)):
        raise ValueError('Pointer package escapes its exact frozen boundary')
    files = {name: (directory/name).read_bytes() for name in FILES}
    hashes = {name: runtime.sha(raw) for name, raw in sorted(files.items())}
    if (any((directory/name).is_symlink() for name in FILES) or hashes != value['public_files_sha256']
            or runtime.sha(action_cache.canonical(hashes)) != value['public_package_sha256']):
        raise ValueError('Pointer public package changed')
    metadata = validate_manifest(json.loads(files['manifest.json']))
    if (metadata['build_id'] != value['build_id'] or metadata['binary_sha256'] != value['binary_sha256']
            or metadata['task_id'] != 'task_'+runtime.sha(files['target']+b'pointer_recover')[:20]
            or runtime.sha(files['target']) != value['binary_sha256'] or files['prompt.txt'] != prompt().encode()
            or files['tools.json'] != (ROOT/settings['analysis_tools_file']).read_bytes()):
        raise ValueError('Pointer public contract differs from its frozen selection')
    return value
