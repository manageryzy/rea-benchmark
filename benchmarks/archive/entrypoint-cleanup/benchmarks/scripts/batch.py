#!/usr/bin/python3
"""Load a named batch configuration and delegate to the existing coordinator."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess

import records

DEFAULT_CONFIG = records.BENCH / 'config/batches.json'
LIVE_LOCK = records.ROOT / '.cache/benchmark-runtime/live-batch.lock'


def load_batch(path, name=None):
    catalog = json.loads(path.read_text())
    if catalog.get('schema_version') != 1:
        raise ValueError('Unsupported batch configuration schema')
    name = name or catalog.get('default_batch')
    if name not in catalog.get('batches', {}):
        raise ValueError('Unknown batch: ' + str(name))
    batch = catalog['batches'][name]
    allowed = {'description', 'models', 'suite', 'condition', 'report_every', 'experiment'}
    if set(batch) - allowed:
        raise ValueError('Unknown batch settings: ' + ', '.join(sorted(set(batch) - allowed)))
    ids = batch.get('models')
    if not isinstance(ids, list) or not ids or any(not isinstance(mid, str) or mid not in records.models() for mid in ids):
        raise ValueError('Batch models must be a nonempty list of registered model IDs')
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate model IDs in batch configuration')
    condition = next((c for c in records.condition_catalog()['conditions'] if c['id'] == batch.get('condition')), None)
    if condition is None:
        raise ValueError('Batch condition is not registered')
    suites = {e['suite'] for e in records.registry()['experiments'] if e['role'] == 'primary'}
    suite = batch.get('suite', 'all')
    if suite != 'all' and (suite not in suites or suite in condition.get('retired_suites', [])
                          or condition['id'] != 'legacy-recorded' and suite not in condition.get('suites', [])):
        raise ValueError('Batch suite is unavailable under the selected condition')
    if 'experiment' in batch and not any(e['id'] == batch['experiment'] and e['role'] == 'primary'
                                       and (suite == 'all' or e['suite'] == suite) for e in records.registry()['experiments']):
        raise ValueError('Batch experiment is not registered under the selected suite')
    cadence = batch.get('report_every', 1)
    if type(cadence) is not int or cadence < 0:
        raise ValueError('report_every must be a nonnegative integer')
    return name, batch


def command(batch):
    result = [str(records.ROOT / 'bin/benchmark'), 'run', '--models', *batch['models'],
              '--suite', batch.get('suite', 'all'), '--condition', batch['condition'],
              '--report-every', str(batch.get('report_every', 1))]
    if batch.get('experiment'):
        result += ['--experiment', batch['experiment']]
    return result


def active_batch():
    return json.loads(LIVE_LOCK.read_text()) if LIVE_LOCK.exists() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'run'])
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--batch')
    parser.add_argument('--json', action='store_true', help='Print a machine-readable plan; no model calls')
    args = parser.parse_args()
    try:
        name, batch = load_batch(args.config, args.batch)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.action == 'run':
        if args.json:
            parser.error('--json is only available for a read-only plan')
        if active_batch():
            parser.error('A benchmark batch is already active; no duplicate batch was started')
        print('Running configured batch:', name, '| models:', ', '.join(batch['models']), flush=True)
        argv = command(batch) + ['--resume-experiments']
        os.execv(argv[0], argv)
    result = subprocess.run(command(batch) + ['--dry-run'], cwd=records.ROOT,
                            capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    plan.update(batch=name, batch_config=str(args.config), selection=batch, live_batch=active_batch())
    if args.json:
        print(json.dumps(plan, indent=2))
        return
    print('Batch:', name)
    print('Config:', args.config.relative_to(records.ROOT) if args.config.is_relative_to(records.ROOT) else args.config)
    print('Models:', ', '.join(batch['models']))
    print('Suite:', batch.get('suite', 'all'), '| condition:', batch['condition'])
    print('Tasks:', len(plan['tasks']), '| statuses:', dict(Counter(e['status'] for e in plan['tasks'])))
    if plan['live_batch']:
        print('A batch is currently active; task-file-only states may belong to its running attempts.')
    print('Model calls: 0. Completed attempts are reused; interrupted attempts are never retried automatically.')


if __name__ == '__main__':
    main()
