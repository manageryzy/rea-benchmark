"""Identity of the current benchmark adapters; historical receipts stay unchanged."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'benchmarks/config/evidence-delivery.json'


def current(harness, condition='lossless-evidence-v1'):
    config = json.loads(CONFIG.read_text())
    files = [CONFIG, ROOT / 'benchmarks/config/conditions.json', Path(__file__)]
    files += [ROOT / 'benchmarks/scripts' / name for name in ['run.py', 'cache.py', 'benchmark_config.py']]
    files += [ROOT / 'benchmarks/scripts' / name for name in
              ['rea-gateway.mjs', 'policy.mjs', 'tool-catalog.mjs', 'execution-control.mjs']]
    names = ['deepseek-harness.mjs'] if harness == 'deepseek-harness' else ['zcode.py', 'zcode-evidence.cjs']
    files += [ROOT / 'benchmarks/harnesses' / name for name in [*names, 'dispatch.py']]
    if condition == 'paged-evidence-v2':
        files += [ROOT / 'benchmarks/config/evidence-paging.json', ROOT / 'benchmarks/scripts/evidence-paging.mjs']
        config = json.loads((ROOT / 'benchmarks/config/evidence-paging.json').read_text())
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    value = {'id': config['id'], 'harness': harness, 'files_sha256': hashes}
    value['fingerprint'] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value
