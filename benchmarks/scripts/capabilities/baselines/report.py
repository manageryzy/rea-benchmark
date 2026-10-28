"""Read-only aggregate preparation receipts; never compile, execute or infer."""
import hashlib
import json

import action_cache
from grading import image_manifest
from grading.c_executor import runtime as executor
from . import build, evidence, tooling, rust_evidence, go_evidence

ROOT = action_cache.ROOT
SCOPES = (
    ('matched_baselines', evidence, 'matched_original_baselines_and_c_cpp_controls_only'),
    ('rust_buffers', rust_evidence, 'restricted_no_std_rust2021_buffer_controls_only'),
    ('go_buffers', go_evidence, 'restricted_go_abiinternal_buffer_controls_only'),
)


def _json(read_bytes, path):
    return json.loads(read_bytes(path))


def _identity(value):
    action_cache.valid_digest({'hash': value, 'size_bytes': 0})
    return value


def _track_pin_inputs(read_bytes):
    """Declare the metadata consumed by pins(), without materializing SDKs."""
    _json(read_bytes, image_manifest.CONFIG)
    active = _json(read_bytes, image_manifest.CACHE / 'active.json')
    for language in ('c', 'cpp', 'rust', 'go'):
        identity = _identity(active[language]['id'])
        _json(read_bytes, image_manifest.CACHE / 'profiles' / (identity + '.json'))
    for directory in (build.family_build.TOOLS, tooling.CACHE):
        identity = _identity(_json(read_bytes, directory / 'active.json')['id'])
        _json(read_bytes, directory / (identity + '.json'))
    configuration = _json(read_bytes, executor.CONFIG)
    engine = _identity(_json(read_bytes, executor.CACHE / 'active.json')['id'])
    _json(read_bytes, executor.CACHE / (engine + '.json'))
    wheel = _identity(configuration['engine']['sha256'])
    _json(read_bytes, executor.CACHE / 'provenance' / engine / (wheel + '.json'))


def for_report(read_bytes):
    rows = []
    for name, module, scope in SCOPES:
        row = {'id': name, 'passed': False, 'current': False, 'checks': 0, 'failed': 0}
        if module.RECEIPT.is_file():
            value = _json(read_bytes, module.RECEIPT)
            checks, failed = value.get('checks'), value.get('failed')
            if (type(value.get('schema_version')) is not int or value['schema_version'] != 1
                    or value.get('model_api_calls') != 0 or type(value.get('model_api_calls')) is not int
                    or value.get('release_accepted') is not False or value.get('scope') != scope
                    or type(value.get('passed')) is not bool
                    or any(type(number) is not int or not 0 <= number <= 10000 for number in (checks, failed))):
                raise ValueError('Invalid matched-baseline offline receipt')
            current = False
            try:
                actual = module.fingerprints()
                tracked = {path: hashlib.sha256(read_bytes(ROOT / path)).hexdigest() for path in actual}
                _track_pin_inputs(read_bytes)
                current = (actual == tracked == value.get('inputs_sha256')
                           and value.get('pins') == module.pins())
            except (OSError, ValueError, RuntimeError, KeyError):
                # Historical acceptance stays visible when its local source or
                # SDK metadata is unavailable. It cannot be called current.
                current = False
            row.update(passed=value['passed'] and failed == 0 and checks >= module.MINIMUM_CHECKS,
                       current=current, checks=checks, failed=failed)
        rows.append(row)
    return rows
