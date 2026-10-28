#!/usr/bin/python3
"""Record per-command timings for the explicitly requested cold regression suite."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time

import action_cache
from runtime_storage import atomic

RECEIPT = action_cache.ROOT / 'benchmarks/config/cold-cache-timings.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'run', 'finish'))
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'start':
        value = {'schema_version': 1, 'scope': 'cold_regression_commands', 'passed': False,
                 'measured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'total_seconds': 0.0, 'complete': False, 'stages': []}
    elif args.action == 'finish':
        value = json.loads(RECEIPT.read_bytes())
        value['complete'] = True
        value['passed'] = bool(value['stages']) and all(row['exit_code'] == 0 for row in value['stages'])
    else:
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if len(command) < 2:
            parser.error('run requires the Python check command after --')
        value = json.loads(RECEIPT.read_bytes())
        started = time.monotonic()
        try:
            code = subprocess.run(command).returncode
            if code < 0:
                code = 128 - code
        finally:
            seconds = round(time.monotonic() - started, 6)
            label = Path(command[1]).stem
            value['stages'].append({'name': label, 'seconds': seconds, 'exit_code': locals().get('code', 130)})
            value['total_seconds'] = round(sum(row['seconds'] for row in value['stages']), 6)
            value['passed'] = False  # Only finish certifies the whole command list.
            atomic(RECEIPT, action_cache.canonical(value) + b'\n')
            print(f'COLD TIMING: {label}: {seconds:.3f}s', flush=True)
        return code if code >= 0 else 128 - code
    atomic(RECEIPT, action_cache.canonical(value) + b'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
