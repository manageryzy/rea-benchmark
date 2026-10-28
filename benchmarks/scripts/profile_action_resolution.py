"""Offline full-catalogue timings; no inference dispatch or validation reruns.

gates measures real current admission (and records a stale receipt as failure).
components prepares all real task inputs with offline_fixture=True: excludes
provider/runtime capture and admission, and cannot authorize a model launch.
"""
import argparse
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
from read_session import ReadSession
import records
import run
from capabilities import coordinator, tasks, config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('gates', 'components', 'resolve'), required=True)
    parser.add_argument('--label', default='current')
    parser.add_argument('--output', type=Path, default=action_cache.ROOT / '.cache/benchmark-runtime/action-resolution-profile')
    args = parser.parse_args()
    directory = args.output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + args.label)
    directory.mkdir(parents=True)
    report = {'mode': args.mode, 'label': args.label, 'model_api_calls': 0,
              'scope': ('real admission, no tests rerun' if args.mode == 'gates' else
                  'real selected-entry inspection and action resolution; no dispatch' if args.mode == 'resolve' else
                  'real saved task input preparation; offline fixtures; excludes gates/provider/runtime capture'),
              'phases': []}
    trace = directory / 'trace.json'
    def phase(name, operation):
        started = time.perf_counter()
        try:
            with recorder.span(name):
                return operation()
        finally:
            row = {'stage': name, 'seconds': round(time.perf_counter() - started, 6)}
            report['phases'].append(row)
            print(json.dumps(row), flush=True)
    try:
        with (patch.object(socket.socket, 'connect', side_effect=AssertionError('No network in timing replay')),
              patch.object(run, 'launch_model', side_effect=AssertionError('No model in timing replay')),
              ReadSession(), preparation_session.scope(True),
              benchmark_trace.Trace(trace, detail=True, render_html=False, scopes={}) as recorder,
              recorder.instrument(benchmark_trace.SCOPES, detail=True)):
            models = records.models()
            settings, _ = config.load()
            declarations = [d for d in tasks.declared() if d['profile']['enabled']]
            report['cases'] = len(declarations)
            report['models'] = ['deepseek-flash', 'glm-5.3']
            if args.mode == 'resolve':
                entries = [row for mid in report['models'] for row in coordinator.entries(models[mid], deferred=True)]
                entries = phase('preparation_inspection', lambda: coordinator.inspect_selected(entries))
                resolved = phase('action_resolution', lambda: coordinator.resolve_selected(entries))
                report.update(cells=len(resolved), resolved=sum('resolved_fingerprint' in row for row in resolved))
                if report['resolved'] != report['cells']:
                    raise RuntimeError('Not all selected actions were resolved')
                phase('resolved_action_reuse', lambda: coordinator.resolve_selected(resolved))
            elif args.mode == 'gates':
                cases = phase('frozen_inventory', lambda: [tasks.frozen(d['task_id']) for d in declarations])
                representatives = {}
                for case in cases: representatives.setdefault((case['kind'], case.get('output_language')), case)
                report['gate_groups'] = len(representatives)
                for repeat in ('first', 'reuse'):
                    for kind, case in representatives.items():
                        phase(repeat + ':' + '/'.join(str(x) for x in kind), lambda: coordinator.require_case_validation(case))
            else:
                cases = phase('frozen_inventory', lambda: [tasks.frozen(d['task_id']) for d in declarations])
                outputs = []
                for repeat in ('first', 'reuse'):
                    digests = []
                    for model_id in report['models']:
                        model = models[model_id]
                        def prepare_all():
                            for case in cases:
                                entry = {'experiment': 'capabilities-v1', 'suite': 'capabilities-v1',
                                    'condition': settings['condition'], 'case': case['id'], 'model': model_id,
                                    'capability_case': case, 'status': 'not_started',
                                    'directory': str(coordinator.attempt_directory(model, case,
                                        coordinator._namespace(settings, model, 'binary')).relative_to(action_cache.ROOT))}
                                task = coordinator.prepare(entry, model, materialize=False, offline_fixture=True)
                                digests.append(task['fingerprint'])
                        phase(repeat + ':' + model_id, prepare_all)
                    outputs.append(hashlib.sha256(action_cache.canonical(digests)).hexdigest())
                if outputs[0] != outputs[1]:
                    raise RuntimeError('Preparation changed between passes')
                report.update(cells=len(cases) * 2, output_sha256=outputs[0], passes_match=True)
            report['status'] = 'complete'
    except Exception as error:
        report.update(status='blocked', error=str(error))
    if 'recorder' in locals():
        report['function_totals'] = {name: {'calls': count, 'inclusive_seconds': seconds / 1e9}
            for name, (count, seconds) in recorder.totals.items()
            if name in ('capabilities/coordinator.py:require_case_validation',
                        'capabilities/coordinator.py:prepare', 'runtime_attestation.py:capture',
                        'capabilities/analysis_context.py:require_validation')}
    report['trace'] = str(trace.resolve())
    (directory / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output / (args.mode + '-latest.json')).write_text(json.dumps({'summary': str((directory / 'summary.json').resolve())}) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
