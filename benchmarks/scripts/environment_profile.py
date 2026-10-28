"""Identity of the current benchmark adapters; historical receipts stay unchanged."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'benchmarks/config/evidence-delivery.json'


def paging_policy_path(condition):
    """Resolve an explicit condition policy while retaining the frozen v2 path."""
    if condition == 'paged-evidence-v2':
        return ROOT / 'benchmarks/config/evidence-paging.json'
    catalog = json.loads((ROOT / 'benchmarks/config/conditions.json').read_text())
    selected = next((row for row in catalog['conditions'] if row['id'] == condition), {})
    name = selected.get('paging_policy')
    if name is None:
        return None
    relative = Path(name)
    if (relative.is_absolute() or '..' in relative.parts
            or relative.parent != Path('benchmarks/config') or relative.suffix != '.json'):
        raise ValueError('Evidence paging policy must be a benchmark configuration file')
    return ROOT / relative


def current(harness, condition='lossless-evidence-v1'):
    config = json.loads(CONFIG.read_text())
    files = [CONFIG, ROOT / 'benchmarks/config/conditions.json', Path(__file__)]
    files += [ROOT / 'benchmarks/scripts' / name for name in ['run.py', 'cache.py', 'benchmark_config.py']]
    files += [ROOT / 'benchmarks/scripts' / name for name in
              ['rea-gateway.mjs', 'policy.mjs', 'tool-catalog.mjs', 'execution-control.mjs']]
    names = ['deepseek-harness.mjs'] if harness == 'deepseek-harness' else ['zcode.py', 'zcode-evidence.cjs']
    files += [ROOT / 'benchmarks/harnesses' / name for name in [*names, 'dispatch.py']]
    paging = paging_policy_path(condition)
    if paging is not None:
        files += [paging, ROOT / 'benchmarks/scripts/evidence-paging.mjs']
        config = json.loads(paging.read_text())
        if config.get('id') != condition:
            raise ValueError('Evidence paging policy differs from its condition')
    if condition == 'lean-evidence-v4':
        files.append(ROOT / 'benchmarks/scripts/adaptive_budget.py')
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    value = {'id': config['id'], 'harness': harness, 'files_sha256': hashes}
    value['fingerprint'] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value
