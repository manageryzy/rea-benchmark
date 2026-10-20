"""Validate an offline replay receipt during report builds, without loading clients."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / 'benchmarks'
RECEIPT = BENCH / 'config/evidence-validation.json'


def for_report(read_bytes, receipt=RECEIPT):
    try:
        value = json.loads(read_bytes(receipt))
    except (OSError, ValueError):
        return {'passed': False, 'inputs_current': False, 'reason': 'Offline validation receipt is missing'}
    hashes = value.get('inputs_sha256', {})
    current = bool(hashes)
    for path, expected in hashes.items():
        source = (ROOT / path).resolve()
        allowed = (source.is_relative_to(BENCH) and not source.is_relative_to(BENCH / 'reports')) or source in {
            ROOT / 'upstream/rea/scripts/verify/clients/model-fixture.mjs', ROOT / 'Makefile', ROOT / 'bin/benchmark'}
        try:
            valid = allowed and hashlib.sha256(read_bytes(source)).hexdigest() == expected
        except OSError:
            valid = False
        current = current and valid
    value['inputs_current'] = current
    value['passed'] = value.get('passed', False) and current
    if not current:
        value['reason'] = 'Adapter, configuration, test or cached input changed; rerun make check-environment'
    return value
