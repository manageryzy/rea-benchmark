"""Time the normal full selection and next-cell preparation through real compilation.

No model is dispatched. The compiler receives an explicitly labelled local
control program, not a fabricated model answer. Timing starts before imports and
runtime mounting; schedules live in a temporary root and never claim inference.
"""
import time
STARTED = time.monotonic_ns()
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case')
    parser.add_argument('--model', choices=('deepseek-flash', 'glm-5.3'), default='deepseek-flash')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--origin', type=int, default=STARTED)
    parser.add_argument('--inside', action='store_true')
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.inside:
        import runtime_storage
        return runtime_storage.execute([sys.executable, str(Path(__file__).resolve()),
            *sys.argv[1:], '--inside', '--origin', str(args.origin)])
    return measure(args)


def measure(args, *, selection=None, selected=None, before_compile=None):
    """Also used by the normal make benchmark --startup-check entrypoint."""
    import socket
    from contextlib import nullcontext
    from unittest.mock import patch
    import benchmark_config
    import benchmark_trace
    import preparation_session
    from read_session import ReadSession, _SESSION
    import records
    import run
    import startup_policy
    import runtime_attestation
    from capabilities.registration import Registry
    from grading import native
    report = {'model_api_calls': 0, 'validation_tests_rerun': 0,
        'scope': 'full configured selection; real next-cell preparation; local compiler/execution control',
        'phases': []}
    def stage(name, operation):
        started = time.monotonic_ns()
        try:
            with recorder.span(name):
                return operation()
        finally:
            ended = time.monotonic_ns()
            row = {'name': name, 'start_ns': started, 'end_ns': ended, 'seconds': (ended - started) / 1e9}
            report['phases'].append(row)
            print(json.dumps(row), flush=True)
    try:
        with (patch.object(socket.socket, 'connect', side_effect=RuntimeError('No network in startup probe')),
              patch.object(run, 'launch_model', side_effect=RuntimeError('No inference in startup probe')),
              (ReadSession() if _SESSION.get() is None else nullcontext()),
              startup_policy.scope('trusted_local'), preparation_session.scope(True),
              tempfile.TemporaryDirectory(prefix='rea-startup-schedule-') as temporary,
              benchmark_trace.Trace(args.output/'trace.json', detail=True, render_html=False,
                  epoch_ns=args.origin, scopes={}) as recorder):
            if selection is None:
                selection = benchmark_config.load(benchmark_config.DEFAULT)
                selection['models'] = ['deepseek-flash', 'glm-5.3']
            if selection['execution']['startup_mode'] != 'trusted_local':
                raise RuntimeError('The normal startup configuration must select trusted_local')
            if selected is None:
                selected = stage('selection', lambda: run.selected_entries(selection))
            report.update(selected_cells=len(selected), typed_cells=sum(e['suite']=='capabilities-v1' for e in selected))
            registry = Registry(temporary)
            measurement = stage('schedule', lambda: registry.begin(selected, selection))
            entry = next(e for e in selected if e['model'] == args.model and
                (e['case'] == args.case if args.case else e['suite'] != 'capabilities-v1'))
            model = records.models()[args.model]
            entry = stage('next_cell', lambda: run.prepare_next_cell(entry, model, registry, measurement, publish_selection=False))
            attestor = registry.attestor(entry['measurement_registration']) if entry.get('measurement_registration') else None
            task = stage('materialize_task', lambda: run.prepare_task(entry, model, attestor=attestor))
            proof = stage('runtime_before', lambda: runtime_attestation.verify(task))
            report.update(case=entry['case'], model=args.model, runtime_scope=proof['runtime_identity_scope'])
            if before_compile is not None:
                before_compile(timeout=45)
            if task.get('kind') == 'c_buffer':
                # Exercise this task's actual freestanding compiler and
                # authenticated emulator, using its reviewed reference source.
                from capabilities import buffers, store
                from grading.c_executor import runtime as executor
                import action_cache
                value = store.spec(task)
                control = run.load_module(ROOT/'benchmarks/suites/capabilities-v1/controls/buffer_solutions.py', 'startup_buffer_control')
                source = control.solution(task['family_id'])
                profile, platform = executor.compile_profile(), executor.platform()
                compiled = stage('typed_compile', lambda: executor._invoke('c_compile',
                    {'source': source, 'exports': ['reconstructed'], 'header': buffers.HEADER},
                    platform, profile, args.output/'compiler', executor.sources('c_compile')))
                if compiled['status'] != 'compiled':
                    raise RuntimeError('Typed reference compilation failed')
                case, expected = value['cases'][0], value['oracle']['observations'][0]
                clocks = json.loads((args.output/'compiler/phase-times.json').read_text())
                request = {'elf_hex': (args.output/'compiler/candidate.elf').read_bytes().hex(),
                    'entry': 'reconstructed', 'invocation': buffers.invocation(case), 'state': None}
                observed = stage('typed_execute', lambda: executor._invoke('c_execute', request,
                    executor.platform(True), executor.execute_profile(), args.output/'execution', executor.sources('c_execute')))
                clocks.update(json.loads((args.output/'execution/phase-times.json').read_text()))
                passed = buffers.compare(observed, expected, case)['passed']
                report.update(language='c', compiled=True, control_passed=passed,
                    scope='full configured selection; real typed preparation; reviewed reference through actual C executor',
                    compiler_start_seconds=(clocks['compile_start_ns']-args.origin)/1e9,
                    execution_start_seconds=(clocks['execution_start_ns']-args.origin)/1e9,
                    total_seconds=(time.monotonic_ns()-args.origin)/1e9)
                report['within_30_seconds'] = report['compiler_start_seconds'] <= 30
                if not passed:
                    raise RuntimeError('Typed execution control failed')
                report['status'] = 'complete'
                return finish(args, report)
            language = task.get('output_language') or task['language']
            if language not in ('c', 'cpp', 'rust', 'go'):
                language = 'c'
            codes = {
                'c': '#include <stdint.h>\n#include <stddef.h>\nint64_t reconstructed(const uint8_t *p, size_t n) { return n; }\n',
                'cpp': '#include <span>\n#include <cstdint>\nint64_t reconstructed(std::span<const uint8_t> p) { return p.size(); }\n',
                'rust': 'pub fn reconstructed(p: &[u8]) -> i64 { p.len() as i64 }\n',
                'go': 'package main\nfunc reconstructed(p []byte) int64 { return int64(len(p)) }\n'}
            vectors = [{'hex': '-', 'expected': 0, 'category': 'startup-control'},
                       {'hex': '00ff', 'expected': 2, 'category': 'startup-control'}]
            grade = stage('closed_compile_and_execute', lambda: native.evaluate(codes[language], language,
                vectors, 'basic', 'c++20' if language == 'cpp' else '2021', None, args.output/'compiler'))
            clocks = json.loads((args.output/'compiler/phase-times.json').read_text())
            report.update(language=language, compiled=grade['compiled'], control_passed=grade['program_success'],
                compile_interval_ns=[clocks['compile_start_ns'], clocks['compile_end_ns']],
                compiler_start_seconds=(clocks['compile_start_ns']-args.origin)/1e9,
                execution_start_seconds=(clocks['execution_start_ns']-args.origin)/1e9,
                total_seconds=(time.monotonic_ns()-args.origin)/1e9)
            report['within_30_seconds'] = report['compiler_start_seconds'] <= 30
            if not grade['program_success']:
                raise RuntimeError('Local compiler/execution control failed')
            report['status'] = 'complete'
    except Exception as error:
        report.update(status='failed', error=str(error), total_seconds=(time.monotonic_ns()-args.origin)/1e9)
    return finish(args, report)


def finish(args, report):
    (args.output/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report.get('within_30_seconds') and report['status']=='complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
