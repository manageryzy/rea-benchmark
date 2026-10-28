"""Bounded offline timings of existing frozen preparations; never build or infer."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kinds', nargs='+', default=['pointer_probe', 'native_abi', 'matched_buffer'])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manifests-only', action='store_true', help='Measure guarded compiler manifest reuse only')
    parser.add_argument('--languages', nargs='+', default=['cpp', 'rust', 'go'])
    parser.add_argument('--selection-only', action='store_true', help='Time full declaration selection; forbid preparation/admission')
    parser.add_argument('--models', nargs='+', default=['deepseek-flash', 'deepseek-v4-pro', 'glm-5.3'])
    parser.add_argument('--later', action='store_true', help='Measure analysis admission and grading binding after preparation')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error('--repeats must be 1..10')
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        args.output = Path('.cache/benchmark-runtime/profiling') / f'preparation-{stamp}-{os.getpid()}.measurements.json'
    from capabilities import tasks, preparation, store, analysis_context, registration
    from grading.metadata_admission import metadata_validation_cache
    rows = []
    os.environ['REA_BENCHMARK_OFFLINE'] = '1'
    with ExitStack() as guard:
        for owner, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'), (subprocess, 'Popen')):
            guard.enter_context(patch.object(owner, name, side_effect=RuntimeError('No network/processes in preparation profile')))
        if args.manifests_only:
            from grading import image_manifest
            previous = {}
            for language in args.languages:
                for repeat in range(args.repeats):
                    with metadata_validation_cache():
                        start = time.perf_counter()
                        value = image_manifest.load(language)
                        elapsed = (time.perf_counter() - start) * 1000
                    row = {'operation': 'image_manifest.load', 'language': language, 'repeat': repeat,
                           'milliseconds': elapsed, 'same_object_as_previous': value is previous.get(language),
                           'result_sha256': image_manifest.digest(value)}
                    previous[language] = value
                    rows.append(row)
                    print(json.dumps(row), flush=True)
        if args.selection_only:
            import benchmark_config
            import run
            selection = benchmark_config.load()
            selection['models'] = args.models
            guard.enter_context(patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}))
            for owner, name in ((preparation, 'inspect'), (tasks, 'frozen'), (store, 'spec')):
                guard.enter_context(patch.object(owner, name, side_effect=AssertionError('Selection must not validate preparations')))
            for repeat in range(args.repeats):
                start = time.perf_counter()
                selected = run.selected_entries(selection)
                row = {'operation': 'selected_entries', 'repeat': repeat,
                       'milliseconds': (time.perf_counter() - start) * 1000,
                       'scheduled': run.scheduled_counts(selected)}
                rows.append(row)
                print(json.dumps(row), flush=True)
        declarations = [] if args.selection_only or args.manifests_only else tasks.declared()
        for kind in ([] if args.selection_only or args.manifests_only else args.kinds):
            declaration = next(d for d in declarations if d['profile']['kind'] == kind)
            if args.later:
                with metadata_validation_cache():
                    prepared = preparation.inspect(declaration)
                    if prepared.get('status') != 'ready': raise RuntimeError('Existing preparation not ready')
                    task = {**tasks.frozen(declaration['task_id']), 'track': 'capability', 'artifact_contract_version': 1}
            for repeat in range(args.repeats):
                # Production selection/resolution each open a fresh scope.
                with metadata_validation_cache():
                    case = None
                    for name in (('analysis_admission', 'scoring_binding') if args.later else ('inspect', 'action', 'frozen', 'spec')):
                        start = time.perf_counter()
                        if name == 'analysis_admission': value = analysis_context.require_validation(task_ids={task['id']})
                        elif name == 'scoring_binding': value = registration.scoring_state(task)
                        elif name == 'inspect': value = preparation.inspect(declaration)
                        elif name == 'action':
                            action = preparation.action(declaration)
                            value = {'key': action.key, 'descriptor': action.descriptor}
                        elif name == 'frozen': value = case = tasks.frozen(declaration['task_id'])
                        else: value = store.spec({**case, 'track': 'capability', 'artifact_contract_version': 1})
                        elapsed = (time.perf_counter() - start) * 1000
                        if name == 'inspect' and value.get('status') != 'ready':
                            raise RuntimeError('Existing preparation is not ready: ' + str(value.get('reason')))
                        row = {'kind': kind, 'task': declaration['task_id'], 'repeat': repeat,
                               'operation': name, 'milliseconds': elapsed,
                               'result_sha256': hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()}
                        rows.append(row)
                        print(json.dumps(row), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump({'model_calls': 0, 'process_launches': 0, 'measurements': rows}, stream, indent=2)
    print('MEASUREMENTS: ' + str(args.output), flush=True)


if __name__ == '__main__':
    main()
