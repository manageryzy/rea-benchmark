"""Prove real preparation and compiler overlap without provider calls."""
import time
ORIGIN = time.monotonic_ns()
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inside', action='store_true')
    parser.add_argument('--origin', type=int, default=ORIGIN)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.inside:
        import runtime_storage
        return runtime_storage.execute([sys.executable, str(Path(__file__).resolve()),
            *sys.argv[1:], '--inside', '--origin', str(args.origin)])
    import parallel_cells
    context = multiprocessing.get_context('spawn')
    cancelled, launches = context.Event(), context.Value('q', 0)
    barrier = context.Barrier(2)
    models = ['deepseek-flash', 'glm-5.3']
    with ProcessPoolExecutor(max_workers=2, mp_context=context, initializer=parallel_cells.initialize,
            initargs=('trusted_local', cancelled, None, launches, str(args.output/'workers'), args.origin, barrier)) as pool:
        pending = [pool.submit(parallel_cells.probe, {'model': model, 'case': 'cpp',
            'output': str(args.output/model), 'origin': args.origin}) for model in models]
        results = [future.result(timeout=90) for future in pending]
    def intersection(intervals):
        return max(0, min(v[1] for v in intervals)-max(v[0] for v in intervals))/1e9
    preparation = []
    for row in results:
        phases = {p['name']:p for p in row['phases']}
        preparation.append((phases['selection']['start_ns'], phases['runtime_before']['end_ns']))
    report = {'model_api_calls': launches.value, 'workers': results,
        'independent_processes': len({r['pid'] for r in results}) == 2,
        'preparation_overlap_seconds': intersection(preparation),
        'compilation_overlap_seconds': intersection([r['compile_interval_ns'] for r in results]),
        'note': 'Local compiler controls synchronize before compilation to test executor capacity; no model answers.',
        'total_seconds': (time.monotonic_ns()-args.origin)/1e9}
    report['passed'] = (report['independent_processes'] and report['model_api_calls']==0
        and report['preparation_overlap_seconds']>0 and report['compilation_overlap_seconds']>0
        and all(r['control_passed'] and r['within_30_seconds'] for r in results))
    (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='workers'}, indent=2),flush=True)
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
