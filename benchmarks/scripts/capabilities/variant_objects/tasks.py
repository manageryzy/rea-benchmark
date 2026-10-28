"""T4 public packages and private original-endpoint specifications."""
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
    return {'family': 'gcc', 'version': record['manifest']['compiler']['version'].splitlines()[0], 'image_sha256': record['id'], 'standard': 'c++20', 'optimization': 'O2', 'library': 'freestanding_no_native_runtime', 'link': 'static_ET_EXEC', 'architecture': 'x86_64'}

def public_facts():
    return {'signature': 'int64_t original_callee(void *object, uint64_t nonce)', 'subobjects': [{'role': 'cookie', 'scalar_kind': 'u64', 'field_offset': 0}, {'role': 'state', 'scalar_kind': 'u64', 'field_offset': 0}], 'tag_kind': 'u32', 'payload_roles': {'number': 'i64', 'pair': {'first': 'i32', 'second': 'u32'}}, 'maximum_object_bytes': 128, 'maximum_payload_extent': 32}

def manifest(target):
    return {'schema_version': 1, 'family_id': 'variable_objects', 'build_id': target['build_id'], 'task_id': 'task_' + runtime.sha(bytes.fromhex(target['public_hex']) + b'variant_recover')[:20], 'capability_ids': ['T4'], 'source_language': 'cpp', 'output_language': None, 'artifact_schema': {'types.json': 1}, 'binary_sha256': target['binary_sha256'], 'architecture': 'x86_64', 'hint_profile': 'known_variant_endpoints_v1', 'condition': 'variant_recover', 'known_abi': 'sysv_amd64', 'entry_points': {name: target['entries'][name] for name in ('original_callee', 'original_caller')}, 'compiler': compiler_metadata(), 'given_facts': public_facts()}

def validate_manifest(value):
    object_fields(value, ('schema_version', 'family_id', 'build_id', 'task_id', 'capability_ids', 'source_language', 'output_language', 'artifact_schema', 'binary_sha256', 'architecture', 'hint_profile', 'condition', 'known_abi', 'entry_points', 'compiler', 'given_facts'))
    integer(value['schema_version'], 1, 1)
    identifier(value['task_id'])
    digest(value['build_id'])
    digest(value['binary_sha256'])
    if value['family_id'] != 'variable_objects' or value['capability_ids'] != ['T4'] or value['source_language'] != 'cpp' or (value['output_language'] is not None) or (value['artifact_schema'] != {'types.json': 1}) or (type(value['artifact_schema']['types.json']) is not int) or (value['architecture'] != 'x86_64') or (value['condition'] != 'variant_recover') or (value['hint_profile'] != 'known_variant_endpoints_v1') or (value['known_abi'] != 'sysv_amd64') or (value['given_facts'] != public_facts()) or (value['compiler'] != compiler_metadata()):
        raise ValueError('Unregistered or answer-bearing public variant-object condition')
    object_fields(value['entry_points'], ('original_callee', 'original_caller'))
    for number in value['entry_points'].values():
        integer(number)
    return value

def prompt():
    return 'Recover discriminants, active union payloads and semantic subobject offsets from the supplied optimized stripped C++ binary. Use only supplied static REA/Ghidra tools: no original execution, shell, source, prior answers or compiler feedback. RVAs start at the lowest PT_LOAD page; the manifest identifies the original callee and a caller of that boundary. The known inner signature is int64_t original_callee(void *object, uint64_t nonce), under SysV AMD64. It updates a state subobject and an active payload, preserves the cookie and tag, and rejects unknown tags without modifying semantic data. Supplied scalar kinds are premises; they are not a scalar-recovery score. Infer the transformation from the binary as evidence for role correspondence; submit descriptions, not source code.\nSubmit types.json with the task-scoped tagged-object schema: schema_version:1, binary_sha256:<manifest digest>, size:<bounded root extent>, alignment:<1/2/4/8/16>, tag_offset:<u32 tag byte offset>, subobjects:<two entries>, payload:<union region>. Every subobject has role (cookie or state) and offset from the root. Each supplied subobject contains its known u64 field at relative offset zero; recover the subobject location. payload has offset, extent (8..32), and alternatives. The number alternative has role:number, tag:<u32 discriminant>, offset:<i64 member offset relative to payload>. The pair alternative has role:pair, tag:<different u32 discriminant>, first_offset:<i32 member relative offset>, second_offset:<u32 member relative offset>. Both alternatives are required, tags are distinct, and pair members cannot overlap. Active alternatives share the union region; tag, semantic subobjects and payload region cannot overlap. The root is at most 128 bytes. JSON list order is unrelated to physical order.\nOnly observable accesses and effects are scored. Compatible alignment and unused root tail space are accepted; nominal C++ inheritance, exact unused allocation size and erased source type names are outside the score. A description of the same observable subobjects does not have to reproduce the original nominal class graph.\nThe trusted worker generates actual reads/writes from your description and links unchanged original object code in both directions: original caller to generated replacement, and generated bridge to original callee. The host checks status, state/cookie values, the active payload, tag preservation and protected descriptor/guard bytes. Inactive union values and unspecified padding are not expected semantic values. Guest stdout cannot supply a score. Close the binary before answering. Return exactly one Markdown block labelled types.json containing the JSON document. A brief evidence explanation may precede it; no other code blocks.\n'

