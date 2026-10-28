"""Hint-controlled public T tasks and separately frozen original-object specs."""
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest
from grading.artifacts import object_fields, digest, identifier, integer
from . import contracts, runtime

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities'


def public_facts(family, condition, binary_sha256):
    value = contracts.gold(family, binary_sha256)
    return project_facts(value, condition)


def project_facts(value, condition):
    """Project only supplied premises, independently of the selected gold layout."""
    by_id = {node['id']: node for node in value['types']}
    if condition == 'abi_recover':
        return value
    facts = []
    def visit(name, path, offset):
        node = by_id[name]
        item = {'path': path, 'kind': node['kind']}
        if condition == 'scalar_recover':
            item['offset'] = offset
            if node['kind'] == 'record': item.update(size=node['size'], alignment=node['alignment'])
            if node['kind'] == 'array': item.update(count=node['count'], stride=node['stride'], alignment=node['alignment'])
            facts.append(item)
        elif node['kind'] == 'integer':
            facts.append({**item, 'bits': node['bits'], 'signed': node['signed']})
        if node['kind'] == 'record':
            for field in node['fields']: visit(field['type'], path + [field['role']], offset + field['offset'])
        elif node['kind'] == 'array':
            for index in range(node['count']): visit(node['element'], path + [index], offset + index * node['stride'])
    visit(value['root'], [], 0)
    # Semantic path order must not disclose physical declaration/offset order in
    # a layout-recovery condition.
    return sorted(facts, key=lambda item: action_cache.canonical(item['path']))


def compiler_metadata():
    record = image_manifest.load('cpp')
    return {'family': 'gcc', 'version': record['manifest']['compiler']['version'].splitlines()[0],
            'image_sha256': record['id'], 'standard': 'c++20', 'optimization': 'O2',
            'library': 'freestanding_no_native_runtime', 'link': 'static_ET_EXEC', 'architecture': 'x86_64'}


def manifest(declaration, target):
    profile = declaration['profile']
    condition, family = profile.get('condition', profile['id']), declaration['family_id']
    public_id = 'task_' + runtime.sha(bytes.fromhex(target['public_hex']) + condition.encode())[:20]
    result = {'schema_version': 1, 'family_id': family, 'build_id': target['build_id'], 'task_id': public_id,
        'capability_ids': [contracts.CONDITIONS[condition]], 'source_language': 'cpp', 'output_language': None,
        'artifact_schema': profile['artifact_schema'], 'binary_sha256': target['binary_sha256'], 'architecture': 'x86_64',
        'hint_profile': 'known_original_endpoints_v1', 'condition': condition,
        'entry_points': {name: target['entries'][name] for name in ('original_callee', 'original_caller')},
        'compiler': compiler_metadata(), 'given_facts': public_facts(family, condition, target['binary_sha256'])}
    if condition != 'abi_recover': result['known_abi'] = target['convention']
    validate_manifest(result)
    return result


def validate_manifest(value):
    object_fields(value, ('schema_version', 'family_id', 'build_id', 'task_id', 'capability_ids', 'source_language',
        'output_language', 'artifact_schema', 'binary_sha256', 'architecture', 'hint_profile', 'condition',
        'entry_points', 'compiler', 'given_facts'), ('known_abi',))
    integer(value['schema_version'], 1, 1)
    for name in ('family_id', 'task_id'): identifier(value[name])
    for name in ('build_id', 'binary_sha256'): digest(value[name])
    condition, family = value['condition'], value['family_id']
    if condition not in contracts.CONDITIONS or family not in contracts.FAMILIES:
        raise ValueError('Unknown public type/ABI condition')
    expected_name = 'abi.json' if condition == 'abi_recover' else 'types.json'
    if (value['artifact_schema'] != {expected_name: 1} or type(value['artifact_schema'][expected_name]) is not int
            or value['capability_ids'] != [contracts.CONDITIONS[condition]] or value['source_language'] != 'cpp'
            or value['output_language'] is not None or value['architecture'] != 'x86_64'
            or value['hint_profile'] != 'known_original_endpoints_v1'):
        raise ValueError('Unsupported public type/ABI contract')
    object_fields(value['entry_points'], ('original_callee', 'original_caller'))
    for number in value['entry_points'].values(): integer(number)
    compiler = value['compiler']
    object_fields(compiler, ('family', 'version', 'image_sha256', 'standard', 'optimization', 'library', 'link', 'architecture'))
    digest(compiler['image_sha256'])
    if (compiler['family'] != 'gcc' or not isinstance(compiler['version'], str) or len(compiler['version']) > 256
            or any(compiler[key] != expected for key, expected in {'standard': 'c++20', 'optimization': 'O2',
                'library': 'freestanding_no_native_runtime', 'link': 'static_ET_EXEC', 'architecture': 'x86_64'}.items())):
        raise ValueError('Unapproved target compiler projection')
    if value['given_facts'] != public_facts(family, condition, value['binary_sha256']):
        raise ValueError('Public facts contain an answer leak or contradict the nonmeasured premises')
    if condition == 'abi_recover':
        if 'known_abi' in value:
            raise ValueError('ABI recovery cannot disclose its convention')
    elif value.get('known_abi') not in contracts.CONVENTIONS:
        raise ValueError('Type recovery requires its given calling convention')
    return value


