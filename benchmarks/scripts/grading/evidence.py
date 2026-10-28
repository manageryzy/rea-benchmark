"""Read-only report projection of saved grading-runtime acceptance evidence."""
import hashlib
import json
from pathlib import Path

import action_cache

ROOT = Path(__file__).resolve().parents[3]
RECEIPT = ROOT / 'benchmarks/config/grading-runtime-validation.json'
PINS = ROOT / 'benchmarks/config/grading-runtime-pins.json'


def input_names():
    names = {'benchmarks/scripts/check_grading_runtime.py', 'benchmarks/scripts/grading/toolchains.py',
             'benchmarks/scripts/grading/evidence.py', 'benchmarks/config/grading-environments.json',
             str(PINS.relative_to(ROOT))}
    for language in ('c', 'cpp', 'rust', 'go'):
        names.update(action_cache.dependencies('grading', language=language))
    return sorted(names)


def save_pins():
    """Project current local image selections without exporting cache internals."""
    from . import image_manifest as toolchains
    active = json.loads((toolchains.CACHE / 'active.json').read_bytes())
    profiles = {}
    for language in sorted(active):
        try:
            record = toolchains.load(language)
        except RuntimeError:
            continue
        profiles[language] = {'id': record['id'], 'files': record['files'], 'bytes': record['bytes'],
                              'compiler_version': record['manifest']['compiler']['version'].splitlines()[0]}
    raw = json.dumps({'schema_version': 1, 'profiles': profiles}, indent=2, sort_keys=True) + '\n'
    if not PINS.exists() or PINS.read_text() != raw:
        temporary = PINS.with_suffix('.tmp')
        temporary.write_text(raw)
        temporary.replace(PINS)


def save(checks):
    from . import image_manifest as toolchains, runtime
    profiles = {}
    save_pins()
    for language in ('c', 'cpp', 'rust', 'go'):
        runtime.require_validation(language)
        record = toolchains.load(language)
        profiles[language] = {'id': record['id'], 'files': record['files'], 'bytes': record['bytes'],
                              'compiler_version': record['manifest']['compiler']['version'].splitlines()[0]}
    value = {'schema_version': 1, 'passed': True, 'checks': checks, 'model_api_calls': 0, 'profiles': profiles,
             'sources': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in input_names()}}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    temporary = RECEIPT.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(RECEIPT)


def for_report(read_bytes):
    result = {'status': 'not_recorded', 'controls_passed': False, 'controls_current': False,
              'checks': 0, 'model_api_calls': 0, 'profiles': {}}
    if not RECEIPT.exists():
        return result
    receipt = json.loads(read_bytes(RECEIPT))
    if receipt.get('schema_version') != 1 or receipt.get('model_api_calls') != 0:
        raise ValueError('Invalid grading runtime controls receipt')
    current = set(receipt['sources']) == set(input_names())
    for name, expected in receipt['sources'].items():
        path = ROOT.joinpath(*action_cache.relative(name).parts)
        current = (hashlib.sha256(read_bytes(path)).hexdigest() == expected if path.exists() else False) and current
    return {**result, 'status': 'saved_offline_controls', 'controls_passed': bool(receipt.get('passed')),
            'controls_current': current, 'checks': receipt['checks'], 'profiles': receipt['profiles']}
