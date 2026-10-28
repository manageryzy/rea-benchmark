"""Measure real startup admission and warm preparation/boundaries, without inference."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

import runtime_attestation as runtime
import runtime_snapshot
import run
from benchmark_trace import Trace


def main():
    root = Path(__file__).resolve().parents[2]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = root/('.cache/benchmark-runtime/profiling/runtime-reuse-' + stamp)
    output.parent.mkdir(parents=True, exist_ok=True)
    samples = []
    def measure(name, function):
        start = time.perf_counter(); result = function()
        samples.append({'stage': name, 'seconds': time.perf_counter()-start})
        print(name, '%.6fs' % samples[-1]['seconds'], flush=True)
        return result
    # No launch, replay, grade or report entrypoint is called. Protect accidental
    # Python network access and explicitly forbid the dispatch callback as well.
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline probe: network forbidden')), \
         patch.object(run, 'launch_model', side_effect=AssertionError('Offline probe: provider forbidden')), \
         Trace(str(output)+'.trace.json', render_html=False, scopes={
             'runtime_attestation.py': {'current_runtime','capture','verify','verify_configuration','provider','shared_libraries','tree'},
             'runtime_snapshot.py': {'current','populate','reuse'},
             'run.py': {'prepare_task'}}):
        for harness in ['deepseek-harness','zcode']:
            measure(harness+':startup_capture', lambda: runtime.current_runtime(harness))
        for model in ['deepseek-flash','deepseek-v4-pro','glm-5.3']:
            entry = {'experiment':'native-pilot-20261019','suite':'record-parser',
                'case':'cpp--clang20--O2','model':model,'condition':'compact-evidence-v3',
                'directory':f'benchmarks/runs/native-pilot-20261019/conditions/compact-evidence-v3/{model}/cpp--clang20--O2'}
            task = measure(model+':prepare_materialized', lambda: run.prepare_task(entry, run.records.models()[model]))
            for phase in ['before','after']:
                proof = measure(model+':verify_'+phase, lambda: runtime.verify(task))
                assert proof['verification_method'] == 'startup_once_trusted_environment'
            task2 = measure(model+':prepare_reused', lambda: run.prepare_task(entry, run.records.models()[model], materialize=False))
            assert task2['fingerprint'] == task['fingerprint']
        assert len(runtime_snapshot._RUNTIMES) == 2
    result = {'schema_version':1, 'model_api_calls':0, 'runtime_captures':2,
        'policy':'startup_once_trusted_environment', 'samples':samples}
    output.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Offline runtime reuse measurement complete; model API calls=0',flush=True)


if __name__ == '__main__':
    main()
