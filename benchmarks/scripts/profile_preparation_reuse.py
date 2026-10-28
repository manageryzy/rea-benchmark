"""Read-only before/after replay on saved task inputs. Never invokes a model.

This profiles frozen inputs, grading contracts and tool projection, not the full
admission gate or a benchmark run. Its timings must not be called full startup.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import time
from unittest.mock import patch

import action_cache
import benchmark_trace
import preparation_session
import read_session
import run
from capabilities import tasks, store, registration

CASES = ('record_codec_c_recover', 'record_codec_cpp_recover',
         'record_codec_clang20_cpp_recover', 'record_codec_rust_c_recover',
         'record_codec_go_c_recover', 'record_codec_sysv_amd64_layout_recover')


def replay(names, trace, optimized):
    counts = {'stat_calls': 0}
    original = read_session.signature
    def stat(path):
        counts['stat_calls'] += 1
        return original(path)
    outputs = []
    started = time.perf_counter()
    with (patch.object(socket.socket, 'connect', side_effect=AssertionError('No network in preparation replay')),
          patch.object(run, 'launch_model', side_effect=AssertionError('No model in preparation replay')),
          read_session.ReadSession(),
          preparation_session.scope(True) if optimized else nullcontext(),
          patch.object(read_session, 'signature', stat),
          benchmark_trace.Trace(trace, detail=True, render_html=False, scopes={}) as recorder,
          recorder.instrument(benchmark_trace.SCOPES, detail=True)):
        # Real saved inputs: two model variants, resolution and binding passes.
        for phase in ('resolution_replay', 'binding_replay'):
            with recorder.span(phase):
                for model in ('offline-a', 'offline-b'):
                    for name in names:
                        case = tasks.frozen(name)
                        task = {**case, 'model': model, 'track': 'capability', 'artifact_contract_version': 1}
                        spec = store.spec(task)
                        score = registration.scoring_state(task)
                        tools = run.paged_tools((action_cache.ROOT / case['public_directory'] / 'tools.json').read_bytes())
                        outputs.append(hashlib.sha256(action_cache.canonical([case, spec, score]) + tools).hexdigest())
    events = json.loads(trace.read_text())['traceEvents']
    beginnings, phases = {}, {}
    for event in events:
        if event.get('name') not in ('resolution_replay', 'binding_replay'):
            continue
        if event.get('ph') == 'B':
            beginnings[event['name']] = event['ts']
        elif event.get('ph') == 'E':
            phases[event['name']] = round((event['ts'] - beginnings[event['name']]) / 1e6, 6)
    return {'seconds': round(time.perf_counter() - started, 6), **counts, 'phases_seconds': phases,
            'output_sha256': hashlib.sha256(action_cache.canonical(outputs)).hexdigest(),
            'operations': len(outputs), 'trace': str(trace.resolve())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=action_cache.ROOT / '.cache/benchmark-runtime/preparation-reuse-profile')
    parser.add_argument('--cases', nargs='+', default=list(CASES))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    directory = args.output / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir()  # Never overwrite a previous trace or timing receipt.
    result = {'scope': 'offline saved-input replay; not full startup or current gate acceptance',
              'cases': args.cases, 'model_api_calls': 0}
    for label, optimized in (('before', False), ('after', True)):
        result[label] = replay(args.cases, directory / (label + '.trace.json'), optimized)
        print(label, json.dumps(result[label]), flush=True)
    if result['before']['output_sha256'] != result['after']['output_sha256']:
        raise RuntimeError('Optimization changed the task/grading/tool projection')
    result['speedup'] = result['before']['seconds'] / result['after']['seconds']
    (directory / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    (args.output / 'latest.json').write_text(json.dumps({'summary': str((directory / 'summary.json').resolve())}) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
