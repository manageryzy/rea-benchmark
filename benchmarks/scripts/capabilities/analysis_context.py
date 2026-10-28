"""Analysis seeds derived exclusively from already disclosed public entry points.

Seeds initialize Ghidra's ephemeral database, never the executable. Localization
tasks remain unseeded. This module does not import private task specifications.
"""
import hashlib
import json
from pathlib import Path
import struct

POLICY = 'public-given-entrypoints-v1'
FILENAME = 'analysis.seeds'
HINT_FREE = frozenset(('localization', 'optimized_localization'))
KNOWN = frozenset(('c_buffer', 'cpp_buffer', 'matched_buffer', 'c_lifecycle',
    'cpp_lifecycle', 'abi_probe', 'pointer_probe', 'variant_object',
    'indirect_call', 'native_abi'))
ROOT = Path(__file__).resolve().parents[3]
RECEIPT = ROOT / 'benchmarks/config/analysis-context-validation.json'
GATE_SOURCES = ('benchmarks/scripts/capabilities/analysis_context.py',
    'benchmarks/scripts/check_analysis_context.py', 'benchmarks/scripts/analysis-context-smoke.mjs',
    'benchmarks/scripts/capabilities/coordinator.py', 'benchmarks/scripts/capabilities/config.py',
    'benchmarks/scripts/runtime_attestation.py', 'benchmarks/scripts/cache.py',
    'benchmarks/scripts/run.py', 'benchmarks/scripts/action_cache.py',
    'benchmarks/scripts/rea-gateway.mjs', 'benchmarks/scripts/tool-catalog.mjs',
    'benchmarks/scripts/evidence-paging.mjs', 'benchmarks/config/evidence-paging.json',
    'benchmarks/config/runtime-attestation.json', 'bin/rea', 'env.sh')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def enabled(runtime):
    value = runtime.get('known_entry_seeds', False)
    if type(value) is not bool:
        raise ValueError('known_entry_seeds must be boolean')
    return value


def load_segments(raw):
    """Read the public ELF64/x86-64 mappings used by the frozen RVA contract."""
    if (len(raw) < 64 or raw[:7] != b'\x7fELF\x02\x01\x01'
            or struct.unpack_from('<H', raw, 18)[0] != 62
            or struct.unpack_from('<H', raw, 16)[0] not in (2, 3)):
        raise ValueError('Entry seeds require a public x86-64 little-endian ELF')
    offset = struct.unpack_from('<Q', raw, 32)[0]
    size, count = struct.unpack_from('<HH', raw, 54)
    if size < 56 or not 1 <= count <= 1024 or offset + size * count > len(raw):
        raise ValueError('Invalid public ELF program headers')
    segments = []
    for index in range(count):
        kind, flags, file_offset, address, _, file_size, memory_size, _ = struct.unpack_from(
            '<IIQQQQQQ', raw, offset + index * size)
        if kind != 1:
            continue
        if (file_size > memory_size or file_offset + file_size > len(raw)
                or address + memory_size > 2**64):
            raise ValueError('Invalid public ELF load segment')
        segments.append((address, file_size, flags))
    if not segments:
        raise ValueError('Public ELF has no load segments')
    return segments


def derive(raw, manifest, kind):
    """No names, layouts, private symbols, or inferred facts become seeds."""
    if kind in HINT_FREE:
        if 'entry_rva' in manifest or 'entry_points' in manifest:
            raise ValueError('Localization cannot disclose or seed business entries')
        return None
    if kind not in KNOWN:
        raise ValueError('Unsupported known-entry task kind')
    if sha(raw) != manifest.get('binary_sha256'):
        raise ValueError('Seed input differs from the public binary binding')
    if 'entry_rva' in manifest and 'entry_points' not in manifest:
        entries = {'target': manifest['entry_rva']}
    elif 'entry_points' in manifest and 'entry_rva' not in manifest:
        entries = manifest['entry_points']
    else:
        raise ValueError('Known-entry task lacks an unambiguous public entry contract')
    if (not isinstance(entries, dict) or not 1 <= len(entries) <= 16
            or any(not isinstance(name, str) or not name or len(name) > 128 for name in entries)
            or any(type(value) is not int or not 0 <= value < 2**64 for value in entries.values())):
        raise ValueError('Invalid public entry points')
    segments = load_segments(raw)
    base = min(address & ~4095 for address, _, _ in segments)
    addresses = {name: base + rva for name, rva in sorted(entries.items())}
    if any(address >= 2**64 or not any(start <= address < start + size and flags & 1
            for start, size, flags in segments) for address in addresses.values()):
        raise ValueError('Public entry is outside initialized executable memory')
    text = ''.join(f'0x{address:x}\tfunction\n' for address in sorted(set(addresses.values())))
    return {'schema_version': 1, 'policy': POLICY, 'binary_sha256': manifest['binary_sha256'],
            'load_base': base, 'entry_addresses': addresses, 'seed_file': FILENAME,
            'seed_text': text, 'seed_sha256': sha(text.encode('ascii'))}


