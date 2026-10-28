"""Source-bound prototype acceptance receipts; no raw guest data in reports."""
import hashlib
import json
from pathlib import Path

import action_cache

ROOT = Path(__file__).resolve().parents[4]
PINS = ROOT / 'benchmarks/config/c-executor-pins.json'
RECEIPT = ROOT / 'benchmarks/config/c-executor-validation.json'


def names():
    result = {'benchmarks/config/c-executor.json', 'benchmarks/scripts/check_c_executor.py',
              'benchmarks/scripts/grading/c_executor/evidence.py', 'benchmarks/scripts/grading/c_executor/setup.py',
              str(PINS.relative_to(ROOT))}
    for stage in ('c_compile', 'c_execute'):
        result.update(action_cache.dependencies(stage))
    return sorted(result)


def save_pins():
    from . import runtime
    identity, record = runtime.engine_record()
    value = {'schema_version': 1, 'engine_id': identity, 'engine_version': record['engine']['version'],
             'base_image': record['base_image'], 'module_files': len(record['files']),
             'module_bytes': sum(item['bytes'] for item in record['files'].values())}
    raw = json.dumps(value, indent=2, sort_keys=True) + '\n'
    if not PINS.exists() or PINS.read_text() != raw:
        PINS.write_text(raw)
    return value


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names()}


def begin():
    pins = save_pins()
    value = {'schema_version': 1, 'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0,
             'pins': pins, 'sources': fingerprints()}
    RECEIPT.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    return value


def finish(started, result):
    if started['sources'] != fingerprints():
        raise RuntimeError('C executor validation inputs changed while controls were running')
    value = {**started, 'passed': result.wasSuccessful(), 'checks': result.testsRun,
             'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def for_report(read_bytes):
    value = {'status': 'not_recorded', 'controls_passed': False, 'controls_current': False,
             'checks': 0, 'failed': 0, 'model_api_calls': 0, 'engine_version': '',
             'engine_id': '', 'base_image': '', 'module_files': 0, 'module_bytes': 0}
    if not RECEIPT.exists():
        return value
    receipt = json.loads(read_bytes(RECEIPT))
    if receipt.get('schema_version') != 1 or receipt.get('model_api_calls') != 0:
        raise ValueError('Invalid C executor control receipt')
    current = set(receipt['sources']) == set(names())
    for name, expected in receipt['sources'].items():
        path = ROOT.joinpath(*action_cache.relative(name).parts)
        current = (path.is_file() and hashlib.sha256(read_bytes(path)).hexdigest() == expected) and current
    pins = receipt['pins']
    return {**value, 'status': 'saved_offline_controls', 'controls_passed': bool(receipt['passed']),
            'controls_current': current, 'checks': receipt['checks'], 'failed': receipt['failed'],
            **{key: pins[key] for key in ('engine_id', 'engine_version', 'base_image', 'module_files', 'module_bytes')}}


from startup_policy import offline_gate

@offline_gate
def require_validation():
    value = for_report(lambda path: path.read_bytes())
    if not value['controls_passed'] or not value['controls_current']:
        raise RuntimeError('C executor controls are missing or stale; run make check-c-executor')
    from . import runtime, image
    if runtime.engine_record()[0] != value['engine_id'] or image.record()['id'] != value['base_image']:
        raise RuntimeError('C executor runtime changed after validation; run make setup-c-executor and make check-c-executor')