def prompt(metadata):
    condition, family = metadata['condition'], metadata['family_id']
    path_text = json.dumps(contracts.paths(family))
    text = ('Recover the requested interface facts from the supplied optimized stripped C++ binary. '
        'Use only the supplied static REA/Ghidra tools; no source, earlier answers, target execution or compiler feedback. '
        'RVAs start at the lowest PT_LOAD page. The public manifest gives the original callee and a caller using that same boundary. '
        'This build deliberately retains independent call boundaries, return-slot guards and ABI alignment/home-slot witnesses. '
        'The logical function takes one record by value plus a 64-bit unsigned nonce and returns an updated record by value. '
        'Semantic field paths (strings are roles, numbers are array indices): ' + path_text + '. '
        'These roles identify data in the transform, not original source names. The codec mixes an adjustment, weight, flag and rotated values; '
        'the numeric condition mixes an adjustment with a weighted counter; the variable-object condition updates two key/change entries and a length.\n')
    if condition == 'abi_recover':
        text += ('Recover the inner call boundary as abi.json. The complete type description is supplied as given_facts; do not infer the convention from wrapper or loader entry. '
            'The supported convention IDs are sysv_amd64 and ms_x64. Report schema_version=1, binary_sha256, entry_rva of original_callee, convention, arguments, return, '
            'stack_alignment, shadow_space and preserved. Each argument has role (record or nonce), passing (register_value, stack_value or indirect_value), and locations. '
            'Each register location is {kind:register,name:<AMD64 register>,size:<bytes>}; register-name case is ignored. Each stack location is {kind:stack,offset:<bytes>,size:<bytes>}. '
            'Register chunks are ordered from low to high record bytes. Stack offsets start at callee-entry RSP, including the return address. '
            'The return has passing, hidden_pointer (register name or null) and locations (return data, or the returned hidden pointer). '
            'stack_alignment describes RSP before CALL; shadow_space excludes the pushed return address. '
            'preserved is the convention-required general-purpose and low-128-bit XMM register set, including RSP; '
            'its restoration is checked through return-stack discipline. Incidental leaf preservation and MXCSR/x87 control state are outside this field. '
            'All claims participate in admission and machine-transfer probes.\n')
    else:
        text += ('Recover types.json with schema_version=1, binary_sha256, root:<type-id>, and types:<list>. Type IDs are arbitrary ASCII C-style identifiers of 1..64 characters; original names are not required. '
            'Integer nodes have id,kind:integer,bits (8/16/32/64),signed (boolean). Record nodes have id,kind:record,size,alignment,fields; '
            'each field has role,offset,type. Array nodes have id,kind:array,element,count,stride,alignment. '
            'Fields may be reordered in the JSON; padding and unobservable equivalent descriptions are accepted. The root is a record. '
            'At most 32 nodes, 16 fields per record, 8 array elements, 128 bytes per type, graph depth 8, and alignments 1/2/4/8/16. '
            'given_facts supplies ' + ('the layout only; recover scalar widths and signedness' if condition == 'scalar_recover' else 'the scalar types only; recover layout, offsets, nested arrays and stride') +
            '. The calling convention is supplied as known_abi. Do not change the supplied nonmeasured facts.\n')
    artifact = next(iter(metadata['artifact_schema']))
    return text + ('The grading worker constructs probes from your description and links the frozen original caller/callee object, testing both directions. '
        'It compares defined values and observable memory effects, not unspecified padding. Close the binary before answering. '
        'Return exactly one Markdown block labelled ' + artifact + ', containing that JSON document. A brief evidence explanation may precede it; no other code blocks.\n')