def prompt(context):
    if context is None:
        return b''
    addresses = {name: hex(address) for name, address in context['entry_addresses'].items()}
    return ('\n\nPUBLIC ANALYSIS CONTEXT: The analysis database is initialized at the '
            'function entries already supplied by this task. Function queries use these '
            'absolute source virtual addresses, derived from the public ELF mappings and RVAs: '
            + json.dumps(addresses, sort_keys=True, separators=(',', ':')) + '\n').encode()


def environment(context, directory):
    """The sole per-task environment override is a verified, fixed-name seed file."""
    if (not isinstance(context, dict) or context.get('policy') != POLICY
            or context.get('schema_version') != 1 or context.get('seed_file') != FILENAME
            or not isinstance(context.get('seed_text'), str)):
        raise ValueError('Invalid declared analysis context')
    raw = context['seed_text'].encode('ascii')
    if sha(raw) != context.get('seed_sha256'):
        raise ValueError('Analysis seed content differs from its declared digest')
    path = Path(directory) / FILENAME
    if path.is_symlink() or path.read_bytes() != raw:
        raise ValueError('Materialized analysis seeds changed')
    return {'REA_GHIDRA_SEED_FILE': str(path)}


def verify_materialized(task, configuration):
    directory = Path(task['input_directory'])
    gateway, harness = configuration['gateway'], configuration['harness']
    use = enabled(harness)
    withheld = task.get('evidence_access', 'binary') == 'premises_only'
    expected = None
    if use and not withheld:
        manifest = json.loads((directory / 'manifest.json').read_bytes())
        expected = derive((directory / 'target').read_bytes(), manifest, task['kind'])
    if gateway.get('analysis_context') != expected:
        raise ValueError('Effective analysis context differs from the public task')
    if expected is None:
        if (directory / FILENAME).exists():
            raise ValueError('Unbound analysis seed file is present')
    else:
        environment(expected, directory)
    return expected


def gate_fingerprints():
    return {name: sha((ROOT / name).read_bytes()) for name in GATE_SOURCES}


def public_catalog(task_ids=None):
    """Bind the tested public packages without reading private specifications."""
    import action_cache
    result = {}
    base = ROOT / '.cache/benchmark-capabilities/task-index'
    if task_ids is None:
        paths = sorted(base.glob('*.json'))
    else:
        task_ids = set(task_ids)
        if not task_ids or any(not isinstance(name, str) or not name
                or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in name) for name in task_ids):
            raise ValueError('Invalid selected public-context task IDs')
        paths = [base / (name + '.json') for name in sorted(task_ids)]
    for path in paths:
        if path.is_symlink() or path.stat().st_size > 1024**2:
            raise ValueError('Invalid public-context catalogue record')
        case = json.loads(path.read_bytes())
        if case['id'] != path.stem:
            raise ValueError('Public-context task index identity changed')
        directory = ROOT / action_cache.relative(case['public_directory'])
        if not directory.resolve().is_relative_to(ROOT / '.cache/benchmark-capabilities/task-freezes'):
            raise ValueError('Public context package escaped the frozen package root')
        hashes = {}
        for name in ('target', 'prompt.txt', 'manifest.json', 'tools.json'):
            file = directory / name
            if file.is_symlink():
                raise ValueError('Public context input is a symlink')
            hashes[name] = sha(file.read_bytes())
        if (hashes != case['public_files_sha256']
                or sha(action_cache.canonical(hashes)) != case['public_package_sha256']):
            raise ValueError('Public context package changed')
        result[case['id']] = {'kind': case['kind'], 'package': case['public_package_sha256']}
    if not result:
        raise ValueError('No frozen public packages for context validation')
    return result


