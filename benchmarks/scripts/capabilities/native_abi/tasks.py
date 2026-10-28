"""Frozen T7 packages: supplied semantics/layout, unknown native transfer."""
import hashlib
import json
from pathlib import Path
import tempfile

import action_cache
from grading import image_manifest
from grading.artifacts import digest, integer, object_fields
from . import build, images, reference

ROOT = action_cache.ROOT
CACHE = ROOT / '.cache/benchmark-capabilities'
FILES = ('target', 'prompt.txt', 'manifest.json', 'tools.json')
LANGUAGES = {'rust_native_recover': 'rust', 'go_internal_recover': 'go'}
ARTIFACT = {'native-abi.json': 1}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def contract(declaration):
    profile = declaration['profile']
    if (profile.get('kind') != 'native_abi' or profile.get('id') not in LANGUAGES
            or profile.get('variant') != LANGUAGES[profile['id']]
            or profile.get('artifact_schema') != ARTIFACT
            or type(profile['artifact_schema']['native-abi.json']) is not int
            or profile.get('output_language') is not None
            or declaration.get('family_id') != 'variable_objects'
            or declaration.get('task_id') != 'variable_objects_' + profile['id']):
        raise ValueError('Unregistered native ABI declaration')
    return LANGUAGES[profile['id']]


def sources(language):
    names = {*build.SOURCES, *(build.PREFIX + language + '/' + name for name in build.FILES[language]),
             build.MODULE + 'build.py', build.MODULE + 'images.py',
             build.MODULE + 'tasks.py', build.MODULE + 'reference.py'}
    return {name: sha((ROOT / name).read_bytes()) for name in sorted(names)}


def vectors(nonces):
    if (not isinstance(nonces, list) or not 1 <= len(nonces) <= 64
            or any(type(n) is not int or not 0 <= n < 2**64 for n in nonces)
            or len(set(nonces)) != len(nonces)):
        raise ValueError('Invalid native ABI private vectors')
    return nonces


def validate_original(target, language):
    selected = build.action(language)[0]
    if target.get('build_id') != selected.key:
        raise ValueError('Native ABI original build differs')
    local = action_cache.Cache()
    saved = local.lookup(selected)
    if not saved:
        raise ValueError('Native ABI original action is missing')
    expected = json.loads(local.get(saved['output_files']['result.json']['digest']))
    if (set(target) != set(expected) | {'build_id'}
            or action_cache.canonical({name: target[name] for name in expected}) != action_cache.canonical(expected)):
        raise ValueError('Native ABI target differs from original AC/CAS outputs')


