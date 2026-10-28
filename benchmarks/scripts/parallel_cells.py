"""Persistent local workers: independent preparation caches and parallel graders.

The shared runtime image is already mounted by the launcher. Workers inherit
that mount and credentials through the environment; no workspace is copied.
Only explicit task results and a launch counter cross the process boundary.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import threading
import time

_STATE = None


def initialize(mode, cancelled, stop_epoch, launches, trace_directory, origin, compile_barrier=None):
    global _STATE
    from multiprocessing.util import Finalize
    import startup_policy
    from read_session import ReadSession
    import benchmark_trace
    stack = ExitStack()
    stack.enter_context(startup_policy.scope(mode))
    stack.enter_context(ReadSession())
    trace_path = Path(trace_directory) / ('worker-' + str(os.getpid()) + '.json')
    recorder = stack.enter_context(benchmark_trace.Trace(trace_path, epoch_ns=origin,
        process_id=os.getpid(), detail=True, render_html=False))
    cancelled.stop_epoch = stop_epoch
    _STATE = {'cancel': cancelled, 'launches': launches, 'recorder': recorder, 'stack': stack,
              'compile_barrier': compile_barrier}
    Finalize(None, stack.close, exitpriority=10)
    print('CELL WORKER TRACE:', trace_path, flush=True)


def execute(entry, model, measurement, offline):
    import run
    import preparation_session
    from capabilities.registration import Registry
    state = _STATE
    if state is None:
        raise RuntimeError('Cell worker was not initialized')
    cancelled = state['cancel']
    def on_launch():
        with state['launches'].get_lock():
            state['launches'].value += 1
    with preparation_session.scope(True):
        run._execution_allowed(cancelled)
        with state['recorder'].span('cell_preparation:' + model['id'] + ':' + entry['case']):
            entry = run.prepare_next_cell(entry, model, Registry(), measurement, offline=offline)
        run._execution_allowed(cancelled)
        result = run.execute(entry, model, cancelled, on_launch)
        return {**result, 'directory': entry['directory']}


def probe(spec):
    """Real preparation and compilation control in the same worker pool; no API."""
    import argparse
    import profile_startup
    args = argparse.Namespace(case=spec['case'], model=spec['model'],
        output=Path(spec['output']), origin=spec['origin'])
    args.output.mkdir(parents=True, exist_ok=True)
    result = profile_startup.measure(args, before_compile=_STATE['compile_barrier'].wait if _STATE['compile_barrier'] else None)
    report = json.loads((args.output/'summary.json').read_text())
    return {'exit_code': result, 'pid': os.getpid(), **report}