def validate_original_binding(target):
    runtime.validate_target(target)

def freeze(declaration, settings, *, publish=True):
    variant = declaration['profile']['variant']
    target = runtime.original(variant)
    metadata = validate_manifest(manifest(target))
    files = {'target': bytes.fromhex(target['public_hex']), 'manifest.json': action_cache.canonical(metadata), 'prompt.txt': prompt().encode(), 'tools.json': (ROOT / settings['analysis_tools_file']).read_bytes()}
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
    if {p.name for p in destination.iterdir()} != set(FILES) or any(((destination / name).is_symlink() or (destination / name).read_bytes() != raw for name, raw in files.items())):
        raise ValueError('Frozen variant-object public package changed')
    spec = {'schema_version': 1, 'kind': 'variant_object', 'binary_sha256': target['binary_sha256'], 'condition': 'variant_recover', 'target': {k: v for k, v in target.items() if k != 'cache_hit'}, 'nonces': list(contracts.NONCES)}
    raw = action_cache.canonical(spec)
    spec_id = runtime.sha(raw)
    path = CACHE / 'grade-specs' / (spec_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    if path.is_symlink() or path.read_bytes() != raw:
        raise ValueError('Frozen variant-object grading spec changed')
    sources = {name: runtime.sha(raw) for name, raw in runtime.sources('original', variant).items()}
    sources[str(runtime.POLICY.relative_to(ROOT))] = runtime.sha(runtime.POLICY.read_bytes())
    record = {'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'], 'family_id': 'variable_objects', 'lineage_id': 'variant_object_interfaces_v1', 'build_id': target['build_id'], 'capability_ids': ['T4'], 'source_language': 'cpp', 'output_language': None, 'artifact_schema': {'types.json': 1}, 'kind': 'variant_object', 'variant': variant, 'profile_id': declaration['profile']['id'], 'hint_profile': metadata['hint_profile'], 'binary_sha256': target['binary_sha256'], 'public_package_sha256': package, 'public_directory': str(destination.relative_to(ROOT)), 'public_files_sha256': hashes, 'grading_spec': str(path.relative_to(ROOT)), 'grading_spec_sha256': spec_id, 'original_sources_sha256': sources, 'compiler_image': target['runtime']['compiler_image']}
    if publish:
        from ..tasks import publish_index
        publish_index(record)
    return record

def validate_frozen(value, declaration, settings):
    variant = declaration['profile']['variant']
    expected = set(runtime.sources('original', variant)) | {str(runtime.POLICY.relative_to(ROOT))}
    if value.get('id') != declaration['task_id'] or value.get('task_id') != declaration['task_id'] or value.get('family_id') != 'variable_objects' or (value.get('lineage_id') != 'variant_object_interfaces_v1') or (value.get('profile_id') != declaration['profile']['id']) or (value.get('variant') != variant) or (value.get('kind') != 'variant_object') or (value.get('artifact_schema') != {'types.json': 1}) or (value.get('capability_ids') != ['T4']) or (value.get('output_language') is not None) or (value.get('compiler_image') != image_manifest.load('cpp')['id']) or (value.get('build_id') != runtime.compile_action('original', {'mode': 'original'}, variant)[0].key) or (set(value.get('original_sources_sha256', {})) != expected):
        raise RuntimeError('Variant-object task compiler/profile selection changed; run make prepare-capabilities')
    if any((runtime.sha((ROOT / name).read_bytes()) != h for name, h in value['original_sources_sha256'].items())):
        raise RuntimeError('Original variant-object sources changed; run make prepare-capabilities')
    directory = ROOT.joinpath(*action_cache.relative(value['public_directory']).parts)
    if directory.is_symlink() or not directory.resolve().is_relative_to((CACHE / 'task-freezes').resolve()) or {p.name for p in directory.iterdir()} != set(FILES) or (set(value['public_files_sha256']) != set(FILES)):
        raise ValueError('Variant-object package escapes its exact frozen boundary')
    files = {name: (directory / name).read_bytes() for name in FILES}
    hashes = {name: runtime.sha(raw) for name, raw in sorted(files.items())}
    if any(((directory / name).is_symlink() for name in FILES)) or hashes != value['public_files_sha256'] or runtime.sha(action_cache.canonical(hashes)) != value['public_package_sha256']:
        raise ValueError('Variant-object public package changed')
    metadata = validate_manifest(json.loads(files['manifest.json']))
    if metadata['build_id'] != value['build_id'] or metadata['binary_sha256'] != value['binary_sha256'] or metadata['task_id'] != 'task_' + runtime.sha(files['target'] + b'variant_recover')[:20] or (runtime.sha(files['target']) != value['binary_sha256']) or (files['prompt.txt'] != prompt().encode()) or (files['tools.json'] != (ROOT / settings['analysis_tools_file']).read_bytes()):
        raise ValueError('Variant-object public contract differs from its frozen selection')
    return value