def endpoints(target, language):
    private, _ = images.validate(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']), language)
    names = ('native_transform', 'native_caller') if language == 'rust' else ('main.nativeTransform', 'main.nativeCaller')
    selected = images.selected_symbols(private, names)
    return {role: selected[name]['rva'] for role, name in zip(('original_callee', 'original_caller'), names)}


def manifest(target, language):
    pinned = image_manifest.load(language)
    binary = bytes.fromhex(target['public_hex'])
    return {'schema_version': 1, 'family_id': 'variable_objects', 'build_id': target['build_id'],
            'task_id': 'task_' + sha(binary + b'native_abi_recover')[:20],
            'capability_ids': ['T7'], 'source_language': language, 'output_language': None,
            'artifact_schema': ARTIFACT, 'binary_sha256': sha(binary), 'architecture': 'x86_64',
            'hint_profile': 'known_native_aggregate_endpoints_v1', 'condition': 'native_abi_recover',
            'entry_points': endpoints(target, language),
            'compiler': {'family': 'rustc' if language == 'rust' else 'gc',
                         'version': pinned['manifest']['compiler']['version'].splitlines()[0],
                         'image_sha256': pinned['id']},
            'given_facts': {'packet_bytes': 24, 'reply_bytes': 24, 'field_offsets': [0, 8, 16],
                            'field_bits': 64, 'byte_order': 'little', 'alignment': 8,
                            'pointer_bytes': 8, 'caller_reply_xor': 0x4c839e1720b6d5fa,
                            'bounded_stack': True, 'runtime_startup': False,
                            'go_r14_context_and_stackguard': language == 'go'},
            'diagnostic_only': True, 'release_accepted': False}


def prompt(language):
    return (f'Recover the physical transfer at the pinned {language} native function boundary in this stripped Linux AMD64 binary. '
        'The manifest supplies both original endpoint RVAs relative to the lowest PT_LOAD page. '
        'Use only supplied static REA/Ghidra tools: no target execution, shell, original source, prior answers or compiler feedback. '
        'The packet and reply each contain three uint64 fields at offsets 0,8,16, with size24/alignment8. '
        'All arithmetic wraps modulo2^64. Packet fields are a,b,c, and the separate scalar argument is nonce. '
        'The complete callee semantics are x=rotl64(a XOR nonce,11)+17*b; '
        'y=rotl64(b+0x7a6d503912c4b8ef,23) XOR c; z=(c+257*nonce) XOR rotl64(x+y,5). '
        'The reply is x,y,z. The caller invokes the same native signature and XORs reply.z with 0x4c839e1720b6d5fa. '
        'These layouts and semantics are supplied premises and receive no type-layout or semantic-recovery score. '
        'Recover how packet, nonce and reply are physically conveyed at this exact compiler boundary. '
        'Rust uses ordinary Rust functions, not extern C; repr(C) specifies only the supplied record layout. '
        'Go uses ABIInternal, not cgo or an ABI0 assembly wrapper. '
        'Execution supplies a bounded stack and the valid Go R14 context/stack guard. Startup, heap/GC, captured closures and unwinding are outside this condition.\n'
        'Submit native-abi.json with schema_version:1, binary_sha256:<manifest digest>, and packet,nonce,reply bindings. '
        'A scalar nonce binding is {kind:"register",register:<name>} or {kind:"stack",offset:<bytes from entry RSP>}. '
        'Packet/reply bindings use {kind:"registers",registers:[<first>,<second>,<third>]}, '
        '{kind:"indirect",register:<pointer register>} or {kind:"stack",offset:<bytes from entry RSP>}. '
        'Allowed registers are rax,rbx,rcx,rdx,rsi,rdi,rbp,r8,r9,r10,r11,r12,r13,r15. '
        'Register packs must have three distinct members. Stack offsets are multiples of8 from8 through512. '
        'There are no expressions, arbitrary addresses, extra fields, source code or executable blobs.\n'
        'Descriptions generate two complementary probes: the unchanged original caller invokes a replacement implementing the supplied semantics, '
        'and a generated bridge invokes the unchanged original callee. Both directions must pass on every selected invocation. '
        'The host reads actual result registers or returned buffer bytes after a guarded real return; stdout cannot supply records. '
        'Invalid hypotheses may fault; coincidental partial matches cannot pass the task. Close the binary session, '
        'explain the evidence briefly, then emit exactly one fenced native-abi.json artifact.\n')


def freeze(declaration, settings, *, publish=True):
    language = contract(declaration)
    before = sources(language)
    target = {name: value for name, value in build.build(language).items() if name != 'cache_hit'}
    validate_original(target, language)
    metadata = manifest(target, language)
    files = {'target': bytes.fromhex(target['public_hex']), 'prompt.txt': prompt(language).encode(),
             'manifest.json': action_cache.canonical(metadata),
             'tools.json': (ROOT / settings['analysis_tools_file']).read_bytes()}
    hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    package = sha(action_cache.canonical(hashes))
    directory = CACHE / 'task-freezes' / package
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pending-', dir=directory.parent) as temporary:
            work = Path(temporary)
            for name, raw in files.items():
                (work / name).write_bytes(raw)
            work.rename(directory)
    if (directory.is_symlink() or {p.name for p in directory.iterdir()} != set(FILES)
            or any((directory / name).is_symlink() or (directory / name).read_bytes() != raw for name, raw in files.items())):
        raise ValueError('Native ABI public package changed')
    spec = {'schema_version': 1, 'kind': 'native_abi', 'condition': 'native_abi_recover',
            'language': language, 'binary_sha256': metadata['binary_sha256'], 'target': target,
            'nonces': list(reference.NONCES)}
    raw = action_cache.canonical(spec)
    spec_id = sha(raw)
    path = CACHE / 'grade-specs' / (spec_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    if path.is_symlink() or path.read_bytes() != raw or sources(language) != before:
        raise ValueError('Native ABI grading inputs changed')
    record = {'schema_version': 1, 'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': 'variable_objects', 'lineage_id': 'native_aggregate_interfaces_v1',
        'build_id': target['build_id'], 'capability_ids': ['T7'], 'source_language': language, 'output_language': None,
        'artifact_schema': ARTIFACT, 'kind': 'native_abi', 'variant': language,
        'profile_id': declaration['profile']['id'], 'hint_profile': metadata['hint_profile'],
        'binary_sha256': metadata['binary_sha256'], 'public_package_sha256': package,
        'public_directory': str(directory.relative_to(ROOT)), 'public_files_sha256': hashes,
        'grading_spec': str(path.relative_to(ROOT)), 'grading_spec_sha256': spec_id,
        'source_inputs_sha256': before}
    if publish:
        from capabilities.tasks import publish_index
        publish_index(record)
    return record


def validate_frozen(value, declaration, settings):
    language = contract(declaration)
    integer(value.get('schema_version'), 1, 1)
    for name, expected in {'id': declaration['task_id'], 'task_id': declaration['task_id'],
        'family_id': 'variable_objects', 'lineage_id': 'native_aggregate_interfaces_v1',
        'kind': 'native_abi', 'profile_id': declaration['profile']['id'], 'variant': language,
        'capability_ids': ['T7'], 'source_language': language, 'output_language': None,
        'artifact_schema': ARTIFACT, 'hint_profile': 'known_native_aggregate_endpoints_v1'}.items():
        if action_cache.canonical(value.get(name)) != action_cache.canonical(expected):
            raise ValueError('Native ABI frozen contract differs: ' + name)
    package, spec_id = value['public_package_sha256'], value['grading_spec_sha256']
    digest(package)
    digest(spec_id)
    directory = ROOT.joinpath(*action_cache.relative(value['public_directory']).parts)
    spec_path = ROOT.joinpath(*action_cache.relative(value['grading_spec']).parts)
    if (directory.is_symlink() or directory.resolve() != (CACHE / 'task-freezes' / package).resolve()
            or {p.name for p in directory.iterdir()} != set(FILES)
            or any((directory / name).is_symlink() for name in FILES)
            or spec_path.is_symlink() or spec_path.resolve() != (CACHE / 'grade-specs' / (spec_id + '.json')).resolve()):
        raise ValueError('Native ABI package escapes its exact boundary')
    files = {name: (directory / name).read_bytes() for name in FILES}
    hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    spec_raw = spec_path.read_bytes()
    if hashes != value['public_files_sha256'] or sha(action_cache.canonical(hashes)) != package or sha(spec_raw) != spec_id:
        raise ValueError('Native ABI immutable inputs changed')
    recorded_sources = value.get('source_inputs_sha256')
    if not isinstance(recorded_sources, dict) or not recorded_sources:
        raise ValueError('Invalid native ABI source input manifest')
    for name, source_digest in recorded_sources.items():
        action_cache.relative(name)
        digest(source_digest)
    if recorded_sources != sources(language):
        raise RuntimeError('Native ABI source inputs changed; prepare the selected task first')
    spec = json.loads(spec_raw)
    object_fields(spec, ('schema_version', 'kind', 'condition', 'language', 'binary_sha256', 'target', 'nonces'))
    integer(spec['schema_version'], 1, 1)
    if spec['kind'] != 'native_abi' or spec['condition'] != 'native_abi_recover' or spec['language'] != language:
        raise ValueError('Native ABI private contract differs')
    vectors(spec['nonces'])
    validate_original(spec['target'], language)
    expected = manifest(spec['target'], language)
    if (action_cache.canonical(json.loads(files['manifest.json'])) != action_cache.canonical(expected)
            or value['build_id'] != expected['build_id'] or value['binary_sha256'] != expected['binary_sha256']
            or spec['binary_sha256'] != expected['binary_sha256'] or sha(files['target']) != expected['binary_sha256']
            or files['prompt.txt'] != prompt(language).encode()
            or files['tools.json'] != (ROOT / settings['analysis_tools_file']).read_bytes()):
        raise ValueError('Native ABI public premises or endpoints differ from the original')
    return value