from startup_policy import offline_gate

@offline_gate
def require_validation(runtime=None, *, task_ids=None):
    """Admit a source-bound offline gate; never launch tools or a provider."""
    import action_cache
    import cache
    from types import SimpleNamespace
    import runtime_attestation
    if not RECEIPT.is_file() or RECEIPT.is_symlink() or RECEIPT.stat().st_size > 1024**2:
        raise RuntimeError('Run make check-analysis-context before enabling supplied-entry seeds')
    selected = json.loads(RECEIPT.read_bytes())
    if (selected.get('schema_version') != 1 or not isinstance(selected.get('action'), dict)
            or not isinstance(selected.get('validation_id'), str)):
        raise RuntimeError('Analysis-context validation has not completed')
    descriptor, key = selected['action'], selected['validation_id']
    if sha(action_cache.canonical(descriptor)) != key or descriptor.get('kind') != 'analysis_context_validation_v1':
        raise ValueError('Analysis-context validation identity changed')
    store = action_cache.Cache()
    saved = store.lookup(SimpleNamespace(key=key, descriptor=descriptor))
    if saved is None:
        raise ValueError('Analysis-context validation action is missing')
    value = json.loads(store.get(saved['output_files']['validation.json']['digest']))
    if (value.get('passed') is not True or value.get('model_api_calls') != 0
            or type(value.get('checks')) is not int or value['checks'] < 22
            or value.get('inputs_sha256') != gate_fingerprints()):
        raise RuntimeError('Analysis-context controls are missing or stale; run make check-analysis-context')
    saved_catalog = json.loads(cache.action_input_bytes(descriptor, 'catalog.json'))
    current_catalog = public_catalog(task_ids)
    expected_catalog = saved_catalog if task_ids is None else {name: saved_catalog.get(name) for name in current_catalog}
    if current_catalog != expected_catalog:
        raise ValueError('Analysis-context public catalogue changed')
    for name, expected in value['inputs_sha256'].items():
        if sha(cache.action_input_bytes(descriptor, 'sources/' + name)) != expected:
            raise ValueError('Analysis-context source evidence changed')
    engine = value['engine_action']
    declared = {'action': engine, 'tool_components': value['tool_components'], 'native_entries': value['native_entries']}
    if cache.action_input_bytes(descriptor, 'engine.json') != action_cache.canonical(declared):
        raise ValueError('Analysis engine validation inputs changed')
    engine_key = sha(action_cache.canonical(engine))
    if engine.get('kind') != 'public_entry_gateway_smoke_v1':
        raise ValueError('Wrong analysis-context engine action')
    result = store.lookup(SimpleNamespace(key=engine_key, descriptor=engine))
    if result is None:
        raise ValueError('Analysis-context engine evidence is missing')
    proof = json.loads(store.get(result['output_files']['result.json']['digest']))
    evidence = store.get(result['output_files']['evidence-02-procedure_info.json']['digest'])
    seeds = cache.action_input_bytes(engine, 'analysis.seeds')
    target = cache.action_input_bytes(engine, 'target')
    if (proof.get('passed') is not True or proof.get('model_api_calls') != 0
            or proof.get('seed_sha256') != sha(seeds) or proof.get('binary_sha256') != sha(target)
            or proof.get('evidence_sha256') != sha(evidence)):
        raise ValueError('Analysis-context engine evidence differs from its declared inputs')
    if runtime is not None:
        components = runtime['manifest']['components']
        for name, expected in value['tool_components'].items():
            if components.get(name, {}).get('sha256') != expected:
                raise ValueError('Analysis tool components differ from the offline context gate')
        native = runtime_attestation.component_entries(components['native-loader-closure'], store.get)
        if any(native.get(name) != expected for name, expected in value['native_entries'].items()):
            raise ValueError('Analysis native dependencies differ from the offline context gate')
    return value