def freeze(declaration, settings, *, publish=True):
    target = runtime.original(declaration['family_id'], declaration['convention'], variant=declaration['profile'].get('variant'))
    metadata = manifest(declaration, target)
    files = {'target': bytes.fromhex(target['public_hex']), 'manifest.json': action_cache.canonical(metadata),
             'prompt.txt': prompt(metadata).encode(), 'tools.json': (ROOT / settings['analysis_tools_file']).read_bytes()}
    hashes = {name: runtime.sha(raw) for name, raw in sorted(files.items())}
    package_id = runtime.sha(action_cache.canonical(hashes))
    destination = CACHE / 'task-freezes' / package_id
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pending-', dir=destination.parent) as temporary:
            work = Path(temporary)
            for name, raw in files.items(): (work / name).write_bytes(raw)
            work.rename(destination)
    if {path.name for path in destination.iterdir()} != set(files) or any((destination / name).is_symlink() or (destination / name).read_bytes() != raw for name, raw in files.items()):
        raise ValueError('Frozen public ABI package changed')
    stable_target = {key: value for key, value in target.items() if key != 'cache_hit'}
    spec = {'schema_version': 1, 'kind': 'abi_probe', 'binary_sha256': target['binary_sha256'],
            'condition': declaration['profile'].get('condition', declaration['profile']['id']), 'target': stable_target, 'nonces': list(contracts.NONCES)}
    raw = action_cache.canonical(spec)
    spec_id = runtime.sha(raw)
    path = CACHE / 'grade-specs' / (spec_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists(): path.write_bytes(raw)
    if path.is_symlink() or path.read_bytes() != raw: raise ValueError('Frozen ABI grading spec changed')
    source_hashes = {name: runtime.sha(raw) for name, raw in runtime.sources('original', declaration['family_id'], declaration['profile'].get('variant')).items()}
    source_hashes[str(runtime.POLICY.relative_to(ROOT))] = runtime.sha(runtime.POLICY.read_bytes())
    if declaration['profile'].get('variant'):
        from . import variants
        source_hashes[str(variants.CATALOG.relative_to(ROOT))] = runtime.sha(variants.CATALOG.read_bytes())
    record = {'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': declaration['family_id'], 'build_id': target['build_id'], 'capability_ids': metadata['capability_ids'],
        'lineage_id': 'integer_record_interfaces_v1',
        'source_language': 'cpp', 'output_language': None, 'artifact_schema': metadata['artifact_schema'], 'kind': 'abi_probe',
        'profile_id': declaration['profile']['id'], 'hint_profile': metadata['hint_profile'],
        'binary_sha256': target['binary_sha256'], 'public_package_sha256': package_id,
        'public_directory': str(destination.relative_to(ROOT)), 'public_files_sha256': hashes,
        'grading_spec': str(path.relative_to(ROOT)), 'grading_spec_sha256': spec_id,
        'original_sources_sha256': source_hashes, 'compiler_image': target['runtime']['compiler_image']}
    if declaration['profile'].get('variant'):
        record['variant'] = declaration['profile']['variant']
    if publish:
        from ..tasks import publish_index
        publish_index(record)
    return record


def validate_frozen(value, declaration, settings):
    family, profile = declaration['family_id'], declaration['profile']
    variant = profile.get('variant')
    expected_sources = set(runtime.sources('original', family, variant)) | {str(runtime.POLICY.relative_to(ROOT))}
    if variant:
        from . import variants
        expected_sources.add(str(variants.CATALOG.relative_to(ROOT)))
    if (value['id'] != declaration['task_id'] or value['compiler_image'] != image_manifest.load('cpp')['id']
            or value.get('profile_id') != profile['id'] or value.get('variant') != variant
            or value.get('build_id') != runtime.original_action(family, declaration['convention'], variant)[0].key
            or set(value['original_sources_sha256']) != expected_sources):
        raise RuntimeError('ABI task compiler selection changed; run make prepare-capabilities')
    for name, expected in value['original_sources_sha256'].items():
        if runtime.sha(ROOT.joinpath(*action_cache.relative(name).parts).read_bytes()) != expected:
            raise RuntimeError('Original ABI target source changed; run make prepare-capabilities')
    directory = ROOT.joinpath(*action_cache.relative(value['public_directory']).parts)
    if directory.is_symlink() or not directory.resolve().is_relative_to((CACHE / 'task-freezes').resolve()) or {path.name for path in directory.iterdir()} != {'target', 'prompt.txt', 'manifest.json', 'tools.json'}:
        raise ValueError('ABI public package escapes its exact frozen boundary')
    files = {name: (directory / name).read_bytes() for name in value['public_files_sha256']}
    if any((directory / name).is_symlink() for name in files): raise ValueError('ABI package contains a symlink')
    hashes = {name: runtime.sha(raw) for name, raw in sorted(files.items())}
    if hashes != value['public_files_sha256'] or runtime.sha(action_cache.canonical(hashes)) != value['public_package_sha256']:
        raise ValueError('ABI public package changed')
    metadata = validate_manifest(json.loads(files['manifest.json']))
    if (files['prompt.txt'] != prompt(metadata).encode() or files['tools.json'] != (ROOT / settings['analysis_tools_file']).read_bytes()
            or value['artifact_schema'] != profile['artifact_schema'] or value['kind'] != 'abi_probe'
            or metadata['build_id'] != value['build_id'] or metadata['binary_sha256'] != value['binary_sha256']
            or metadata['condition'] != profile.get('condition', profile['id'])):
        raise RuntimeError('ABI public task contract changed; run make prepare-capabilities')
    return value
